"""
TelemetryStrip — a compact band of live hardware telemetry tiles for the Monitor's Hardware tab.

Surfaces the readings the stats pipeline ALREADY collects (CPU/GPU utilisation + temperature + power,
RAM and VRAM used/total) but that the Monitor didn't previously show — so the Hardware tab answers
"how hot / how loaded / how much memory" at a glance, not just the utilisation graph. It reads the
values straight off the main widget's live attributes (updated every poll by StatsController), so it
needs no extra sampling; a tile gracefully omits a reading that's unavailable (no sensor, or the
temp/power gate is off) rather than showing a dead "N/A".

Graph-free + matplotlib-free.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens


class _TeleTile(QFrame):
    """A small card: a caption (e.g. "CPU") over a live value line (e.g. "45%  ·  62°C")."""

    def __init__(self, caption: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("teleTile")
        c = su.semantic_colors()
        self.setStyleSheet(
            f"#teleTile {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CONTROL}px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(1)
        self._caption = QLabel(caption)
        self._caption.setFont(su.font(tokens.TYPE_CAPTION))
        self._caption.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._value = QLabel("—")
        self._value.setFont(su.font(tokens.TYPE_BODY_STRONG))
        self._value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        lay.addWidget(self._caption)
        lay.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text or "—")


class TelemetryStrip(QWidget):
    """A left-aligned row of telemetry tiles, built from the enabled hardware sources."""

    def __init__(self, config: Dict[str, Any], i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._i18n = i18n
        self._temps = bool(config.get("show_hardware_temps", True))
        self._power = bool(config.get("show_hardware_power", False))
        cpu_on = bool(config.get("monitor_cpu_enabled"))
        gpu_on = bool(config.get("monitor_gpu_enabled"))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(8)

        # A tile exists only when its DATA is actually collected. monitor_thread emits ram_used only
        # under monitor_cpu_enabled and vram_used only under monitor_gpu_enabled (the *_ram_/_vram_
        # flags drive the Overview sparklines, not these live attributes) — so RAM rides CPU, VRAM GPU.
        self._cpu = _TeleTile(self._tr("ORDER_TYPE_CPU", "CPU")) if cpu_on else None
        self._gpu = _TeleTile(self._tr("ORDER_TYPE_GPU", "GPU")) if gpu_on else None
        self._ram = _TeleTile(self._tr("MONITOR_TILE_RAM", "RAM")) if cpu_on else None
        self._vram = _TeleTile(self._tr("MONITOR_TILE_VRAM", "VRAM")) if gpu_on else None
        for tile in (self._cpu, self._gpu, self._ram, self._vram):
            if tile is not None:
                lay.addWidget(tile)
        lay.addStretch(1)
        # If nothing is monitored (e.g. the Hardware tab opened in a RAM-only config, where the live
        # ram/vram attributes aren't even populated), don't leave an empty band — collapse the strip.
        if self.is_empty():
            self.setVisible(False)

    def is_empty(self) -> bool:
        return all(t is None for t in (self._cpu, self._gpu, self._ram, self._vram))

    def update_from(self, w) -> None:
        """Pull the live readings off the main widget's attributes and refresh each tile. The CPU/GPU
        tiles always have a usage% to show; the memory tiles hide themselves when their reading is
        unavailable (e.g. VRAM with no PDH dedicated-usage counter) rather than showing a dead "—"."""
        if w is None:
            return
        if self._cpu is not None:
            self._cpu.set_value(self._proc_text(getattr(w, "cpu_usage", 0.0),
                                                getattr(w, "cpu_temp", None), getattr(w, "cpu_power", None)))
        if self._gpu is not None:
            self._gpu.set_value(self._proc_text(getattr(w, "gpu_usage", 0.0),
                                                getattr(w, "gpu_temp", None), getattr(w, "gpu_power", None)))
        self._update_mem(self._ram, getattr(w, "ram_used", None), getattr(w, "ram_total", None))
        self._update_mem(self._vram, getattr(w, "vram_used", None), getattr(w, "vram_total", None))

    def _update_mem(self, tile, used, total) -> None:
        if tile is None:
            return
        tile.setVisible(used is not None)   # hide rather than show a permanent "—"
        if used is not None:
            tile.set_value(self._mem_text(used, total))

    # --- formatting -------------------------------------------------------------
    def _proc_text(self, usage: float, temp: Optional[float], power: Optional[float]) -> str:
        parts: List[str] = [f"{float(usage):.0f}%"]
        if self._temps and temp is not None:
            parts.append(f"{float(temp):.0f}°C")
        if self._power and power is not None:
            parts.append(f"{float(power):.0f} W")
        return "  ·  ".join(parts)

    def _mem_text(self, used: Optional[float], total: Optional[float]) -> str:
        if used is None:
            return "—"
        if total:
            return f"{float(used):.1f} / {float(total):.1f} GB"
        return f"{float(used):.1f} GB"

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
