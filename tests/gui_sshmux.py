"""GUI test: a terminal and its file manager share one SSH connection.

Against a throwaway local sshd (tests/throwaway_sshd.py): the passphrase is
typed into the real terminal once; SCP then opens without a credential
dialog, and the terminal still ends at once on `exit` while the file manager
keeps using the connection."""

import atexit
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, find_top, make_profile, run, term_text  # noqa: E402
import throwaway_sshd  # noqa: E402

from bunnydeck import sftp, sshmux  # noqa: E402
from bunnydeck.dialogs import CredentialDialog  # noqa: E402

SSHD = throwaway_sshd.ThrowawaySSHD()
atexit.register(SSHD.stop)
_orig = sftp.ssh_sftp_argv
sftp.ssh_sftp_argv = lambda profile: _orig(profile, extra_opts=["-F", SSHD.config])


def test(app):
    win = app.window
    prof = make_profile("Shared", host=throwaway_sshd.HOST_ALIAS, user="")
    # What the app runs for this profile, plus -F for the throwaway sshd.
    argv = [sshmux.SSH_BINARY, "-F", SSHD.config, *sshmux.master_opts(), *prof.ssh_args()]
    panel = win.open_profile(prof, argv=argv)

    yield lambda: "(yes/no" in term_text(panel)
    panel.terminal.sendText("yes\n")
    yield lambda: "passphrase" in term_text(panel).lower()
    check(True, "OpenSSH asks for the passphrase in the terminal")
    panel.terminal.sendText(throwaway_sshd.PASSPHRASE + "\n")
    # OpenSSH drops input typed ahead while it reads the passphrase, so keep
    # asking until the remote shell answers.
    for _ in range(15):
        panel.terminal.sendText("echo REMOTE-$((40+2))\n")
        yield 1.0
        if "REMOTE-42" in term_text(panel):
            break
    check("REMOTE-42" in term_text(panel), "logged in through the terminal")

    panel.scpRequested.emit(panel)
    fm = win.file_manager
    R = fm.right
    yield lambda: R.cwd is not None or find_top(CredentialDialog) is not None
    check(find_top(CredentialDialog) is None, "SCP opened without asking for credentials")
    home = os.path.expanduser("~")
    check(R.cwd == home and not R.error_visible()
          and sorted(R.names()) == sorted(n for n in os.listdir(home) if not n.startswith(".")),
          f"remote files listed over the shared connection ({R.cwd})")

    win.show_terminals()
    panel.terminal.sendText("exit\n")
    yield lambda: not panel.running
    check(not panel.running, "terminal ends on exit although the file manager uses the connection")
    client = fm.pool.cached(prof.id)
    check(client is not None and isinstance(client.listdir(client.home), list),
          "file manager still works after the terminal closed")


if __name__ == "__main__":
    sys.exit(run(test, timeout=60))
