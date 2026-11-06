"""Editing files in a local editor, for the whole window: the file manager
and the Docker panel's compose files use the same service.

It holds the SFTP connections (connections.ConnectionPool), the remote edits
(remoteedit.RemoteEdits: download, watch, safe upload, conflicts, copies kept
on quit), starting the chosen editor (config.editor, editors.resolve) and the
editor menus (Default Editor, Edit With ▸). RemoteEdits talks to it as its
UI: ask(), notify(), launch().

notify() goes to whoever shows messages right now (the file manager's status
bar, else a toast in the main window); errors are shown as a message box.
"""

import os

from PyQt6.QtCore import QObject, QProcess, pyqtSignal
from PyQt6.QtGui import QActionGroup, QIcon
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from . import editors
from .connections import ConnectionPool
from .dialogs import CredentialDialog
from .mainthread import call_soon
from .remoteedit import RemoteEdits


class Editing(QObject):
    """notified(text, error): a message for the user. saved(session): a
    remote file was uploaded after a save in the editor."""

    notified = pyqtSignal(str, bool)
    saved = pyqtSignal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pool = ConnectionPool(call_soon, self.ask_credentials)
        self.edits = RemoteEdits(self.pool, self)
        self.edits.saved.connect(self.saved)
        # Set by the window: show a message where the user looks now; error
        # messages also go to show_error(title, text).
        self.show_message = lambda text, error: None
        self.show_error = self._message_box

    # ----- RemoteEdits' UI ---------------------------------------------------

    def ask(self, title, text, informative, yes_label):
        """A question with a safe default (Cancel); True for yes_label."""
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self.window)
        box.setInformativeText(informative)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        yes = box.addButton(yes_label, QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is yes

    def notify(self, text, error=False):
        self.notified.emit(text, error)
        self.show_message(text, error)
        if error:
            self.show_error("Editing a file", text)

    def launch(self, path, editor=None):
        """Starts the editor on a local file; None, or an error message."""
        editor = editor or editors.resolve(self.window.config.editor)
        if editor is None:
            return ("No text editor was found. Choose one with Default Editor ▸ "
                    "Custom Command… (right-click a file in Files).")
        try:
            argv = editors.command_for(editor, path)
        except ValueError as e:
            return f"The command of {editor.name} cannot be read: {e}"
        ok, _pid = QProcess.startDetached(argv[0], argv[1:], os.path.dirname(path))
        return None if ok else f"{editor.name} could not be started ({argv[0]})."

    def ask_credentials(self, profile, prompt, kind, retry):
        if kind == "confirm":
            dlg = CredentialDialog(self.window, f"Verify host — {profile.name}", prompt,
                                   confirm=True)
        else:
            dlg = CredentialDialog(self.window, f"SFTP login — {profile.name}", prompt,
                                   secret=True, retry=retry)
        try:
            return dlg.ask()
        finally:
            dlg.deleteLater()

    def _message_box(self, title, text):
        box = QMessageBox(QMessageBox.Icon.Critical, title, title, parent=self.window)
        box.setInformativeText(text)
        box.exec()

    # ----- opening ------------------------------------------------------------

    def open_remote(self, profile, path, editor=None):
        """Opens a server's file in the editor over the profile's SFTP
        connection (remoteedit.py does the rest)."""

        def connected(client, error):
            if error:
                if error != "cancelled":
                    self.notify(f"Could not open {path} from {profile.name}:\n{error}",
                                error=True)
                return
            self.edits.open(profile, client, path, editor)

        self.pool.get(profile, connected)

    # ----- choosing the editor ----------------------------------------------------

    def fill_default_editor_menu(self, menu, found=None):
        """Bunny Deck's own editor choice; the desktop's settings are not
        changed."""
        found = editors.find_editors() if found is None else found
        current = self.window.config.editor
        group = QActionGroup(menu)

        def option(text, choice, tip=None):
            a = menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(choice == current)
            if tip:
                a.setToolTip(tip)
            group.addAction(a)
            a.triggered.connect(lambda: self.set_default_editor(choice))
            return a

        default = editors.system_default(found)
        option(f"System Default ({default.name})" if default else "System Default",
               editors.SYSTEM)
        for e in found:
            option(e.name, e.choice).setIcon(QIcon.fromTheme(e.icon))
        menu.addSeparator()
        custom = current[len(editors.COMMAND_PREFIX):] \
            if current.startswith(editors.COMMAND_PREFIX) else ""
        a = menu.addAction(f"Custom Command: {custom}…" if custom else "Custom Command…")
        a.setCheckable(True)
        a.setChecked(bool(custom))
        group.addAction(a)
        a.triggered.connect(self.ask_custom_editor)
        menu.setToolTipsVisible(True)

    def fill_edit_with_menu(self, menu, on_editor, found=None, other_text="Other Command…"):
        """The installed editors, then a command typed in; on_editor(editor)
        is called with the one picked."""
        found = editors.find_editors() if found is None else found
        for e in found:
            menu.addAction(QIcon.fromTheme(e.icon), e.name).triggered.connect(
                lambda _=False, e=e: on_editor(e))
        if found:
            menu.addSeparator()
        menu.addAction(other_text).triggered.connect(lambda: self._edit_with_command(on_editor))

    def _edit_with_command(self, on_editor):
        cmd = self.ask_command("Edit With")
        if cmd is not None:
            on_editor(editors.resolve(editors.COMMAND_PREFIX + cmd))

    def set_default_editor(self, choice):
        """Stores the choice; returns a line for a status bar."""
        self.window.config.set_editor(choice)
        e = editors.resolve(choice)
        text = f"Files open in {e.name}" if e else "No text editor found"
        self.show_message(text, False)
        return text

    def ask_command(self, title):
        """A command line for an editor, checked; None when cancelled."""
        current = self.window.config.editor
        text = current[len(editors.COMMAND_PREFIX):] \
            if current.startswith(editors.COMMAND_PREFIX) else ""
        while True:
            text, ok = QInputDialog.getText(
                self.window, title, "Command that opens a file, for example “kate” or "
                "“konsole -e nvim”.\nThe file is added at the end, or where you put %f.",
                text=text)
            if not ok:
                return None
            error = editors.check_command(text.strip())
            if error is None:
                return text.strip()
            self.show_error("Cannot use this command", error)

    def ask_custom_editor(self):
        cmd = self.ask_command("Custom Editor")
        if cmd is not None:
            self.set_default_editor(editors.COMMAND_PREFIX + cmd)

    # ----- quitting -------------------------------------------------------------

    def unsaved(self):
        """Names of files being edited whose last changes are not on their server."""
        return [s.name for s in self.edits.sessions if s.unsaved or s.busy]

    def shutdown(self):
        for path in self.edits.shutdown():
            print(f"bunny-deck: kept {path}: its last changes are not on the server")
        self.pool.close_all()
