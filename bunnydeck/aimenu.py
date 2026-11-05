"""The AI menu of the toolbar: each added assistant as a row with a coloured
initial, its name, its command and how many of its terminals are open.

The rows are QWidgetActions whose text is the tool's name, so the menu still
works like a normal one (actions, trigger()); only the looks are custom.
"""

import zlib

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget, QWidgetAction,
)

from . import icons, theme


# Tiles of tools without a colour of their own: one of these, by name.
PALETTE = ("#a78bfa", "#f0a35e", "#4fc1b0", "#e879a6", "#5b8def", "#7cc46e")


def tool_colour(tool):
    colour = theme.accent_for(tool.appearance)
    if colour != theme.ACCENT:
        return colour  # the colour the user chose for its terminal
    return PALETTE[zlib.crc32(tool.name.lower().encode()) % len(PALETTE)]


def initial(name):
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "?"


def tile_pixmap(name, colour, size, dpr=2.0):
    """A rounded square in the tool's colour with its initial."""
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = QColor(colour)
    fill = QColor(base)
    fill.setAlpha(55)
    p.setPen(base)
    p.setBrush(fill)
    r = QRectF(0.5, 0.5, size - 1, size - 1)
    p.drawRoundedRect(r, size * 0.28, size * 0.28)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 0.5))
    p.setFont(f)
    p.setPen(base.lighter(135))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, initial(name))
    p.end()
    return pm


def tile_icon(name, colour, size=16):
    return QIcon(tile_pixmap(name, colour, size))


class _Row(QWidget):
    """One assistant: tile, name, command, open count. Clicking triggers
    the action and closes the menu."""

    def __init__(self, action, tool, n_open, parent=None):
        super().__init__(parent)
        self.action = action
        self.setObjectName("aiRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Open {tool.name} in a new terminal\n$ {tool.command}")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 10, 6)
        h.setSpacing(10)
        tile = QLabel()
        tile.setPixmap(tile_pixmap(tool.name, tool_colour(tool), 30))
        tile.setFixedSize(30, 30)
        h.addWidget(tile)
        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(tool.name)
        name.setObjectName("aiName")
        cmd = QLabel(tool.command)
        cmd.setObjectName("aiCommand")
        text.addWidget(name)
        text.addWidget(cmd)
        h.addLayout(text, 1)
        if n_open:
            badge = QLabel(f"{n_open} open")
            badge.setObjectName("aiOpen")
            h.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        for w in (tile, name, cmd):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            menu = self.parentWidget()
            while menu is not None and not isinstance(menu, QMenu):
                menu = menu.parentWidget()
            if menu is not None:
                menu.close()
            self.action.trigger()


def _header(menu, text):
    a = QWidgetAction(menu)
    lbl = QLabel(text)
    lbl.setObjectName("menuHeader")
    a.setDefaultWidget(lbl)
    a.setEnabled(False)
    menu.addAction(a)


def fill(menu, tools, open_counts, on_open, on_add, on_remove):
    """Rebuilds menu. tools: AITools (sorted); open_counts: {tool id: n}."""
    menu.clear()
    menu.setObjectName("aiMenu")
    _header(menu, "AI ASSISTANTS")
    if not tools:
        a = QWidgetAction(menu)
        a.setText("No AI tools added")
        box = QLabel("No AI tools added yet.\nAdd a command-line assistant such as "
                     "claude, codex, aider or opencode; it opens in its own terminal.")
        box.setObjectName("aiEmpty")
        box.setWordWrap(True)
        box.setFixedWidth(260)
        a.setDefaultWidget(box)
        a.setEnabled(False)
        menu.addAction(a)
    for tool in tools:
        a = QWidgetAction(menu)
        a.setText(tool.name)
        a.setDefaultWidget(_Row(a, tool, open_counts.get(tool.id, 0)))
        a.triggered.connect(lambda _=False, t=tool: on_open(t))
        menu.addAction(a)
    menu.addSeparator()
    menu.addAction(icons.icon("plus"), "Add AI tool…").triggered.connect(on_add)
    remove = menu.addMenu(icons.icon("trash"), "Remove")
    remove.setEnabled(bool(tools))
    for tool in tools:
        a = remove.addAction(tile_icon(tool.name, tool_colour(tool)),
                             f"{tool.name}\t{tool.command}")
        a.triggered.connect(lambda _=False, tid=tool.id: on_remove(tid))
    return remove
