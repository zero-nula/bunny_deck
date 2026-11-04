"""GUI test: editing a server's file in a local editor; every save is
uploaded again, and a change on the server is never overwritten silently.

A local sftp-server rooted in a temp directory stands in for a host (as in
gui_delete.py), and tests/fake_editor.py for the editor: it only records
which file it was given, and the test "saves" by writing that file itself,
in place and by renaming (as Zed, VS Code and vim do).
"""

import os
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = tempfile.mkdtemp(prefix="bunnydeck-edit-")
# The local copies go below the test's own cache folder, never ~/.cache.
os.environ["XDG_CACHE_HOME"] = os.path.join(BASE, "cache")

from gui_harness import check, find_top, local_shell_argv, make_profile, run  # noqa: E402

from PyQt6.QtWidgets import QMenu, QMessageBox  # noqa: E402

from bunnydeck import editors, sftp  # noqa: E402
from bunnydeck.remoteedit import edit_root  # noqa: E402

from openssh_paths import SFTP_SERVER  # noqa: E402

ROOT = os.path.join(BASE, "server")
sftp.ssh_sftp_argv = lambda profile: [SFTP_SERVER, "-d", ROOT]
FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_editor.py")
LOG = os.path.join(BASE, "editor.log")
LOG2 = os.path.join(BASE, "editor2.log")


