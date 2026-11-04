"""Line icons for the toolbar, drawn with QPainter so they look the same on
every desktop and icon theme, and stay sharp at any scale."""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

NORMAL = "#aab1bd"
HOVER = "#ffffff"
ON = "#ffffff"

SIZE = 20  # logical size; drawn on a 24 x 24 grid


def _plus(p):
    p.drawLine(QPointF(12, 5), QPointF(12, 19))
    p.drawLine(QPointF(5, 12), QPointF(19, 12))


def _terminal(p):
    p.drawRoundedRect(QRectF(3, 4.5, 18, 15), 2.5, 2.5)
    path = QPainterPath(QPointF(7, 9.5))
    path.lineTo(QPointF(10, 12))
    path.lineTo(QPointF(7, 14.5))
    p.drawPath(path)
    p.drawLine(QPointF(12, 15), QPointF(16.5, 15))


def _saved(p):  # a bookmarked list of servers
    for y in (6, 12, 18):
        p.drawPoint(QPointF(5, y))
        p.drawLine(QPointF(9, y), QPointF(19, y))


def _ai(p):  # sparkles
    def star(cx, cy, r):
        k = r * 0.22
        pts = [(0, -r), (k, -k), (r, 0), (k, k), (0, r), (-k, k), (-r, 0), (-k, -k)]
        path = QPainterPath(QPointF(cx + pts[0][0], cy + pts[0][1]))
        for x, y in pts[1:]:
            path.lineTo(QPointF(cx + x, cy + y))
        path.closeSubpath()
        p.drawPath(path)

    star(10, 13, 7.5)
    star(18, 6, 3.2)


def _radar(p):  # a radar screen: rings, a sweep and a blip
    p.drawEllipse(QPointF(12, 12), 9, 9)
    pen = p.pen()
    thin = QPen(pen)
    thin.setWidthF(pen.widthF() * 0.7)
    p.setPen(thin)
    p.drawArc(QRectF(7.5, 7.5, 9, 9), 0, 360 * 16)
    p.setPen(pen)
    p.drawLine(QPointF(12, 12), QPointF(18.4, 5.6))
    p.setBrush(pen.color())
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(12, 12), 1.4, 1.4)
    p.drawEllipse(QPointF(8.2, 15.8), 1.6, 1.6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _broadcast(p):
    pen = p.pen()
    p.setBrush(pen.color())
    p.drawEllipse(QPointF(12, 12), 1.8, 1.8)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for r, span in ((5, 100), (9, 90)):
        rect = QRectF(12 - r, 12 - r, 2 * r, 2 * r)
        p.drawArc(rect, int((180 - span / 2) * 16), int(span * 16))
        p.drawArc(rect, int(-span / 2 * 16), int(span * 16))


def _split_v(p):  # side by side
    p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
    p.drawLine(QPointF(12, 5), QPointF(12, 19))


def _split_h(p):  # stacked
    p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
    p.drawLine(QPointF(3.5, 12), QPointF(20.5, 12))


def _folder(p):
    path = QPainterPath(QPointF(3, 7))
    path.lineTo(QPointF(3, 18))
    path.quadTo(QPointF(3, 19), QPointF(4, 19))
    path.lineTo(QPointF(20, 19))
    path.quadTo(QPointF(21, 19), QPointF(21, 18))
    path.lineTo(QPointF(21, 9))
    path.quadTo(QPointF(21, 8), QPointF(20, 8))
    path.lineTo(QPointF(11, 8))
    path.lineTo(QPointF(9, 5.5))
    path.lineTo(QPointF(4, 5.5))
    path.quadTo(QPointF(3, 5.5), QPointF(3, 7))
    p.drawPath(path)


def _keyboard(p):
    p.drawRoundedRect(QRectF(2.5, 6, 19, 12), 2, 2)
    for x in (6, 9.3, 12.6, 15.9):
        p.drawPoint(QPointF(x, 9.5))
    for x in (7.5, 10.8, 14.1, 17.4):
        p.drawPoint(QPointF(x, 12.2))
    p.drawLine(QPointF(8, 15), QPointF(16, 15))


def _sidebar(p):
    p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
    p.drawLine(QPointF(9, 5), QPointF(9, 19))
    for y in (8.5, 11.5):
        p.drawLine(QPointF(5.5, y), QPointF(6.8, y))


def _move(p):  # four arrows
    p.drawLine(QPointF(12, 4), QPointF(12, 20))
    p.drawLine(QPointF(4, 12), QPointF(20, 12))
    heads = (((12, 4), (-2.5, 2.5), (2.5, 2.5)), ((12, 20), (-2.5, -2.5), (2.5, -2.5)),
             ((4, 12), (2.5, -2.5), (2.5, 2.5)), ((20, 12), (-2.5, -2.5), (-2.5, 2.5)))
    for (x, y), a, b in heads:
        p.drawLine(QPointF(x, y), QPointF(x + a[0], y + a[1]))
        p.drawLine(QPointF(x, y), QPointF(x + b[0], y + b[1]))


def _image(p):  # a picture: background
    p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
    path = QPainterPath(QPointF(4, 17))
    path.lineTo(QPointF(9.5, 11.5))
    path.lineTo(QPointF(13, 15))
    path.lineTo(QPointF(15.5, 12.5))
    path.lineTo(QPointF(20, 17))
    p.drawPath(path)
    p.drawEllipse(QPointF(15.5, 8.8), 1.3, 1.3)


def _close(p):
    p.drawLine(QPointF(7, 7), QPointF(17, 17))
    p.drawLine(QPointF(17, 7), QPointF(7, 17))


def _import(p):  # arrow into a tray
    p.drawLine(QPointF(12, 4), QPointF(12, 14))
    p.drawLine(QPointF(12, 14), QPointF(8.5, 10.5))
    p.drawLine(QPointF(12, 14), QPointF(15.5, 10.5))
    path = QPainterPath(QPointF(4, 14))
    path.lineTo(QPointF(4, 19))
    path.lineTo(QPointF(20, 19))
    path.lineTo(QPointF(20, 14))
    p.drawPath(path)


def _search(p):
    p.drawEllipse(QPointF(10.5, 10.5), 5.5, 5.5)
    p.drawLine(QPointF(14.5, 14.5), QPointF(19.5, 19.5))


def _layouts(p):  # a snap layout: one big zone, two small ones
    p.drawRoundedRect(QRectF(3.5, 5, 7.5, 14), 1.8, 1.8)
    p.drawRoundedRect(QRectF(13, 5, 7.5, 6), 1.8, 1.8)
    p.drawRoundedRect(QRectF(13, 13, 7.5, 6), 1.8, 1.8)


def _folder_plus(p):
    _folder(p)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
    p.drawLine(QPointF(9.25, 13.75), QPointF(14.75, 13.75))


def _trash(p):
    p.drawLine(QPointF(4.5, 7), QPointF(19.5, 7))
    p.drawLine(QPointF(9.5, 7), QPointF(10, 4.5))
    p.drawLine(QPointF(10, 4.5), QPointF(14, 4.5))
    p.drawLine(QPointF(14, 4.5), QPointF(14.5, 7))
    path = QPainterPath(QPointF(6.5, 7))
    path.lineTo(QPointF(7.5, 19.5))
    path.lineTo(QPointF(16.5, 19.5))
    path.lineTo(QPointF(17.5, 7))
    p.drawPath(path)
    p.drawLine(QPointF(10.5, 10.5), QPointF(10.5, 16))
    p.drawLine(QPointF(13.5, 10.5), QPointF(13.5, 16))


def _eye(p):
    path = QPainterPath(QPointF(3, 12))
    path.quadTo(QPointF(12, 3), QPointF(21, 12))
    path.quadTo(QPointF(12, 21), QPointF(3, 12))
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 2.8, 2.8)


