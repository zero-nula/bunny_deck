"""Background images for terminals, composed to the terminal's size.

QTermWidget's own Fit and Center modes leave the area around the image
unpainted (it shows white), and it has no Fill mode. So the image is placed
here on the background colour, at exactly the display's size, and handed to
QTermWidget in Stretch mode, which then draws it 1:1. The files live in the
private runtime directory of the colour schemes, removed when the app quits.
"""

import itertools
import os

from PyQt6.QtCore import QRectF, QSizeF
from PyQt6.QtGui import QColor, QImage, QPainter

from . import colorschemes

STRETCH_MODE = 1  # QTermWidget (Konsole BackgroundMode::Stretch)

_counter = itertools.count()
_sources = {}  # (path, mtime) -> QImage


def _load(path):
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return None
    if key not in _sources:
        img = QImage(path)
        if img.isNull():
            return None
        _sources.clear()  # keep only the latest few images in memory
        _sources[key] = img
    return _sources[key]


def target_rect(mode, image_w, image_h, width, height):
    """Where the image goes inside a width x height display."""
    if mode == "stretch":
        return QRectF(0, 0, width, height)
    if mode == "center":
        scale = 1.0
    elif mode == "fill":
        scale = max(width / image_w, height / image_h)
    else:  # fit
        scale = min(width / image_w, height / image_h)
    size = QSizeF(image_w * scale, image_h * scale)
    return QRectF((width - size.width()) / 2, (height - size.height()) / 2,
                  size.width(), size.height())


def render(image_path, mode, background, opacity, width, height):
    """Writes the composed background and returns its path ("" if the image
    cannot be read or the size is empty)."""
    src = _load(image_path)
    if src is None or width <= 0 or height <= 0:
        return ""
    out = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    color = QColor(background)
    color.setAlphaF(opacity)
    out.fill(color)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(target_rect(mode, src.width(), src.height(), width, height), src)
    p.end()
    path = os.path.join(colorschemes.scheme_dir(), f"bunnydeck-bg-{next(_counter)}.png")
    return path if out.save(path) else ""
