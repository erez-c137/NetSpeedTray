"""
OverviewTab — the Monitor's control center: an at-a-glance "everything that's going on" screen.

By contract this tab NEVER imports matplotlib (it's the default tab, so a glance-only session stays
at the idle-RAM baseline). It leads with a NetworkHero (co-equal download + upload over a dual
sparkline), then a row of hardware tiles (CPU / GPU / RAM / VRAM — utilisation, temperature, memory),
then a Today/This-month data-usage card. The Monitor forces hardware collection while it's open, so
the hardware tiles show real data even when the taskbar widget has hardware monitoring off.

The tiles refresh on a 1 Hz timer that only runs while the tab is actually visible. All data is read
defensively from the main widget and its WidgetState, so a missing field or a teardown race degrades
to a dash (or a hidden tile), never a crash.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Deque, Dict, Optional

from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QToolButton, QScrollArea, QFrame,
)

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed, format_data_size
from netspeedtray.utils import summaries as S
from netspeedtray.views.monitor.overview.tiles import StatTile, UsageTile, NetworkHero, dynamic_range
from netspeedtray.views.monitor.overview.busiest_apps import BusiestAppsCard
from netspeedtray.views.monitor.timeline_selector import TimelineSelector
from netspeedtray.views.monitor.stats_detail import StatsDetailSheet, run_interactive_export

# Per-resource accent as (dark, light) pairs. Network up/down get a distinct, harmonious pair; CPU/GPU
# echo the graph's line hues; RAM/VRAM get their own calm colours. The light variant keeps the thin
# trend line legible on the near-white light card.
_ACCENTS = {
    # Download green / upload blue — the SAME codes as the standalone graph (color.DOWNLOAD/UPLOAD_LINE_COLOR)
    # so the hero and the Network-tab graph read identically.
    "down": ("#42B883", "#42B883"),
    "up":   ("#4287F5", "#4287F5"),
    "cpu":  ("#00BCD4", "#0097A7"),
    "gpu":  ("#FF9800", "#F57C00"),
    "ram":  ("#4CAF50", "#388E3C"),
    "vram": ("#9C27B0", "#7B1FA2"),
}

_REFRESH_MS = 1000        # live current-value tick
_HISTORY_MS = 6000        # DB window reload (sparklines + avg/peak) — cheap, off the 1 Hz path
_NARROW_BP = 760          # below this content width: reflow to the compact layout (2×2 tiles, stacked cards)


class OverviewTab(QWidget):
    """Overview tab content — the control center. Matplotlib-free by contract."""

    stat_type = "overview"

    def __init__(self, main_widget, config: Dict[str, Any], i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._main_widget = main_widget
        self._config = config
        self._i18n = i18n
        self.logger = logging.getLogger("NetSpeedTray.OverviewTab")
        self._tiles: Dict[str, StatTile] = {}
        # RAM/VRAM have no WidgetState history deque, so the tab keeps its own rolling buffer.
        self._ram_series: Deque[float] = deque(maxlen=120)
        self._vram_series: Deque[float] = deque(maxlen=120)

        dark = su.is_dark_mode()

        def accent(key: str) -> str:
            pair = _ACCENTS.get(key)
            return (pair[0] if dark else pair[1]) if pair else su.semantic_colors()["accent"]

        # The global timeline scopes every card to real DB history; default to the saved period (24h
        # if unset), so the control center opens on history, not "since the page opened".
        self._period_index = int(config.get("history_period_slider_value", 2) or 2)
        self._series: Dict[str, Any] = {}     # latest per-metric sparkline series for the active window
        self._win_summ: Dict[str, Any] = {}   # latest per-metric WindowSummary for the active window

        # The tab itself holds ONLY a scroll area; all cards live in an inner content widget. When the
        # window is shorter than the content needs, it scrolls instead of squeezing widgets past their
        # minimums (which is what made the hero's avg/peak line ride up over the sparkline). Horizontal
        # scrolling is off — width is handled by the responsive reflow (resizeEvent) instead.
        self._narrow = True   # provisional; resolved by the first resizeEvent
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        outer.addWidget(self._scroll)

        c = su.semantic_colors()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Header: a discoverable Export action (left) + the timeline dropdown (right). The Export
        # button makes "pull the numbers" a first-class action instead of a hidden click-the-hero gesture.
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        self._export_btn = QToolButton()
        self._export_btn.setText(f"⤓  {self._tr('OVERVIEW_EXPORT', 'Export…')}")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setToolTip(self._tr("OVERVIEW_EXPORT_TIP",
                                              "Export this period's stats (summary + raw CSV)"))
        r = tokens.RADIUS_CONTROL
        self._export_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {c['text_secondary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: {r}px; padding: 4px 12px; }}"
            f" QToolButton:hover {{ color: {c['text_primary']}; border-color: {c['accent']}; }}")
        self._export_btn.clicked.connect(self._export_window)
        head.addWidget(self._export_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self._timeline = TimelineSelector(i18n, current_index=self._period_index)
        self._timeline.period_changed.connect(self._on_period_changed)
        head.addWidget(self._timeline, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        # --- Network hero (the headline) ---
        self._hero = NetworkHero(i18n, accent("down"), accent("up"))
        self._hero.clicked.connect(lambda: self._open_detail("network"))
        root.addWidget(self._hero)

        # --- Thin context strip: session uptime + totals (left), and on the right the one thing you
        # can't read off the cards — true System power when the platform exposes it, otherwise the
        # connection-health summary (drops/loss over the window) so a latency blip you missed live is
        # still visible. (CPU/GPU power already live on the cards, so repeating it here was noise.) ---
        strip = QHBoxLayout()
        strip.setContentsMargins(4, 0, 4, 0)
        self._session_lbl = QLabel("")
        self._session_lbl.setFont(su.font(tokens.TYPE_CAPTION))
        self._session_lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._context_r_lbl = QLabel("")
        self._context_r_lbl.setFont(su.font(tokens.TYPE_CAPTION))
        self._context_r_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._context_r_lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        strip.addWidget(self._session_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        strip.addStretch(1)
        strip.addWidget(self._context_r_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(strip)
        self._latency_events: Dict[str, Any] = {}   # outage_summary for the active window

        # --- Hardware tiles: CPU / GPU / RAM / VRAM. Always built (the Monitor forces collection);
        # VRAM hides itself when there's no dedicated-VRAM reading (integrated GPUs, etc). Laid out in a
        # GRID so they can reflow 1×4 (wide) → 2×2 (narrow) without ever crushing or overlapping. ---
        self._hw_grid = QGridLayout()
        self._hw_grid.setContentsMargins(0, 0, 0, 0)
        self._hw_grid.setHorizontalSpacing(12)
        self._hw_grid.setVerticalSpacing(12)
        self._tile_order = ["cpu", "gpu", "ram", "vram"]
        for key, label in (("cpu", self._tr("ORDER_TYPE_CPU", "CPU")),
                           ("gpu", self._tr("ORDER_TYPE_GPU", "GPU")),
                           ("ram", self._tr("MONITOR_TILE_RAM", "RAM")),
                           ("vram", self._tr("MONITOR_TILE_VRAM", "VRAM"))):
            t = StatTile(label, accent(key))
            t.clicked.connect(lambda k=key: self._open_detail(k))   # a click opens the stat sheet
            self._tiles[key] = t
        root.addLayout(self._hw_grid)

        # --- Bottom row: Data-usage card + Top-talkers card. Grid so it reflows side-by-side (wide) →
        # stacked (narrow). ---
        self._bottom_grid = QGridLayout()
        self._bottom_grid.setContentsMargins(0, 0, 0, 0)
        self._bottom_grid.setHorizontalSpacing(12)
        self._bottom_grid.setVerticalSpacing(12)
        self._usage = UsageTile(i18n)
        self._usage.set_cap_requested.connect(self._open_settings)
        self._busiest = BusiestAppsCard(i18n)
        self._busiest.go_to_network.connect(self._goto_network)
        root.addLayout(self._bottom_grid)
        root.addStretch(1)

        # Seed the compact layout (all tiles; _render narrows the set once it knows what has data).
        self._visible_keys = list(self._tile_order)
        self._tile_sig = None
        self._set_visible_tiles(self._tile_order)
        self._place_bottom(stacked=True)
        self._scroll.setWidget(content)

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        # Separate, slower timer reloads the window's DB series + summaries (sparklines + avg/peak), so
        # the 1 Hz current-value tick never touches the DB.
        self._hist_timer = QTimer(self)
        self._hist_timer.setInterval(_HISTORY_MS)
        self._hist_timer.timeout.connect(self._reload_window)

    # --------------------------------------------------------------- responsive layout
    def _set_visible_tiles(self, visible_keys) -> None:
        """Show exactly `visible_keys` (in order) and reflow them to FILL the row — so e.g. CPU/GPU/RAM
        with no VRAM tile grow to thirds instead of leaving a hole where the 4th tile would be."""
        for k in self._tile_order:
            self._tiles[k].setVisible(k in visible_keys)
        self._visible_keys = list(visible_keys)
        sig = (tuple(visible_keys), self._narrow)
        if sig != self._tile_sig:
            self._tile_sig = sig
            self._layout_tiles(visible_keys)

    def _layout_tiles(self, visible_keys) -> None:
        """Place the visible tiles in a grid: up to 4 columns when wide, 2 when narrow, but never more
        than there are tiles — and the last tile spans any leftover columns so a row is always full."""
        while self._hw_grid.count():
            self._hw_grid.takeAt(0)          # detach (widgets stay parented); we re-add below
        for col in range(4):
            self._hw_grid.setColumnStretch(col, 0)
        if not visible_keys:
            return
        max_cols = 2 if self._narrow else 4
        cols = max(1, min(len(visible_keys), max_cols))
        last = len(visible_keys) - 1
        for i, key in enumerate(visible_keys):
            r, c = divmod(i, cols)
            span = (cols - c) if i == last else 1   # fill the rest of the final row
            self._hw_grid.addWidget(self._tiles[key], r, c, 1, span)
        for c in range(cols):
            self._hw_grid.setColumnStretch(c, 1)

    def _place_bottom(self, stacked: bool) -> None:
        """Data-usage + Top-talkers cards side by side (wide) or stacked (narrow)."""
        self._bottom_grid.addWidget(self._usage, 0, 0)
        if stacked:
            self._bottom_grid.addWidget(self._busiest, 1, 0)
            self._bottom_grid.setColumnStretch(0, 1)
            self._bottom_grid.setColumnStretch(1, 0)
        else:
            self._bottom_grid.addWidget(self._busiest, 0, 1)
            self._bottom_grid.setColumnStretch(0, 1)
            self._bottom_grid.setColumnStretch(1, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # Reflow only when crossing the breakpoint (cheap; never every pixel). Use the tab's own width —
        # it's already updated when resizeEvent fires, whereas the scroll viewport lags a layout pass.
        narrow = self.width() < _NARROW_BP
        if narrow != self._narrow:
            self._narrow = narrow
            self._set_visible_tiles(self._visible_keys)   # re-flow current tiles for the new width
            self._place_bottom(stacked=narrow)

    # --------------------------------------------------------------- timeline / window
    def _on_period_changed(self, index: int) -> None:
        self._period_index = int(index)
        # Persist the choice (shared with the graph's slider) without a repaint storm.
        try:
            self._main_widget.config_controller.update_config(
                {"history_period_slider_value": self._period_index}, apply_and_repaint=False)
        except Exception:
            pass
        self._reload_window()

    def _period_key(self) -> str:
        return constants.data.history_period.PERIOD_MAP.get(self._period_index, "TIMELINE_24_HOURS")

    def _window(self):
        """(start, end, is_session) for the active period."""
        hp = constants.data.history_period
        key = self._period_key()
        now = datetime.now()
        session_start = getattr(self._main_widget, "session_start_time", None)
        ws = getattr(self._main_widget, "widget_state", None)
        earliest = None
        if key in ("TIMELINE_ALL", "TIMELINE_SYSTEM_UPTIME") and ws is not None:
            try:
                earliest = ws.get_earliest_data_timestamp()
            except Exception:
                earliest = None
        start = hp.get_start_time(key, now, session_start, None, earliest)
        if start is None:
            start = now - timedelta(hours=24)
        return start, now, (key == "TIMELINE_SESSION")

    # ----------------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._reload_window()   # load the window's DB series + summaries, then paint immediately
        self._timer.start()
        self._hist_timer.start()
        self._busiest.start()   # start the per-app connection sampler (idles again on hide)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        self._hist_timer.stop()
        self._busiest.stop()
        super().hideEvent(event)

    def teardown(self) -> None:
        """Called by MonitorWindow on close."""
        for t in (self._timer, self._hist_timer):
            try:
                t.stop()
            except Exception:
                pass
        try:
            self._busiest.teardown()
        except Exception:
            pass

    # ----------------------------------------------------------------- refresh

    def _reload_window(self) -> None:
        """Load the selected window's series (sparklines) + honest summaries (avg/peak) from the DB —
        or the live in-memory deques for the Session window — then repaint. Runs every few seconds and
        on a period change, NOT on the 1 Hz tick, so the DB read never touches the current-value path."""
        if not self.isVisible():
            return
        ws = getattr(self._main_widget, "widget_state", None)
        if ws is None:
            self._render()
            return
        start, end, is_session = self._window()
        poll = float(self._config.get("update_rate", 1.0) or 1.0)
        try:
            if is_session:
                agg = ws.get_aggregated_speed_history()
                self._series = {
                    "down": [a.download for a in agg], "up": [a.upload for a in agg],
                    "cpu": [s.value for s in ws.get_cpu_history()],
                    "gpu": [s.value for s in ws.get_gpu_history()],
                    "ram": [s.value for s in ws.get_ram_history()],
                }
            else:
                net = ws.get_speed_history(start, end, None, resolution='auto')
                self._series = {
                    "down": [r[2] for r in net], "up": [r[1] for r in net],
                    "cpu": [v for _, v in ws.get_hardware_history("cpu", start, end)],
                    "gpu": [v for _, v in ws.get_hardware_history("gpu", start, end)],
                    "ram": [v for _, v in ws.get_hardware_history("ram", start, end)],
                }
            self._win_summ = {
                "down": ws.summarize_network("download", start, end, None, poll),
                "up": ws.summarize_network("upload", start, end, None, poll),
                "cpu": ws.summarize_hardware("cpu", start, end, poll),
                "gpu": ws.summarize_hardware("gpu", start, end, poll),
                "ram": ws.summarize_hardware("ram", start, end, poll),
            }
            # Connection-drop events over the window (from the persisted gateway-timeout series), so a
            # latency blip you missed live is still counted. Cheap; off the 1 Hz path.
            if self._config.get("latency_enabled", True):
                self._latency_events = S.outage_summary(
                    ws.get_hardware_history("latency_gw_timeout", start, end))
            else:
                self._latency_events = {}
        except Exception as e:
            self.logger.debug("Overview window reload skipped: %s", e)
        self._render()

    def _tick(self) -> None:
        # hideEvent stops the timer when we leave Overview or the window minimizes; this guard is
        # belt-and-suspenders (a child's isVisible() stays True while the top-level is minimized).
        if not self.isVisible():
            return
        win = self.window()
        if win is not None and win.isMinimized():
            return
        self._render()

    def _render(self) -> None:
        """Paint the cards: live CURRENT values (1 Hz) over the cached window series + avg/peak."""
        mw = self._main_widget
        ser = self._series
        summ = self._win_summ
        try:
            # --- Network hero: live current ↓/↑ over the window's dual sparkline; sub = window avg+peak.
            down = float(getattr(mw, "download_speed", 0.0) or 0.0)
            up = float(getattr(mw, "upload_speed", 0.0) or 0.0)
            down_series, up_series = ser.get("down", []), ser.get("up", [])
            peak_v = max(max(down_series, default=0.0), max(up_series, default=0.0))
            sd, su_ = summ.get("down"), summ.get("up")
            sub = ""
            if sd is not None and su_ is not None and sd.count:
                sub = (f"{self._tr('STAT_AVG_SHORT', 'avg')}  ↓ {self._fmt_speed(sd.avg or 0)}"
                       f"  ↑ {self._fmt_speed(su_.avg or 0)}      "
                       f"{self._tr('GRAPH_PEAK_SHORT', 'Peak')}  ↓ {self._fmt_speed(sd.max or 0)}"
                       f"  ↑ {self._fmt_speed(su_.max or 0)}")
            self._hero.set(self._fmt_speed(down), self._fmt_speed(up), down_series, up_series, sub,
                           scale_label=self._fmt_speed(peak_v))
            self._hero.set_latency(self._latency_html(mw))

            # A discrete GPU has dedicated VRAM; an integrated one shares system RAM -> "iGPU".
            vu, vt = getattr(mw, "vram_used", None), getattr(mw, "vram_total", None)
            integrated = vu is None and vt is None

            # Each utilisation sparkline auto-zooms to its own active band (dynamic_range), so a
            # low-but-varying metric (e.g. CPU mostly 5–20%) reads in detail instead of as a flat
            # squiggle against a fixed 0–100% — with a minimum span so a steady metric isn't blown up.
            cpu_series = ser.get("cpu", [])
            cpu_lo, cpu_hi = dynamic_range(cpu_series)
            self._tiles["cpu"].set(f"{float(getattr(mw, 'cpu_usage', 0.0) or 0.0):.0f}%",
                                   cpu_series, vmax=cpu_hi, vmin=cpu_lo,
                                   sub_text=self._hw_sub(getattr(mw, "cpu_temp", None),
                                                         getattr(mw, "cpu_power", None)))

            gpu_present = bool(getattr(mw, "gpu_present", True))
            if gpu_present:
                gpu_word = self._tr("ORDER_TYPE_GPU", "GPU")
                self._tiles["gpu"].set_label(("i" + gpu_word) if integrated else gpu_word)
                gpu_series = ser.get("gpu", [])
                gpu_lo, gpu_hi = dynamic_range(gpu_series)
                self._tiles["gpu"].set(f"{float(getattr(mw, 'gpu_usage', 0.0) or 0.0):.0f}%",
                                       gpu_series, vmax=gpu_hi, vmin=gpu_lo,
                                       sub_text=self._hw_sub(getattr(mw, "gpu_temp", None),
                                                             getattr(mw, "gpu_power", None)))

            ru, rt = getattr(mw, "ram_used", None), getattr(mw, "ram_total", None)
            ram_series = ser.get("ram", [])
            ram_lo, ram_hi = dynamic_range(ram_series)
            self._tiles["ram"].set(f"{self._pct(ru, rt):.0f}%", ram_series, vmax=ram_hi, vmin=ram_lo,
                                   sub_text=self._mem_sub(ru, rt))

            # VRAM is session-only for now (not persisted) — keeps its own rolling buffer. There's no
            # dedicated VRAM reading when it's None OR ~0 (an iGPU reports 0 dedicated VRAM).
            vram_reading = vu is not None and float(vu) >= 0.05
            if vram_reading:
                vpct = self._pct(vu, vt)
                self._vram_series.append(vpct)
                if vt:
                    vlo, vhi = dynamic_range(list(self._vram_series))
                    self._tiles["vram"].set(f"{vpct:.0f}%", list(self._vram_series),
                                            vmax=vhi, vmin=vlo, sub_text=self._mem_sub(vu, vt))
                else:
                    self._tiles["vram"].set(f"{float(vu):.1f} GB", [float(vu)],
                                            vmax=None, sub_text=self._mem_sub(vu, vt))

            # Show only the tiles that actually have data, reflowed to fill the width (no empty hole
            # where a missing GPU/VRAM tile would sit). Order preserved: CPU · GPU · RAM · VRAM.
            visible = [k for k in self._tile_order
                       if k in ("cpu", "ram")
                       or (k == "gpu" and gpu_present)
                       or (k == "vram" and vram_reading)]
            self._set_visible_tiles(visible)

            today, month = (mw._hover_usage_totals() if hasattr(mw, "_hover_usage_totals")
                            else ((0.0, 0.0), (0.0, 0.0)))
            cap = mw._hover_cap_info() if hasattr(mw, "_hover_cap_info") else None
            self._usage.set(today, month, cap)

            self._session_lbl.setText(self._session_text(mw))
            self._context_r_lbl.setText(self._context_right_text(mw))
        except Exception as e:
            self.logger.debug("Overview render skipped: %s", e)

    def _session_text(self, mw) -> str:
        start = getattr(mw, "session_start_time", None)
        if start is None:
            return ""
        secs = max(0.0, (datetime.now() - start).total_seconds())
        parts = [f"{self._tr('STAT_SESSION_LABEL', 'Session')} {self._fmt_dur(secs)}"]
        ws = getattr(mw, "widget_state", None)
        if ws is not None:
            try:
                u, d = ws.get_total_bandwidth_for_period(start, datetime.now())
                dv, du = format_data_size(d, self._i18n, precision=1)
                uv, uu = format_data_size(u, self._i18n, precision=1)
                parts.append(f"↓ {self._num(dv)} {du}  ↑ {self._num(uv)} {uu}")
            except Exception:
                pass
        return "    ·    ".join(parts)

    def _context_right_text(self, mw) -> str:
        """The strip's right slot shows the one thing the cards DON'T already say: true System power
        when the platform exposes it (RAPL PSYS / battery discharge), otherwise the window's connection
        health — drop count + last drop, so a latency blip you missed live is still on screen. (CPU/GPU
        power is on the cards; repeating it here was redundant.)"""
        sysp = getattr(mw, "system_power", None)
        if sysp is not None and float(sysp) >= 0.5:
            return f"{self._tr('STAT_SYS_POWER_TRUE', 'System')} {float(sysp):.0f} W"
        return self._connection_health_html(mw)

    def _connection_health_html(self, mw) -> str:
        """Window connection-health: drop count + last-drop time (green when clean, amber/red when not).
        Empty when latency monitoring is off or no probe has run yet."""
        if not self._config.get("latency_enabled", True):
            return ""
        ev = self._latency_events or {}
        count = int(ev.get("count", 0) or 0)
        # Only claim "no drops" once we actually have latency history; otherwise stay quiet.
        gw = getattr(mw, "latency_gw", None)
        anchor = getattr(mw, "latency_anchor", None)
        if count == 0 and gw is None and anchor is None:
            return ""
        label = self._tr("CONN_HEALTH_LABEL", "Connection")
        if count == 0:
            return (f"<span style='color:#3FB950;'>{label}: "
                    f"{self._tr('CONN_HEALTH_STEADY', 'steady')}</span>")
        color = "#E81123" if count >= 3 else "#FFB900"
        last = ev.get("last_start")
        lt = last.strftime("%H:%M") if hasattr(last, "strftime") else ""
        drops = self._tr("CONN_HEALTH_DROPS", "drops")
        out = f"<span style='color:{color};'>{label}: {count} {drops}</span>"
        if lt:
            sub = su.semantic_colors()["text_secondary"]
            out += f"  <span style='color:{sub};'>· {self._tr('STATS_DETAIL_LAST', 'last')} {lt}</span>"
        return out

    def _export_window(self) -> None:
        """The header Export action — write the two-file stats export for the active timeline window."""
        ws = getattr(self._main_widget, "widget_state", None)
        if ws is None:
            return
        from netspeedtray import __version__
        start, end, _is_session = self._window()
        label = self._timeline.current_label() if hasattr(self._timeline, "current_label") \
            else self._period_key()
        run_interactive_export(self.window(), ws, start, end, label, self._config, self._i18n, __version__)

    def _goto_network(self) -> None:
        win = self.window()
        if win is not None and hasattr(win, "select_tab"):
            try:
                win.select_tab("network")
            except Exception:
                pass

    def _open_settings(self) -> None:
        """Open the app settings (the data-cap controls live on its Network page)."""
        try:
            self._main_widget.show_settings()
        except Exception as e:
            self.logger.debug("Could not open settings from usage card: %s", e)

    @staticmethod
    def _fmt_dur(secs: float) -> str:
        h, m = int(secs // 3600), int((secs % 3600) // 60)
        return f"{h}h {m}m" if h else f"{m}m"

    def _num(self, value: float) -> str:
        s = f"{value:.1f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _latency_html(self, mw) -> str:
        """Latency pill: a colour-coded plain word (Good/OK/Slow — the panel insisted avg-ms is a bad
        headline) with the ms + loss% as quiet subtext. Internet (public anchor) latency wins over the
        gateway when the user opted into the public probe."""
        gw = getattr(mw, "latency_gw", None)
        anchor = getattr(mw, "latency_anchor", None)
        loss = float(getattr(mw, "latency_loss", 0.0) or 0.0)
        ms = anchor if anchor is not None else gw
        if ms is None and loss <= 0:
            return ""   # no probe data yet
        if ms is None or loss >= 5 or (ms is not None and ms >= 150):
            word, color = self._tr("LATENCY_SLOW", "Slow"), "#E81123"
        elif (ms is not None and ms >= 50) or loss >= 1:
            word, color = self._tr("LATENCY_OK", "OK"), "#FFB900"
        else:
            word, color = self._tr("LATENCY_GOOD", "Good"), "#3FB950"
        sub = su.semantic_colors()["text_secondary"]
        out = f"<span style='color:{color};'>{self._tr('LATENCY_LABEL', 'Internet')}: {word}</span>"
        detail = []
        if ms is not None:
            detail.append(f"{ms:.0f} ms")
        if loss > 0:
            detail.append(f"{loss:.0f}% loss")
        if detail:
            out += f"  <span style='color:{sub};'>· {' · '.join(detail)}</span>"
        return out

    def _detail_subjects(self, metric: str):
        """The subject list a card opens its Stats-detail sheet with. Hardware cards lead with their
        utilisation and add temperature/power when a sensor reported them; network leads with both
        directions plus gateway latency. (Empty secondary blocks drop out inside the sheet.)"""
        cpu = self._tr("ORDER_TYPE_CPU", "CPU")
        gpu = self._tr("ORDER_TYPE_GPU", "GPU")
        if metric == "network":
            return [
                {"key": "download", "label": self._tr("DOWNLOAD_LABEL", "Download"),
                 "unit": "Mbps", "kind": "net_down", "primary": True},
                {"key": "upload", "label": self._tr("UPLOAD_LABEL", "Upload"),
                 "unit": "Mbps", "kind": "net_up", "primary": True},
                {"key": "latency_gw", "label": self._tr("LATENCY_LABEL", "Internet"),
                 "unit": "ms", "kind": "hw"},
            ]
        if metric == "cpu":
            return [
                {"key": "cpu", "label": cpu, "unit": "%", "kind": "hw", "primary": True},
                {"key": "cpu_temp", "label": f"{cpu} {self._tr('STAT_TEMP', 'temperature')}",
                 "unit": "°C", "kind": "hw"},
                {"key": "cpu_power", "label": f"{cpu} {self._tr('STAT_POWER', 'power')}",
                 "unit": "W", "kind": "hw"},
            ]
        if metric == "gpu":
            return [
                {"key": "gpu", "label": gpu, "unit": "%", "kind": "hw", "primary": True},
                {"key": "gpu_temp", "label": f"{gpu} {self._tr('STAT_TEMP', 'temperature')}",
                 "unit": "°C", "kind": "hw"},
                {"key": "gpu_power", "label": f"{gpu} {self._tr('STAT_POWER', 'power')}",
                 "unit": "W", "kind": "hw"},
            ]
        if metric == "ram":
            return [{"key": "ram", "label": self._tr("MONITOR_TILE_RAM", "RAM"),
                     "unit": "%", "kind": "hw", "primary": True}]
        if metric == "vram":
            return [{"key": "vram", "label": self._tr("MONITOR_TILE_VRAM", "VRAM"),
                     "unit": "%", "kind": "hw", "primary": True}]
        return []

    def _open_detail(self, metric: str) -> None:
        """Open the Stats-detail sheet for the clicked card, scoped to the active timeline window."""
        ws = getattr(self._main_widget, "widget_state", None)
        if ws is None:
            return
        subjects = self._detail_subjects(metric)
        if not subjects:
            return
        from netspeedtray import __version__
        start, end, _is_session = self._window()
        label = self._timeline.current_label() if hasattr(self._timeline, "current_label") \
            else self._period_key()
        try:
            sheet = StatsDetailSheet(ws, subjects, (start, end, label), self._config, self._i18n,
                                     app_version=__version__, parent=self.window())
            sheet.exec()
        except Exception as e:
            self.logger.error("Could not open stats detail: %s", e, exc_info=True)

    def _hw_sub(self, temp: Optional[float], power: Optional[float]) -> str:
        """CPU/GPU sub-line: temperature and (when collected) power, e.g. "62°C  ·  35 W"."""
        parts = []
        if temp is not None and float(temp) >= 1:
            parts.append(f"{float(temp):.0f}°C")
        # >= 0.5 W, not > 0: a flaky iGPU power reading of 0.3 W rounds to "0 W" and then vanishes,
        # which would make the sub-line flicker — only show power that actually displays as >= 1 W.
        if power is not None and float(power) >= 0.5:
            parts.append(f"{float(power):.0f} W")
        return "  ·  ".join(parts)

    def _mem_sub(self, used: Optional[float], total: Optional[float]) -> str:
        if used is None:
            return ""
        if total:
            return f"{float(used):.1f} / {float(total):.1f} GB"
        return f"{float(used):.1f} GB"

    @staticmethod
    def _pct(used: Optional[float], total: Optional[float]) -> float:
        if used and total and total > 0:
            return max(0.0, min(100.0, (used / total) * 100.0))
        return 0.0

    def _fmt_speed(self, bps: float) -> str:
        cfg = self._config
        return format_speed(
            bps, self._i18n,
            force_mega_unit=(cfg.get("speed_display_mode") == "always_mbps"),
            decimal_places=int(cfg.get("decimal_places", 1)),
            unit_type=cfg.get("unit_type", "bits_decimal"),
            short_labels=cfg.get("short_unit_labels", False),
        )

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
