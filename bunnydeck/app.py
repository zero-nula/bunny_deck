"""Bunny Deck main application and window (Qt6 / QTermWidget)."""

import os
import shlex
import shutil
import signal
import sys

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusInterface
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import colorschemes, mainthread, sshmux
from .config import KIND_LOCAL, Config, Profile, default_local_appearance, store_background
from .dialogs import AddAIToolDialog, AddSSHDialog, AppearanceDialog
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

    def close_panel(self, panel):
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

        default = default_local_appearance() if profile is self.local_profile else None
        dlg = AppearanceDialog(self, profile, panel.appearance, preview, saveable=saved,
                               default=default)
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
        for p in list(self.layout_.panels):
            p.terminate()
        if self.file_manager is not None:
            self.file_manager.shutdown()

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
    # Start with a local shell (only in the first instance; a second launch
    # just raises this window above).
    window.open_local()
    return app.exec()
