"""A terminal panel: compact header + QTermWidget.

The QTermWidget and its child process are created exactly once per panel.
Layout changes may move the panel between splitters, but nothing here is
recreated because of a resize, and there are no resize/scroll overrides:
QTermWidget handles resizing itself.
"""

import os
import re
import signal
import time

from PyQt6.QtCore import QEvent, QObject, QPropertyAnimation, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QFontDatabase, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from . import background, colorschemes, icons, latency, shortcuts, sshmux, stats, theme
from .config import Appearance
from .native import QTermWidget

FONT_FAMILY = "Monospace"  # when the desktop names no fixed-width font
TEXT_MARGIN = 6  # pixels between the terminal's edge and the text
FONT_SIZE = 10.5
SCROLLBACK_LINES = 10000
SSH_BINARY = "/usr/bin/ssh"
FALLBACK_SHELL = "/bin/bash"

# True while keys typed in one terminal are being replayed into the others
# (broadcast), so the replayed keys are not broadcast again.
_forwarding = False


def terminal_font(size):
    """The desktop's fixed-width font (KDE: Hack, GNOME: its monospace font)
    at the given size."""
    family = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    font = QFont(family or FONT_FAMILY)
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setFixedPitch(True)
    font.setPointSizeF(size)
    return font


# ----- who is logged in: root or not ------------------------------------------
#
# Read from what the shell shows: the prompt (the last, unfinished line of
# output) and the window title most distributions set ("user@host: dir").
# A root shell, also after `sudo -i` or `su -`, shows "root@host...#" or
# "bash-5.2#". Prompts that show neither (some custom ones) are not judged.

_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b.")
_ROOT_PROMPT = re.compile(r"(?:^|[\s\[(])root@[\w.-]+.*#\s?$|^[\w.-]*#\s?$")
_USER_PROMPT = re.compile(r"[\w.-]+@[\w.-]+.*[$%>#]\s?$|^[\w.-]*[$%>]\s?$")


def prompt_is_root(tail):
    """True/False when the last line of output looks like a root/user
    prompt, None when it does not look like a prompt."""
    line = _ESCAPES.sub("", tail).replace("\r\n", "\n")
    line = re.split(r"[\r\n]", line)[-1]
    if len(line) > 200:
        return None
    if _ROOT_PROMPT.search(line):
        return True
    if _USER_PROMPT.search(line):
        return False
    return None


def title_is_root(title):
    m = re.match(r"\s*\[?([\w.-]+)@", title or "")
    return None if m is None else m.group(1) == "root"


