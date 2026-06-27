"""
Overview tiles — the matplotlib-free at-a-glance cards.

Three small, self-contained widgets used by OverviewTab:

* ``Sparkline``  — a tiny, antialiased trend line + soft fill. Deliberately standalone: it does
  NOT reuse ``WidgetRenderer.draw_mini_graph`` (whose point cache is keyed to the single taskbar
  widget; sharing it across five tiles would thrash it). A sparkline is a handful of line
  segments — cheaper and safer to paint directly than to bend the widget's cache.
* ``StatTile``  — label + big current value (+ optional sub-line) over a sparkline.
* ``UsageTile`` — Today / This-month byte totals and, when a data cap is set, a progress bar.

By contract this module imports only Qt + app utils — never matplotlib (it backs the default tab).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QPainterPath, QPolygonF, QLinearGradient,
)
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QSizePolicy,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_data_size

_SPARK_POINTS = 120  #: cap the series so an all-day session can't grow the polyline unbounded


class Sparkline(QWidget):
    """A tiny trend line with a soft gradient fill, in a single accent colour."""

    def __init__(self, color: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._series: List[float] = []
        self._vmax: Optional[float] = None
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_series(self, series: List[float], vmax: Optional[float] = None) -> None:
        """Replace the data. ``vmax`` fixes the top of the scale (e.g. 100 for %); None auto-scales."""
        self._series = list(series)[-_SPARK_POINTS:]
        self._vmax = vmax
        self.update()

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
                    vmax = self._vmax if (self._vmax and self._vmax > 0) else max(self._series[0], 1.0)
                    y = base_y - (min(max(self._series[0], 0.0), vmax) / vmax) * gh
                p.drawLine(QPointF(pad, y), QPointF(pad + gw, y))
            finally:
                p.end()
            return

        vmax = self._vmax if (self._vmax and self._vmax > 0) else max(self._series)
        if vmax <= 0:
            vmax = 1.0
        step = gw / (n - 1)
        pts = [
            QPointF(pad + i * step, base_y - (min(max(v, 0.0), vmax) / vmax) * gh)
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
        finally:
            p.end()


class StatTile(QFrame):
    """A glanceable card: label, big current value, optional sub-line, and a sparkline."""

    def __init__(self, label: str, accent: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        c = su.semantic_colors()
        self.setObjectName("statTile")
        self.setStyleSheet(
            f"#statTile {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)

        self._label = QLabel(label)
        self._label.setFont(su.font(tokens.TYPE_CAPTION))
        self._label.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")

        self._value = QLabel("—")
        self._value.setFont(su.font(tokens.TYPE_TITLE))
        self._value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")

        self._sub = QLabel("")
        self._sub.setFont(su.font(tokens.TYPE_CAPTION))
        self._sub.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._sub.setVisible(False)

        self._spark = Sparkline(accent)

        lay.addWidget(self._label)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)
        lay.addWidget(self._spark, 1)

    def set(self, value_text: str, series: List[float],
            vmax: Optional[float] = None, sub_text: str = "") -> None:
        self._value.setText(value_text)
        self._sub.setText(sub_text)
        self._sub.setVisible(bool(sub_text))
        self._spark.set_series(series, vmax)


class UsageTile(QFrame):
    """Today / This-month byte totals, plus a data-cap progress bar when a cap is set."""

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()
        self.setObjectName("usageTile")
        self.setStyleSheet(
            f"#usageTile {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title = QLabel(self._tr("DATA_CAP_SECTION_TITLE", "Data usage"))
        title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        title.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        lay.addWidget(title)

        self._today = self._make_row(self._tr("USAGE_TODAY_LABEL", "Today"), c)
        self._month = self._make_row(self._tr("USAGE_THIS_MONTH_LABEL", "This Month"), c)
        lay.addLayout(self._today[0])
        lay.addLayout(self._month[0])

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

    def _make_row(self, label: str, c: dict) -> Tuple[QHBoxLayout, QLabel]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFont(su.font(tokens.TYPE_BODY))
        name.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        value = QLabel("—")
        value.setFont(su.font(tokens.TYPE_BODY))
        value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(value)
        return row, value

    def _fmt_num(self, value: float) -> str:
        """One-decimal, honouring the locale's decimal separator (so the usage rows match the
        Network tile's localized speed text in the same window — e.g. ``1,2`` on de_DE)."""
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
        """Calm accent under 80%, amber 80–99%, red at/over the cap — so the bar warns at the one
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
        if cap is None:
            self._cap_bar.setVisible(False)
            self._cap_text.setVisible(False)
            return
        used_gb, cap_gb, pct = cap
        self._apply_cap_color(pct)
        self._cap_bar.setValue(int(max(0.0, min(100.0, pct))))
        tmpl = self._tr("TRAY_DATA_CAP_PROGRESS_TEMPLATE",
                        "Data cap:   {used:.1f} / {cap:g} GB   ({pct:.0f}%)")
        try:
            self._cap_text.setText(tmpl.format(used=used_gb, cap=cap_gb, pct=pct))
        except Exception:
            self._cap_text.setText(f"{used_gb:.1f} / {cap_gb:g} GB ({pct:.0f}%)")
        self._cap_bar.setVisible(True)
        self._cap_text.setVisible(True)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
