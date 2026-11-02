"""A terminal panel: compact header + QTermWidget.

The QTermWidget and its child process are created exactly once per panel.
Layout changes may move the panel between splitters, but nothing here is
recreated because of a resize, and there are no resize/scroll overrides:
QTermWidget handles resizing itself.
"""

import os
import signal

from PyQt6.QtCore import QEvent, QObject, QPropertyAnimation, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from . import background, colorschemes, icons, shortcuts, sshmux, theme
from .config import Appearance
from .native import QTermWidget

FONT_FAMILY = "Monospace"
FONT_SIZE = 10.5
SCROLLBACK_LINES = 10000
SSH_BINARY = "/usr/bin/ssh"
FALLBACK_SHELL = "/bin/bash"

# True while keys typed in one terminal are being replayed into the others
# (broadcast), so the replayed keys are not broadcast again.
_forwarding = False


def local_shell():
    """The user's login shell from $SHELL, or /bin/bash if it is unset,
    not an absolute path, or not executable."""
    shell = os.environ.get("SHELL", "")
    if os.path.isabs(shell) and os.path.isfile(shell) and os.access(shell, os.X_OK):
        return shell
    return FALLBACK_SHELL


def default_argv(profile):
    """Local profiles run the shell without arguments; SSH profiles run ssh,
    sharing one connection per host (see sshmux.py)."""
    if profile.is_local:
        return [local_shell()]
    return [SSH_BINARY] + sshmux.master_opts() + profile.ssh_args()


def _process_alive(pid):
    """False once the process is gone or is a zombie waiting to be reaped."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False
    return state != "Z"


class _TerminalKeys(QObject):
    """Bunny Deck's shortcuts (shortcuts.py) on the terminal display, and
    broadcasting of everything else typed.

    Konsole's TerminalDisplay claims every key (also as ShortcutOverride),
    so QShortcut never sees these; they are intercepted on the display."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def _action(self, event):
        name = shortcuts.action_for(event)
        if name is None:
            return None
        local = {
            "copy": self.panel.copy,
            "paste": self.panel.paste,
            "broadcast": self.panel.broadcastToggleRequested.emit,
            "zoom-in": lambda: self.panel.zoom(1),
            "zoom-out": lambda: self.panel.zoom(-1),
            "zoom-reset": lambda: self.panel.zoom(0),
        }
        return local.get(name) or (lambda: self.panel.shortcutRequested.emit(self.panel, name))

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.ShortcutOverride and self._action(event):
            event.accept()
            return True
        if t == QEvent.Type.KeyPress:
            action = self._action(event)
            if action is not None:
                action()
                return True
            self.panel.forward_key(event)
        if (t == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.MiddleButton):
            for p in self.panel.broadcast_peers():
                p.terminal.pasteSelection()
        if t == QEvent.Type.FocusIn:
            self.panel.focused.emit(self.panel)
        return False


