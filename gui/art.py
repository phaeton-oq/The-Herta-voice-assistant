"""Декоративные примитивы на QPainter.

Всё, что придаёт интерфейсу характер, но не является виджетом: угловые
скобки, гексагоны, кольцо делений, точечная сетка, срезанные углы, ромбы.
Функции чистые — принимают painter и рисуют, ничего не хранят.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from gui import styles as T


def corner_brackets(
    painter: QPainter,
    width: float,
    height: float,
    color: str = T.GOLD,
    length: float | None = None,
    inset: float = 1.0,
    pen_width: float = 1.0,
) -> None:
    """Четыре угловые скобки — основной приём рамок в этом интерфейсе.

    Вместо замкнутой рамки рисуем только углы: панель читается как
    «прицел», а не как таблица.
    """
    arm = length or T.BRACKET_LEN
    i = inset
    w, h = width, height

    painter.save()
    painter.setPen(QPen(QColor(color), pen_width))
    painter.setBrush(Qt.NoBrush)

    for points in (
        ((i, i + arm), (i, i), (i + arm, i)),                       # верх-лево
        ((w - i - arm, i), (w - i, i), (w - i, i + arm)),           # верх-право
        ((w - i, h - i - arm), (w - i, h - i), (w - i - arm, h - i)),  # низ-право
        ((i + arm, h - i), (i, h - i), (i, h - i - arm)),           # низ-лево
    ):
        path = QPainterPath(QPointF(*points[0]))
        path.lineTo(QPointF(*points[1]))
        path.lineTo(QPointF(*points[2]))
        painter.drawPath(path)

    painter.restore()


def hexagon_path(cx: float, cy: float, radius: float, rotation: float = 0.0) -> QPolygonF:
    """Правильный шестиугольник вершиной вверх — мотив Общества гениев."""
    points = []
    for k in range(6):
        angle = math.radians(60 * k - 90) + rotation
        points.append(QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return QPolygonF(points)


def tick_ring(
    painter: QPainter,
    cx: float,
    cy: float,
    radius: float,
    count: int = 48,
    length: float = 4.0,
    color: str = T.LINE_STRONG,
    phase: float = 0.0,
    accent: str | None = None,
    accent_every: int = 12,
    pen_width: float = 1.0,
) -> None:
    """Кольцо делений. Вращается изменением phase — это «kuru kuru»."""
    painter.save()
    for k in range(count):
        is_accent = accent is not None and k % accent_every == 0
        painter.setPen(QPen(QColor(accent if is_accent else color), pen_width))
        arm = length * (1.8 if is_accent else 1.0)
        angle = math.radians(360 * k / count) + phase
        painter.drawLine(
            QPointF(cx + (radius - arm) * math.cos(angle), cy + (radius - arm) * math.sin(angle)),
            QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle)),
        )
    painter.restore()


def dot_grid(
    painter: QPainter,
    width: float,
    height: float,
    color: str = T.LINE,
    step: int = 22,
    radius: float = 1.0,
) -> None:
    """Точечная сетка фона. Даёт глубину без градиентов."""
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    y = step // 2
    while y < height:
        x = step // 2
        while x < width:
            painter.drawEllipse(QPointF(x, y), radius, radius)
            x += step
        y += step
    painter.restore()


def clipped_rect_path(
    rect: QRectF,
    cut: float = T.NOTCH,
    corners: tuple[str, ...] = ('tl',),
) -> QPainterPath:
    """Прямоугольник со срезанными углами.

    Скос вместо скругления: читается острее и попадает в стилистику HSR.
    """
    x1, y1 = rect.left(), rect.top()
    x2, y2 = rect.right(), rect.bottom()
    path = QPainterPath()

    path.moveTo(x1 + cut, y1) if 'tl' in corners else path.moveTo(x1, y1)

    if 'tr' in corners:
        path.lineTo(x2 - cut, y1)
        path.lineTo(x2, y1 + cut)
    else:
        path.lineTo(x2, y1)

    if 'br' in corners:
        path.lineTo(x2, y2 - cut)
        path.lineTo(x2 - cut, y2)
    else:
        path.lineTo(x2, y2)

    if 'bl' in corners:
        path.lineTo(x1 + cut, y2)
        path.lineTo(x1, y2 - cut)
    else:
        path.lineTo(x1, y2)

    if 'tl' in corners:
        path.lineTo(x1, y1 + cut)

    path.closeSubpath()
    return path


def diamond(painter: QPainter, cx: float, cy: float, radius: float, color: str = T.LAVENDER) -> None:
    """Ромб — маркер строки в логе инструментов."""
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPolygon(
        QPolygonF([
            QPointF(cx, cy - radius),
            QPointF(cx + radius, cy),
            QPointF(cx, cy + radius),
            QPointF(cx - radius, cy),
        ])
    )
    painter.restore()


def pill(
    painter: QPainter,
    rect: QRectF,
    fill: str,
    outline: str,
    pen_width: float = 1.0,
) -> None:
    """Капсула для тумблера."""
    painter.save()
    painter.setPen(QPen(QColor(outline), pen_width))
    painter.setBrush(QColor(fill))
    painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    painter.restore()
