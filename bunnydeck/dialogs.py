"""Qt dialogs: Add SSH, Add AI tool, import from ~/.ssh/config, key login
setup, terminal appearance, and the SSH credential prompt."""

import os
import shlex

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSlider, QSpinBox, QVBoxLayout,
)

from .config import (
    AITool, Appearance, IMAGE_MODES, Profile, validate_host, validate_user,
)

IMAGE_MODE_LABELS = {"fill": "Fill (crop to fit)", "fit": "Fit (whole image)",
                     "stretch": "Stretch", "center": "Center (original size)"}
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg)"

SWATCHES = ("#0e1013", "#0d1b2e", "#1a1030", "#0f2318", "#2a1212",
            "#1f1a0e", "#10262a", "#1c1c1c", "#22152a")


def _hint(text):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setObjectName("dimLabel")
    return lbl


class AddSSHDialog(QDialog):
    """Collects a connection profile. Never asks for a password: OpenSSH
    prompts interactively inside the terminal when needed."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add SSH Connection")
        self.setMinimumWidth(440)
        self.profile = None
        form = QFormLayout()
        self.name = QLineEdit(placeholderText="e.g. Raspberry Pi")
        self.host = QLineEdit(placeholderText="hostname, IP, or ~/.ssh/config alias")
        self.user = QLineEdit(placeholderText="optional — default from ~/.ssh/config")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        self.save = QCheckBox("Save to Saved Connections")
        self.save.setChecked(True)
        form.addRow("Name", self.name)
        form.addRow("Host", self.host)
        form.addRow("Username", self.user)
        form.addRow("Port", self.port)
        form.addRow("", self.save)

        self.error = QLabel()
        self.error.setObjectName("errorLabel")
        self.error.setWordWrap(True)
        self.error.hide()
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        connect = buttons.addButton("Connect", QDialogButtonBox.ButtonRole.AcceptRole)
        connect.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addWidget(_hint("Authentication uses your SSH keys, ssh-agent and ~/.ssh/config. "
                          "If a password is required, OpenSSH asks for it in the terminal. "
                          "Passwords are never stored."))
        v.addWidget(self.error)
        v.addWidget(buttons)

    def accept(self):
        host = self.host.text().strip()
        user = self.user.text().strip()
        err = validate_host(host) or validate_user(user)
        if err:
            self.error.setText(err)
            self.error.show()
            return
        name = self.name.text().strip() or (f"{user}@{host}" if user else host)
        self.profile = Profile(name=name, host=host, user=user, port=self.port.value())
        super().accept()

    def run_for_profile(self):
        """Returns (Profile, save_flag) or (None, False)."""
        if self.exec() != QDialog.DialogCode.Accepted:
            return None, False
        return self.profile, self.save.isChecked()


class AddAIToolDialog(QDialog):
    """Adds an AI assistant to the AI menu: a name and the command to run."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add AI Tool")
        self.setMinimumWidth(440)
        self.tool = None
        form = QFormLayout()
        self.command = QLineEdit(placeholderText="e.g. claude, opencode, codex --full-auto")
        self.name = QLineEdit(placeholderText="optional — default from the command")
        form.addRow("Command", self.command)
        form.addRow("Name", self.name)

        self.error = QLabel()
        self.error.setObjectName("errorLabel")
        self.error.setWordWrap(True)
        self.error.hide()
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        add = buttons.addButton("Add", QDialogButtonBox.ButtonRole.AcceptRole)
        add.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addWidget(_hint("The command runs in a new local terminal, in your shell with "
                          "~/.bashrc loaded. When it exits, the shell stays open."))
        v.addWidget(self.error)
        v.addWidget(buttons)

    def accept(self):
        command = self.command.text().strip()
        if not command:
            self.error.setText("Command is required.")
            self.error.show()
            return
        name = self.name.text().strip()
        if not name:
            try:
                name = os.path.basename(shlex.split(command)[0]).capitalize()
            except (ValueError, IndexError):
                name = command
        self.tool = AITool(name=name, command=command)
        super().accept()


