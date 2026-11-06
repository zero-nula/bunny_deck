"""A panel in a window of its own ("Detach"), and where it came from so
"Attach" can put it back.

The panel is moved, never re-created: setParent() into this window's layout,
as QSplitter.insertWidget moves panels between splitters. The terminal
widget and its process keep running, with their scrollback, font and colours.
The window is translucent like the main window, so a terminal's background
opacity looks the same. Its position is left to the window manager (Wayland
does not let applications place windows).
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox, QVBoxLayout, QWidget

from .layout import ABOVE, BELOW, LEFT, RIGHT, HORIZONTAL_SPLIT

APP_NAME = "Bunny Deck"


def first_panel(node, panels):
    """The first panel in a describe() subtree (a panel or a tuple)."""
    if isinstance(node, tuple):
        for child in node[2]:
            found = first_panel(child, panels)
            if found is not None:
                return found
        return None
    return node if node in panels else None


def place_of(tree, panel, panels):
    """(neighbour, side) that puts panel back where it is in a describe()
    tree: after its previous sibling (right of / below it), else before its
    next one (left of / above it). None when it is the only panel."""

    def walk(node):
        orientation, _sizes, children = node
        for i, child in enumerate(children):
            if child is panel:
                after = RIGHT if orientation != HORIZONTAL_SPLIT else BELOW
                before = LEFT if orientation != HORIZONTAL_SPLIT else ABOVE
                for j in range(i - 1, -1, -1):
                    ref = first_panel(children[j], panels)
                    if ref is not None:
                        return ref, after
                for j in range(i + 1, len(children)):
                    ref = first_panel(children[j], panels)
                    if ref is not None:
                        return ref, before
                return None
            if isinstance(child, tuple):
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(tree)


class DetachedWindow(QWidget):
    """Holds one detached panel. closeAsked(window): the user closed it with
    the window's X; the main window decides (attach back, close, cancel)."""

    closeAsked = pyqtSignal(object)

    def __init__(self, panel, place, icon=None):
        super().__init__(None, Qt.WindowType.Window)
        self.panel = panel
        self.place = place  # (neighbour, side) in the layout, or None
        self.closing = False  # set when the window really goes
        self.setObjectName("detachedWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)
        else:
            self.setWindowIcon(QApplication.windowIcon() or QIcon())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        size = panel.size()
        lay.addWidget(panel)  # moves it here; nothing is restarted
        panel.show()
        self.resize(max(size.width(), 420), max(size.height(), 260))
        self.update_title()

    def update_title(self):
        name = self.panel.name_label.text()
        self.setWindowTitle(f"{name} — {APP_NAME}")

    def take_panel(self):
        """Gives the panel back (unparented); the window is then empty."""
        panel = self.panel
        self.layout().removeWidget(panel)
        panel.setParent(None)
        return panel

    def closeEvent(self, event):
        if self.closing:
            event.accept()
            return
        event.ignore()
        self.closeAsked.emit(self)


def ask_close(parent, panel):
    """"attach", "close" or None (cancel) for a detached window's X."""
    box = QMessageBox(QMessageBox.Icon.Question, "Close this window?",
                      f"Close the window of “{panel.name_label.text()}”?", parent=parent)
    if panel.is_terminal:
        box.setInformativeText("Attach it back to the main window, or close the session. "
                               + ("The connection or shell will be terminated."
                                  if panel.running else "The session has already ended."))
        close_text = "Close Session"
    else:
        box.setInformativeText("Attach it back to the main window, or close it.")
        close_text = "Close"
    attach = box.addButton("Attach Back", QMessageBox.ButtonRole.AcceptRole)
    close = box.addButton(close_text, QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(attach)
    box.exec()
    clicked = box.clickedButton()
    if clicked is attach:
        return "attach"
    if clicked is close:
        return "close"
    return None
