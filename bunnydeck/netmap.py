"""The Network Scan window: a live map of the devices on a local network.

The router sits in the middle like a hub, every device found by nmap
(netscan.py) pops up on the rings around it, coloured and drawn by what it
seems to be. A radar sweep turns while the scan runs. Click a device for its
details, double-click an SSH server to connect to it.
"""

import math
import socket

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QUrl,
    QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDesktopServices, QFont, QGuiApplication, QIcon,
    QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGraphicsItem, QGraphicsObject, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QProgressBar,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from . import netscan
from .netscan import Host

COLORS = {
    netscan.KIND_SELF: "#c792ea",
    netscan.KIND_ROUTER: "#f4a340",
    netscan.KIND_SERVER: "#3ddc84",
    netscan.KIND_HYPERVISOR: "#2ec4b6",
    netscan.KIND_NAS: "#4cc9f0",
    netscan.KIND_PRINTER: "#e9c46a",
    netscan.KIND_WINDOWS: "#5b8def",
    netscan.KIND_CAMERA: "#ef476f",
    netscan.KIND_MEDIA: "#ff8c42",
    netscan.KIND_IOT: "#a3e635",
    netscan.KIND_WEB: "#8ab4f8",
    netscan.KIND_PHONE: "#b392f0",
    netscan.KIND_UNKNOWN: "#7d8590",
}

BG_INNER = QColor("#0f1a2b")
BG_OUTER = QColor("#06080c")
RING = QColor(91, 141, 239, 38)
SWEEP = QColor(61, 220, 132)

FIRST_RING = 200
RING_STEP = 155
NODE_SPACING = 140


def kind_color(kind):
    return QColor(COLORS.get(kind, COLORS[netscan.KIND_UNKNOWN]))


# ----- device glyphs --------------------------------------------------------


