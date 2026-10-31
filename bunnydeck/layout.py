"""Arranges terminal panels in a tree of nested QSplitters.

The root splitter holds panels and/or child splitters; every child splitter
runs across its parent's direction, so any mix of rows and columns can be
built. Panels are only ever moved directly from one splitter into another
(QSplitter.insertWidget re-parents in place): they are never made top-level,
re-created or restarted. Window resizes are handled by QSplitter, which keeps
the existing size proportions.
"""

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QSplitter, QVBoxLayout, QWidget

# "Vertical split": panels side by side, separated by vertical dividers.
# "Horizontal split": panels stacked, separated by horizontal dividers.
VERTICAL_SPLIT = Qt.Orientation.Horizontal
HORIZONTAL_SPLIT = Qt.Orientation.Vertical

LEFT, RIGHT, ABOVE, BELOW = "left", "right", "above", "below"
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
