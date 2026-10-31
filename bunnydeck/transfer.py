"""File backends (local / SFTP) and the copy engine. No UI code in here.

Remote paths always use POSIX semantics (posixpath), local paths os.path.
No function in this module deletes user files. The only removals are of
temporary files/dirs this module itself created (".bunnydeck-part" files and
the private staging directory used for remote-to-remote copies).
"""

import errno
import os
import posixpath
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass

from . import sftp


@dataclass
class FileItem:
    name: str
    is_dir: bool
    size: int
    mtime: int
    is_link: bool = False


class TransferError(Exception):
    pass


def resolve_remote(cwd, text, home):
    """Resolves user input from the remote path entry to a normalized
    absolute POSIX path. Absolute input is used as-is (never prefixed with
    the current directory)."""
    text = text.strip()
    if not text:
        return cwd
    if text == "~" or text.startswith("~/"):
        text = home.rstrip("/") + text[1:]
    path = text if text.startswith("/") else posixpath.join(cwd, text)
    path = posixpath.normpath(path)
    if path.startswith("//"):
        path = "/" + path.lstrip("/")
    return path


def resolve_local(cwd, text):
    text = os.path.expanduser(text.strip())
    if not text:
        return cwd
    return os.path.normpath(text if os.path.isabs(text) else os.path.join(cwd, text))


class LocalBackend:
    is_remote = False
    label = "Local"

    def __init__(self):
        self.home = os.path.expanduser("~")

    def resolve(self, cwd, text):
        return resolve_local(cwd, text)

    def join(self, d, name):
        return os.path.join(d, name)

    def basename(self, p):
        return os.path.basename(p.rstrip("/")) or p

    def parent(self, p):
        return os.path.dirname(p.rstrip("/")) or "/"

    def list(self, path):
        items = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    is_link = e.is_symlink()
                    try:
                        st = e.stat()
                    except OSError:
                        st = e.stat(follow_symlinks=False)
                    items.append(FileItem(e.name, stat.S_ISDIR(st.st_mode), st.st_size,
                                          int(st.st_mtime), is_link))
                except OSError:
                    items.append(FileItem(e.name, False, 0, 0))
        return items

    def stat(self, path):
        """Returns (exists, is_dir, size, mode)."""
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return (os.path.lexists(path), False, 0, 0)
        return (True, stat.S_ISDIR(st.st_mode), st.st_size, stat.S_IMODE(st.st_mode))

    def mkdir(self, path):
        os.mkdir(path, 0o755)


class RemoteBackend:
    is_remote = True

    def __init__(self, client, label):
        self.client = client
        self.label = label
        self.home = client.home or "/"

    def resolve(self, cwd, text):
        return resolve_remote(cwd, text, self.home)

    def join(self, d, name):
        return posixpath.join(d, name)

    def basename(self, p):
        return posixpath.basename(p.rstrip("/")) or p

    def parent(self, p):
        return posixpath.dirname(p.rstrip("/")) or "/"

    def list(self, path):
        return [
            FileItem(e.name, e.is_dir, e.attrs.size or 0, e.attrs.mtime or 0, e.attrs.is_link)
            for e in self.client.listdir(path)
        ]

    def stat(self, path):
        try:
            a = self.client.stat(path)
        except sftp.SFTPError as e:
            if e.code == sftp.FX_NO_SUCH_FILE:
                # dangling symlink still "exists" as a name
                try:
                    self.client.lstat(path)
                    return (True, False, 0, 0)
                except sftp.SFTPError:
                    return (False, False, 0, 0)
            raise
        return (True, a.is_dir, a.size or 0, stat.S_IMODE(a.permissions or 0o644))

    def mkdir(self, path):
        self.client.mkdir(path)


def _staging_dir():
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isdir(base):
        base = None
    return tempfile.mkdtemp(prefix="bunny-deck-stage-", dir=base)  # mode 0700