class TerminalPanel(QWidget):
    """One independent terminal session."""

    closeRequested = pyqtSignal(object)
    scpRequested = pyqtSignal(object)
    appearanceRequested = pyqtSignal(object)
    keySetupRequested = pyqtSignal(object)
    broadcastToggleRequested = pyqtSignal()
    # (panel, action name from shortcuts.py) for the window's shortcuts
    shortcutRequested = pyqtSignal(object, str)
    broadcastChanged = pyqtSignal(object)
    focused = pyqtSignal(object)
    # (panel, QMenu): the window fills the Move menu just before it opens.
    moveMenuRequested = pyqtSignal(object, object)
    # The Move button: the window shows the snap layouts for this panel.
    snapRequested = pyqtSignal(object)
    # Dragging by the header: (panel, global QPoint, "start" | "move" | "drop").
    headerDragged = pyqtSignal(object, object, str)

    _counter = 0

    def __init__(self, profile, argv=None, appearance=None, parent=None):
        super().__init__(parent)
        TerminalPanel._counter += 1
        self.session_id = TerminalPanel._counter
        self.profile = profile
        self.argv = argv or default_argv(profile)
        self.child_pid = None
        self.running = False
        self._bg_file = ""
        self.broadcast = False
        self.font_size = FONT_SIZE
        # Set by the window: the other terminals that receive what is typed
        # here while broadcasting (empty otherwise).
        self.broadcast_peers = lambda: []
        self.setObjectName("termPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        self.terminal = self._build_terminal()
        layout.addWidget(self.terminal, 1)
        self.apply_appearance(appearance or profile.appearance)

    # ----- UI -------------------------------------------------------------

    def _build_header(self):
        header = self.header = QWidget(self)
        header.setObjectName("termHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(header)
        h.setContentsMargins(6, 0, 4, 0)
        h.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.name_label = QLabel(self.profile.name)
        self.name_label.setObjectName("termName")
        self.target_label = QLabel(self.profile.target)
        self.target_label.setObjectName("termTarget")
        self.status_label = QLabel("starting")
        self.status_label.setObjectName("termStatus")
        # Only user@host gives way (down to nothing) when the panel is narrow;
        # name and status keep their natural width so they stay readable.
        self.target_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for lbl in (self.name_label, self.status_label):
            lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        self.broadcast_box = QCheckBox(header)
        self.broadcast_box.setObjectName("broadcastBox")
        self.broadcast_box.setToolTip("Broadcast: what you type goes to every ticked terminal")
        self.broadcast_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.broadcast_box.toggled.connect(self.set_broadcast)
        self.broadcast_box.hide()
        h.addWidget(self.broadcast_box)
        h.addWidget(self.status_dot)
        h.addWidget(self.name_label)
        h.addWidget(self.target_label, 1)
        h.addWidget(self.status_label)

        def button(icon_name, text, tip, sig=None, name="hdrBtn"):
            b = QToolButton(header)
            b.setText(text)  # (the accessible name; only the icon shows)
            b.setIcon(icons.icon(icon_name))
            b.setIconSize(QSize(14, 14))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            b.setToolTip(tip)
            b.setObjectName(name)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if sig is not None:
                b.clicked.connect(lambda: sig.emit(self))
            h.addWidget(b)
            return b

        self.move_btn = button("move", "Move", "Snap layouts: choose where this terminal goes. "
                               "You can also drag the terminal by its header.", self.snapRequested)
        # The exact "left of / right of … another terminal" menu, offered in
        # the snap popup.
        self.move_menu = QMenu(self.move_btn)
        self.move_menu.aboutToShow.connect(lambda: self.moveMenuRequested.emit(self, self.move_menu))
        self._drag_start = None
        self._dragging = False
        self.appearance_btn = button("image", "Background", "Background: colour, opacity and "
                                     "image of this terminal", self.appearanceRequested)
        # Move and background show while the mouse is over the header
        # (faded in, so the header never shifts); close is always there.
        self.tools = (self.move_btn, self.appearance_btn)
        self._hovered = False
        self._tool_effects = []
        for b in self.tools:
            effect = QGraphicsOpacityEffect(b)
            effect.setOpacity(0.0)
            b.setGraphicsEffect(effect)
            self._tool_effects.append(effect)
        self.close_btn = button("close", "✕", "Close this session (Ctrl+Shift+W)",
                                self.closeRequested, "hdrClose")
        header.installEventFilter(self)
        header.setCursor(Qt.CursorShape.OpenHandCursor)
        return header

    def _build_terminal(self):
        term = QTermWidget(0, self)  # 0: configure first, start below
        term.setShellProgram(self.argv[0])
        term.setArgs(self.argv[1:])
        # $HOME for both kinds (ssh ignores it; a local shell starts there).
        term.setWorkingDirectory(os.path.expanduser("~"))
        font = QFont(FONT_FAMILY)
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSizeF(FONT_SIZE)
        term.setTerminalFont(font)
        term.setHistorySize(SCROLLBACK_LINES)
        term.setScrollBarPosition(QTermWidget.ScrollBarPosition.ScrollBarRight)
        term.setBlinkingCursor(False)
        term.setFlowControlEnabled(False)
        term.setConfirmMultilinePaste(False)
        if hasattr(term, "autoHideMouseAfter"):  # not in older QTermWidget (e.g. 2.1)
            term.autoHideMouseAfter(2000)
        term.setMargin(2)
        term.setTerminalSizeHint(False)  # no "Size: 80 x 24" overlay while resizing
        # Keep the display (and the last output, e.g. why ssh ended) when the
        # session ends. With the default autoClose QTermWidget hides it, which
        # leaves a fully transparent hole in the translucent window.
        term.setAutoClose(False)
        # With autoClose off QTermWidget no longer emits finished(), so the
        # end of the session is detected from the process itself.
        term.finished.connect(self._on_finished)
        term.titleChanged.connect(self._check_alive)
        self._alive_timer = QTimer(self)
        self._alive_timer.setInterval(1000)
        self._alive_timer.timeout.connect(self._check_alive)
        term.termGetFocus.connect(lambda: self.focused.emit(self))
        term.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        term.customContextMenuRequested.connect(self._context_menu)

        self._keys = _TerminalKeys(self)
        for w in term.findChildren(QWidget):
            if w.metaObject().className() == "Konsole::TerminalDisplay":
                w.installEventFilter(self._keys)
                self.display = w
        # A background image is composed to the display's size: recompose it
        # once a resize has settled (the terminal itself is not involved).
        self._bg_timer = QTimer(self)
        self._bg_timer.setSingleShot(True)
        self._bg_timer.setInterval(120)
        self._bg_timer.timeout.connect(self._update_background_image)
        self.display.installEventFilter(self)
        return term

    def eventFilter(self, obj, event):
        if obj is self.header and event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self._hovered = event.type() == QEvent.Type.Enter
            self._show_tools(self._hovered)
        elif obj is self.header and event.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonRelease):
            return self._header_drag(event)
        elif (obj is getattr(self, "display", None) and event.type() == QEvent.Type.Resize
                and self.appearance.image):
            self._bg_timer.start()
        return False

    def _header_drag(self, event):
        """Press and drag the header to move the terminal (see headerDragged)."""
        t = event.type()
        gpos = event.globalPosition().toPoint()
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = gpos
            return False
        if t == QEvent.Type.MouseMove and self._drag_start is not None:
            if not self._dragging:
                if (gpos - self._drag_start).manhattanLength() < \
                        QApplication.startDragDistance():
                    return False
                self._dragging = True
                self.header.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.headerDragged.emit(self, gpos, "start")
            self.headerDragged.emit(self, gpos, "move")
            return True
        if t == QEvent.Type.MouseButtonRelease:
            was = self._dragging
            self._drag_start = None
            self._dragging = False
            if was:
                self.header.setCursor(Qt.CursorShape.OpenHandCursor)
                self.headerDragged.emit(self, gpos, "drop")
                return True
        return False

    def _show_tools(self, on):
        """Fades the header's Move / Files / Background buttons in or out."""
        for effect in self._tool_effects:
            anim = QPropertyAnimation(effect, b"opacity", effect)
            anim.setDuration(120)
            anim.setEndValue(1.0 if on else 0.0)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # ----- appearance -----------------------------------------------------

    def apply_appearance(self, appearance: Appearance):
        """Change colours live; never touches the running process."""
        self.appearance = appearance
        self.terminal.setColorScheme(colorschemes.scheme_path(appearance.background))
        self.terminal.setTerminalOpacity(appearance.opacity)
        self._update_background_image()
        self._update_active_line()

    def _update_background_image(self):
        """(Re)composes the background image at the display's current size.
        A missing image file (e.g. deleted) just shows the colour."""
        a = self.appearance
        path = ""
        if a.image:
            size = self.display.size()
            path = background.render(a.image, a.image_mode, a.background, a.opacity,
                                     size.width(), size.height())
        self.terminal.setTerminalBackgroundMode(background.STRETCH_MODE)
        self.terminal.setTerminalBackgroundImage(path)
        self._remove_background_file()
        self._bg_file = path

    def _remove_background_file(self):
        if self._bg_file:
            try:
                os.unlink(self._bg_file)
            except OSError:
                pass
            self._bg_file = ""

    # ----- process --------------------------------------------------------

    def spawn(self):
        self._set_status("connecting", "connecting")
        self.terminal.startShellProgram()
        pid = self.terminal.getShellPID()
        if pid <= 0:
            self._set_status("failed to start", "error")
            return
        self.child_pid = pid
        self.running = True
        self._set_status("connected", "ok")
        self._alive_timer.start()

    def _check_alive(self):
        if self.running and self.child_pid and not _process_alive(self.child_pid):
            self._on_finished()

    def _on_finished(self):
        # QTermWidget does not report the exit code. The terminal keeps
        # showing the last output (e.g. why ssh ended).
        if not self.running and self.child_pid is None:
            return
        self._alive_timer.stop()
        self.running = False
        self.child_pid = None
        self._set_status("disconnected", "off")

    def terminate(self):
        self._alive_timer.stop()
        self._bg_timer.stop()
        self._remove_background_file()
        if self.child_pid:
            try:
                os.kill(self.child_pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            self.child_pid = None
        self.running = False

    def _set_status(self, text, state):
        self.status_label.setText(text)
        # "connected" is the green dot; other states are spelled out.
        self.status_label.setVisible(state != "ok")
        self.status_dot.setToolTip(text)
        self.status_dot.setProperty("state", state)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    # ----- clipboard ------------------------------------------------------

    def copy(self):
        self.terminal.copyClipboard()

    def zoom(self, step):
        """Font size of this terminal: +1 / -1 point, 0 back to the default."""
        size = FONT_SIZE if step == 0 else max(6.0, min(40.0, self.font_size + step))
        if size == self.font_size:
            return
        self.font_size = size
        font = QFont(FONT_FAMILY)
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSizeF(size)
        self.terminal.setTerminalFont(font)

    def paste(self):
        self.terminal.pasteClipboard()
        for p in self.broadcast_peers():
            p.terminal.pasteClipboard()

    # ----- broadcast ------------------------------------------------------

    def set_broadcast_mode(self, on, include=False):
        """Shows or hides the broadcast tick box; include ticks it."""
        self.broadcast_box.setVisible(on)
        self.broadcast_box.setChecked(on and include)

    def set_broadcast(self, on):
        if on == self.broadcast:
            return
        self.broadcast = on
        if self.broadcast_box.isChecked() != on:
            self.broadcast_box.setChecked(on)
        self.setProperty("broadcast", on)
        # The header's look depends on this property of its parent too.
        for w in (self, self.header):
            w.style().unpolish(w)
            w.style().polish(w)
        self.broadcastChanged.emit(self)

    def forward_key(self, event):
        """Replays a key typed here into the other broadcast terminals."""
        global _forwarding
        if _forwarding:
            return
        peers = self.broadcast_peers()
        if not peers:
            return
        _forwarding = True
        try:
            for p in peers:
                QApplication.sendEvent(p.display, QKeyEvent(
                    event.type(), event.key(), event.modifiers(), event.text(),
                    event.isAutoRepeat(), event.count()))
        finally:
            _forwarding = False

    def _context_menu(self, pos):
        menu = QMenu(self)
        copy = QAction("Copy", menu)
        copy.setShortcut(QKeySequence("Ctrl+Shift+C"))
        copy.setEnabled(bool(self.terminal.selectedText()))
        copy.triggered.connect(self.copy)
        paste = QAction("Paste", menu)
        paste.setShortcut(QKeySequence("Ctrl+Shift+V"))
        paste.triggered.connect(self.paste)
        menu.addAction(copy)
        menu.addAction(paste)
        menu.addSeparator()
        files = QAction("Files for this connection", menu)
        files.setShortcut(QKeySequence("Ctrl+Shift+F"))
        files.triggered.connect(lambda: self.scpRequested.emit(self))
        menu.addAction(files)
        if not self.profile.is_local:
            menu.addSeparator()
            menu.addAction("Set up key login…").triggered.connect(
                lambda: self.keySetupRequested.emit(self))
        menu.exec(self.terminal.mapToGlobal(pos))

    def set_active(self, active):
        self.setProperty("active", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self._update_active_line()

    def _update_active_line(self):
        """A thin line over the active terminal's header, in its colour."""
        color = theme.accent_for(self.appearance) if self.property("active") else "transparent"
        self.header.setStyleSheet(f"#termHeader {{ border-top: 2px solid {color}; }}")

    def focus_terminal(self):
        getattr(self, "display", self.terminal).setFocus()
