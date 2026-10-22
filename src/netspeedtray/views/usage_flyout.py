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

import logging
import math
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QRect, pyqtSignal
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
_SHADOW_MARGIN = 12   # the card window's transparent drop-shadow margin (outer.setContentsMargins)
_OVER_CAP_COLOR = "#E81123"  # Win11 "critical" red, used when usage is at/over the cap
# Win11 success green per theme - the dark one is ~2:1 on the light card; both clear 4.5:1 on theirs.
_CHARGING_COLOR_DARK = "#6CCB5F"
_CHARGING_COLOR_LIGHT = "#0F7B0F"

# (up_bytes, down_bytes)
Totals = Tuple[float, float]
# (used_gb, cap_gb, pct)
CapInfo = Tuple[float, float, float]


_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020

logger = logging.getLogger(__name__)
# Directions (True = set, False = clear) whose failure has already been logged: a style call that
# fails tends to fail on every card, and the card is rebuilt on every hover - warn once, not per show.
_click_through_warned: set = set()


def _warn_click_through_failed(through: bool, detail: object) -> None:
    if through in _click_through_warned:
        return
    _click_through_warned.add(through)
    logger.warning(
        "Hover card: could not %s WS_EX_TRANSPARENT on its native window (%s) - %s. "
        "Further failures are not logged.",
        "set" if through else "clear", detail,
        "the card may catch the pointer while it is only informational" if through
        else "a pinned card may not take clicks")


def _set_click_through(widget: QWidget, through: bool) -> bool:
    """Toggle WS_EX_TRANSPARENT on the card's native window, in place. This native style is the
    card's ONLY click-through mechanism.

    Never ``Qt.WindowTransparentForInput``: Qt drops every mouse event addressed to a window carrying
    that flag, even after the native style is cleared - a pinned card then swallowed clicks and did
    nothing (rc.1 live test B5, 2026-10-22; pinned by test_hover_card_pin_native.py). Not
    ``setWindowFlags`` either (re-creates and re-shows the window: a blink) nor ``QWindow.setFlag`` (verified 2026-10-21: drops
    WS_EX_LAYERED, which the translucent card uses). Every other extended style - WS_EX_NOACTIVATE
    above all, and WS_EX_LAYERED, without which WS_EX_TRANSPARENT does not pass clicks through - is
    kept. Returns True if the style is now as asked; a failure (an exception, or the style read back
    unchanged) is logged once per direction at WARNING."""
    try:
        import ctypes
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        new = (ex | _WS_EX_TRANSPARENT) if through else (ex & ~_WS_EX_TRANSPARENT)
        if new != ex:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new)
            # SetWindowLongW's return value can't tell failure from a previous style of 0: read back.
            if bool(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TRANSPARENT) != bool(through):
                _warn_click_through_failed(through, f"style still 0x{ex & 0xFFFFFFFF:08X}")
                return False
        return True
    except Exception as e:
        _warn_click_through_failed(through, e)
        return False


