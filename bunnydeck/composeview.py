"""A read-only view of a compose project's files, as a panel below the
Docker panel (it moves, snaps, detaches and closes like the others, see
panelbase.py). One tab per file (compose.yml, compose.override.yml, …),
with line numbers and light YAML colouring.

The files are read over the server's shared SSH connection
(docker.compose_read_script through remote.RemoteScript): no new login, no
password. Editing happens in the server's own editor, in a terminal
(Edit compose in the Docker panel), so nothing is written from here.
"""

import posixpath
import re

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QLabel, QMenu, QPlainTextEdit, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from . import docker, icons, remote
from .panelbase import BasePanel
from .terminal import terminal_font

FONT_SIZE = 10.0


def _fmt(colour, bold=False, italic=False):
    f = QTextCharFormat()
    f.setForeground(QColor(colour))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    f.setFontItalic(italic)
    return f


class YamlHighlighter(QSyntaxHighlighter):
    """Keys, strings, numbers, true/false/null, anchors and aliases,
    document markers and comments: enough to read a compose file."""

    KEY = _fmt("#8fb8ff", bold=True)
    STRING = _fmt("#b5d98c")
    NUMBER = _fmt("#e0a43a")
    CONSTANT = _fmt("#e879a6")
    ANCHOR = _fmt("#4fc1b0")
    MARKER = _fmt("#a78bfa", bold=True)
    COMMENT = _fmt("#6b7280", italic=True)
    DASH = _fmt("#a78bfa")

    _key = re.compile(r"^(\s*(?:-\s+)?)([^\s#'\"][^:#]*?|\"[^\"]*\"|'[^']*')(\s*:)(?=\s|$)")
    _string = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'")
    _number = re.compile(r"(?<![\w.:-])[-+]?\d+(?:\.\d+)?(?![\w.:-])")
    _constant = re.compile(r"(?<![\w-])(?:true|false|yes|no|null|on|off|~)(?![\w-])", re.I)
    _anchor = re.compile(r"[&*][\w-]+")
    _dash = re.compile(r"^\s*-(?=\s|$)")

    def highlightBlock(self, text):
        if text.startswith("---") or text.startswith("..."):
            self.setFormat(0, len(text), self.MARKER)
            return
        comment = self._comment_start(text)
        code = text[:comment] if comment is not None else text
        m = self._dash.match(code)
        if m:
            self.setFormat(m.end() - 1, 1, self.DASH)
        for rx, fmt in ((self._number, self.NUMBER), (self._constant, self.CONSTANT),
                        (self._anchor, self.ANCHOR), (self._string, self.STRING)):
            for m in rx.finditer(code):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        m = self._key.match(code)
        if m:
            self.setFormat(m.start(2), m.end(2) - m.start(2), self.KEY)
        if comment is not None:
            self.setFormat(comment, len(text) - comment, self.COMMENT)

    @staticmethod
    def _comment_start(text):
        """Where a # comment starts (not inside quotes), or None."""
        quote = None
        for i, ch in enumerate(text):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
                return i
        return None


