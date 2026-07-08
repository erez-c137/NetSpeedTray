"""
UsageFlyout - a small Win11-style hover card that shows data usage at a glance.

Shown when the pointer rests on the widget; it carries the gesture hint (while it's still
graduating) and the Today / This-month usage, plus a data-cap progress line when a cap is
set. It deliberately does NOT use Qt's built-in tooltip: that gets positioned by Qt near the
cursor - which sits *on* the taskbar - so the always-on-top taskbar clips it. This card we
place ourselves, clamped fully inside the screen's work area (``availableGeometry``, which
excludes the taskbar), so it can never be clipped.

Frameless + translucent + always-on-top + never steals focus, with a soft drop shadow and
rounded corners to match the native Win11 flyouts. Built on the shared design tokens.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QGridLayout, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect, QProgressBar,
)

from netspeedtray.utils import styles as su, helpers
from netspeedtray.constants.styles import styles as tokens

# Gap between the card and the widget, and keep-off from the work-area edges.
_GAP_ABOVE = 8
_EDGE_MARGIN = 8
_OVER_CAP_COLOR = "#E81123"  # Win11 "critical" red, used when usage is at/over the cap

# (up_bytes, down_bytes)
Totals = Tuple[float, float]
# (used_gb, cap_gb, pct)
CapInfo = Tuple[float, float, float]


class UsageFlyout(QWidget):
    """A hover-triggered, non-focusing usage card. Build it, then call ``show_for()``."""

    def __init__(self, i18n, today: Optional[Totals] = None, month: Optional[Totals] = None,
                 hint: Optional[str] = None, cap: Optional[CapInfo] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self._i18n = i18n
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # never steal focus

        self._c = su.semantic_colors()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)  # room for the drop shadow

        card = QFrame()
        card.setObjectName("usageCard")
        card.setStyleSheet(
            f"QFrame#usageCard {{ background-color: {self._c['card_bg']}; "
            f"border: 1px solid {self._c['card_stroke']}; border-radius: {tokens.RADIUS_CARD}px; }}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 90))
        shadow.setOffset(0, 4)
        card.setGraphicsEffect(shadow)

        body = QVBoxLayout(card)
        body.setContentsMargins(tokens.SPACE_M + 2, tokens.SPACE_M, tokens.SPACE_M + 2, tokens.SPACE_M)
        body.setSpacing(tokens.SPACE_S)
        outer.addWidget(card)

        # The card carries any subset of {gesture hint, usage rows, cap progress}, driven by the
        # two independent Settings toggles. Sections are divided only when more than one is shown.
        sections = 0

        # --- Gesture hint (right-click / double-click) ---
        if hint:
            hint_lbl = self._label(hint, tokens.TYPE_BODY, self._c["text_secondary"])
            hint_lbl.setWordWrap(True)
            hint_lbl.setMaximumWidth(300)
            body.addWidget(hint_lbl)
            sections += 1

        # --- Today / This-month usage (aligned columns) ---
        if today is not None and month is not None:
            if sections:
                body.addWidget(self._separator())
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(tokens.SPACE_S)
            grid.setColumnStretch(0, 1)  # the label column flexes; values hug the right
            self._usage_row(grid, 0, self._tr("USAGE_TODAY_LABEL", "Today"), today)
            self._usage_row(grid, 1, self._tr("USAGE_THIS_MONTH_LABEL", "This month"), month)
            body.addLayout(grid)
            sections += 1

        # --- Data-cap progress (only when a cap is actually set) ---
        if cap is not None:
            used_gb, cap_gb, pct = cap
            if sections:
                body.addWidget(self._separator())
            over = pct >= 100.0
            cap_row = QHBoxLayout()
            cap_row.setContentsMargins(0, 0, 0, 0)
            cap_label = self._tr("TRAY_DATA_CAP_MENU_ITEM", "Data cap").rstrip("… .")
            cap_row.addWidget(self._label(cap_label, tokens.TYPE_BODY_STRONG, self._c["text_primary"]))
            cap_row.addStretch(1)
            gb = self._tr("GB_UNIT", "GB")
            cap_row.addWidget(self._label(f"{used_gb:.1f} / {cap_gb:g} {gb}",
                                          tokens.TYPE_BODY, self._c["text_secondary"],
                                          Qt.AlignmentFlag.AlignRight))
            pct_color = _OVER_CAP_COLOR if over else self._c["accent"]
            cap_row.addWidget(self._label(f"  {pct:.0f}%", tokens.TYPE_BODY_STRONG, pct_color,
                                          Qt.AlignmentFlag.AlignRight))
            body.addLayout(cap_row)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(max(0.0, min(100.0, pct))))
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            fill = _OVER_CAP_COLOR if over else self._c["accent"]
            bar.setStyleSheet(
                f"QProgressBar {{ background: {self._c['subtle_fill']}; border: none; border-radius: 2px; }}"
                f"QProgressBar::chunk {{ background: {fill}; border-radius: 2px; }}"
            )
            body.addWidget(bar)

    # ----------------------------------------------------------------- helpers

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default

    def _label(self, text: str, token: tuple, color: str,
               align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeading) -> QLabel:  # start-aligned; mirrors under RTL (#194)
        lbl = QLabel(text)
        lbl.setFont(su.font(token))
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {self._c['card_stroke']}; border: none;")
        return line

    def _fmt(self, data_bytes: float) -> str:
        value, unit = helpers.format_data_size(data_bytes, self._i18n, precision=1)
        return f"{value:.1f} {unit}"

    def _usage_row(self, grid: QGridLayout, row: int, label: str, totals: Totals) -> None:
        up_bytes, down_bytes = totals
        grid.addWidget(self._label(label, tokens.TYPE_BODY_STRONG, self._c["text_primary"]), row, 0)
        grid.addWidget(self._label(f"↓ {self._fmt(down_bytes)}", tokens.TYPE_BODY,
                                   self._c["text_primary"], Qt.AlignmentFlag.AlignRight), row, 1)
        grid.addWidget(self._label(f"↑ {self._fmt(up_bytes)}", tokens.TYPE_BODY,
                                   self._c["text_primary"], Qt.AlignmentFlag.AlignRight), row, 2)

    # ----------------------------------------------------------------- show

    def show_for(self, widget_geo: QRect, avail: QRect) -> None:
        """
        Show the card anchored to ``widget_geo`` (the widget's global frame rect) but clamped
        fully inside ``avail`` (the screen's work area, which excludes the taskbar). Preferred
        placement is centered just above the widget; the clamp guarantees it never overlaps the
        taskbar regardless of which edge the taskbar is on.
        """
        self.adjustSize()
        sz = self.size()

        x = widget_geo.center().x() - sz.width() // 2
        y = widget_geo.top() - sz.height() - _GAP_ABOVE  # prefer above the widget

        x = max(avail.left() + _EDGE_MARGIN, min(x, avail.right() - sz.width() - _EDGE_MARGIN))
        y = max(avail.top() + _EDGE_MARGIN, min(y, avail.bottom() - sz.height() - _EDGE_MARGIN))

        self.move(int(x), int(y))
        self.show()
