"""GUI test: broadcast typing. Turning Broadcast on ticks no terminal; keys
typed in one ticked terminal (letters, Enter, Ctrl+C) and pastes go to every
ticked terminal and nowhere else; Ctrl+Shift+B turns it off."""

import os
import sys

os.environ["SHELL"] = "/bin/sh"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, local_shell_argv, make_profile, run, term_text  # noqa: E402

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def type_line(panel, text):
    QTest.keyClicks(panel.display, text)
    QTest.keyClick(panel.display, Qt.Key.Key_Return)


def test(app):
    win = app.window
    win.resize(1400, 700)
    # SSH profiles (running a local shell, so no server is needed) and a local terminal.
    a, b, c = (win.open_profile(make_profile(n, host=f"{n}.invalid"), argv=local_shell_argv())
               for n in ("web1", "web2", "db"))
    local = win.open_local()
    yield lambda: all(p.child_pid for p in (a, b, c, local))
    yield 0.5
    check(not any(p.broadcast_box.isVisible() for p in (a, b, c, local)),
          "broadcast is off by default: no tick boxes")

    win.broadcast_btn.click()
    check(win.broadcasting and all(p.broadcast_box.isVisible() for p in (a, b, c, local)),
          "Broadcast shows a tick box on every terminal")
    check(not any(p.broadcast for p in (a, b, c, local))
          and win.broadcast_btn.text() == "⇉ Broadcast: 0",
          "nothing is ticked: the user picks the terminals")
    type_line(a, "echo NONE-$((5*5))")
    yield lambda: "NONE-25" in term_text(a)
    yield 0.3
    check("NONE-25" not in term_text(b), "typing in an unticked terminal stays there")

    for p in (a, b, c):
        p.broadcast_box.click()
    check(win.broadcast_btn.text() == "⇉ Broadcast: 3", f"button counts ({win.broadcast_btn.text()})")
    check(a.property("broadcast") is True, "ticked terminals are highlighted")
    c.broadcast_box.click()
    check(not c.broadcast and win.broadcast_btn.text() == "⇉ Broadcast: 2",
          "unticking a terminal takes it out")

    type_line(a, "echo BC-$((6*7))")
    yield lambda: "BC-42" in term_text(b)
    check("BC-42" in term_text(a) and "BC-42" in term_text(b), "typed once, ran in both ticked")
    yield 0.3
    check("BC-42" not in term_text(c) and "BC-42" not in term_text(local),
          "unticked and local terminals received nothing")

    # Ctrl+C reaches the others too: without it, b would sleep for 30 s.
    type_line(b, "sleep 30")
    yield 0.4
    QTest.keyClick(b.display, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    yield 0.3
    type_line(b, "echo AFTER-$((1+1))")
    yield lambda: "AFTER-2" in term_text(a)
    check("AFTER-2" in term_text(a), "Ctrl+C and typing from another ticked terminal work")

    QApplication.clipboard().setText("echo PASTE-$((2+3))")
    a.paste()
    QTest.keyClick(a.display, Qt.Key.Key_Return)
    yield lambda: "PASTE-5" in term_text(b)
    check("PASTE-5" in term_text(b) and "PASTE-5" not in term_text(c), "paste is broadcast too")

    d = win.open_profile(make_profile("web3", host="web3.invalid"), argv=local_shell_argv())
    yield lambda: d.child_pid
    check(d.broadcast_box.isVisible() and not d.broadcast,
          "a terminal opened during broadcast joins only when ticked")

    win.close_panel(b)
    check(win.broadcast_btn.text() == "⇉ Broadcast: 1", "closing a terminal updates the count")

    QTest.keyClick(a.display, Qt.Key.Key_B,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    check(not win.broadcasting and not win.broadcast_btn.isChecked(),
          "Ctrl+Shift+B in a terminal turns broadcast off")
    check(not a.broadcast_box.isVisible() and not a.broadcast and a.property("broadcast") is False,
          "tick boxes and highlight gone")
    check(win.broadcast_btn.text() == "⇉ Broadcast", "button back to normal")
    type_line(a, "echo SOLO-$((3+4))")
    yield lambda: "SOLO-7" in term_text(a)
    yield 0.3
    check("SOLO-7" not in term_text(c) + term_text(d) + term_text(local),
          "after turning it off, typing stays in one terminal")


if __name__ == "__main__":
    sys.exit(run(test, timeout=60))
