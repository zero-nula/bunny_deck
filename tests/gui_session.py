"""GUI test: the session (terminals and their arrangement) is saved on
quit and reopened by the next window: local, saved SSH, unsaved SSH and AI
terminals, a custom nested layout with its proportions, and the active
terminal. Removed connections are skipped; a broken or empty session file
falls back to the usual single local terminal."""

import json
import os
import sys

os.environ["SHELL"] = "/bin/sh"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, make_profile, run  # noqa: E402

from bunnydeck import session  # noqa: E402
from bunnydeck.app import MainWindow  # noqa: E402
from bunnydeck.config import AITool  # noqa: E402
from bunnydeck.layout import BELOW, HORIZONTAL_SPLIT, VERTICAL_SPLIT  # noqa: E402


def kinds(win):
    out = []
    for p in win.layout_.ordered_panels():
        e = win._session_entry(p)
        out.append(e["kind"] + (":" + p.profile.name if e["kind"] != "local" else ""))
    return out


def shape(node):
    """The arrangement without panels and sizes, for comparing."""
    orientation, _, children = node
    return (orientation, [shape(c) if isinstance(c, tuple) else "T" for c in children])


def new_window(app):
    w = MainWindow(app.config)
    w.resize(1400, 800)
    w.show()
    return w


def test(app):
    win = app.window
    win.resize(1400, 800)
    # Refused port: the SSH terminals start and end quickly, without a server.
    saved = app.config.add(make_profile("Pi", host="127.0.0.1", port=1))
    gone = app.config.add(make_profile("Gone", host="127.0.0.1", port=1))
    tool = app.config.add_ai_tool(AITool(name="Shell", command="true"))

    local = win.open_local()
    pi = win.open_profile(saved)
    adhoc = win.open_profile(make_profile("Adhoc", host="127.0.0.1", user="me", port=1))
    ai = win.open_ai(tool)
    win.open_profile(gone)
    yield 0.5
    win.move_panel(ai, local, BELOW)  # custom: local above ai, beside pi and adhoc
    yield 0.3
    root = win.layout_.splitter
    total = sum(root.sizes())
    root.setSizes([total // 2] + [total // (2 * (root.count() - 1))] * (root.count() - 1))
    win._focus_panel(adhoc)
    yield 0.2
    before_shape = shape(win.layout_.describe())
    before_sizes = win.layout_.describe()[1]
    win.save_session()
    app.config.remove(gone.id)  # removed before the next start
    data = session.load()
    check(data is not None and os.stat(session.session_path()).st_mode & 0o777 == 0o600,
          "session saved to a private file")
    text = json.dumps(data)
    check("password" not in text.lower() and '"host": "127.0.0.1"' in text,
          "unsaved connection kept by address only")

    # --- the next start ----------------------------------------------------
    win2 = new_window(app)
    check(win2.restore_session(), "a saved session is restored")
    yield 0.5
    check(kinds(win2) == ["local", "ai:Shell", "ssh:Pi", "ssh:Adhoc"],
          f"same terminals in the same order, removed connection skipped ({kinds(win2)})")
    check(before_shape == (VERTICAL_SPLIT, [(HORIZONTAL_SPLIT, ["T", "T"]), "T", "T", "T"]),
          f"(set-up: local above ai, beside three terminals {before_shape})")
    check(shape(win2.layout_.describe()) == (VERTICAL_SPLIT, [(HORIZONTAL_SPLIT, ["T", "T"]),
                                                             "T", "T"])
          and win2.layout_.custom,
          f"custom arrangement rebuilt ({shape(win2.layout_.describe())})")
    after = win2.layout_.describe()[1]
    check(abs(after[0] / after[1] - before_sizes[0] / before_sizes[1]) < 0.2,
          f"proportions kept (before {before_sizes}, after {after})")
    check(win2.active_panel is not None and win2.active_panel.profile.name == "Adhoc",
          "the active terminal is active again")
    check(not win2.vsplit_btn.isChecked() and not win2.hsplit_btn.isChecked(),
          "custom layout: no preset button checked")
    check(win2.layout_.ordered_panels()[2].profile is saved,
          "saved connection reopened with its profile")
    win2.shutdown()
    win2.hide()

    # --- a plain preset is restored as that preset --------------------------
    win3 = new_window(app)
    win3.open_local()
    win3.open_local()
    win3._set_split(HORIZONTAL_SPLIT)
    yield 0.3
    win3.save_session()
    win3.shutdown()
    win3.hide()
    win4 = new_window(app)
    check(win4.restore_session() and win4.layout_.orientation == HORIZONTAL_SPLIT
          and win4.hsplit_btn.isChecked() and not win4.layout_.custom,
          "stacked preset restored with its button")
    win4.layout_.set_orientation(VERTICAL_SPLIT)
    for p in list(win4.layout_.panels):
        win4.close_panel(p)
    win4.save_session()
    win4.shutdown()
    win4.hide()

    # --- nothing to restore -----------------------------------------------
    win5 = new_window(app)
    check(not win5.restore_session() and not win5.layout_.panels,
          "an empty session restores nothing (the app then opens one local terminal)")
    with open(session.session_path(), "w") as f:
        f.write("{broken")
    check(not win5.restore_session(), "a broken session file is ignored")
    win5.shutdown()
    win5.hide()


if __name__ == "__main__":
    sys.exit(run(test, timeout=60))
