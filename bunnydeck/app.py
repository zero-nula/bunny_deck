"""Bunny Deck main application and window (Qt6 / QTermWidget)."""

import os
import re
import shlex
import shutil
import signal
import sys

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusInterface
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QMenu, QMessageBox, QSplitter,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import colorschemes, icons, keysetup, snap, theme, mainthread, session, shortcuts, sshconfig, sshmux
from .config import (
    KIND_LOCAL, Config, Profile, store_background, validate_host, validate_user,
)
from .dialogs import (
    AddAIToolDialog, AddSSHDialog, AppearanceDialog, CredentialDialog, ImportSSHConfigDialog,
    KeySetupDialog, ShortcutsDialog,
)
from .layout import (
    ABOVE, BELOW, HORIZONTAL_SPLIT, LEFT, RIGHT, SWAP, VERTICAL_SPLIT, SplitLayout,
)
from .sidebar import LOCAL_ID, ConnectionSidebar
from .terminal import TerminalPanel, local_shell, terminal_font
from .welcome import WelcomeScreen

APP_ID = "org.bunnydeck.BunnyDeck"
APP_NAME = "Bunny Deck"
ICON_NAME = "bunny-deck"
DBUS_PATH = "/org/bunnydeck/BunnyDeck"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class MainWindow(QMainWindow):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.active_panel = None
        self.file_manager = None
        self.network_scan = None
        self.snap_popup = None
        # One unsaved profile shared by all local terminals, so a colour
        # change applies to every open local terminal (like an SSH profile).
        self.local_profile = Profile(name="Local", host="", kind=KIND_LOCAL,
                                     appearance=config.local_appearance)
        # Broadcast: typing goes to every ticked terminal. Never remembered.
        self.broadcasting = False
        # Likewise one unsaved local profile per AI tool (keyed by tool id).
        self.ai_profiles = {}
        self.setWindowTitle(APP_NAME)
        self.setObjectName("mainWindow")
        # Translucent window: a terminal's background alpha reveals what is
        # behind the window. All other surfaces paint solid backgrounds.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(1200, 760)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.sidebar = None
        self.empty = None
        v.addWidget(self._build_toolbar())
        self.stack = QStackedWidget()
        # (QMainWindow already has a layout() method, hence "layout_".)
        self.layout_ = SplitLayout()
        self.empty = WelcomeScreen(os.path.join(ROOT, "data", "bunny-deck.svg"))
        self.empty.actionRequested.connect(self.run_shortcut)
        self.empty.openRequested.connect(self._on_saved_activate)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.layout_)

        self.sidebar = ConnectionSidebar()
        self.sidebar.openRequested.connect(self._on_sidebar_open)
        self.sidebar.reconnectRequested.connect(self._on_sidebar_reconnect)
        self.sidebar.keySetupRequested.connect(self._on_key_setup)
        self.sidebar.removeRequested.connect(self._on_saved_remove)
        self.sidebar.addRequested.connect(lambda: self.add_ssh())
        self.sidebar.importRequested.connect(self.import_ssh_config)
        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.body.setObjectName("bodySplit")
        self.body.setChildrenCollapsible(False)
        self.body.addWidget(self.sidebar)
        self.body.addWidget(self.stack)
        self.body.setStretchFactor(1, 1)
        self.body.setSizes([230, 970])
        v.addWidget(self.body, 1)
        self.setCentralWidget(root)

        self.set_sidebar_visible(config.sidebar_visible, save=False)
        self.refresh_connections()
        self._update_view()

    # ----- sidebar and start screen ---------------------------------------

    def refresh_connections(self):
        """Updates the sidebar and the start screen (profiles, colours, which
        connections have terminals open, recent ones)."""
        if self.sidebar is None or self.empty is None:
            return
        counts, ended = {}, {}
        local_open = 0
        for p in self.layout_.panels:
            key = LOCAL_ID if p.profile is self.local_profile else p.profile.id
            if p.profile is self.local_profile:
                local_open += 1
            counts[p.profile.id] = counts.get(p.profile.id, 0) + 1
            if p.ended and not p.running:
                ended[key] = ended.get(key, 0) + 1
        self.sidebar.set_connections(self.config.profiles, counts,
                                     theme.accent_for(self.local_profile.appearance), local_open,
                                     ended)
        self.empty.set_recent(self.config.recent_profiles())

    def _on_sidebar_open(self, profile_id):
        if profile_id == LOCAL_ID:
            self.open_local()
        else:
            self._on_saved_activate(profile_id)

    def _on_sidebar_reconnect(self, profile_id):
        """Reconnects the connection's disconnected terminals; if none has
        disconnected (e.g. a frozen session), ends and reopens the running
        ones after asking."""
        panels = [p for p in self.layout_.ordered_panels()
                  if (p.profile is self.local_profile if profile_id == LOCAL_ID
                      else p.profile.id == profile_id)]
        ended = [p for p in panels if p.ended and not p.running]
        if ended:
            for p in ended:
                self.reconnect(p)
            return
        if not panels:
            return
        name = panels[0].profile.name
        n = len(panels)
        what = "shell" if profile_id == LOCAL_ID else "session"
        if not self._confirm(
            f"Reconnect “{name}”?",
            (f"The running {what} will be ended" if n == 1 else
             f"The {n} running {what}s will be ended")
            + " and a new one opened in its place.",
            "Reconnect",
        ):
            return
        for p in panels:
            self.reconnect(p, force=True)

    def set_sidebar_visible(self, visible, save=True):
        self.sidebar.setVisible(visible)
        if self.sidebar_btn.isChecked() != visible:
            self.sidebar_btn.setChecked(visible)
        if save and visible != self.config.sidebar_visible:
            self.config.set_sidebar_visible(visible)

    # ----- toolbar --------------------------------------------------------

    def _tool_button(self, icon_name, name, tip, slot=None, checkable=False):
        """An icon-only toolbar button; name is its accessible name, tip the
        tooltip (with the shortcut)."""
        b = QToolButton()
        b.setIcon(icons.icon(icon_name))
        b.setIconSize(QSize(icons.SIZE, icons.SIZE))
        b.setAccessibleName(name)
        b.setToolTip(f"<b>{name}</b><br>{tip}" if tip else name)
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        b.setProperty("iconOnly", True)
        if slot is not None:
            (b.toggled if checkable else b.clicked).connect(slot)
        return b

    @staticmethod
    def _toolbar_separator():
        line = QFrame()
        line.setObjectName("toolbarSep")
        line.setFixedSize(1, 20)
        return line

    def _build_toolbar(self):
        bar = QWidget()
        bar.setObjectName("deckToolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(4)

        self.sidebar_btn = self._tool_button(
            "sidebar", "Connections sidebar", "Show or hide the saved connections — "
            "Ctrl+Shift+S", lambda on: self.set_sidebar_visible(on), checkable=True)
        h.addWidget(self.sidebar_btn)
        h.addSpacing(2)

        # The two main actions keep their text.
        add = self.add_btn = QToolButton()
        add.setObjectName("primaryBtn")
        add.setText("New SSH")
        add.setIcon(icons.icon("plus", color="#ffffff"))
        add.setIconSize(QSize(16, 16))
        add.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        add.setToolTip("<b>New SSH connection</b><br>Ctrl+Shift+N")
        add.clicked.connect(lambda: self.add_ssh())
        h.addWidget(add)

        self.saved_button = QToolButton()
        self.saved_button.setObjectName("savedBtn")
        self.saved_button.setText("Saved ▾")
        self.saved_button.setIcon(icons.icon("saved"))
        self.saved_button.setIconSize(QSize(icons.SIZE - 2, icons.SIZE - 2))
        self.saved_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.saved_button.setToolTip("<b>Saved connections</b><br>Open, import, set up key "
                                     "login or remove — Ctrl+Shift+O")
        self.saved_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.saved_menu = QMenu(self.saved_button)
        self.saved_button.setMenu(self.saved_menu)
        h.addWidget(self.saved_button)
        h.addSpacing(6)
        h.addWidget(self._toolbar_separator())
        h.addSpacing(6)

        self.local_btn = self._tool_button(
            "terminal", "Local terminal", "A shell on this computer — Ctrl+Shift+T",
            self.open_local)
        h.addWidget(self.local_btn)

        self.ai_button = self._tool_button(
            "ai", "AI assistants", "Open an AI assistant (e.g. Claude) in a new terminal")
        self.ai_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.ai_menu = QMenu(self.ai_button)
        self.ai_button.setMenu(self.ai_menu)
        self.rebuild_ai_menu()
        h.addWidget(self.ai_button)

        self.scan_btn = self._tool_button(
            "radar", "Scan network", "A map of the devices on your network (nmap) — "
            "Ctrl+Shift+M", self.open_network_scan)
        h.addWidget(self.scan_btn)

        self.broadcast_btn = self._tool_button(
            "broadcast", "Broadcast", "Type into several terminals at once — Ctrl+Shift+B",
            self.set_broadcast_mode, checkable=True)
        self.broadcast_btn.setObjectName("broadcastBtn")
        self.broadcast_btn.setIcon(icons.icon("broadcast", on_color="#f0a35a"))
        self.broadcast_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        h.addWidget(self.broadcast_btn)
        # Outside a terminal (e.g. in the file manager) these window-wide
        # shortcuts apply; inside one, the terminal's key filter handles them.
        for seq, action in shortcuts.sequences():
            if action not in ("copy", "paste"):
                QShortcut(seq, self, activated=lambda a=action: self.run_shortcut(a, None))

        h.addStretch(1)
        # Split presets as one segmented control.
        segment = QFrame()
        segment.setObjectName("splitSegment")
        sh = QHBoxLayout(segment)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(0)
        self.vsplit_btn = self._tool_button("split-v", "Side by side",
                                            "Arrange the terminals in a row")
        self.hsplit_btn = self._tool_button("split-h", "Stacked",
                                            "Arrange the terminals top to bottom")
        self.vsplit_btn.setObjectName("segLeft")
        self.hsplit_btn.setObjectName("segRight")
        self.split_group = group = QButtonGroup(self)
        for b in (self.vsplit_btn, self.hsplit_btn):
            b.setCheckable(True)
            b.setAutoRaise(False)
            group.addButton(b)
            sh.addWidget(b)
        self.layouts_btn = self._tool_button(
            "layouts", "Snap layouts", "Arrange the terminals; the current one goes where you "
            "click — Ctrl+Shift+L", lambda: self.show_snap(self.active_panel, self.layouts_btn))
        self.layouts_btn.setObjectName("segRight")
        self.hsplit_btn.setObjectName("segMid")
        self.layouts_btn.setAutoRaise(False)
        sh.addWidget(self.layouts_btn)
        self.vsplit_btn.setChecked(True)
        self.vsplit_btn.toggled.connect(lambda on: on and self._set_split(VERTICAL_SPLIT))
        self.hsplit_btn.toggled.connect(lambda on: on and self._set_split(HORIZONTAL_SPLIT))
        h.addWidget(segment)
        h.addStretch(1)

        self.files_btn = self._tool_button(
            "folder", "Files", "File manager for the current terminal's computer (the "
            "terminals keep running) — Ctrl+Shift+F", self._on_files_toggled, checkable=True)
        self.files_btn.setObjectName("filesBtn")
        self.files_btn.setText("Files")
        self.files_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        h.addWidget(self.files_btn)
        self.help_btn = self._tool_button("keyboard", "Keyboard shortcuts", "Ctrl+Shift+?",
                                          lambda: self.run_shortcut("help"))
        h.addWidget(self.help_btn)
        self.rebuild_saved_menu()
        return bar

    def rebuild_saved_menu(self):
        self.refresh_connections()
        self.saved_menu.clear()
        profiles = sorted(self.config.profiles, key=lambda p: p.name.lower())
        ssh_profiles = [p for p in profiles if not p.is_local]

        def entry(menu, profile, slot):
            # The text after the tab is shown right-aligned and dimmed.
            a = menu.addAction(icons.dot(theme.accent_for(profile.appearance), 10),
                               f"{profile.name}\t{profile.target}")
            a.triggered.connect(lambda _=False, pid=profile.id: slot(pid))
            return a

        if not profiles:
            self.saved_menu.addAction("No saved connections").setEnabled(False)
        for profile in profiles:
            entry(self.saved_menu, profile, self._on_saved_activate)
        self.saved_menu.addSeparator()
        self.saved_menu.addAction(icons.icon("import"), "Import from ~/.ssh/config…") \
            .triggered.connect(self.import_ssh_config)
        keys = self.saved_menu.addMenu(icons.icon("key"), "Set up key login")
        keys.setEnabled(bool(ssh_profiles))
        for profile in ssh_profiles:
            entry(keys, profile, self._on_key_setup)
        # Removing is rare, so it lives at the end of this menu.
        self.remove_menu = self.saved_menu.addMenu(icons.icon("trash"), "Remove")
        self.remove_menu.setEnabled(bool(profiles))
        for profile in profiles:
            entry(self.remove_menu, profile, self._on_saved_remove)

    def _on_key_setup(self, profile_id):
        profile = self.config.get(profile_id)
        if profile is not None:
            self.setup_key_login(profile)

    # ----- ~/.ssh/config and key login ------------------------------------

    def import_ssh_config(self):
        aliases = [a for a in sshconfig.host_aliases() if validate_host(a) is None]
        if not aliases:
            QMessageBox.information(self, "Import from ~/.ssh/config",
                                    f"No hosts found in {sshconfig.config_path()}.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            entries = [(alias, sshconfig.resolve(alias)) for alias in aliases]
        finally:
            QApplication.restoreOverrideCursor()
        saved = {p.host for p in self.config.profiles}
        dlg = ImportSSHConfigDialog(self, entries, saved)
        if dlg.exec():
            for alias in dlg.selected():
                # The alias as host: ssh keeps applying its ~/.ssh/config settings.
                self.config.add(Profile(name=alias, host=alias))
            self.rebuild_saved_menu()
        dlg.deleteLater()

    def setup_key_login(self, profile):
        ks = keysetup.KeySetup(profile, self._ask_key_credentials, mainthread.call_soon)
        existing = keysetup.fingerprint() if keysetup.key_exists() else None
        aliases = sshconfig.host_aliases()
        config_alias = None if profile.host in aliases else _suggest_alias(profile, aliases)
        steps = {"key": ks.create_key, "install": ks.install_key, "verify": ks.verify}

        def run_step(key, args, done):
            keysetup.run_in_thread(lambda: steps[key](*args), done)

        def add_config(alias):
            backup = sshconfig.add_host(alias, profile.host, profile.user, profile.port,
                                        keysetup.key_path())
            note = f" (backup: {os.path.basename(backup)})" if backup else ""
            return f"added “Host {alias}”{note}"

        dlg = KeySetupDialog(self, profile, keysetup.key_path(), existing, config_alias,
                             run_step, add_config)
        try:
            dlg.exec()
        finally:
            ks.close()
            dlg.deleteLater()

    def _ask_key_credentials(self, profile, prompt, kind, retry):
        if kind == "confirm":
            dlg = CredentialDialog(self, f"Verify host — {profile.name}", prompt, confirm=True)
        else:
            dlg = CredentialDialog(self, f"Key setup — {profile.name}", prompt,
                                   secret=True, retry=retry)
        try:
            return dlg.ask()
        finally:
            dlg.deleteLater()

    def _on_saved_activate(self, profile_id):
        profile = self.config.get(profile_id)
        if profile is not None:
            self.open_profile(profile)

    def _on_saved_remove(self, profile_id):
        profile = self.config.get(profile_id)
        if profile is None or not self._confirm(
            f"Remove the saved connection “{profile.name}”?",
            f"Host: {profile.target}\n\nOnly the saved profile is removed. Open sessions, "
            "SSH keys and ~/.ssh/config are not affected.",
            "Remove",
        ):
            return
        if self.file_manager is not None:
            # Keep the profile resolvable for a pane browsing it or a running transfer.
            self.file_manager._extra_profiles[profile.id] = profile
        self.config.remove(profile_id)
        self.rebuild_saved_menu()

    # ----- sessions -------------------------------------------------------

    def add_ssh(self, host="", port=22, name=""):
        """The Add SSH dialog, optionally filled in (e.g. from the network scan)."""
        dlg = AddSSHDialog(self)
        if host:
            dlg.prefill(host, port, name)
        profile, save = dlg.run_for_profile()
        dlg.deleteLater()
        if profile is None:
            return
        if save:
            self.config.add(profile)
            self.rebuild_saved_menu()
        self.open_profile(profile)

    def open_local(self):
        return self.open_profile(self.local_profile)

    # ----- network scan ---------------------------------------------------

    def open_network_scan(self):
        from .netmap import NetworkScanWindow

        if self.network_scan is None:
            self.network_scan = NetworkScanWindow(self)
            self.network_scan.connectRequested.connect(self._connect_from_scan)
        self.network_scan.show()
        self.network_scan.raise_()
        self.network_scan.activateWindow()
        return self.network_scan

    def _connect_from_scan(self, host, port, name):
        self.raise_()
        self.activateWindow()
        self.add_ssh(host, port, name)

    # ----- AI assistants --------------------------------------------------

    def rebuild_ai_menu(self):
        self.ai_menu.clear()
        tools = sorted(self.config.ai_tools, key=lambda t: t.name.lower())
        if not tools:
            self.ai_menu.addAction("No AI tools added").setEnabled(False)
        for tool in tools:
            a = self.ai_menu.addAction(tool.name)
            a.setToolTip(tool.command)
            a.triggered.connect(lambda _=False, t=tool: self.open_ai(t))
        self.ai_menu.setToolTipsVisible(True)
        self.ai_menu.addSeparator()
        self.ai_menu.addAction("Add…").triggered.connect(self.add_ai_tool)
        remove = self.ai_menu.addMenu("Remove")
        remove.setEnabled(bool(tools))
        for tool in tools:
            a = remove.addAction(tool.name)
            a.triggered.connect(lambda _=False, tid=tool.id: self._on_ai_remove(tid))

    def add_ai_tool(self):
        dlg = AddAIToolDialog(self)
        if dlg.exec() and dlg.tool is not None:
            self.config.add_ai_tool(dlg.tool)
            self.rebuild_ai_menu()

    def _on_ai_remove(self, tool_id):
        tool = next((t for t in self.config.ai_tools if t.id == tool_id), None)
        if tool is None or not self._confirm(
            f"Remove “{tool.name}” from the AI menu?",
            f"Command: {tool.command}\n\nOnly the menu entry is removed. Open terminals and "
            "the program itself are not affected.",
            "Remove",
        ):
            return
        self.config.remove_ai_tool(tool_id)
        self.rebuild_ai_menu()

    def open_ai(self, tool):
        """Run the tool's command in a local terminal. When it exits, the user's
        shell takes over in the same terminal, so the last output stays
        readable. The shell is interactive (-i), so ~/.bashrc's PATH and
        aliases apply even when the app was started from the desktop."""
        profile = self.ai_profiles.get(tool.id)
        if profile is None:
            profile = self.ai_profiles[tool.id] = Profile(name=tool.name, host="",
                                                          kind=KIND_LOCAL,
                                                          appearance=tool.appearance)
        shell = local_shell()
        argv = [shell, "-ic", f"{tool.command}; exec {shlex.quote(shell)}"]
        return self.open_profile(profile, argv=argv)

    def _new_panel(self, profile, argv=None, appearance=None):
        """A terminal wired to the window, not yet placed or started."""
        panel = TerminalPanel(profile, argv=argv, appearance=appearance)
        panel.closeRequested.connect(self._on_close_requested)
        panel.appearanceRequested.connect(self._on_appearance_requested)
        panel.keySetupRequested.connect(lambda p: self.setup_key_login(p.profile))
        panel.broadcastToggleRequested.connect(self.broadcast_btn.toggle)
        panel.shortcutRequested.connect(lambda p, action: self.run_shortcut(action, p))
        panel.broadcastChanged.connect(self._update_broadcast_button)
        panel.broadcast_peers = lambda p=panel: self._broadcast_peers(p)
        if self.broadcasting:
            panel.set_broadcast_mode(True, include=False)
        panel.scpRequested.connect(self.open_file_manager)
        panel.focused.connect(self._on_panel_focused)
        panel.moveMenuRequested.connect(self._fill_move_menu)
        panel.snapRequested.connect(lambda p: self.show_snap(p, p.move_btn))
        panel.headerDragged.connect(self._on_header_drag)
        panel.reconnectRequested.connect(self.reconnect)
        panel.sessionEnded.connect(lambda _p: self.refresh_connections())
        return panel

    def open_profile(self, profile, argv=None):
        panel = self._new_panel(profile, argv)
        # In a custom arrangement, open next to the active terminal instead of
        # flattening the layout; with a preset, keep the uniform row/column.
        near = self.active_panel if self.layout_.custom else None
        self.layout_.add_panel(panel, near=near)
        if self.config.get(profile.id) is not None:
            self.config.mark_used(profile.id)
        self.refresh_connections()
        if self.file_manager is not None:
            self.file_manager.sessions_changed()
        self.show_terminals()
        panel.spawn()
        panel.focus_terminal()
        self._on_panel_focused(panel)
        return panel

    # ----- keyboard shortcuts ---------------------------------------------

    def run_shortcut(self, action, panel=None):
        """Runs a shortcut action (shortcuts.py); panel is the terminal it
        was pressed in, or None (then the active terminal)."""
        panel = panel or self.active_panel
        if action == "new-local":
            self.open_local()
        elif action == "new-ssh":
            self.add_ssh()
        elif action == "saved-menu":
            self.saved_button.showMenu()
        elif action == "broadcast":
            self.broadcast_btn.toggle()
        elif action == "network-scan":
            self.open_network_scan()
        elif action == "import":
            self.import_ssh_config()
        elif action == "layouts":
            self.show_snap(panel, self.layouts_btn)
        elif action == "sidebar":
            self.set_sidebar_visible(not self.sidebar.isVisible())
        elif action == "help":
            dlg = ShortcutsDialog(self, shortcuts.OVERVIEW)
            dlg.exec()
            dlg.deleteLater()
        elif action == "files":
            if self.current_view() == "files":
                self.show_terminals()
                if self.active_panel is not None:
                    self.active_panel.focus_terminal()
            else:
                self.open_file_manager(panel)
        elif self.current_view() != "terminals" or panel is None:
            return  # the rest acts on terminals, which are not shown
        elif action == "close":
            self._on_close_requested(panel)
        elif action in ("next", "previous"):
            order = self._panels_in_reading_order()
            i = order.index(panel) if panel in order else 0
            self._focus_panel(order[(i + (1 if action == "next" else -1)) % len(order)])
        elif action.startswith("focus-"):
            target = self._neighbour(panel, action[len("focus-"):])
            if target is not None:
                self._focus_panel(target)
        elif action.startswith("zoom-"):
            panel.zoom({"zoom-in": 1, "zoom-out": -1, "zoom-reset": 0}[action])

    def _focus_panel(self, panel):
        panel.focus_terminal()
        self._on_panel_focused(panel)

    def _panel_rects(self):
        return {p: QRect(p.mapTo(self, QPoint(0, 0)), p.size())
                for p in self.layout_.panels if p.isVisible()}

    def _panels_in_reading_order(self):
        rects = self._panel_rects()
        return sorted(rects, key=lambda p: (rects[p].top(), rects[p].left()))

    def _neighbour(self, panel, direction):
        """The closest terminal in that direction, preferring ones that
        overlap the current one across the direction of movement."""
        rects = self._panel_rects()
        if panel not in rects:
            return None
        c = rects[panel]
        best, best_score = None, None
        for p, r in rects.items():
            if p is panel:
                continue
            if direction == "left":
                ok, gap = r.right() < c.left(), c.left() - r.right()
                overlap = min(r.bottom(), c.bottom()) - max(r.top(), c.top())
            elif direction == "right":
                ok, gap = r.left() > c.right(), r.left() - c.right()
                overlap = min(r.bottom(), c.bottom()) - max(r.top(), c.top())
            elif direction == "up":
                ok, gap = r.bottom() < c.top(), c.top() - r.bottom()
                overlap = min(r.right(), c.right()) - max(r.left(), c.left())
            else:
                ok, gap = r.top() > c.bottom(), r.top() - c.bottom()
                overlap = min(r.right(), c.right()) - max(r.left(), c.left())
            if not ok:
                continue
            score = (overlap <= 0, gap, -overlap)
            if best_score is None or score < best_score:
                best, best_score = p, score
        return best

    # ----- broadcast ------------------------------------------------------

    def set_broadcast_mode(self, on):
        """Turns broadcast on or off. Turning it on ticks nothing: the user
        picks the terminals that should receive the typing."""
        self.broadcasting = on
        if self.broadcast_btn.isChecked() != on:
            self.broadcast_btn.setChecked(on)
        for p in self.layout_.panels:
            p.set_broadcast_mode(on, include=False)
        self._update_broadcast_button()

    def _broadcast_peers(self, panel):
        if not self.broadcasting or not panel.broadcast:
            return []
        return [p for p in self.layout_.panels if p is not panel and p.broadcast]

    def _update_broadcast_button(self, *_):
        n = sum(1 for p in self.layout_.panels if p.broadcast) if self.broadcasting else 0
        # The count of ticked terminals sits next to the icon while it is on.
        self.broadcast_btn.setText(str(n) if self.broadcasting else "")

    def close_panel(self, panel):
        panel.set_broadcast(False)
        panel.terminate()
        self.layout_.remove_panel(panel)
        panel.deleteLater()
        self.refresh_connections()
        if self.active_panel is panel:
            self.active_panel = None
            if self.layout_.panels:
                self.layout_.panels[-1].focus_terminal()
        if self.file_manager is not None:
            self.file_manager.sessions_changed()
        self._update_view()

    def reconnect(self, old, force=False):
        """Starts an ended session again: a new terminal in the old one's
        place, with its background, font size and broadcast tick. force: also
        a running one (it is ended first)."""
        if (old.running and not force) or old not in self.layout_.panels:
            return None
        panel = self._new_panel(old.profile, old.argv_given, old.appearance)
        if self.broadcasting:
            panel.set_broadcast_mode(True, include=old.broadcast)
        if old.font_size != panel.font_size:
            panel.font_size = old.font_size
            panel.terminal.setTerminalFont(terminal_font(old.font_size))
        was_active = self.active_panel is old
        old.set_broadcast(False)
        old.terminate()
        self.layout_.replace_panel(old, panel)
        old.deleteLater()
        if was_active:
            self.active_panel = None
        if self.config.get(old.profile.id) is not None:
            self.config.mark_used(old.profile.id)
        self.refresh_connections()
        if self.file_manager is not None:
            self.file_manager.sessions_changed()
        panel.spawn()
        panel.focus_terminal()
        return panel

    def _on_close_requested(self, panel):
        what = ("The shell in this terminal" if panel.profile.is_local
                else "The SSH connection in this terminal")
        if panel.running and not self._confirm(
            f"Close the session “{panel.profile.name}”?",
            f"{what} will be terminated.",
            "Close Session",
        ):
            return
        self.close_panel(panel)

    def _on_panel_focused(self, panel):
        if self.active_panel is not None and self.active_panel is not panel:
            self.active_panel.set_active(False)
        self.active_panel = panel
        panel.set_active(True)

    def panels_for_profile(self, profile_id):
        return [p for p in self.layout_.panels if p.profile.id == profile_id]

    def _appearance_saver(self, profile):
        """How to persist this profile's appearance, or None if it is not
        saved (an unsaved SSH connection)."""
        if self.config.get(profile.id) is not None:
            return lambda a: self.config.update_appearance(profile.id, a)
        if profile is self.local_profile:
            return self.config.update_local_appearance
        for tool_id, p in self.ai_profiles.items():
            if p is profile:
                return lambda a: self.config.update_ai_tool_appearance(tool_id, a)
        return None

    def _on_appearance_requested(self, panel):
        profile = panel.profile
        saver = self._appearance_saver(profile)
        saved = saver is not None
        siblings = self.panels_for_profile(profile.id)

        def preview(appearance):
            for p in siblings:
                p.apply_appearance(appearance)

        dlg = AppearanceDialog(self, profile, panel.appearance, preview, saveable=saved)
        if not self.supports_alpha():
            dlg.set_note("No compositing available: opacity below 100 % will not show the "
                         "desktop behind the window.")
        result = dlg.run_for_appearance()
        dlg.deleteLater()
        if result is None:
            return
        if saved:
            # A stored copy keeps the background when the original file moves.
            result.image = store_background(result.image)
            saver(result)
        profile.appearance = result
        preview(result)
        self.refresh_connections()

    def supports_alpha(self):
        app = QApplication.instance()
        if app.platformName() == "wayland":
            return True
        return bool(os.environ.get("DISPLAY")) and self.testAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground)

    # ----- file manager ---------------------------------------------------

    def open_file_manager(self, panel=None):
        """Shows the file manager with panel's computer on the right (the
        active terminal's, or this computer's without one)."""
        from .filemanager import FileManager

        if self.file_manager is None:
            self.file_manager = FileManager(self)
            self.stack.addWidget(self.file_manager)
        panel = panel or self.active_panel
        self.file_manager.open_for(panel.profile if panel is not None else self.local_profile)
        # (Without the toggled signal, which would open it again for the
        # active terminal instead of panel.)
        self.files_btn.blockSignals(True)
        self.files_btn.setChecked(True)
        self.files_btn.blockSignals(False)
        self._update_view()

    def _on_files_toggled(self, on):
        if on and self.current_view() != "files":
            self.open_file_manager()
        else:
            self._update_view()

    def show_terminals(self):
        if self.files_btn.isChecked():
            self.files_btn.setChecked(False)
        self._update_view()

    def _update_view(self):
        if self.files_btn.isChecked() and self.file_manager is not None:
            self.stack.setCurrentWidget(self.file_manager)
        elif self.layout_.panels:
            self.stack.setCurrentWidget(self.layout_)
        else:
            self.stack.setCurrentWidget(self.empty)

    def current_view(self):
        w = self.stack.currentWidget()
        if w is self.empty:
            return "empty"
        if w is self.layout_:
            return "terminals"
        return "files"

    # ----- layout / window ------------------------------------------------

    def _set_split(self, orientation):
        self.layout_.set_orientation(orientation)
        self.show_terminals()

    def set_split(self, orientation):
        (self.vsplit_btn if orientation == VERTICAL_SPLIT else self.hsplit_btn).setChecked(True)

    def _fill_move_menu(self, panel, menu):
        menu.clear()
        others = [(i, p) for i, p in enumerate(self.layout_.ordered_panels(), 1) if p is not panel]
        if not others:
            menu.addAction("No other terminals").setEnabled(False)
            return
        for side, title in ((LEFT, "Left of"), (RIGHT, "Right of"),
                            (ABOVE, "Above"), (BELOW, "Below")):
            sub = menu.addMenu(title)
            for i, ref in others:
                a = sub.addAction(f"{i}. {ref.profile.name}    {ref.profile.target}")
                a.triggered.connect(lambda _=False, r=ref, s=side: self.move_panel(panel, r, s))

    # ----- snap layouts and dragging --------------------------------------

    def show_snap(self, panel, anchor):
        """The snap layouts popup for panel (the active terminal if None)."""
        panel = panel if panel in self.layout_.panels else self.active_panel
        if panel is None and self.layout_.panels:
            panel = self.layout_.panels[0]
        if panel is None:
            return None
        n = len(self.layout_.panels)
        extra = None
        if n > 1:
            extra = snap.next_to_button("Next to a terminal…", panel.move_menu)
        popup = snap.SnapPopup(n, f"Click where “{panel.profile.name}” should go", extra, self)
        popup.picked.connect(lambda template, zone: self.apply_snap(template, zone, panel))
        popup.show_below(anchor)
        self.snap_popup = popup
        return popup

    def apply_snap(self, template, zone, panel):
        if panel not in self.layout_.panels:
            return
        tree = snap.resolve(template, len(self.layout_.panels))
        by_zone = snap.assign(tree, self.layout_.ordered_panels(), panel, zone)
        self.layout_.arrange(snap.build(tree, by_zone))
        if template == snap.ALL_SIDE_BY_SIDE:
            self.set_split(VERTICAL_SPLIT)
        elif template == snap.ALL_STACKED:
            self.set_split(HORIZONTAL_SPLIT)
        else:
            self.layout_.custom = True
            self._clear_presets()
        self.show_terminals()
        self._focus_panel(panel)

    def _clear_presets(self):
        # Neither preset applies any more; clicking one re-applies it.
        self.split_group.setExclusive(False)
        for b in (self.vsplit_btn, self.hsplit_btn):
            b.setChecked(False)
        self.split_group.setExclusive(True)

    def _on_header_drag(self, panel, global_pos, phase):
        """A terminal dragged by its header: preview while moving, place it
        on drop (on a side of another terminal, or swap in the middle)."""
        target = self.layout_.drop_target(global_pos, panel)
        if phase in ("start", "move"):
            self.layout_.show_drop_preview(target, panel)
            return
        self.layout_.hide_drop_preview()
        if target is None:
            return
        ref, side, _ = target
        if side == SWAP:
            self.layout_.swap_panels(panel, ref)
            self._focus_panel(panel)
        else:
            self.move_panel(panel, ref, side)

    def move_panel(self, panel, ref, side):
        """Moves a running terminal next to another; switches to Custom Layout."""
        if not self.layout_.move_panel(panel, ref, side):
            return
        self._clear_presets()
        self.show_terminals()
        panel.focus_terminal()

    def closeEvent(self, event):
        running = [p for p in self.layout_.panels if p.running]
        if running and not self._confirm(
            "Quit Bunny Deck?",
            f"{len(running)} session(s) are still running and will be terminated.",
            "Quit",
        ):
            event.ignore()
            return
        unsaved = self.file_manager.unsaved_edits() if self.file_manager is not None else []
        if unsaved and not self._confirm(
            "Quit Bunny Deck?",
            "These files have changes that are not saved on their server: "
            f"{', '.join(unsaved)}. The local copies are kept in "
            "~/.cache/bunny-deck/edit.",
            "Quit",
        ):
            event.ignore()
            return
        self.shutdown()
        event.accept()

    def shutdown(self):
        self.save_session()
        for p in list(self.layout_.panels):
            p.terminate()
        if self.file_manager is not None:
            self.file_manager.shutdown()
        if self.network_scan is not None:
            self.network_scan.scanner.stop()
            self.network_scan.close()

    # ----- session: the terminals reopened on the next start ---------------

    def _session_entry(self, panel):
        profile = panel.profile
        if profile is self.local_profile:
            return {"kind": "local"}
        for tool_id, p in self.ai_profiles.items():
            if p is profile:
                return {"kind": "ai", "tool": tool_id}
        if profile.is_local:
            return None
        if self.config.get(profile.id) is not None:
            return {"kind": "ssh", "profile": profile.id}
        return {"kind": "ssh", "name": profile.name, "host": profile.host,
                "user": profile.user, "port": profile.port}

    def _panel_for_entry(self, entry):
        """Opens the terminal a session entry describes, or None when that
        is no longer possible (connection or AI tool removed)."""
        if not isinstance(entry, dict):
            return None
        kind = entry.get("kind")
        if kind == "local":
            return self.open_local()
        if kind == "ai":
            tool = next((t for t in self.config.ai_tools if t.id == entry.get("tool")), None)
            return self.open_ai(tool) if tool is not None else None
        if kind == "ssh" and "profile" in entry:
            profile = self.config.get(entry["profile"])
            return self.open_profile(profile) if profile is not None else None
        if kind == "ssh":
            host = str(entry.get("host") or "")
            user = str(entry.get("user") or "")
            if validate_host(host) or validate_user(user):
                return None
            try:
                port = int(entry.get("port") or 22)
            except (TypeError, ValueError):
                port = 22
            return self.open_profile(Profile(name=str(entry.get("name") or host), host=host,
                                             user=user, port=port))
        return None

    def save_session(self):
        try:
            session.save(session.encode(self.layout_.describe(), self._session_entry,
                                        self.active_panel))
        except OSError as e:
            print(f"bunny-deck: could not save the session: {e}")

    def restore_session(self):
        """Reopens the terminals of the last session in their arrangement.
        False if there was nothing to reopen."""
        data = session.load()
        if data is None:
            return False
        tree, panels = session.decode(data, self._panel_for_entry)
        if not panels:
            return False
        if tree is not None:
            self.layout_.arrange(tree)
            # A nested arrangement is custom; otherwise mark the preset in use.
            self.split_group.setExclusive(False)
            for b in (self.vsplit_btn, self.hsplit_btn):
                b.blockSignals(True)
                b.setChecked(not self.layout_.custom and (b is self.vsplit_btn) ==
                             (self.layout_.orientation == VERTICAL_SPLIT))
                b.blockSignals(False)
            self.split_group.setExclusive(True)
        active = data.get("active")
        target = panels[active] if isinstance(active, int) and 0 <= active < len(panels) \
            else panels[0]
        self._focus_panel(target)
        return True

    def _confirm(self, title, text, action):
        box = QMessageBox(QMessageBox.Icon.Question, title, title, parent=self)
        box.setInformativeText(text)
        box.addButton(QMessageBox.StandardButton.Cancel)
        ok = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        return box.clickedButton() is ok


@pyqtClassInfo("D-Bus Interface", APP_ID)
class _ActivateAdaptor(QDBusAbstractAdaptor):
    """Lets a second launch raise the already running window."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    @pyqtSlot()
    def Activate(self):
        win = self.owner.window
        if win is not None:
            if win.isMinimized():
                win.showMaximized()
            win.raise_()
            win.activateWindow()


class _DBusRoot(QObject):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.adaptor = _ActivateAdaptor(self)


def _single_instance(bus):
    """True if we are the first instance. Otherwise asks it to activate."""
    if bus.registerService(APP_ID):
        return True
    iface = QDBusInterface(APP_ID, DBUS_PATH, APP_ID, bus)
    if iface.isValid():
        iface.call("Activate")
    return False


def _dark_palette():
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#121418",
        QPalette.ColorRole.WindowText: "#c9ced6",
        QPalette.ColorRole.Base: "#0a0b0d",
        QPalette.ColorRole.AlternateBase: "#0d0f12",
        QPalette.ColorRole.Text: "#c9ced6",
        QPalette.ColorRole.Button: "#16191e",
        QPalette.ColorRole.ButtonText: "#c9ced6",
        QPalette.ColorRole.Highlight: "#2c4a80",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#121418",
        QPalette.ColorRole.ToolTipText: "#c9ced6",
        QPalette.ColorRole.PlaceholderText: "#5c6370",
        QPalette.ColorRole.Link: "#5b8def",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#4b5058"))
    return p


def _suggest_alias(profile, taken):
    """A one-word name for ~/.ssh/config from the connection's name."""
    base = re.sub(r"[^a-z0-9._-]+", "-", profile.name.lower()).strip("-.") or "server"
    alias, n = base, 2
    while alias in taken:
        alias, n = f"{base}-{n}", n + 1
    return alias


def create_application(argv):
    app = QApplication(argv)
    # X11 WM_CLASS class = application name; must equal StartupWMClass.
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    # Wayland app_id and KDE window <-> launcher association.
    app.setDesktopFileName(APP_ID)
    icon = QIcon.fromTheme(ICON_NAME)
    if icon.isNull():
        icon = QIcon(os.path.join(ROOT, "data", "bunny-deck.svg"))
    app.setWindowIcon(icon)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    with open(os.path.join(HERE, "style.qss"), encoding="utf-8") as f:
        app.setStyleSheet(f.read().replace("@HERE@", HERE))
    mainthread.init()
    app.aboutToQuit.connect(lambda: shutil.rmtree(colorschemes.scheme_dir(), True))
    app.aboutToQuit.connect(sshmux.shutdown)
    return app


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    app = create_application(argv)
    bus = QDBusConnection.sessionBus()
    if bus.isConnected() and not _single_instance(bus):
        return 0
    window = MainWindow(Config())
    if bus.isConnected():
        root = _DBusRoot(window)
        bus.registerObject(DBUS_PATH, root)
        app._dbus_root = root

    # Quit cleanly on SIGTERM/SIGINT (logout, kill): sessions are hung up and
    # SFTP connections / askpass socket are cleaned up.
    def on_signal(*_):
        window.shutdown()
        app.quit()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, on_signal)
    timer = QTimer()
    timer.start(250)  # lets Python run its signal handlers
    timer.timeout.connect(lambda: None)

    window.showMaximized()
    # Reopen the last session, or start with a local shell (only in the first
    # instance; a second launch just raises this window above).
    if not window.restore_session():
        window.open_local()
    code = app.exec()
    dispose_windows()
    return code


def dispose_windows():
    """Deletes the main windows while QApplication still exists. Otherwise
    Python may destroy them during interpreter shutdown, after QApplication,
    which can crash (seen as a segfault on exit)."""
    for w in QApplication.topLevelWidgets():
        if isinstance(w, MainWindow) and not sip.isdeleted(w):
            sip.delete(w)
