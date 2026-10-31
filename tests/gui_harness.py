"""Tiny harness for driving the real Qt application in automated GUI tests.

Tests run against an isolated XDG_CONFIG_HOME on a private X server (see
tests/run_gui_tests.sh). Each test is a generator that yields either a number
of seconds to wait or a zero-arg predicate to wait for (with timeout), while
the Qt event loop keeps running (also inside modal dialogs).
"""

import os
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if not os.environ.get("XDG_CONFIG_HOME", "").startswith(tempfile.gettempdir()):
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="bunnydeck-test-cfg-")

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from bunnydeck.app import MainWindow, create_application  # noqa: E402
from bunnydeck.config import Config, Profile  # noqa: E402

RESULTS = []


def check(cond, msg):
    RESULTS.append((bool(cond), msg))
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    return cond


def screen_lines(panel):
    """The lines currently visible in the terminal (no scrollback)."""
    t = panel.terminal
    top = t.historyLinesCount()
    t.setSelectionStart(top, 0)
    t.setSelectionEnd(top + t.screenLinesCount() - 1, t.screenColumnsCount())
    text = t.selectedText(True)
    t.setSelectionStart(-1, -1)
    t.setSelectionEnd(-1, -1)
    return text.split("\n")


def term_text(panel):
    """Scrollback + screen as text."""
    t = panel.terminal
    t.setSelectionStart(0, 0)
    t.setSelectionEnd(t.historyLinesCount() + t.screenLinesCount() - 1, t.screenColumnsCount())
    text = t.selectedText(True)
    t.setSelectionStart(-1, -1)
    t.setSelectionEnd(-1, -1)
    return text


def scrollbar(panel):
    from PyQt6.QtWidgets import QScrollBar

    return panel.terminal.findChild(QScrollBar)


def find_top(cls):
    for w in QApplication.topLevelWidgets():
        if isinstance(w, cls) and w.isVisible():
            return w
    return None


def run(test_gen_factory, timeout=60):
    """Runs the app and drives test_gen_factory(app) inside its event loop."""
    app = create_application([sys.argv[0]])
    app.config = Config()
    app.window = MainWindow(app.config)
    app.window.show()
    deadline = time.monotonic() + timeout
    gen = test_gen_factory(app)

    def finish():
        win = app.window
        win.shutdown()
        win.hide()
        app.quit()

    def step():
        if time.monotonic() > deadline:
            check(False, "test timed out")
            finish()
            return
        try:
            what = next(gen)
        except StopIteration:
            finish()
            return
        except Exception:
            traceback.print_exc()
            check(False, "exception in test")
            finish()
            return
        if callable(what):
            start_t = time.monotonic()
            timer = QTimer(app)

            def poll():
                ok = False
                try:
                    ok = what()
                except Exception:
                    ok = False
                if ok or time.monotonic() - start_t > 15:
                    timer.stop()
                    timer.deleteLater()
                    QTimer.singleShot(0, step)

            timer.timeout.connect(poll)
            timer.start(50)
        else:
            QTimer.singleShot(int(float(what) * 1000), step)

    QTimer.singleShot(400, step)
    app.exec()
    failed = [m for ok, m in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed or not RESULTS else 0


def local_shell_argv():
    # A clean local bash; used instead of a real SSH host so tests never
    # contact a remote machine.
    return ["/bin/bash", "--noprofile", "--norc", "-i"]


def make_profile(name, host="test.invalid", user="tester", port=22):
    return Profile(name=name, host=host, user=user, port=port)


def process_events():
    QApplication.processEvents()


def screenshot(win, path):
    win.grab().save(path, "PNG")
    print(f"  screenshot: {path}")


def descendants(widget, cls=QWidget):
    return widget.findChildren(cls)