class UsageFlyout(QWidget):
    """A hover-triggered, non-focusing usage card. Build it, then call ``show_for()``."""

    # A click on a PINNED card (the only state in which it takes clicks) asks the widget to close it.
    dismiss_requested = pyqtSignal()

    def __init__(self, i18n, today: Optional[Totals] = None, month: Optional[Totals] = None,
                 hint: Optional[str] = None, cap: Optional[CapInfo] = None,
                 power: Optional[dict] = None,
                 parent: Optional[QWidget] = None, history_note: Optional[str] = None,
                 hardware: Optional[dict] = None, month_label: Optional[str] = None,
                 pin_hint: Optional[str] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus,
            # Click-through while informational is the NATIVE WS_EX_TRANSPARENT style, set in
            # show_for (see _set_click_through) - deliberately not Qt.WindowTransparentForInput,
            # which would leave a pinned card deaf to clicks for good.
        )
        self._i18n = i18n
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # never steal focus

        self._c = su.semantic_colors()
        self._charging_color = _CHARGING_COLOR_DARK if su.is_dark_mode() else _CHARGING_COLOR_LIGHT

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*(_SHADOW_MARGIN,) * 4)  # room for the drop shadow

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
        self._body_layout = body
        outer.addWidget(card)

        # The card carries any subset of {hardware, battery, gesture hint, usage rows, cap progress},
        # driven by the Settings toggles. Sections are divided only when more than one is shown.
        sections = 0

        # --- Hardware the side taskbar has no room for (live while the card is open) ---
        self._hw_values: dict = {}
        if hardware:
            self._hardware_section(body, hardware)
            sections += 1

        # --- Battery (opt-in, laptops only; live while the card is open, beside the hardware) ---
        self._power_values: dict = {}
        self._power_rows: Optional[tuple] = None
        if power is not None:
            if sections:
                body.addWidget(self._separator())
            self._battery_section(body, power)
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

        self._pin_hint_lbl: Optional[QLabel] = None
        self._last_show: Optional[tuple] = None
        self._pinned = False
        # Live usage labels, for update_usage() while the card is pinned.
        self._usage_labels: dict = {}
        self._cap_labels: Optional[tuple] = None

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
            # month_label: "This period" when the row follows a data-cap billing period (main.py).
            self._usage_row(grid, 1, month_label or self._tr("USAGE_THIS_MONTH_LABEL", "This month"), month)
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
            used_lbl = self._label(f"{helpers.format_decimal(used_gb, self._i18n, 1)} / {cap_txt} {gb}",
                                   tokens.TYPE_BODY, self._c["text_secondary"], Qt.AlignmentFlag.AlignRight)
            cap_row.addWidget(used_lbl)
            pct_color = _OVER_CAP_COLOR if over else self._c["accent"]
            pct_lbl = self._label("  " + helpers.format_percent(f"{pct:.0f}", self._i18n),
                                  tokens.TYPE_BODY_STRONG, pct_color, Qt.AlignmentFlag.AlignRight)
            cap_row.addWidget(pct_lbl)
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
            self._cap_labels = (used_lbl, pct_lbl, bar, cap_txt, gb)

        # --- Footer: "Click the widget to keep this open" (a card opened by hover; dropped once
        # pinned). Word-wrapped at 300 px, like the other text rows: a long translation wraps, never
        # clips (test_hover_card_pin_hint_text pins this). ---
        if pin_hint:
            self._pin_hint_lbl = self._label(pin_hint, tokens.TYPE_CAPTION, self._c["text_secondary"])
            self._pin_hint_lbl.setWordWrap(True)
            self._pin_hint_lbl.setMaximumWidth(300)
            body.addWidget(self._pin_hint_lbl)

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
        down = self._label(f"↓ {self._fmt(down_bytes)}", tokens.TYPE_BODY,
                           self._c["text_primary"], Qt.AlignmentFlag.AlignRight)
        up = self._label(f"↑ {self._fmt(up_bytes)}", tokens.TYPE_BODY,
                         self._c["text_primary"], Qt.AlignmentFlag.AlignRight)
        grid.addWidget(down, row, 1)
        grid.addWidget(up, row, 2)
        self._usage_labels[row] = (down, up)

    def update_usage(self, today: Optional[Totals], month: Optional[Totals],
                     cap: Optional[CapInfo] = None) -> None:
        """Refresh the usage rows / cap line in place (a pinned card stays open for minutes)."""
        for row, totals in ((0, today), (1, month)):
            labels = self._usage_labels.get(row)
            if labels is None or totals is None:
                continue
            up_bytes, down_bytes = totals
            labels[0].setText(f"↓ {self._fmt(down_bytes)}")
            labels[1].setText(f"↑ {self._fmt(up_bytes)}")
        if cap is not None and self._cap_labels is not None:
            used_lbl, pct_lbl, bar, cap_txt, gb = self._cap_labels
            used_gb, _cap_gb, pct = cap
            used_lbl.setText(f"{helpers.format_decimal(used_gb, self._i18n, 1)} / {cap_txt} {gb}")
            pct_lbl.setText("  " + helpers.format_percent(f"{pct:.0f}", self._i18n))
            bar.setValue(int(max(0.0, min(100.0, pct))))

    # Rows of the hardware section, in order: (key, label). The labels are the universal abbreviations
    # the widget itself paints (not translated there either).
    _HW_ROWS = (("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU"), ("vram", "VRAM"))
    # Widest value each column can show, so a live update never makes the card jump in width.
    _HW_REFS = {"pct": "100%", "temp": "100°C", "power": "250.0 W", "mem": "99.9 / 99.9 GB"}
    # (The "temp" reference follows the display unit - helpers.temperature_reference(hover=True).)

    @staticmethod
    def _temp_unit(hardware: dict) -> str:
        """The snapshot's display unit: its "temp_unit" key, absent = Celsius (rc.2 F8)."""
        return helpers.temperature_unit({"temperature_unit": hardware.get("temp_unit")})

    def _hardware_section(self, body: QVBoxLayout, hardware: dict) -> None:
        """CPU/GPU/memory (and the Wi-Fi identity) the side taskbar has no room for. Fixed-width,
        right-aligned value columns so the 1 Hz updates (``update_hardware``) never shift the card."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(tokens.SPACE_S)
        grid.setColumnStretch(0, 1)
        fm = QFontMetrics(su.font(tokens.TYPE_BODY))
        refs = dict(self._HW_REFS, temp=helpers.temperature_reference(self._temp_unit(hardware), hover=True))
        widths = {k: fm.horizontalAdvance(helpers.format_percent(100, self._i18n) if k == "pct" else v)
                  for k, v in refs.items()}
        # Temperature / power columns only when a stat carries them (the widget's own toggles).
        stat_cols = ["pct"] + [c for c in ("temp", "power")
                               if any(c in (hardware.get(k) or {}) for k in ("cpu", "gpu"))]
        row = 0
        for key, label in self._HW_ROWS:
            if key not in hardware:
                continue
            if key == "vram" and hardware.get("vram_shared"):
                # An integrated GPU's "VRAM" is shared system memory - say so here, where there is
                # room (the taskbar keeps the short "VRAM"). update_hardware keeps it current.
                label = self._tr("VRAM_SHARED_LABEL", "VRAM (shared)")
            row_label = self._label(label, tokens.TYPE_BODY_STRONG, self._c["text_primary"])
            if key == "vram":
                self._hw_values[("vram", "label")] = row_label
            grid.addWidget(row_label, row, 0)
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
        Keys: cpu/gpu -> {usage, temp, power}; ram/vram -> (used_gb, total_gb); vram_shared -> True
        when the VRAM is an integrated GPU's shared memory; temp_unit -> "fahrenheit" (absent = Celsius; the
        temps are Celsius; rc.2 F8); identity -> str. A missing reading shows an em dash, never a made-up 0."""
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
                self._hw_values[(key, "temp")].setText(
                    helpers.format_temperature(temp, self._temp_unit(hardware))
                    if temp is not None else dash)
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
        if ("vram", "label") in self._hw_values and "vram" in hardware:
            # The card can stay up across a poll that changes what the VRAM reading is.
            self._hw_values[("vram", "label")].setText(
                self._tr("VRAM_SHARED_LABEL", "VRAM (shared)") if hardware.get("vram_shared") else "VRAM")
        if ("identity", "text") in self._hw_values:
            self._hw_values[("identity", "text")].setText(str(hardware.get("identity") or dash))

    # ----------------------------------------------------------------- battery

    @staticmethod
    def _power_row_set(power: dict) -> tuple:
        """Which battery rows a summary shows. A row whose data is missing is omitted, not dashed
        (the live "Now" row is the exception: it is always there, with a dash before the first
        reading), so the set changes with the mode - and an open card then rebuilds."""
        mode = power.get("mode", "none")
        return (mode,
                bool(power.get("today_avg_w")),
                mode == "discharge" and bool(power.get("projected_hours")),
                power.get("charge_pct") is not None)

    def _power_texts(self, power: dict) -> dict:
        """Display text (and the Now row's color) for each battery row, locale-formatted like the
        hardware rows beside it."""
        mode = power.get("mode", "none")
        live_w = power.get("live_w")
        watts = lambda w: f"{helpers.format_decimal(w, self._i18n, 1)} W"   # noqa: E731
        # Now - the live rate. Charging is "+" and green (always good news); a missing reading
        # (first seconds after launch, or a firmware that reports nothing yet) shows an em dash
        # rather than a wrong 0.0 W. live_w is the signed poll value (negative while charging).
        if mode == "charge" and live_w:
            now = (f"+{watts(abs(live_w))}", self._charging_color)
        elif mode == "ac":
            now = (self._tr("POWER_ON_AC_VALUE", "On AC"), self._c["text_secondary"])
        elif live_w:
            now = (watts(live_w), self._c["text_primary"])
        else:
            now = ("—", self._c["text_secondary"])
        out = {"now": now}
        if power.get("today_avg_w"):
            out["today"] = watts(power["today_avg_w"])
        if mode == "discharge" and power.get("projected_hours"):
            out["projected"] = helpers.format_duration_short(power["projected_hours"] * 3600.0, self._i18n)
        if power.get("charge_pct") is not None:
            out["charge"] = self._tr("POWER_CHARGE_PCT_TEMPLATE", "{pct}% battery").replace(
                "{pct}", str(power["charge_pct"]))
        return out

    def _battery_section(self, body: QVBoxLayout, power: dict) -> None:
        """The battery rows: live draw, today's average, projected runtime, charge level. Values are
        kept so ``update_power`` can refresh them in place while the card is open."""
        texts = self._power_texts(power)
        # A caption, so "Now 9.6 W" doesn't read as one more hardware row above it and "Today avg"
        # isn't mistaken for the usage "Today" below (the hardware rows need none: CPU/GPU/RAM
        # name themselves).
        body.addWidget(self._label(self._tr("POWER_SECTION_TITLE", "Battery"), tokens.TYPE_CAPTION,
                                   self._c["text_secondary"]))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(tokens.SPACE_S)
        grid.setColumnStretch(0, 1)
        rows = (("now", "POWER_NOW_LABEL", "Now"),
                ("today", "POWER_TODAY_AVG_LABEL", "Today avg"),
                ("projected", "USAGE_PROJECTED_LABEL", "Projected"))
        grid_row = 0
        for key, i18n_key, default in rows:
            if key not in texts:
                continue
            grid.addWidget(self._label(self._tr(i18n_key, default), tokens.TYPE_BODY_STRONG,
                                       self._c["text_primary"]), grid_row, 0)
            text, color = texts[key] if key == "now" else (texts[key], self._c["text_primary"])
            value = self._label(text, tokens.TYPE_BODY_STRONG if key == "now" else tokens.TYPE_BODY,
                                color, Qt.AlignmentFlag.AlignRight)
            # Room for the widest live value, so a refresh never makes the card jump in width.
            value.setMinimumWidth(QFontMetrics(value.font()).horizontalAdvance("+888.8 W"))
            grid.addWidget(value, grid_row, 1)
            self._power_values[key] = value
            grid_row += 1
        body.addLayout(grid)
        if "charge" in texts:
            foot = self._label(texts["charge"], tokens.TYPE_BODY, self._c["text_secondary"])
            body.addWidget(foot)
            self._power_values["charge"] = foot
        self._power_rows = self._power_row_set(power)

    @property
    def has_power(self) -> bool:
        return self._power_rows is not None

    def update_power(self, power: Optional[dict]) -> bool:
        """Refresh the battery rows in place. False when the summary needs a DIFFERENT set of rows
        (plugged in or unplugged mid-hover, history arrived, section gone): the caller rebuilds the
        card then, rather than leaving a stale row up."""
        if power is None or self._power_rows is None or self._power_row_set(power) != self._power_rows:
            return False
        for key, value in self._power_texts(power).items():
            lbl = self._power_values.get(key)
            if lbl is None:
                continue
            if key == "now":
                text, color = value
                lbl.setStyleSheet(f"color: {color}; background: transparent;")
                lbl.setText(text)
            else:
                lbl.setText(value)
        return True

    # ----------------------------------------------------------------- show

    def show_for(self, widget_geo: QRect, avail: QRect, edge: Optional[object] = None,
                 screen: Optional[QScreen] = None) -> None:
        """
        Show the card anchored to ``widget_geo`` (the widget's global frame rect) but clamped
        fully inside ``avail`` (the work area of the widget's screen, which excludes the taskbar).

        Placement follows the taskbar ``edge`` (a ``TaskbarEdge``; None = bottom): above the widget on
        a bottom bar, below it on a top bar, and BESIDE it on a side bar, vertically centred on the
        widget; it flips to the other side when there is no room, and never covers the widget (see
        ``placement``). The clamp keeps it off a docked taskbar. ``screen`` (the widget's) is set on the card's window before it is sized, so a card
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
        bounds = None
        try:
            bounds = screen.geometry() if screen is not None else None
        except Exception:
            bounds = None
        self._last_show = (widget_geo, avail, edge, bounds)
        self.adjustSize()
        rect = self.placement(widget_geo, avail, self.size(), edge, bounds=bounds)
        self.move(rect.x(), rect.y())
        # Purely informational until pinned: let the pointer fall through to whatever is under it.
        # Then even a card forced over its widget (a screen too small for it anywhere, see
        # ``placement``) cannot take the hover away from the widget and start a flicker loop. Set on
        # the native window BEFORE it is shown, so not even the first frame catches the pointer.
        try:
            self.winId()
        except Exception:
            pass
        _set_click_through(self, not self._pinned)
        self.show()

    def _replace(self, widget_geo: Optional[QRect] = None) -> None:
        """Re-place against the widget (its new frame rect, or the one it was shown for), with the
        same work area, edge and screen bounds as ``show_for``."""
        if self._last_show is None:
            return
        old_geo, avail, edge, bounds = self._last_show
        geo = widget_geo if widget_geo is not None else old_geo
        self._last_show = (geo, avail, edge, bounds)
        self.adjustSize()
        rect = self.placement(geo, avail, self.size(), edge, bounds=bounds)
        self.move(rect.x(), rect.y())

    def reanchor(self, widget_geo: QRect) -> None:
        """The widget moved (no dock change - e.g. the tray grew): follow it."""
        self._replace(widget_geo)

    @property
    def pin_hint_visible(self) -> bool:
        return self._pin_hint_lbl is not None and not self._pin_hint_lbl.isHidden()

    @property
    def pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, pinned: bool) -> None:
        """The widget was clicked to keep the card open: drop the "Click the widget to keep this open"
        line, re-place the (now shorter) card against the widget, and make it take clicks - an
        informational card passes the pointer through (native WS_EX_TRANSPARENT), but a pinned one
        sits there for minutes, and a click on it must not press a button or drop a caret in the app
        behind it. Clicking it (or the widget again) closes it
        (``dismiss_requested``). Moves, resizes and restyles only - never activates (WS_EX_NOACTIVATE /
        WindowDoesNotAcceptFocus / WA_ShowWithoutActivating stay)."""
        pinned = bool(pinned)
        if pinned == self._pinned:
            return
        self._pinned = pinned
        _set_click_through(self, not pinned)
        if self._pin_hint_lbl is not None and self._pin_hint_lbl.isHidden() != pinned:
            self._pin_hint_lbl.setVisible(not pinned)
            # The hide only POSTS a layout request; settle the nested layouts now so adjustSize()
            # measures the card without the line.
            for lay in (self._body_layout, self.layout()):
                if lay is not None:
                    lay.invalidate()
                    lay.activate()
            self._replace()

    def mousePressEvent(self, event) -> None:
        """Only a pinned card receives clicks (see set_pinned): a click closes it, and is consumed."""
        event.accept()
        if self._pinned:
            self.dismiss_requested.emit()

    @staticmethod
    def _candidates(widget_geo: QRect, avail: QRect, sz, edge: Optional[object]):
        """Unclamped top-left positions for the card, most preferred first. Each one lies wholly
        beside the widget (gap included), so none of them intersects it before clamping.

        - side bar: beside the widget, toward the desktop, vertically centred on it. The inner edge
          is ``min(work area, widget)``: an AUTO-HIDE side bar does not shrink the work area, so
          flushing to the work area alone put the card on top of the widget.
        - top bar: below, else above; bottom bar / free-float / no edge: above, else below (a widget
          dragged or floated to the top of a screen has no room above it).
        """
        from netspeedtray.constants import TaskbarEdge
        w, h = sz.width(), sz.height()
        cx = widget_geo.center().x() - w // 2
        cy = widget_geo.center().y() - h // 2
        # (x, y, axis that separates the card from the widget)
        above = (cx, widget_geo.top() - _GAP_ABOVE - h, "y")
        below = (cx, widget_geo.bottom() + 1 + _GAP_ABOVE, "y")
        # QRect.right() is left + width - 1, so the first column past an edge is right() + 1.
        left_of = (min(avail.right() + 1 - _EDGE_MARGIN, widget_geo.left() - _GAP_ABOVE) - w, cy, "x")
        right_of = (max(avail.left() + _EDGE_MARGIN, widget_geo.right() + 1 + _GAP_ABOVE), cy, "x")
        if edge == TaskbarEdge.RIGHT:
            order = [left_of, right_of, above, below]
        elif edge == TaskbarEdge.LEFT:
            order = [right_of, left_of, above, below]
        elif edge == TaskbarEdge.TOP:
            order = [below, above, left_of, right_of]
        else:
            order = [above, below, left_of, right_of]
        return order

    @classmethod
    def placement(cls, widget_geo: QRect, avail: QRect, sz, edge: Optional[object] = None,
                  bounds: Optional[QRect] = None) -> QRect:
        """The card's global rect for ``show_for`` (pure geometry; the window is not touched).

        The first preferred position that, once clamped into ``avail`` (minus _EDGE_MARGIN), still
        leaves the widget uncovered wins. A card over its own widget is not just ugly: it sits under
        the resting cursor, so the widget gets a leave event, hides the card, gets an enter event and
        re-shows it 350 ms later - a flicker loop.

        No room anywhere (a screen too small for the card on any side): each candidate keeps the axis
        that does not separate it from the widget clamped into ``avail``, and the separating axis is
        limited to ``bounds`` (the widget's whole screen; ``avail`` when unknown) so the card's body
        never leaves the monitor or lands on a neighbour. The one covering the least of the widget
        wins; the card is transparent for input, so even then the pointer stays on the widget."""
        w, h = sz.width(), sz.height()
        lo_x, hi_x = avail.left() + _EDGE_MARGIN, avail.right() + 1 - _EDGE_MARGIN - w
        lo_y, hi_y = avail.top() + _EDGE_MARGIN, avail.bottom() + 1 - _EDGE_MARGIN - h
        candidates = cls._candidates(widget_geo, avail, sz, edge)
        for x, y, _axis in candidates:
            x = max(lo_x, min(x, hi_x))
            y = max(lo_y, min(y, hi_y))
            rect = QRect(int(x), int(y), w, h)
            if not rect.intersects(widget_geo):
                return rect

        screen_rect = bounds if bounds is not None and not bounds.isEmpty() else avail

        def body_range(lo_edge_of, hi_edge_of, size):
            # The shadow margin is transparent: only the BODY must stay inside. In the work area
            # (off the taskbar) when the body fits there, else on the widget's screen.
            for r in (avail, screen_rect):
                lo, hi = lo_edge_of(r) - _SHADOW_MARGIN, hi_edge_of(r) + 1 + _SHADOW_MARGIN - size
                if hi >= lo:
                    return lo, hi
            return lo, hi

        blo_x, bhi_x = body_range(QRect.left, QRect.right, w)
        blo_y, bhi_y = body_range(QRect.top, QRect.bottom, h)

        def clamp(v, lo, hi):
            return lo if hi < lo else max(lo, min(v, hi))

        best, best_cover = None, None
        for x, y, axis in candidates:
            if axis == "y":
                x, y = clamp(x, lo_x, hi_x), clamp(y, blo_y, bhi_y)
            else:
                x, y = clamp(x, blo_x, bhi_x), clamp(y, lo_y, hi_y)
            rect = QRect(int(x), int(y), w, h)
            cover = rect.intersected(widget_geo)
            area = 0 if cover.isEmpty() else cover.width() * cover.height()
            if best is None or area < best_cover:
                best, best_cover = rect, area
        return best

    @classmethod
    def _anchor(cls, widget_geo: QRect, avail: QRect, sz, edge: Optional[object]) -> Tuple[int, int]:
        """Top-left of ``placement`` (kept for callers that clamp it themselves - a no-op on it)."""
        rect = cls.placement(widget_geo, avail, sz, edge)
        return rect.x(), rect.y()
