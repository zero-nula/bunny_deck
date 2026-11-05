"""Arranges terminal panels in a tree of nested QSplitters.

The root splitter holds panels and/or child splitters; every child splitter
runs across its parent's direction, so any mix of rows and columns can be
built. Panels are only ever moved directly from one splitter into another
(QSplitter.insertWidget re-parents in place): they are never made top-level,
re-created or restarted. Window resizes are handled by QSplitter, which keeps
the existing size proportions.
"""

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSplitter, QVBoxLayout, QWidget

# "Vertical split": panels side by side, separated by vertical dividers.
# "Horizontal split": panels stacked, separated by horizontal dividers.
VERTICAL_SPLIT = Qt.Orientation.Horizontal
HORIZONTAL_SPLIT = Qt.Orientation.Vertical

LEFT, RIGHT, ABOVE, BELOW = "left", "right", "above", "below"
SWAP = "swap"
_SIDE_ORIENTATION = {LEFT: VERTICAL_SPLIT, RIGHT: VERTICAL_SPLIT,
                     ABOVE: HORIZONTAL_SPLIT, BELOW: HORIZONTAL_SPLIT}


def _new_splitter(orientation, parent=None):
    s = QSplitter(orientation, parent)
    s.setChildrenCollapsible(False)
    s.setHandleWidth(3)
    return s


