#!/usr/bin/env python3
"""Draws data/backgrounds/bunny-skate.png: a skate-style badge (bunny head
with the prompt for a face, ring lettering) for a terminal
background. Transparent and dim, so it suits any background colour and text
stays readable. Use it with the "Center (original size)" image mode.

    python3 make_bunny_skate.py [--bright]   (--bright: full-strength preview)
"""

import math
import os
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath, QPen,
)

SIZE = 560
STRENGTH = 0.20  # opacity of the whole badge in the terminal

INK = QColor("#e8eaed")
FILL = QColor("#15171b")
GREEN = QColor("#8fc970")
PINK = QColor("#f2a7b8")
RED = QColor("#d9453f")
GREY = QColor("#9aa0a8")


def pen(color, width):
    p = QPen(color, width)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    return p


def arc_text(p, text, radius, center_deg, font, top):
    """Letters along a circle around the origin; top: read clockwise above,
    otherwise counter-clockwise below (both upright)."""
    fm = QFontMetricsF(font)
    p.setFont(font)
    widths = [fm.horizontalAdvance(c) for c in text]
    spacing = 4.0
    total = sum(widths) + spacing * (len(text) - 1)
    angle = math.radians(center_deg) - (total / radius / 2 if top else -total / radius / 2)
    for c, w in zip(text, widths):
        half = (w / 2) / radius
        a = angle + (half if top else -half)
        p.save()
        p.translate(math.cos(a) * radius, math.sin(a) * radius)
        p.rotate(math.degrees(a) + (90 if top else -90))
        p.drawText(QPointF(-w / 2, fm.capHeight() / 2), c)
        p.restore()
        step = (w + spacing) / radius
        angle += step if top else -step


def bunny(p):
    p.setBrush(FILL)
    p.setPen(pen(INK, 8))
    # left ear upright
    p.save()
    p.translate(-46, -112)
    p.rotate(-10)
    p.drawEllipse(QRectF(-24, -78, 48, 156))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(RED)
    p.drawEllipse(QRectF(-10, -58, 20, 112))
    p.restore()
    # right ear, folded over
    ear = QPainterPath(QPointF(22, -60))
    ear.cubicTo(18, -150, 40, -196, 82, -200)
    ear.cubicTo(122, -204, 150, -176, 156, -146)
    ear.cubicTo(160, -126, 140, -116, 126, -132)
    ear.cubicTo(110, -150, 92, -156, 80, -140)
    ear.cubicTo(66, -122, 66, -92, 72, -60)
    ear.closeSubpath()
    p.setBrush(FILL)
    p.setPen(pen(INK, 8))
    p.drawPath(ear)
    inner = QPainterPath(QPointF(42, -70))
    inner.cubicTo(40, -140, 56, -176, 84, -180)
    inner.cubicTo(106, -182, 122, -170, 132, -156)
    inner.cubicTo(116, -168, 96, -170, 80, -160)
    inner.cubicTo(60, -146, 54, -110, 56, -70)
    inner.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(RED)
    p.drawPath(inner)
    # head
    p.setBrush(FILL)
    p.setPen(pen(INK, 8))
    p.drawEllipse(QRectF(-104, -92, 208, 176))
    # face: the prompt ">_" for eyes, pink nose, buck teeth, whiskers
    p.setPen(pen(GREEN, 12))
    p.setBrush(Qt.BrushStyle.NoBrush)
    chevron = QPainterPath(QPointF(-62, -38))
    chevron.lineTo(-34, -16)
    chevron.lineTo(-62, 6)
    p.drawPath(chevron)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(INK)
    p.drawRoundedRect(QRectF(22, -4, 46, 12), 6, 6)
    nose = QPainterPath(QPointF(-14, 18))
    nose.lineTo(14, 18)
    nose.lineTo(0, 32)
    nose.closeSubpath()
    p.setPen(pen(PINK, 5))
    p.setBrush(PINK)
    p.drawPath(nose)
    p.setPen(pen(INK, 5))
    p.setBrush(INK)
    p.drawRoundedRect(QRectF(-13, 38, 26, 22), 4, 4)
    p.drawLine(QPointF(0, 38), QPointF(0, 58))
    p.setPen(pen(GREY, 4))
    for side in (-1, 1):
        for dy, slope in ((24, -8), (36, 4)):
            p.drawLine(QPointF(side * 34, dy), QPointF(side * 92, dy + slope))


def draw(bright):
    img = QImage(SIZE * 2, SIZE * 2, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2, 2)
    p.translate(SIZE / 2, SIZE / 2)
    # Each element is painted opaque on a layer, then the layer is placed dim,
    # so overlapping parts do not add up.
    layer = QImage(img.size(), img.format())
    layer.fill(Qt.GlobalColor.transparent)
    q = QPainter(layer)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.scale(2, 2)
    q.translate(SIZE / 2, SIZE / 2)
    # badge rings
    q.setPen(pen(INK, 8))
    q.setBrush(FILL)
    q.drawEllipse(QPointF(0, 0), 268, 268)
    q.setPen(pen(GREY, 3))
    q.setBrush(Qt.BrushStyle.NoBrush)
    q.drawEllipse(QPointF(0, 0), 254, 254)
    q.setPen(pen(INK, 6))
    q.drawEllipse(QPointF(0, 0), 196, 196)
    # lettering
    font = QFont("Noto Sans")
    font.setPixelSize(44)
    font.setWeight(QFont.Weight.Black)
    font.setStretch(QFont.Stretch.Condensed)
    q.setPen(INK)
    arc_text(q, "BUNNY DECK", 226, -90, font, top=True)
    small = QFont(font)
    small.setPixelSize(30)
    q.setPen(GREEN)
    arc_text(q, "SHRED THE SHELL", 230, 90, small, top=False)
    # the bunny, kept inside the inner ring
    q.save()
    clip = QPainterPath()
    clip.addEllipse(QPointF(0, 0), 193, 193)
    q.setClipPath(clip)
    q.translate(0, 34)
    bunny(q)
    q.restore()
    q.end()
    p.resetTransform()
    p.setOpacity(1.0 if bright else STRENGTH)
    p.drawImage(0, 0, layer)
    p.end()
    return img.scaled(SIZE, SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv)
    bright = "--bright" in sys.argv
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "bunny-skate-preview.png" if bright else "bunny-skate.png")
    draw(bright).save(out)
    print(out)
