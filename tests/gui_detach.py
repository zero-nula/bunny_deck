"""GUI test: detaching a panel into a window of its own and attaching it
back. The terminal is moved, never restarted: same process, same widget,
same scrollback, font, colours and size. Also: shortcuts in the detached
window, counts and broadcast, the way back (same place, next to the same
connection, at the end), the window's X, the session and quitting."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gui_harness import check, local_shell_argv, make_profile, run, term_text  # noqa: E402

from PyQt6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import bunnydeck.app as app_module  # noqa: E402
from bunnydeck import docker, session  # noqa: E402
from bunnydeck.detach import DetachedWindow  # noqa: E402
from bunnydeck.layout import BELOW  # noqa: E402
from bunnydeck.sidebar import ROLE_OPEN  # noqa: E402

docker.SSH_BINARY = os.path.join(HERE, "fake_ssh_exec.py")


def geo(win, p):
    pos = p.mapTo(win.layout_, QPoint(0, 0))
    return pos.x(), pos.y(), p.width(), p.height()


def key(panel, k, mods):
    QApplication.sendEvent(panel.display, QKeyEvent(QEvent.Type.KeyPress, k, mods))


def bg_pixel(panel):
    img = panel.display.grab().toImage()
    return img.pixelColor(img.width() - 3, img.height() - 3).name()


def test(app):
    win = app.window
    win.resize(1400, 860)
    srv = app.config.add(make_profile("srv"))
    other = make_profile("other")
    win.refresh_connections()
    one = win.open_profile(srv, argv=local_shell_argv())
    two = win.open_profile(srv, argv=local_shell_argv())
    three = win.open_profile(other, argv=local_shell_argv())
    yield lambda: one.child_pid and two.child_pid and three.child_pid
    win.layout_.move_panel(three, two, BELOW)  # one | (two / three)
    two.terminal.sendText("echo MARK-$((6*7))\n")
    two.zoom(2)
    yield lambda: "MARK-42" in term_text(two)
    yield 0.4
    before = geo(win, two)
    pid, widget = two.child_pid, two.terminal
    font = two.terminal.getTerminalFont().pointSizeF()
    cols, lines = two.terminal.screenColumnsCount(), two.terminal.screenLinesCount()
    pixel = bg_pixel(two)

    # Detach (the mouse leaves the header as it moves: Attach must stay shown)
    QApplication.sendEvent(two.header, QEvent(QEvent.Type.Enter))
    yield 0.3
    QApplication.sendEvent(two.header, QEvent(QEvent.Type.Leave))
    two.detach_btn.click()
    dw = win.detached.get(two)
    yield 0.5
    check(isinstance(dw, DetachedWindow) and dw.isVisible() and two.window() is dw,
          "Detach puts it in a window of its own")
    check(two not in win.layout_.panels and two in win.all_panels(),
          "out of the layout, still an open panel")
    check(two.child_pid == pid and two.running and two.terminal is widget,
          "same process and terminal: nothing restarted")
    check("MARK-42" in term_text(two), "scrollback kept")
    check(two.terminal.getTerminalFont().pointSizeF() == font, "font kept")
    check(bg_pixel(two) == pixel, f"colours kept ({bg_pixel(two)} vs {pixel})")
    check((two.terminal.screenColumnsCount(), two.terminal.screenLinesCount()) == (cols, lines),
          f"size kept ({two.terminal.screenColumnsCount()}x"
          f"{two.terminal.screenLinesCount()} vs {cols}x{lines})")
    check(dw.windowTitle() == "srv — Bunny Deck", f"titled after the panel ({dw.windowTitle()})")
    check(two.detach_btn.text() == "Attach" and not two.move_btn.isVisibleTo(two),
          "the header offers Attach, no Move")
    check(two.detach_btn.graphicsEffect().opacity() == 1.0,
          "Attach is always shown, also after a fade-out the move started")
    check(win.active_panel is two, "it is the active panel")

    # It still works, shortcuts included
    two.terminal.sendText("echo STILL-$((40+2))\n")
    yield lambda: "STILL-42" in term_text(two)
    check(True, "typing still reaches the same shell")
    key(two, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
    check(two.font_size == font + 1, "Ctrl++ zooms in the detached window")
    two.terminal.setSelectionStart(0, 0)
    two.terminal.setSelectionEnd(0, 3)
    QApplication.clipboard().setText("")
    key(two, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    check(QApplication.clipboard().text() != "", "Ctrl+Shift+C copies there")
    two.zoom(-1)

    # Counted as open
    check(win.sidebar.item_for(srv.id).data(ROLE_OPEN) == 2, "the sidebar still counts it")
    win.broadcast_btn.setChecked(True)
    check(two.broadcast_box.isVisibleTo(two), "broadcast reaches the detached terminal")
    one.set_broadcast(True)
    two.set_broadcast(True)
    check(win._broadcast_peers(one) == [two], "and it is a broadcast peer")
    win.broadcast_btn.setChecked(False)
    tree = win.session_tree()
    check(two in tree[2], "the session keeps it (in the layout next time)")
    entries = session.encode(tree, win._session_entry)["layout"]
    check(len(entries["children"]) == 3, "saved as a third part of the layout")

    # Attach: back in the same place
    two.detach_btn.click()
    yield 0.5
    check(two in win.layout_.panels and two not in win.detached and two.window() is win,
          "Attach puts it back into the main window")
    check(win.layout_.ordered_panels() == [one, two, three], "in the same place")
    g = geo(win, two)
    check(all(abs(a - b) <= 2 for a, b in zip(g, before)),
          f"with the same size, give or take a divider pixel ({g} vs {before})")
    yield 0.3
    check((two.terminal.screenColumnsCount(), two.terminal.screenLinesCount()) == (cols, lines),
          "the terminal has its columns and lines back")
    check(two.terminal.getTerminalFont().pointSizeF() == font, "and its font")
    check(two.child_pid == pid and "STILL-42" in term_text(two), "still the same session")
    check(two.detach_btn.text() == "Detach" and two.move_btn.isVisibleTo(two),
          "the header offers Detach and Move again")

    # Moving inside the layout keeps a zoomed font too (it used to reset it)
    win.move_panel(two, one, BELOW)
    yield 0.3
    win.layout_.swap_panels(two, three)
    yield 0.3
    check(two.terminal.getTerminalFont().pointSizeF() == font, "moving keeps the zoomed font")
    win.layout_.swap_panels(two, three)
    win.move_panel(two, three, BELOW)
    yield 0.2

    # Its neighbour is gone: next to a terminal of the same connection
    win.detach_panel(two)
    win.close_panel(three)  # two's place was "above three"
    win.attach_panel(two)
    yield 0.3
    check(win.layout_.ordered_panels() == [one, two], "right of the same connection")
    check(geo(win, two)[0] > geo(win, one)[0], "to its right")

    # Nothing to go next to: at the end
    lone = win.open_profile(make_profile("lone"), argv=local_shell_argv())
    yield lambda: lone.child_pid
    win.detach_panel(lone)
    win.attach_panel(lone)
    check(win.layout_.ordered_panels()[-1] is lone, "else at the end")

    # The detached window's X
    answers = []
    app_module.ask_close = lambda parent, panel: answers.pop(0)
    win.detach_panel(lone)
    lw = win.detached[lone]
    answers.append(None)
    lw.close()
    check(lone in win.detached and lw.isVisible(), "X, Cancel: stays detached")
    answers.append("attach")
    lw.close()
    check(lone in win.layout_.panels, "X, Attach back: back in the layout")
    win.detach_panel(lone)
    lpid = lone.child_pid
    answers.append("close")
    win.detached[lone].close()
    check(lone not in win.all_panels(), "X, Close session: gone")
    yield lambda: not os.path.exists(f"/proc/{lpid}")
    check(True, "and its shell has ended")

    # A Docker panel detaches and keeps watching
    dp = win.open_docker(srv)
    yield lambda: dp.containers
    win.detach_panel(dp)
    yield lambda: dp.watching and dp.isVisible()
    check(dp.window() is win.detached[dp], "a Docker panel detaches too")
    check(win.docker_panel_for(srv) is dp, "and is still found as the connection's")
    win.attach_panel(dp)
    yield lambda: dp.watching
    check(dp in win.layout_.panels, "and attaches back")

    # Reconnect in the detached window
    win.detach_panel(one)
    one.terminal.sendText("exit\n")
    yield lambda: not one.running
    new = win.reconnect(one)
    check(new is not None and new in win.detached and win.detached[new].panel is new,
          "Reconnect happens in the detached window")
    yield lambda: new.child_pid
    nw = win.detached[new]

    # Quitting closes the detached windows
    win._confirm = lambda *a: True
    win.close()
    yield 0.3
    check(not nw.isVisible(), "quitting closes detached windows too")


if __name__ == "__main__":
    sys.exit(run(test))
