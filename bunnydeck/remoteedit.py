"""Editing a file on a server in a local editor.

The file is downloaded into a private folder of its own
($XDG_CACHE_HOME/bunny-deck/edit/<connection>-<random>/<name>) and opened in
the editor. Every time the editor saves it, the new content goes back to the
server over the connection's SFTP channel, with the same safe upload as a
copy (a temporary file renamed into place). If the file changed on the
server since it was opened or last saved, the user is asked first.

Editors save in different ways: in place, or by writing a new file and
renaming it over the old one (Zed, VS Code, vim). The watcher follows the
folder as well as the file, so both are seen, and a save is only uploaded
when the content really changed.

When Bunny Deck quits, the local copies are removed, except those whose
last changes could not be saved to the server.

The UI is passed in as an object with:
  ask(title, text, informative, yes_label) -> bool   (UI thread)
  notify(text, error=False)
  launch(local_path, editor) -> None or an error message
"""

import hashlib
import io
import os
import re
import secrets
import shutil
import stat
import threading

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer

from . import sftp
from .connections import run_on_main_sync
from .mainthread import call_soon

# Bigger files are only opened after a question (an editor is not the
# right tool for a log of hundreds of megabytes).
LARGE = 20 * 1024 * 1024
# Saves arrive as several file system events; they are collected for this long.
SETTLE_MS = 300


def edit_root():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "bunny-deck", "edit")


def _folder_name(profile_name):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", profile_name).strip("._") or "server"
    return f"{safe[:40]}-{secrets.token_hex(3)}"


def looks_binary(data):
    """A NUL byte in the first 8 KiB: an image, archive or program, not text."""
    return b"\0" in data[:8192]


def _signature(attrs):
    return (attrs.mtime, attrs.size)


class EditSession:
    """One remote file open in an editor."""

    def __init__(self, profile, remote, local, mode, remote_sig, digest):
        self.profile = profile
        self.remote = remote        # absolute path on the server (symlinks resolved)
        self.local = local
        self.folder = os.path.dirname(local)
        self.mode = mode
        self.remote_sig = remote_sig  # (mtime, size) on the server after our last sync
        self.synced = digest          # sha256 of the content the server has from us
        self.busy = False
        self.again = False
        self.unsaved = False          # the last change did not reach the server
        self.timer = None

    @property
    def name(self):
        return os.path.basename(self.local)


