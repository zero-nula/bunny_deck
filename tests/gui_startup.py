"""GUI test: the real main() opens a local terminal at startup.

D-Bus points at a socket that does not exist, so this never reaches (or
raises) a running Bunny Deck on the user's session bus."""

import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_harness  # noqa: E402,F401  (sys.path, throwaway XDG_CONFIG_HOME)
from gui_harness import RESULTS, check  # noqa: E402

HOME = tempfile.mkdtemp(prefix="bunnydeck-test-home-")
atexit.register(shutil.rmtree, HOME, True)
os.environ["HOME"] = HOME
os.environ["SHELL"] = "/bin/sh"
os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={HOME}/no-bus"

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from bunnydeck import app as appmod  # noqa: E402

_create = appmod.create_application


def create_application(argv):
    app = _create(argv)
    QTimer.singleShot(1500, lambda: inspect(app))
    QTimer.singleShot(20000, lambda: (check(False, "test timed out"), finish()))
    return app


def windows():
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, appmod.MainWindow)]


def finish():
    for w in windows():
        w.shutdown()
        w.hide()
    QApplication.instance().quit()


def inspect(app):
    wins = windows()
    check(len(wins) == 1, "main() created the main window")
    win = wins[0]
    panels = win.layout_.panels
    check(len(panels) == 1, f"exactly one terminal open at startup ({len(panels)})")
    if panels:
        p = panels[0]
        check(p.profile.is_local, "the startup terminal is a local terminal")
        check(p.running and p.argv == ["/bin/sh"], f"local shell running ({p.argv})")
        check(win.current_view() == "terminals", "terminal view shown, not the empty state")
        check(win.active_panel is p, "startup terminal is the active terminal")
    finish()


appmod.create_application = create_application

if __name__ == "__main__":
    rc = appmod.main([sys.argv[0]])
    failed = [m for ok, m in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    sys.exit(1 if rc or failed or not RESULTS else 0)
