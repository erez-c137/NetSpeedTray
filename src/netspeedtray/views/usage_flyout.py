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
_CHARGING_COLOR = "#6CCB5F"  # Win11 dark-mode success green

# (up_bytes, down_bytes)
Totals = Tuple[float, float]
# (used_gb, cap_gb, pct)
CapInfo = Tuple[float, float, float]


class UsageFlyout(QWidget):
    """A hover-triggered, non-focusing usage card. Build it, then call ``show_for()``."""

    def __init__(self, i18n, today: Optional[Totals] = None, month: Optional[Totals] = None,
                 hint: Optional[str] = None, cap: Optional[CapInfo] = None,
                 power: Optional[dict] = None,
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

        # --- Battery power (fed by utils/power_utils + the system_power stat; every
        # value degrades silently - a row whose data is missing is omitted, not dashed) ---
        if power is not None:
            if sections:
                body.addWidget(self._separator())
            self._battery_section(body, power)
            sections += 1

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

    def _battery_section(self, body: QVBoxLayout, power: dict) -> None:
        """The battery rows: live draw, today's average, projected runtime, charge level."""
        mode = power.get("mode", "none")
        live_w = power.get("live_w")
        today_avg_w = power.get("today_avg_w")
        projected_hours = power.get("projected_hours")
        charge_pct = power.get("charge_pct")

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(tokens.SPACE_S)
        grid.setColumnStretch(0, 1)
        grid_row = 0

        # Now - the live rate. Charging is "+" and green (always good news); a missing
        # reading (first seconds after launch, or a firmware that reports nothing yet)
        # shows an em-dash rather than a wrong 0.0 W.
        if mode == "charge" and live_w:
            # live_w is the signed poll value (negative while charging) - render "+".
            now_text, now_color = f"+{abs(live_w):.1f} W", _CHARGING_COLOR
        elif mode == "ac":
            now_text, now_color = self._tr("POWER_ON_AC_VALUE", "On AC"), self._c["text_secondary"]
        elif live_w:
            now_text, now_color = f"{live_w:.1f} W", self._c["text_primary"]
        else:
            now_text, now_color = "—", self._c["text_secondary"]
        grid.addWidget(self._label(self._tr("POWER_NOW_LABEL", "Now"), tokens.TYPE_BODY_STRONG,
                                   self._c["text_primary"]), grid_row, 0)
        grid.addWidget(self._label(now_text, tokens.TYPE_BODY_STRONG, now_color,
                                   Qt.AlignmentFlag.AlignRight), grid_row, 1)
        grid_row += 1

        if today_avg_w:
            grid.addWidget(self._label(self._tr("POWER_TODAY_AVG_LABEL", "Today avg"),
                                       tokens.TYPE_BODY_STRONG, self._c["text_primary"]), grid_row, 0)
            grid.addWidget(self._label(f"{today_avg_w:.1f} W", tokens.TYPE_BODY,
                                       self._c["text_primary"],
                                       Qt.AlignmentFlag.AlignRight), grid_row, 1)
            grid_row += 1

        # Only meaningful while discharging with a real reading and a known pack size.
        if mode == "discharge" and projected_hours:
            grid.addWidget(self._label(self._tr("USAGE_PROJECTED_LABEL", "Projected"),
                                       tokens.TYPE_BODY_STRONG, self._c["text_primary"]), grid_row, 0)
            grid.addWidget(self._label(self._fmt_duration(projected_hours), tokens.TYPE_BODY,
                                       self._c["text_primary"],
                                       Qt.AlignmentFlag.AlignRight), grid_row, 1)
            grid_row += 1
        body.addLayout(grid)

        if charge_pct is not None:
            foot = self._tr("POWER_CHARGE_PCT_TEMPLATE", "{pct}% battery").replace("{pct}", str(charge_pct))
            body.addWidget(self._label(foot, tokens.TYPE_BODY, self._c["text_secondary"]))

    @staticmethod
    def _fmt_duration(hours: float) -> str:
        total = int(round(hours * 60.0))
        h, m = divmod(total, 60)
        return f"{h} h {m:02d} m" if h else f"{m} m"

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
