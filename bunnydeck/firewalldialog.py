"""The firewall window of one SSH server: allow a port from the LAN, an
address or anywhere, and see or delete the ufw rules.

The window never runs sudo itself. runRequested(command) asks the main
window to type the command into the server's Firewall terminal, where the
user enters the sudo password. The rules come back from that terminal's
output (firewall.StatusReader) through show_status().
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import firewall, icons
from .dialogs import _section

PROTOCOL_CHOICES = (("TCP", "tcp"), ("UDP", "udp"), ("TCP and UDP", "both"))
SOURCE_LAN, SOURCE_IP, SOURCE_ANY = "lan", "ip", "any"


class FirewallDialog(QDialog):
    """runRequested(command): a command (with the status after it) for the
    Firewall terminal."""

    runRequested = pyqtSignal(str)

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.info = None
        self.status = None
        self.setObjectName("firewallDialog")
        self.setWindowTitle(f"Firewall — {profile.name}")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(640, 640)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(8)
        head = QHBoxLayout()
        shield = QLabel()
        shield.setPixmap(icons.pixmap("shield", "#5b8def", 22, 2.0))
        title = QLabel(f"Firewall · {profile.name}")
        title.setObjectName("dialogTitle")
        target = QLabel(profile.target)
        target.setObjectName("dimLabel")
        head.addWidget(shield)
        head.addWidget(title)
        head.addWidget(target)
        head.addStretch(1)
        v.addLayout(head)

        self.banner = QLabel("Checking the server…")
        self.banner.setObjectName("fwBanner")
        self.banner.setWordWrap(True)
        self._set_banner("Checking the server…", "info")
        v.addWidget(self.banner)

        v.addSpacing(4)
        v.addWidget(_section("Allow a port"))
        v.addWidget(self._build_form())

        v.addSpacing(6)
        rules_head = QHBoxLayout()
        rules_head.addWidget(_section("Rules"))
        rules_head.addStretch(1)
        self.refresh_btn = QPushButton(icons.icon("refresh"), "Refresh")
        self.refresh_btn.setAutoDefault(False)
        self.refresh_btn.setToolTip("Run sudo ufw status numbered in the Firewall terminal")
        self.refresh_btn.clicked.connect(self.refresh)
        self.delete_btn = QPushButton(icons.icon("trash"), "Delete")
        self.delete_btn.setAutoDefault(False)
        self.delete_btn.setEnabled(False)
        self.delete_btn.setToolTip("Delete the selected rule; ufw shows it and asks (y|n) in "
                                   "the Firewall terminal")
        self.delete_btn.clicked.connect(self.delete_selected)
        rules_head.addWidget(self.refresh_btn)
        rules_head.addWidget(self.delete_btn)
        v.addLayout(rules_head)
        self.rules = QTreeWidget()
        self.rules.setObjectName("fwRules")
        self.rules.setRootIsDecorated(False)
        self.rules.setHeaderLabels(["#", "To", "Action", "From", "Comment"])
        self.rules.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.rules.header().setStretchLastSection(True)
        self.rules.setColumnWidth(1, 140)
        self.rules.setColumnWidth(2, 90)
        self.rules.setColumnWidth(3, 160)
        self.rules.itemSelectionChanged.connect(
            lambda: self.delete_btn.setEnabled(bool(self.rules.selectedItems())))
        v.addWidget(self.rules, 1)
        self.rules_note = QLabel("Enter your sudo password in the Firewall terminal below the "
                                 "server to see the rules.")
        self.rules_note.setObjectName("dimLabel")
        self.rules_note.setWordWrap(True)
        v.addWidget(self.rules_note)
        self.update_preview()

    # ----- the form -------------------------------------------------------

    def _build_form(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        g.setColumnStretch(1, 1)

        self.port = QLineEdit()
        self.port.setPlaceholderText("8080 or 8000:8010")
        self.port.textChanged.connect(self.update_preview)
        self.protocol = QComboBox()
        for label, value in PROTOCOL_CHOICES:
            self.protocol.addItem(label, value)
        self.protocol.currentIndexChanged.connect(self.update_preview)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port, 1)
        port_row.addWidget(self.protocol)
        g.addWidget(QLabel("Port"), 0, 0)
        g.addLayout(port_row, 0, 1)

        self.source_group = QButtonGroup(self)
        self.src_lan = QRadioButton("LAN")
        self.src_ip = QRadioButton("IP or network")
        self.src_any = QRadioButton("Anywhere")
        for b, key in ((self.src_lan, SOURCE_LAN), (self.src_ip, SOURCE_IP),
                       (self.src_any, SOURCE_ANY)):
            b.setProperty("source", key)
            self.source_group.addButton(b)
        self.src_lan.setChecked(True)
        self.ip = QLineEdit()
        self.ip.setPlaceholderText("192.168.1.20 or 10.0.0.0/24")
        self.ip.textChanged.connect(lambda: self.src_ip.setChecked(True) if self.ip.text()
                                    else None)
        self.ip.textChanged.connect(self.update_preview)
        src = QVBoxLayout()
        src.setSpacing(4)
        lan_row = QHBoxLayout()
        lan_row.addWidget(self.src_lan)
        self.lan_label = QLabel("(finding the network…)")
        self.lan_label.setObjectName("dimLabel")
        lan_row.addWidget(self.lan_label, 1)
        ip_row = QHBoxLayout()
        ip_row.addWidget(self.src_ip)
        ip_row.addWidget(self.ip, 1)
        src.addLayout(lan_row)
        src.addLayout(ip_row)
        src.addWidget(self.src_any)
        self.any_warning = QLabel("⚠  Anyone who can reach this server can connect to this port, "
                                  "also from the internet if the server is reachable from there.")
        self.any_warning.setObjectName("fwWarning")
        self.any_warning.setWordWrap(True)
        src.addWidget(self.any_warning)
        g.addWidget(QLabel("From"), 1, 0, Qt.AlignmentFlag.AlignTop)
        g.addLayout(src, 1, 1)

        self.comment = QLineEdit()
        self.comment.setPlaceholderText("optional, e.g. Open WebUI")
        self.comment.setMaxLength(firewall.COMMENT_MAX)
        self.comment.textChanged.connect(self.update_preview)
        g.addWidget(QLabel("Comment"), 2, 0)
        g.addWidget(self.comment, 2, 1)

        self.preview = QLabel()
        self.preview.setObjectName("fwPreview")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error = QLabel()
        self.error.setObjectName("errorLabel")
        self.error.setWordWrap(True)
        self.allow_btn = QPushButton("Allow")
        self.allow_btn.setObjectName("primaryBtn")
        self.allow_btn.setAutoDefault(False)
        self.allow_btn.setToolTip("Type this command into the Firewall terminal (sudo asks "
                                  "for your password there)")
        self.allow_btn.clicked.connect(self.allow)
        g.addWidget(self.preview, 3, 0, 1, 2)
        bottom = QHBoxLayout()
        bottom.addWidget(self.error, 1)
        bottom.addWidget(self.allow_btn)
        g.addLayout(bottom, 4, 0, 1, 2)
        # Connected last: the preview needs every field to exist.
        for b in (self.src_lan, self.src_ip, self.src_any):
            b.toggled.connect(self.update_preview)
        return w

    def source_kind(self):
        b = self.source_group.checkedButton()
        return b.property("source") if b is not None else SOURCE_LAN

    def commands(self):
        """The commands for the form, or raises ValueError with the problem."""
        kind = self.source_kind()
        if kind == SOURCE_LAN:
            if not (self.info and self.info.lan):
                raise ValueError("The server's network is not known; choose an IP or network.")
            source = self.info.lan
        elif kind == SOURCE_IP:
            source = self.ip.text()
        else:
            source = None
        return firewall.allow_commands(self.port.text(), self.protocol.currentData(), source,
                                       self.comment.text())

    def update_preview(self, *_):
        self.any_warning.setVisible(self.source_kind() == SOURCE_ANY)
        try:
            cmds = self.commands()
        except ValueError as e:
            self.preview.setText("")
            self.preview.hide()
            # Say nothing about an empty port until something is typed.
            self.error.setText(str(e) if self.port.text().strip() or self.ip.text().strip()
                               else "")
            self.allow_btn.setEnabled(False)
            return
        self.error.setText("")
        self.preview.setText("\n".join(cmds))
        self.preview.show()
        self.allow_btn.setEnabled(self.info is None or self.info.installed)

    def set_port(self, port):
        if port is not None:
            self.port.setText(str(port))

    # ----- actions --------------------------------------------------------

    def allow(self):
        try:
            cmds = self.commands()
        except ValueError:
            return
        self.runRequested.emit(firewall.with_status("; ".join(cmds)))
        self.rules_note.setText("Sent to the Firewall terminal. If sudo asks, enter your "
                                "password there.")

    def refresh(self):
        self.runRequested.emit(firewall.STATUS_COMMAND)

    def delete_selected(self):
        items = self.rules.selectedItems()
        if not items:
            return
        number = int(items[0].text(0))
        self.runRequested.emit(firewall.with_status(firewall.delete_command(number)))
        self.rules_note.setText(f"Rule {number}: confirm with y in the Firewall terminal "
                                "(ufw shows the rule it will delete).")

    # ----- what the server says -------------------------------------------

    def _set_banner(self, text, level):
        self.banner.setText(text)
        self.banner.setProperty("level", level)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)

    def show_info(self, info):
        self.info = info
        if info.lan:
            self.lan_label.setText(info.lan)
        else:
            self.lan_label.setText("(not found)")
            if self.source_kind() == SOURCE_LAN:
                self.src_ip.setChecked(True)
        self.src_lan.setEnabled(bool(info.lan))
        self._update_banner()
        self.update_preview()

    def show_unreachable(self):
        self._set_banner("Not connected to the server. Open its terminal first; the window "
                         "uses that connection.", "bad")

    def show_status(self, status):
        self.status = status
        self.rules.clear()
        if status.error:
            self.rules_note.setText(status.error)
        else:
            for r in status.rules:
                item = QTreeWidgetItem([str(r.number), r.to, r.action, r.source, r.comment])
                item.setToolTip(4, r.comment)
                self.rules.addTopLevelItem(item)
            self.rules_note.setText("" if status.rules else "No rules yet.")
        self._update_banner()

    def _update_banner(self):
        info, status = self.info, self.status
        if info is not None and not info.installed:
            self._set_banner("ufw is not installed on this server. Install it with "
                             "sudo apt install ufw (Debian/Ubuntu).", "bad")
            self.allow_btn.setEnabled(False)
            self.refresh_btn.setEnabled(False)
            return
        active = status.active if status is not None and status.active is not None else \
            (info.enabled if info is not None else None)
        if active is False:
            self._set_banner("ufw is installed but not enabled: rules are saved, but do "
                             "nothing until it is. Allow SSH first (sudo ufw allow OpenSSH), "
                             "then run sudo ufw enable in the terminal.", "warn")
        elif active is True:
            self._set_banner("ufw is active.", "ok")
        elif info is not None:
            self._set_banner("ufw is installed. Whether it is active shows once the rules "
                             "are read.", "info")
