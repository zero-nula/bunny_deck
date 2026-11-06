"""The Docker panel: a server's containers, grouped by compose project, as a
panel in the layout next to its terminals (it moves, snaps and drags like
one, see panelbase.py).

While the panel is visible, docker.Watch runs one small loop on the server
over the shared SSH connection; hiding or closing the panel stops it. A
double click opens a shell in the container, the right-click menu also
offers its logs; both open as ordinary terminals (the window does that, on
shellRequested / logsRequested / projectLogsRequested).
"""

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QHeaderView, QLabel, QMenu, QSizePolicy, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

from . import docker, icons
from .panelbase import BasePanel

RETRY_MS = 3000  # trying the connection again while it is not up

LEVEL_COLOURS = {"running": "#6cc070", "starting": "#e0a43a", "bad": "#e5534b",
                 "stopped": "#5f6773"}

COLUMNS = ("Name", "Image", "Status", "Ports")
ROLE_CONTAINER = Qt.ItemDataRole.UserRole
ROLE_PROJECT = Qt.ItemDataRole.UserRole + 1


class DockerPanel(BasePanel):
    """argv_for(script): the argv that runs a script on this panel's server."""

    shellRequested = pyqtSignal(object, str)  # (panel, container name)
    logsRequested = pyqtSignal(object, str)  # (panel, container name)
    projectLogsRequested = pyqtSignal(object, str)  # (panel, compose project)
    runningChanged = pyqtSignal(object, int)  # (panel, running containers)

    def __init__(self, profile, argv_for=None, parent=None):
        super().__init__(profile, parent)
        self.argv_for = argv_for or (lambda script: docker.ssh_argv(profile.ssh_args(), script))
        self.containers = []
        self.running_count = None
        self.problem = None  # (kind, message) while there is one
        self._watch = None
        self._data_key = None
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(RETRY_MS)
        self._retry.timeout.connect(self._start_watch)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.message = QLabel("Waiting for the SSH connection…")
        self.message.setObjectName("dockerMessage")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tree = self._build_tree()
        self.stack.addWidget(self.message)
        self.stack.addWidget(self.tree)
        layout.addWidget(self.stack, 1)
        self._update_active_line()

    # ----- UI -------------------------------------------------------------

    def _build_header(self):
        header = self._build_base_header(self.profile.name)
        h = self.header_layout
        self.whale = QLabel()
        self.whale.setPixmap(icons.pixmap("docker", "#5b8def", 16, 2.0))
        self.whale.setToolTip("Docker")
        self.count_label = QLabel("")
        self.count_label.setObjectName("termTarget")
        h.addWidget(self.status_dot)
        h.addWidget(self.whale)
        h.addWidget(self.name_label)
        h.addWidget(self.count_label)
        h.addStretch(1)
        self.refresh_btn = self.header_button("refresh", "Refresh", "Refresh the containers now")
        self.refresh_btn.clicked.connect(self.refresh)
        h.addWidget(self.refresh_btn)
        self._add_move_button("panel")
        self._finish_header((self.move_btn,),
                            (self.status_dot, self.whale, self.name_label, self.count_label),
                            "Close the Docker panel")
        self._set_dot("connecting", "connecting")
        return header

    def _build_tree(self):
        t = QTreeWidget()
        t.setObjectName("dockerTree")
        t.setColumnCount(len(COLUMNS))
        t.setHeaderLabels(COLUMNS)
        t.setRootIsDecorated(True)
        t.setUniformRowHeights(True)
        t.setAlternatingRowColors(False)
        t.setIconSize(QSize(10, 10))
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(self._context_menu)
        t.itemDoubleClicked.connect(self._double_clicked)
        hdr = t.header()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        t.setColumnWidth(0, 200)
        t.setColumnWidth(1, 180)
        t.setColumnWidth(2, 150)
        t.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        t.installEventFilter(self)
        return t

    def eventFilter(self, obj, event):
        handled, result = self._header_event(obj, event)
        if handled:
            return result
        # (Events come while the header is built, before the tree exists.)
        if obj is getattr(self, "tree", None) and event.type() == event.Type.FocusIn:
            self.focused.emit(self)
        return False

    def focus_terminal(self):
        self.tree.setFocus()

    # ----- watching -------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if self._watch is None:
            self._start_watch()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._stop_watch()

    def refresh(self):
        self._stop_watch()
        self._start_watch()

    def _start_watch(self):
        self._retry.stop()
        if self._watch is not None or not self.isVisible():
            return
        self._watch = docker.Watch(self.argv_for(docker.WATCH_SCRIPT), self)
        self._watch.updated.connect(self.show_containers)
        self._watch.problem.connect(self._on_problem)

    def _stop_watch(self):
        self._retry.stop()
        if self._watch is not None:
            self._watch.stop()
            self._watch.deleteLater()
            self._watch = None

    @property
    def watching(self):
        return self._watch is not None

    def terminate(self):
        self._stop_watch()

    def _on_problem(self, kind, message):
        self.problem = (kind, message)
        if kind == "connection":
            # Not connected (yet): the terminal may still be logging in.
            self._show_message("Waiting for the SSH connection…\n" + message, "connecting")
            self._stop_watch()
            self._retry.start()
        else:
            self._show_message(message, "error" if kind != "nodocker" else "off")

    def _show_message(self, text, state):
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)
        self._set_dot(text.splitlines()[0], state)

    # ----- the containers -------------------------------------------------

    def show_containers(self, containers):
        self.problem = None
        self.containers = containers
        running = sum(1 for c in containers if c.running)
        self.count_label.setText(f"{running}/{len(containers)} running")
        self._set_dot(f"{running} of {len(containers)} containers running", "ok")
        self.stack.setCurrentWidget(self.tree)
        if running != self.running_count:
            self.running_count = running
            self.runningChanged.emit(self, running)
        key = tuple(c.key() for c in containers)
        if key == self._data_key:
            return  # nothing changed: keep the tree (selection, scrolling) as it is
        self._data_key = key
        self._fill(containers)

    def _fill(self, containers):
        t = self.tree
        collapsed = {t.topLevelItem(i).data(0, ROLE_PROJECT)
                     for i in range(t.topLevelItemCount())
                     if not t.topLevelItem(i).isExpanded()}
        current = t.currentItem()
        selected = (current.data(0, ROLE_CONTAINER) or current.data(0, ROLE_PROJECT)) \
            if current is not None else None
        scroll = t.verticalScrollBar().value()
        t.clear()
        bold = QFont(t.font())
        bold.setBold(True)
        for project, workdir, members in docker.group_by_project(containers):
            up = sum(1 for c in members if c.running)
            top = QTreeWidgetItem([project, "", f"{up}/{len(members)} running", ""])
            top.setData(0, ROLE_PROJECT, project)
            top.setFont(0, bold)
            top.setIcon(0, icons.icon("folder"))
            top.setToolTip(0, workdir or "Containers that are not part of a compose project")
            top.setForeground(2, QColor("#8a919c"))
            top.setFirstColumnSpanned(False)
            t.addTopLevelItem(top)
            for c in members:
                item = QTreeWidgetItem([c.name, c.image, c.status, ", ".join(c.ports)])
                item.setData(0, ROLE_CONTAINER, c.name)
                item.setIcon(2, icons.dot(LEVEL_COLOURS[c.level], 10))
                item.setToolTip(0, f"{c.name}\nDouble-click for a shell")
                item.setToolTip(2, c.status)
                item.setToolTip(3, ", ".join(c.ports))
                if not c.running:
                    for col in range(len(COLUMNS)):
                        item.setForeground(col, QColor("#7d8590"))
                top.addChild(item)
                if c.name == selected:
                    t.setCurrentItem(item)
            top.setExpanded(project not in collapsed)
            if project == selected:
                t.setCurrentItem(top)
        t.verticalScrollBar().setValue(scroll)

    # ----- actions --------------------------------------------------------

    def _double_clicked(self, item, _column):
        name = item.data(0, ROLE_CONTAINER)
        if name:
            self.shellRequested.emit(self, name)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        name = item.data(0, ROLE_CONTAINER) if item is not None else None
        project = item.data(0, ROLE_PROJECT) if item is not None else None
        if name:
            menu.addAction(icons.icon("terminal"), "Shell").triggered.connect(
                lambda: self.shellRequested.emit(self, name))
            menu.addAction(icons.icon("saved"), "Logs").triggered.connect(
                lambda: self.logsRequested.emit(self, name))
            menu.addSeparator()
            menu.addAction("Copy name").triggered.connect(
                lambda: QGuiApplication.clipboard().setText(name))
        elif project and project != docker.NO_PROJECT:
            menu.addAction(icons.icon("saved"), "Logs").triggered.connect(
                lambda: self.projectLogsRequested.emit(self, project))
        if menu.actions():
            menu.addSeparator()
        menu.addAction(self.detach_btn.icon(), self.detach_btn.text()).triggered.connect(
            self._detach_clicked)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def item_for(self, name):
        """The tree row of a container (for tests)."""
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                if top.child(j).data(0, ROLE_CONTAINER) == name:
                    return top.child(j)
        return None
