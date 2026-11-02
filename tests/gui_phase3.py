"""Phase 3/4 GUI test: SCP button -> integrated dual-pane file manager.

The SFTP transport is replaced by local sftp-server processes rooted in two
temp directories standing in for the "Ubuntu" and "Raspberry Pi" hosts.
Everything above the transport (panes, pool, transfer worker, dialog
callbacks, drag and drop handling) is the real application code.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, local_shell_argv, make_profile, run, screenshot  # noqa: E402

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PyQt6.QtGui import QDropEvent  # noqa: E402
from PyQt6.QtWidgets import QPushButton, QToolButton  # noqa: E402

from bunnydeck import sftp  # noqa: E402
from bunnydeck.filemanager import MIME_INTERNAL  # noqa: E402

from openssh_paths import SFTP_SERVER  # noqa: E402
SHOTS = os.environ.get("BUNNYDECK_SHOTS", "")
BASE = tempfile.mkdtemp(prefix="bunnydeck-fm-")
ROOTS = {"ubuntu.invalid": os.path.join(BASE, "ubuntu"),
         "rpi.invalid": os.path.join(BASE, "rpi")}
LOCAL = os.path.join(BASE, "local")
sftp.ssh_sftp_argv = lambda profile: [SFTP_SERVER, "-d", ROOTS[profile.host]]


def setup_tree():
    for d in list(ROOTS.values()) + [LOCAL]:
        os.makedirs(os.path.join(d, "Documents"))
    with open(os.path.join(LOCAL, "report.txt"), "w") as f:
        f.write("local report")
    with open(os.path.join(LOCAL, "dropped.txt"), "w") as f:
        f.write("from dolphin")
    with open(os.path.join(ROOTS["rpi.invalid"], "sensor.csv"), "w") as f:
        f.write("t,v\n1,2\n" * 50000)
    with open(os.path.join(ROOTS["ubuntu.invalid"], "notes.md"), "w") as f:
        f.write("ubuntu notes")


def read(p):
    with open(p) as f:
        return f.read()


def drop(pane, mime):
    ev = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pane.view.dropEvent(ev)
    return ev.isAccepted()


def test(app):
    setup_tree()
    win = app.window
    ubuntu = app.config.add(make_profile("Ubuntu", host="ubuntu.invalid"))
    rpi = app.config.add(make_profile("Raspberry Pi", host="rpi.invalid", user="pi"))
    win.resize(1600, 900)
    t_u = win.open_profile(ubuntu, argv=local_shell_argv())
    t_r = win.open_profile(rpi, argv=local_shell_argv())
    yield lambda: t_u.child_pid and t_r.child_pid
    pids = (t_u.child_pid, t_r.child_pid)

    # --- SCP button on the Ubuntu terminal ------------------------------
    t_u.scpRequested.emit(t_u)
    fm = win.file_manager
    check(fm is not None and win.current_view() == "files",
          "SCP opens the integrated file manager in the same window")
    L, R = fm.left, fm.right
    yield lambda: R.cwd is not None and L.cwd is not None
    check(R.cwd == ROOTS["ubuntu.invalid"], f"right pane = Ubuntu home ({R.cwd})")
    check(L.source_id == "__local__", "left pane = Local")
    check(set(R.names()) == {"Documents", "notes.md"}, f"real remote contents {R.names()}")
    sources = [L.source.itemText(i) for i in range(L.source.count())]
    check(any("Ubuntu" in s for s in sources) and any("Raspberry" in s for s in sources)
          and any("This computer" in s for s in sources), f"pane sources {sources}")

    # --- editable path, absolute input never duplicated ------------------
    R.path_entry.setText(os.path.join(ROOTS["ubuntu.invalid"], "Documents"))
    R.path_entry.returnPressed.emit()
    yield lambda: R.cwd and R.cwd.endswith("Documents")
    check(R.cwd == os.path.join(ROOTS["ubuntu.invalid"], "Documents"),
          f"absolute path used as-is: {R.cwd}")
    R.go_up()
    yield lambda: R.cwd == ROOTS["ubuntu.invalid"]
    check(R.cwd == ROOTS["ubuntu.invalid"], "parent navigation")
    R.path_entry.setText("Documents")
    R.path_entry.returnPressed.emit()
    yield lambda: R.cwd and R.cwd.endswith("Documents")
    check(R.cwd == os.path.join(ROOTS["ubuntu.invalid"], "Documents"), "relative path resolved")

    # --- inaccessible directory shows an error, not a silent empty list --
    R.path_entry.setText("/definitely/not/here")
    R.path_entry.returnPressed.emit()
    yield lambda: R.error_visible()
    check(R.error_visible() and "No such file" in R.error_label.text(),
          f"error shown: {R.error_label.text()!r}")
    R.go_home()
    yield lambda: R.cwd == ROOTS["ubuntu.invalid"] and not R.error_visible()
    check(not R.error_visible(), "error cleared after successful navigation")

    # --- Local -> Ubuntu upload -----------------------------------------
    L.path_entry.setText(LOCAL)
    L.path_entry.returnPressed.emit()
    yield lambda: L.cwd == LOCAL and "report.txt" in L.names()
    L.select_names("report.txt")
    fm.copy_selection(L, R)
    yield lambda: not fm.busy and "report.txt" in R.names()
    check(read(os.path.join(ROOTS["ubuntu.invalid"], "report.txt")) == "local report",
          "Local -> Ubuntu upload")
    check("Copied 1 file" in fm.status.text(), f"status: {fm.status.text()!r}")

    # --- overwrite confirmation: skip, then replace ----------------------
    asked = []
    with open(os.path.join(LOCAL, "report.txt"), "w") as f:
        f.write("local report v2")
    fm._ask_overwrite = lambda path, where, multi: asked.append(path) or "skip"
    fm.copy_selection(L, R)
    yield lambda: not fm.busy and len(asked) == 1
    yield 0.2
    check(asked == [os.path.join(ROOTS["ubuntu.invalid"], "report.txt")],
          "overwrite confirmation requested with the correct destination path")
    check(read(os.path.join(ROOTS["ubuntu.invalid"], "report.txt")) == "local report",
          "skip keeps the existing remote file")
    fm._ask_overwrite = lambda path, where, multi: asked.append(path) or "replace"
    fm.copy_selection(L, R)
    yield lambda: not fm.busy and len(asked) == 2
    yield 0.3
    check(read(os.path.join(ROOTS["ubuntu.invalid"], "report.txt")) == "local report v2",
          "confirmed replace overwrites")

    # --- drag & drop: from Dolphin (file:// URL) and between panes --------
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(os.path.join(LOCAL, "dropped.txt"))])
    check(drop(R, md), "drop of a local file URL accepted")
    yield lambda: not fm.busy and "dropped.txt" in R.names()
    check(read(os.path.join(ROOTS["ubuntu.invalid"], "dropped.txt")) == "from dolphin",
          "drop from a file manager uploads into the pane's folder")
    R.select_names("notes.md")
    md = R.view.mimeData(R.view.selectedItems())
    payload = json.loads(bytes(md.data(MIME_INTERNAL)).decode())
    check(payload == {"side": "right",
                      "paths": [os.path.join(ROOTS["ubuntu.invalid"], "notes.md")]},
          f"drag payload carries the selected remote path {payload}")
    check(not drop(R, md), "drop onto the same pane is refused")
    L.path_entry.setText(os.path.join(LOCAL, "Documents"))
    L.path_entry.returnPressed.emit()
    yield lambda: L.cwd == os.path.join(LOCAL, "Documents")
    check(drop(L, md), "drag from the right pane accepted by the left pane")
    yield lambda: not fm.busy and "notes.md" in L.names()
    check(read(os.path.join(LOCAL, "Documents", "notes.md")) == "ubuntu notes",
          "Ubuntu -> Local by drag and drop, into the selected folder")

    # --- Raspberry Pi (left) -> Ubuntu (right) ---------------------------
    L.select_source(rpi.id)
    yield lambda: L.cwd == ROOTS["rpi.invalid"] and "sensor.csv" in L.names()
    check(L.cwd == ROOTS["rpi.invalid"], "left pane switched to Raspberry Pi")
    R.path_entry.setText("Documents")
    R.path_entry.returnPressed.emit()
    yield lambda: R.cwd == os.path.join(ROOTS["ubuntu.invalid"], "Documents")
    L.select_names("sensor.csv")
    fm.copy_selection(L, R)
    yield lambda: not fm.busy and "sensor.csv" in R.names()
    check(read(os.path.join(ROOTS["ubuntu.invalid"], "Documents", "sensor.csv")) ==
          read(os.path.join(ROOTS["rpi.invalid"], "sensor.csv")),
          "Raspberry Pi -> Ubuntu (remote to remote via local staging)")
    check(os.path.exists(os.path.join(ROOTS["rpi.invalid"], "sensor.csv")),
          "source file not deleted")

    # --- Ubuntu -> Raspberry Pi -------------------------------------------
    R.select_names("sensor.csv")
    L.path_entry.setText("Documents")
    L.path_entry.returnPressed.emit()
    yield lambda: L.cwd == os.path.join(ROOTS["rpi.invalid"], "Documents")
    fm.copy_selection(R, L)
    yield lambda: not fm.busy and "sensor.csv" in L.names()
    check(os.path.exists(os.path.join(ROOTS["rpi.invalid"], "Documents", "sensor.csv")),
          "Ubuntu -> Raspberry Pi (remote to remote)")
    parts = [os.path.join(dp, f) for dp, _, fs in os.walk(BASE) for f in fs
             if "bunnydeck-part" in f]
    check(not parts, "no temporary part files left behind")
    img = win.grab().toImage()
    mid = fm.to_right.mapTo(win, fm.to_right.rect().center())
    gap = img.pixelColor(mid.x(), 140)
    check(gap.alpha() == 255, f"file manager background is opaque (gap alpha {gap.alpha()})")
    if SHOTS:
        screenshot(win, os.path.join(SHOTS, "qt-file-manager.png"))

    # --- the only delete actions are the panes' Delete buttons -------------
    # (they always ask first: tests/gui_delete.py)
    labels = [b.text().lower() for b in fm.findChildren(QPushButton) + fm.findChildren(QToolButton)]
    check(labels.count("delete") == 2 and not any("remove" in s for s in labels),
          f"one Delete button per pane, nothing else deletes ({len(labels)} buttons checked)")

    # --- terminals kept running in the background -------------------------
    check((t_u.child_pid, t_r.child_pid) == pids and t_u.running and t_r.running,
          "SSH terminals stayed alive while the file manager was used")
    win.show_terminals()
    check(win.current_view() == "terminals", "back to terminals")
    shutil.rmtree(BASE, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run(test, timeout=90))
