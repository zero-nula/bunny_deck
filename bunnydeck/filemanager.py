"""Integrated dual-pane file manager (Local / any SSH connection per pane).

Network work runs on worker threads; results are delivered to the UI thread
with mainthread.call_soon. Delete always asks first.
"""

import json
import os
import threading
import time

from PyQt6.QtCore import QEvent, QMimeData, QMimeDatabase, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import editors, icons, sftp, theme
from .connections import run_on_main_sync
from .mainthread import call_soon
from .transfer import (
    LocalBackend, RemoteBackend, TransferError, copy_file, count_tree, delete_path, inside,
    plan_folder, protected, same_place,
)

LOCAL_ID = "__local__"
MIME_INTERNAL = "application/x-bunnydeck-files"
ROLE_ISDIR = Qt.ItemDataRole.UserRole


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return str(n)


def error_text(e):
    if isinstance(e, OSError) and e.strerror:
        return f"{e.strerror}: {e.filename}" if e.filename else e.strerror
    return str(e) or e.__class__.__name__


class _TransferResult:
    """Counters of one transfer, filled by the worker thread."""

    def __init__(self):
        self.files = self.folders = self.bytes = self.skipped = self.links = 0
        self.errors = []


def _icon(name, fallback):
    icon = QIcon.fromTheme(name)
    return icon if not icon.isNull() else QIcon.fromTheme(fallback)


_mimes = QMimeDatabase()
_icon_cache = {}


def file_icon(name):
    """The desktop's icon for a file name (by type), e.g. a script, an image."""
    mime = _mimes.mimeTypeForFile(name, QMimeDatabase.MatchMode.MatchExtension)
    key = mime.name()
    if key not in _icon_cache:
        icon = QIcon.fromTheme(mime.iconName())
        if icon.isNull():
            icon = QIcon.fromTheme(mime.genericIconName())
        if icon.isNull():
            icon = _icon("text-x-generic", "text-x-generic-symbolic")
        _icon_cache[key] = icon
    return _icon_cache[key]


def is_text_name(name):
    """Whether a file name suggests text, so a double-click opens it in the
    editor. Names without a known type (hosts, .bashrc, nginx.conf) are
    usually configuration files and count as text."""
    mime = _mimes.mimeTypeForFile(name, QMimeDatabase.MatchMode.MatchExtension)
    return mime.isDefault() or mime.inherits("text/plain")


def friendly_time(mtime, now=None):
    """Today 14:32 / Yesterday 09:10 / 3 Nov 14:32 / 3 Nov 2024."""
    if not mtime:
        return ""
    now = now or time.time()
    t = time.localtime(mtime)
    today = time.localtime(now)
    if t.tm_year == today.tm_year and t.tm_yday == today.tm_yday:
        return time.strftime("Today %H:%M", t)
    if t.tm_year == today.tm_year and t.tm_yday == today.tm_yday - 1:
        return time.strftime("Yesterday %H:%M", t)
    if t.tm_year == today.tm_year:
        return f"{t.tm_mday} {time.strftime('%b %H:%M', t)}"
    return f"{t.tm_mday} {time.strftime('%b %Y', t)}"


