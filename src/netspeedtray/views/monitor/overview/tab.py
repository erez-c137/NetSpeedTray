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
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed, format_data_size
from netspeedtray.views.monitor.overview.tiles import StatTile, UsageTile, NetworkHero
from netspeedtray.views.monitor.timeline_selector import TimelineSelector

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

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Header: period caption (left) + the timeline dropdown (right) ---
        c = su.semantic_colors()
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        self._caption = QLabel("")
        self._caption.setFont(su.font(tokens.TYPE_CAPTION))
        self._caption.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        head.addWidget(self._caption, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self._timeline = TimelineSelector(i18n, current_index=self._period_index)
        self._timeline.period_changed.connect(self._on_period_changed)
        head.addWidget(self._timeline, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        # --- Network hero (the headline) ---
        self._hero = NetworkHero(i18n, accent("down"), accent("up"))
        root.addWidget(self._hero)

        # --- Thin context strip: session uptime + session totals (left), CPU+GPU power (right) ---
        strip = QHBoxLayout()
        strip.setContentsMargins(4, 0, 4, 0)
        self._session_lbl = QLabel("")
        self._session_lbl.setFont(su.font(tokens.TYPE_CAPTION))
        self._session_lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._syspower_lbl = QLabel("")
        self._syspower_lbl.setFont(su.font(tokens.TYPE_CAPTION))
        self._syspower_lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        strip.addWidget(self._session_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        strip.addStretch(1)
        strip.addWidget(self._syspower_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(strip)

        # --- Hardware tiles: CPU / GPU / RAM / VRAM. Always built (the Monitor forces collection);
        # VRAM hides itself when there's no dedicated-VRAM reading (integrated GPUs, etc). ---
        hw = QHBoxLayout()
        hw.setContentsMargins(0, 0, 0, 0)
        hw.setSpacing(12)
        for key, label in (("cpu", self._tr("ORDER_TYPE_CPU", "CPU")),
                           ("gpu", self._tr("ORDER_TYPE_GPU", "GPU")),
                           ("ram", self._tr("MONITOR_TILE_RAM", "RAM")),
                           ("vram", self._tr("MONITOR_TILE_VRAM", "VRAM"))):
            t = StatTile(label, accent(key))
            t.clicked.connect(self._goto_hardware)   # a click drills into the Hardware tab
            self._tiles[key] = t
            hw.addWidget(t, 1)
        root.addLayout(hw)

        # --- Usage / data-cap card ---
        self._usage = UsageTile(i18n)
        root.addWidget(self._usage)
        root.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        # Separate, slower timer reloads the window's DB series + summaries (sparklines + avg/peak), so
        # the 1 Hz current-value tick never touches the DB.
        self._hist_timer = QTimer(self)
        self._hist_timer.setInterval(_HISTORY_MS)
        self._hist_timer.timeout.connect(self._reload_window)
        self._update_caption()

    # --------------------------------------------------------------- timeline / window
    def _on_period_changed(self, index: int) -> None:
        self._period_index = int(index)
        self._update_caption()
        # Persist the choice (shared with the graph's slider) without a repaint storm.
        try:
            self._main_widget.config_controller.update_config(
                {"history_period_slider_value": self._period_index}, apply_and_repaint=False)
        except Exception:
            pass
        self._reload_window()

    def _period_key(self) -> str:
        return constants.data.history_period.PERIOD_MAP.get(self._period_index, "TIMELINE_24_HOURS")

    def _update_caption(self) -> None:
        self._caption.setText(self._tr(self._period_key(), ""))

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

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        self._hist_timer.stop()
        super().hideEvent(event)

    def teardown(self) -> None:
        """Called by MonitorWindow on close."""
        for t in (self._timer, self._hist_timer):
            try:
                t.stop()
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

            # A discrete GPU has dedicated VRAM; an integrated one shares system RAM -> "iGPU".
            vu, vt = getattr(mw, "vram_used", None), getattr(mw, "vram_total", None)
            integrated = vu is None and vt is None

            self._tiles["cpu"].set(f"{float(getattr(mw, 'cpu_usage', 0.0) or 0.0):.0f}%",
                                   ser.get("cpu", []), vmax=100.0,
                                   sub_text=self._hw_sub(getattr(mw, "cpu_temp", None),
                                                         getattr(mw, "cpu_power", None)))

            gpu_tile = self._tiles["gpu"]
            if not getattr(mw, "gpu_present", True):
                gpu_tile.setVisible(False)
            else:
                gpu_tile.setVisible(True)
                gpu_word = self._tr("ORDER_TYPE_GPU", "GPU")
                gpu_tile.set_label(("i" + gpu_word) if integrated else gpu_word)
                gpu_tile.set(f"{float(getattr(mw, 'gpu_usage', 0.0) or 0.0):.0f}%",
                             ser.get("gpu", []), vmax=100.0,
                             sub_text=self._hw_sub(getattr(mw, "gpu_temp", None),
                                                   getattr(mw, "gpu_power", None)))

            ru, rt = getattr(mw, "ram_used", None), getattr(mw, "ram_total", None)
            self._tiles["ram"].set(f"{self._pct(ru, rt):.0f}%", ser.get("ram", []), vmax=100.0,
                                   sub_text=self._mem_sub(ru, rt))

            # VRAM is session-only for now (not persisted) — keeps its own rolling buffer.
            vram_tile = self._tiles["vram"]
            if vu is None:
                vram_tile.setVisible(False)
            else:
                vram_tile.setVisible(True)
                vpct = self._pct(vu, vt)
                self._vram_series.append(vpct)
                vram_tile.set(f"{vpct:.0f}%" if vt else f"{float(vu):.1f} GB",
                              list(self._vram_series) if vt else [float(vu)],
                              vmax=100.0 if vt else None, sub_text=self._mem_sub(vu, vt))

            today, month = (mw._hover_usage_totals() if hasattr(mw, "_hover_usage_totals")
                            else ((0.0, 0.0), (0.0, 0.0)))
            cap = mw._hover_cap_info() if hasattr(mw, "_hover_cap_info") else None
            self._usage.set(today, month, cap)

            self._session_lbl.setText(self._session_text(mw))
            self._syspower_lbl.setText(self._syspower_text(mw))
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

    def _syspower_text(self, mw) -> str:
        total = float(getattr(mw, "cpu_power", None) or 0.0) + float(getattr(mw, "gpu_power", None) or 0.0)
        return f"{self._tr('STAT_SYS_POWER', 'CPU+GPU power')}  {total:.0f} W" if total >= 0.5 else ""

    @staticmethod
    def _fmt_dur(secs: float) -> str:
        h, m = int(secs // 3600), int((secs % 3600) // 60)
        return f"{h}h {m}m" if h else f"{m}m"

    def _num(self, value: float) -> str:
        s = f"{value:.1f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _goto_hardware(self) -> None:
        """A hardware tile was clicked — drill into the Hardware tab for the full graph + per-process
        breakdown (the Monitor's own "more details", better than punting to Task Manager)."""
        win = self.window()
        if win is not None and hasattr(win, "select_tab"):
            try:
                win.select_tab("hardware")
            except Exception:
                pass

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
