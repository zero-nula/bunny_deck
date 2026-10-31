"""Integrated dual-pane file manager (Local / any SSH connection per pane).

Network work runs on worker threads; results are delivered to the UI thread
with mainthread.call_soon. There is intentionally no delete action.
"""

import json
import threading
import time

from PyQt6.QtCore import QMimeData, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import sftp
from .connections import ConnectionPool, run_on_main_sync
from .dialogs import CredentialDialog
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
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
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
        header.setObjectName("fmHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hv = QVBoxLayout(header)
        hv.setContentsMargins(4, 4, 4, 4)
        hv.setSpacing(4)
        row1 = QHBoxLayout()
        self.source = QComboBox()
        self.source.setToolTip("Source/target for this pane")
        self.source.currentIndexChanged.connect(self._on_source_changed)
        row1.addWidget(self.source, 1)
        self.busy = QLabel("")
        self.busy.setObjectName("dimLabel")
        row1.addWidget(self.busy)
        self.hidden_btn = QToolButton()
        self.hidden_btn.setText(".*")
        self.hidden_btn.setCheckable(True)
        self.hidden_btn.setToolTip("Show hidden files")
        self.hidden_btn.toggled.connect(lambda *_: self.refresh())
        row1.addWidget(self.hidden_btn)
        self.newdir_btn = QPushButton("New Folder")
        self.newdir_btn.clicked.connect(self.new_folder)
        row1.addWidget(self.newdir_btn)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("deleteBtn")
        self.delete_btn.setToolTip("Delete the selected files and folders (asks first)")
        self.delete_btn.clicked.connect(lambda: self.fm.delete_selection(self))
        row1.addWidget(self.delete_btn)
        hv.addLayout(row1)

        row2 = QHBoxLayout()

        def tool(icon, fallback, text, tip, slot):
            b = QToolButton()
            b.setIcon(_icon(icon, fallback))
            if b.icon().isNull():
                b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row2.addWidget(b)
            return b

        tool("go-up", "go-up-symbolic", "↑", "Parent directory", self.go_up)
        tool("go-home", "go-home-symbolic", "⌂", "Home directory", self.go_home)
        self.path_entry = QLineEdit()
        self.path_entry.setToolTip("Type a path and press Enter")
        self.path_entry.returnPressed.connect(self._on_path_activate)
        row2.addWidget(self.path_entry, 1)
        tool("view-refresh", "view-refresh-symbolic", "⟳", "Reload (or reconnect)",
             lambda: self.refresh(reconnect=True))
        hv.addLayout(row2)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
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
        v.addWidget(self.error_label)
        v.addWidget(self.view, 1)

    # ----- sources --------------------------------------------------------

    def set_sources(self, sources):
        """sources: list of (id, label). Keeps the current selection."""
        current = self.source_id
        self._updating = True
        self.source.clear()
        for sid, label in sources:
            self.source.addItem(label, sid)
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
            self.path_entry.setText(path)
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
            file_icon = _icon("text-x-generic", "text-x-generic-symbolic")
            link_icon = _icon("emblem-symbolic-link", "inode-symlink")
            rows = []
            for it in items:
                if not show_hidden and it.name.startswith("."):
                    continue
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(it.mtime)) \
                    if it.mtime else ""
                row = QTreeWidgetItem([it.name, "" if it.is_dir else human_size(it.size), mtime])
                row.setIcon(0, dir_icon if it.is_dir else (link_icon if it.is_link
                                                           else file_icon))
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
        self.pool = ConnectionPool(call_soon, self._ask_credentials)
        self._extra_profiles = {}
        self._cancel = None
        self.setObjectName("fmRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        top = QWidget()
        top.setObjectName("fmHeader")
        top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        th = QHBoxLayout(top)
        th.setContentsMargins(6, 4, 6, 4)
        back = QPushButton("← Terminals")
        back.setToolTip("Back to the terminals (they kept running)")
        back.clicked.connect(self.window.show_terminals)
        th.addWidget(back)
        title = QLabel("<b>File Transfer</b>&nbsp;&nbsp;<small>Select a source and a target "
                       "for each side. Files are copied, never moved or deleted.</small>")
        th.addWidget(title, 1)

        self.left = FilePane(self, "left")
        self.right = FilePane(self, "right")
        mid = QVBoxLayout()
        mid.setContentsMargins(6, 6, 6, 6)
        mid.addStretch(1)
        self.to_right = QPushButton("Copy →")
        self.to_right.setToolTip("Copy the selected files from the left pane into the right "
                                 "pane's folder")
        self.to_right.clicked.connect(lambda: self.copy_selection(self.left, self.right))
        self.to_left = QPushButton("← Copy")
        self.to_left.setToolTip("Copy the selected files from the right pane into the left "
                                "pane's folder")
        self.to_left.clicked.connect(lambda: self.copy_selection(self.right, self.left))
        mid.addWidget(self.to_right)
        mid.addWidget(self.to_left)
        mid.addStretch(1)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self.left, 1)
        body.addLayout(mid)
        body.addWidget(self.right, 1)

        bar = QWidget()
        bar.setObjectName("fmStatusbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(8, 4, 8, 4)
        self.status = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(lambda: self._cancel and self._cancel.set())
        bh.addWidget(self.status, 1)
        bh.addWidget(self.progress)
        bh.addWidget(self.cancel_btn)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(top)
        v.addLayout(body, 1)
        v.addWidget(bar)

    # ----- credentials ----------------------------------------------------

    def _ask_credentials(self, profile, prompt, kind, retry):
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

    # ----- sources --------------------------------------------------------

    def pane(self, side):
        return self.left if side == "left" else self.right

    def profile_for(self, pid):
        return self.window.config.get(pid) or self._extra_profiles.get(pid)

    def sessions_changed(self):
        sources = [(LOCAL_ID, "Local (this computer)")]
        seen = {LOCAL_ID}
        for panel in self.window.layout_.panels:
            p = panel.profile
            if p.is_local:
                continue  # not an SFTP source: "Local (this computer)" is the same place
            if p.id not in seen:
                seen.add(p.id)
                if self.window.config.get(p.id) is None:
                    self._extra_profiles[p.id] = p
                sources.append((p.id, f"{p.name}  ({p.target})"))
        for p in sorted(self.window.config.profiles, key=lambda x: x.name.lower()):
            if p.id not in seen and not p.is_local:
                seen.add(p.id)
                sources.append((p.id, f"{p.name}  ({p.target})"))
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

    def _set_transferring(self, active):
        self.progress.setVisible(active)
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

    # ----- misc -----------------------------------------------------------

    def message(self, title, text, error=False):
        box = QMessageBox(QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information,
                          title, title, parent=self.window)
        box.setInformativeText(text)
        box.exec()

    def shutdown(self):
        if self._cancel is not None:
            self._cancel.set()
        self.pool.close_all()