class AppearanceDialog(QDialog):
    """Per-profile terminal background: colour, opacity and an optional
    image, previewed live."""

    RESET = 2

    def __init__(self, parent, profile, appearance, on_preview, saveable=True, default=None):
        super().__init__(parent)
        self.setWindowTitle(f"Background — {profile.name}")
        self.on_preview = on_preview
        self.original = appearance
        # What "Reset to Default" restores (local terminals have the bunny).
        self.default = default or Appearance()
        self.image = appearance.image

        self.chooser = QColorDialog(QColor(appearance.background), self)
        self.chooser.setOptions(QColorDialog.ColorDialogOption.NoButtons |
                                QColorDialog.ColorDialogOption.DontUseNativeDialog)
        self.chooser.setWindowFlags(Qt.WindowType.Widget)
        self.chooser.currentColorChanged.connect(self._changed)

        swatches = QHBoxLayout()
        swatches.addWidget(QLabel("Presets"))
        for c in SWATCHES:
            b = QPushButton()
            b.setFixedSize(26, 20)
            b.setToolTip(c)
            b.setStyleSheet(f"background-color:{c}; border:1px solid #3a3f47;")
            b.clicked.connect(lambda _=False, c=c: self.chooser.setCurrentColor(QColor(c)))
            swatches.addWidget(b)
        swatches.addStretch(1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Background opacity"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(round(appearance.opacity * 100))
        self.opacity_label = QLabel()
        self.opacity.valueChanged.connect(self._changed)
        row.addWidget(self.opacity, 1)
        row.addWidget(self.opacity_label)

        image_row = QHBoxLayout()
        image_row.addWidget(QLabel("Image"))
        self.image_label = QLabel()
        self.image_label.setObjectName("dimLabel")
        image_row.addWidget(self.image_label, 1)
        self.image_mode = QComboBox()
        for mode in IMAGE_MODES:
            self.image_mode.addItem(IMAGE_MODE_LABELS[mode], mode)
        self.image_mode.setCurrentIndex(IMAGE_MODES.index(appearance.image_mode))
        self.image_mode.currentIndexChanged.connect(self._changed)
        image_row.addWidget(self.image_mode)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_image)
        image_row.addWidget(choose)
        self.remove_image_btn = QPushButton("Remove")
        self.remove_image_btn.clicked.connect(lambda: self.set_image(""))
        image_row.addWidget(self.remove_image_btn)

        self.note = _hint("")
        self.note.setVisible(not saveable)
        if not saveable:
            self.note.setText("This connection is not saved, so the change only lasts for "
                              "this session.")

        buttons = QDialogButtonBox()
        reset = buttons.addButton("Reset to Default", QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self.reset)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton("Save" if saveable else "Apply",
                               QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        v = QVBoxLayout(self)
        v.addWidget(self.chooser)
        v.addLayout(swatches)
        v.addLayout(row)
        v.addLayout(image_row)
        v.addWidget(self.note)
        v.addWidget(buttons)
        self._update_label()

    def set_note(self, text):
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def current(self):
        return Appearance(background=self.chooser.currentColor().name().lower(),
                          opacity=round(self.opacity.value() / 100.0, 2),
                          image=self.image, image_mode=self.image_mode.currentData())

    def choose_image(self):
        start = os.path.dirname(self.image) if self.image else \
            os.path.expanduser("~/Pictures")
        path, _ = QFileDialog.getOpenFileName(self, "Background Image", start, IMAGE_FILTER)
        if path:
            self.set_image(path)

    def set_image(self, path):
        self.image = path
        self._changed()

    def _update_label(self):
        self.opacity_label.setText(f"{self.opacity.value()} %")
        self.image_label.setText(os.path.basename(self.image) if self.image else "None")
        self.image_label.setToolTip(self.image)
        self.image_mode.setEnabled(bool(self.image))
        self.remove_image_btn.setEnabled(bool(self.image))

    def _changed(self, *_):
        self._update_label()
        self.on_preview(self.current())

    def reset(self):
        d = self.default
        self.chooser.setCurrentColor(QColor(d.background))
        self.opacity.setValue(round(d.opacity * 100))
        self.image = d.image
        self.image_mode.setCurrentIndex(IMAGE_MODES.index(d.image_mode))
        self._changed()

    def run_for_appearance(self):
        """Returns the chosen Appearance, or None if cancelled (preview reverted)."""
        if self.exec() == QDialog.DialogCode.Accepted:
            return self.current()
        self.on_preview(self.original)
        return None


class CredentialDialog(QDialog):
    """Shows an OpenSSH prompt (password, passphrase, keyboard-interactive or
    host-key confirmation) for SFTP connections."""

    def __init__(self, parent, title, prompt, secret=True, retry=False, confirm=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.confirm = confirm
        v = QVBoxLayout(self)
        if retry:
            warn = QLabel("Authentication failed. Please try again.")
            warn.setObjectName("errorLabel")
            v.addWidget(warn)
        msg = QLabel(prompt.strip())
        msg.setWordWrap(True)
        msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(msg)
        self.message = msg

        self.entry = None
        buttons = QDialogButtonBox()
        if confirm:
            buttons.addButton("Reject", QDialogButtonBox.ButtonRole.RejectRole)
            ok = buttons.addButton("Accept host key", QDialogButtonBox.ButtonRole.AcceptRole)
        else:
            self.entry = QLineEdit()
            if secret:
                self.entry.setEchoMode(QLineEdit.EchoMode.Password)
            v.addWidget(self.entry)
            buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
            ok = buttons.addButton("OK", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def text(self):
        return self.message.text()

    def ask(self):
        """Returns the answer string, or None when cancelled."""
        if self.exec() != QDialog.DialogCode.Accepted:
            return None
        if self.confirm:
            return "yes"
        answer = self.entry.text()
        self.entry.clear()
        return answer


class ImportSSHConfigDialog(QDialog):
    """Picks hosts from ~/.ssh/config to add to Saved Connections.
    entries: [(alias, {"hostname", "user", "port"} or None)]."""

    def __init__(self, parent, entries, saved_hosts):
        super().__init__(parent)
        self.setWindowTitle("Import from ~/.ssh/config")
        self.setMinimumWidth(480)
        self.list = QListWidget()
        for alias, info in entries:
            detail = ""
            if info:
                detail = info["hostname"]
                if info.get("user"):
                    detail = f"{info['user']}@{detail}"
                if info.get("port", 22) != 22:
                    detail += f":{info['port']}"
            item = QListWidgetItem(f"{alias}    {detail}".rstrip())
            item.setData(Qt.ItemDataRole.UserRole, alias)
            if alias in saved_hosts:
                item.setText(item.text() + "    (already saved)")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)

        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.import_btn = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.list.itemChanged.connect(self._update)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("Choose the hosts to add to Saved Connections:"))
        v.addWidget(self.list, 1)
        v.addWidget(_hint("Each connection uses the host's name from ~/.ssh/config, so its "
                          "settings there (user, port, keys, jump hosts) keep applying."))
        v.addWidget(buttons)
        self._update()

    def selected(self):
        items = (self.list.item(i) for i in range(self.list.count()))
        return [it.data(Qt.ItemDataRole.UserRole) for it in items
                if it.checkState() == Qt.CheckState.Checked]

    def _update(self, *_):
        self.import_btn.setEnabled(bool(self.selected()))


class KeySetupDialog(QDialog):
    """Sets up SSH key login for a connection: create the key if needed,
    authorize it on the server, verify it, and optionally add a Host entry
    to ~/.ssh/config. run_step(name, args, done) runs one KeySetup step off
    the UI thread and calls done(message, error) on it."""

    STEPS = [("key", "Key"), ("install", "Add the key to the server"),
             ("verify", "Log in with the key"), ("config", "Add to ~/.ssh/config")]

    def __init__(self, parent, profile, key_path, existing_fingerprint, config_alias,
                 run_step, add_config):
        super().__init__(parent)
        self.setWindowTitle(f"Set up key login — {profile.name}")
        self.setMinimumWidth(560)
        self.profile = profile
        self.run_step = run_step
        self.add_config = add_config
        self.running = False
        self.succeeded = False

        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Log in to <b>{profile.target}</b> with an SSH key instead of a "
                           "password."))
        home = os.path.expanduser("~")
        shown = "~" + key_path[len(home):] if key_path.startswith(home + os.sep) else key_path

        form = QFormLayout()
        self.passphrase = self.passphrase2 = None
        if existing_fingerprint is not None:
            form.addRow("Key", QLabel(f"{shown} (exists, will be used)\n{existing_fingerprint}"))
        else:
            form.addRow("Key", QLabel(f"A new key will be created: {shown}"))
            self.passphrase = QLineEdit(echoMode=QLineEdit.EchoMode.Password,
                                        placeholderText="recommended")
            self.passphrase2 = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
            form.addRow("Passphrase", self.passphrase)
            form.addRow("Repeat", self.passphrase2)
        v.addLayout(form)
        if self.passphrase is not None:
            v.addWidget(_hint("The passphrase protects the key file if someone copies it. "
                              "Leave it empty for a key without one."))

        self.config_box = QCheckBox("Also add a Host entry to ~/.ssh/config")
        self.alias = QLineEdit(config_alias or "")
        row = QHBoxLayout()
        row.addWidget(self.config_box)
        row.addWidget(self.alias, 1)
        if config_alias is None:
            self.config_box.setChecked(False)
            self.config_box.setEnabled(False)
            self.alias.hide()
            self.config_box.setText(f"“{profile.host}” is already in ~/.ssh/config")
        else:
            self.config_box.setChecked(True)
            self.config_box.toggled.connect(self.alias.setEnabled)
        v.addLayout(row)
        if config_alias is not None:
            v.addWidget(_hint("Then “ssh <name>” also works in any terminal, with this key."))

        steps = QGridLayout()
        self.marks = {}
        self.texts = {}
        for i, (key, title) in enumerate(self.STEPS):
            mark = QLabel("•")
            mark.setFixedWidth(18)
            text = QLabel(title)
            text.setWordWrap(True)
            steps.addWidget(mark, i, 0, Qt.AlignmentFlag.AlignTop)
            steps.addWidget(text, i, 1)
            self.marks[key], self.texts[key] = mark, text
        v.addLayout(steps)

        self.error = QLabel()
        self.error.setObjectName("errorLabel")
        self.error.setWordWrap(True)
        self.error.hide()
        v.addWidget(self.error)
        self.result = _hint("")
        self.result.hide()
        v.addWidget(self.result)

        buttons = QDialogButtonBox()
        self.close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.start_btn = buttons.addButton("Set up", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self.start)
        self.close_btn.clicked.connect(self.reject)
        v.addWidget(buttons)

    def _mark(self, key, mark, text=None):
        self.marks[key].setText(mark)
        title = dict(self.STEPS)[key]
        self.texts[key].setText(f"{title}: {text}" if text else title)
        self.adjustSize()  # longer, wrapped step texts need more height

    def _fail(self, key, message):
        self._mark(key, "✗")
        self.error.setText(message)
        self.error.show()
        self.running = False
        self.start_btn.setText("Try again")
        self.start_btn.setEnabled(True)
        self.close_btn.setEnabled(True)

    def start(self):
        if self.running:
            return
        passphrase = ""
        if self.passphrase is not None:
            passphrase = self.passphrase.text()
            if passphrase != self.passphrase2.text():
                self.error.setText("The passphrases do not match.")
                self.error.show()
                return
        if self.config_box.isChecked():
            from .sshconfig import valid_alias
            if not valid_alias(self.alias.text().strip()):
                self.error.setText("The name for ~/.ssh/config must be one word without "
                                   "spaces, * or ?.")
                self.error.show()
                return
        self.error.hide()
        self.running = True
        self.start_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        for key, _ in self.STEPS:
            self._mark(key, "•")
        self._step("key", passphrase)

    def _step(self, key, *args):
        self._mark(key, "…")
        order = [k for k, _ in self.STEPS]

        def done(message, error):
            if error is not None:
                self._fail(key, error)
                return
            self._mark(key, "✓", message)
            nxt = order[order.index(key) + 1]
            if nxt == "config":
                self._finish_config()
            else:
                self._step(nxt)

        self.run_step(key, args, done)

    def _finish_config(self):
        if self.config_box.isChecked():
            try:
                message = self.add_config(self.alias.text().strip())
            except (OSError, ValueError) as e:
                self._fail("config", str(e))
                return
            self._mark("config", "✓", message)
        else:
            self._mark("config", "–", "skipped")
        self.running = False
        self.succeeded = True
        self.result.setText(f"Done: {self.profile.name} now logs in with your key. The server "
                            "still accepts passwords too; you can turn them off in its sshd "
                            "settings once key login works from all your devices.")
        self.result.show()
        self.start_btn.hide()
        self.close_btn.setText("Close")
        self.close_btn.setEnabled(True)
        self.adjustSize()

    def reject(self):
        if not self.running:
            super().reject()


class ShortcutsDialog(QDialog):
    """Overview of the keyboard shortcuts (shortcuts.OVERVIEW)."""

    def __init__(self, parent, overview):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        row = 0
        for group, entries in overview:
            title = QLabel(f"<b>{group}</b>")
            grid.addWidget(title, row, 0, 1, 2)
            row += 1
            for keys, text in entries:
                k = QLabel(keys)
                k.setObjectName("shortcutKeys")
                grid.addWidget(k, row, 0)
                grid.addWidget(QLabel(text), row, 1)
                row += 1
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addLayout(grid)
        v.addWidget(buttons)

