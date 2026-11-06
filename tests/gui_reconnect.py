"""GUI test: reconnecting an ended session from its header (button, Enter,
menu entry): a new terminal takes the old one's exact place."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, local_shell_argv, make_profile, run  # noqa: E402

from PyQt6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from bunnydeck.layout import BELOW  # noqa: E402
from bunnydeck.sidebar import ROLE_ENDED, ROLE_OPEN  # noqa: E402


def geo(win, p):
    pos = p.mapTo(win.layout_, QPoint(0, 0))
    return pos.x(), pos.y(), p.width(), p.height()


def test(app):
    win = app.window
    win.resize(1400, 860)
    one, two, three = [win.open_profile(make_profile(n), argv=local_shell_argv())
                       for n in ("one", "two", "three")]
    yield lambda: one.child_pid and two.child_pid and three.child_pid
    win.layout_.move_panel(three, two, BELOW)  # one | (two / three)
    yield 0.4
    two.zoom(2)

    check(not two.reconnect_btn.isVisibleTo(two), "no Reconnect while connected")
    two.terminal.sendText("exit\n")
    yield lambda: not two.running
    check(two.ended and two.reconnect_btn.isVisibleTo(two), "Reconnect shows once it ended")
    check(two.reconnect_btn.text() == "Reconnect", "SSH terminals say Reconnect")
    before = geo(win, two)
    order = win.layout_.ordered_panels()

    two.reconnect_btn.click()
    new = win.layout_.ordered_panels()[1]
    check(new is not two and two not in win.layout_.panels, "a new terminal replaced it")
    check(len(win.layout_.panels) == 3, "still three terminals")
    yield lambda: new.child_pid
    yield 0.3
    check(geo(win, new) == before, f"same place and size ({geo(win, new)} vs {before})")
    check(win.layout_.ordered_panels() == [order[0], new, order[2]], "the others did not move")
    check(new.profile is two.profile and new.argv == two.argv, "same connection")
    check(new.font_size == two.font_size, "font size kept")
    check(not new.reconnect_btn.isVisibleTo(new), "the new one has no Reconnect button")

    # Enter in an ended terminal reconnects; other keys do nothing
    new.terminal.sendText("exit\n")
    yield lambda: not new.running
    for key in (Qt.Key.Key_A, Qt.Key.Key_Return):
        QApplication.sendEvent(new.display, QKeyEvent(QEvent.Type.KeyPress, key,
                                                       Qt.KeyboardModifier.NoModifier))
        if key == Qt.Key.Key_A:
            check(new in win.layout_.panels, "another key does not reconnect")
    newer = win.layout_.ordered_panels()[1]
    check(newer is not new and new not in win.layout_.panels, "Enter reconnects")
    yield lambda: newer.child_pid

    # From the sidebar: a saved connection that disconnected
    saved = app.config.add(make_profile("srv"))
    win.refresh_connections()
    sp = win.open_profile(saved, argv=local_shell_argv())
    yield lambda: sp.child_pid
    item = win.sidebar.item_for(saved.id)
    check(item.data(ROLE_OPEN) == 1 and not item.data(ROLE_ENDED), "badge: one open, green")
    sp.terminal.sendText("exit\n")
    yield lambda: not sp.running
    yield lambda: win.sidebar.item_for(saved.id).data(ROLE_ENDED) == 1
    check(True, "badge turns red when it disconnects")
    asked = []
    win._confirm = lambda *a: asked.append(a) or True
    win._on_sidebar_reconnect(saved.id)
    sp2 = win.panels_for_profile(saved.id)[0]
    check(sp2 is not sp and not asked, "sidebar Reconnect reopens it in place, without asking")
    yield lambda: sp2.child_pid
    yield lambda: not win.sidebar.item_for(saved.id).data(ROLE_ENDED)
    check(True, "badge green again")

    # A running (maybe frozen) session: asks first, then replaces it
    pid_before = sp2.child_pid
    win._confirm = lambda *a: asked.append(a) or False
    win._on_sidebar_reconnect(saved.id)
    check(asked and win.panels_for_profile(saved.id)[0] is sp2, "running: asks; Cancel keeps it")
    win._confirm = lambda *a: True
    win._on_sidebar_reconnect(saved.id)
    sp3 = win.panels_for_profile(saved.id)[0]
    check(sp3 is not sp2 and len(win.panels_for_profile(saved.id)) == 1,
          "running: Reconnect ends it and opens a new one")
    yield lambda: sp3.child_pid
    check(sp3.child_pid != pid_before, "a new process")

    # A local terminal says Restart
    loc = win.open_profile(win.local_profile, argv=local_shell_argv())
    check(loc.reconnect_btn.text() == "Restart", "a local terminal says Restart")


if __name__ == "__main__":
    sys.exit(run(test))
