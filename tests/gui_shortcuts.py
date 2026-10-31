"""GUI test: keyboard shortcuts pressed inside a terminal (real key events
through the terminal's key filter), and plain Ctrl keys still reaching the
program in the terminal."""

import os
import sys

os.environ["SHELL"] = "/bin/sh"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, find_top, local_shell_argv, run, term_text  # noqa: E402

from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

from bunnydeck.dialogs import AddSSHDialog, ShortcutsDialog  # noqa: E402
from bunnydeck.layout import HORIZONTAL_SPLIT, VERTICAL_SPLIT  # noqa: E402
from bunnydeck.terminal import FONT_SIZE  # noqa: E402

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier


def press(panel, key, mods=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(panel.display, key, mods)


def test(app):
    win = app.window
    win.resize(1500, 800)
    first = win.open_profile(win.local_profile, argv=local_shell_argv())
    yield lambda: first.child_pid

    # --- new terminal ------------------------------------------------------
    press(first, Qt.Key.Key_T, CTRL | SHIFT)
    yield lambda: len(win.layout_.panels) == 2 and win.layout_.panels[1].child_pid
    check(len(win.layout_.panels) == 2 and win.layout_.panels[1].profile.is_local,
          "Ctrl+Shift+T opens a local terminal")
    third = win.open_profile(win.local_profile, argv=local_shell_argv())
    yield lambda: third.child_pid
    a, b, c = win.layout_.panels
    win._set_split(VERTICAL_SPLIT)
    yield 0.4

    # --- moving between terminals -----------------------------------------
    win._focus_panel(a)
    press(a, Qt.Key.Key_Right, ALT)
    check(win.active_panel is b, "Alt+Right: terminal to the right")
    press(b, Qt.Key.Key_Right, ALT)
    check(win.active_panel is c, "Alt+Right again")
    press(c, Qt.Key.Key_Right, ALT)
    check(win.active_panel is c, "nothing further right: stays")
    press(c, Qt.Key.Key_Left, ALT)
    check(win.active_panel is b, "Alt+Left")
    press(b, Qt.Key.Key_Up, ALT)
    check(win.active_panel is b, "nothing above in side-by-side layout")

    win._set_split(HORIZONTAL_SPLIT)
    yield 0.4
    win._focus_panel(a)
    press(a, Qt.Key.Key_Down, ALT)
    check(win.active_panel is b, "Alt+Down in stacked layout")
    press(b, Qt.Key.Key_Up, ALT)
    check(win.active_panel is a, "Alt+Up")

    press(a, Qt.Key.Key_Tab, CTRL)
    check(win.active_panel is b, "Ctrl+Tab: next terminal")
    press(b, Qt.Key.Key_Backtab, CTRL | SHIFT)
    check(win.active_panel is a, "Ctrl+Shift+Tab: previous terminal")
    press(a, Qt.Key.Key_Backtab, CTRL | SHIFT)
    check(win.active_panel is c, "previous wraps around")

    # --- font size ---------------------------------------------------------
    rows = c.terminal.screenLinesCount()
    press(c, Qt.Key.Key_Plus, CTRL)
    press(c, Qt.Key.Key_Plus, CTRL)
    yield 0.3
    check(c.font_size == FONT_SIZE + 2
          and c.terminal.getTerminalFont().pointSizeF() == FONT_SIZE + 2,
          f"Ctrl++ makes the font larger ({c.font_size})")
    check(c.terminal.screenLinesCount() < rows, "fewer, larger lines")
    check(a.font_size == FONT_SIZE, "other terminals unchanged")
    press(c, Qt.Key.Key_Minus, CTRL)
    check(c.font_size == FONT_SIZE + 1, "Ctrl+- makes it smaller")
    press(c, Qt.Key.Key_0, CTRL)
    check(c.font_size == FONT_SIZE, "Ctrl+0 resets")

    # --- plain Ctrl keys still reach the program --------------------------
    c.terminal.sendText("sleep 30\n")
    yield 0.4
    press(c, Qt.Key.Key_C, CTRL)
    yield 0.3
    c.terminal.sendText("echo AFTER-$((2*21))\n")
    yield lambda: "AFTER-42" in term_text(c)
    check("AFTER-42" in term_text(c), "plain Ctrl+C still interrupts the program")

    # --- dialogs and menus -------------------------------------------------
    QTimer.singleShot(0, lambda: press(c, Qt.Key.Key_Question, CTRL | SHIFT))
    yield lambda: find_top(ShortcutsDialog)
    d = find_top(ShortcutsDialog)
    check(d is not None, "Ctrl+Shift+? shows the shortcut overview")
    d.reject()
    yield lambda: find_top(ShortcutsDialog) is None

    QTimer.singleShot(0, lambda: press(c, Qt.Key.Key_N, CTRL | SHIFT))
    yield lambda: find_top(AddSSHDialog)
    check(find_top(AddSSHDialog) is not None, "Ctrl+Shift+N opens Add SSH")
    find_top(AddSSHDialog).reject()
    yield lambda: find_top(AddSSHDialog) is None

    QTimer.singleShot(0, lambda: press(c, Qt.Key.Key_O, CTRL | SHIFT))
    yield lambda: win.saved_menu.isVisible()
    check(win.saved_menu.isVisible(), "Ctrl+Shift+O opens Saved Connections")
    win.saved_menu.close()
    yield lambda: not win.saved_menu.isVisible()

    # --- file manager and back --------------------------------------------
    press(c, Qt.Key.Key_F, CTRL | SHIFT)
    check(win.current_view() == "files", "Ctrl+Shift+F opens the file manager")
    win.run_shortcut("files")  # (what the window-wide shortcut does there)
    check(win.current_view() == "terminals", "and goes back to the terminals")

    # --- closing -----------------------------------------------------------
    asked = []
    win._confirm = lambda *args: asked.append(args) or True
    win._focus_panel(b)
    press(b, Qt.Key.Key_W, CTRL | SHIFT)
    check(asked and b not in win.layout_.panels and len(win.layout_.panels) == 2,
          "Ctrl+Shift+W closes the terminal after asking")

    # --- window-wide shortcuts in the file manager -------------------------
    win.open_file_manager(a)
    before = a.font_size
    win.run_shortcut("zoom-in")
    win.run_shortcut("focus-right")
    check(a.font_size == before and win.current_view() == "files",
          "terminal-only shortcuts do nothing while the file manager is shown")


if __name__ == "__main__":
    sys.exit(run(test, timeout=60))
