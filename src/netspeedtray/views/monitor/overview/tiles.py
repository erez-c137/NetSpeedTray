"""
Overview tiles - the matplotlib-free at-a-glance cards.

Three small, self-contained widgets used by OverviewTab:

* ``Sparkline``  - a tiny, antialiased trend line + soft fill. Deliberately standalone: it does
  NOT reuse ``WidgetRenderer.draw_mini_graph`` (whose point cache is keyed to the single taskbar
  widget; sharing it across five tiles would thrash it). A sparkline is a handful of line
  segments - cheaper and safer to paint directly than to bend the widget's cache.
* ``StatTile``  - label + big current value (+ optional sub-line) over a sparkline.
* ``UsageTile`` - Today / This-month byte totals and, when a data cap is set, a progress bar.

By contract this module imports only Qt + app utils - never matplotlib (it backs the default tab).
"""
from __future__ import annotations

import calendar
from datetime import datetime
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QPainterPath, QPolygonF, QLinearGradient, QFont, QFontMetrics,
)
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QSizePolicy,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_data_size, format_decimal

_SPARK_POINTS = 120  #: cap the series so an all-day session can't grow the polyline unbounded


def dynamic_range(series: List[float], min_span: float = 15.0,
                  hard_min: Optional[float] = 0.0, hard_max: Optional[float] = 100.0,
                  headroom: float = 0.08) -> Tuple[float, float]:
    """A (vmin, vmax) window fitted to the data so low-but-varying activity reads in detail instead of
    as a flat line against 0-100. Enforces a minimum span (so a genuinely steady metric isn't blown up
    into dramatic noise), adds a little top headroom, and clamps to any hard bounds (0-100 for a %)."""
    vals = [float(v) for v in series if v is not None and float(v) == float(v)]
    if not vals:
        return (hard_min or 0.0, (hard_min or 0.0) + min_span)
    lo, hi = min(vals), max(vals)
    if hi - lo < min_span:                      # too flat to fill - widen to the minimum span
        pad = (min_span - (hi - lo)) / 2.0
        lo, hi = lo - pad, hi + pad
    hi += (hi - lo) * headroom                  # a little air above the peak
    if hard_min is not None:
        lo = max(hard_min, lo)
    if hard_max is not None:
        hi = min(hard_max, hi)
    if hi - lo < 1e-6:
        hi = lo + min_span
    return (lo, hi)


