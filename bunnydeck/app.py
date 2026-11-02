"""Bunny Deck main application and window (Qt6 / QTermWidget)."""

import os
import re
import shlex
import shutil
import signal
import sys

from PyQt6.QtCore import QObject, QPoint, QRect, Qt, QTimer, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusInterface
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import colorschemes, keysetup, mainthread, session, shortcuts, sshconfig, sshmux
from .config import (
    KIND_LOCAL, Config, Profile, store_background, validate_host, validate_user,
)
from .dialogs import (
    AddAIToolDialog, AddSSHDialog, AppearanceDialog, CredentialDialog, ImportSSHConfigDialog,
    KeySetupDialog, ShortcutsDialog,
)
from .layout import ABOVE, BELOW, LEFT, RIGHT, HORIZONTAL_SPLIT, VERTICAL_SPLIT, SplitLayout
from .terminal import TerminalPanel, local_shell

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
        v.addWidget(self._build_toolbar())
        self.stack = QStackedWidget()
        # (QMainWindow already has a layout() method, hence "layout_".)
        self.layout_ = SplitLayout()
        self.empty = self._build_empty_state()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.layout_)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self._update_view()

    # ----- toolbar --------------------------------------------------------

    def _build_toolbar(self):
        bar = QWidget()
        bar.setObjectName("deckToolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)

        add = QPushButton("+ Add SSH")
        add.setToolTip("Open a new SSH connection")
        add.clicked.connect(self.add_ssh)
        h.addWidget(add)

        self.local_btn = QPushButton("Local terminal")
        self.local_btn.setToolTip("Open a shell on this computer")
        self.local_btn.clicked.connect(self.open_local)
        # May shrink (clipping its text) so the window stays as narrow as before.
        self.local_btn.setMinimumWidth(30)
        h.addWidget(self.local_btn)

        self.saved_button = QToolButton()
        self.saved_button.setText("Saved Connections ▾")
        self.saved_button.setToolTip("Open a saved connection")
        self.saved_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.saved_menu = QMenu(self.saved_button)
        self.saved_button.setMenu(self.saved_menu)
        # Like the Local terminal button, may shrink to keep the window narrow.
        self.saved_button.setMinimumWidth(30)
        h.addWidget(self.saved_button)

        self.ai_button = QToolButton()
        self.ai_button.setText("AI ▾")
        self.ai_button.setToolTip("Open an AI assistant (e.g. Claude) in a new terminal")
        self.ai_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.ai_menu = QMenu(self.ai_button)
        self.ai_button.setMenu(self.ai_menu)
        self.rebuild_ai_menu()
        h.addWidget(self.ai_button)

        self.broadcast_btn = QPushButton("⇉ Broadcast")
        self.broadcast_btn.setObjectName("broadcastBtn")
        self.broadcast_btn.setCheckable(True)
        self.broadcast_btn.setToolTip("Type into several terminals at once (Ctrl+Shift+B)")
        # May shrink (clipping its text) so the window stays narrow.
        self.broadcast_btn.setMinimumWidth(30)
        self.broadcast_btn.toggled.connect(self.set_broadcast_mode)
        h.addWidget(self.broadcast_btn)
        # Outside a terminal (e.g. in the file manager) these window-wide
        # shortcuts apply; inside one, the terminal's key filter handles them.
        for seq, action in shortcuts.sequences():
            if action not in ("copy", "paste"):
                QShortcut(seq, self, activated=lambda a=action: self.run_shortcut(a, None))

        h.addStretch(1)
        self.vsplit_btn = QPushButton("Vertical Split")
        self.vsplit_btn.setToolTip("Arrange terminals side by side")
        self.hsplit_btn = QPushButton("Horizontal Split")
        self.hsplit_btn.setToolTip("Stack terminals top to bottom")
        self.split_group = group = QButtonGroup(self)
        for b in (self.vsplit_btn, self.hsplit_btn):
            b.setCheckable(True)
            group.addButton(b)
            h.addWidget(b)
        self.vsplit_btn.setChecked(True)
        self.vsplit_btn.toggled.connect(lambda on: on and self._set_split(VERTICAL_SPLIT))
        self.hsplit_btn.toggled.connect(lambda on: on and self._set_split(HORIZONTAL_SPLIT))
        h.addStretch(1)

        self.files_btn = QPushButton("Files")
        self.files_btn.setCheckable(True)
        self.files_btn.setToolTip("Show the file manager (terminals keep running)")
        self.files_btn.toggled.connect(self._on_files_toggled)
        self.files_btn.hide()
        h.addWidget(self.files_btn)
        self.remove_button = QToolButton()
        self.remove_button.setText("Remove Connection")
        self.remove_button.setToolTip("Remove a saved connection")
        self.remove_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.remove_menu = QMenu(self.remove_button)
        self.remove_button.setMenu(self.remove_menu)
        h.addWidget(self.remove_button)
        self.rebuild_saved_menu()
        return bar

    def rebuild_saved_menu(self):
        self.saved_menu.clear()
        self.remove_menu.clear()
        if not self.config.profiles:
            for menu in (self.saved_menu, self.remove_menu):
                menu.addAction("No saved connections").setEnabled(False)
        for profile in sorted(self.config.profiles, key=lambda p: p.name.lower()):
            label = f"{profile.name}    {profile.target}"
            a = self.saved_menu.addAction(label)
            a.triggered.connect(lambda _=False, pid=profile.id: self._on_saved_activate(pid))
            a = self.remove_menu.addAction(label)
            a.triggered.connect(lambda _=False, pid=profile.id: self._on_saved_remove(pid))
        self.saved_menu.addSeparator()
        self.saved_menu.addAction("Import from ~/.ssh/config…").triggered.connect(
            self.import_ssh_config)
        keys = self.saved_menu.addMenu("Set up key login")
        ssh_profiles = [p for p in self.config.profiles if not p.is_local]
        keys.setEnabled(bool(ssh_profiles))
        for profile in sorted(ssh_profiles, key=lambda p: p.name.lower()):
            a = keys.addAction(f"{profile.name}    {profile.target}")
            a.triggered.connect(lambda _=False, pid=profile.id: self._on_key_setup(pid))

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

    def _build_empty_state(self):
        w = QWidget()
        w.setObjectName("emptyState")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(w)
        v.addStretch(1)
        title = QLabel("No open sessions")
        title.setObjectName("emptyTitle")
        hint = QLabel("Use “+ Add SSH”, “Local terminal” or “Saved Connections” to open "
                      "a terminal.")
        hint.setObjectName("dimLabel")
        for lbl in (title, hint):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(lbl)
        v.addStretch(1)
        return w

    # ----- sessions -------------------------------------------------------

    def add_ssh(self):
        dlg = AddSSHDialog(self)
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

    def open_profile(self, profile, argv=None):
        panel = TerminalPanel(profile, argv=argv)
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
        # In a custom arrangement, open next to the active terminal instead of
        # flattening the layout; with a preset, keep the uniform row/column.
        near = self.active_panel if self.layout_.custom else None
        self.layout_.add_panel(panel, near=near)
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
        elif action == "help":
            dlg = ShortcutsDialog(self, shortcuts.OVERVIEW)
            dlg.exec()
            dlg.deleteLater()
        elif action == "files":
            if self.current_view() == "files":
                self.show_terminals()
                if self.active_panel is not None:
                    self.active_panel.focus_terminal()
            elif panel is not None:
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
        self.broadcast_btn.setText(f"⇉ Broadcast: {n}" if self.broadcasting else "⇉ Broadcast")

    def close_panel(self, panel):
        panel.set_broadcast(False)
        panel.terminate()
        self.layout_.remove_panel(panel)
        panel.deleteLater()
        if self.active_panel is panel:
            self.active_panel = None
            if self.layout_.panels:
                self.layout_.panels[-1].focus_terminal()
        if self.file_manager is not None:
            self.file_manager.sessions_changed()
        self._update_view()

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

    def supports_alpha(self):
        app = QApplication.instance()
        if app.platformName() == "wayland":
            return True
        return bool(os.environ.get("DISPLAY")) and self.testAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground)

    # ----- file manager ---------------------------------------------------

    def open_file_manager(self, panel):
        from .filemanager import FileManager

        if self.file_manager is None:
            self.file_manager = FileManager(self)
            self.stack.addWidget(self.file_manager)
            self.files_btn.show()
        self.file_manager.open_for(panel.profile)
        self.files_btn.setChecked(True)
        self._update_view()

    def _on_files_toggled(self, on):
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

    def move_panel(self, panel, ref, side):
        """Moves a running terminal next to another; switches to Custom Layout."""
        if not self.layout_.move_panel(panel, ref, side):
            return
        # Neither preset applies any more; clicking one re-applies it.
        self.split_group.setExclusive(False)
        for b in (self.vsplit_btn, self.hsplit_btn):
            b.setChecked(False)
        self.split_group.setExclusive(True)
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
        self.shutdown()
        event.accept()

    def shutdown(self):
        self.save_session()
        for p in list(self.layout_.panels):
            p.terminate()
        if self.file_manager is not None:
            self.file_manager.shutdown()

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
        app.setStyleSheet(f.read())
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
    return app.exec()
