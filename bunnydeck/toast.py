"""A short message at the bottom right of the main window, optionally with a
button (for example "Apply (up -d)" after a compose file was saved). It goes
away by itself; with a button it stays longer, and the button never acts on
its own."""

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton

from . import icons

PLAIN_MS = 4000
WITH_ACTION_MS = 15000
MARGIN = 16


class Toast(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 8, 10)
        h.setSpacing(10)
        self.label = QLabel()
        self.label.setObjectName("toastText")
        self.label.setWordWrap(True)
        self.label.setMinimumWidth(260)
        self.label.setMaximumWidth(420)
        self.action_btn = QPushButton()
        self.action_btn.setObjectName("primaryBtn")
        self.action_btn.setAutoDefault(False)
        self.action_btn.clicked.connect(self._act)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("toastClose")
        self.close_btn.setIcon(icons.icon("close"))
        self.close_btn.setAutoRaise(True)
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.clicked.connect(self.hide)
        h.addWidget(self.label, 1)
        h.addWidget(self.action_btn)
        h.addWidget(self.close_btn)
        self._action = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text, action_text=None, action=None, error=False):
        self.label.setText(text)
        self.setProperty("error", bool(error))
        self.style().unpolish(self)
        self.style().polish(self)
        self._action = action
        self.action_btn.setVisible(bool(action_text and action))
        if action_text:
            self.action_btn.setText(action_text)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self._timer.start(WITH_ACTION_MS if action else PLAIN_MS)

    def place(self):
        w = self.window_
        self.move(w.width() - self.width() - MARGIN, w.height() - self.height() - MARGIN)

    def _act(self):
        action = self._action
        self.hide()
        if action is not None:
            action()
