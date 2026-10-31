"""GUI test: the panel's Background dialog with a background image. The image
is shown in the terminal, copied into the config directory (so it survives the
original being deleted), and persists for saved connections, local terminals
and AI tools."""

import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, find_top, local_shell_argv, make_profile, run  # noqa: E402
from gui_phase5 import pixel  # noqa: E402

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402

from bunnydeck.config import (  # noqa: E402
    DEFAULT_BACKGROUND, LOCAL_BACKGROUND_IMAGE, AITool, Appearance, Config, backgrounds_dir,
)
from bunnydeck.dialogs import AppearanceDialog  # noqa: E402

TMP = tempfile.mkdtemp(prefix="bunnydeck-test-bg-")
atexit.register(shutil.rmtree, TMP, True)
os.environ["SHELL"] = "/bin/sh"


def make_image(name, color):
    path = os.path.join(TMP, name)
    img = QImage(64, 32, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    img.save(path)
    return path


def edit_background(panel, image, mode="stretch"):
    """Opens the panel's Background dialog, sets the image and saves."""
    QTimer.singleShot(0, panel.appearance_btn.click)
    yield lambda: find_top(AppearanceDialog)
    d = find_top(AppearanceDialog)
    d.set_image(image)
    d.image_mode.setCurrentIndex(d.image_mode.findData(mode))
    yield 0.3
    d.accept()
    yield lambda: find_top(AppearanceDialog) is None
    yield 0.3


def test(app):
    win = app.window
    win.resize(1200, 700)
    red = make_image("red.png", "#c03030")
    green = make_image("green.png", "#30c030")

    # --- local terminal ---------------------------------------------------
    local = win.open_local()
    check(local.appearance_btn.text() == "Background", "header button is called Background")
    yield lambda: local.child_pid
    check(local.appearance.image == LOCAL_BACKGROUND_IMAGE and local._bg_file,
          "local terminal shows the bunny badge by default")
    yield from edit_background(local, red)
    check(pixel(win, local) == ("#c03030", 255), f"image shown (stretch) {pixel(win, local)}")
    stored = Config(app.config.path).local_appearance
    check(os.path.dirname(stored.image) == backgrounds_dir() and os.path.isfile(stored.image),
          f"image copied into the config directory ({stored.image})")
    check(stored.image_mode == "stretch", "image mode saved")

    # The original may disappear: the stored copy is used after a restart.
    os.unlink(red)
    win.local_profile.appearance = Config(app.config.path).local_appearance
    local2 = win.open_local()
    yield lambda: local2.child_pid
    yield 0.4
    check(pixel(win, local2) == ("#c03030", 255), "local background persists (stored copy)")

    # --- saved SSH connection --------------------------------------------
    prof = app.config.add(make_profile("Pi", host="pi.invalid"))
    ssh = win.open_profile(prof, argv=local_shell_argv())
    yield lambda: ssh.child_pid
    yield from edit_background(ssh, green)
    check(pixel(win, ssh) == ("#30c030", 255), "image on a saved connection")
    check(os.path.isfile(Config(app.config.path).get(prof.id).appearance.image),
          "saved connection's image persists")
    check(pixel(win, local) == ("#c03030", 255), "other terminals unaffected")

    # --- AI tool -----------------------------------------------------------
    tool = app.config.add_ai_tool(AITool(name="Shell", command="true"))
    ai = win.open_ai(tool)
    yield lambda: ai.child_pid
    yield from edit_background(ai, green, mode="fit")
    saved_tool = Config(app.config.path).ai_tools[0]
    check(saved_tool.appearance.image and saved_tool.appearance.image_mode == "fit",
          "AI tool's background persists")

    # --- Fit leaves the background colour around the image (QTermWidget's
    # own Fit mode left it unpainted, i.e. white); Fill covers everything.
    wide = make_image("wide.png", "#3030c0")
    QImage(wide).scaled(400, 40).save(wide)
    ai.apply_appearance(Appearance(background="#0f2318", image=wide, image_mode="fit"))
    yield 0.3
    check(pixel(win, ai) == ("#0f2318", 255), f"Fit: colour around the image {pixel(win, ai)}")
    ai.apply_appearance(Appearance(background="#0f2318", image=wide, image_mode="fill"))
    yield 0.3
    check(pixel(win, ai) == ("#3030c0", 255), f"Fill: image covers the terminal {pixel(win, ai)}")

    # Resizing recomposes the image for the new size.
    first = ai._bg_file
    win.resize(900, 600)
    yield lambda: ai._bg_file != first
    check(ai._bg_file != first and not os.path.exists(first),
          "image recomposed after a resize (old file removed)")

    # --- reset removes the image; a missing file falls back to the colour --
    QTimer.singleShot(0, ssh.appearance_btn.click)
    yield lambda: find_top(AppearanceDialog)
    d = find_top(AppearanceDialog)
    d.reset()
    d.accept()
    yield lambda: find_top(AppearanceDialog) is None
    yield 0.3
    check(pixel(win, ssh) == (DEFAULT_BACKGROUND, 255), "Reset to Default removes the image")
    check(Config(app.config.path).get(prof.id).appearance.image == "", "reset saved")

    # For a local terminal, Reset to Default brings the bunny badge back.
    QTimer.singleShot(0, local.appearance_btn.click)
    yield lambda: find_top(AppearanceDialog)
    d = find_top(AppearanceDialog)
    d.reset()
    check(d.current().image == LOCAL_BACKGROUND_IMAGE and d.current().image_mode == "center",
          "local Reset to Default is the bunny badge")
    d.reject()
    yield lambda: find_top(AppearanceDialog) is None

    ssh.apply_appearance(Appearance(image=os.path.join(TMP, "missing.png"), image_mode="stretch"))
    yield 0.3
    check(pixel(win, ssh) == (DEFAULT_BACKGROUND, 255), "missing image file shows the colour")


if __name__ == "__main__":
    sys.exit(run(test, timeout=90))
