"""Phase 5 GUI test: per-profile appearance via the real dialog, persistence,
live application without reconnecting, and genuine background alpha."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, find_top, local_shell_argv, make_profile, run, screenshot  # noqa: E402

from PyQt6.QtCore import QPoint, QTimer  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402

from bunnydeck.config import DEFAULT_BACKGROUND, Config  # noqa: E402
from bunnydeck.dialogs import AppearanceDialog  # noqa: E402

SHOTS = os.environ.get("BUNNYDECK_SHOTS", "")


def pixel(win, panel, where="bg"):
    """RGBA of a window pixel inside the terminal background (bottom-right
    corner area, away from text and scrollbar) or inside the header."""
    img = win.grab().toImage()
    if where == "bg":
        p = panel.terminal.mapTo(win, QPoint(panel.terminal.width() - 40,
                                            panel.terminal.height() - 6))
    else:
        p = panel.mapTo(win, QPoint(panel.width() // 2, 4))
    c = img.pixelColor(p.x(), p.y())
    return (c.name(), c.alpha())


def same(px, color, alpha):
    """Pixel equality allowing +-1 per channel (premultiplied alpha rounding)."""
    name, a = px
    want = QColor(color)
    got = QColor(name)
    return a == alpha and all(abs(x - y) <= 1 for x, y in zip(
        (got.red(), got.green(), got.blue()), (want.red(), want.green(), want.blue())))


def test(app):
    win = app.window
    win.resize(1400, 800)
    ub1 = app.config.add(make_profile("Ubuntu 01", host="u1.invalid"))
    rpi = app.config.add(make_profile("Raspberry Pi", host="rpi.invalid"))
    a = win.open_profile(ub1, argv=local_shell_argv())
    b = win.open_profile(ub1, argv=local_shell_argv())   # 2nd session, same profile
    c = win.open_profile(rpi, argv=local_shell_argv())
    yield lambda: a.child_pid and b.child_pid and c.child_pid
    yield 0.5
    pids = (a.child_pid, b.child_pid, c.child_pid)
    check(pixel(win, a) == (DEFAULT_BACKGROUND, 255), f"default appearance {pixel(win, a)}")

    # --- open the dialog from session A, preview, cancel -> reverted -----
    QTimer.singleShot(0, a.appearance_btn.click)
    yield lambda: find_top(AppearanceDialog)
    d = find_top(AppearanceDialog)
    d.chooser.setCurrentColor(QColor("#0d1b2e"))
    d.opacity.setValue(80)
    yield 0.3
    check(same(pixel(win, a), "#0d1b2e", 204) and same(pixel(win, b), "#0d1b2e", 204),
          f"live preview applies to every session of the profile {pixel(win, a)}")
    check(pixel(win, c) == (DEFAULT_BACKGROUND, 255), "other profiles unaffected")
    d.reject()
    yield lambda: find_top(AppearanceDialog) is None
    yield 0.2
    check(pixel(win, a) == (DEFAULT_BACKGROUND, 255), "cancel reverts preview")

    # --- save dark blue for Ubuntu 01 -------------------------------------
    QTimer.singleShot(0, b.appearance_btn.click)
    yield lambda: find_top(AppearanceDialog)
    d = find_top(AppearanceDialog)
    d.chooser.setCurrentColor(QColor("#0d1b2e"))
    d.opacity.setValue(75)
    d.accept()
    yield lambda: find_top(AppearanceDialog) is None
    yield 0.2
    check(same(pixel(win, a), "#0d1b2e", 191) and same(pixel(win, b), "#0d1b2e", 191),
          f"saved and applied {pixel(win, a)}")
    with open(app.config.path) as f:
        data = json.load(f)
    saved = {p["name"]: p["appearance"] for p in data["profiles"]}
    check(saved["Ubuntu 01"] == {"background": "#0d1b2e", "opacity": 0.75, "image": "",
                                 "image_mode": "fill"},
          f"written to config.json: {saved['Ubuntu 01']}")
    check(Config(app.config.path).get(ub1.id).appearance.background == "#0d1b2e",
          "persists across a config reload (restart)")
    check((a.child_pid, b.child_pid, c.child_pid) == pids, "no reconnect: same processes")
    check(pixel(win, a, "header")[1] == 255, "header stays opaque (only the bg is translucent)")
    check(a.windowOpacity() == 1.0 and win.windowOpacity() == 1.0,
          "widget/window opacity untouched (text stays fully opaque)")

    # --- dark green for the Pi via a preset; reopening restores it --------
    QTimer.singleShot(0, c.appearance_btn.click)
    yield lambda: find_top(AppearanceDialog)
    d = find_top(AppearanceDialog)
    d.chooser.setCurrentColor(QColor("#0f2318"))
    d.accept()
    yield lambda: find_top(AppearanceDialog) is None
    c2 = win.open_profile(app.config.get(rpi.id), argv=local_shell_argv())
    yield lambda: c2.child_pid
    yield 0.4
    check(pixel(win, c2) == ("#0f2318", 255), f"new session restores the saved colour "
                                              f"{pixel(win, c2)}")
    if SHOTS:
        screenshot(win, os.path.join(SHOTS, "qt-appearance.png"))

    # --- reset to default --------------------------------------------------
    QTimer.singleShot(0, a.appearance_btn.click)
    yield lambda: find_top(AppearanceDialog)
    d = find_top(AppearanceDialog)
    d.reset()
    yield 0.3
    check(pixel(win, a) == (DEFAULT_BACKGROUND, 255), "Reset to Default previews default")
    d.accept()
    yield lambda: find_top(AppearanceDialog) is None
    check(Config(app.config.path).get(ub1.id).appearance.is_default(), "default saved")


if __name__ == "__main__":
    sys.exit(run(test, timeout=90))