def draw_glyph(p, kind, s, color=QColor("white")):
    """Draws the line icon for a device kind, centred on (0, 0), about s wide."""
    pen = QPen(color, max(1.4, s / 13))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    h = s / 2
    K = netscan
    if kind == K.KIND_SELF:  # a bunny head
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, h * 0.30), h * 0.52, h * 0.46)
        for side in (-1, 1):
            p.save()
            p.translate(side * h * 0.26, -h * 0.30)
            p.rotate(side * 12)
            p.drawEllipse(QPointF(0, 0), h * 0.17, h * 0.55)
            p.restore()
        p.setBrush(QColor("#1b1030"))
        for side in (-1, 1):
            p.drawEllipse(QPointF(side * h * 0.20, h * 0.25), h * 0.07, h * 0.08)
    elif kind == K.KIND_ROUTER:
        p.drawRoundedRect(QRectF(-h * 0.85, h * 0.05, h * 1.7, h * 0.65), 3, 3)
        p.drawLine(QPointF(-h * 0.5, h * 0.05), QPointF(-h * 0.7, -h * 0.75))
        p.drawLine(QPointF(h * 0.5, h * 0.05), QPointF(h * 0.7, -h * 0.75))
        for x in (-0.45, -0.15, 0.15):
            p.drawPoint(QPointF(h * x, h * 0.38))
        p.drawArc(QRectF(-h * 0.35, -h * 0.75, h * 0.7, h * 0.7), 30 * 16, 120 * 16)
    elif kind in (K.KIND_SERVER, K.KIND_HYPERVISOR):
        for i in range(3):
            y = -h * 0.8 + i * h * 0.56
            p.drawRoundedRect(QRectF(-h * 0.75, y, h * 1.5, h * 0.44), 2, 2)
            p.drawPoint(QPointF(-h * 0.45, y + h * 0.22))
            if kind == K.KIND_HYPERVISOR:
                p.drawLine(QPointF(0, y + h * 0.22), QPointF(h * 0.5, y + h * 0.22))
    elif kind == K.KIND_NAS:
        p.drawEllipse(QRectF(-h * 0.7, -h * 0.8, h * 1.4, h * 0.45))
        p.drawLine(QPointF(-h * 0.7, -h * 0.58), QPointF(-h * 0.7, h * 0.58))
        p.drawLine(QPointF(h * 0.7, -h * 0.58), QPointF(h * 0.7, h * 0.58))
        p.drawArc(QRectF(-h * 0.7, h * 0.35, h * 1.4, h * 0.45), 180 * 16, 180 * 16)
        p.drawArc(QRectF(-h * 0.7, -h * 0.1, h * 1.4, h * 0.45), 180 * 16, 180 * 16)
    elif kind == K.KIND_PRINTER:
        p.drawRect(QRectF(-h * 0.5, -h * 0.8, h, h * 0.5))
        p.drawRoundedRect(QRectF(-h * 0.85, -h * 0.3, h * 1.7, h * 0.75), 2, 2)
        p.drawRect(QRectF(-h * 0.5, h * 0.2, h, h * 0.6))
    elif kind == K.KIND_WINDOWS:
        p.drawRoundedRect(QRectF(-h * 0.85, -h * 0.7, h * 1.7, h * 1.1), 2, 2)
        p.drawLine(QPointF(0, h * 0.4), QPointF(0, h * 0.75))
        p.drawLine(QPointF(-h * 0.4, h * 0.75), QPointF(h * 0.4, h * 0.75))
    elif kind == K.KIND_CAMERA:
        p.drawRoundedRect(QRectF(-h * 0.85, -h * 0.45, h * 1.2, h * 0.9), 3, 3)
        p.drawEllipse(QPointF(-h * 0.25, 0), h * 0.25, h * 0.25)
        path = QPainterPath(QPointF(h * 0.35, -h * 0.2))
        path.lineTo(QPointF(h * 0.85, -h * 0.45))
        path.lineTo(QPointF(h * 0.85, h * 0.45))
        path.lineTo(QPointF(h * 0.35, h * 0.2))
        p.drawPath(path)
    elif kind == K.KIND_MEDIA:
        p.drawEllipse(QPointF(0, 0), h * 0.85, h * 0.85)
        path = QPainterPath(QPointF(-h * 0.25, -h * 0.4))
        path.lineTo(QPointF(h * 0.45, 0))
        path.lineTo(QPointF(-h * 0.25, h * 0.4))
        path.closeSubpath()
        p.setBrush(color)
        p.drawPath(path)
    elif kind == K.KIND_IOT:
        p.drawEllipse(QPointF(0, -h * 0.2), h * 0.5, h * 0.5)
        p.drawLine(QPointF(-h * 0.25, h * 0.5), QPointF(h * 0.25, h * 0.5))
        p.drawLine(QPointF(-h * 0.18, h * 0.75), QPointF(h * 0.18, h * 0.75))
    elif kind == K.KIND_WEB:
        p.drawEllipse(QPointF(0, 0), h * 0.8, h * 0.8)
        p.drawEllipse(QPointF(0, 0), h * 0.35, h * 0.8)
        p.drawLine(QPointF(-h * 0.8, 0), QPointF(h * 0.8, 0))
    elif kind == K.KIND_PHONE:
        p.drawRoundedRect(QRectF(-h * 0.45, -h * 0.85, h * 0.9, h * 1.7), 4, 4)
        p.drawPoint(QPointF(0, h * 0.6))
    else:
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(s * 0.8))
        p.setFont(f)
        p.drawText(QRectF(-h, -h, s, s), Qt.AlignmentFlag.AlignCenter, "?")


def kind_icon(kind, size=28):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(size / 2, size / 2)
    color = kind_color(kind)
    p.setPen(QPen(color, 1.5))
    p.setBrush(QColor("#0f1622"))
    p.drawEllipse(QPointF(0, 0), size / 2 - 1, size / 2 - 1)
    draw_glyph(p, kind, size * 0.5, color)
    p.end()
    return QIcon(pm)


# ----- map items ------------------------------------------------------------