class _Gutter(QWidget):
    """The line numbers beside a CodeView."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self):
        return QSize(self.view.gutter_width(), 0)

    def paintEvent(self, event):
        self.view.paint_gutter(event)


class CodeView(QPlainTextEdit):
    """Read-only text with line numbers."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setObjectName("composeText")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(terminal_font(FONT_SIZE))
        self.gutter = _Gutter(self)
        self.blockCountChanged.connect(lambda _n: self._update_margin())
        self.updateRequest.connect(self._scroll_gutter)
        self.highlighter = YamlHighlighter(self.document())
        self.setPlainText(text)
        self._update_margin()

    def gutter_width(self):
        digits = max(2, len(str(self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _scroll_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), QColor("#0a0b0d"))
        p.setPen(QColor("#4b5260"))
        p.setFont(self.font())
        block = self.firstVisibleBlock()
        height = self.fontMetrics().height()
        offset = self.contentOffset()
        while block.isValid():
            # Each number at its own line's position (adding up rounded
            # heights would drift).
            top = round(self.blockBoundingGeometry(block).translated(offset).top())
            if top > event.rect().bottom():
                break
            if block.isVisible():
                p.drawText(0, top, self.gutter.width() - 6, height,
                           Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            block = block.next()
        p.end()


MESSAGES = {
    "missing": "This file is no longer on the server:\n{path}",
    "unreadable": "This user may not read the file:\n{path}",
}


class ComposeViewPanel(BasePanel):
    """The compose files of one project (profile, project, working folder,
    files), read when shown and on Refresh. editRequested(panel, [paths],
    editor or None): in a local editor; terminalEditRequested(panel, [paths]):
    in the server's $EDITOR; applyRequested(panel): docker compose up -d."""

    editRequested = pyqtSignal(object, list, object)
    terminalEditRequested = pyqtSignal(object, list)
    applyRequested = pyqtSignal(object)

    def __init__(self, profile, project, working_dir, files, parent=None):
        super().__init__(profile, parent)
        self.project = project
        self.working_dir = working_dir
        self.files = list(files)
        self.results = None  # [(path, status, content)] of the last read
        self._read = None
        self._nonce = ""
        # Set by the window: fill_edit_with(menu, on_editor) (editing.py).
        self.fill_edit_with = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.message = QLabel("Reading…")
        self.message.setObjectName("dockerMessage")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("composeTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.stack.addWidget(self.message)
        self.stack.addWidget(self.tabs)
        layout.addWidget(self.stack, 1)
        self._update_active_line()
        self.refresh()

    def _build_header(self):
        header = self._build_base_header(f"compose: {self.project}")
        h = self.header_layout
        self.file_icon = QLabel()
        self.file_icon.setPixmap(icons.pixmap("docker", "#5b8def", 16, 2.0))
        self.path_label = QLabel(self.files[0] if self.files else "")
        self.path_label.setObjectName("termTarget")
        self.path_label.setMinimumWidth(1)
        h.addWidget(self.status_dot)
        h.addWidget(self.file_icon)
        h.addWidget(self.name_label)
        h.addWidget(self.path_label, 1)
        self.edit_btn = self.header_button(
            "edit", "Edit", "Edit this file in your editor on this computer; every save is "
            "uploaded to the server (right-click the text for more)")
        self.edit_btn.clicked.connect(lambda: self.editRequested.emit(
            self, [self.current_path()], None))
        h.addWidget(self.edit_btn)
        self.refresh_btn = self.header_button("refresh", "Refresh", "Read the files again")
        self.refresh_btn.clicked.connect(self.refresh)
        h.addWidget(self.refresh_btn)
        self._add_move_button("panel")
        self._finish_header((self.move_btn,), (self.status_dot, self.file_icon, self.name_label,
                                               self.path_label), "Close")
        self._set_dot("reading", "connecting")
        return header

    def focus_terminal(self):
        w = self.tabs.currentWidget()
        (w or self.tabs).setFocus()

    def current_path(self):
        page = self.tabs.currentWidget()
        path = page.property("path") if page is not None else None
        return path or (self.files[0] if self.files else "")

    def context_menu(self, global_pos):
        """Edit / Edit With ▸ / Edit in terminal / Apply for the file shown."""
        path = self.current_path()
        menu = QMenu(self)
        edit = menu.addAction(icons.icon("edit"), "Edit")
        f = edit.font()
        f.setBold(True)
        edit.setFont(f)
        edit.triggered.connect(lambda: self.editRequested.emit(self, [path], None))
        with_ = menu.addMenu("Edit With")
        if self.fill_edit_with is not None:
            self.fill_edit_with(with_, lambda e: self.editRequested.emit(self, [path], e))
        menu.addAction("Edit in terminal ($EDITOR)").triggered.connect(
            lambda: self.terminalEditRequested.emit(self, [path]))
        menu.addSeparator()
        menu.addAction("Apply (up -d)").triggered.connect(lambda: self.applyRequested.emit(self))
        menu.addSeparator()
        menu.addAction(self.detach_btn.icon(), self.detach_btn.text()).triggered.connect(
            self._detach_clicked)
        menu.exec(global_pos)
        menu.deleteLater()

    # ----- reading ----------------------------------------------------------

    def refresh(self):
        if self._read is not None:
            self._read.stop()
        self._nonce = docker.new_nonce()
        files, nonce = list(self.files), self._nonce
        self._read = remote.RemoteScript(
            self.profile.ssh_args(), docker.compose_read_script(files, nonce),
            parse=lambda text: docker.parse_compose_read(text, files, nonce),
            delays=(0,), ssh_binary=lambda: docker.SSH_BINARY, parent=self)
        self._read.output.connect(self.show_files)
        self._read.gave_up.connect(self._not_connected)
        self._set_dot("reading", "connecting")
        self._read.start()

    def _not_connected(self):
        self.results = None
        self.message.setText("Could not read the files: not connected to the server.\n"
                             "Open its terminal, then click Refresh.")
        self.stack.setCurrentWidget(self.message)
        self._set_dot("not connected", "error")

    def show_files(self, results):
        self.results = results
        current = self.tabs.currentIndex()
        self.tabs.clear()
        for path, status, content in results:
            if status == "ok":
                page = CodeView(content)
                page.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                page.customContextMenuRequested.connect(
                    lambda pos, pg=page: self.context_menu(pg.mapToGlobal(pos)))
            else:
                page = QLabel(MESSAGES[status].format(path=path))
                page.setObjectName("dockerMessage")
                page.setWordWrap(True)
                page.setAlignment(Qt.AlignmentFlag.AlignCenter)
            page.setProperty("path", path)
            i = self.tabs.addTab(page, posixpath.basename(path))
            self.tabs.setTabToolTip(i, path)
            if status != "ok":
                self.tabs.setTabIcon(i, icons.dot("#e5534b", 8))
        if 0 <= current < self.tabs.count():
            self.tabs.setCurrentIndex(current)
        self.stack.setCurrentWidget(self.tabs)
        bad = [s for _p, s, _c in results if s != "ok"]
        self._set_dot("some files could not be read" if bad else "read", "error" if bad else "ok")
        self._tab_changed(self.tabs.currentIndex())

    def _tab_changed(self, index):
        page = self.tabs.widget(index)
        if page is not None:
            self.path_label.setText(page.property("path"))
            self.path_label.setToolTip(page.property("path"))

    def terminate(self):
        if self._read is not None:
            self._read.stop()
