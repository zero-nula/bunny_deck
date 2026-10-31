"""Minimal SFTP (protocol version 3) client running over the system OpenSSH.

The transport is `ssh -s <host> sftp`, so OpenSSH does all the security
work: ~/.ssh/config, ssh-agent, keys, known_hosts and host-key checking. This
module only speaks the SFTP wire protocol over ssh's stdin/stdout. Password
prompts are routed through SSH_ASKPASS (see askpass.py); credentials never
pass through this module.

Deliberately not implemented: REMOVE/RMDIR of user files. The only removal
is of a temporary ".bunnydeck-part" upload file this client itself created
with O_EXCL, when that upload fails or is cancelled.
"""

import collections
import os
import posixpath
import secrets
import stat
import struct
import subprocess
import threading

from . import sshmux

SSH_BINARY = "/usr/bin/ssh"

# packet types
FXP_INIT, FXP_VERSION, FXP_OPEN, FXP_CLOSE, FXP_READ, FXP_WRITE = 1, 2, 3, 4, 5, 6
FXP_LSTAT, FXP_FSTAT, FXP_SETSTAT, FXP_FSETSTAT, FXP_OPENDIR, FXP_READDIR = 7, 8, 9, 10, 11, 12
FXP_REMOVE, FXP_MKDIR, FXP_RMDIR, FXP_REALPATH, FXP_STAT, FXP_RENAME = 13, 14, 15, 16, 17, 18
FXP_EXTENDED = 200
FXP_STATUS, FXP_HANDLE, FXP_DATA, FXP_NAME, FXP_ATTRS = 101, 102, 103, 104, 105
FXP_EXTENDED_REPLY = 201

# open flags
FXF_READ, FXF_WRITE, FXF_APPEND, FXF_CREAT, FXF_TRUNC, FXF_EXCL = 1, 2, 4, 8, 16, 32

# attr flags
ATTR_SIZE, ATTR_UIDGID, ATTR_PERMISSIONS, ATTR_ACMODTIME = 1, 2, 4, 8
ATTR_EXTENDED = 0x80000000

# status codes
FX_OK, FX_EOF, FX_NO_SUCH_FILE, FX_PERMISSION_DENIED, FX_FAILURE = 0, 1, 2, 3, 4
FX_BAD_MESSAGE, FX_NO_CONNECTION, FX_CONNECTION_LOST, FX_OP_UNSUPPORTED = 5, 6, 7, 8

STATUS_TEXT = {
    FX_EOF: "End of file",
    FX_NO_SUCH_FILE: "No such file or directory",
    FX_PERMISSION_DENIED: "Permission denied",
    FX_FAILURE: "Operation failed",
    FX_BAD_MESSAGE: "Bad message",
    FX_NO_CONNECTION: "No connection",
    FX_CONNECTION_LOST: "Connection lost",
    FX_OP_UNSUPPORTED: "Operation not supported by server",
}

CHUNK = 32 * 1024
MAX_OUTSTANDING = 32
MAX_PACKET = 512 * 1024


class SFTPError(Exception):
    def __init__(self, message, code=None, path=None):
        self.code = code
        self.path = path
        super().__init__(f"{message}: {path}" if path else message)


class ConnectionFailed(SFTPError):
    pass


class Cancelled(Exception):
    pass


def enc(path):
    return path.encode("utf-8", "surrogateescape")


def dec(b):
    return b.decode("utf-8", "surrogateescape")


