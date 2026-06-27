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
from collections import deque
from typing import Any, Deque, Dict, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed
from netspeedtray.views.widget.preview import PreviewWidget
from netspeedtray.views.monitor.overview.tiles import StatTile, UsageTile

# Tile accents — CPU/GPU echo the graph's line colours; RAM/VRAM get their own calm hues.
_ACCENT_CPU = "#00BCD4"
_ACCENT_GPU = "#FF9800"
_ACCENT_RAM = "#4CAF50"
_ACCENT_VRAM = "#9C27B0"

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

        # Live preview tile — the real widget, bigger, on a taskbar-ish card.
        tile = QFrame()
        tile.setObjectName("previewTile")
        tile.setStyleSheet(
            f"#previewTile {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px; }}")
        tl = QHBoxLayout(tile)
        tl.setContentsMargins(16, 16, 16, 16)
        tl.addStretch(1)
        self._preview = PreviewWidget(config, i18n, width=340, height=46)
        tl.addWidget(self._preview)
        tl.addStretch(1)
        root.addWidget(tile)

        # --- Sparkline stat tiles (Network always; hardware tiles only when monitored) ---
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        specs = [("network", self._tr("MONITOR_TAB_NETWORK", "Network"), c["accent"])]
        if config.get("monitor_cpu_enabled"):
            specs.append(("cpu", self._tr("ORDER_TYPE_CPU", "CPU"), _ACCENT_CPU))
        if config.get("monitor_gpu_enabled"):
            specs.append(("gpu", self._tr("ORDER_TYPE_GPU", "GPU"), _ACCENT_GPU))
        if config.get("monitor_ram_enabled"):
            specs.append(("ram", "RAM", _ACCENT_RAM))
        if config.get("monitor_vram_enabled"):
            specs.append(("vram", "VRAM", _ACCENT_VRAM))
        for i, (key, label, accent) in enumerate(specs):
            t = StatTile(label, accent)
            self._tiles[key] = t
            grid.addWidget(t, i // _TILES_PER_ROW, i % _TILES_PER_ROW)
        for col in range(_TILES_PER_ROW):
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
        if not self.isVisible():
            return
        mw = self._main_widget
        ws = getattr(mw, "widget_state", None)
        try:
            if "network" in self._tiles:
                agg = ws.get_aggregated_speed_history() if ws is not None else []
                down = agg[-1].download if agg else 0.0
                up = agg[-1].upload if agg else 0.0
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
