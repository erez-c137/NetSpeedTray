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

        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 0, 4, 0)
        self._lay.setSpacing(8)

        # The Monitor forces hardware collection while it's open, so all four sources are available
        # regardless of the widget's config flags — create every tile. Each tile shows only what it
        # actually has: CPU/GPU always have a usage%; temp/power append when collected; the memory
        # tiles hide themselves entirely when their reading is unavailable (no VRAM counter, etc).
        self._cpu = _TeleTile(self._tr("ORDER_TYPE_CPU", "CPU"))
        self._gpu = _TeleTile(self._tr("ORDER_TYPE_GPU", "GPU"))
        self._ram = _TeleTile(self._tr("MONITOR_TILE_RAM", "RAM"))
        self._vram = _TeleTile(self._tr("MONITOR_TILE_VRAM", "VRAM"))
        # Equal-width tiles (stretch 1 each, no trailing stretch) so a tile NEVER resizes when its value
        # grows — "18% · 69°C · 37 W" sits in the same box as "18%", and the cards don't shove each other
        # around. A hidden tile's slot is reclaimed (stretch 0) so the visible ones fill the width.
        self._ordered = [self._cpu, self._gpu, self._ram, self._vram]
        for tile in self._ordered:
            self._lay.addWidget(tile, 1)

    def update_from(self, w) -> None:
        """Pull the live readings off the main widget's attributes and refresh each tile. The CPU/GPU
        tiles always have a usage% to show; the memory tiles hide themselves when their reading is
        unavailable (e.g. VRAM with no PDH dedicated-usage counter) rather than showing a dead "—"."""
        if w is None:
            return
        self._cpu.set_value(self._proc_text(getattr(w, "cpu_usage", 0.0),
                                            getattr(w, "cpu_temp", None), getattr(w, "cpu_power", None)))
        # Hide the GPU tile on a confirmed no-GPU box rather than show a permanent 0%.
        gpu_present = bool(getattr(w, "gpu_present", True))
        self._set_tile_visible(self._gpu, gpu_present)
        if gpu_present:
            self._gpu.set_value(self._proc_text(getattr(w, "gpu_usage", 0.0),
                                                getattr(w, "gpu_temp", None), getattr(w, "gpu_power", None)))
        self._update_mem(self._ram, getattr(w, "ram_used", None), getattr(w, "ram_total", None))
        self._update_mem(self._vram, getattr(w, "vram_used", None), getattr(w, "vram_total", None))

    def _set_tile_visible(self, tile, visible: bool) -> None:
        """Show/hide a tile AND reclaim its stretch so the remaining tiles fill the width (no gap)."""
        tile.setVisible(visible)
        self._lay.setStretch(self._ordered.index(tile), 1 if visible else 0)

    def _update_mem(self, tile, used, total) -> None:
        if tile is None:
            return
        self._set_tile_visible(tile, used is not None)   # hide rather than show a permanent "—"
        if used is not None:
            tile.set_value(self._mem_text(used, total))

    # --- formatting -------------------------------------------------------------
    def _proc_text(self, usage: float, temp: Optional[float], power: Optional[float]) -> str:
        # Show whatever was collected: usage always; temp/power append only when present (temp flows
        # by default under the Monitor's forced collection; power only when the user opted into it).
        parts: List[str] = [f"{float(usage):.0f}%"]
        if temp is not None and float(temp) >= 1:
            parts.append(f"{float(temp):.0f}°C")
        if power is not None and float(power) >= 0.5:   # >= 0.5 so a 0.3 W reading doesn't show "0 W"
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
