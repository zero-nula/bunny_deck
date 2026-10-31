"""Qt dialogs: Add SSH, Add AI tool, terminal appearance, and the SSH credential prompt."""

import os
import shlex

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout,
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
