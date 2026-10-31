"""Where the OpenSSH server programs used by the tests live; this differs
between distributions. None when not installed (openssh-server)."""

import os
import shutil


def _first(paths):
    return next((p for p in paths if os.path.exists(p)), None)


SFTP_SERVER = _first((
    "/usr/libexec/ssh/sftp-server",      # openSUSE
    "/usr/libexec/openssh/sftp-server",  # Fedora
    "/usr/lib/openssh/sftp-server",      # Debian, Ubuntu
    "/usr/lib/ssh/sftp-server",          # Arch
))
SSHD = _first(("/usr/sbin/sshd", "/usr/bin/sshd")) or shutil.which("sshd")