class Sparkline(QWidget):
    """A tiny trend line with a soft gradient fill, in a single accent colour."""

    def __init__(self, color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._series: List[float] = []
        self._series2: List[float] = []          # optional second trace (e.g. upload over download)
        self._color2: Optional[QColor] = None
        self._vmax: Optional[float] = None
        self._vmin: float = 0.0                  # bottom of the scale (non-zero = "zoom" to the data band)
        self._scale_label: str = ""              # optional top-of-scale readout (e.g. "16.9 Mbps")
        self._curve: float = 1.0                 # 1.0 = linear; <1 (e.g. 0.5) warps the axis so a single
                                                 # spike doesn't flatten the baseline (sqrt-style)
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_series(self, series: List[float], vmax: Optional[float] = None, vmin: float = 0.0,
                   curve: float = 1.0) -> None:
        """Replace the data. ``vmax`` fixes the top of the scale (None auto-scales to the data max);
        ``vmin`` is the bottom - pass a non-zero value to "zoom" the trend into its active band so a
        low-but-varying metric shows detail instead of a flat line near the floor. ``curve`` < 1 bends
        the height axis (0.5 = square-root) so brief spikes don't squash the everyday band flat."""
        self._series = list(series)[-_SPARK_POINTS:]
        self._series2 = []
        self._vmax = vmax
        self._vmin = float(vmin or 0.0)
        self._curve = float(curve or 1.0)
        self.update()

    def set_dual(self, primary: List[float], secondary: List[float], color2: str,
                 vmax: Optional[float] = None, scale_label: str = "", curve: float = 1.0) -> None:
        """Two traces sharing one scale - the primary gets the fill + line, the secondary a thinner
        line in ``color2``. Both auto-scale to their combined max unless ``vmax`` is fixed.
        ``scale_label`` (e.g. "16.9 Mbps") is drawn at the top-of-scale so the trend has magnitude.
        ``curve`` < 1 bends the axis (0.5 = sqrt) so an occasional peak doesn't flatten normal traffic."""
        self._series = list(primary)[-_SPARK_POINTS:]
        self._series2 = list(secondary)[-_SPARK_POINTS:]
        self._color2 = QColor(color2)
        self._vmax = vmax
        self._vmin = 0.0          # network reads from a true zero floor (a 0-baseline burst is meaningful)
        self._scale_label = scale_label
        self._curve = float(curve or 1.0)
        self.update()

    def _height_for(self, v: float, vmin: float, vmax: float, gh: float) -> float:
        """Map a value to a pixel height above the baseline, applying the (optional) axis curve."""
        denom = (vmax - vmin) or 1.0
        t = (min(max(v, vmin), vmax) - vmin) / denom
        if self._curve != 1.0 and t > 0.0:
            t = t ** self._curve
        return t * gh

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        n = len(self._series)
        pad = 2.0
        gw = self.width() - pad * 2
        gh = self.height() - pad * 2
        if gw <= 0 or gh <= 0:
            return
        base_y = pad + gh

        # Fewer than two points: draw a faint baseline rule so the tile keeps its visual weight
        # and reads as "collecting data" rather than half-drawn. RAM/VRAM accrue one sample per
        # tick, so this is what's on screen for the first second(s) after the tab opens.
        if n < 2:
            p = QPainter(self)
            try:
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                ghost = QColor(self._color); ghost.setAlpha(28)
                pen = QPen(ghost); pen.setWidthF(1.4); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                y = base_y
                if n == 1:
                    vmin = self._vmin
                    vmax = self._vmax if (self._vmax and self._vmax > vmin) else max(self._series[0], vmin + 1.0)
                    y = base_y - self._height_for(self._series[0], vmin, vmax, gh)
                p.drawLine(QPointF(pad, y), QPointF(pad + gw, y))
            finally:
                p.end()
            return

        # Both traces share one scale so up/down read at true relative magnitude. vmin lets a tile
        # "zoom" into a low band (e.g. CPU 5-20%) so the trend isn't a flat squiggle against 0-100.
        data_max = max(max(self._series, default=0.0), max(self._series2, default=0.0))
        vmin = self._vmin
        vmax = self._vmax if (self._vmax and self._vmax > vmin) else data_max
        if vmax <= vmin:
            vmax = vmin + 1.0
        step = gw / (n - 1)
        pts = [
            QPointF(pad + i * step, base_y - self._height_for(v, vmin, vmax, gh))
            for i, v in enumerate(self._series)
        ]

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            # Soft fill under the line.
            fill_path = QPainterPath()
            fill_path.moveTo(pts[0].x(), base_y)
            for pt in pts:
                fill_path.lineTo(pt)
            fill_path.lineTo(pts[-1].x(), base_y)
            fill_path.closeSubpath()
            grad = QLinearGradient(0.0, pad, 0.0, base_y)
            top = QColor(self._color); top.setAlpha(64)
            bot = QColor(self._color); bot.setAlpha(0)
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, bot)
            p.fillPath(fill_path, grad)

            # The trend line itself.
            pen = QPen(self._color)
            pen.setWidthF(1.6)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPolyline(QPolygonF(pts))

            # Optional second trace (upload), same scale, thinner line, no fill.
            if self._series2 and self._color2 is not None and len(self._series2) >= 2:
                n2 = len(self._series2)
                step2 = gw / (n2 - 1)
                pts2 = [
                    QPointF(pad + i * step2, base_y - self._height_for(v, vmin, vmax, gh))
                    for i, v in enumerate(self._series2)
                ]
                pen2 = QPen(self._color2)
                pen2.setWidthF(1.4)
                pen2.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen2)
                p.drawPolyline(QPolygonF(pts2))

            # Scale readout: a faint top-of-scale rule + the max value at top-left, plus a "0" baseline
            # - so the hero trend has magnitude at a glance instead of a scaleless squiggle.
            if self._scale_label:
                sc = su.semantic_colors()
                gpen = QPen(QColor(sc["card_stroke"])); gpen.setWidthF(1.0)
                p.setPen(gpen)
                p.drawLine(QPointF(pad, pad + 0.5), QPointF(pad + gw, pad + 0.5))
                f = QFont(); f.setPixelSize(9)
                p.setFont(f)
                p.setPen(QColor(sc["text_secondary"]))
                p.drawText(QRectF(pad + 2, pad + 1.0, gw - 4, 12),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), self._scale_label)
                p.drawText(QRectF(pad + 2, base_y - 13, gw - 4, 12),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom), "0")
        finally:
            p.end()


