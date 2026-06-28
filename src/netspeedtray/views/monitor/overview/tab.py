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

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from netspeedtray.utils import styles as su
from netspeedtray.utils.helpers import format_speed
from netspeedtray.views.monitor.overview.tiles import StatTile, UsageTile, NetworkHero

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

_REFRESH_MS = 1000


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

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Network hero (the headline) ---
        self._hero = NetworkHero(i18n, accent("down"), accent("up"))
        root.addWidget(self._hero)

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

    # ----------------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._tick()  # paint immediately so the tiles aren't blank for a second
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        super().hideEvent(event)

    def teardown(self) -> None:
        """Called by MonitorWindow on close."""
        try:
            self._timer.stop()
        except Exception:
            pass

    # ----------------------------------------------------------------- refresh

    def _tick(self) -> None:
        # hideEvent stops the timer when we leave Overview or the window minimizes; this guard is
        # belt-and-suspenders (a child's isVisible() stays True while the top-level is minimized,
        # so check the window too).
        if not self.isVisible():
            return
        win = self.window()
        if win is not None and win.isMinimized():
            return
        mw = self._main_widget
        ws = getattr(mw, "widget_state", None)
        try:
            # --- Network hero: co-equal down + up over one dual sparkline, with a peak context line.
            agg = ws.get_aggregated_speed_history() if ws is not None else []
            down = agg[-1].download if agg else 0.0
            up = agg[-1].upload if agg else 0.0
            down_series = [a.download for a in agg]
            up_series = [a.upload for a in agg]
            peak_v = max(max(down_series, default=0.0), max(up_series, default=0.0))
            sub = ""
            if down_series or up_series:
                sub = (f"{self._tr('GRAPH_PEAK_SHORT', 'Peak')}   "
                       f"↓ {self._fmt_speed(max(down_series, default=0.0))}   "
                       f"↑ {self._fmt_speed(max(up_series, default=0.0))}")
            self._hero.set(self._fmt_speed(down), self._fmt_speed(up), down_series, up_series, sub,
                           scale_label=self._fmt_speed(peak_v))

            # A discrete GPU has dedicated VRAM; an integrated one shares system RAM. Use that to label
            # the tile "iGPU" vs "GPU" (read once, also used by the VRAM tile below).
            vu, vt = getattr(mw, "vram_used", None), getattr(mw, "vram_total", None)
            integrated = vu is None and vt is None

            # --- CPU / GPU: utilisation %, temperature + power in the sub-line, sparkline from history.
            cpu = float(getattr(mw, "cpu_usage", 0.0) or 0.0)
            cpu_series = [s.value for s in ws.get_cpu_history()] if ws is not None else []
            self._tiles["cpu"].set(f"{cpu:.0f}%", cpu_series, vmax=100.0,
                                   sub_text=self._hw_sub(getattr(mw, "cpu_temp", None),
                                                         getattr(mw, "cpu_power", None)))

            # GPU tile hides on a confirmed no-GPU box (else a permanent 0% reads as a dead sensor).
            gpu_tile = self._tiles["gpu"]
            if not getattr(mw, "gpu_present", True):
                gpu_tile.setVisible(False)
            else:
                gpu_tile.setVisible(True)
                gpu_word = self._tr("ORDER_TYPE_GPU", "GPU")
                gpu_tile.set_label(("i" + gpu_word) if integrated else gpu_word)   # iGPU vs GPU
                gpu = float(getattr(mw, "gpu_usage", 0.0) or 0.0)
                gpu_series = [s.value for s in ws.get_gpu_history()] if ws is not None else []
                gpu_tile.set(f"{gpu:.0f}%", gpu_series, vmax=100.0,
                             sub_text=self._hw_sub(getattr(mw, "gpu_temp", None),
                                                   getattr(mw, "gpu_power", None)))

            # --- RAM: % of total, used/total in the sub-line.
            ru, rt = getattr(mw, "ram_used", None), getattr(mw, "ram_total", None)
            rpct = self._pct(ru, rt)
            self._ram_series.append(rpct)
            self._tiles["ram"].set(f"{rpct:.0f}%", list(self._ram_series), vmax=100.0,
                                   sub_text=self._mem_sub(ru, rt))

            # --- VRAM: hidden when there's no dedicated-VRAM reading (integrated GPUs, etc).
            vram_tile = self._tiles["vram"]
            if vu is None:
                vram_tile.setVisible(False)
            else:
                vram_tile.setVisible(True)
                vpct = self._pct(vu, vt)
                self._vram_series.append(vpct)
                vmax = 100.0 if vt else None
                vval = f"{vpct:.0f}%" if vt else f"{float(vu):.1f} GB"
                vram_tile.set(vval, list(self._vram_series) if vt else [float(vu)], vmax=vmax,
                              sub_text=self._mem_sub(vu, vt))

            today, month = (mw._hover_usage_totals() if hasattr(mw, "_hover_usage_totals")
                            else ((0.0, 0.0), (0.0, 0.0)))
            cap = mw._hover_cap_info() if hasattr(mw, "_hover_cap_info") else None
            self._usage.set(today, month, cap)
        except Exception as e:
            self.logger.debug("Overview tick skipped: %s", e)

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
        if temp is not None:
            parts.append(f"{float(temp):.0f}°C")
        if power is not None:
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
