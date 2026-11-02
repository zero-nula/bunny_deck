"""GUI test of SFTP authentication dialogs against real OpenSSH.

Uses a throwaway local sshd (tests/throwaway_sshd.py). The test answers the
real Qt CredentialDialogs the application shows: host-key confirmation,
a wrong secret (retry warning must appear), the correct secret, and a
cancelled login.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, find_top, local_shell_argv, make_profile, run  # noqa: E402
import throwaway_sshd  # noqa: E402

from PyQt6.QtWidgets import QLabel, QLineEdit  # noqa: E402

from bunnydeck import sftp  # noqa: E402
from bunnydeck.dialogs import CredentialDialog  # noqa: E402

SSHD = throwaway_sshd.ThrowawaySSHD()
_orig = sftp.ssh_sftp_argv
sftp.ssh_sftp_argv = lambda profile: _orig(profile, extra_opts=["-F", SSHD.config])


def visible_entries(path):
    """What the pane should list (hidden files are off by default). May be
    empty, e.g. a fresh home directory in CI."""
    return sorted(n for n in os.listdir(path) if not n.startswith("."))


def dialog():
    return find_top(CredentialDialog)


def dialog_text(dlg):
    return "\n".join(lbl.text() for lbl in dlg.findChildren(QLabel))


def test(app):
    win = app.window
    prof = make_profile("Throwaway", host=throwaway_sshd.HOST_ALIAS, user="")
    panel = win.open_profile(prof, argv=local_shell_argv())
    yield lambda: panel.child_pid
    panel.scp_btn.click()
    fm = win.file_manager
    R = fm.right

    yield dialog
    d = dialog()
    check(d is not None and d.confirm, "host-key confirmation dialog shown")
    check(d is not None and "authenticity of host" in dialog_text(d), "shows OpenSSH host-key text")
    d.accept()

    yield lambda: dialog() is not None and not dialog().confirm
    d = dialog()
    check(d is not None and d.entry is not None and
          d.entry.echoMode() == QLineEdit.EchoMode.Password, "secret prompt with hidden entry")
    check("passphrase" in dialog_text(d).lower(), "prompt text from OpenSSH shown")
    check("Authentication failed" not in dialog_text(d), "no retry warning on first prompt")
    d.entry.setText("wrong")
    d.accept()

    yield lambda: dialog() is not None and dialog() is not d
    d2 = dialog()
    check(d2 is not None and "Authentication failed" in dialog_text(d2),
          "wrong secret -> dialog asks again with a retry warning")
    d2.entry.setText(throwaway_sshd.PASSPHRASE)
    d2.accept()

    yield lambda: R.cwd is not None
    check(R.cwd == os.path.expanduser("~"), f"connected; remote home listed ({R.cwd})")
    check(sorted(R.names()) == visible_entries(R.cwd) and not R.error_visible(),
          "real directory contents shown")

    # --- cancelled login on a fresh connection ---------------------------
    prof2 = make_profile("Throwaway 2", host=throwaway_sshd.HOST_ALIAS, user="")
    p2 = win.open_profile(prof2, argv=local_shell_argv())
    yield lambda: p2.child_pid
    p2.scp_btn.click()
    yield lambda: dialog() is not None
    d3 = dialog()
    check(d3 is not None and not d3.confirm, "known host now: goes straight to secret prompt")
    d3.reject()
    yield lambda: R.error_visible() and "cancelled" in R.error_label.text()
    check("cancelled" in R.error_label.text(), f"cancel handled: {R.error_label.text()!r}")
    check(fm.pool.cached(prof2.id) is None, "no connection kept after cancel")
    yield 0.5
    check(dialog() is None, "no further prompts after cancel")
    check(panel.running and p2.running, "terminals unaffected")


if __name__ == "__main__":
    try:
        rc = run(test, timeout=90)
    finally:
        SSHD.stop()
    sys.exit(rc)
