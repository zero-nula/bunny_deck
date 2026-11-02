"""GUI test: deleting in the file manager, always after a confirmation.

A local sftp-server rooted in a temp directory stands in for a host (as in
gui_phase3.py). Home-folder protection is only exercised on that temp
"remote home", never on the real home directory.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, find_top, local_shell_argv, make_profile, run  # noqa: E402

from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from bunnydeck import sftp  # noqa: E402

from openssh_paths import SFTP_SERVER  # noqa: E402
BASE = tempfile.mkdtemp(prefix="bunnydeck-delete-")
ROOT = os.path.join(BASE, "ubuntu")
sftp.ssh_sftp_argv = lambda profile: [SFTP_SERVER, "-d", ROOT]


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def confirm_box():
    box = find_top(QMessageBox)
    return box if box is not None and "Delete" in box.text() else None


def button(box, text):
    return next(b for b in box.buttons() if b.text().replace("&", "") == text)


def test(app):
    write(os.path.join(ROOT, "project", "src", "main.py"))
    write(os.path.join(ROOT, "project", "README.md"))
    write(os.path.join(ROOT, "notes.txt"))
    write(os.path.join(ROOT, "other.txt"))
    os.makedirs(os.path.join(BASE, "outside"))
    write(os.path.join(BASE, "outside", "keep.txt"), "keep")
    os.symlink(os.path.join(BASE, "outside"), os.path.join(ROOT, "project", "ext"))

    win = app.window
    prof = app.config.add(make_profile("Ubuntu", host="ubuntu.invalid"))
    t = win.open_profile(prof, argv=local_shell_argv())
    yield lambda: t.child_pid
    t.scpRequested.emit(t)
    fm = win.file_manager
    R = fm.right
    yield lambda: R.cwd == ROOT and "project" in R.names()
    check(R.delete_btn.isVisible() and R.delete_btn.text() == "Delete", "panes have a Delete button")

    # --- Cancel deletes nothing -------------------------------------------
    R.select_names("project", "notes.txt")
    QTimer.singleShot(0, R.delete_btn.click)
    yield confirm_box
    box = confirm_box()
    info = box.informativeText()
    check(box.text() == "Delete 4 file(s) and 2 folder(s) on Ubuntu?",
          f"says how much will be deleted ({box.text()!r})")
    check("project/  (folder: 3 file(s), 1 subfolder(s))" in info and "• notes.txt" in info
          and "cannot be undone" in info, f"lists the items ({info!r})")
    check(box.defaultButton() is button(box, "Cancel"), "Cancel is the default button")
    button(box, "Cancel").click()
    yield lambda: not fm.busy
    check(os.path.exists(os.path.join(ROOT, "project", "src", "main.py"))
          and os.path.exists(os.path.join(ROOT, "notes.txt")), "Cancel deleted nothing")
    check(fm.status.text() == "Nothing deleted", f"status: {fm.status.text()!r}")

    # --- Delete removes them (a link inside: only the link) ----------------
    R.select_names("project", "notes.txt")
    QTimer.singleShot(0, R.delete_btn.click)
    yield confirm_box
    button(confirm_box(), "Delete").click()
    yield lambda: not fm.busy and "project" not in R.names()
    check(not os.path.exists(os.path.join(ROOT, "project"))
          and not os.path.exists(os.path.join(ROOT, "notes.txt")), "Delete removed them")
    check(os.path.exists(os.path.join(BASE, "outside", "keep.txt")),
          "what a link pointed to is untouched")
    check(os.path.exists(os.path.join(ROOT, "other.txt")), "unselected files untouched")
    check("Deleted 4 file(s) and 2 folder(s)" in fm.status.text(), f"status: {fm.status.text()!r}")
    check(R.names() == ["other.txt"], f"pane refreshed ({R.names()})")

    # --- the Delete key asks too -------------------------------------------
    R.select_names("other.txt")
    R.view.setFocus()
    QTimer.singleShot(0, lambda: QTest.keyClick(R.view, Qt.Key.Key_Delete))
    yield confirm_box
    check(confirm_box() is not None, "the Delete key opens the same confirmation")
    button(confirm_box(), "Cancel").click()
    yield lambda: not fm.busy
    check(os.path.exists(os.path.join(ROOT, "other.txt")), "still there after Cancel")

    # --- the (remote) home folder is protected -----------------------------
    errors = []
    fm.message = lambda title, text, error=False: errors.append(text)
    R.load(BASE)
    yield lambda: R.cwd == BASE and "ubuntu" in R.names()
    R.select_names("ubuntu")
    R.delete_btn.click()
    yield 0.3
    check(errors and "does not delete the root or your home folder" in errors[0]
          and confirm_box() is None, f"home folder refused before any question ({errors})")
    check(os.path.exists(os.path.join(ROOT, "other.txt")), "home folder intact")


if __name__ == "__main__":
    try:
        sys.exit(run(test, timeout=60))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