class DeviceNode(QGraphicsObject):
    """One device on the map: a glowing disc with its icon, name, IP and the
    first few open ports."""

    activated = pyqtSignal(object)  # Host (double-click)

    LABEL_W = 150

    def __init__(self, host, view):
        super().__init__()
        self.host = host
        self.view = view
        self.hovered = False
        self.ripple = 0.0
        self.radius = 34 if self.is_hub_candidate() else 26
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.tooltip())
        self._pos_anim = None

    def is_hub_candidate(self):
        return self.host.is_self or self.host.is_gateway

    def tooltip(self):
        h = self.host
        lines = [f"<b>{h.name}</b> — {netscan.KIND_LABELS[h.kind]}", h.ip]
        if h.mac:
            lines.append(f"{h.mac} {h.vendor}".strip())
        if h.ports:
            lines.append("Open: " + ", ".join(str(p.number) for p in h.ports))
        return "<br>".join(lines)

    def refresh(self):
        self.prepareGeometryChange()
        self.setToolTip(self.tooltip())
        self.update()

    # -- animation --

    def pop_in(self):
        self.setScale(0.01)
        anim = QPropertyAnimation(self, b"scale", self)
        anim.setDuration(550)
        anim.setStartValue(0.01)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        ripple = QVariantAnimation(self)
        ripple.setDuration(1100)
        ripple.setStartValue(0.0)
        ripple.setEndValue(1.0)
        ripple.valueChanged.connect(self._set_ripple)
        ripple.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _set_ripple(self, v):
        self.ripple = v
        self.update()

    def move_to(self, point, animate=True):
        if self._pos_anim is not None:
            self._pos_anim.stop()
        if not animate:
            self.setPos(point)
            return
        anim = self._pos_anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(650)
        anim.setStartValue(self.pos())
        anim.setEndValue(point)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()

    # -- painting --

    def boundingRect(self):
        r = self.radius + 46  # glow and ripple
        return QRectF(-max(r, self.LABEL_W / 2), -r, 2 * max(r, self.LABEL_W / 2),
                      r + self.radius + 66)

    def shape(self):
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.radius + 4, self.radius + 4)
        path.addRect(QRectF(-self.LABEL_W / 2, self.radius + 4, self.LABEL_W, 46))
        return path

    def paint(self, p, option, widget=None):
        h = self.host
        kind = h.kind
        color = kind_color(kind)
        r = self.radius
        selected = self.isSelected()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.ripple and self.ripple < 1:
            c = QColor(color)
            c.setAlphaF(0.7 * (1 - self.ripple))
            p.setPen(QPen(c, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = r + 40 * self.ripple
            p.drawEllipse(QPointF(0, 0), rr, rr)

        glow = QRadialGradient(QPointF(0, 0), r + 22)
        g = QColor(color)
        g.setAlpha(110 if (selected or self.hovered) else 60)
        glow.setColorAt(0.55, g)
        g.setAlpha(0)
        glow.setColorAt(1.0, g)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(0, 0), r + 22, r + 22)

        fill = QRadialGradient(QPointF(-r * 0.3, -r * 0.4), r * 1.4)
        fill.setColorAt(0, QColor("#1c2738"))
        fill.setColorAt(1, QColor("#0b111b"))
        p.setBrush(QBrush(fill))
        p.setPen(QPen(color.lighter(130) if self.hovered else color, 3 if selected else 2.2))
        p.drawEllipse(QPointF(0, 0), r, r)
        if selected:
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(0, 0), r + 7, r + 7)

        if self.view.scanning_ports and not h.ports_scanned:
            arc = QPen(color.lighter(150), 2.5)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(arc)
            p.setBrush(Qt.BrushStyle.NoBrush)
            start = int(-self.view.phase * 360 * 16 * 1.5) % (360 * 16)
            p.drawArc(QRectF(-r - 4, -r - 4, 2 * r + 8, 2 * r + 8), start, 90 * 16)

        p.save()
        draw_glyph(p, kind, r * 0.95, QColor("#f2f4f8"))
        p.restore()

        if h.ssh_port:  # a small "terminal" badge: you can SSH into it
            bx, by = r * 0.72, -r * 0.72
            p.setPen(QPen(QColor("#0b111b"), 2))
            p.setBrush(QColor(COLORS[netscan.KIND_SERVER]))
            p.drawEllipse(QPointF(bx, by), 9, 9)
            f = QFont()
            f.setPixelSize(9)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#0b111b"))
            p.drawText(QRectF(bx - 9, by - 9, 18, 18), Qt.AlignmentFlag.AlignCenter, ">_")

        # Labels
        y = r + 8
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        title = "You" if h.is_self and not h.hostname else h.name
        if h.is_self and h.hostname:
            title = f"{h.name} (you)"
        p.setPen(QColor("#e6e9ef"))
        rect = QRectF(-self.LABEL_W / 2, y, self.LABEL_W, 17)
        p.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, self.LABEL_W))
        y += 17
        if title != h.ip:
            f.setPixelSize(11)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor("#8b93a1"))
            p.drawText(QRectF(-self.LABEL_W / 2, y, self.LABEL_W, 14),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, h.ip)
            y += 16
        if h.ports:
            self._paint_chips(p, y, color)

    def _paint_chips(self, p, y, color):
        nums = [str(n) for n in self.host.port_numbers]
        shown = nums[:4]
        if len(nums) > 4:
            shown.append(f"+{len(nums) - 4}")
        f = QFont()
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        widths = [fm.horizontalAdvance(t) + 10 for t in shown]
        total = sum(widths) + 4 * (len(widths) - 1)
        x = -total / 2
        bg = QColor(color)
        bg.setAlpha(45)
        for t, w in zip(shown, widths):
            p.setPen(QPen(color, 1))
            p.setBrush(bg)
            rect = QRectF(x, y, w, 15)
            p.drawRoundedRect(rect, 7, 7)
            p.setPen(QColor("#e6e9ef"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, t)
            x += w + 4

    # -- interaction --

    def hoverEnterEvent(self, event):
        self.hovered = True
        self.setZValue(2)
        self.update()

    def hoverLeaveEvent(self, event):
        self.hovered = False
        self.setZValue(1)
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.activated.emit(self.host)


class NetworkMapView(QGraphicsView):
    """The map: radar background, links from the hub, device nodes."""

    hostActivated = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("netMap")
        self.scene_ = QGraphicsScene(self)
        self.scene_.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(360, 300)
        self.nodes = {}  # ip -> DeviceNode
        self.hub = None
        self.rings = 1
        self.sweeping = False
        self.scanning_ports = False
        self.phase = 0.0
        self.message = ""
        self.user_zoomed = False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._fit_timer = QTimer(self, singleShot=True, interval=120)
        self._fit_timer.timeout.connect(self.fit)
        self.scene_.setSceneRect(QRectF(-2000, -2000, 4000, 4000))

    # -- state --

    def clear(self):
        self.scene_.clear()
        self.nodes = {}
        self.hub = None
        self.rings = 1
        self.user_zoomed = False
        self.viewport().update()

    def set_scanning(self, on, ports=False):
        self.sweeping = on
        self.scanning_ports = on and ports
        if on:
            self._timer.start()
        else:
            self._timer.stop()
        self.viewport().update()

    def set_message(self, text):
        self.message = text
        self.viewport().update()

    def _tick(self):
        self.phase = (self.phase + 0.033 / 3.0) % 1.0  # one turn in 3 s
        self.viewport().update()

    def add_host(self, host):
        if host.ip in self.nodes:
            self.update_host(host)
            return self.nodes[host.ip]
        node = DeviceNode(host, self)
        node.activated.connect(self.hostActivated)
        node.setZValue(1)
        self.scene_.addItem(node)
        self.nodes[host.ip] = node
        self.relayout(new=node)
        node.pop_in()
        return node

    def update_host(self, host):
        node = self.nodes.get(host.ip)
        if node is not None:
            node.host = host
            node.refresh()

    def select(self, ip):
        node = self.nodes.get(ip)
        self.scene_.clearSelection()
        if node is not None:
            node.setSelected(True)
            if not self.viewport().rect().contains(self.mapFromScene(node.pos())):
                self.centerOn(node)

    def selected_host(self):
        items = self.scene_.selectedItems()
        return items[0].host if items else None

    # -- layout --

    def _pick_hub(self):
        nodes = list(self.nodes.values())
        for test in (lambda n: n.host.is_gateway, lambda n: n.host.is_self):
            for n in nodes:
                if test(n):
                    return n
        return None

    def relayout(self, new=None):
        """Hub in the middle, the others by IP address on rings around it."""
        self.hub = self._pick_hub()
        others = sorted((n for n in self.nodes.values() if n is not self.hub),
                        key=lambda n: n.host.sort_key)
        if self.hub is not None:
            self.hub.move_to(QPointF(0, 0), animate=self.hub is not new)
        ring = 0
        i = 0
        rings = 0
        while i < len(others):
            radius = FIRST_RING + ring * RING_STEP
            capacity = max(6, int(2 * math.pi * radius / NODE_SPACING))
            batch = others[i:i + capacity]
            step = 2 * math.pi / len(batch)
            offset = -math.pi / 2 + (step / 2 if ring % 2 else 0)
            for k, node in enumerate(batch):
                a = offset + k * step
                target = QPointF(radius * math.cos(a), radius * math.sin(a))
                if node is new:
                    node.setPos(target)
                else:
                    node.move_to(target)
            i += capacity
            ring += 1
            rings = ring
        self.rings = max(1, rings)
        if not self.user_zoomed:
            self._fit_timer.start()

    def fit(self):
        extent = FIRST_RING + (self.rings - 1) * RING_STEP + 110
        self.fitInView(QRectF(-extent, -extent, 2 * extent, 2 * extent),
                       Qt.AspectRatioMode.KeepAspectRatio)

    def reset_zoom(self):
        self.user_zoomed = False
        self.fit()

    # -- painting --

    def drawBackground(self, p, rect):
        p.save()
        p.resetTransform()
        vp = self.viewport().rect()
        center = self.mapFromScene(QPointF(0, 0))
        grad = QRadialGradient(QPointF(center), max(vp.width(), vp.height()) * 0.8)
        grad.setColorAt(0, BG_INNER)
        grad.setColorAt(1, BG_OUTER)
        p.fillRect(vp, QBrush(grad))
        p.restore()

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Rings and crosshair
        outer = FIRST_RING + (self.rings - 1) * RING_STEP
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(self.rings + 1):
            radius = FIRST_RING * 0.5 if k == 0 else FIRST_RING + (k - 1) * RING_STEP
            p.setPen(QPen(RING, 1.2, Qt.PenStyle.DashLine if k == 0 else Qt.PenStyle.SolidLine))
            p.drawEllipse(QPointF(0, 0), radius, radius)
        p.setPen(QPen(QColor(91, 141, 239, 22), 1))
        p.drawLine(QPointF(-outer - 60, 0), QPointF(outer + 60, 0))
        p.drawLine(QPointF(0, -outer - 60), QPointF(0, outer + 60))

        # Radar sweep
        if self.sweeping:
            angle = -self.phase * 360
            cone = QConicalGradient(QPointF(0, 0), angle)
            c = QColor(SWEEP)
            c.setAlpha(90)
            cone.setColorAt(0.0, c)
            c.setAlpha(0)
            cone.setColorAt(0.18, c)
            cone.setColorAt(1.0, c)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(cone))
            p.drawEllipse(QPointF(0, 0), outer + 60, outer + 60)
            rad = math.radians(angle)
            c.setAlpha(140)
            p.setPen(QPen(c, 1.5))
            p.drawLine(QPointF(0, 0), QPointF((outer + 60) * math.cos(rad),
                                              -(outer + 60) * math.sin(rad)))

        # Links from the hub (or, without router and this computer, the centre)
        if self.nodes:
            hub_pos = self.hub.pos() if self.hub is not None else QPointF(0, 0)
            if self.hub is None:
                p.setPen(QPen(QColor(91, 141, 239, 120), 1.5))
                p.setBrush(QColor(91, 141, 239, 40))
                p.drawEllipse(QPointF(0, 0), 10, 10)
            for node in self.nodes.values():
                if node is self.hub:
                    continue
                color = kind_color(node.host.kind)
                color.setAlpha(150 if node.isSelected() or node.hovered else 70)
                p.setPen(QPen(color, 2 if node.isSelected() else 1.3))
                p.drawLine(hub_pos, node.pos())
                if self.scanning_ports and not node.host.ports_scanned:
                    # a packet travelling to the device being probed
                    t = (self.phase * 3 + (node.host.sort_key % 97) / 97) % 1.0
                    pt = hub_pos + (node.pos() - hub_pos) * t
                    color.setAlpha(230)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color)
                    p.drawEllipse(pt, 3.5, 3.5)

    def drawForeground(self, p, rect):
        if not self.message:
            return
        p.save()
        p.resetTransform()
        vp = QRectF(self.viewport().rect())
        f = QFont()
        f.setPixelSize(15)
        p.setFont(f)
        p.setPen(QColor("#9aa3b2"))
        box = vp.adjusted(40, 0, -40, 0)
        if self.nodes:
            box = QRectF(vp.left() + 20, vp.bottom() - 60, vp.width() - 40, 40)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        p.restore()

    # -- zoom --

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        scale = self.transform().m11() * factor
        if 0.08 < scale < 4:
            self.scale(factor, factor)
            self.user_zoomed = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.user_zoomed:
            self.fit()


