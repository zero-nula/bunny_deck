"""One SSH connection per host, shared by its terminals and file manager.

OpenSSH connection sharing (ControlMaster): the first terminal's ssh becomes
the master and keeps the authenticated connection; further terminals and the
file manager's SFTP channel to the same host run over it, so a password is
asked once and they open at once.

Only terminals start a master. The SFTP channel uses an existing master but
never becomes one (ControlMaster=no in sftp.py), because a master started with
its restrictive options (no agent, X11 or port forwarding) would impose them
on terminals sharing it.

The sockets live in a private 0700 runtime directory of this app run. With
ControlPersist the master moves to the background, so a terminal ends as soon
as its own session ends, even while the file manager still uses the
connection; an idle master exits after PERSIST_SECONDS, and all masters are
closed when the app quits.
"""

import os
import shutil
import subprocess
import tempfile

SSH_BINARY = "/usr/bin/ssh"
PERSIST_SECONDS = 60

_dir = None


def control_dir():
    global _dir
    if _dir is None:
        base = os.environ.get("XDG_RUNTIME_DIR")
        if not base or not os.path.isdir(base):
            base = None
        _dir = tempfile.mkdtemp(prefix="bunny-deck-ssh-", dir=base)  # mode 0700
    return _dir


def control_path():
    # %C: a hash of local host, remote host, port and user (fixed length, so
    # the socket path stays well below the Unix socket path limit).
    return os.path.join(control_dir(), "%C")


def master_opts():
    """ssh options for a terminal: share an existing connection or become
    the master that others share."""
    return ["-o", "ControlMaster=auto", "-o", f"ControlPath={control_path()}",
            "-o", f"ControlPersist={PERSIST_SECONDS}"]


def client_opts():
    """ssh options for SFTP: use an existing master if there is one (combine
    with ControlMaster=no, so it never becomes one)."""
    return ["-o", f"ControlPath={control_path()}"]


def shutdown():
    """Closes every master of this app run and removes the sockets."""
    global _dir
    if _dir is None:
        return
    for name in os.listdir(_dir):
        sock = os.path.join(_dir, name)
        try:
            # The destination is required but unused: ControlPath has no tokens here.
            subprocess.run([SSH_BINARY, "-o", f"ControlPath={sock}", "-O", "exit", "x"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
    shutil.rmtree(_dir, ignore_errors=True)
    _dir = None