class RemoteEdits(QObject):
    def __init__(self, pool, ui):
        super().__init__()
        self.pool = pool
        self.ui = ui
        self.sessions = []
        self._opening = set()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_change)
        self.watcher.directoryChanged.connect(self._on_change)

    # ----- opening --------------------------------------------------------

    def find(self, profile_id, remote):
        return next((s for s in self.sessions
                     if s.profile.id == profile_id and s.remote == remote), None)

    def open(self, profile, client, path, editor=None):
        """Opens a remote file in the editor (or the given one). A file that
        is already open is not downloaded again: the editor is just asked to
        show the copy it already has, which may contain newer changes."""
        key = (profile.id, path)
        if key in self._opening:
            return
        session = self.find(profile.id, path)
        if session is not None:
            self._launch(session, editor)
            return
        self._opening.add(key)
        self.ui.notify(f"Opening {os.path.basename(path)} from {profile.name}…")
        threading.Thread(target=self._download, args=(profile, client, path, editor, key),
                         daemon=True).start()

    def _ask(self, *args):
        return run_on_main_sync(call_soon, self.ui.ask, *args)

    def _download(self, profile, client, path, editor, key):
        folder = None
        try:
            remote = client.realpath(path)
            attrs = client.stat(remote)
            if attrs.is_dir:
                raise _Stop(f"{path} is a folder.")
            if attrs.permissions is not None and not attrs.is_reg:
                raise _Stop(f"{path} is not a regular file.")
            name = os.path.basename(remote)
            if (attrs.size or 0) > LARGE and not self._ask(
                    "Large file", f"Open {name} in the editor?",
                    f"It is {(attrs.size or 0) / 1048576:.0f} MB. It is downloaded first, and "
                    "every save uploads all of it again.", "Open"):
                raise _Stop(None)
            existing = run_on_main_sync(call_soon, self.find, profile.id, remote)
            if existing is not None:  # the path was a symlink to a file already open
                call_soon(lambda: self._launch(existing, editor))
                return
            os.makedirs(edit_root(), mode=0o700, exist_ok=True)
            folder = os.path.join(edit_root(), _folder_name(profile.name))
            os.mkdir(folder, 0o700)
            local = os.path.join(folder, name)
            fd = os.open(local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                client.download(remote, f)
            with open(local, "rb") as f:
                data = f.read()
            head = data[:8192]
            if looks_binary(head) and not self._ask(
                    "Not a text file", f"{name} does not look like a text file.",
                    "It may be an image, an archive or a program. Saving it from a text "
                    "editor can damage it.", "Open Anyway"):
                raise _Stop(None)
            mode = stat.S_IMODE(attrs.permissions or 0o644)
            session = EditSession(profile, remote, local, mode, _signature(attrs),
                                  hashlib.sha256(data).hexdigest())
            folder = None  # the session owns it now
            call_soon(lambda: self._opened(session, editor, key))
        except _Stop as e:
            message = e.message  # (e itself is gone when the lambda runs)
            call_soon(lambda: self._failed(key, message))
        except (OSError, sftp.SFTPError, sftp.Cancelled) as e:
            msg = f"Could not open {path} from {profile.name}:\n{_error_text(e)}"
            call_soon(lambda: self._failed(key, msg))
        finally:
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)

    def _failed(self, key, message):
        self._opening.discard(key)
        if message:
            self.ui.notify(message, error=True)
        else:
            self.ui.notify("Not opened")

    def _opened(self, session, editor, key):
        self._opening.discard(key)
        self.sessions.append(session)
        session.timer = QTimer(self)
        session.timer.setSingleShot(True)
        session.timer.setInterval(SETTLE_MS)
        session.timer.timeout.connect(lambda: self._check(session))
        self.watcher.addPath(session.folder)
        self.watcher.addPath(session.local)
        self._launch(session, editor)

    def _launch(self, session, editor):
        error = self.ui.launch(session.local, editor)
        if error:
            self.ui.notify(error, error=True)
        else:
            self.ui.notify(f"Editing {session.name} from {session.profile.name}: every save "
                           "is uploaded")

    # ----- saving ---------------------------------------------------------

    def _session_for(self, path):
        for s in self.sessions:
            if path in (s.local, s.folder):
                return s
        return None

    def _on_change(self, path):
        session = self._session_for(path)
        if session is None:
            return
        # A save that replaced the file ends the watch on the old one.
        if os.path.exists(session.local) and session.local not in self.watcher.files():
            self.watcher.addPath(session.local)
        session.timer.start()

    def _check(self, session):
        if session not in self.sessions:
            return
        if session.busy:
            session.again = True
            return
        try:
            with open(session.local, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return  # in the middle of a save by renaming; its next event comes
        except OSError as e:
            self.ui.notify(f"Cannot read {session.local}: {_error_text(e)}", error=True)
            return
        digest = hashlib.sha256(data).hexdigest()
        if digest == session.synced:
            return
        session.busy = True
        session.again = False
        self.ui.notify(f"Saving {session.name} to {session.profile.name}…")

        def connected(client, error):
            if error:
                self._saved(session, digest, None,
                            "Authentication was cancelled" if error == "cancelled" else error)
                return
            threading.Thread(target=self._upload, args=(session, client, data, digest),
                             daemon=True).start()

        self.pool.get(session.profile, connected)

    def _upload(self, session, client, data, digest):
        try:
            try:
                sig = _signature(client.stat(session.remote))
            except sftp.SFTPError as e:
                if e.code != sftp.FX_NO_SUCH_FILE:
                    raise
                sig = None
            if sig != session.remote_sig:
                where = f"{session.remote} on {session.profile.name}"
                if sig is None:
                    ok = self._ask("File deleted on the server",
                                   f"{session.name} was deleted on the server.",
                                   f"{where} no longer exists. Create it again with your "
                                   "changes?", "Create It")
                else:
                    ok = self._ask("File changed on the server",
                                   f"{session.name} was changed on the server.",
                                   f"{where} was changed by someone or something else "
                                   "since you opened it. Replace it with your version?",
                                   "Replace on Server")
                if not ok:
                    call_soon(lambda: self._saved(session, digest, None, None, declined=True))
                    return
            client.upload(io.BytesIO(data), session.remote, len(data), overwrite=sig is not None,
                          mode=session.mode)
            new_sig = _signature(client.stat(session.remote))
            call_soon(lambda: self._saved(session, digest, new_sig, None))
        except (OSError, sftp.SFTPError, sftp.Cancelled) as e:
            msg = _error_text(e)
            call_soon(lambda: self._saved(session, digest, None, msg))

    def _saved(self, session, digest, sig, error, declined=False):
        session.busy = False
        if error:
            session.unsaved = True
            self.ui.notify(f"{session.name} was NOT saved to {session.profile.name}: {error}\n"
                           "Your changes are kept in the editor; save again to retry.",
                           error=True)
        elif declined:
            session.unsaved = True
            self.ui.notify(f"{session.name}: not saved to {session.profile.name}")
        else:
            session.synced = digest
            session.remote_sig = sig
            session.unsaved = False
            self.ui.notify(f"Saved {session.name} to {session.profile.name}")
        if session.again:
            self._check(session)

    # ----- quitting -------------------------------------------------------

    def shutdown(self):
        """Removes the local copies whose content the server has. Returns
        the paths of copies that are kept because they have changes the
        server does not have."""
        kept = []
        for s in self.sessions:
            if s.timer is not None:
                s.timer.stop()
            try:
                with open(s.local, "rb") as f:
                    current = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                current = s.synced
            if s.unsaved or current != s.synced:
                kept.append(s.local)
            else:
                shutil.rmtree(s.folder, ignore_errors=True)
        paths = self.watcher.files() + self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)
        self.sessions = []
        return kept


class _Stop(Exception):
    """Opening ends early: with a message for the user, or quietly (None)."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _error_text(e):
    if isinstance(e, OSError) and e.strerror:
        return f"{e.strerror}: {e.filename}" if e.filename else e.strerror
    return str(e) or e.__class__.__name__