def write(path, data, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def opened(log=LOG):
    return (read(log) or "").splitlines()


def save_by_rename(path, text):
    tmp = path + ".tmp~"
    write(tmp, text)
    os.replace(tmp, path)


def question(words):
    box = find_top(QMessageBox)
    return box if box is not None and words in box.text() else None


def button(box, text):
    return next(b for b in box.buttons() if b.text().replace("&", "") == text)


def test(app):
    conf = os.path.join(ROOT, "conf", "app.conf")
    write(conf, "a=1\n", mtime=1_700_000_000)
    os.symlink("conf/app.conf", os.path.join(ROOT, "link.conf"))
    write(os.path.join(ROOT, "conf", "data.bin"), b"\x7fELF\0\0binary")
    write(os.path.join(ROOT, "photo.png"), b"\x89PNG")
    write(os.path.join(ROOT, "other.txt"), "other\n")
    os.chmod(conf, 0o640)

    win = app.window
    errors = []
    prof = app.config.add(make_profile("Ubuntu", host="ubuntu.invalid"))
    t = win.open_profile(prof, argv=local_shell_argv())
    yield lambda: t.child_pid
    t.scpRequested.emit(t)
    fm = win.file_manager
    fm.message = lambda title, text, error=False: errors.append(text)
    R = fm.right
    yield lambda: R.cwd == ROOT and "conf" in R.names()
    app.config.set_editor(f"command:{sys.executable} {FAKE} {LOG}")

    # --- double-click opens a copy in the editor ---------------------------
    R.load(os.path.join(ROOT, "conf"))
    yield lambda: "app.conf" in R.names()
    item = R.view.findItems("app.conf", Qt_MatchExactly())[0]
    R.view.itemActivated.emit(item, 0)
    yield lambda: len(opened()) == 1
    local = opened()[0]
    check(os.path.dirname(os.path.dirname(local)) == edit_root()
          and os.path.basename(local) == "app.conf"
          and os.path.basename(os.path.dirname(local)).startswith("Ubuntu-"),
          f"the copy is in its own folder under the cache ({local})")
    check(read(local) == "a=1\n", "with the file's content")
    check(stat.S_IMODE(os.stat(local).st_mode) == 0o600
          and stat.S_IMODE(os.stat(os.path.dirname(local)).st_mode) == 0o700,
          "private: file 0600, folder 0700")
    check("Editing app.conf from Ubuntu" in fm.status.text(), f"status: {fm.status.text()!r}")

    # --- saves go back to the server ---------------------------------------
    write(local, "a=2\n")
    yield lambda: read(conf) == "a=2\n"
    check(True, "a save in place is uploaded")
    yield lambda: fm.status.text() == "Saved app.conf to Ubuntu"
    check(stat.S_IMODE(os.stat(conf).st_mode) == 0o640, "the server file keeps its permissions")
    save_by_rename(local, "a=3\n")
    yield lambda: read(conf) == "a=3\n"
    check(True, "a save by renaming is uploaded")
    save_by_rename(local, "a=4\n")
    yield lambda: read(conf) == "a=4\n"
    check(True, "and the next one too (the watch follows the new file)")
    check(not [n for n in os.listdir(os.path.join(ROOT, "conf")) if "bunnydeck-part" in n],
          "no temporary files left on the server")

    # --- opening it again reuses the copy; a symlink edits its target ------
    fm.edit_file(R, "app.conf")
    yield lambda: len(opened()) == 2
    check(opened()[1] == local, "opening it again shows the same copy (no new download)")
    R.load(ROOT)
    yield lambda: "link.conf" in R.names()
    fm.edit_file(R, "link.conf")
    yield lambda: len(opened()) == 3
    check(opened()[2] == local, "a symlink to it opens the same copy")
    write(local, "a=5\n")
    yield lambda: read(conf) == "a=5\n"
    check(os.path.islink(os.path.join(ROOT, "link.conf")), "the symlink stays a symlink")

    # --- a change on the server is never overwritten silently --------------
    write(conf, "changed on server\n", mtime=1_700_000_100)
    write(local, "a=6\n")
    yield lambda: question("was changed on the server")
    box = question("was changed on the server")
    check(box.defaultButton() is button(box, "Cancel"), "Cancel is the default")
    button(box, "Cancel").click()
    yield lambda: "not saved" in fm.status.text()
    check(read(conf) == "changed on server\n", "Cancel keeps the server's version")
    check(fm.unsaved_edits() == ["app.conf"], "and the file counts as not saved")
    write(local, "a=7\n")
    yield lambda: question("was changed on the server")
    button(question("was changed on the server"), "Replace on Server").click()
    yield lambda: read(conf) == "a=7\n"
    check(True, "Replace on Server uploads")
    yield lambda: fm.status.text() == "Saved app.conf to Ubuntu"
    check(fm.unsaved_edits() == [], "and it counts as saved again")

    os.remove(conf)
    write(local, "a=8\n")
    yield lambda: question("was deleted on the server")
    button(question("was deleted on the server"), "Create It").click()
    yield lambda: read(conf) == "a=8\n"
    check(True, "a file deleted on the server can be created again")

    # --- not a text file ---------------------------------------------------
    R.load(os.path.join(ROOT, "conf"))
    yield lambda: "data.bin" in R.names()
    fm.edit_file(R, "data.bin")
    yield lambda: question("does not look like a text file")
    button(question("does not look like a text file"), "Cancel").click()
    yield lambda: fm.status.text() == "Not opened"
    check(len(opened()) == 3 and len(os.listdir(edit_root())) == 1,
          "a binary file is only opened after asking; Cancel leaves no copy")
    R.load(ROOT)
    yield lambda: "photo.png" in R.names()
    R.view.itemActivated.emit(R.view.findItems("photo.png", Qt_MatchExactly())[0], 0)
    yield 0.3
    check("not a text file" in fm.status.text() and len(opened()) == 3,
          f"double-clicking an image on a server does not open the editor "
          f"({fm.status.text()!r})")

    # --- Edit With, and files on this computer -----------------------------
    other = editors.Editor("", "Other", f"{sys.executable} {FAKE} {LOG2}")
    fm.edit_file(R, "other.txt", other)
    yield lambda: len(opened(LOG2)) == 1
    check(opened(LOG2)[0].endswith("/other.txt") and len(opened()) == 3,
          "Edit With uses the chosen editor once")
    local_dir = os.path.join(BASE, "here")
    write(os.path.join(local_dir, "notes.txt"), "local\n")
    L = fm.left
    L.load(local_dir)
    yield lambda: "notes.txt" in L.names()
    fm.edit_file(L, "notes.txt")
    yield lambda: len(opened()) == 4
    check(opened()[3] == os.path.join(local_dir, "notes.txt"),
          "a file on this computer is opened where it is (no copy)")

    # --- Default Editor menu -----------------------------------------------
    menu = QMenu()
    fm._fill_default_editor_menu(menu, [editors.Editor("zed.desktop", "Zed", "zed %U")])
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    checked = [a.text() for a in menu.actions() if a.isChecked()]
    check(texts[0].startswith("System Default") and "Zed" in texts
          and texts[-1].startswith("Custom Command: "), f"Default Editor menu: {texts}")
    check(len(checked) == 1 and checked[0].startswith("Custom Command"),
          f"the current choice is ticked ({checked})")
    menu.deleteLater()
    fm.set_default_editor("desktop:zed.desktop")
    check('"editor": "desktop:zed.desktop"' in read(app.config.path),
          "the choice is saved in config.json")
    app.config.set_editor(f"command:{sys.executable} {FAKE} {LOG}")

    # --- quitting removes the copies, except unsaved ones ------------------
    write(conf, "changed again\n", mtime=1_700_000_200)
    write(local, "a=9\n")
    yield lambda: question("was changed on the server")
    button(question("was changed on the server"), "Cancel").click()
    yield lambda: "not saved" in fm.status.text()
    kept = fm.edits.shutdown()
    check(kept == [local] and read(local) == "a=9\n", f"an unsaved copy is kept ({kept})")
    check(sorted(os.listdir(edit_root())) == [os.path.basename(os.path.dirname(local))],
          f"the other copies are removed ({os.listdir(edit_root())})")
    check(not errors, f"no error messages ({errors})")


def Qt_MatchExactly():
    from PyQt6.QtCore import Qt

    return Qt.MatchFlag.MatchExactly


if __name__ == "__main__":
    try:
        sys.exit(run(test, timeout=90))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
