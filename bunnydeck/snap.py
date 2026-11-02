"""Snap layouts, as in Windows 11: a popup with layout templates; clicking a
zone of a template puts the terminal there and the others in the remaining
zones.

A template is a tree like layout.SplitLayout.describe(): (orientation,
weights, children), where a leaf is a zone number. Zones are numbered in
reading order (left to right, top to bottom).
"""

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import theme
from .layout import HORIZONTAL_SPLIT, VERTICAL_SPLIT

H, V = VERTICAL_SPLIT, HORIZONTAL_SPLIT  # H: side by side, V: stacked

ALL_SIDE_BY_SIDE = "all-side-by-side"
ALL_STACKED = "all-stacked"

# (name, tree or one of the "all" kinds, which adapt to the number of terminals)
TEMPLATES = [
    ("All side by side", ALL_SIDE_BY_SIDE),
    ("All stacked", ALL_STACKED),
    ("Two columns", (H, [1, 1], [0, 1])),
    ("Big left", (H, [3, 2], [0, (V, [1, 1], [1, 2])])),
    ("Big top", (V, [3, 2], [0, (H, [1, 1], [1, 2])])),
    ("Three columns", (H, [1, 1, 1], [0, 1, 2])),
    ("Big centre", (H, [1, 2, 1], [0, 1, 2])),
    ("Grid", (V, [1, 1], [(H, [1, 1], [0, 1]), (H, [1, 1], [2, 3])])),
    ("Big left, three right", (H, [3, 2], [0, (V, [1, 1, 1], [1, 2, 3])])),
]


def resolve(template, n_panels):
    """The template's tree; the "all" kinds get one zone per terminal."""
    if template == ALL_SIDE_BY_SIDE:
        n = max(1, n_panels)
        return (H, [1] * n, list(range(n)))
    if template == ALL_STACKED:
        n = max(1, n_panels)
        return (V, [1] * n, list(range(n)))
    return template


def zone_count(tree):
    if not isinstance(tree, tuple):
        return 1
    return sum(zone_count(c) for c in tree[2])


def zone_rects(tree, rect):
    """{zone: QRectF} for drawing a template inside rect."""
    out = {}

    def walk(node, r):
        if not isinstance(node, tuple):
            out[node] = r
            return
        orientation, weights, children = node
        total = float(sum(weights))
        pos = r.left() if orientation == H else r.top()
        for w, child in zip(weights, children):
            if orientation == H:
                part = QRectF(pos, r.top(), r.width() * w / total, r.height())
                pos += part.width()
            else:
                part = QRectF(r.left(), pos, r.width(), r.height() * w / total)
                pos += part.height()
            walk(child, part)

    walk(tree, QRectF(rect))
    return out


def assign(tree, panels, chosen=None, zone=None):
    """{zone: [panels]}: chosen goes to zone, the others fill the remaining
    zones in order; terminals beyond the zones share the last zone."""
    n = zone_count(tree)
    others = [p for p in panels if p is not chosen]
    result = {z: [] for z in range(n)}
    free = [z for z in range(n) if not (chosen is not None and z == zone)]
    if chosen is not None and zone is not None and 0 <= zone < n:
        result[zone].append(chosen)
    elif chosen is not None:
        others.insert(0, chosen)
        free = list(range(n))
    for z, p in zip(free, others):
        result[z].append(p)
    extra = others[len(free):]
    if extra:
        last = free[-1] if free else n - 1
        result[last].extend(extra)
    return result


def build(tree, by_zone):
    """An arrange() tree for SplitLayout: zones without terminals are left
    out, a zone with several terminals splits across its parent."""

    def walk(node, parent_orientation):
        if not isinstance(node, tuple):
            panels = by_zone.get(node, [])
            if not panels:
                return None
            if len(panels) == 1:
                return panels[0]
            across = V if parent_orientation == H else H
            return (across, [1000] * len(panels), list(panels))
        orientation, weights, children = node
        kept = [(w, walk(c, orientation)) for w, c in zip(weights, children)]
        kept = [(w, c) for w, c in kept if c is not None]
        if not kept:
            return None
        if len(kept) == 1:
            return kept[0][1]
        return (orientation, [w * 1000 for w, _ in kept], [c for _, c in kept])

    out = walk(tree, V)
    if out is None or not isinstance(out, tuple):
        # A single terminal: one row.
        return (H, [1000], [out] if out is not None else [])
    return out


# ----- the popup --------------------------------------------------------------


class _Thumb(QWidget):
    """One template, drawn as its zones; the zone under the mouse lights up."""

    picked = pyqtSignal(int)

    def __init__(self, name, tree, parent=None):
        super().__init__(parent)
        self.tree = tree
        self.hover = None
        self.setFixedSize(116, 72)
        self.setMouseTracking(True)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _rects(self):
        return zone_rects(self.tree, QRectF(self.rect()).adjusted(5, 5, -5, -5))

    def zone_at(self, pos):
        for z, r in self._rects().items():
            if r.contains(QPointF(pos)):
                return z
        return None

    def mouseMoveEvent(self, event):
        z = self.zone_at(event.position())
        if z != self.hover:
            self.hover = z
            self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, event):
        z = self.zone_at(event.position())
        if z is not None and event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(z)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#262b33"), 1))
        p.setBrush(QColor("#0e1013"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        for z, r in self._rects().items():
            r = r.adjusted(2, 2, -2, -2)
            if z == self.hover:
                p.setPen(QPen(QColor(theme.ACCENT), 1.5))
                p.setBrush(QColor(theme.ACCENT))
            else:
                p.setPen(QPen(QColor("#3a404b"), 1))
                p.setBrush(QColor("#1d2128"))
            p.drawRoundedRect(r, 4, 4)
        p.end()


class SnapPopup(QFrame):
    """picked(template, zone): template is a TEMPLATES entry's tree or kind."""

    picked = pyqtSignal(object, int)

    def __init__(self, n_panels, subtitle, extra=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("snapPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)
        title = QLabel("Snap layouts")
        title.setObjectName("snapTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("dimLabel")
        v.addWidget(title)
        v.addWidget(sub)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.thumbs = []
        for i, (name, template) in enumerate(TEMPLATES):
            tree = resolve(template, n_panels)
            if not isinstance(template, str) and zone_count(tree) > max(2, n_panels):
                continue  # more zones than terminals
            thumb = _Thumb(name, tree)
            thumb.picked.connect(lambda z, t=template: self._pick(t, z))
            grid.addWidget(thumb, len(self.thumbs) // 3, len(self.thumbs) % 3)
            self.thumbs.append((template, thumb))
        v.addLayout(grid)
        if extra is not None:
            v.addWidget(extra)

    def _pick(self, template, zone):
        self.picked.emit(template, zone)
        self.close()

    def show_below(self, widget):
        self.adjustSize()
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        screen = widget.screen().availableGeometry()
        x = min(pos.x(), screen.right() - self.width() - 4)
        self.move(max(screen.left(), x), pos.y() + 4)
        self.show()


def next_to_button(text, menu):
    """A footer button in the popup that opens the exact Move menu."""
    b = QPushButton(text)
    b.setObjectName("snapMore")
    b.setMenu(menu)
    return b