# ----- the window -------------------------------------------------------------


class NetworkScanWindow(QWidget):
    """Scan a network with nmap and explore it on the map.

    connectRequested(host, port, name) asks the main window to open an SSH
    connection (through its Add SSH dialog)."""

    connectRequested = pyqtSignal(str, int, str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("netScanRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Network Scan — Bunny Deck")
        self.resize(1180, 760)
        self.scanner = netscan.NetworkScanner(self)
        self.scanner.hostFound.connect(self._on_host_found)
        self.scanner.hostUpdated.connect(self._on_host_updated)
        self.scanner.progress.connect(self._on_progress)
        self.scanner.finished.connect(self._on_finished)
        self.items = {}  # ip -> QListWidgetItem

        # Top bar
        top = QHBoxLayout()
        top.setContentsMargins(10, 8, 10, 4)
        top.addWidget(QLabel("Network"))
        self.target = QComboBox()
        self.target.setEditable(True)
        self.target.setMinimumWidth(200)
        self.target.setToolTip("The network to scan, e.g. 192.168.1.0/24")
        top.addWidget(self.target)
        self.versions = QCheckBox("Detect service versions (slower)")
        self.versions.setToolTip("Asks each open port what software answers (nmap -sV)")
        top.addWidget(self.versions)
        top.addStretch(1)
        self.fit_btn = QPushButton("Fit")
        self.fit_btn.setToolTip("Show the whole map (wheel zooms, drag moves)")
        top.addWidget(self.fit_btn)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setObjectName("primaryBtn")
        self.scan_btn.setDefault(True)
        top.addWidget(self.scan_btn)

        self.progress = QProgressBar()
        self.progress.setObjectName("scanProgress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(3)
        self.status = QLabel()
        self.status.setObjectName("dimLabel")

        # nmap missing
        self.missing = QFrame()
        self.missing.setObjectName("nmapMissing")
        mh = QHBoxLayout(self.missing)
        mh.setContentsMargins(12, 8, 12, 8)
        self.missing_text = QLabel()
        self.missing_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mh.addWidget(self.missing_text, 1)
        self.copy_install = QPushButton("Copy command")
        self.recheck = QPushButton("Check again")
        mh.addWidget(self.copy_install)
        mh.addWidget(self.recheck)

        # Map + side panel
        self.map = NetworkMapView()
        side = QWidget()
        side.setObjectName("netSide")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(10, 10, 10, 10)
        self.count = QLabel("Devices")
        self.count.setObjectName("netHeading")
        sv.addWidget(self.count)
        self.list = QListWidget()
        self.list.setObjectName("netList")
        self.list.setIconSize(QSize(26, 26))
        sv.addWidget(self.list, 3)
        self.details = QLabel()
        self.details.setObjectName("netDetails")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sv.addWidget(self.details, 2)
        row = QHBoxLayout()
        self.ssh_btn = QPushButton("Connect with SSH…")
        self.ssh_btn.setObjectName("sshBtn")
        self.web_btn = QPushButton("Open in browser")
        self.copy_btn = QPushButton("Copy IP")
        for b in (self.ssh_btn, self.web_btn, self.copy_btn):
            row.addWidget(b)
        sv.addLayout(row)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.map)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setSizes([830, 340])

        hint = QLabel("Only scan networks you own or are allowed to scan. nmap runs as your "
                      "user (no root), with TCP connect scans.")
        hint.setObjectName("dimLabel")
        hint.setContentsMargins(10, 2, 10, 6)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addLayout(top)
        v.addWidget(self.missing)
        v.addWidget(self.progress)
        sv_status = QHBoxLayout()
        sv_status.setContentsMargins(10, 4, 10, 4)
        sv_status.addWidget(self.status)
        v.addLayout(sv_status)
        v.addWidget(split, 1)
        v.addWidget(hint)

        self.scan_btn.clicked.connect(self.toggle_scan)
        self.target.lineEdit().returnPressed.connect(self.toggle_scan)
        self.fit_btn.clicked.connect(self.map.reset_zoom)
        self.recheck.clicked.connect(self.check_nmap)
        self.copy_install.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(netscan.install_hint()))
        self.list.currentItemChanged.connect(self._on_list_current)
        self.list.itemDoubleClicked.connect(
            lambda item: self.connect_host(self._host(item.data(Qt.ItemDataRole.UserRole))))
        self.map.scene_.selectionChanged.connect(self._on_map_selection)
        self.map.hostActivated.connect(self.connect_host)
        self.ssh_btn.clicked.connect(lambda: self.connect_host(self.current_host()))
        self.web_btn.clicked.connect(self._open_web)
        self.copy_btn.clicked.connect(self._copy_ip)

        self.fill_networks()
        self.check_nmap()
        self.show_details(None)

    # -- setup --

    def fill_networks(self):
        self.target.clear()
        for net in netscan.local_networks():
            self.target.addItem(str(net.network), str(net.network))
            self.target.setItemData(self.target.count() - 1, net.label,
                                    Qt.ItemDataRole.ToolTipRole)

    def check_nmap(self):
        ok = netscan.nmap_path() is not None
        self.missing.setVisible(not ok)
        self.scan_btn.setEnabled(ok)
        if ok:
            self.map.set_message("" if self.map.nodes else
                                 "Press Scan to map the devices on your network.")
        else:
            hint = netscan.install_hint()
            self.missing_text.setText(f"<b>nmap is not installed.</b> Install it with "
                                      f"<code>{hint}</code>, then press Check again.")
            self.map.set_message("The network scan needs nmap.")
        return ok

    # -- scanning --

    def toggle_scan(self):
        if self.scanner.running:
            self.scanner.stop()
            return
        self.start_scan(self.target.currentText())

    def start_scan(self, text):
        if not self.check_nmap():
            return False
        try:
            network = netscan.parse_target(text)
        except ValueError as e:
            self.status.setText(str(e))
            self.status.setObjectName("errorLabel")
            self._restyle(self.status)
            return False
        self.status.setObjectName("dimLabel")
        self._restyle(self.status)
        self.target.setEditText(str(network))
        self.map.clear()
        self.list.clear()
        self.items = {}
        self.show_details(None)
        self.progress.setValue(0)
        self.map.set_message("")
        if not self.scanner.start(network, self.versions.isChecked()):
            return False
        self.scan_btn.setText("Stop")
        self.target.setEnabled(False)
        self.versions.setEnabled(False)
        self.map.set_scanning(True)
        # This computer and the router are known before nmap finds them.
        for ip in sorted(self.scanner.self_ips):
            self.scanner._on_host(Host(ip=ip, hostname=_own_hostname()))
        if self.scanner.gateway:
            self.scanner._on_host(Host(ip=self.scanner.gateway))
        return True

    def _on_host_found(self, host):
        self.map.add_host(host)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, host.ip)
        self.items[host.ip] = item
        self._fill_item(item, host)
        # keep the list in IP order
        row = 0
        for row in range(self.list.count() + 1):
            if row == self.list.count():
                break
            other = self._host(self.list.item(row).data(Qt.ItemDataRole.UserRole))
            if other is not None and other.sort_key > host.sort_key:
                break
        self.list.insertItem(row, item)
        self._update_count()

    def _on_host_updated(self, host):
        self.map.update_host(host)
        item = self.items.get(host.ip)
        if item is not None:
            self._fill_item(item, host)
        if self.current_host() is host:
            self.show_details(host)

    def _fill_item(self, item, host):
        ports = ", ".join(str(n) for n in host.port_numbers) or (
            "no open ports" if host.ports_scanned else "…")
        name = host.name + (" (you)" if host.is_self else "")
        item.setText(f"{name}\n{host.ip}  ·  {ports}" if name != host.ip
                     else f"{host.ip}\n{ports}")
        item.setIcon(kind_icon(host.kind))
        item.setToolTip(netscan.KIND_LABELS[host.kind])

    def _on_progress(self, text, percent):
        self.status.setText(text)
        if percent >= 0:
            self.progress.setValue(int(percent * 10))
        self.map.set_scanning(self.scanner.running, ports=self.scanner.phase == "ports")

    def _on_finished(self, error):
        self.scan_btn.setText("Scan")
        self.target.setEnabled(True)
        self.versions.setEnabled(True)
        self.map.set_scanning(False)
        if error:
            self.status.setText(error)
        self.progress.setValue(1000 if not error else self.progress.value())
        if not self.map.nodes:
            self.map.set_message(error or "No devices found.")

    def _update_count(self):
        n = len(self.items)
        self.count.setText(f"Devices ({n})")

    # -- selection and details --

    def _host(self, ip):
        return self.scanner.hosts.get(ip)

    def current_host(self):
        item = self.list.currentItem()
        return self._host(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _on_list_current(self, item, _prev):
        host = self._host(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if host is not None and self.map.selected_host() is not host:
            self.map.select(host.ip)
        self.show_details(host)

    def _on_map_selection(self):
        host = self.map.selected_host()
        if host is not None and host.ip in self.items:
            self.list.setCurrentItem(self.items[host.ip])

    def show_details(self, host):
        self.ssh_btn.setEnabled(host is not None and not host.is_self)
        self.web_btn.setEnabled(bool(host and host.web_urls))
        self.copy_btn.setEnabled(host is not None)
        if host is None:
            self.details.setText("<span style='color:#6b7280'>Click a device on the map "
                                 "to see its open ports. Double-click an SSH server to "
                                 "connect.</span>")
            return
        color = COLORS.get(host.kind)
        dim = "color:#8b93a1"
        html = (f"<div style='font-size:15px'><b>{_esc(host.name)}</b></div>"
                f"<div style='color:{color}; margin-bottom:8px'>"
                f"{netscan.KIND_LABELS[host.kind]}</div>"
                f"<div><b>IP</b>&nbsp; {host.ip}</div>")
        if host.hostname and host.hostname != host.ip:
            html += f"<div><b>Name</b>&nbsp; {_esc(host.hostname)}</div>"
        if host.mac:
            vendor = f" <span style='{dim}'>({_esc(host.vendor)})</span>" if host.vendor else ""
            html += f"<div><b>MAC</b>&nbsp; {host.mac}{vendor}</div>"
        if host.seen_by == "arp":
            html += (f"<div style='{dim}'>Ignores pings; answered on the local "
                     "network.</div>")
        html += "<div style='margin-top:10px'><b>Open ports</b></div>"
        if not host.ports_scanned:
            html += f"<div style='{dim}'>checking…</div>"
        elif not host.ports:
            html += f"<div style='{dim}'>none of the common ports</div>"
        else:
            html += "<table cellspacing='0' cellpadding='2'>" + "".join(
                f"<tr><td style='color:{color}'><b>{p.number}</b></td>"
                f"<td>&nbsp;{_esc(p.label)}</td></tr>" for p in host.ports) + "</table>"
        self.details.setText(html)
        if host.ssh_port:
            self.ssh_btn.setText(f"Connect with SSH (port {host.ssh_port})…")
        else:
            self.ssh_btn.setText("Connect with SSH…")

    # -- actions --

    def connect_host(self, host):
        if host is None or host.is_self:
            return
        name = host.name if host.name != host.ip else ""
        self.connectRequested.emit(host.ip, host.ssh_port or 22, name)

    def _open_web(self):
        host = self.current_host()
        if host is None or not host.web_urls:
            return
        urls = host.web_urls
        if len(urls) == 1:
            QDesktopServices.openUrl(QUrl(urls[0]))
            return
        menu = QMenu(self)
        for u in urls:
            menu.addAction(u).triggered.connect(
                lambda _=False, u=u: QDesktopServices.openUrl(QUrl(u)))
        menu.exec(self.web_btn.mapToGlobal(self.web_btn.rect().bottomLeft()))

    def _copy_ip(self):
        host = self.current_host()
        if host is not None:
            QGuiApplication.clipboard().setText(host.ip)

    def _restyle(self, w):
        w.style().unpolish(w)
        w.style().polish(w)

    def closeEvent(self, event):
        self.scanner.stop()
        super().closeEvent(event)


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _own_hostname():
    return socket.gethostname()