class ClickableCard(QFrame):
    """A focusable, keyboard-activatable card. Left-click, Enter, or Space emit ``clicked``, and an
    accent focus ring (drawn via a ``:focus`` border the subclass reserves in its QSS) makes keyboard
    focus visible - so the Overview's drill-in cards aren't mouse-only. Subclasses set their object
    name, QSS (including the reserved transparent border + ``:focus`` rule via ``focus_qss``), and an
    accessible name."""

    clicked = pyqtSignal()   #: emitted on left-click / Enter / Space

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # reachable by Tab, not just the mouse

    @staticmethod
    def focus_qss(obj_name: str, c: dict) -> str:
        """The Fluent card recipe for ``#obj_name``: a 1px ``card_stroke`` at rest (so the card reads as a
        seated card on the dark surface, not a flat tonal block) that brightens to the accent on hover and
        keyboard focus. All 1px, so there's no layout shift between states, and hover/focus double as the
        'this opens a detail' affordance for the clickable Overview cards."""
        return (f" #{obj_name} {{ border: 1px solid {c['card_stroke']}; }}"
                f" #{obj_name}:hover {{ border-color: {c['accent']}; }}"
                f" #{obj_name}:focus {{ border-color: {c['accent']}; }}")

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class StatTile(ClickableCard):
    """A glanceable card: label, big current value, optional sub-line, and a sparkline. Clickable -
    selecting it opens the matching detail (the Monitor routes the click to the Hardware tab)."""

    def __init__(self, label: str, accent: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        c = su.semantic_colors()
        self.setObjectName("statTile")
        self.setAccessibleName(label)
        self.setStyleSheet(
            f"#statTile {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px; }}"
            + self.focus_qss("statTile", c))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)

        self._label = QLabel(label)
        self._label.setFont(su.font(tokens.TYPE_CAPTION))
        self._label.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")

        self._value = QLabel("-")
        self._value.setFont(su.font(tokens.TYPE_TITLE))
        self._value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")

        # The sub-line ALWAYS reserves its one caption line (even when empty) - toggling its visibility
        # as a reading comes and goes (e.g. an iGPU's flaky power sub) would reflow the tile and shove
        # the title + value up and down. Reserve the height; only the text changes.
        self._sub = QLabel(" ")
        self._sub.setFont(su.font(tokens.TYPE_CAPTION))
        self._sub.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._sub.setMinimumHeight(QFontMetrics(self._sub.font()).height())

        self._spark = Sparkline(accent)

        lay.addWidget(self._label)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)
        lay.addWidget(self._spark, 1)

    def set(self, value_text: str, series: List[float],
            vmax: Optional[float] = None, sub_text: str = "", vmin: float = 0.0) -> None:
        self._value.setText(value_text)
        self._sub.setText(sub_text or " ")   # keep a blank line so the tile height never changes
        self._spark.set_series(series, vmax, vmin)

    def set_label(self, text: str) -> None:
        self._label.setText(text)
        self.setAccessibleName(text)


