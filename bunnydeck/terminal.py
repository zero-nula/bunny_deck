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

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QFontDatabase, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QLabel, QMenu, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from . import background, colorschemes, icons, latency, shortcuts, sshmux, stats
from .panelbase import BasePanel
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


def default_argv(profile, remote_command=None):
    """Local profiles run the shell without arguments; SSH profiles run ssh,
    sharing one connection per host (see sshmux.py). remote_command (SSH
    only, already quoted for the remote shell) runs that instead of a login
    shell, with a terminal (-t), e.g. `docker exec -it …`."""
    if profile.is_local:
        return [local_shell()]
    tty = ["-t"] if remote_command else []
    return ([SSH_BINARY] + sshmux.master_opts() + tty + profile.ssh_args()
            + ([remote_command] if remote_command else []))


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


class TerminalPanel(BasePanel):
    """One independent terminal session."""

    is_terminal = True
    scpRequested = pyqtSignal(object)
    appearanceRequested = pyqtSignal(object)
    keySetupRequested = pyqtSignal(object)
    broadcastToggleRequested = pyqtSignal()
    # (panel, action name from shortcuts.py) for the window's shortcuts
    shortcutRequested = pyqtSignal(object, str)
    broadcastChanged = pyqtSignal(object)
    # The session ended and the user wants it again (button, Enter, menu).
    reconnectRequested = pyqtSignal(object)
    sessionEnded = pyqtSignal(object)
    # "Docker…" in the right-click menu (only when the server has Docker).
    dockerRequested = pyqtSignal(object)
    firewallRequested = pyqtSignal(object)

    _counter = 0

    def __init__(self, profile, argv=None, appearance=None, parent=None,
                 remote_command=None, title=None):
        super().__init__(profile, parent)
        TerminalPanel._counter += 1
        self.session_id = TerminalPanel._counter
        self.argv = argv or default_argv(profile, remote_command)
        # What a reconnect passes on.
        self.argv_given = argv
        self.remote_command = remote_command
        self.title = title
        self.child_pid = None
        # ended: the session ran and has ended (not: never started).
        # Set by the window: whether "Docker…" belongs in the menu.
        self.has_docker = lambda: False
        self._bg_file = ""
        # Root: known from the profile at first, then from the prompt/title.
        self.is_root = False
        self._root_evidence = profile.user == "root"
        self._output_tail = ""
        # Latency only for real SSH sessions (not when argv is given, as in tests).
        self.measure_latency = argv is None and not profile.is_local
        self._latency = None
        self.font_size = FONT_SIZE
        # Set by the window: the other terminals that receive what is typed
        # here while broadcasting (empty otherwise).
        self.broadcast_peers = lambda: []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        self.terminal = self._build_terminal()
        layout.addWidget(self.terminal, 1)
        self.apply_appearance(appearance or profile.appearance)

    # ----- UI -------------------------------------------------------------

    def _build_header(self):
        header = self._build_base_header(self.title or self.profile.name)
        h = self.header_layout
        self.target_label = QLabel(self.profile.target)
        self.target_label.setObjectName("termTarget")
        self.status_label = QLabel("starting")
        self.status_label.setObjectName("termStatus")
        # Only user@host gives way (down to nothing) when the panel is narrow;
        # name and status keep their natural width so they stay readable.
        self.target_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.target_label.setMinimumWidth(1)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        self.broadcast_box = QCheckBox(header)
        self.broadcast_box.setObjectName("broadcastBox")
        self.broadcast_box.setToolTip("Broadcast: what you type goes to every ticked terminal")
        self.broadcast_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._add_move_button("terminal")
        # Shown when the session has ended: start it again in the same place.
        self.reconnect_btn = QToolButton(header)
        self.reconnect_btn.setObjectName("reconnectBtn")
        self.reconnect_btn.setText("Restart" if self.profile.is_local and not self.remote_command
                                   else "Reconnect")
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
        self.appearance_btn = self.header_button(
            "image", "Background", "Background: colour, opacity and image of this terminal",
            self.appearanceRequested)
        h.addWidget(self.appearance_btn)
        self._finish_header(
            (self.move_btn, self.appearance_btn),
            (self.broadcast_box, self.status_dot, self.name_label, self.root_badge,
             self.target_label, self.status_label),
            "Close this session (Ctrl+Shift+W)")
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

    def eventFilter(self, obj, event):
        handled, result = self._header_event(obj, event)
        if handled:
            return result
        if (obj is getattr(self, "display", None) and event.type() == QEvent.Type.Resize
                and self.appearance.image):
            self._bg_timer.start()
        elif (obj is getattr(self, "display", None) and event.type() == QEvent.Type.FontChange
                and abs(obj.font().pointSizeF() - self.font_size) > 0.01):
            # Moving the panel (another splitter or window) polishes it again
            # with the style sheet, which resets the font: set ours again.
            QTimer.singleShot(0, self.after_reparent)
        return False

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
        self._set_dot(text, state)

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

    def after_reparent(self):
        # A new window polishes the widgets again with the style sheet, which
        # resets the terminal's font to the default size: set this terminal's
        # (zoomed) font again. Colours and the process are not affected.
        if abs(self.display.font().pointSizeF() - self.font_size) > 0.01:
            self.terminal.setTerminalFont(terminal_font(self.font_size))

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
        selected = bool(self.terminal.selectedText())
        copy.setEnabled(selected)
        copy.triggered.connect(self.copy)
        paste = QAction("Paste", menu)
        paste.setShortcut(QKeySequence("Ctrl+Shift+V"))
        paste.triggered.connect(self.paste)
        menu.addAction(copy)
        if not selected:
            # Programs that use the mouse (Claude Code, vim, htop, tmux…)
            # select by themselves; the terminal does not see that selection.
            tip = QLabel("Nothing selected in the terminal. Where a program uses\n"
                         "the mouse (Claude Code, vim, htop…), it copies its own\n"
                         "selection; hold Shift while dragging to select here.")
            tip.setObjectName("menuHint")
            hint = QWidgetAction(menu)
            hint.setDefaultWidget(tip)
            hint.setEnabled(False)
            menu.addAction(hint)
        menu.addAction(paste)
        menu.addSeparator()
        files = QAction("Files for this connection", menu)
        files.setShortcut(QKeySequence("Ctrl+Shift+F"))
        files.triggered.connect(lambda: self.scpRequested.emit(self))
        menu.addAction(files)
        menu.addAction(self.detach_btn.icon(), self.detach_btn.text()).triggered.connect(
            self._detach_clicked)
        if self.ended:
            menu.addSeparator()
            menu.addAction(self.reconnect_btn.icon(), self.reconnect_btn.text()).triggered \
                .connect(lambda: self.reconnectRequested.emit(self))
        if not self.profile.is_local and self.has_docker():
            menu.addSeparator()
            menu.addAction(icons.icon("docker"), "Docker…").triggered.connect(
                lambda: self.dockerRequested.emit(self))
        if not self.profile.is_local:
            menu.addSeparator()
            menu.addAction(icons.icon("shield"), "Firewall…").triggered.connect(
                lambda: self.firewallRequested.emit(self))
            menu.addAction("Set up key login…").triggered.connect(
                lambda: self.keySetupRequested.emit(self))
        menu.exec(self.terminal.mapToGlobal(pos))

    def focus_terminal(self):
        getattr(self, "display", self.terminal).setFocus()