class Attrs:
    __slots__ = ("size", "uid", "gid", "permissions", "atime", "mtime")

    def __init__(self, size=None, uid=None, gid=None, permissions=None, atime=None, mtime=None):
        self.size, self.uid, self.gid = size, uid, gid
        self.permissions, self.atime, self.mtime = permissions, atime, mtime

    @property
    def is_dir(self):
        return self.permissions is not None and stat.S_ISDIR(self.permissions)

    @property
    def is_link(self):
        return self.permissions is not None and stat.S_ISLNK(self.permissions)

    @property
    def is_reg(self):
        return self.permissions is not None and stat.S_ISREG(self.permissions)

    def pack(self):
        flags, body = 0, b""
        if self.size is not None:
            flags |= ATTR_SIZE
            body += struct.pack(">Q", self.size)
        if self.permissions is not None:
            flags |= ATTR_PERMISSIONS
            body += struct.pack(">I", self.permissions)
        return struct.pack(">I", flags) + body


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def u32(self):
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u64(self):
        v = struct.unpack_from(">Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def string(self):
        n = self.u32()
        v = self.data[self.pos:self.pos + n]
        if len(v) != n:
            raise SFTPError("Malformed SFTP packet")
        self.pos += n
        return v

    def attrs(self):
        flags = self.u32()
        a = Attrs()
        if flags & ATTR_SIZE:
            a.size = self.u64()
        if flags & ATTR_UIDGID:
            a.uid, a.gid = self.u32(), self.u32()
        if flags & ATTR_PERMISSIONS:
            a.permissions = self.u32()
        if flags & ATTR_ACMODTIME:
            a.atime, a.mtime = self.u32(), self.u32()
        if flags & ATTR_EXTENDED:
            for _ in range(self.u32()):
                self.string()
                self.string()
        return a


def s_str(b):
    return struct.pack(">I", len(b)) + b


class Entry:
    """A directory entry. `attrs` is lstat info; for symlinks `target_is_dir`
    tells whether the link points at a directory."""

    __slots__ = ("name", "attrs", "target_is_dir")

    def __init__(self, name, attrs, target_is_dir=False):
        self.name, self.attrs, self.target_is_dir = name, attrs, target_is_dir

    @property
    def is_dir(self):
        return self.attrs.is_dir or (self.attrs.is_link and self.target_is_dir)


class _Future:
    __slots__ = ("event", "result")

    def __init__(self):
        self.event = threading.Event()
        self.result = None


def ssh_sftp_argv(profile, extra_opts=()):
    """`ssh ... -s host sftp` with options that keep the session a pure,
    non-interactive data channel while honouring the user's ssh config.
    It runs over a terminal's connection to the host if there is one, but
    never becomes a shared connection itself (see sshmux.py)."""
    opts = [
        "-x", "-a", "-T",
        "-o", "BatchMode=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "PermitLocalCommand=no",
        "-o", "RemoteCommand=none",
        "-o", "RequestTTY=no",
        "-o", "ControlMaster=no",
        *sshmux.client_opts(),
        "-o", "ConnectTimeout=20",
        "-o", "ServerAliveInterval=20",
    ]
    return [SSH_BINARY] + list(extra_opts) + opts + ["-s"] + profile.ssh_args() + ["sftp"]


class SFTPClient:
    def __init__(self, argv, env=None):
        self.argv = argv
        self._env = env
        self._proc = None
        self._send_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._next_id = 1
        self._closed = False
        self._close_reason = None
        self.extensions = {}
        self.stderr_lines = collections.deque(maxlen=30)
        self.home = None

    # ----- lifecycle ------------------------------------------------------

    def connect(self):
        """Blocking: starts ssh and performs the SFTP handshake. May take a
        while when the user has to answer password prompts."""
        env = dict(os.environ)
        if self._env:
            env.update(self._env)
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,  # no controlling tty: prompts must use askpass
                bufsize=0,
            )
        except OSError as e:
            raise ConnectionFailed(f"Could not start ssh: {e.strerror}")
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()
        try:
            self._write_packet(FXP_INIT, struct.pack(">I", 3))
            ptype, data = self._read_packet()
        except (OSError, EOFError, struct.error, ValueError):
            self._fail_connect()
        if ptype != FXP_VERSION:
            self._fail_connect("Unexpected reply from the SFTP server")
        r = Reader(data)
        version = r.u32()
        if version < 3:
            self._fail_connect(f"Unsupported SFTP version {version}")
        while r.pos < len(data):
            name = dec(r.string())
            self.extensions[name] = r.string()
        threading.Thread(target=self._reader_loop, daemon=True).start()
        self.home = self.realpath(".")
        return self

    def _fail_connect(self, msg=None):
        if self._proc is not None:
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            # Let the stderr reader drain ssh's final messages first.
            self._stderr_thread.join(timeout=2)
            self._proc.stdout.close()
            try:
                self._proc.stdin.close()
            except OSError:
                pass
        self._closed = True
        if self._close_reason == "cancelled":
            raise Cancelled()
        raise ConnectionFailed(msg or self.describe_failure())

    def describe_failure(self):
        lines = [ln for ln in self.stderr_lines if ln.strip()]
        low = "\n".join(lines).lower()
        text = "\n".join(lines[-4:])
        if "remote host identification has changed" in low:
            return ("REMOTE HOST IDENTIFICATION HAS CHANGED. OpenSSH refused the connection "
                    "because the server's host key does not match known_hosts. "
                    "Verify the key with the server administrator before updating known_hosts.")
        if "permission denied" in low:
            return ("Authentication failed (" + lines[-1].strip() + ").\n"
                    "No usable key was accepted and no correct password was given.")
        if "host key verification failed" in low:
            return "Host key verification failed. The server's host key was rejected or has changed."
        if "subsystem request failed" in low:
            return "The server does not provide the SFTP subsystem."
        if text:
            return text
        rc = self._proc.returncode if self._proc is not None else None
        return f"ssh exited unexpectedly (status {rc})."

    def close(self, reason=None):
        if self._close_reason is None:
            self._close_reason = reason
        self._closed = True
        p = self._proc
        if p is not None:
            with self._send_lock:
                try:
                    p.stdin.close()
                except OSError:
                    pass
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                p.kill()
        self._wake_all()

    @property
    def alive(self):
        return not self._closed and self._proc is not None and self._proc.poll() is None

    # ----- transport ------------------------------------------------------

    def _stderr_loop(self):
        try:
            for raw in iter(self._proc.stderr.readline, b""):
                self.stderr_lines.append(raw.decode("utf-8", "replace").rstrip())
        except (OSError, ValueError):
            pass
        finally:
            # Each pipe is closed only by the thread that reads it.
            self._proc.stderr.close()

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self._proc.stdout.read(n - len(buf))
            if not chunk:
                raise EOFError()
            buf += chunk
        return buf

    def _read_packet(self):
        (length,) = struct.unpack(">I", self._read_exact(4))
        if length < 1 or length > MAX_PACKET:
            raise EOFError()
        data = self._read_exact(length)
        return data[0], data[1:]

    def _write_packet(self, ptype, payload):
        data = bytes([ptype]) + payload
        with self._send_lock:
            self._proc.stdin.write(struct.pack(">I", len(data)) + data)

    def _reader_loop(self):
        try:
            while True:
                ptype, data = self._read_packet()
                (rid,) = struct.unpack_from(">I", data, 0)
                with self._pending_lock:
                    fut = self._pending.pop(rid, None)
                if fut is not None:
                    fut.result = (ptype, data[4:])
                    fut.event.set()
        except (OSError, EOFError, struct.error, ValueError):
            pass
        self._closed = True
        self._proc.stdout.close()
        self._wake_all()

    def _wake_all(self):
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for fut in pending:
            fut.event.set()

    def _send(self, ptype, payload):
        if self._closed:
            raise SFTPError("Connection closed", FX_CONNECTION_LOST)
        fut = _Future()
        with self._pending_lock:
            rid = self._next_id
            self._next_id = (self._next_id + 1) & 0xFFFFFFFF
            self._pending[rid] = fut
        try:
            self._write_packet(ptype, struct.pack(">I", rid) + payload)
        except (OSError, ValueError):
            with self._pending_lock:
                self._pending.pop(rid, None)
            self._closed = True
            raise SFTPError("Connection lost", FX_CONNECTION_LOST)
        return fut

    def _wait(self, fut):
        fut.event.wait()
        if fut.result is None:
            raise SFTPError("Connection lost", FX_CONNECTION_LOST)
        return fut.result

    def _request(self, ptype, payload):
        return self._wait(self._send(ptype, payload))

    @staticmethod
    def _status(data, path=None, ok_eof=False):
        r = Reader(data)
        code = r.u32()
        if code == FX_OK or (ok_eof and code == FX_EOF):
            return code
        try:
            msg = dec(r.string())
        except (SFTPError, struct.error):
            msg = ""
        msg = STATUS_TEXT.get(code) or msg or f"SFTP error {code}"
        raise SFTPError(msg, code, path)

    def _expect(self, reply, rtype, path=None):
        ptype, data = reply
        if ptype == rtype:
            return data
        if ptype == FXP_STATUS:
            self._status(data, path)
        raise SFTPError(f"Unexpected SFTP reply {ptype}", path=path)

    # ----- operations -----------------------------------------------------

    def realpath(self, path):
        data = self._expect(self._request(FXP_REALPATH, s_str(enc(path))), FXP_NAME, path)
        r = Reader(data)
        if r.u32() < 1:
            raise SFTPError("Empty REALPATH reply", path=path)
        return dec(r.string())

    def stat(self, path):
        data = self._expect(self._request(FXP_STAT, s_str(enc(path))), FXP_ATTRS, path)
        return Reader(data).attrs()

    def lstat(self, path):
        data = self._expect(self._request(FXP_LSTAT, s_str(enc(path))), FXP_ATTRS, path)
        return Reader(data).attrs()

    def exists(self, path):
        try:
            self.lstat(path)
            return True
        except SFTPError as e:
            if e.code == FX_NO_SUCH_FILE:
                return False
            raise

    def mkdir(self, path, mode=0o755):
        _, data = self._request(FXP_MKDIR, s_str(enc(path)) + Attrs(permissions=mode).pack())
        self._status(data, path)

    def listdir(self, path):
        """Returns the entries of `path` (without '.' and '..'). Raises
        SFTPError if the directory cannot be read."""
        handle = self._expect(self._request(FXP_OPENDIR, s_str(enc(path))), FXP_HANDLE, path)
        handle = Reader(handle).string()
        entries = []
        try:
            while True:
                ptype, data = self._request(FXP_READDIR, s_str(handle))
                if ptype == FXP_STATUS:
                    self._status(data, path, ok_eof=True)
                    break
                if ptype != FXP_NAME:
                    raise SFTPError("Unexpected READDIR reply", path=path)
                r = Reader(data)
                for _ in range(r.u32()):
                    name = dec(r.string())
                    r.string()  # longname
                    attrs = r.attrs()
                    if name in (".", ".."):
                        continue
                    entries.append(Entry(name, attrs))
        finally:
            self._close_handle(handle)
        # Resolve symlink targets so links to directories are navigable.
        links = [e for e in entries if e.attrs.is_link]
        futs = [(e, self._send(FXP_STAT, s_str(enc(posixpath.join(path, e.name)))))
                for e in links]
        for e, fut in futs:
            ptype, data = self._wait(fut)
            if ptype == FXP_ATTRS:
                e.target_is_dir = Reader(data).attrs().is_dir
        return entries

    def _close_handle(self, handle):
        try:
            self._request(FXP_CLOSE, s_str(handle))
        except SFTPError:
            pass

    def _open(self, path, flags, attrs=None):
        payload = s_str(enc(path)) + struct.pack(">I", flags) + (attrs or Attrs()).pack()
        data = self._expect(self._request(FXP_OPEN, payload), FXP_HANDLE, path)
        return Reader(data).string()

    def rename(self, src, dst, overwrite=False):
        if overwrite:
            if "posix-rename@openssh.com" not in self.extensions:
                raise SFTPError("Server cannot replace files atomically (no posix-rename)")
            payload = s_str(b"posix-rename@openssh.com") + s_str(enc(src)) + s_str(enc(dst))
            _, data = self._request(FXP_EXTENDED, payload)
        else:
            # Plain SSH_FXP_RENAME fails when the target exists: no silent overwrite.
            _, data = self._request(FXP_RENAME, s_str(enc(src)) + s_str(enc(dst)))
        self._status(data, dst)

    def _remove_own_tempfile(self, path):
        """Removes a '.bunnydeck-part' file this client created (O_EXCL)."""
        assert posixpath.basename(path).endswith(".bunnydeck-part")
        try:
            self._request(FXP_REMOVE, s_str(enc(path)))
        except SFTPError:
            pass

    def download(self, remote_path, local_file, progress=None, cancel=None):
        """Copies remote_path into the open binary file object local_file.
        Returns the number of bytes written."""
        handle = self._open(remote_path, FXF_READ)
        written = 0
        try:
            outstanding = collections.deque()
            next_off, eof = 0, False

            def issue(off, length):
                payload = s_str(handle) + struct.pack(">QI", off, length)
                outstanding.append((off, length, self._send(FXP_READ, payload)))

            while True:
                while not eof and len(outstanding) < MAX_OUTSTANDING:
                    issue(next_off, CHUNK)
                    next_off += CHUNK
                if not outstanding:
                    break
                off, length, fut = outstanding.popleft()
                ptype, data = self._wait(fut)
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if ptype == FXP_STATUS:
                    self._status(data, remote_path, ok_eof=True)
                    eof = True
                    continue
                if ptype != FXP_DATA:
                    raise SFTPError("Unexpected READ reply", path=remote_path)
                chunk = Reader(data).string()
                local_file.seek(off)
                local_file.write(chunk)
                written += len(chunk)
                if 0 < len(chunk) < length and not eof:
                    issue(off + len(chunk), length - len(chunk))
                if progress is not None:
                    progress(written)
            # drain anything left after an error/eof
        finally:
            self._close_handle(handle)
        return written

    def upload(self, local_file, remote_path, size, overwrite=False, mode=0o644,
               progress=None, cancel=None):
        """Uploads to a temporary sibling file and renames it into place, so
        a failure never leaves a truncated target and an existing file is
        only replaced when overwrite=True."""
        directory = posixpath.dirname(remote_path)
        base = posixpath.basename(remote_path)
        tmp = posixpath.join(directory, f".{base}.{secrets.token_hex(4)}.bunnydeck-part")
        if not overwrite and self.exists(remote_path):
            raise SFTPError("File exists", FX_FAILURE, remote_path)
        if overwrite:
            try:
                mode = stat.S_IMODE(self.stat(remote_path).permissions or mode)
            except SFTPError:
                pass
        handle = self._open(tmp, FXF_WRITE | FXF_CREAT | FXF_EXCL,
                            Attrs(permissions=mode & 0o777))
        sent = 0
        ok = False
        try:
            outstanding = collections.deque()
            off = 0
            while True:
                data = local_file.read(CHUNK) if off < size or size < 0 else b""
                if data:
                    payload = s_str(handle) + struct.pack(">Q", off) + s_str(data)
                    outstanding.append((len(data), self._send(FXP_WRITE, payload)))
                    off += len(data)
                if not data or len(outstanding) >= MAX_OUTSTANDING:
                    if not outstanding:
                        break
                    n, fut = outstanding.popleft()
                    _, rdata = self._wait(fut)
                    self._status(rdata, remote_path)
                    sent += n
                    if progress is not None:
                        progress(sent)
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
            self._close_handle(handle)
            handle = None
            self.rename(tmp, remote_path, overwrite=overwrite)
            ok = True
        finally:
            if handle is not None:
                self._close_handle(handle)
            if not ok and not self._closed:
                self._remove_own_tempfile(tmp)
        return sent