def _arrow_up(p):
    p.drawLine(QPointF(12, 19), QPointF(12, 5))
    p.drawLine(QPointF(12, 5), QPointF(6.5, 10.5))
    p.drawLine(QPointF(12, 5), QPointF(17.5, 10.5))


def _arrow_right(p):
    p.drawLine(QPointF(5, 12), QPointF(19, 12))
    p.drawLine(QPointF(19, 12), QPointF(13.5, 6.5))
    p.drawLine(QPointF(19, 12), QPointF(13.5, 17.5))


def _arrow_left(p):
    p.drawLine(QPointF(19, 12), QPointF(5, 12))
    p.drawLine(QPointF(5, 12), QPointF(10.5, 6.5))
    p.drawLine(QPointF(5, 12), QPointF(10.5, 17.5))


def _home(p):
    path = QPainterPath(QPointF(4, 11.5))
    path.lineTo(QPointF(12, 4.5))
    path.lineTo(QPointF(20, 11.5))
    p.drawPath(path)
    path = QPainterPath(QPointF(6.5, 9.5))
    path.lineTo(QPointF(6.5, 19.5))
    path.lineTo(QPointF(17.5, 19.5))
    path.lineTo(QPointF(17.5, 9.5))
    p.drawPath(path)
    p.drawRect(QRectF(10.3, 14, 3.4, 5.5))


def _refresh(p):
    p.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 270 * 16)
    p.drawLine(QPointF(15.5, 6), QPointF(19.2, 5.2))
    p.drawLine(QPointF(15.5, 6), QPointF(16.2, 9.8))


def _key(p):
    p.drawEllipse(QPointF(8, 12), 4, 4)
    p.drawLine(QPointF(12, 12), QPointF(20.5, 12))
    p.drawLine(QPointF(17.5, 12), QPointF(17.5, 15.5))
    p.drawLine(QPointF(20.5, 12), QPointF(20.5, 15))


DRAW = {
    "plus": _plus, "terminal": _terminal, "saved": _saved, "ai": _ai, "radar": _radar,
    "broadcast": _broadcast, "split-v": _split_v, "split-h": _split_h, "folder": _folder,
    "keyboard": _keyboard, "sidebar": _sidebar, "move": _move, "image": _image,
    "close": _close, "import": _import, "search": _search, "layouts": _layouts,
    "folder-plus": _folder_plus, "trash": _trash, "eye": _eye, "arrow-up": _arrow_up,
    "arrow-right": _arrow_right, "arrow-left": _arrow_left, "home": _home, "refresh": _refresh,
    "key": _key,
}


def _pixmap(name, color, size, dpr):
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    DRAW[name](p)
    p.end()
    return pm


def icon(name, color=NORMAL, on_color=ON, size=SIZE):
    """A QIcon in the toolbar colours: dim normally, bright on hover and when
    checked."""
    ic = QIcon()
    for dpr in (1, 2):
        ic.addPixmap(_pixmap(name, color, size, dpr), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(_pixmap(name, HOVER, size, dpr), QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(_pixmap(name, on_color, size, dpr), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(_pixmap(name, on_color, size, dpr), QIcon.Mode.Active, QIcon.State.On)
        ic.addPixmap(_pixmap(name, "#4b5260", size, dpr), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def dot(color, size=12):
    """A filled circle: the colour mark of a connection."""
    ic = QIcon()
    for dpr in (1, 2):
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
        p.end()
        ic.addPixmap(pm)
    return ic
