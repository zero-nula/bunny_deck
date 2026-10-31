"""File backends (local / SFTP) and the copy engine. No UI code in here.

Remote paths always use POSIX semantics (posixpath), local paths os.path.
Copying never deletes user files: its only removals are of temporary files
and dirs it created itself (".bunnydeck-part" files and the private staging
directory of remote-to-remote copies). User files are deleted only by
delete_path(), which the file manager calls after the user confirmed it.
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
    # Neither a regular file nor a folder (FIFO, socket, device): never copied.
    is_special: bool = False


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
                    is_dir = stat.S_ISDIR(st.st_mode)
                    items.append(FileItem(e.name, is_dir, st.st_size, int(st.st_mtime), is_link,
                                          not is_dir and not stat.S_ISREG(st.st_mode)))
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

    def kind(self, path):
        """"dir", "file" (also links and special files) or None; links are
        not followed."""
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        return "dir" if stat.S_ISDIR(st.st_mode) else "file"

    def remove(self, path):
        os.unlink(path)

    def rmdir(self, path):
        os.rmdir(path)


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
            FileItem(e.name, e.is_dir, e.attrs.size or 0, e.attrs.mtime or 0, e.attrs.is_link,
                     e.attrs.permissions is not None and not e.is_dir and not e.attrs.is_link
                     and not e.attrs.is_reg)
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

    def kind(self, path):
        try:
            a = self.client.lstat(path)
        except sftp.SFTPError as e:
            if e.code == sftp.FX_NO_SUCH_FILE:
                return None
            raise
        return "dir" if a.is_dir else "file"

    def remove(self, path):
        self.client.remove(path)

    def rmdir(self, path):
        self.client.rmdir(path)


def same_place(a, b):
    """Whether two backends show the same file system."""
    if not a.is_remote and not b.is_remote:
        return True
    return a.is_remote and b.is_remote and a.client is b.client


def inside(path, folder):
    """Whether path is folder itself or below it (both normalized, POSIX)."""
    folder = folder.rstrip("/")
    return path == folder or path.startswith(folder + "/")


@dataclass
class FolderPlan:
    """What copying a folder takes, found by plan_folder()."""
    dirs: list      # (target folder, relative name) to create, parents first
    files: list     # (source, target, size, relative name)
    skipped: list   # relative names of symlinks and special files (not copied)
    errors: list    # (relative name, exception) of folders that could not be read

    @property
    def total(self):
        return sum(f[2] for f in self.files)


def plan_folder(src_backend, src_dir, dst_backend, dst_dir, cancel=None):
    """Lists src_dir recursively for copying it to dst_dir. Symlinks are not
    followed (so the walk cannot loop or leave the folder) and, like special
    files, are skipped. A subfolder that cannot be read is reported in
    errors and the rest is still copied."""
    root = src_backend.basename(src_dir)
    plan = FolderPlan([(dst_dir, root)], [], [], [])
    stack = [(src_dir, dst_dir, root)]
    while stack:
        if cancel is not None and cancel.is_set():
            raise sftp.Cancelled()
        s, d, rel = stack.pop()
        try:
            items = src_backend.list(s)
        except (OSError, sftp.SFTPError) as e:
            plan.errors.append((rel, e))
            continue
        for it in sorted(items, key=lambda i: i.name):
            r = f"{rel}/{it.name}"
            if it.is_link or it.is_special:
                plan.skipped.append(r)
                continue
            sp, dp = src_backend.join(s, it.name), dst_backend.join(d, it.name)
            if it.is_dir:
                plan.dirs.append((dp, r))
                stack.append((sp, dp, r))
            else:
                plan.files.append((sp, dp, it.size, r))
    return plan


def protected(backend, path):
    """Paths the file manager refuses to delete: the root and the home
    folder, or any folder containing it."""
    return path.rstrip("/") in ("", "/") or inside(backend.home, path)


def _walk_tree(backend, path, cancel):
    """Yields ("file", p) and, after their contents, ("dir", p) for
    everything below path, then ("dir", path). Symlinks count as files: they
    are never followed."""
    stack = [(path, False)]
    while stack:
        if cancel is not None and cancel.is_set():
            raise sftp.Cancelled()
        p, listed = stack.pop()
        if listed:
            yield "dir", p
            continue
        stack.append((p, True))
        for it in backend.list(p):
            child = backend.join(p, it.name)
            if it.is_dir and not it.is_link:
                stack.append((child, False))
            else:
                yield "file", child


def count_tree(backend, path, cancel=None):
    """(files, folders) that deleting path removes, path itself included."""
    if backend.kind(path) != "dir":
        return 1, 0
    files = folders = 0
    for kind, _ in _walk_tree(backend, path, cancel):
        if kind == "dir":
            folders += 1
        else:
            files += 1
    return files, folders


def delete_path(backend, path, cancel=None, progress=None):
    """Deletes a file, a symlink (not its target) or a folder with all its
    contents. progress(kind, path) after each removal. Stops at the first
    error (raised); what was removed until then stays removed."""
    if protected(backend, path):
        raise TransferError("Bunny Deck does not delete the root or your home folder "
                            "(or a folder containing it)")
    kind = backend.kind(path)
    if kind is None:
        raise TransferError(f"No longer exists: {path}")
    if kind == "file":
        backend.remove(path)
        if progress:
            progress("file", path)
        return
    for kind, p in _walk_tree(backend, path, cancel):
        if cancel is not None and cancel.is_set():
            raise sftp.Cancelled()
        (backend.rmdir if kind == "dir" else backend.remove)(p)
        if progress:
            progress(kind, p)


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
        raise TransferError(f"Is a folder (use plan_folder): {src_path}")

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
