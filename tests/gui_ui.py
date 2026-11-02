"""GUI test: the connections sidebar, the start screen and the terminal
header (status dot, tools shown on hover, line over the active terminal)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, local_shell_argv, make_profile, run  # noqa: E402

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from bunnydeck import theme  # noqa: E402
from bunnydeck.config import Appearance, Config  # noqa: E402
from bunnydeck.sidebar import LOCAL_ID, ROLE_OPEN, ROLE_TARGET  # noqa: E402


def visible_rows(sb):
    return [sb.list.item(i).text() for i in range(sb.list.count())
            if not sb.list.item(i).isHidden()]


def test(app):
    win = app.window
    sb = win.sidebar
    welcome = win.empty

    # Start screen
    check(win.current_view() == "empty" and welcome.isVisible(), "start screen at first")
    check(set(welcome.action_buttons) == {"new-ssh", "new-local", "network-scan", "import"},
          "start screen has the four actions")
    check(welcome.no_recent.isVisible() and not welcome.recent_buttons, "no recent ones yet")

    # Sidebar with saved connections
    check(sb.isVisible() and win.sidebar_btn.isChecked(), "sidebar shown by default")
    check(visible_rows(sb) == ["Local terminal"] and sb.empty.isVisible(),
          "only the local terminal and an import hint without saved connections")
    web = make_profile("web1", host="web1.example.com", user="admin")
    web.appearance = Appearance(background="#4a1416")
    db = make_profile("db1", host="db1.example.com", user="admin")
    for p in (web, db):
        win.config.add(p)
    win.rebuild_saved_menu()
    check(visible_rows(sb) == ["Local terminal", "db1", "web1"], "saved connections, sorted")
    check(not sb.empty.isVisible(), "import hint hidden once there are connections")
    check(sb.item_for(web.id).data(ROLE_TARGET) == "admin@web1.example.com", "rows show user@host")

    sb.search.setText("WEB")
    check(visible_rows(sb) == ["web1"], "search filters (case-insensitive)")
    sb.search.setText("example db")
    check(visible_rows(sb) == ["db1"], "every word must match")
    sb.search.setText("nothing-like-this")
    check(visible_rows(sb) == [] and sb.no_match.isVisible(), "no match is said")
    # Only record what would be opened (never ssh to the example hosts).
    opened = []
    sb.openRequested.disconnect()
    sb.openRequested.connect(opened.append)
    sb.search.setText("web")
    sb.search.returnPressed.emit()
    check(opened == [web.id], "Enter in the search opens the first match")
    sb.search.clear()
    sb.list.itemActivated.emit(sb.item_for(LOCAL_ID))
    check(opened[-1] == LOCAL_ID, "activating a row opens it")
    sb.openRequested.disconnect()
    sb.openRequested.connect(win._on_sidebar_open)
    sb.list.itemActivated.emit(sb.item_for(LOCAL_ID))

    # Opening terminals: counts in the sidebar, recent list
    yield lambda: win.layout_.panels and win.layout_.panels[0].child_pid
    local = win.layout_.panels[0]
    check(local.profile is win.local_profile, "the local row opened a local terminal")
    w1 = win.open_profile(web, argv=local_shell_argv())
    w2 = win.open_profile(web, argv=local_shell_argv())
    yield lambda: w2.child_pid
    check(sb.item_for(web.id).data(ROLE_OPEN) == 2, "sidebar counts web1's open terminals")
    check(sb.item_for(LOCAL_ID).data(ROLE_OPEN) == 1, "…and the local one")
    check(sb.item_for(db.id).data(ROLE_OPEN) == 0, "db1 has none")
    check(Config().recent == [web.id], "opening a saved connection makes it recent")

    # Header: dot instead of "connected", tools on hover, line on the active one
    check(not w2.status_label.isVisible() and w2.status_dot.property("state") == "ok",
          "connected shows only the green dot")
    effects = [b.graphicsEffect().opacity() for b in w2.tools]
    check(effects == [0.0, 0.0], "Move / Background hidden at first")
    check(not hasattr(w2, "scp_btn"), "Files is in the toolbar, not in the header")
    check(w2.close_btn.graphicsEffect() is None, "close is always shown")
    QApplication.sendEvent(w2.header, QEvent(QEvent.Type.Enter))
    yield 0.4
    check([b.graphicsEffect().opacity() for b in w2.tools] == [1.0, 1.0],
          "they fade in when the mouse is over the header")
    QApplication.sendEvent(w2.header, QEvent(QEvent.Type.Leave))
    yield 0.4
    check(w2.tools[0].graphicsEffect().opacity() == 0.0, "…and out again")
    win._focus_panel(w1)
    red = theme.accent_for(web.appearance)
    check(red in w1.header.styleSheet(), f"active terminal has a line in its colour ({red})")
    check("transparent" in w2.header.styleSheet(), "the others have none")
    check(red != theme.ACCENT and red.startswith("#e"), "a red background gives a red line")

    # Hiding the sidebar is remembered.
    win.run_shortcut("sidebar")
    check(not sb.isVisible() and not win.sidebar_btn.isChecked(), "Ctrl+Shift+S hides it")
    check(Config().sidebar_visible is False, "…and that is saved")
    win.sidebar_btn.click()
    check(sb.isVisible() and Config().sidebar_visible, "the toolbar button shows it again")

    # Closing all terminals: back to the start screen with the recent one.
    for p in list(win.layout_.panels):
        win.close_panel(p)
    yield 0.2
    check(win.current_view() == "empty", "back to the start screen")
    check([b.text().split()[0] for b in welcome.recent_buttons] == ["web1"],
          "web1 is listed as recent")
    opened = []
    win.open_profile = lambda profile, argv=None: opened.append(profile.id)
    welcome.recent_buttons[0].click()
    check(opened == [web.id], "clicking a recent connection opens it")
    del win.open_profile
    win.config.remove(web.id)
    win.rebuild_saved_menu()
    check(not welcome.recent_buttons and welcome.no_recent.isVisible(),
          "a removed connection leaves the recent list")


    # The toolbar's Files button works without a terminal too.
    check(win.files_btn.isVisible(), "Files is always in the toolbar")
    win.files_btn.click()
    yield lambda: win.file_manager and win.file_manager.right.cwd
    check(win.current_view() == "files", "Files opens the file manager")
    check(win.file_manager.right.source_id == "__local__", "…for this computer")
    check(win.file_manager.right.path_bar.path == win.file_manager.right.cwd,
          "the path bar shows the folder")
    win.files_btn.click()
    check(win.current_view() == "empty", "clicking it again goes back")


sys.exit(run(test))
