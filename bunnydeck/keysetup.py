"""Setting up SSH key login for a connection, step by step.

1. Key: use ~/.ssh/id_ed25519, or create it with ssh-keygen. OpenSSH uses
   this file by default, so no further configuration is needed for it.
2. Install: append the public key to the server's ~/.ssh/authorized_keys
   (only if it is not there yet), over the terminal's shared connection when
   there is one (no password needed), otherwise with one password prompt.
3. Verify: log in with only that key (no password, no shared connection).

Secrets never appear on a command line: ssh-keygen and ssh ask for them
through SSH_ASKPASS, answered by the app (askpass.py). Nothing here touches
the server's sshd configuration; disabling password logins stays a manual,
deliberate step.
"""

import os
import subprocess
import threading

from . import sshmux
from .askpass import AskpassServer
from .connections import run_on_main_sync

SSH_BINARY = "/usr/bin/ssh"
SSH_KEYGEN = "/usr/bin/ssh-keygen"
KEY_NAME = "id_ed25519"

# Appends the key read from stdin to authorized_keys unless it is already
# there; run by sh whatever the remote login shell is. {dir} is ~/.ssh.
_INSTALL = (
    "umask 077; d={dir}; mkdir -p \"$d\" && touch \"$d/authorized_keys\" && "
    "IFS= read -r key && "
    "{{ grep -qxF \"$key\" \"$d/authorized_keys\" && echo BUNNYDECK-PRESENT || "
    "{{ printf '%s\\n' \"$key\" >> \"$d/authorized_keys\" && echo BUNNYDECK-ADDED; }}; }} && "
    "{{ command -v restorecon >/dev/null 2>&1 && restorecon -F \"$d\" \"$d/authorized_keys\""
    " >/dev/null 2>&1; true; }}"
)


class StepFailed(Exception):
    pass


def key_path():
    return os.path.join(os.path.expanduser("~/.ssh"), KEY_NAME)


def key_exists():
    return os.path.isfile(key_path()) and os.path.isfile(key_path() + ".pub")


def fingerprint(path=None):
    """`SHA256:...` of the public key, or "" if it cannot be read."""
    try:
        out = subprocess.run([SSH_KEYGEN, "-l", "-f", (path or key_path()) + ".pub"],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = out.stdout.split()
    return parts[1] if out.returncode == 0 and len(parts) > 1 else ""


def _sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


class KeySetup:
    """Runs the steps for one connection profile. ask(profile, prompt, kind,
    retry) is called on the UI thread (through call_soon) for OpenSSH
    prompts, like ConnectionPool's. All methods block: run them off the UI
    thread. extra_opts are put before every ssh option (tests use -F)."""

    def __init__(self, profile, ask, call_soon, extra_opts=(), verify_opts=None,
                 remote_dir="~/.ssh"):
        self.profile = profile
        self.ask = ask
        self.call_soon = call_soon
        self.extra_opts = list(extra_opts)
        self.verify_opts = list(self.extra_opts if verify_opts is None else verify_opts)
        self.remote_dir = remote_dir
        self._askpass = AskpassServer()
        self._seq = 0

    def close(self):
        self._askpass.close()

    # ----- running OpenSSH tools with prompts answered by the app ----------

    def _run(self, argv, answer=None, stdin=b""):
        """Runs argv with SSH_ASKPASS routed to answer(prompt, kind), or to
        the user through ask(); returns (returncode, stdout, stderr)."""
        self._seq += 1
        conn_id = f"keysetup-{self._seq}"
        seen = {}

        def handler(prompt, kind):
            if answer is not None:
                return answer(prompt, kind)
            key = prompt.strip()
            retry = kind == "secret" and seen.get(key, 0) > 0
            seen[key] = seen.get(key, 0) + 1
            return run_on_main_sync(self.call_soon, self.ask, self.profile, prompt, kind, retry)

        self._askpass.register(conn_id, handler)
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, timeout=300,
                               env={**os.environ, **self._askpass.env_for(conn_id)},
                               start_new_session=True)  # no tty: prompts go to askpass
        except subprocess.TimeoutExpired:
            raise StepFailed("Timed out.")
        except OSError as e:
            raise StepFailed(f"Could not run {os.path.basename(argv[0])}: {e.strerror}")
        finally:
            self._askpass.unregister(conn_id)
        return (p.returncode, p.stdout.decode(errors="replace"),
                p.stderr.decode(errors="replace"))

    @staticmethod
    def _error(stderr, fallback):
        lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        return lines[-1] if lines else fallback

    # ----- the steps --------------------------------------------------------

    def create_key(self, passphrase):
        """Creates ~/.ssh/id_ed25519 (never overwrites an existing key)."""
        if key_exists():
            return f"Using your existing key {key_path()}"
        os.makedirs(os.path.dirname(key_path()), mode=0o700, exist_ok=True)
        comment = f"{os.environ.get('USER', 'user')}@{os.uname().nodename} (Bunny Deck)"
        code, _, err = self._run(
            [SSH_KEYGEN, "-q", "-t", "ed25519", "-f", key_path(), "-C", comment],
            answer=lambda prompt, kind: passphrase)
        if code != 0 or not key_exists():
            raise StepFailed(self._error(err, "ssh-keygen failed."))
        return f"Created {key_path()}"

    def install_key(self):
        """Adds the public key to the server's authorized_keys."""
        with open(key_path() + ".pub", encoding="utf-8") as f:
            pub = f.read().strip()
        if not pub.startswith("ssh-"):
            raise StepFailed(f"{key_path()}.pub does not look like a public key.")
        remote_dir = '"$HOME/.ssh"' if self.remote_dir == "~/.ssh" else _sh_quote(self.remote_dir)
        command = "exec sh -c " + _sh_quote(_INSTALL.format(dir=remote_dir))
        argv = [SSH_BINARY, *self.extra_opts, "-o", "ControlMaster=no", *sshmux.client_opts(),
                "-o", "BatchMode=no", "-T", "-x", "-a", *self.profile.ssh_args(), command]
        code, out, err = self._run(argv, stdin=(pub + "\n").encode())
        if code != 0 or "BUNNYDECK-" not in out:
            raise StepFailed(self._error(err, "Could not add the key on the server."))
        if "BUNNYDECK-PRESENT" in out:
            return "The key was already authorized on the server"
        return "Key added to ~/.ssh/authorized_keys on the server"

    def verify(self):
        """Logs in with only the key, bypassing the shared connection."""
        argv = [SSH_BINARY, *self.verify_opts,
                "-o", "ControlPath=none", "-o", "ControlMaster=no", "-o", "BatchMode=no",
                "-o", "PreferredAuthentications=publickey",
                "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
                "-o", "IdentitiesOnly=yes", "-i", key_path(),
                "-T", "-x", "-a", *self.profile.ssh_args(), "true"]
        code, _, err = self._run(argv)
        if code != 0:
            raise StepFailed(self._error(err, "Logging in with the key failed."))
        return "Logged in with the key, without a password"


def run_in_thread(fn, done):
    """Runs fn() on a worker thread and done(result, error) on the UI thread."""
    from .mainthread import call_soon

    def work():
        try:
            result, error = fn(), None
        except StepFailed as e:
            result, error = None, str(e)
        except Exception as e:  # unexpected; report rather than crash the UI
            result, error = None, f"Unexpected error: {e}"
        call_soon(lambda: done(result, error))

    threading.Thread(target=work, daemon=True).start()
