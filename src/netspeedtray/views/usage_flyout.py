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

import math
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QFontMetrics, QScreen
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
                 parent: Optional[QWidget] = None, history_note: Optional[str] = None,
                 hardware: Optional[dict] = None) -> None:
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

        # The card carries any subset of {hardware, gesture hint, usage rows, cap progress}, driven by
        # the Settings toggles. Sections are divided only when more than one is shown.
        sections = 0

        # --- Hardware the side taskbar has no room for (live while the card is open) ---
        self._hw_values: dict = {}
        if hardware:
            self._hardware_section(body, hardware)
            sections += 1

        # --- Gesture hint (right-click / double-click) ---
        if hint:
            if sections:
                body.addWidget(self._separator())
            hint_lbl =self._label(hint, tokens.TYPE_BODY, self._c["text_secondary"])
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
            if history_note:
                # D5: these totals come from recorded history, and part of them was estimated from
                # speeds saved before 2.2 - the cap line below (the odometer) is the exact count.
                note = self._label(history_note, tokens.TYPE_CAPTION, self._c["text_secondary"])
                note.setWordWrap(True)
                note.setMaximumWidth(300)
                body.addWidget(note)
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
            cap_txt = helpers.format_decimal(cap_gb, self._i18n, 1).rstrip("0").rstrip(".,")   # 500 / 2,5
            cap_row.addWidget(self._label(f"{helpers.format_decimal(used_gb, self._i18n, 1)} / {cap_txt} {gb}",
                                          tokens.TYPE_BODY, self._c["text_secondary"],
                                          Qt.AlignmentFlag.AlignRight))
            pct_color = _OVER_CAP_COLOR if over else self._c["accent"]
            cap_row.addWidget(self._label("  " + helpers.format_percent(f"{pct:.0f}", self._i18n),
                                          tokens.TYPE_BODY_STRONG, pct_color,
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
        return f"{helpers.format_decimal(value, self._i18n, 1)} {unit}"   # "8,4 GB" in de_DE

    def _usage_row(self, grid: QGridLayout, row: int, label: str, totals: Totals) -> None:
        up_bytes, down_bytes = totals
        grid.addWidget(self._label(label, tokens.TYPE_BODY_STRONG, self._c["text_primary"]), row, 0)
        grid.addWidget(self._label(f"↓ {self._fmt(down_bytes)}", tokens.TYPE_BODY,
                                   self._c["text_primary"], Qt.AlignmentFlag.AlignRight), row, 1)
        grid.addWidget(self._label(f"↑ {self._fmt(up_bytes)}", tokens.TYPE_BODY,
                                   self._c["text_primary"], Qt.AlignmentFlag.AlignRight), row, 2)

    # Rows of the hardware section, in order: (key, label). The labels are the universal abbreviations
    # the widget itself paints (not translated there either).
    _HW_ROWS = (("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU"), ("vram", "VRAM"))
    # Widest value each column can show, so a live update never makes the card jump in width.
    _HW_REFS = {"pct": "100%", "temp": "100°C", "power": "250.0 W", "mem": "99.9 / 99.9 GB"}

    def _hardware_section(self, body: QVBoxLayout, hardware: dict) -> None:
        """CPU/GPU/memory (and the Wi-Fi identity) the side taskbar has no room for. Fixed-width,
        right-aligned value columns so the 1 Hz updates (``update_hardware``) never shift the card."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(tokens.SPACE_S)
        grid.setColumnStretch(0, 1)
        fm = QFontMetrics(su.font(tokens.TYPE_BODY))
        widths = {k: fm.horizontalAdvance(helpers.format_percent(100, self._i18n) if k == "pct" else v)
                  for k, v in self._HW_REFS.items()}
        # Temperature / power columns only when a stat carries them (the widget's own toggles).
        stat_cols = ["pct"] + [c for c in ("temp", "power")
                               if any(c in (hardware.get(k) or {}) for k in ("cpu", "gpu"))]
        row = 0
        for key, label in self._HW_ROWS:
            if key not in hardware:
                continue
            grid.addWidget(self._label(label, tokens.TYPE_BODY_STRONG, self._c["text_primary"]), row, 0)
            cols = stat_cols if key in ("cpu", "gpu") else ["mem"]
            for i, col in enumerate(cols):
                lbl = self._label("", tokens.TYPE_BODY, self._c["text_primary"], Qt.AlignmentFlag.AlignRight)
                lbl.setMinimumWidth(widths[col])
                lbl.setAccessibleName(label)
                span = len(stat_cols) if col == "mem" else 1
                grid.addWidget(lbl, row, 1 + i, 1, span)
                self._hw_values[(key, col)] = lbl
            row += 1
        identity = hardware.get("identity")
        if identity:
            grid.addWidget(self._label(self._tr("NETWORK_IDENTITY_TITLE", "Network"), tokens.TYPE_BODY_STRONG,
                                       self._c["text_primary"]), row, 0)
            lbl = self._label("", tokens.TYPE_BODY, self._c["text_primary"], Qt.AlignmentFlag.AlignRight)
            grid.addWidget(lbl, row, 1, 1, len(stat_cols))
            self._hw_values[("identity", "text")] = lbl
        body.addLayout(grid)
        self.update_hardware(hardware)

    @property
    def has_hardware(self) -> bool:
        return bool(self._hw_values)

    def update_hardware(self, hardware: Optional[dict]) -> None:
        """Refresh the hardware rows in place (the card stays up while the pointer rests on the widget).
        Keys: cpu/gpu -> {usage, temp, power}; ram/vram -> (used_gb, total_gb); identity -> str. A
        missing reading shows an em dash, never a made-up 0."""
        if not hardware or not self._hw_values:
            return
        dash = "—"

        def num(v: object) -> Optional[float]:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if math.isfinite(f) else None

        for key in ("cpu", "gpu"):
            stat = hardware.get(key)
            if stat is None or (key, "pct") not in self._hw_values:
                continue
            usage, temp, power = num(stat.get("usage")), num(stat.get("temp")), num(stat.get("power"))
            self._hw_values[(key, "pct")].setText(
                helpers.format_percent(int(round(usage)), self._i18n) if usage is not None else dash)
            if (key, "temp") in self._hw_values:
                self._hw_values[(key, "temp")].setText(f"{temp:.0f}°C" if temp is not None else dash)
            if (key, "power") in self._hw_values:
                self._hw_values[(key, "power")].setText(
                    f"{helpers.format_decimal(power, self._i18n, 1)} W" if power is not None else dash)
        gb = self._tr("GB_UNIT", "GB")
        for key in ("ram", "vram"):
            mem = hardware.get(key)
            if mem is None or (key, "mem") not in self._hw_values:
                continue
            used, total = (num(mem[0]), num(mem[1])) if isinstance(mem, (tuple, list)) and len(mem) == 2 \
                else (None, None)
            if used is None:
                text = dash
            elif total:
                text = (f"{helpers.format_decimal(used, self._i18n, 1)} / "
                        f"{helpers.format_decimal(total, self._i18n, 1)} {gb}")
            else:
                text = f"{helpers.format_decimal(used, self._i18n, 1)} {gb}"
            self._hw_values[(key, "mem")].setText(text)
        if ("identity", "text") in self._hw_values:
            self._hw_values[("identity", "text")].setText(str(hardware.get("identity") or dash))

    # ----------------------------------------------------------------- show

    def show_for(self, widget_geo: QRect, avail: QRect, edge: Optional[object] = None,
                 screen: Optional[QScreen] = None) -> None:
        """
        Show the card anchored to ``widget_geo`` (the widget's global frame rect) but clamped
        fully inside ``avail`` (the work area of the widget's screen, which excludes the taskbar).

        Placement follows the taskbar ``edge`` (a ``TaskbarEdge``; None = bottom): above the widget on
        a bottom bar, below it on a top bar, and BESIDE it on a side bar - flush with the work area's
        inner edge, vertically centred on the widget. The clamp guarantees it never overlaps the
        taskbar. ``screen`` (the widget's) is set on the card's window before it is sized, so a card
        on a second monitor with a different scale lays out at that monitor's DPI.
        """
        if screen is not None:
            try:
                self.winId()                     # create the native window so it has a handle
                handle = self.windowHandle()
                if handle is not None:
                    handle.setScreen(screen)
            except Exception:
                pass
        self.adjustSize()
        sz = self.size()
        x, y = self._anchor(widget_geo, avail, sz, edge)
        x = max(avail.left() + _EDGE_MARGIN, min(x, avail.right() - sz.width() - _EDGE_MARGIN))
        y = max(avail.top() + _EDGE_MARGIN, min(y, avail.bottom() - sz.height() - _EDGE_MARGIN))

        self.move(int(x), int(y))
        self.show()

    @staticmethod
    def _anchor(widget_geo: QRect, avail: QRect, sz, edge: Optional[object]) -> Tuple[int, int]:
        """Preferred (unclamped) top-left for the card - see ``show_for``. The card's window has a
        12 px transparent shadow margin, so "flush" uses the same _EDGE_MARGIN the clamp keeps."""
        from netspeedtray.constants import TaskbarEdge
        cx = widget_geo.center().x() - sz.width() // 2
        cy = widget_geo.center().y() - sz.height() // 2
        if edge == TaskbarEdge.RIGHT:
            return avail.right() - _EDGE_MARGIN - sz.width(), cy
        if edge == TaskbarEdge.LEFT:
            return avail.left() + _EDGE_MARGIN, cy
        if edge == TaskbarEdge.TOP:
            return cx, widget_geo.bottom() + _GAP_ABOVE
        return cx, widget_geo.top() - sz.height() - _GAP_ABOVE   # bottom bar: above the widget
