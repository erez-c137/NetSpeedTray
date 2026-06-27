"""
OverviewTab — the matplotlib-free glance: "the taskbar widget, bigger", plus at-a-glance tiles.

By contract this tab NEVER imports matplotlib (it's the default tab, so a glance-only session stays
at the idle-RAM baseline). It shows the live PreviewWidget, a row of sparkline stat tiles
(Network always; CPU/GPU/RAM/VRAM gated on what's monitored), and a Today/This-month usage tile.

The tiles refresh on a 1 Hz timer that only runs while the tab is actually visible — a glance at
another tab, or a hidden window, costs nothing. All data is read defensively from the main widget
and its WidgetState, so a missing field or a teardown race degrades to a dash, never a crash.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Any, Deque, Dict, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed
from netspeedtray.views.widget.preview import PreviewWidget
from netspeedtray.views.monitor.overview.tiles import StatTile, UsageTile

# Tile accents as (dark, light) pairs — CPU/GPU echo the graph's line colours; RAM/VRAM get their
# own calm hues. The light variant keeps the thin trend line legible on the near-white light card
# (the dark cyan/green wash out as a 1.6px stroke over subtle_fill).
_ACCENTS = {
    "cpu": ("#00BCD4", "#0097A7"),
    "gpu": ("#FF9800", "#F57C00"),
    "ram": ("#4CAF50", "#388E3C"),
    "vram": ("#9C27B0", "#7B1FA2"),
}

_REFRESH_MS = 1000
_TILES_PER_ROW = 3


class OverviewTab(QWidget):
    """Overview tab content. Matplotlib-free by contract."""

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

        c = su.semantic_colors()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Live preview card — "the taskbar widget, bigger". A capped, centered card with a caption
        # so it reads as a featured object, not a small thing stranded in a wide band. Fed live
        # metrics in _tick() (same snapshot the real widget paints from).
        tile = QFrame()
        tile.setObjectName("previewTile")
        tile.setMaximumWidth(440)
        tile.setStyleSheet(
            f"#previewTile {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px; }}")
        tv = QVBoxLayout(tile)
        tv.setContentsMargins(16, 12, 16, 12)
        tv.setSpacing(8)
        cap = QLabel(self._tr("SETTINGS_PREVIEW_LABEL", "Preview"))
        cap.setFont(su.font(tokens.TYPE_CAPTION))
        cap.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        tv.addWidget(cap)
        prow = QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addStretch(1)
        self._preview = PreviewWidget(config, i18n, width=340, height=46)
        prow.addWidget(self._preview)
        prow.addStretch(1)
        tv.addLayout(prow)
        crow = QHBoxLayout()       # center the capped card in the page
        crow.setContentsMargins(0, 0, 0, 0)
        crow.addStretch(1)
        crow.addWidget(tile)
        crow.addStretch(1)
        root.addLayout(crow)

        # --- Sparkline stat tiles (Network always; hardware tiles only when monitored) ---
        dark = su.is_dark_mode()

        def accent(key: str) -> str:
            pair = _ACCENTS.get(key)
            return (pair[0] if dark else pair[1]) if pair else c["accent"]

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        specs = [("network", self._tr("MONITOR_TAB_NETWORK", "Network"), c["accent"])]
        if config.get("monitor_cpu_enabled"):
            specs.append(("cpu", self._tr("ORDER_TYPE_CPU", "CPU"), accent("cpu")))
        if config.get("monitor_gpu_enabled"):
            specs.append(("gpu", self._tr("ORDER_TYPE_GPU", "GPU"), accent("gpu")))
        if config.get("monitor_ram_enabled"):
            specs.append(("ram", self._tr("MONITOR_TILE_RAM", "RAM"), accent("ram")))
        if config.get("monitor_vram_enabled"):
            specs.append(("vram", self._tr("MONITOR_TILE_VRAM", "VRAM"), accent("vram")))
        # Balanced grid: fewest rows of <=3, then even per-row — so 1/2/4 tiles fill the width
        # instead of clinging to the left under empty stretched columns.
        rows = max(1, math.ceil(len(specs) / _TILES_PER_ROW))
        per_row = math.ceil(len(specs) / rows)
        for i, (key, label, acc) in enumerate(specs):
            t = StatTile(label, acc)
            self._tiles[key] = t
            grid.addWidget(t, i // per_row, i % per_row)
        for col in range(per_row):
            grid.setColumnStretch(col, 1)
        root.addLayout(grid)

        # --- Usage / data-cap tile ---
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
            # Feed the hero preview the same live snapshot the real widget paints from, so it's a
            # live "taskbar widget, bigger" rather than a frozen demo render.
            if hasattr(mw, "_build_metrics"):
                self._preview.set_metrics(mw._build_metrics())
                cm = getattr(mw, "_current_cycle_mode", None)
                if cm:
                    self._preview.set_cycle_mode(cm)

            if "network" in self._tiles:
                agg = ws.get_aggregated_speed_history() if ws is not None else []
                down = agg[-1].download if agg else 0.0
                up = agg[-1].upload if agg else 0.0
                # vmax=None: auto-scale the trend (no absolute Mbps ceiling exists), unlike the
                # percentage tiles which fix vmax=100.
                self._tiles["network"].set(
                    f"↓ {self._fmt_speed(down)}", [a.download for a in agg], vmax=None,
                    sub_text=f"↑ {self._fmt_speed(up)}")

            if "cpu" in self._tiles:
                cpu = float(getattr(mw, "cpu_usage", 0.0) or 0.0)
                series = [s.value for s in ws.get_cpu_history()] if ws is not None else []
                self._tiles["cpu"].set(f"{cpu:.0f}%", series, vmax=100.0)

            if "gpu" in self._tiles:
                gpu = float(getattr(mw, "gpu_usage", 0.0) or 0.0)
                series = [s.value for s in ws.get_gpu_history()] if ws is not None else []
                self._tiles["gpu"].set(f"{gpu:.0f}%", series, vmax=100.0)

            if "ram" in self._tiles:
                pct = self._pct(getattr(mw, "ram_used", None), getattr(mw, "ram_total", None))
                self._ram_series.append(pct)
                self._tiles["ram"].set(f"{pct:.0f}%", list(self._ram_series), vmax=100.0)

            if "vram" in self._tiles:
                pct = self._pct(getattr(mw, "vram_used", None), getattr(mw, "vram_total", None))
                self._vram_series.append(pct)
                self._tiles["vram"].set(f"{pct:.0f}%", list(self._vram_series), vmax=100.0)

            today, month = (mw._hover_usage_totals() if hasattr(mw, "_hover_usage_totals")
                            else ((0.0, 0.0), (0.0, 0.0)))
            cap = mw._hover_cap_info() if hasattr(mw, "_hover_cap_info") else None
            self._usage.set(today, month, cap)
        except Exception as e:
            self.logger.debug("Overview tick skipped: %s", e)

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