def _process_uid(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


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
        if t == QEvent.Type.KeyPress and not self.panel.running \
                and self.panel.child_pid is None and self.panel.ended \
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.panel.reconnectRequested.emit(self.panel)
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
    # The session ended and the user wants it again (button, Enter, menu).
    reconnectRequested = pyqtSignal(object)
    sessionEnded = pyqtSignal(object)
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
        self.argv_given = argv  # what a reconnect passes on
        self.child_pid = None
        self.running = False
        self.ended = False  # the session ran and has ended (not: never started)
        self._bg_file = ""
        # Root: known from the profile at first, then from the prompt/title.
        self.is_root = False
        self._root_evidence = profile.user == "root"
        self._output_tail = ""
        # Latency only for real SSH sessions (not when argv is given, as in tests).
        self.measure_latency = argv is None and not profile.is_local
        self._latency = None
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
        self.target_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.target_label.setMinimumWidth(1)
        for lbl in (self.name_label, self.status_label):
            lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        self.broadcast_box = QCheckBox(header)
        self.broadcast_box.setObjectName("broadcastBox")
        self.broadcast_box.setToolTip("Broadcast: what you type goes to every ticked terminal")
        self.broadcast_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # The header shows a hand for dragging; things you click get a pointer.
        self.broadcast_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.broadcast_box.toggled.connect(self.set_broadcast)
        self.broadcast_box.hide()
        h.addWidget(self.broadcast_box)
        h.addWidget(self.status_dot)
        h.addWidget(self.name_label)
        self.root_badge = QLabel("root")
        self.root_badge.setObjectName("rootBadge")
        self.root_badge.setToolTip("You are logged in as root here: every command has full "
                                   "rights on this machine")
        self.root_badge.setFixedHeight(15)
        self.root_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.root_badge.hide()
        h.addWidget(self.root_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.target_label)
        # The empty space is where you grab the terminal to move it; the
        # name, the tick box and the buttons are not.
        h.addStretch(1)
        # The stats button: a chart icon with the ping beside it (SSH);
        # click for the computer's live stats.
        self.stats_btn = QToolButton(header)
        self.stats_btn.setObjectName("statsBtn")
        self.stats_btn.setIcon(icons.icon("stats"))
        self.stats_btn.setIconSize(QSize(13, 13))
        self.stats_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.stats_btn.setAutoRaise(True)
        self.stats_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stats_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stats_btn.setToolTip("Stats: CPU, memory, disks and ping of this computer")
        self.stats_btn.clicked.connect(self.show_stats)
        self.stats_btn.hide()
        self.stats_popup = None
        self._stats_closed_at = 0.0
        h.addWidget(self.stats_btn)
        h.addWidget(self.status_label)
        self._no_drag = (self.broadcast_box, self.status_dot, self.name_label,
                         self.root_badge, self.target_label,
                         self.status_label)
        for w in self._no_drag:
            if w is not self.broadcast_box:
                w.setCursor(Qt.CursorShape.ArrowCursor)

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
            b.setCursor(Qt.CursorShape.PointingHandCursor)
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
        # Shown when the session has ended: start it again in the same place.
        self.reconnect_btn = QToolButton(header)
        self.reconnect_btn.setObjectName("reconnectBtn")
        self.reconnect_btn.setText("Restart" if self.profile.is_local else "Reconnect")
        self.reconnect_btn.setIcon(icons.icon("refresh", color="#ffffff"))
        self.reconnect_btn.setIconSize(QSize(12, 12))
        self.reconnect_btn.setFixedHeight(18)
        self.reconnect_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.reconnect_btn.setToolTip(
            ("Start the shell again" if self.profile.is_local else
             f"Connect to {self.profile.target} again") + " (or press Enter in the terminal)")
        self.reconnect_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reconnect_btn.clicked.connect(lambda: self.reconnectRequested.emit(self))
        self.reconnect_btn.hide()
        h.addWidget(self.reconnect_btn, 0, Qt.AlignmentFlag.AlignVCenter)
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
        term.setTerminalFont(terminal_font(FONT_SIZE))
        term.setHistorySize(SCROLLBACK_LINES)
        term.setScrollBarPosition(QTermWidget.ScrollBarPosition.ScrollBarRight)
        term.setBlinkingCursor(False)
        term.setFlowControlEnabled(False)
        term.setConfirmMultilinePaste(False)
        if hasattr(term, "autoHideMouseAfter"):  # not in older QTermWidget (e.g. 2.1)
            term.autoHideMouseAfter(2000)
        term.setMargin(TEXT_MARGIN)
        term.setTerminalSizeHint(False)  # no "Size: 80 x 24" overlay while resizing
        # Keep the display (and the last output, e.g. why ssh ended) when the
        # session ends. With the default autoClose QTermWidget hides it, which
        # leaves a fully transparent hole in the translucent window.
        term.setAutoClose(False)
        # With autoClose off QTermWidget no longer emits finished(), so the
        # end of the session is detected from the process itself.
        term.finished.connect(self._on_finished)
        term.titleChanged.connect(self._check_alive)
        term.titleChanged.connect(self._title_changed)
        term.receivedData.connect(self._on_output)
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

    def _set_grabbed(self, on):
        """Lights the terminal up while it is held by its header."""
        if bool(self.property("grabbed")) == on:
            return
        self.setProperty("grabbed", on)
        for w in (self, self.header, self.name_label):
            w.style().unpolish(w)
            w.style().polish(w)
        if on:
            if getattr(self, "_grab_tint", None) is None:
                self._grab_tint = QWidget(self)
                self._grab_tint.setObjectName("grabTint")
                self._grab_tint.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._grab_tint.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._grab_tint.setGeometry(self.rect())
            self._grab_tint.raise_()
            self._grab_tint.show()
        elif getattr(self, "_grab_tint", None) is not None:
            self._grab_tint.hide()

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
            if self.header.childAt(event.position().toPoint()) in self._no_drag:
                return False
            self._drag_start = gpos
            self._set_grabbed(True)
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
            self._set_grabbed(False)
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
        self._update_root()
        self.stats_btn.show()
        if self.measure_latency:
            self.start_latency()

    def _check_alive(self):
        if self.running and self.child_pid and not _process_alive(self.child_pid):
            self._on_finished()
        elif self.running and self.profile.is_local:
            self._update_root()

    # ----- root and latency in the header ----------------------------------

    def _on_output(self, text):
        self._output_tail = (self._output_tail + text)[-400:]
        root = prompt_is_root(self._output_tail)
        if root is not None and root != self._root_evidence:
            self._root_evidence = root
            self._update_root()

    def _title_changed(self):
        root = title_is_root(self.terminal.title())
        if root is not None and root != self._root_evidence:
            self._root_evidence = root
            self._update_root()

    def _update_root(self):
        root = self.running and self._root_evidence
        if self.running and self.profile.is_local:
            # A local terminal: the process in front (also `sudo -i`) says it.
            uid = _process_uid(self.terminal.getForegroundProcessId())
            if uid is not None:
                root = uid == 0
        if root != self.is_root:
            self.is_root = root
            self.root_badge.setVisible(root)

    def start_latency(self):
        if self._latency is None:
            self._latency = latency.HostWatch(self.profile.ssh_args(), self)
            self._latency.updated.connect(self._show_latency)

    def _stop_latency(self):
        if self._latency is not None:
            self._latency.stop()
            self._latency = None
        self._show_latency(-1.0)

    def _show_latency(self, ms):
        """The ping beside the stats icon (-1: none, only the icon shows)."""
        b = self.stats_btn
        if ms < 0:
            level, text = "", ""
            b.setToolTip("Stats: CPU, memory and disks of this computer")
        else:
            level = "good" if ms < 60 else "fair" if ms < 150 else "slow"
            text = f"{ms:.0f} ms" if ms >= 1 else "<1 ms"
            b.setToolTip(f"Ping to the server: {ms:.1f} ms. Click for CPU, memory, "
                         "disks and the ping graph.")
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                             else Qt.ToolButtonStyle.ToolButtonIconOnly)
        if b.property("level") != level:
            b.setProperty("level", level)
            b.style().unpolish(b)
            b.style().polish(b)
        if self.stats_popup is not None:
            self.stats_popup.update_ping()

    @property
    def latency_text(self):
        return self.stats_btn.text()

    def stats_argv(self):
        """The stats script: over the shared SSH connection, or here for a
        local terminal (and for test terminals that run a local shell)."""
        if self.profile.is_local or not self.measure_latency:
            return stats.local_argv()
        return stats.ssh_argv(self.profile.ssh_args())

    def show_stats(self):
        if self.stats_popup is not None:
            self.stats_popup.close()
            return
        # The click on this button that closed the popup must not reopen it.
        if self._stats_closed_at and time.monotonic() - self._stats_closed_at < 0.3:
            return
        history = self._latency.history if self._latency is not None else None
        title = "This computer" if self.profile.is_local else self.profile.name
        popup = stats.StatsPopup(title, self.stats_argv(), history)
        popup.closed.connect(self._stats_closed)
        self.stats_popup = popup
        popup.show_below(self.stats_btn)

    def _stats_closed(self):
        self.stats_popup = None
        self._stats_closed_at = time.monotonic()

    def _on_finished(self):
        # QTermWidget does not report the exit code. The terminal keeps
        # showing the last output (e.g. why ssh ended).
        if not self.running and self.child_pid is None:
            return
        self._alive_timer.stop()
        self.running = False
        self.child_pid = None
        self._set_status("disconnected", "off")
        self.ended = True
        self.reconnect_btn.show()
        self.sessionEnded.emit(self)
        self._stop_latency()
        self.stats_btn.hide()
        if self.stats_popup is not None:
            self.stats_popup.close()
        self._update_root()

    def terminate(self):
        self._alive_timer.stop()
        self._stop_latency()
        if self.stats_popup is not None:
            self.stats_popup.close()
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
        self.terminal.setTerminalFont(terminal_font(size))

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
        if self.ended:
            menu.addSeparator()
            menu.addAction(self.reconnect_btn.icon(), self.reconnect_btn.text()).triggered \
                .connect(lambda: self.reconnectRequested.emit(self))
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