class SplitLayout(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.panels = []
        # True once the user moved a panel; a preset clears it.
        self.custom = False
        self.splitter = _new_splitter(VERTICAL_SPLIT, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.splitter)
        self.overlay = _DropOverlay(self)
        self.overlay.hide()

    @property
    def orientation(self):
        return self.splitter.orientation()

    def add_panel(self, panel, near=None):
        """Adds panel at the end of the root, or right after `near` (in near's
        own splitter) so a custom arrangement is not flattened."""
        self.panels.append(panel)
        parent = near.parentWidget() if near in self.panels else None
        if isinstance(parent, QSplitter):
            parent.insertWidget(parent.indexOf(near) + 1, panel)
        else:
            parent = self.splitter
            parent.addWidget(panel)
        panel.show()
        self._equalize_later(parent)

    def remove_panel(self, panel):
        if panel in self.panels:
            self.panels.remove(panel)
            parent = panel.parentWidget()
            panel.setParent(None)
            self._normalize()
            self._equalize_later(parent if self._alive(parent) else self.splitter)

    def set_orientation(self, orientation):
        """Preset: all panels in one uniform row or column."""
        for i, p in enumerate(self.ordered_panels()):
            if p.parentWidget() is not self.splitter or self.splitter.indexOf(p) != i:
                self.splitter.insertWidget(i, p)
        self.splitter.setOrientation(orientation)
        self._normalize()
        self.custom = False
        self._equalize_later(self.splitter)

    def move_panel(self, panel, ref, side):
        """Places panel to the left of / right of / above / below ref."""
        if panel is ref or panel not in self.panels or ref not in self.panels:
            return False
        orientation = _SIDE_ORIENTATION[side]
        after = side in (RIGHT, BELOW)
        target = ref.parentWidget()
        if target.orientation() != orientation:
            # Wrap ref in a new splitter running the requested direction,
            # keeping the space ref had in its parent.
            sizes = target.sizes()
            box = _new_splitter(orientation)
            target.insertWidget(target.indexOf(ref), box)
            box.addWidget(ref)
            target.setSizes(sizes)
            target = box
        idx = target.indexOf(ref) + (1 if after else 0)
        cur = target.indexOf(panel)
        if cur != -1 and cur < idx:
            idx -= 1  # insertWidget moves an existing child to final index idx
        target.insertWidget(idx, panel)
        panel.show()
        self._normalize()
        self.custom = True
        self._equalize_later(panel.parentWidget())
        return True

    def replace_panel(self, old, new):
        """Puts new exactly where old is (same splitter, index and size) and
        takes old out of the layout."""
        if old not in self.panels:
            return False
        splitter = old.parentWidget()
        sizes = splitter.sizes()
        idx = splitter.indexOf(old)
        self.panels[self.panels.index(old)] = new
        old.setParent(None)
        splitter.insertWidget(idx, new)
        new.show()
        splitter.setSizes(sizes)
        return True

    def swap_panels(self, a, b):
        """Exchanges the places of two terminals."""
        if a is b or a not in self.panels or b not in self.panels:
            return False

        def walk(node):
            orientation, sizes, children = node
            out = []
            for c in children:
                if isinstance(c, tuple):
                    out.append(walk(c))
                else:
                    out.append(b if c is a else a if c is b else c)
            return (orientation, sizes, out)

        custom = self.custom
        self.arrange(walk(self.describe()))
        self.custom = custom or self.custom
        return True

    # ----- dragging a terminal by its header ------------------------------

    def drop_target(self, global_pos, dragged):
        """Where dragged would go if dropped at global_pos: (ref panel, side,
        QRect in this widget's coordinates to preview), or None. The middle of
        a terminal swaps the two; near an edge places dragged on that side."""
        pos = self.mapFromGlobal(global_pos)
        for p in self.panels:
            if p is dragged or not p.isVisible():
                continue
            r = QRect(p.mapTo(self, QPoint(0, 0)), p.size())
            if not r.contains(pos):
                continue
            rx = (pos.x() - r.x()) / max(1, r.width())
            ry = (pos.y() - r.y()) / max(1, r.height())
            if 0.3 < rx < 0.7 and 0.3 < ry < 0.7:
                return p, SWAP, r
            side = min(((rx, LEFT), (1 - rx, RIGHT), (ry, ABOVE), (1 - ry, BELOW)))[1]
            half = QRect(r)
            if side == LEFT:
                half.setWidth(r.width() // 2)
            elif side == RIGHT:
                half.setLeft(r.x() + r.width() // 2)
            elif side == ABOVE:
                half.setHeight(r.height() // 2)
            else:
                half.setTop(r.y() + r.height() // 2)
            return p, side, half
        return None

    def show_drop_preview(self, target, dragged):
        if target is None:
            self.overlay.set_target(None, "", None)
            return
        ref, side, rect = target
        words = {SWAP: "Swap with", LEFT: "Left of", RIGHT: "Right of", ABOVE: "Above",
                 BELOW: "Below"}
        src = QRect(dragged.mapTo(self, QPoint(0, 0)), dragged.size())
        self.overlay.set_target(rect, f"{words[side]} {ref.profile.name}", src)

    def hide_drop_preview(self):
        self.overlay.hide()

    def drop(self, dragged, target):
        if target is None:
            return False
        ref, side, _ = target
        if side == SWAP:
            return self.swap_panels(dragged, ref)
        return self.move_panel(dragged, ref, side)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())

    def ordered_panels(self):
        """Panels in visual order (depth first, left to right / top to bottom)."""
        out = []

        def walk(s):
            for i in range(s.count()):
                w = s.widget(i)
                if isinstance(w, QSplitter):
                    walk(w)
                elif w in self.panels:
                    out.append(w)

        walk(self.splitter)
        return out

    # ----- saving and restoring the arrangement ----------------------------

    def describe(self):
        """The arrangement as nested (orientation, sizes, children); the
        leaves are the panels themselves."""
        def walk(s):
            children = []
            for i in range(s.count()):
                w = s.widget(i)
                if isinstance(w, QSplitter):
                    children.append(walk(w))
                elif w in self.panels:
                    children.append(w)
            return (s.orientation(), s.sizes(), children)

        return walk(self.splitter)

    def arrange(self, tree):
        """Rebuilds the splitters from a describe()-style tree whose leaves
        are panels already added with add_panel. Panels are only moved."""
        built = []  # (splitter, saved sizes)

        def build(splitter, node):
            orientation, sizes, children = node
            splitter.setOrientation(orientation)
            built.append((splitter, sizes))
            for child in children:
                if isinstance(child, tuple):
                    sub = _new_splitter(child[0])
                    splitter.addWidget(sub)
                    build(sub, child)
                else:
                    splitter.addWidget(child)  # moves it to its place
                    child.show()

        build(self.splitter, tree)
        self.custom = any(isinstance(c, tuple) for c in tree[2])
        self._normalize()

        def apply_sizes():
            # Saved sizes are scaled to the splitter's current size, so the
            # proportions are kept in a window of a different size.
            for s, sizes in built:
                if self._alive(s) and len(sizes) == s.count() and sum(sizes) > 0:
                    total = sum(s.sizes()) or sum(sizes)
                    s.setSizes([max(1, x * total // sum(sizes)) for x in sizes])
        # After the equalizing that add_panel queued.
        QTimer.singleShot(0, apply_sizes)

    # ----- tree maintenance -----------------------------------------------

    def _alive(self, s):
        return (isinstance(s, QSplitter) and not sip.isdeleted(s)
                and (s is self.splitter or s.parentWidget() is not None))

    def _normalize(self):
        """Drops empty splitters, unwraps one-child splitters and merges a
        child splitter into a parent running the same direction."""
        changed = True
        while changed:
            changed = self._normalize_once(self.splitter)
        # A root holding only one splitter: adopt its children and direction.
        if self.splitter.count() == 1 and isinstance(self.splitter.widget(0), QSplitter):
            only = self.splitter.widget(0)
            self.splitter.setOrientation(only.orientation())
            self._merge_into(self.splitter, only)
            self._normalize()

    def _normalize_once(self, s):
        for i in range(s.count()):
            w = s.widget(i)
            if not isinstance(w, QSplitter):
                continue
            if self._normalize_once(w):
                return True
            if w.count() == 0:
                self._discard(w)
                return True
            if w.count() == 1 or w.orientation() == s.orientation():
                self._merge_into(s, w)
                return True
        return False

    def _merge_into(self, parent, child):
        """Moves child's widgets into parent at child's position, splitting
        child's share of the space between them proportionally. Only used
        when child has one widget or runs the same direction as parent."""
        idx = parent.indexOf(child)
        outer = parent.sizes()
        inner = child.sizes()
        share = outer[idx] if idx < len(outer) else 0
        total = sum(inner) or 1
        widgets = [child.widget(j) for j in range(child.count())]
        for j, w in enumerate(widgets):
            parent.insertWidget(idx + j, w)
        self._discard(child)
        if share:
            parts = [max(1, share * x // total) for x in inner]
            parent.setSizes(outer[:idx] + parts + outer[idx + 1:])

    def _discard(self, splitter):
        splitter.hide()
        splitter.setParent(None)
        splitter.deleteLater()

    # ----- sizes ----------------------------------------------------------

    def _equalize_later(self, splitter=None):
        # Only after a layout change, never in response to a window resize.
        s = splitter or self.splitter
        QTimer.singleShot(0, lambda: self.equalize(s) if self._alive(s) else None)

    def equalize(self, splitter=None):
        s = splitter or self.splitter
        n = s.count()
        if n:
            total = s.width() if s.orientation() == Qt.Orientation.Horizontal else s.height()
            s.setSizes([max(1, total // n)] * n)


class _DropOverlay(QWidget):
    """The preview while a terminal is dragged: where it will land (accent
    colour) and where it comes from (dimmed)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.target = None
        self.source = None
        self.label = ""

    def set_target(self, rect, label, source):
        self.target, self.label, self.source = rect, label, source
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.source is not None:
            p.fillRect(self.source, QColor(10, 11, 13, 150))
        if self.target is not None:
            r = QRectF(self.target).adjusted(6, 6, -6, -6)
            p.setPen(QPen(QColor(91, 141, 239), 2))
            p.setBrush(QColor(91, 141, 239, 60))
            p.drawRoundedRect(r, 10, 10)
            f = QFont()
            f.setPixelSize(15)
            f.setBold(True)
            p.setFont(f)
            text_w = p.fontMetrics().horizontalAdvance(self.label) + 28
            pill = QRectF(r.center().x() - text_w / 2, r.center().y() - 16, text_w, 32)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(47, 95, 191, 230))
            p.drawRoundedRect(pill, 16, 16)
            p.setPen(QColor("#ffffff"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, self.label)
        p.end()
