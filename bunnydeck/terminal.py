"""A terminal panel: compact header + QTermWidget.

The QTermWidget and its child process are created exactly once per panel.
Layout changes may move the panel between splitters, but nothing here is
recreated because of a resize, and there are no resize/scroll overrides:
QTermWidget handles resizing itself.
"""

import os
import signal

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from . import background, colorschemes, sshmux
from .config import Appearance
from .native import QTermWidget

FONT_FAMILY = "Monospace"
FONT_SIZE = 10.5
SCROLLBACK_LINES = 10000
SSH_BINARY = "/usr/bin/ssh"
FALLBACK_SHELL = "/bin/bash"


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


class _ClipboardKeys(QObject):
    """Ctrl+Shift+C / Ctrl+Shift+V / Shift+Insert for the terminal display.

    Konsole's TerminalDisplay claims every key (also as ShortcutOverride),
    so QShortcut never sees these; they are intercepted on the display."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def _action(self, event):
        mods = event.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                                    Qt.KeyboardModifier.ShiftModifier |
                                    Qt.KeyboardModifier.AltModifier |
                                    Qt.KeyboardModifier.MetaModifier)
        ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        if mods == ctrl_shift and event.key() == Qt.Key.Key_C:
            return self.panel.copy
        if mods == ctrl_shift and event.key() == Qt.Key.Key_V:
            return self.panel.paste
        if mods == Qt.KeyboardModifier.ShiftModifier and event.key() == Qt.Key.Key_Insert:
            return self.panel.paste
        return None

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
        if t == QEvent.Type.FocusIn:
            self.panel.focused.emit(self.panel)
        return False


class TerminalPanel(QWidget):
    """One independent terminal session."""

    closeRequested = pyqtSignal(object)
    scpRequested = pyqtSignal(object)
    appearanceRequested = pyqtSignal(object)
    keySetupRequested = pyqtSignal(object)
    focused = pyqtSignal(object)
    # (panel, QMenu): the window fills the Move menu just before it opens.
    moveMenuRequested = pyqtSignal(object, object)

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
        header = QWidget(self)
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

        h.addWidget(self.status_dot)
        h.addWidget(self.name_label)
        h.addWidget(self.target_label, 1)
        h.addWidget(self.status_label)

        def button(text, tip, sig, name="hdrBtn"):
            b = QToolButton(header)
            b.setText(text)
            b.setToolTip(tip)
            b.setObjectName(name)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda: sig.emit(self))
            h.addWidget(b)
            return b

        self.move_btn = QToolButton(header)
        self.move_btn.setText("Move")
        self.move_btn.setToolTip("Place this terminal next to another one")
        self.move_btn.setObjectName("hdrBtn")
        self.move_btn.setAutoRaise(True)
        self.move_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.move_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_menu = QMenu(self.move_btn)
        self.move_menu.aboutToShow.connect(lambda: self.moveMenuRequested.emit(self, self.move_menu))
        self.move_btn.setMenu(self.move_menu)
        h.addWidget(self.move_btn)
        self.scp_btn = button("SCP", "Open file manager for this connection", self.scpRequested)
        self.appearance_btn = button("Background", "Terminal background (colour, opacity "
                                     "and image)", self.appearanceRequested)
        # May shrink (clipping its text) so two panels still fit a narrow window.
        self.appearance_btn.setMinimumWidth(40)
        self.close_btn = button("✕", "Close this session", self.closeRequested, "hdrClose")
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

        self._keys = _ClipboardKeys(self)
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
        if obj is self.display and event.type() == QEvent.Type.Resize and self.appearance.image:
            self._bg_timer.start()
        return False

    # ----- appearance -----------------------------------------------------

    def apply_appearance(self, appearance: Appearance):
        """Change colours live; never touches the running process."""
        self.appearance = appearance
        self.terminal.setColorScheme(colorschemes.scheme_path(appearance.background))
        self.terminal.setTerminalOpacity(appearance.opacity)
        self._update_background_image()

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
        self.status_dot.setToolTip(text)
        self.status_dot.setProperty("state", state)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    # ----- clipboard ------------------------------------------------------

    def copy(self):
        self.terminal.copyClipboard()

    def paste(self):
        self.terminal.pasteClipboard()

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
        if not self.profile.is_local:
            menu.addSeparator()
            menu.addAction("Set up key login…").triggered.connect(
                lambda: self.keySetupRequested.emit(self))
        menu.exec(self.terminal.mapToGlobal(pos))

    def set_active(self, active):
        self.setProperty("active", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)

    def focus_terminal(self):
        getattr(self, "display", self.terminal).setFocus()
