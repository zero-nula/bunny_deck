"""The connections sidebar: every saved connection with its colour, a search
field, and which servers have terminals open right now."""

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from . import icons, theme

LOCAL_ID = "__local__"

ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_TARGET = Qt.ItemDataRole.UserRole + 1
ROLE_COLOR = Qt.ItemDataRole.UserRole + 2
ROLE_OPEN = Qt.ItemDataRole.UserRole + 3


class _ConnectionDelegate(QStyledItemDelegate):
    """Paints a row: colour dot, name, user@host, and a green badge with the
    number of open terminals."""

    HEIGHT = 42

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.HEIGHT)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(4, 2, -4, -2)
        selected = option.state & QStyle.StateFlag.State_Selected
        hovered = option.state & QStyle.StateFlag.State_MouseOver
        if selected or hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1a2436") if selected else QColor("#15181e"))
            p.drawRoundedRect(r, 6, 6)

        color = QColor(index.data(ROLE_COLOR) or theme.ACCENT)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(r.left() + 10, r.center().y() - 4.5, 9, 9))

        n_open = index.data(ROLE_OPEN) or 0
        right = r.right() - 8
        if n_open:
            f = QFont(option.font)
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            text = str(n_open)
            w = max(18, p.fontMetrics().horizontalAdvance(text) + 10)
            badge = QRectF(right - w, r.center().y() - 8, w, 16)
            p.setBrush(QColor(108, 192, 112, 45))
            p.setPen(QPen(QColor(theme.OK), 1))
            p.drawRoundedRect(badge, 8, 8)
            p.setPen(QColor(theme.OK))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
            right = badge.left() - 6

        x = r.left() + 28
        width = right - x
        f = QFont(option.font)
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff" if selected else "#dfe3ea"))
        name = p.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "",
                                          Qt.TextElideMode.ElideRight, int(width))
        p.drawText(QRectF(x, r.top() + 3, width, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        f.setBold(False)
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(theme.DIM))
        target = p.fontMetrics().elidedText(index.data(ROLE_TARGET) or "",
                                            Qt.TextElideMode.ElideMiddle, int(width))
        p.drawText(QRectF(x, r.top() + 20, width, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, target)
        p.restore()


class ConnectionSidebar(QWidget):
    """openRequested(id) opens a terminal (LOCAL_ID: a local shell);
    keySetupRequested(id), removeRequested(id), addRequested(),
    importRequested() ask the main window to do the rest."""

    openRequested = pyqtSignal(str)
    keySetupRequested = pyqtSignal(str)
    removeRequested = pyqtSignal(str)
    addRequested = pyqtSignal()
    importRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(170)

        title = QLabel("CONNECTIONS")
        title.setObjectName("sidebarTitle")
        add = QToolButton()
        add.setObjectName("sidebarAdd")
        add.setIcon(icons.icon("plus"))
        add.setIconSize(QSize(16, 16))
        add.setAutoRaise(True)
        add.setToolTip("New SSH connection (Ctrl+Shift+N)")
        add.clicked.connect(self.addRequested)
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 6, 4)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(add)

        self.search = QLineEdit()
        self.search.setObjectName("sidebarSearch")
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._open_first_visible)

        self.list = QListWidget()
        self.list.setObjectName("sidebarList")
        self.list.setItemDelegate(_ConnectionDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setToolTip("Double-click or Enter opens a terminal; right-click for more")
        self.list.itemActivated.connect(lambda item: self.openRequested.emit(item.data(ROLE_ID)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)

        self.empty = QWidget()
        ev = QVBoxLayout(self.empty)
        ev.setContentsMargins(12, 4, 12, 4)
        msg = QLabel("No saved connections yet.")
        msg.setObjectName("dimLabel")
        msg.setWordWrap(True)
        imp = QPushButton("Import from ~/.ssh/config")
        imp.setIcon(icons.icon("import"))
        imp.clicked.connect(self.importRequested)
        ev.addWidget(msg)
        ev.addWidget(imp)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 6)
        v.setSpacing(4)
        v.addLayout(head)
        sv = QHBoxLayout()
        sv.setContentsMargins(8, 0, 8, 4)
        sv.addWidget(self.search)
        v.addLayout(sv)
        v.addWidget(self.list, 1)
        v.addWidget(self.empty)
        self.no_match = QLabel("No match.")
        self.no_match.setObjectName("dimLabel")
        self.no_match.setContentsMargins(14, 0, 14, 0)
        v.addWidget(self.no_match)
        self.no_match.hide()

    def set_connections(self, profiles, open_counts, local_color, local_open):
        """profiles: saved Profiles; open_counts: {profile id: open terminals}."""
        current = self.list.currentItem().data(ROLE_ID) if self.list.currentItem() else None
        self.list.clear()
        rows = [(LOCAL_ID, "Local terminal", "this computer", local_color, local_open)]
        for p in sorted(profiles, key=lambda p: p.name.lower()):
            rows.append((p.id, p.name, p.target, theme.accent_for(p.appearance),
                         open_counts.get(p.id, 0)))
        for pid, name, target, color, n in rows:
            item = QListWidgetItem(name)
            item.setData(ROLE_ID, pid)
            item.setData(ROLE_TARGET, target)
            item.setData(ROLE_COLOR, color)
            item.setData(ROLE_OPEN, n)
            item.setToolTip(f"{name} — {target}" + (f"\n{n} open" if n else ""))
            self.list.addItem(item)
            if pid == current:
                self.list.setCurrentItem(item)
        self.empty.setVisible(not profiles)
        self._filter(self.search.text())

    def item_for(self, profile_id):
        for i in range(self.list.count()):
            if self.list.item(i).data(ROLE_ID) == profile_id:
                return self.list.item(i)
        return None

    def _filter(self, text):
        words = text.lower().split()
        visible = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            hay = f"{item.text()} {item.data(ROLE_TARGET)}".lower()
            hide = any(w not in hay for w in words)
            item.setHidden(hide)
            visible += not hide
        self.no_match.setVisible(bool(words) and not visible)

    def _open_first_visible(self):
        for i in range(self.list.count()):
            item = self.list.item(i)
            if not item.isHidden():
                self.openRequested.emit(item.data(ROLE_ID))
                return

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        pid = item.data(ROLE_ID)
        menu = QMenu(self)
        menu.addAction("Open terminal").triggered.connect(lambda: self.openRequested.emit(pid))
        if pid != LOCAL_ID:
            menu.addAction("Set up key login…").triggered.connect(
                lambda: self.keySetupRequested.emit(pid))
            menu.addSeparator()
            menu.addAction("Remove…").triggered.connect(lambda: self.removeRequested.emit(pid))
        menu.exec(self.list.viewport().mapToGlobal(pos))
