"""Phase 1 GUI test: window, QTermWidget, local shell interaction, real ssh
spawn, Add SSH dialog and Saved Connections."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import (  # noqa: E402
    check, find_top, local_shell_argv, make_profile, run, term_text,
)

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from bunnydeck.dialogs import AddSSHDialog  # noqa: E402


def test(app):
    win = app.window
    check(win is not None, "main window created")
    check(win.windowTitle() == "Bunny Deck", "window title")
    check(QApplication.desktopFileName() == "org.bunnydeck.BunnyDeck", "desktop file name / app id")
    check(win.current_view() == "empty", "empty state shown with no sessions")

    p = win.open_profile(make_profile("Local shell"), argv=local_shell_argv())
    yield lambda: p.child_pid
    check(p.child_pid and p.running, "terminal child process spawned")
    check(win.current_view() == "terminals", "terminal view shown")
    yield 0.5
    p.terminal.sendText("PS1='deck$ '; printf '\\033[31mRED\\033[0m %s\\n' ok\n")
    yield lambda: "RED ok" in term_text(p)
    check("RED ok" in term_text(p), "shell executed command; ANSI output rendered")

    # clipboard keys reach the panel even though TerminalDisplay eats keys
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    calls = []
    p.copy, p.paste = (lambda: calls.append("copy")), (lambda: calls.append("paste"))
    ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    QTest.keyClick(p.display, Qt.Key.Key_C, ctrl_shift)
    QTest.keyClick(p.display, Qt.Key.Key_V, ctrl_shift)
    QTest.keyClick(p.display, Qt.Key.Key_Insert, Qt.KeyboardModifier.ShiftModifier)
    check(calls == ["copy", "paste", "paste"], f"Ctrl+Shift+C/V and Shift+Insert handled {calls}")
    del p.copy, p.paste

    p.terminal.sendText("exit\n")
    yield lambda: not p.running
    check(not p.running, "child exit detected")
    check(p.status_label.text() == "disconnected", f"status shows exit ({p.status_label.text()!r})")
    check(p.status_label.isVisible() and p.status_label.width() >= 40,
          f"status label actually visible in the header (width {p.status_label.width()})")
    # Regression: QTermWidget's default autoClose hid the display when the
    # session ended, leaving a fully transparent hole in the window.
    from PyQt6.QtCore import QPoint
    yield 0.3
    img = win.grab().toImage()
    pt = p.mapTo(win, QPoint(p.width() // 2, p.height() // 2))
    px = img.pixelColor(pt.x(), pt.y())
    check(p.display.isVisible() and px.alpha() == 255,
          f"terminal stays painted after the session ended (alpha {px.alpha()})")
    check("RED ok" in term_text(p), "last output stays readable after the session ended")

    # Real ssh binary against a closed local port: verifies the ssh argv and
    # spawn path without contacting any host.
    prof = make_profile("Refused", host="127.0.0.1", user="nobody", port=1)
    s = win.open_profile(prof)
    check(s.argv[:1] == ["/usr/bin/ssh"] and "-p" in s.argv and s.argv[-2:] == ["--", "127.0.0.1"],
          f"ssh argv {s.argv}")
    yield lambda: not s.running
    txt = term_text(s)
    check("Connection refused" in txt or "connect to host" in txt,
          "ssh ran inside QTermWidget and reported connection refused")
    check(s.status_label.text() == "disconnected", "status after ssh exit")

    win.close_panel(p)
    win.close_panel(s)
    yield 0.1
    check(win.current_view() == "empty", "back to empty state after closing")

    # --- the real Add SSH dialog: validation, save, immediate connect -----
    QTimer.singleShot(0, win.add_ssh)
    yield lambda: find_top(AddSSHDialog)
    d = find_top(AddSSHDialog)
    check(d is not None and d.port.value() == 22, "Add SSH dialog, port defaults to 22")
    d.host.setText("-oProxyCommand=evil")
    d.accept()
    yield lambda: d.error.isVisible()
    check(d.error.isVisible() and d.isVisible(), "invalid host rejected with an error message")
    d.name.setText("Refused box")
    d.host.setText("127.0.0.1")
    d.user.setText("nobody")
    d.port.setValue(1)
    d.accept()
    yield lambda: find_top(AddSSHDialog) is None and win.layout_.panels
    saved = [x for x in app.config.profiles if x.name == "Refused box"]
    check(len(saved) == 1 and saved[0].port == 1, "profile saved from dialog")
    items = [a.text() for a in win.saved_menu.actions()]
    check(any("Refused box" in i for i in items), "Saved Connections menu updated")
    t = win.layout_.panels[0]
    yield lambda: not t.running
    check(t.argv[-2:] == ["--", "127.0.0.1"], "dialog opened a terminal immediately")
    next(a for a in win.saved_menu.actions() if "Refused box" in a.text()).trigger()
    yield 0.3
    check(len(win.layout_.panels) == 2 and win.layout_.panels[1].profile.id == saved[0].id,
          "Saved Connections entry opens another session of the same profile")
    for x in list(win.layout_.panels):
        win.close_panel(x)


if __name__ == "__main__":
    sys.exit(run(test))
