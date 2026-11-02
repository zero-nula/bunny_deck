"""The start screen, shown when no terminal is open: the main actions as big
buttons and the connections opened most recently."""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import icons, theme

# (action, icon, title, shortcut)
ACTIONS = (
    ("new-ssh", "plus", "New SSH connection", "Ctrl+Shift+N"),
    ("new-local", "terminal", "Local terminal", "Ctrl+Shift+T"),
    ("network-scan", "radar", "Scan your network", "Ctrl+Shift+M"),
    ("import", "import", "Import from ~/.ssh/config", ""),
)


class WelcomeScreen(QWidget):
    """actionRequested(name): one of ACTIONS; openRequested(profile id)."""

    actionRequested = pyqtSignal(str)
    openRequested = pyqtSignal(str)

    def __init__(self, app_icon_path="", parent=None):
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        column = QWidget()
        column.setMaximumWidth(620)
        cv = QVBoxLayout(column)
        cv.setSpacing(6)

        logo = QLabel()
        icon = QIcon(app_icon_path) if app_icon_path else QIcon()
        if not icon.isNull():
            logo.setPixmap(icon.pixmap(QSize(72, 72)))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Bunny Deck")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("SSH terminals and files for the Linux desktop")
        sub.setObjectName("dimLabel")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cv.addWidget(logo)
        cv.addWidget(title)
        cv.addWidget(sub)
        cv.addSpacing(22)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.action_buttons = {}
        for i, (name, icon_name, text, keys) in enumerate(ACTIONS):
            b = QPushButton()
            b.setObjectName("welcomeCard")
            b.setIcon(icons.icon(icon_name, color=theme.ACCENT, on_color=theme.ACCENT))
            b.setIconSize(QSize(26, 26))
            b.setText(f"{text}\n{keys}" if keys else text)
            b.setSizePolicy(b.sizePolicy().Policy.Expanding, b.sizePolicy().Policy.Fixed)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.actionRequested.emit(n))
            grid.addWidget(b, i // 2, i % 2)
            self.action_buttons[name] = b
        cv.addLayout(grid)
        cv.addSpacing(22)

        recent_title = QLabel("RECENT")
        recent_title.setObjectName("sidebarTitle")
        cv.addWidget(recent_title)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        cv.addLayout(self.recent_box)
        self.recent_buttons = []
        self.no_recent = QLabel("The connections you open will appear here.")
        self.no_recent.setObjectName("dimLabel")
        cv.addWidget(self.no_recent)
        cv.addSpacing(18)
        hint = QLabel("Ctrl+Shift+?  shows all keyboard shortcuts")
        hint.setObjectName("dimLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cv.addWidget(hint)

        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(column, 10)
        h.addStretch(1)
        v = QVBoxLayout(self)
        v.addStretch(1)
        v.addLayout(h)
        v.addStretch(2)

    def set_recent(self, profiles):
        for b in self.recent_buttons:
            self.recent_box.removeWidget(b)
            b.deleteLater()
        self.recent_buttons = []
        for p in profiles:
            b = QPushButton()
            b.setObjectName("recentRow")
            b.setIcon(icons.dot(theme.accent_for(p.appearance), 10))
            b.setText(f"{p.name}     {p.target}")
            b.setToolTip(f"Open {p.name} ({p.target})")
            b.setSizePolicy(b.sizePolicy().Policy.Expanding, b.sizePolicy().Policy.Fixed)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, pid=p.id: self.openRequested.emit(pid))
            self.recent_box.addWidget(b)
            self.recent_buttons.append(b)
        self.no_recent.setVisible(not profiles)
