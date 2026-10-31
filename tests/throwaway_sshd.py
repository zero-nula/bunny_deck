"""A throwaway, unprivileged sshd on 127.0.0.1 for tests.

Everything (host key, client key, authorized_keys, ssh_config, known_hosts)
lives in a private temp directory; ssh is always run with -F <tmp config>, so
~/.ssh is never read or modified. The client key has a passphrase, because an
unprivileged sshd cannot verify system passwords; OpenSSH asks for that
passphrase through the same SSH_ASKPASS path it uses for passwords.
"""

import getpass
import os
import shutil
import socket
import subprocess
import tempfile
import time

from openssh_paths import SFTP_SERVER, SSHD

PASSPHRASE = "correct horse"
HOST_ALIAS = "deck-test"


def available():
    return bool(SSHD and SFTP_SERVER)


class ThrowawaySSHD:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="bunnydeck-sshd-")
        os.chmod(self.tmp, 0o700)
        t = self.tmp
        kg = ["ssh-keygen", "-q", "-t", "ed25519"]
        subprocess.run(kg + ["-N", "", "-f", f"{t}/host_key"], check=True)
        subprocess.run(kg + ["-N", PASSPHRASE, "-f", f"{t}/client_key"], check=True)
        shutil.copy(f"{t}/client_key.pub", f"{t}/authorized_keys")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        user = getpass.getuser()
        with open(f"{t}/sshd_config", "w") as f:
            f.write(f"Port {self.port}\nListenAddress 127.0.0.1\nHostKey {t}/host_key\n"
                    f"PidFile {t}/sshd.pid\nAuthorizedKeysFile {t}/authorized_keys\n"
                    "StrictModes no\nUsePAM no\nPasswordAuthentication no\n"
                    "KbdInteractiveAuthentication no\nPubkeyAuthentication yes\n"
                    f"AllowUsers {user}\nSubsystem sftp {SFTP_SERVER}\n")
        with open(f"{t}/ssh_config", "w") as f:
            f.write(f"Host {HOST_ALIAS}\n  HostName 127.0.0.1\n  Port {self.port}\n"
                    f"  User {user}\n  IdentityFile {t}/client_key\n  IdentitiesOnly yes\n"
                    "  IdentityAgent none\n  PasswordAuthentication no\n"
                    f"  UserKnownHostsFile {t}/known_hosts\n  GlobalKnownHostsFile /dev/null\n"
                    "  StrictHostKeyChecking ask\n")
        self.config = f"{t}/ssh_config"
        self.proc = subprocess.Popen([SSHD, "-D", "-e", "-f", f"{t}/sshd_config"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("throwaway sshd did not start")

    def reset_known_hosts(self):
        try:
            os.unlink(f"{self.tmp}/known_hosts")
        except FileNotFoundError:
            pass

    def stop(self):
        self.proc.terminate()
        self.proc.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)