def _download_to_local(client, src, dst, overwrite, mode, progress, cancel):
    """Downloads into a hidden temp file next to dst, then moves it into
    place without overwriting unless confirmed."""
    d = os.path.dirname(dst)
    tmp = os.path.join(d, f".{os.path.basename(dst)}.{secrets.token_hex(4)}.bunnydeck-part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    ok = False
    try:
        with os.fdopen(fd, "wb") as f:
            n = client.download(src, f, progress=progress, cancel=cancel)
            os.fchmod(f.fileno(), (mode or 0o644) & 0o777 & ~_UMASK)
        _place_local(tmp, dst, overwrite)
        ok = True
        return n
    finally:
        if not ok:
            try:
                os.unlink(tmp)  # our own partial file only
            except FileNotFoundError:
                pass


def _read_umask():
    m = os.umask(0o022)
    os.umask(m)
    return m


# Read once at import (os.umask is process-global and not thread-safe).
_UMASK = _read_umask()


def _place_local(tmp, dst, overwrite):
    if overwrite:
        os.replace(tmp, dst)
        return
    try:
        os.link(tmp, dst)  # fails with EEXIST: never a silent overwrite
    except FileExistsError:
        raise TransferError(f"File exists: {dst}")
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EXDEV, errno.EMLINK):
            raise
        if os.path.lexists(dst):
            raise TransferError(f"File exists: {dst}")
        os.rename(tmp, dst)
        return
    os.unlink(tmp)


def _copy_local(src, dst, overwrite, progress, cancel):
    d = os.path.dirname(dst)
    tmp = os.path.join(d, f".{os.path.basename(dst)}.{secrets.token_hex(4)}.bunnydeck-part")
    ok = False
    try:
        done = 0
        with open(src, "rb") as fi, open(tmp, "xb") as fo:
            while True:
                if cancel is not None and cancel.is_set():
                    raise sftp.Cancelled()
                buf = fi.read(256 * 1024)
                if not buf:
                    break
                fo.write(buf)
                done += len(buf)
                if progress:
                    progress(done)
        shutil.copymode(src, tmp)
        _place_local(tmp, dst, overwrite)
        ok = True
        return done
    finally:
        if not ok:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def copy_file(src_backend, src_path, dst_backend, dst_path, overwrite=False,
              progress=None, cancel=None, phase=None):
    """Copies one regular file. progress(done, total) where total accounts
    for both legs of a remote-to-remote copy. phase(text) announces stages."""
    exists, is_dir, size, mode = src_backend.stat(src_path)
    if not exists:
        raise TransferError(f"Source does not exist: {src_path}")
    if is_dir:
        raise TransferError(f"Folders are not supported (skipped): {src_path}")

    def prog(offset, total):
        return (lambda n: progress(offset + n, total)) if progress else None

    if not src_backend.is_remote and not dst_backend.is_remote:
        return _copy_local(src_path, dst_path, overwrite, prog(0, size), cancel)

    if not src_backend.is_remote:
        if phase:
            phase(f"Uploading to {dst_backend.label}")
        with open(src_path, "rb") as f:
            return dst_backend.client.upload(f, dst_path, size, overwrite=overwrite,
                                             mode=mode or 0o644, progress=prog(0, size),
                                             cancel=cancel)

    if not dst_backend.is_remote:
        if phase:
            phase(f"Downloading from {src_backend.label}")
        return _download_to_local(src_backend.client, src_path, dst_path, overwrite, mode,
                                  prog(0, size), cancel)

    # Remote -> remote: routed through a private local staging file.
    stage = _staging_dir()
    try:
        tmp = os.path.join(stage, "data")
        if phase:
            phase(f"Downloading from {src_backend.label} (staging)")
        with open(tmp, "xb") as f:
            n = src_backend.client.download(src_path, f, progress=prog(0, 2 * size),
                                            cancel=cancel)
        if phase:
            phase(f"Uploading to {dst_backend.label}")
        with open(tmp, "rb") as f:
            dst_backend.client.upload(f, dst_path, n, overwrite=overwrite, mode=mode or 0o644,
                                      progress=prog(n, 2 * n), cancel=cancel)
        return n
    finally:
        shutil.rmtree(stage, ignore_errors=True)