class NetworkHero(ClickableCard):
    """The Overview's headline card: download AND upload as co-equal large readouts over a single
    dual-trace sparkline (download filled, upload a thinner line), plus a peak/session context line.
    Clickable - selecting it opens the network Stats-detail sheet (download/upload distributions)."""

    def __init__(self, i18n, down_color: str, up_color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._down_color = down_color
        self._up_color = up_color
        c = su.semantic_colors()
        self.setObjectName("netHero")
        self.setAccessibleName(self._tr("MONITOR_TAB_NETWORK", "Network"))
        self.setStyleSheet(
            f"#netHero {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px; }}"
            + self.focus_qss("netHero", c))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel(self._tr("MONITOR_TAB_NETWORK", "Network"))
        title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        # Latency pill (top-right) - plain word first (Good/OK/Slow), the ms as quiet subtext. Wrapped
        # in a subtle inset chip so it reads as a native Win11 status badge, not floating text (the
        # status colour is carried by the inline text; the label hides itself when there's no reading).
        self._latency = QLabel("")
        self._latency.setFont(su.font(tokens.TYPE_CAPTION))
        self._latency.setStyleSheet(
            f"background: {c['card_bg']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: 10px; padding: 2px 10px;")
        self._latency.setTextFormat(Qt.TextFormat.RichText)
        title_row.addWidget(self._latency, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(title_row)

        metrics = QHBoxLayout()
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setSpacing(36)
        self._down_v = self._metric(metrics, self._tr("DOWNLOAD_ARROW", "↓"),
                                    self._tr("DOWNLOAD_LABEL", "Download"), down_color, c)
        self._up_v = self._metric(metrics, self._tr("UPLOAD_ARROW", "↑"),
                                  self._tr("UPLOAD_LABEL", "Upload"), up_color, c)
        metrics.addStretch(1)
        lay.addLayout(metrics)

        self._spark = Sparkline(down_color)
        self._spark.setMinimumHeight(90)   # the hero is the headline - give the trend real room, but
        lay.addWidget(self._spark, 1)      # not so tall the Overview needs scrolling on a 1080p screen

        self._sub = QLabel("")
        self._sub.setFont(su.font(tokens.TYPE_CAPTION))
        self._sub.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        lay.addWidget(self._sub)

    def _metric(self, parent: QHBoxLayout, arrow: str, word: str, color: str, c: dict) -> QLabel:
        block = QVBoxLayout()
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(0)
        cap = QLabel(f"<span style='color:{color};'>{arrow}</span> {word}")
        cap.setFont(su.font(tokens.TYPE_CAPTION))
        cap.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        val = QLabel("-")
        hero_font = su.font(tokens.TYPE_TITLE)   # the headline values dominate the 20px tile values
        hero_font.setPixelSize(28)
        val.setFont(hero_font)
        val.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        block.addWidget(cap)
        block.addWidget(val)
        parent.addLayout(block)
        return val

    def set(self, down_text: str, up_text: str, down_series: List[float],
            up_series: List[float], sub_text: str = "", scale_label: str = "") -> None:
        self._down_v.setText(down_text)
        self._up_v.setText(up_text)
        # curve=0.5 (sqrt): a brief 50 Mbps spike no longer squashes everyday sub-Mbps traffic flat -
        # the baseline keeps its detail while the peak still reaches the top of the scale.
        self._spark.set_dual(down_series, up_series, self._up_color, vmax=None,
                             scale_label=scale_label, curve=0.5)
        self._sub.setText(sub_text)
        self._sub.setVisible(bool(sub_text))

    def set_latency(self, html: str) -> None:
        """Set the top-right latency pill (rich text: a colour-coded word + quiet ms/loss subtext)."""
        self._latency.setText(html)
        self._latency.setVisible(bool(html))

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default


class UsageTile(QFrame):
    """Today / This-month byte totals + an avg/day and projected-month estimate, plus a data-cap
    progress bar when a cap is set (or a one-click "set a monthly limit" hint when it isn't)."""

    set_cap_requested = pyqtSignal()   #: the "set a limit" hint was clicked (no cap configured yet)

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()
        self.setObjectName("usageTile")
        self.setStyleSheet(
            f"#usageTile {{ background: {c['subtle_fill']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title = QLabel(self._tr("PANEL_DATA_USAGE_TITLE", "Data usage"))
        title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        lay.addWidget(title)

        self._today = self._make_row(self._tr("USAGE_TODAY_LABEL", "Today"), c)
        self._month = self._make_row(self._tr("USAGE_THIS_MONTH_LABEL", "This Month"), c)
        self._avgday = self._make_row(self._tr("USAGE_AVG_DAY_LABEL", "Avg / day"), c)
        self._proj = self._make_row(self._tr("USAGE_PROJECTED_LABEL", "Projected"), c)
        for row in (self._today, self._month, self._avgday, self._proj):
            lay.addLayout(row[0])

        # Cap progress (hidden unless a cap is configured).
        self._cap_bar = QProgressBar()
        self._cap_bar.setTextVisible(False)
        self._cap_bar.setFixedHeight(6)
        self._cap_bar.setRange(0, 100)
        self._cap_accent = c["accent"]
        self._cap_track = c["card_stroke"]
        self._apply_cap_color(0.0)
        self._cap_text = QLabel("")
        self._cap_text.setFont(su.font(tokens.TYPE_CAPTION))
        self._cap_text.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        lay.addSpacing(2)
        lay.addWidget(self._cap_text)
        lay.addWidget(self._cap_bar)
        self._cap_bar.setVisible(False)
        self._cap_text.setVisible(False)

        # When no cap is set, a discoverable one-click hint to configure one (Settings → Network).
        hint = self._tr("USAGE_SET_LIMIT_HINT", "Set a monthly limit")
        self._cap_hint = QLabel(f"<a href='#' style='color:{c['accent']}; text-decoration:none;'>{hint} →</a>")
        self._cap_hint.setFont(su.font(tokens.TYPE_CAPTION))
        self._cap_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cap_hint.setStyleSheet("background: transparent;")
        # Reachable + activatable by keyboard (Tab to it, Enter to fire), not mouse-only.
        self._cap_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self._cap_hint.setAccessibleName(hint)
        self._cap_hint.linkActivated.connect(lambda _: self.set_cap_requested.emit())
        lay.addWidget(self._cap_hint)
        lay.addStretch(1)   # keep rows top-aligned; fill the rest of the (taller) card

    def _make_row(self, label: str, c: dict) -> Tuple[QHBoxLayout, QLabel]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFont(su.font(tokens.TYPE_BODY))
        name.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        value = QLabel("-")
        value.setFont(su.font(tokens.TYPE_BODY))
        value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(value)
        return row, value

    def _fmt_num(self, value: float) -> str:
        """One-decimal, honouring the locale's decimal separator (so the usage rows match the
        Network tile's localized speed text in the same window - e.g. ``1,2`` on de_DE)."""
        s = f"{value:.1f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _fmt_pair(self, pair: Tuple[float, float]) -> str:
        """``(up_bytes, down_bytes)`` -> ``↓ 1.2 GB   ↑ 0.3 GB`` (locale-aware decimals)."""
        up, down = pair
        dv, du = format_data_size(down, self._i18n, precision=1)
        uv, uu = format_data_size(up, self._i18n, precision=1)
        return f"↓ {self._fmt_num(dv)} {du}   ↑ {self._fmt_num(uv)} {uu}"

    def _apply_cap_color(self, pct: float) -> None:
        """Calm accent under 80%, amber 80-99%, red at/over the cap - so the bar warns at the one
        moment it exists to (mirrors the Windows Settings data-usage bar)."""
        if pct >= 100.0:
            chunk = "#E81123"   # Win red
        elif pct >= 80.0:
            chunk = "#FFB900"   # Win amber
        else:
            chunk = self._cap_accent
        self._cap_bar.setStyleSheet(
            f"QProgressBar {{ background: {self._cap_track}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}")

    def set(self, today: Tuple[float, float], month: Tuple[float, float],
            cap: Optional[Tuple[float, float, float]]) -> None:
        self._today[1].setText(self._fmt_pair(today))
        self._month[1].setText(self._fmt_pair(month))
        # Avg/day = this month's total ÷ days elapsed; projected = that rate × days in the month - a
        # quick "am I on track" read (and what makes a limit meaningful).
        now = datetime.now()
        day = max(1, now.day)
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        m_up, m_down = month
        avg = (m_up / day, m_down / day)
        self._avgday[1].setText(self._fmt_pair(avg))
        self._proj[1].setText(self._fmt_pair((avg[0] * days_in_month, avg[1] * days_in_month)))

        if cap is None:
            self._cap_bar.setVisible(False)
            self._cap_text.setVisible(False)
            self._cap_hint.setVisible(True)     # surface the (built-but-hidden) data-cap feature
            return
        self._cap_hint.setVisible(False)
        used_gb, cap_gb, pct = cap
        self._apply_cap_color(pct)
        self._cap_bar.setValue(int(max(0.0, min(100.0, pct))))
        # Pre-format numbers with the locale decimal separator and pass plain tokens, so the template
        # carries no Python format spec (a translator can't break it, and comma-locales show "12,5"
        # not "12.5") (#10). cap drops a whole-number's decimal to mirror the old :g.
        used_s = format_decimal(used_gb, self._i18n, 1)
        cap_s = format_decimal(cap_gb, self._i18n, 0) if float(cap_gb).is_integer() else format_decimal(cap_gb, self._i18n, 1)
        pct_s = format_decimal(pct, self._i18n, 0)
        tmpl = self._tr("TRAY_DATA_CAP_PROGRESS_TEMPLATE",
                        "Data cap:   {used} / {cap} GB   ({pct}%)")
        try:
            self._cap_text.setText(tmpl.format(used=used_s, cap=cap_s, pct=pct_s))
        except Exception:
            self._cap_text.setText(f"{used_s} / {cap_s} GB ({pct_s}%)")
        self._cap_bar.setVisible(True)
        self._cap_text.setVisible(True)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