class PathBar(QStackedWidget):
    """The folder as clickable parts (/ › home › alice › projects); clicking
    the free space after them, or pressing Ctrl+L, edits it as text."""

    navigate = pyqtSignal(str)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.setObjectName("pathBar")
        self.entry = entry
        self.path = ""
        self.crumbs = QFrame()
        self.crumbs.setObjectName("crumbs")
        self.crumbs.setCursor(Qt.CursorShape.IBeamCursor)
        self.crumbs.installEventFilter(self)
        self.row = QHBoxLayout(self.crumbs)
        self.row.setContentsMargins(4, 0, 4, 0)
        self.row.setSpacing(0)
        self.addWidget(self.crumbs)
        self.addWidget(entry)
        entry.installEventFilter(self)
        entry.editingFinished.connect(self.show_crumbs)
        self.setFixedHeight(30)

    def set_path(self, path):
        self.path = path or ""
        self.entry.setText(self.path)
        while self.row.count():
            item = self.row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        parts = [p for p in self.path.split("/") if p]
        targets = [("/", "/")]
        acc = ""
        for part in parts:
            acc += "/" + part
            targets.append((part, acc))
        shown = targets if len(targets) <= 6 else [targets[0], ("…", None)] + targets[-4:]
        for i, (label, target) in enumerate(shown):
            if i > 0:
                sep = QLabel("›")
                sep.setObjectName("crumbSep")
                self.row.addWidget(sep)
            b = QToolButton()
            b.setObjectName("crumb")
            b.setText(label)
            b.setAutoRaise(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            last = i == len(shown) - 1
            b.setProperty("current", last)
            if target is None:
                b.setEnabled(False)
            else:
                b.setToolTip(target)
                b.clicked.connect(lambda _=False, t=target: self.navigate.emit(t))
            self.row.addWidget(b)
        self.row.addStretch(1)
        if self.currentWidget() is not self.entry or not self.entry.hasFocus():
            self.setCurrentWidget(self.crumbs)

    def edit(self):
        self.entry.setText(self.path)
        self.setCurrentWidget(self.entry)
        self.entry.setFocus()
        self.entry.selectAll()

    def show_crumbs(self):
        self.setCurrentWidget(self.crumbs)

    def eventFilter(self, obj, event):
        if obj is self.crumbs and event.type() == QEvent.Type.MouseButtonPress:
            self.edit()
            return True
        if obj is self.entry and event.type() == QEvent.Type.KeyPress \
                and event.key() == Qt.Key.Key_Escape:
            self.entry.setText(self.path)
            self.show_crumbs()
            return True
        return False


class FileList(QTreeWidget):
    """File list with drag (to the other pane) and drop (from the other pane
    or from local file managers such as Dolphin)."""

    dropFiles = pyqtSignal(object, object)  # (source side or None for local URLs, paths)
    deleteRequested = pyqtSignal()

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key.Key_Delete
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.deleteRequested.emit()
            return
        super().keyPressEvent(event)

    def __init__(self, pane):
        super().__init__()
        self.pane = pane
        self.setColumnCount(3)
        self.setHeaderLabels(["Name", "Size", "Modified"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setIconSize(QSize(18, 18))
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: pane.fm.show_file_menu(pane, self.viewport().mapToGlobal(pos)))
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

    def mimeTypes(self):
        return [MIME_INTERNAL, "text/uri-list"]

    def mimeData(self, items):
        md = QMimeData()
        payload = {"side": self.pane.side, "paths": self.pane.selected_paths()}
        md.setData(MIME_INTERNAL, json.dumps(payload).encode("utf-8"))
        return md

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction

    def _accepts(self, md):
        if md.hasFormat(MIME_INTERNAL):
            try:
                payload = json.loads(bytes(md.data(MIME_INTERNAL)).decode("utf-8"))
            except ValueError:
                return None
            if payload.get("side") != self.pane.side and payload.get("paths"):
                return payload["side"], payload["paths"]
            return None
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            return (None, paths) if paths else None
        return None

    def dragEnterEvent(self, e):
        if self._accepts(e.mimeData()):
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        res = self._accepts(e.mimeData())
        if res is None:
            e.ignore()
            return
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()
        self.dropFiles.emit(*res)


class FilePane(QWidget):
    """One side of the file manager."""

    def __init__(self, fm, side):
        super().__init__()
        self.fm = fm
        self.side = side
        self.backend = None
        self.source_id = None
        self.cwd = None
        self._load_seq = 0
        self._updating = False
        self.setObjectName("fmPane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        header = QWidget()
        header.setObjectName("fmPaneHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hv = QVBoxLayout(header)
        hv.setContentsMargins(8, 8, 8, 6)
        hv.setSpacing(6)

        def tool(icon_name, tip, slot=None, checkable=False, name="fmTool"):
            b = QToolButton()
            b.setObjectName(name)
            b.setIcon(icons.icon(icon_name))
            b.setIconSize(QSize(17, 17))
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setAutoRaise(True)
            if slot is not None:
                (b.toggled if checkable else b.clicked).connect(slot)
            return b

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self.source = QComboBox()
        self.source.setObjectName("fmSource")
        self.source.setIconSize(QSize(12, 12))
        self.source.setToolTip("Which computer this side shows")
        self.source.currentIndexChanged.connect(self._on_source_changed)
        row1.addWidget(self.source, 1)
        self.busy = QLabel("")
        self.busy.setObjectName("dimLabel")
        row1.addWidget(self.busy)
        self.hidden_btn = tool("eye", "Show hidden files", lambda *_: self.refresh(),
                               checkable=True)
        self.newdir_btn = tool("folder-plus", "New folder", self.new_folder)
        self.delete_btn = tool("trash", "Delete the selected files and folders (Delete key; "
                               "asks first)", lambda: self.fm.delete_selection(self),
                               name="deleteBtn")
        # The names, for screen readers and tests; only the icons show.
        for b, text in ((self.hidden_btn, "Hidden files"), (self.newdir_btn, "New Folder"),
                        (self.delete_btn, "Delete")):
            b.setText(text)
            row1.addWidget(b)
        hv.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(2)
        row2.addWidget(tool("arrow-up", "Parent folder", self.go_up))
        row2.addWidget(tool("home", "Home folder", self.go_home))
        self.path_entry = QLineEdit()
        self.path_entry.setObjectName("pathEntry")
        self.path_entry.setToolTip("Type a path and press Enter (Esc goes back)")
        self.path_entry.returnPressed.connect(self._on_path_activate)
        self.path_bar = PathBar(self.path_entry)
        self.path_bar.navigate.connect(lambda path: self.backend and self.load(path))
        row2.addWidget(self.path_bar, 1)
        row2.addWidget(tool("refresh", "Reload (or reconnect)",
                            lambda: self.refresh(reconnect=True)))
        hv.addLayout(row2)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        # (Inside its card border; padding in the style sheet is not counted
        # when a word-wrapped label measures its height.)
        self.error_label.setContentsMargins(10, 8, 10, 8)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.hide()
        self._error_is_info = False

        self.view = FileList(self)
        self.view.itemActivated.connect(self._on_item_activated)
        self.view.dropFiles.connect(self._on_drop)
        self.view.deleteRequested.connect(lambda: self.fm.delete_selection(self))

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(header)
        # The message (an error card or a dim note) with some air around it.
        msg = QHBoxLayout()
        msg.setContentsMargins(8, 8, 8, 0)
        msg.addWidget(self.error_label)
        v.addLayout(msg)
        v.addWidget(self.view, 1)

    # ----- sources --------------------------------------------------------

    def set_sources(self, sources):
        """sources: list of (id, label, icon). Keeps the current selection."""
        current = self.source_id
        self._updating = True
        self.source.clear()
        for sid, label, icon in sources:
            self.source.addItem(icon, label, sid)
        idx = self.source.findData(current) if current is not None else -1
        self.source.setCurrentIndex(idx)
        self._updating = False
        if current is not None and idx < 0:
            # The selected connection disappeared; fall back to Local.
            self.select_source(LOCAL_ID)

    def select_source(self, source_id):
        idx = self.source.findData(source_id)
        if idx < 0:
            return
        if self.source.currentIndex() == idx and self.backend is not None:
            return
        if self.source.currentIndex() == idx:
            self._on_source_changed(idx)
        else:
            self.source.setCurrentIndex(idx)

    def _on_source_changed(self, idx):
        if self._updating or idx < 0:
            return
        sid = self.source.itemData(idx)
        if sid == self.source_id and self.backend is not None:
            return
        self.source_id = sid
        self.backend = None
        self.cwd = None
        self.view.clear()
        self._connect_and_load()

    def _connect_and_load(self, path=None):
        sid = self.source_id
        if sid == LOCAL_ID:
            self.backend = LocalBackend()
            self.load(path or self.backend.home)
            return
        profile = self.fm.profile_for(sid)
        if profile is None:
            self.show_error("This connection is no longer available.")
            return
        self.set_busy(True, f"Connecting to {profile.name}…")

        def done(client, error):
            if self.source_id != sid:
                return
            self.set_busy(False)
            if error == "cancelled":
                self.show_error(f"Authentication for {profile.name} was cancelled. "
                                "Press reload to try again.")
                return
            if error:
                self.show_error(f"Could not connect to {profile.name}:\n{error}")
                return
            self.backend = RemoteBackend(client, profile.name)
            self.load(path or self.backend.home)

        self.fm.pool.get(profile, done)

    # ----- navigation -----------------------------------------------------

    def load(self, path):
        if self.backend is None:
            self._connect_and_load(path)
            return
        self._load_seq += 1
        seq = self._load_seq
        backend = self.backend
        self.set_busy(True)

        def work():
            try:
                items, err = backend.list(path), None
            except (OSError, sftp.SFTPError) as e:
                items, err = None, error_text(e)
            call_soon(lambda: finish(items, err))

        def finish(items, err):
            if seq != self._load_seq or backend is not self.backend:
                return
            self.set_busy(False)
            self.path_bar.set_path(path)
            self.view.clear()
            self.cwd = path
            if err is not None:
                if isinstance(backend, RemoteBackend) and not backend.client.alive:
                    self.backend = None
                    self.show_error(f"Connection lost: {err}\nPress reload to reconnect.")
                else:
                    self.show_error(f"Cannot open {path}:\n{err}")
                return
            self.hide_error()
            show_hidden = self.hidden_btn.isChecked()
            items = sorted(items, key=lambda it: (not it.is_dir, it.name.lower()))
            dir_icon = _icon("folder", "folder-symbolic")
            link_icon = _icon("emblem-symbolic-link", "inode-symlink")
            dim = QColor(theme.DIM)
            rows = []
            for it in items:
                if not show_hidden and it.name.startswith("."):
                    continue
                mtime = friendly_time(it.mtime)
                row = QTreeWidgetItem([it.name, "" if it.is_dir else human_size(it.size), mtime])
                row.setIcon(0, dir_icon if it.is_dir else (link_icon if it.is_link
                                                           else file_icon(it.name)))
                if it.mtime:
                    row.setToolTip(2, time.strftime("%Y-%m-%d %H:%M:%S",
                                                    time.localtime(it.mtime)))
                for col in (1, 2):
                    row.setForeground(col, dim)
                row.setData(0, ROLE_ISDIR, it.is_dir)
                row.setTextAlignment(1, Qt.AlignmentFlag.AlignRight |
                                     Qt.AlignmentFlag.AlignVCenter)
                rows.append(row)
            self.view.addTopLevelItems(rows)

        threading.Thread(target=work, daemon=True).start()

    def refresh(self, reconnect=False):
        if self.source_id is None:
            return
        if self.backend is None or (reconnect and isinstance(self.backend, RemoteBackend)
                                    and not self.backend.client.alive):
            self.backend = None
            self._connect_and_load(self.cwd)
        elif self.cwd is not None:
            self.load(self.cwd)

    def go_up(self):
        if self.backend is not None and self.cwd:
            self.load(self.backend.parent(self.cwd))

    def go_home(self):
        if self.backend is not None:
            self.load(self.backend.home)

    def _on_path_activate(self):
        if self.backend is None:
            self.refresh(reconnect=True)
            return
        self.load(self.backend.resolve(self.cwd or self.backend.home, self.path_entry.text()))

    def _on_item_activated(self, item, column):
        if item.data(0, ROLE_ISDIR):
            self.load(self.backend.join(self.cwd, item.text(0)))
        elif self.backend is not None and self.cwd is not None:
            self.fm.open_file(self, item.text(0))

    def selected_files(self):
        """Names of the selected items that are not folders."""
        return [it.text(0) for it in self.view.selectedItems() if not it.data(0, ROLE_ISDIR)]

    def names(self):
        return [self.view.topLevelItem(i).text(0) for i in range(self.view.topLevelItemCount())]

    def selected_names(self):
        return [it.text(0) for it in self.view.selectedItems()]

    def selected_paths(self):
        if self.backend is None or self.cwd is None:
            return []
        return [self.backend.join(self.cwd, n) for n in self.selected_names()]

    def select_names(self, *names):
        self.view.clearSelection()
        for i in range(self.view.topLevelItemCount()):
            it = self.view.topLevelItem(i)
            if it.text(0) in names:
                it.setSelected(True)

    def _on_drop(self, src_side, paths):
        if src_side is None:
            self.fm.start_transfer(LocalBackend(), paths, self)
        else:
            src = self.fm.pane(src_side)
            if src.backend is not None:
                self.fm.start_transfer(src.backend, paths, self)

    # ----- folders --------------------------------------------------------

    def new_folder(self):
        if self.backend is None or self.cwd is None:
            return
        name = None
        while True:
            text, ok = QInputDialog.getText(self, "New Folder",
                                            f"Create a folder in {self.cwd}")
            if not ok:
                return
            n = text.strip()
            if n and "/" not in n and n not in (".", "..") and "\0" not in n:
                name = n
                break
        backend, path = self.backend, self.backend.join(self.cwd, name)

        def work():
            try:
                backend.mkdir(path)
                err = None
            except (OSError, sftp.SFTPError) as e:
                err = error_text(e)
            call_soon(lambda: finish(err))

        def finish(err):
            if err:
                self.fm.message("Could not create folder", f"{path}\n{err}", error=True)
            self.refresh()

        threading.Thread(target=work, daemon=True).start()

    # ----- UI state -------------------------------------------------------

    def set_busy(self, busy, text=None):
        self.busy.setText("…" if busy else "")
        if text:
            self._show_label(text, info=True)
        elif not busy and self._error_is_info:
            self.error_label.hide()

    def _show_label(self, text, info):
        self._error_is_info = info
        self.error_label.setObjectName("dimLabel" if info else "errorLabel")
        self.error_label.style().unpolish(self.error_label)
        self.error_label.style().polish(self.error_label)
        self.error_label.setText(text)
        self.error_label.show()

    def show_error(self, text):
        self.view.clear()
        self._show_label(text, info=False)

    def hide_error(self):
        self.error_label.hide()

    def error_visible(self):
        return self.error_label.isVisible() and not self._error_is_info


class FileManager(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        # The SFTP connections and the remote edits belong to the window,
        # shared with the Docker panel's compose files (editing.py).
        self.editing = window.editing
        self.pool = self.editing.pool
        self.edits = self.editing.edits
        self._extra_profiles = {}
        self._cancel = None
        self.setObjectName("fmRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        top = QWidget()
        top.setObjectName("fmTop")
        top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        th = QHBoxLayout(top)
        th.setContentsMargins(14, 10, 14, 6)
        title = QLabel("Files")
        title.setObjectName("fmTitle")
        hint = QLabel("Copy between this computer and your servers, or between two servers. "
                      "Double-click a text file to edit it: every save goes back to the server. "
                      "Delete always asks first.")
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        back = QPushButton("Back to terminals")
        back.setObjectName("fmBack")
        back.setIcon(icons.icon("terminal"))
        back.setToolTip("Back to the terminals (they kept running) — Ctrl+Shift+F")
        back.clicked.connect(self.window.show_terminals)
        th.addWidget(title)
        th.addSpacing(12)
        th.addWidget(hint, 1)
        th.addWidget(back)

        self.left = FilePane(self, "left")
        self.right = FilePane(self, "right")
        mid = QVBoxLayout()
        mid.setContentsMargins(8, 6, 8, 6)
        mid.setSpacing(8)
        mid.addStretch(1)
        self.to_right = QToolButton()
        self.to_right.setObjectName("copyBtn")
        self.to_right.setIcon(icons.icon("arrow-right", color="#ffffff"))
        self.to_right.setIconSize(QSize(20, 20))
        self.to_right.setToolTip("Copy the selected files from the left side into the folder "
                                 "on the right")
        self.to_right.clicked.connect(lambda: self.copy_selection(self.left, self.right))
        self.to_left = QToolButton()
        self.to_left.setObjectName("copyBtn")
        self.to_left.setIcon(icons.icon("arrow-left", color="#ffffff"))
        self.to_left.setIconSize(QSize(20, 20))
        self.to_left.setToolTip("Copy the selected files from the right side into the folder "
                                "on the left")
        self.to_left.clicked.connect(lambda: self.copy_selection(self.right, self.left))
        # A caption for the two arrows (not a control, so not dimmed like one).
        copy_label = QLabel("COPY")
        copy_label.setObjectName("copyCaption")
        copy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mid.addWidget(copy_label)
        mid.addWidget(self.to_right)
        mid.addWidget(self.to_left)
        mid.addStretch(1)
        body = QHBoxLayout()
        body.setContentsMargins(10, 4, 10, 6)
        body.setSpacing(0)
        body.addWidget(self.left, 1)
        body.addLayout(mid)
        body.addWidget(self.right, 1)

        bar = QWidget()
        bar.setObjectName("fmStatusbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(14, 6, 14, 8)
        self.status = QLabel("Ready")
        self.status.setObjectName("fmStatus")
        self.progress = QProgressBar()
        self.progress.setObjectName("fmProgress")
        self.progress.setFixedWidth(260)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.percent = QLabel("")
        self.percent.setObjectName("dimLabel")
        self.percent.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(lambda: self._cancel and self._cancel.set())
        bh.addWidget(self.status, 1)
        bh.addWidget(self.progress)
        bh.addWidget(self.percent)
        bh.addWidget(self.cancel_btn)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(top)
        v.addLayout(body, 1)
        v.addWidget(bar)

    # ----- credentials ----------------------------------------------------

    # ----- sources --------------------------------------------------------

    def pane(self, side):
        return self.left if side == "left" else self.right

    def profile_for(self, pid):
        return self.window.config.get(pid) or self._extra_profiles.get(pid)

    def sessions_changed(self):
        sources = [(LOCAL_ID, "This computer", icons.icon("terminal"))]
        seen = {LOCAL_ID}
        for panel in self.window.all_panels():
            p = panel.profile
            if p.is_local:
                continue  # not an SFTP source: "Local (this computer)" is the same place
            if p.id not in seen:
                seen.add(p.id)
                if self.window.config.get(p.id) is None:
                    self._extra_profiles[p.id] = p
                sources.append((p.id, f"{p.name}  ·  {p.target}",
                                icons.dot(theme.accent_for(p.appearance))))
        for p in sorted(self.window.config.profiles, key=lambda x: x.name.lower()):
            if p.id not in seen and not p.is_local:
                seen.add(p.id)
                sources.append((p.id, f"{p.name}  ·  {p.target}",
                                icons.dot(theme.accent_for(p.appearance))))
        for pane in (self.left, self.right):
            pane.set_sources(sources)

    def open_for(self, profile):
        if profile.is_local:
            # A local terminal is this computer: show it as "Local" (no SFTP).
            self.sessions_changed()
            if self.left.source_id is None:
                self.left.select_source(LOCAL_ID)
            self.right.select_source(LOCAL_ID)
            return
        if self.window.config.get(profile.id) is None:
            self._extra_profiles[profile.id] = profile
        self.sessions_changed()
        if self.left.source_id is None:
            self.left.select_source(LOCAL_ID)
        self.right.select_source(profile.id)

    # ----- transfers ------------------------------------------------------

    def copy_selection(self, src_pane, dst_pane):
        paths = src_pane.selected_paths()
        if not paths:
            self.message("Nothing selected", "Select one or more files in the "
                         f"{src_pane.side} pane first.")
            return
        self.start_transfer(src_pane.backend, paths, dst_pane)

    def start_transfer(self, src_backend, paths, dst_pane):
        if self._cancel is not None:
            self.message("Transfer in progress", "Wait for the current transfer to finish.")
            return
        dst_backend, dst_dir = dst_pane.backend, dst_pane.cwd
        if src_backend is None or dst_backend is None or dst_dir is None:
            self.message("No target folder", "Both panes need an open folder.", error=True)
            return
        cancel = threading.Event()
        self._cancel = cancel
        self._set_transferring(True)
        threading.Thread(target=self._transfer_worker,
                         args=(src_backend, list(paths), dst_backend, dst_dir, dst_pane, cancel),
                         daemon=True).start()

    @property
    def busy(self):
        return self._cancel is not None

    def _transfer_worker(self, src_backend, paths, dst_backend, dst_dir, dst_pane, cancel):
        r = _TransferResult()

        def ui(fn, *a):
            call_soon(lambda: fn(*a))

        jobs = self._plan_jobs(src_backend, paths, dst_backend, dst_dir, cancel, r, ui)
        if not cancel.is_set():
            self._run_jobs(src_backend, dst_backend, jobs, cancel, r, ui)
        ui(self._finish_transfer, r, cancel.is_set(), dst_pane)

    def _plan_jobs(self, src_backend, paths, dst_backend, dst_dir, cancel, r, ui):
        """Expands the selection into ("dir", target, name) and
        ("file", source, target, size, name) jobs, parents first."""
        jobs = []
        for src in paths:
            if cancel.is_set():
                break
            name = src_backend.basename(src)
            dst = dst_backend.join(dst_dir, name)
            try:
                exists, is_dir, size, _ = src_backend.stat(src)
                if not exists:
                    raise TransferError("No longer exists")
                if not is_dir:
                    jobs.append(("file", src, dst, size, name))
                    continue
                if same_place(src_backend, dst_backend) and inside(dst, src):
                    raise TransferError("A folder cannot be copied into itself")
                d_exists, d_is_dir, _, _ = dst_backend.stat(dst)
                if d_exists and not d_is_dir:
                    raise TransferError(f"A file named “{name}” already exists in the target")
                if d_exists:
                    answer = run_on_main_sync(call_soon, self._ask_merge, dst, dst_backend.label)
                    if answer == "cancel":
                        cancel.set()
                        break
                    if answer == "skip":
                        r.skipped += 1
                        continue
                ui(self._progress, f"Reading folder {name}…", None)
                plan = plan_folder(src_backend, src, dst_backend, dst, cancel)
                jobs += [("dir", d, rel) for d, rel in plan.dirs]
                jobs += [("file", *f) for f in plan.files]
                r.links += len(plan.skipped)
                r.errors += [f"{rel}: {error_text(e)}" for rel, e in plan.errors]
            except sftp.Cancelled:
                break
            except (OSError, sftp.SFTPError, TransferError) as e:
                r.errors.append(f"{name}: {error_text(e)}")
        return jobs

    def _run_jobs(self, src_backend, dst_backend, jobs, cancel, r, ui):
        files = [j for j in jobs if j[0] == "file"]
        total = sum(j[3] for j in files)
        done_size = 0  # planned bytes of the files already handled
        failed_dirs = []
        overwrite_all = False
        last_ui = [0.0]
        index = 0

        for job in jobs:
            if cancel.is_set():
                break
            if job[0] == "dir":
                _, d, rel = job
                try:
                    exists, is_dir, _, _ = dst_backend.stat(d)
                    if exists and not is_dir:
                        raise TransferError("A file with this name is in the way")
                    if not exists:
                        dst_backend.mkdir(d)
                        r.folders += 1
                except (OSError, sftp.SFTPError, TransferError) as e:
                    failed_dirs.append(d)
                    r.errors.append(f"{rel}: {error_text(e)}")
                continue

            _, src, dst, size, name = job
            index += 1
            if any(inside(dst, f) for f in failed_dirs):
                done_size += size  # its folder failed; already reported
                continue
            label = f"[{index}/{len(files)}] {name}"
            ui(self._progress, label, (done_size / total) if total else None)
            try:
                exists, is_dir, _, _ = dst_backend.stat(dst)
                overwrite = False
                if exists:
                    if is_dir:
                        raise TransferError(f"A folder named “{dst_backend.basename(dst)}” "
                                            "already exists in the target")
                    if same_place(src_backend, dst_backend) and src == dst:
                        raise TransferError("Source and target are the same file")
                    if overwrite_all:
                        overwrite = True
                    else:
                        answer = run_on_main_sync(call_soon, self._ask_overwrite, dst,
                                                  dst_backend.label, len(files) - index > 0)
                        if answer == "cancel":
                            cancel.set()
                            break
                        if answer == "skip":
                            r.skipped += 1
                            done_size += size
                            continue
                        overwrite = True
                        overwrite_all = answer == "all"

                def progress(d, t, before=done_size, size=size):
                    now = time.monotonic()
                    if now - last_ui[0] > 0.08 or d >= t:
                        last_ui[0] = now
                        part = (d / t) if t else 1.0
                        ui(self._progress, None,
                           ((before + part * size) / total) if total else part)

                def phase(text, label=label):
                    ui(self._progress, f"{label} — {text}", None)

                n = copy_file(src_backend, src, dst_backend, dst, overwrite=overwrite,
                              progress=progress, cancel=cancel, phase=phase)
                r.files += 1
                r.bytes += n
            except sftp.Cancelled:
                break
            except (OSError, sftp.SFTPError, TransferError) as e:
                r.errors.append(f"{name}: {error_text(e)}")
            done_size += size

    # ----- deleting -------------------------------------------------------

    def delete_selection(self, pane):
        """Counts what the selection contains, asks for confirmation, then
        deletes it (in a worker thread, with progress and Cancel)."""
        paths = pane.selected_paths()
        if not paths:
            self.message("Nothing selected", "Select the files or folders to delete in the "
                         f"{pane.side} pane first.")
            return
        if self._cancel is not None:
            self.message("Transfer in progress", "Wait for the current transfer to finish.")
            return
        backend = pane.backend
        refused = [p for p in paths if protected(backend, p)]
        if refused:
            self.message("Not deleted", "Bunny Deck does not delete the root or your home "
                         "folder (or a folder containing it):\n" + "\n".join(refused), error=True)
            return
        cancel = threading.Event()
        self._cancel = cancel
        self._set_transferring(True)
        self._progress("Checking what will be deleted…", None)

        def count():
            items, error = [], None
            try:
                for p in paths:
                    items.append((p, backend.kind(p) == "dir", *count_tree(backend, p, cancel)))
            except sftp.Cancelled:
                error = "cancelled"
            except (OSError, sftp.SFTPError) as e:
                error = error_text(e)
            call_soon(lambda: self._confirm_delete(pane, backend, items, error, cancel))

        threading.Thread(target=count, daemon=True).start()

    def _confirm_delete(self, pane, backend, items, error, cancel):
        if error is not None:
            self._cancel = None
            self._set_transferring(False)
            self.status.setText("Nothing deleted")
            if error != "cancelled":
                self.message("Cannot delete", error, error=True)
            return
        files = sum(i[2] for i in items)
        folders = sum(i[3] for i in items)
        if not self.ask_delete(items, files, folders, backend.label):
            self._cancel = None
            self._set_transferring(False)
            self.status.setText("Nothing deleted")
            return
        total = max(1, files + folders)

        def work():
            done = [0, 0]  # files, folders
            errors = []
            last_ui = [0.0]

            def progress(kind, p):
                done[kind == "dir"] += 1
                now = time.monotonic()
                if now - last_ui[0] > 0.08:
                    last_ui[0] = now
                    call_soon(lambda: self._progress(f"Deleting {backend.basename(p)}",
                                                     sum(done) / total))

            for p, *_ in items:
                if cancel.is_set():
                    break
                try:
                    delete_path(backend, p, cancel, progress)
                except sftp.Cancelled:
                    break
                except (OSError, sftp.SFTPError, TransferError) as e:
                    errors.append(f"{backend.basename(p)}: {error_text(e)}")
            call_soon(lambda: self._finish_delete(done, errors, cancel.is_set()))

        threading.Thread(target=work, daemon=True).start()

    def ask_delete(self, items, files, folders, where):
        """The confirmation; True only for an explicit Delete."""
        names = []
        for path, is_dir, f, d in items[:10]:
            name = path.rstrip("/").rsplit("/", 1)[-1]
            names.append(f"• {name}/  (folder: {f} file(s), {d - 1} subfolder(s))" if is_dir
                         else f"• {name}")
        if len(items) > 10:
            names.append(f"… and {len(items) - 10} more")
        what = f"{files} file(s)" + (f" and {folders} folder(s)" if folders else "")
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete permanently?",
                          f"Delete {what} on {where}?", parent=self.window)
        box.setInformativeText("\n".join(names) + "\n\nThis cannot be undone: nothing goes "
                               "to a trash folder.")
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is delete

    def _finish_delete(self, done, errors, cancelled):
        self._cancel = None
        self._set_transferring(False)
        parts = [f"Deleted {done[0]} file(s)" + (f" and {done[1]} folder(s)" if done[1] else "")]
        if errors:
            parts.append(f"{len(errors)} failed")
        if cancelled:
            parts.append("cancelled")
        self.status.setText(" · ".join(parts))
        for pane in (self.left, self.right):
            pane.refresh()
        if errors:
            self.message("Some items were not deleted", "\n".join(errors[:15]), error=True)

    def _ask_merge(self, path, where):
        box = QMessageBox(QMessageBox.Icon.Question, "Folder already exists",
                          "Folder already exists", parent=self.window)
        box.setInformativeText(f"{path}\non {where} already exists. Copy into it? Files that "
                               "exist in both are asked about one by one.")
        box.addButton("Cancel Transfer", QMessageBox.ButtonRole.RejectRole)
        skip = box.addButton("Skip Folder", QMessageBox.ButtonRole.NoRole)
        merge = box.addButton("Copy Into It", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(merge)
        box.exec()
        clicked = box.clickedButton()
        if clicked is merge:
            return "merge"
        if clicked is skip:
            return "skip"
        return "cancel"

    def _ask_overwrite(self, path, where, multiple):
        box = QMessageBox(QMessageBox.Icon.Warning, "File already exists",
                          "File already exists", parent=self.window)
        box.setInformativeText(f"{path}\non {where} already exists. Replace it?")
        box.addButton("Cancel Transfer", QMessageBox.ButtonRole.RejectRole)
        skip = box.addButton("Skip", QMessageBox.ButtonRole.NoRole)
        all_ = box.addButton("Replace All", QMessageBox.ButtonRole.YesRole) if multiple else None
        replace = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(skip)
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace:
            return "replace"
        if all_ is not None and clicked is all_:
            return "all"
        if clicked is skip:
            return "skip"
        return "cancel"  # Cancel Transfer, or the dialog was closed

    def _progress(self, label, fraction):
        if label is not None:
            self.status.setText(label)
        if fraction is not None:
            self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
            self.progress.setFormat(f"{fraction * 100:.0f}%")
            self.percent.setText(f"{fraction * 100:.0f}%")

    def _set_transferring(self, active):
        self.progress.setVisible(active)
        self.percent.setVisible(active)
        self.percent.setText("")
        self.cancel_btn.setVisible(active)
        self.to_left.setEnabled(not active)
        self.to_right.setEnabled(not active)
        for pane in (self.left, self.right):
            pane.delete_btn.setEnabled(not active)
        if active:
            self.progress.setValue(0)

    def _finish_transfer(self, r, cancelled, dst_pane):
        self._cancel = None
        self._set_transferring(False)
        copied = f"Copied {r.files} file(s)"
        if r.folders:
            copied += f" and {r.folders} folder(s)"
        parts = [f"{copied}, {human_size(r.bytes)}"]
        if r.skipped:
            parts.append(f"{r.skipped} skipped")
        if r.links:
            parts.append(f"{r.links} link(s)/special file(s) not copied")
        if r.errors:
            parts.append(f"{len(r.errors)} failed")
        if cancelled:
            parts.append("cancelled")
        self.status.setText(" · ".join(parts))
        dst_pane.refresh()
        if r.errors:
            more = f"\n… and {len(r.errors) - 15} more" if len(r.errors) > 15 else ""
            self.message("Some files were not copied", "\n".join(r.errors[:15]) + more,
                         error=True)

    # ----- editing --------------------------------------------------------

    def open_file(self, pane, name):
        """Double-click on a file: text files open in the editor; other
        files on this computer open in their usual application."""
        if is_text_name(name):
            self.edit_file(pane, name)
        elif not pane.backend.is_remote:
            QDesktopServices.openUrl(QUrl.fromLocalFile(pane.backend.join(pane.cwd, name)))
        else:
            self.notify(f"{name} is not a text file. Right-click it and choose Edit to open "
                        "it in the editor anyway, or copy it to this computer.")

    def edit_file(self, pane, name, editor=None):
        """Opens a file in the editor (Bunny Deck's choice unless one is
        given). A file on a server is downloaded, and every save in the
        editor is uploaded again (remoteedit.py)."""
        backend = pane.backend
        if backend is None or pane.cwd is None:
            return
        path = backend.join(pane.cwd, name)
        if not backend.is_remote:
            error = self.launch(path, editor)
            if error:
                self.message("Cannot open the editor", error, error=True)
            return
        profile = self.profile_for(pane.source_id)
        if profile is None:
            return
        self.edits.open(profile, backend.client, path, editor)

    def launch(self, path, editor=None):
        """Starts the editor on a local file; None, or an error message."""
        return self.editing.launch(path, editor)

    def ask(self, title, text, informative, yes_label):
        return self.editing.ask(title, text, informative, yes_label)

    def notify(self, text, error=False):
        self.status.setText(text.split("\n", 1)[0])
        if error:
            self.message("Editing a file", text, error=True)

    def show_file_menu(self, pane, global_pos):
        files = pane.selected_files() if pane.backend is not None else []
        menu = QMenu(self)
        found = editors.find_editors()
        if len(files) == 1:
            name = files[0]
            edit = menu.addAction(QIcon.fromTheme("document-edit"), "Edit")
            f = edit.font()
            f.setBold(True)
            edit.setFont(f)
            edit.triggered.connect(lambda: self.edit_file(pane, name))
            self.editing.fill_edit_with_menu(
                menu.addMenu("Edit With"), lambda e: self.edit_file(pane, name, e), found)
            if not pane.backend.is_remote and not is_text_name(name):
                menu.addAction("Open").triggered.connect(lambda: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(pane.backend.join(pane.cwd, name))))
            menu.addSeparator()
        self._fill_default_editor_menu(menu.addMenu("Default Editor"), found)
        if pane.selected_names():
            menu.addSeparator()
            menu.addAction(icons.icon("trash"), "Delete…").triggered.connect(
                lambda: self.delete_selection(pane))
        menu.exec(global_pos)
        menu.deleteLater()

    def _fill_default_editor_menu(self, menu, found):
        self.editing.fill_default_editor_menu(menu, found)

    def set_default_editor(self, choice):
        self.status.setText(self.editing.set_default_editor(choice))

    def ask_custom_editor(self):
        self.editing.ask_custom_editor()

    def _edit_with_command(self, pane, name):
        cmd = self.editing.ask_command("Edit With")
        if cmd is not None:
            self.edit_file(pane, name, editors.resolve(editors.COMMAND_PREFIX + cmd))

    def unsaved_edits(self):
        """Names of files being edited whose last changes are not on their server."""
        return self.editing.unsaved()

    # ----- misc -----------------------------------------------------------

    def message(self, title, text, error=False):
        box = QMessageBox(QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information,
                          title, title, parent=self.window)
        box.setInformativeText(text)
        box.exec()

    def shutdown(self):
        if self._cancel is not None:
            self._cancel.set()
        # (The edits and connections are the window's: editing.py.)
