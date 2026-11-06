"""What every panel in the layout has in common: the header with its colour
dot and name, the Move and close buttons, dragging by the header (with the
blue "grabbed" look), the line over the active panel, and fading tools in.

TerminalPanel (terminal.py) and DockerPanel (dockerpanel.py) build on it,
so snap layouts, Move and drag behave the same for both. The window treats
panels alike; where only terminals make sense (broadcast, reconnect, the
open-terminal counts) it checks `is_terminal`.
"""

from PyQt6.QtCore import QEvent, QPropertyAnimation, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton,
    QWidget,
)

from . import icons, theme


class BasePanel(QWidget):
    """A panel with a header. Subclasses call _build_base_header(), add their
    own widgets to self.header_layout, then _finish_header(tools)."""

    closeRequested = pyqtSignal(object)
    focused = pyqtSignal(object)
    # (panel, QMenu): the window fills the Move menu just before it opens.
    moveMenuRequested = pyqtSignal(object, object)
    # The Move button: the window shows the snap layouts for this panel.
    snapRequested = pyqtSignal(object)
    # Dragging by the header: (panel, global QPoint, "start" | "move" | "drop").
    headerDragged = pyqtSignal(object, object, str)
    # Detach: into a window of its own; attach: back into the layout.
    detachRequested = pyqtSignal(object)
    attachRequested = pyqtSignal(object)

    is_terminal = False

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.appearance = profile.appearance
        # Terminal-only state, with neutral values for other panels.
        self.running = False
        self.ended = False
        self.broadcast = False
        self.detached = False  # in a window of its own (detach.py)
        self.setObjectName("termPanel")  # same frame, borders and states
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    # ----- header ---------------------------------------------------------

    def _build_base_header(self, name):
        header = self.header = QWidget(self)
        header.setObjectName("termHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = self.header_layout = QHBoxLayout(header)
        h.setContentsMargins(6, 0, 4, 0)
        h.setSpacing(6)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.name_label = QLabel(name)
        self.name_label.setObjectName("termName")
        self.name_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._drag_start = None
        self._dragging = False
        self._hovered = False
        self._no_drag = ()
        return header

    def header_button(self, icon_name, text, tip, sig=None, name="hdrBtn"):
        """An icon button for the header; sig(self) is emitted on click."""
        b = QToolButton(self.header)
        b.setText(text)  # (the accessible name; only the icon shows)
        b.setIcon(icons.icon(icon_name))
        b.setIconSize(QSize(14, 14))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        b.setToolTip(tip)
        b.setObjectName(name)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if sig is not None:
            b.clicked.connect(lambda: sig.emit(self))
        return b

    def _add_move_button(self, what="panel"):
        self.move_btn = self.header_button(
            "move", "Move", f"Snap layouts: choose where this {what} goes. "
            f"You can also drag the {what} by its header.", self.snapRequested)
        # The exact "left of / right of … another panel" menu, offered in
        # the snap popup.
        self.move_menu = QMenu(self.move_btn)
        self.move_menu.aboutToShow.connect(
            lambda: self.moveMenuRequested.emit(self, self.move_menu))
        self.header_layout.addWidget(self.move_btn)
        return self.move_btn

    def _finish_header(self, tools, no_drag, close_tip="Close"):
        """tools fade in while the mouse is over the header (so the header
        never shifts); no_drag are the widgets a press does not drag from.
        Adds Detach (Attach while detached) and the close button, which is
        always there."""
        self.detach_btn = self.header_button(
            "detach", "Detach", "Detach: move this panel into a window of its own "
            "(it keeps running)")
        self.detach_btn.clicked.connect(self._detach_clicked)
        self.header_layout.addWidget(self.detach_btn)
        self.tools = tuple(tools) + (self.detach_btn,)
        self._tool_effects = []
        for b in self.tools:
            effect = QGraphicsOpacityEffect(b)
            effect.setOpacity(0.0)
            b.setGraphicsEffect(effect)
            self._tool_effects.append(effect)
        self.close_btn = self.header_button("close", "✕", close_tip, self.closeRequested,
                                            "hdrClose")
        self.header_layout.addWidget(self.close_btn)
        self._no_drag = tuple(no_drag)
        self.header.installEventFilter(self)
        self.header.setCursor(Qt.CursorShape.OpenHandCursor)
        # The header shows a hand for dragging; labels in it do not drag,
        # and things you click (buttons, tick box) have their own pointer.
        for w in self._no_drag:
            if not isinstance(w, QToolButton) and w.objectName() != "broadcastBox":
                w.setCursor(Qt.CursorShape.ArrowCursor)

    def _detach_clicked(self):
        (self.attachRequested if self.detached else self.detachRequested).emit(self)

    def set_detached(self, on):
        """The header for a panel in its own window (Attach, always shown;
        no Move: snap layouts and dragging belong to the main window)."""
        self.detached = on
        b = self.detach_btn
        b.setText("Attach" if on else "Detach")
        b.setIcon(icons.icon("attach" if on else "detach"))
        b.setToolTip("Attach: put this panel back into the main window" if on else
                     "Detach: move this panel into a window of its own (it keeps running)")
        self.move_btn.setVisible(not on)
        effect = self._tool_effects[self.tools.index(b)]
        # A fade started just before (the mouse leaves the header as the
        # panel moves) would hide Attach when it ends: stop it first.
        for anim in effect.findChildren(QPropertyAnimation):
            anim.stop()
        effect.setOpacity(1.0 if on or self._hovered else 0.0)

    def _header_event(self, obj, event):
        """Header part of eventFilter: (handled, result)."""
        if obj is not self.header:
            return False, False
        t = event.type()
        if t in (QEvent.Type.Enter, QEvent.Type.Leave):
            self._hovered = t == QEvent.Type.Enter
            self._show_tools(self._hovered)
            return True, False
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                 QEvent.Type.MouseButtonRelease):
            return True, self._header_drag(event)
        return False, False

    def eventFilter(self, obj, event):
        handled, result = self._header_event(obj, event)
        return result if handled else False

    def _header_drag(self, event):
        """Press and drag the header to move the panel (see headerDragged)."""
        t = event.type()
        gpos = event.globalPosition().toPoint()
        if self.detached:
            return False  # dragging places panels in the main window's layout
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if self.header.childAt(event.position().toPoint()) in self._no_drag:
                return False
            self._drag_start = gpos
            self._set_grabbed(True)
            return False
        if t == QEvent.Type.MouseMove and self._drag_start is not None:
            if not self._dragging:
                if (gpos - self._drag_start).manhattanLength() < \
                        QApplication.startDragDistance():
                    return False
                self._dragging = True
                self.header.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.headerDragged.emit(self, gpos, "start")
            self.headerDragged.emit(self, gpos, "move")
            return True
        if t == QEvent.Type.MouseButtonRelease:
            was = self._dragging
            self._drag_start = None
            self._dragging = False
            self._set_grabbed(False)
            if was:
                self.header.setCursor(Qt.CursorShape.OpenHandCursor)
                self.headerDragged.emit(self, gpos, "drop")
                return True
        return False

    def _set_grabbed(self, on):
        """Lights the panel up while it is held by its header."""
        if bool(self.property("grabbed")) == on:
            return
        self.setProperty("grabbed", on)
        for w in (self, self.header, self.name_label):
            w.style().unpolish(w)
            w.style().polish(w)
        if on:
            if getattr(self, "_grab_tint", None) is None:
                self._grab_tint = QWidget(self)
                self._grab_tint.setObjectName("grabTint")
                self._grab_tint.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._grab_tint.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._grab_tint.setGeometry(self.rect())
            self._grab_tint.raise_()
            self._grab_tint.show()
        elif getattr(self, "_grab_tint", None) is not None:
            self._grab_tint.hide()

    def _show_tools(self, on):
        """Fades the header's tool buttons in or out (Attach stays shown)."""
        for b, effect in zip(self.tools, self._tool_effects):
            if self.detached and b is self.detach_btn:
                continue
            anim = QPropertyAnimation(effect, b"opacity", effect)
            anim.setDuration(120)
            anim.setEndValue(1.0 if on else 0.0)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _set_dot(self, text, state):
        """The status dot: state is ok / connecting / off / error."""
        self.status_dot.setToolTip(text)
        self.status_dot.setProperty("state", state)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    # ----- active panel -----------------------------------------------------

    def set_active(self, active):
        self.setProperty("active", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self._update_active_line()

    def _update_active_line(self):
        """A thin line over the active panel's header, in its colour."""
        color = theme.accent_for(self.appearance) if self.property("active") else "transparent"
        self.header.setStyleSheet(f"#termHeader {{ border-top: 2px solid {color}; }}")

    # ----- what the window may call on any panel ----------------------------

    def apply_appearance(self, appearance):
        self.appearance = appearance
        self._update_active_line()

    def set_broadcast_mode(self, on, include=False):
        pass

    def set_broadcast(self, on):
        pass

    def terminate(self):
        pass

    def zoom(self, step):
        pass

    def after_reparent(self):
        """Called after the panel moved into another window (detach, attach)."""

    def focus_terminal(self):
        """Focuses the panel's main widget (the name is kept from terminals)."""
        self.setFocus()
