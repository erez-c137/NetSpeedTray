"""
Shared widget paint path (refactor C1 - the keystone).

The live taskbar widget (`views/widget/main.py`) and every off-taskbar *preview* of it
(the Settings live-preview, the Monitor Overview tile) must draw **identically** - same
modes, same layouts, same arrows, same graph. Historically that logic lived inside
`NetworkSpeedWidget.paintEvent` and read directly from `self.*`, so a preview could only
be faked by spinning up a real widget.

This module lifts that logic into one pure function, `render_widget`, parameterised on a
`WidgetMetrics` snapshot plus the layout context. `paintEvent` builds a snapshot from its
live state and calls it; `PreviewWidget` builds a synthetic snapshot and calls the same
function. There is exactly one place the widget knows how to draw itself.

Nothing here owns a QWidget or any live state - it takes a `QPainter`, a `WidgetRenderer`,
a `RenderConfig`, and the snapshot, and draws. That keeps it trivially testable and reusable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QImage

from netspeedtray import constants
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer


@dataclass
class WidgetMetrics:
    """
    An immutable-ish snapshot of everything the widget draws in a single frame.

    The live widget fills this from its update slots; a preview fills it with synthetic or
    held values. Histories are plain lists of floats (bytes/sec for network, percent for
    CPU/GPU) sized to the mini-graph window.
    """
    upload_mbps: float = 0.0
    download_mbps: float = 0.0
    cpu_usage: Optional[float] = 0.0
    gpu_usage: Optional[float] = 0.0
    cpu_temp: Optional[float] = None
    gpu_temp: Optional[float] = None
    cpu_power: Optional[float] = None
    gpu_power: Optional[float] = None
    ram_used: Optional[float] = None
    ram_total: Optional[float] = None
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    net_history: List[Any] = field(default_factory=list)   # AggregatedSpeedData items
    cpu_history: List[float] = field(default_factory=list)
    gpu_history: List[float] = field(default_factory=list)
    identity_band: Optional[str] = None        # v2.1 Wi-Fi band tag ("5G"/"2.4G") or None when off/unknown/hidden
    identity_band_color: Optional[str] = None  # band tint (hex) or None for the widget's default text color
    identity_band_solid: bool = False          # True = solid alert pill; False = outline pill
    identity_ssid: Optional[str] = None        # v2.1 truncated SSID / connection name, or None when off/blocked

    def net_bytes(self) -> Tuple[float, float]:
        """(upload, download) in bytes/sec from the stored Mbps - the renderer's unit."""
        up = (self.upload_mbps * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
        dw = (self.download_mbps * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
        return up, dw


def font_from_config(config: Dict[str, Any]) -> QFont:
    """
    Build the main display QFont from a config dict - mirrors
    ``WidgetLayoutManager.set_font`` so a preview's text metrics match the live widget.
    """
    family = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
    size = config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)
    weight_val = config.get("font_weight", constants.config.defaults.DEFAULT_FONT_WEIGHT)
    if isinstance(weight_val, int):
        weight = weight_val
    elif isinstance(weight_val, str):
        weight = {"normal": QFont.Weight.Normal, "bold": QFont.Weight.Bold}.get(
            weight_val.lower(), QFont.Weight.Normal)
    else:
        weight = QFont.Weight.Normal
    return QFont(family, int(size), weight)


def demo_metrics() -> WidgetMetrics:
    """
    Synthetic, self-explanatory values for previews. Numbers are chosen to read clearly
    (a busy-but-not-maxed machine) so a glance teaches what each field shows. Histories are
    left empty here; the Settings preview feed (C5) supplies a live synthetic stream.
    """
    return WidgetMetrics(
        upload_mbps=12.4, download_mbps=88.6,
        cpu_usage=37.0, gpu_usage=52.0,
        cpu_temp=58.0, gpu_temp=64.0,
        cpu_power=35.0, gpu_power=120.0,
        ram_used=9.2, ram_total=16.0,
        vram_used=3.1, vram_total=8.0,
    )


# Auto-Cycle phases of the separate upload/download graphs: with the split on, they replace the combined
# "network_only" phase in the rotation, so each direction gets the whole widget and the widget keeps its size.
SPLIT_PHASES: Tuple[str, str] = ("network_upload", "network_download")


def split_active(config: RenderConfig) -> bool:
    """Separate upload/download graphs are on - they need the mini graph itself to be on."""
    return bool(config.graph_enabled and config.graph_split_directions)


def split_network_phases(swap_upload_download: bool) -> List[str]:
    """The two network phases of the Auto-Cycle rotation, in the readout's order (upload first unless swapped)."""
    return list(reversed(SPLIT_PHASES)) if swap_upload_download else list(SPLIT_PHASES)


def _split_geometry(renderer: WidgetRenderer, config: RenderConfig, metrics: WidgetMetrics,
                    network_width: Optional[int]) -> Tuple[int, int]:
    """(panel width, badge reserve) of the side-by-side split view. A panel is one network block - the
    layout manager's network width, which includes the identity badge's reserve - and only the LAST
    panel draws the badge, so the first one is that much narrower (no badge-wide blank in it)."""
    reserve = renderer.identity_reserve(metrics.identity_band, metrics.identity_ssid)
    if network_width is not None:
        return network_width, reserve
    up_bytes, dw_bytes = metrics.net_bytes()
    return renderer.network_panel_width(up_bytes, dw_bytes, config) + reserve, reserve


def _split_rects(x: int, width: int, height: int, layout: str, panel_width: int,
                 badge_reserve: int) -> List[QRect]:
    """The two panels of the split view: stacked on a side (vertical) taskbar, side by side otherwise
    (the first one without the badge reserve - see _split_geometry)."""
    gap = constants.renderer.GRAPH_SPLIT_GAP
    if layout == "vertical":
        top = (height - gap) // 2
        return [QRect(x, 0, width, top), QRect(x, top + gap, width, height - top - gap)]
    first = panel_width - badge_reserve
    return [QRect(x, 0, first, height), QRect(x + first + gap, 0, panel_width, height)]


def _resolve_mode(config: RenderConfig, cycle_mode: str) -> str:
    """`cycle` resolves to whichever single metric is currently showing. The widget only rebuilds its
    phase list on the next step, so a phase left over from before the split was toggled is mapped onto
    the current set (the combined network phase <-> the first split phase)."""
    mode = config.widget_display_mode
    if mode != "cycle":
        return mode
    if split_active(config):
        return split_network_phases(config.swap_upload_download)[0] if cycle_mode == "network_only" else cycle_mode
    return "network_only" if cycle_mode in SPLIT_PHASES else cycle_mode


def _draw_graph(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                config: RenderConfig, metrics: WidgetMetrics, mode: str, layout: str) -> None:
    """Mini-graph background layer (skipped in side_by_side - scoped per-segment there - and for the
    split network readout, whose panels each draw their own graph)."""
    if mode == "side_by_side":
        return
    if mode == "network_only" and split_active(config):
        return
    if mode in SPLIT_PHASES:
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.net_history), layout,
                                 series="upload" if mode == "network_upload" else "download")
    elif mode == "cpu_only":
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.cpu_history),
                                 layout, is_hardware=True, hardware_color=constants.graph.CPU_LINE_COLOR)
    elif mode == "gpu_only":
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.gpu_history),
                                 layout, is_hardware=True, hardware_color=constants.graph.GPU_LINE_COLOR)
    else:
        renderer.draw_mini_graph(painter, width, height, config, list(metrics.net_history), layout)


def side_by_side_keys(display_order: List[str], cpu_enabled: bool, gpu_enabled: bool,
                      stack_hw: bool) -> List[str]:
    """The side_by_side segments in display order: 'network', 'cpu', 'gpu', or one 'hardware' segment
    when CPU and GPU are stacked together. Shared by the paint path and the layout's size reservation."""
    keys: List[str] = []
    for k in display_order:
        if k == "network":
            keys.append(k)
        elif k == "cpu" and cpu_enabled:
            if stack_hw and gpu_enabled:
                if "hardware" not in keys:
                    keys.append("hardware")
            else:
                keys.append("cpu")
        elif k == "gpu" and gpu_enabled:
            if stack_hw and cpu_enabled:
                if "hardware" not in keys:
                    keys.append("hardware")
            else:
                keys.append("gpu")
    return keys


def stacked_segment_height(key: str, line_height: int, ram_enabled: bool, vram_enabled: bool) -> int:
    """Height of one side_by_side segment when a side taskbar stacks the segments top-to-bottom.

    Matches what the renderer paints: the network readout is two rows + 1px (draw_network_speeds);
    CPU and GPU are one row each, plus a row of its own for the RAM/VRAM, stacked CPU+GPU included
    (draw_hardware_stats, memory_below). Worst case, so a reading that arrives later never clips.
    """
    if key == "network":
        return line_height * 2 + 1
    if key == "hardware":
        return line_height * (2 + (1 if ram_enabled else 0) + (1 if vram_enabled else 0))
    has_memory = ram_enabled if key == "cpu" else vram_enabled
    return line_height * (2 if has_memory else 1)


# --- Side (LEFT/RIGHT) taskbar: the "fits" ladder ------------------------------------------------
#
# A side taskbar is as narrow as its thickness (44 px inside the margins on Win11's Default 48 px bar,
# 28 on Small). Today's classic readout needs ~83 px, so instead of a "side bar" switch the widget
# climbs down a ladder until its content fits the measured width:
#   T0 classic (today's layout, unchanged)        T1 arrow + compact number + short unit inline
#   T2 arrow + compact number per row + unit row  T3 T2 with whole numbers from 10 up
#   T4 no arrows                                  T5 nothing fits: T4 clipped (logged once)
# ONE pure function decides it, from font metrics and reference strings only, for both the layout's
# sizing and the paint path (tier from the rect's width), so the Settings preview matches by being
# given the same width. Hardware that cannot fit on the bar moves to the hover card.

TIER_CLASSIC, TIER_INLINE_UNIT, TIER_UNIT_ROW, TIER_WHOLE, TIER_NO_ARROWS, TIER_CLIP = range(6)

_HARDWARE_MODES = ("cpu_only", "gpu_only", "combined")


class SideBarFit(NamedTuple):
    """What a side-taskbar widget of a given width can show (see ``side_bar_fit``)."""
    tier: int                 # TIER_* for the network readout
    hw_full_fits: bool        # today's hardware rows (with temps/power/memory) fit the width
    hw_inline: bool           # compact CPU/GPU rows: icon + percent on one row (else icon over value)
    unit_short: bool          # the shared unit row uses the short labels (the configured ones don't fit)
    classic: bool             # today's layout fits as a whole - draw exactly what 2.2 did
    hides_hardware: bool      # enabled hardware (or its memory/temps/power) is off the bar -> hover card
    hides_identity: bool      # the Wi-Fi identity badge is off the bar -> hover card
    # compact CPU/GPU rows keep the "%" sign. False when even the stacked "100%" is wider than the bar
    # (Small 32 px at 11 pt): the value shows as "100" under its CPU/GPU icon rather than clipped.
    hw_percent_sign: bool = True


def _str_items(items: Any) -> List[str]:
    return [s for s in (items or []) if isinstance(s, str) and s]


def _arrow_glyphs(config: RenderConfig, i18n: Any) -> List[str]:
    up = config.arrow_up_symbol or getattr(i18n, "UPLOAD_ARROW", None)
    dw = config.arrow_down_symbol or getattr(i18n, "DOWNLOAD_ARROW", None)
    return [g if isinstance(g, str) and g else d for g, d in ((up, "↑"), (dw, "↓"))]


def _unit_labels(i18n: Any, unit_type: str, short: bool) -> List[str]:
    try:
        from netspeedtray.utils.helpers import get_unit_labels_for_type
        return _str_items(get_unit_labels_for_type(i18n, unit_type, short))
    except Exception:
        return []


def _decimal_sep(i18n: Any) -> str:
    sep = getattr(i18n, "DECIMAL_SEPARATOR", ".")
    return sep if isinstance(sep, str) and sep else "."


def _max_w(fm: Any, items: List[str]) -> int:
    return max((fm.horizontalAdvance(s) for s in items), default=0)


def side_hardware_stats(config: RenderConfig, mode: str) -> List[str]:
    """The CPU/GPU stats a mode puts on the bar, in display order ('CPU'/'GPU')."""
    order = list(getattr(config, "widget_display_order", None) or ["network", "cpu", "gpu"])
    if mode == "side_by_side":   # its segments, display order included ('none' drops one)
        keys = side_by_side_keys(order, config.monitor_cpu_enabled, config.monitor_gpu_enabled,
                                 getattr(config, "stack_hardware_stats", False))
        return [s for k in keys for s in (["CPU", "GPU"] if k == "hardware" else [k.upper()])
                if k != "network"]
    want = {"cpu_only": {"cpu"}, "gpu_only": {"gpu"}, "combined": {"cpu", "gpu"}}.get(mode)
    if want is None:
        want = {k for k, on in (("cpu", config.monitor_cpu_enabled), ("gpu", config.monitor_gpu_enabled)) if on}
    ranked = sorted(want, key=lambda k: order.index(k) if k in order else 99)
    return [k.upper() for k in ranked]


def _mem_reference(mem_totals: Tuple[Optional[float], Optional[float]]) -> str:
    """The widest memory value the renderer can paint ("99.9/99.9G"), digit-for-digit as wide as the
    machine's real total - the same class whether the total is known yet or not. (On a >= 100 GB
    machine the layout may size before the total is known and get the 2-digit class; the 3-digit
    paint can then disagree for one re-size on a borderline bar - harmless, the hardware then goes to
    the hover card.)"""
    t = max([v for v in mem_totals if isinstance(v, (int, float)) and v > 0] or [0.0])
    whole = "9" * max(2, len(str(int(round(t, 1)))))
    return f"{whole}.9/{whole}.9G"


def _hw_rows_width(config: RenderConfig, fm: Any, stats: List[str], memory_below: bool,
                   mem_ref: str) -> int:
    """Width of today's hardware rows as ``draw_hardware_stats`` lays them out (its ``seg_w``)."""
    if not stats:
        return 0
    from netspeedtray.utils.helpers import memory_label_width
    style = config.hardware_label_style
    label_col = (_max_w(fm, stats) + 4) if style == "text" else 14
    pct_col = fm.horizontalAdvance("100%")
    temps, power = config.show_hardware_temps, config.show_hardware_power
    suffix_ref = ("(99°C, 250.0W)" if temps and power else "(250.0W)" if power
                  else "(99°C)" if temps else "")
    suffix_col = (fm.horizontalAdvance(" ") + fm.horizontalAdvance(suffix_ref)) if suffix_ref else 0
    any_mem = ("CPU" in stats and config.monitor_ram_enabled) or ("GPU" in stats and config.monitor_vram_enabled)
    mem_num = fm.horizontalAdvance(mem_ref) if any_mem else 0
    mem_label = memory_label_width(fm) if (config.show_memory_labels and any_mem) else 0
    inline = len(stats) > 1 and not memory_below
    mem_col = (fm.horizontalAdvance("  ") + mem_label + mem_num) if (any_mem and inline) else 0
    below = (label_col + mem_label + mem_num) if (any_mem and not inline) else 0
    return max(label_col + pct_col + suffix_col + mem_col, below)


def _hw_details_enabled(config: RenderConfig, stats: List[str]) -> bool:
    """Something beyond the bare percent is on for these stats (memory, temperature or power)."""
    return bool(stats) and bool(config.show_hardware_temps or config.show_hardware_power
                                or ("CPU" in stats and config.monitor_ram_enabled)
                                or ("GPU" in stats and config.monitor_vram_enabled))


def side_bar_fit(config: RenderConfig, metrics: Any, arrow_metrics: Any, i18n: Any, width: int, *,
                 identity_reserve: int = 0,
                 mem_totals: Tuple[Optional[float], Optional[float]] = (None, None)) -> SideBarFit:
    """Pick the side-taskbar tier for a widget ``width`` px wide - the one decision the layout sizes
    from and ``render_widget`` paints from. Pure: font metrics and reference strings, never a live
    value, so the answer cannot change from one tick to the next."""
    fm, afm = metrics, (arrow_metrics or metrics)
    margin = constants.renderer.TEXT_MARGIN
    inner = width - 2 * margin
    mode = config.widget_display_mode
    show_arrows, show_units = not config.hide_arrows, not config.hide_unit_suffix
    arrow_w = _max_w(afm, _arrow_glyphs(config, i18n)) if show_arrows else 0

    # T0: today's two-row readout (draw_network_speeds) - same columns it pins.
    from netspeedtray.utils.helpers import get_reference_value_string, compact_speed_reference_strings
    labels = _unit_labels(i18n, config.unit_type, config.short_unit_labels)
    short_labels = _unit_labels(i18n, config.unit_type, True)
    classic_w = (arrow_w + (constants.renderer.ARROW_NUMBER_GAP if show_arrows else 0)
                 + fm.horizontalAdvance(get_reference_value_string(False, config.decimal_places, config.unit_type))
                 + ((constants.renderer.VALUE_UNIT_GAP + _max_w(fm, labels)) if show_units else 0)
                 + identity_reserve)

    sep = _decimal_sep(i18n)
    num_dec = _max_w(fm, compact_speed_reference_strings(config.decimal_places, False, sep))
    num_whole = _max_w(fm, compact_speed_reference_strings(config.decimal_places, True, sep))
    ag = constants.renderer.COMPACT_ARROW_GAP_PX if show_arrows else 0
    inline_unit_w = (constants.renderer.COMPACT_UNIT_GAP_PX + _max_w(fm, short_labels)) if show_units else 0
    if classic_w <= inner:
        tier = TIER_CLASSIC
    elif arrow_w + ag + num_dec + inline_unit_w <= inner:
        tier = TIER_INLINE_UNIT
    elif arrow_w + ag + num_dec <= inner:
        tier = TIER_UNIT_ROW
    elif arrow_w + ag + num_whole <= inner:
        tier = TIER_WHOLE
    elif num_whole <= inner:
        tier = TIER_NO_ARROWS
    else:
        tier = TIER_CLIP
    unit_short = bool(show_units and not config.short_unit_labels and _max_w(fm, labels) > inner)

    # Hardware: today's rows as the side bar stacks them, else compact "[icon] 37%" rows.
    mem_ref = _mem_reference(mem_totals)
    if mode == "side_by_side":
        keys = side_by_side_keys(config.widget_display_order, config.monitor_cpu_enabled,
                                 config.monitor_gpu_enabled, getattr(config, "stack_hardware_stats", False))
        groups = [["CPU", "GPU"] if k == "hardware" else [k.upper()] for k in keys if k != "network"]
        hw_full = all(_hw_rows_width(config, fm, g, True, mem_ref) <= inner for g in groups)
        stats = [s for g in groups for s in g]
    elif mode in _HARDWARE_MODES:
        stats = side_hardware_stats(config, mode)
        hw_full = _hw_rows_width(config, fm, stats, False, mem_ref) <= inner
    elif mode == "cycle":
        stats = side_hardware_stats(config, mode)
        hw_full = all(_hw_rows_width(config, fm, [s], False, mem_ref) <= inner for s in stats)
    else:
        stats, hw_full = [], True
    icon_col = (_max_w(fm, stats or ["CPU"]) if config.hardware_label_style == "text"
                else constants.renderer.HW_ICON_SIZE_PX)
    hw_inline = icon_col + constants.renderer.COMPACT_HW_ICON_GAP_PX + fm.horizontalAdvance("100%") <= inner
    hw_percent_sign = hw_inline or fm.horizontalAdvance("100%") <= inner

    if mode in _HARDWARE_MODES:
        classic = hw_full
    else:
        classic = tier == TIER_CLASSIC and hw_full
    if classic:
        hides_hw = False
    elif mode == "side_by_side":
        hides_hw = bool(stats)                       # all of it goes to the card
    else:
        hides_hw = (not hw_full) and _hw_details_enabled(config, stats)
    hides_identity = bool(identity_reserve) and tier != TIER_CLASSIC and mode not in _HARDWARE_MODES
    return SideBarFit(tier, hw_full, hw_inline, unit_short, classic, hides_hw, hides_identity, hw_percent_sign)


def compact_network_rows(config: RenderConfig, fit: SideBarFit, directions: int = 2) -> int:
    """Text rows of the compact network readout: one per direction, plus the shared unit row
    (T2 and down, unless units are hidden)."""
    unit_row = (not config.hide_unit_suffix) and fit.tier >= TIER_UNIT_ROW
    return directions + (1 if unit_row else 0)


def _rows_band(rows: int, line_height: int) -> int:
    return rows * line_height + max(0, rows - 1) * constants.renderer.TWO_ROW_GAP_PX


def _hw_band(config: RenderConfig, fit: SideBarFit, stats: List[str], line_height: int) -> int:
    """Height of a hardware mode's rows on the bar: today's rows (memory on a row of its own), or the
    compact ones (one row per stat, two when the icon goes over the value)."""
    if fit.hw_full_fits:
        mem_rows = sum(1 for s in stats if (s == "CPU" and config.monitor_ram_enabled)
                       or (s == "GPU" and config.monitor_vram_enabled))
        return line_height * (len(stats) + mem_rows)
    return _rows_band(len(stats) * (1 if fit.hw_inline else 2), line_height)


def side_compact_height(config: RenderConfig, fit: SideBarFit, line_height: int) -> int:
    """Widget height for a side taskbar when ``fit`` is not classic, sized to the TALLEST thing it can
    show (every Auto-Cycle phase), with today's 4 x TEXT_MARGIN padding."""
    pad = 4 * constants.renderer.TEXT_MARGIN
    mode = config.widget_display_mode
    net_band = (2 * line_height if fit.tier == TIER_CLASSIC
                else _rows_band(compact_network_rows(config, fit), line_height))
    if mode in _HARDWARE_MODES:
        return _hw_band(config, fit, side_hardware_stats(config, mode), line_height) + pad
    if mode == "network_only" and split_active(config):
        return split_compact_height(config, fit, line_height)
    if mode == "cycle":
        bands = [net_band] + [_hw_band(config, fit, [s], line_height)
                              for s in side_hardware_stats(config, mode)]
        return max(bands) + pad
    return net_band + pad   # network_only / side_by_side (its hardware is in the hover card)


def side_classic_height(config: RenderConfig, line_height: int) -> int:
    """Today's (T0) side-taskbar widget height: two text rows + padding; separate upload/download
    graphs stack two such panels (network_only); side_by_side stacks a band per segment."""
    margin = constants.renderer.TEXT_MARGIN
    height = line_height * 2 + margin * 4
    mode = config.widget_display_mode
    if split_active(config) and mode == "network_only":
        height = 2 * height + constants.renderer.GRAPH_SPLIT_GAP
    if mode == "side_by_side":
        keys = side_by_side_keys(config.widget_display_order, config.monitor_cpu_enabled,
                                 config.monitor_gpu_enabled, getattr(config, "stack_hardware_stats", False))
        if len(keys) > 1:
            height = (sum(stacked_segment_height(k, line_height, config.monitor_ram_enabled,
                                                 config.monitor_vram_enabled) for k in keys)
                      + constants.layout.WIDGET_SEGMENT_GAP_STACKED_PX * (len(keys) - 1) + margin * 4)
    return height


def side_bar_height(config: RenderConfig, fit: Optional[SideBarFit], line_height: int) -> int:
    """The side-taskbar widget height for ``fit`` - what the layout reserves and the Settings preview
    shows, so the two can't disagree."""
    if fit is None or fit.classic:
        return side_classic_height(config, line_height)
    return side_compact_height(config, fit, line_height)


def split_compact_height(config: RenderConfig, fit: SideBarFit, line_height: int) -> int:
    """Separate upload/download graphs on a side bar: two one-row panels and the shared unit row."""
    m = constants.renderer.TEXT_MARGIN
    panel = line_height + 4 * m
    unit_row = compact_network_rows(config, fit, 0)
    return 2 * panel + constants.renderer.GRAPH_SPLIT_GAP + (unit_row * (line_height + 2 * m))


def _draw_side_compact(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                       config: RenderConfig, metrics: WidgetMetrics, mode: str, layout: str,
                       fit: SideBarFit) -> None:
    """Paint a side-taskbar widget whose classic layout doesn't fit (see side_bar_fit)."""
    up_bytes, dw_bytes = metrics.net_bytes()
    history = list(metrics.net_history)
    if mode in _HARDWARE_MODES:
        stats = side_hardware_stats(config, mode)
        if config.graph_enabled and mode in ("cpu_only", "gpu_only"):
            _draw_graph(painter, renderer, width, height, config, metrics, mode, layout)
        if fit.hw_full_fits:
            _draw_foreground(painter, renderer, width, height, config, metrics, mode, layout, None)
        else:
            values = {"CPU": metrics.cpu_usage, "GPU": metrics.gpu_usage}
            renderer.draw_hardware_compact(painter, [(s, values[s]) for s in stats], width, height,
                                           config, inline=fit.hw_inline, percent_sign=fit.hw_percent_sign)
        return

    if mode == "network_only" and split_active(config):
        m = constants.renderer.TEXT_MARGIN
        line = renderer.metrics.height()
        panel = line + 4 * m
        gap = constants.renderer.GRAPH_SPLIT_GAP
        unit_rows = compact_network_rows(config, fit, 0)
        top = max(0, (height - split_compact_height(config, fit, line)) // 2)
        rects = [QRect(0, top, width, panel), QRect(0, top + panel + gap, width, panel)]
        first_up = not config.swap_upload_download
        for rect, is_up in zip(rects, (first_up, not first_up)):
            painter.save()
            painter.translate(rect.x(), rect.y())
            renderer.draw_mini_graph(painter, rect.width(), rect.height(), config, history, layout,
                                     series="upload" if is_up else "download")
            painter.restore()
            renderer.draw_network_compact(painter, up_bytes, dw_bytes, width, rect.height(), config,
                                          fit, y_offset=rect.y(), only_upload=is_up, unit_row=False)
        renderer.draw_split_divider(painter, rects, config)
        if unit_rows:
            renderer.draw_network_compact(painter, up_bytes, dw_bytes, width, line + 2 * m, config, fit,
                                          y_offset=rects[1].bottom() + 1, unit_only=True)
        return

    if config.graph_enabled:
        if mode in SPLIT_PHASES:
            renderer.draw_mini_graph(painter, width, height, config, history, layout,
                                     series="upload" if mode == "network_upload" else "download")
        else:
            renderer.draw_mini_graph(painter, width, height, config, history, layout)
    if fit.tier == TIER_CLASSIC:
        # The network fits as it always did; only the hardware moved to the hover card.
        renderer.draw_network_speeds(painter, up_bytes, dw_bytes, width, height, config, layout,
                                     identity_text=metrics.identity_band,
                                     identity_color=metrics.identity_band_color,
                                     identity_solid=metrics.identity_band_solid,
                                     identity_ssid=metrics.identity_ssid)
    elif mode in SPLIT_PHASES:
        renderer.draw_network_compact(painter, up_bytes, dw_bytes, width, height, config, fit,
                                      only_upload=(mode == "network_upload"))
    else:
        renderer.draw_network_compact(painter, up_bytes, dw_bytes, width, height, config, fit)


def _draw_segment(painter: QPainter, renderer: WidgetRenderer, key: str, width: int, height: int,
                  config: RenderConfig, metrics: WidgetMetrics, layout: str, x_offset: int = 0,
                  y_offset: int = 0, slot_width: Optional[int] = None, memory_below: bool = False) -> None:
    """Paint the text/stats of one side_by_side segment (the network graph is the caller's)."""
    if key == "network":
        up_bytes, dw_bytes = metrics.net_bytes()
        renderer.draw_network_speeds(painter, up_bytes, dw_bytes, width, height, config, layout,
                                     x_offset=x_offset, slot_width=slot_width,
                                     identity_text=metrics.identity_band,
                                     identity_color=metrics.identity_band_color,
                                     identity_solid=metrics.identity_band_solid,
                                     identity_ssid=metrics.identity_ssid, y_offset=y_offset)
    elif key == "cpu" and config.monitor_cpu_enabled:
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, None, width, height, config,
                                     metrics.cpu_temp, None, ram, None, layout,
                                     x_offset=x_offset, cpu_power=metrics.cpu_power, y_offset=y_offset)
    elif key == "gpu" and config.monitor_gpu_enabled:
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, None, metrics.gpu_usage, width, height, config,
                                     None, metrics.gpu_temp, None, vram, layout,
                                     x_offset=x_offset, gpu_power=metrics.gpu_power, y_offset=y_offset)
    elif key == "hardware":
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, metrics.gpu_usage, width, height, config,
                                     metrics.cpu_temp, metrics.gpu_temp, ram, vram, layout,
                                     x_offset=x_offset, cpu_power=metrics.cpu_power, gpu_power=metrics.gpu_power,
                                     y_offset=y_offset, memory_below=memory_below)


def _draw_stacked(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                  config: RenderConfig, metrics: WidgetMetrics, layout: str, keys: List[str]) -> None:
    """Side taskbar: the widget is only as wide as the taskbar, so the segments stack top-to-bottom
    (network over CPU/GPU), each centered in the band the layout reserved for it. For the same reason
    the RAM/VRAM goes on a row of its own instead of inline after stacked CPU+GPU."""
    line_height = renderer.metrics.height()
    bands = [stacked_segment_height(k, line_height, config.monitor_ram_enabled, config.monitor_vram_enabled)
             for k in keys]
    gap = constants.layout.WIDGET_SEGMENT_GAP_STACKED_PX
    y = max(0, (height - sum(bands) - gap * (len(bands) - 1)) // 2)
    for key, band in zip(keys, bands):
        if key == "network" and config.graph_enabled:
            painter.save()
            painter.translate(0, y)
            renderer.draw_mini_graph(painter, width, band, config, list(metrics.net_history), layout)
            painter.restore()
        _draw_segment(painter, renderer, key, width, band, config, metrics, layout, y_offset=y,
                      memory_below=True)
        y += band + gap


def _draw_side_by_side(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                       config: RenderConfig, metrics: WidgetMetrics, layout: str,
                       network_width: Optional[int]) -> None:
    """Multi-segment side-by-side painting (network + cpu/gpu/combined laid out in a row, or stacked
    on a side taskbar)."""
    active_keys = side_by_side_keys(config.widget_display_order, config.monitor_cpu_enabled,
                                    config.monitor_gpu_enabled, getattr(config, "stack_hardware_stats", False))
    if not active_keys:
        active_keys = ["network"]

    if layout == "vertical" and len(active_keys) > 1:
        _draw_stacked(painter, renderer, width, height, config, metrics, layout, active_keys)
        return

    net_w = network_width if network_width is not None else width
    # Split graphs only in a row (horizontal taskbar): a side taskbar stacks the segments above
    # (_draw_stacked) with the network keeping its two-row band.
    split = split_active(config) and layout != "vertical"
    split_width = 0
    current_x = 0
    for key in active_keys:
        if key == "network" and split:
            up_bytes, dw_bytes = metrics.net_bytes()
            panel_w, reserve = _split_geometry(renderer, config, metrics, network_width)
            renderer.draw_network_split(painter, up_bytes, dw_bytes,
                                        _split_rects(current_x, width, height, layout, panel_w, reserve),
                                        config, list(metrics.net_history), layout,
                                        identity_text=metrics.identity_band,
                                        identity_color=metrics.identity_band_color,
                                        identity_solid=metrics.identity_band_solid,
                                        identity_ssid=metrics.identity_ssid)
            split_width = 2 * panel_w - reserve + constants.renderer.GRAPH_SPLIT_GAP
        else:
            if key == "network" and config.graph_enabled:
                painter.save()
                painter.translate(current_x, 0)
                renderer.draw_mini_graph(painter, net_w, height, config, list(metrics.net_history), layout)
                painter.restore()
            _draw_segment(painter, renderer, key, width, height, config, metrics, layout,
                          x_offset=current_x, slot_width=network_width)

        if key == "network":
            # Advance by the explicit network width if given (live widget), else by the
            # width the renderer just measured (preview has no layout-manager width).
            advance = network_width if network_width is not None else renderer.get_last_text_rect().width()
            if split:
                advance = split_width
            current_x += advance + constants.layout.WIDGET_SEGMENT_GAP_AFTER_NETWORK_PX
        else:
            current_x += renderer.get_last_text_rect().width() + constants.layout.WIDGET_SEGMENT_GAP_BETWEEN_HARDWARE_PX


def _draw_foreground(painter: QPainter, renderer: WidgetRenderer, width: int, height: int,
                     config: RenderConfig, metrics: WidgetMetrics, mode: str, layout: str,
                     network_width: Optional[int]) -> None:
    """Text/stats foreground layer for the resolved single-metric or side-by-side mode."""
    if mode == "side_by_side":
        _draw_side_by_side(painter, renderer, width, height, config, metrics, layout, network_width)
    elif mode == "network_only" and split_active(config):
        up_bytes, dw_bytes = metrics.net_bytes()
        panel_w, reserve = _split_geometry(renderer, config, metrics, network_width)
        renderer.draw_network_split(painter, up_bytes, dw_bytes,
                                    _split_rects(0, width, height, layout, panel_w, reserve),
                                    config, list(metrics.net_history), layout,
                                    identity_text=metrics.identity_band,
                                    identity_color=metrics.identity_band_color,
                                    identity_solid=metrics.identity_band_solid,
                                    identity_ssid=metrics.identity_ssid)
    elif mode == "network_only":
        up_bytes, dw_bytes = metrics.net_bytes()
        renderer.draw_network_speeds(painter, up_bytes, dw_bytes, width, height, config, layout,
                                     identity_text=metrics.identity_band,
                                     identity_color=metrics.identity_band_color,
                                     identity_solid=metrics.identity_band_solid,
                                     identity_ssid=metrics.identity_ssid)
    elif mode in SPLIT_PHASES:
        up_bytes, dw_bytes = metrics.net_bytes()
        renderer.draw_network_single(painter, up_bytes, dw_bytes, mode == "network_upload", width, height, config,
                                     identity_text=metrics.identity_band,
                                     identity_color=metrics.identity_band_color,
                                     identity_solid=metrics.identity_band_solid,
                                     identity_ssid=metrics.identity_ssid)
    elif mode == "cpu_only":
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, None, width, height, config,
                                     metrics.cpu_temp, None, ram, None, layout, cpu_power=metrics.cpu_power)
    elif mode == "gpu_only":
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, None, metrics.gpu_usage, width, height, config,
                                     None, metrics.gpu_temp, None, vram, layout, gpu_power=metrics.gpu_power)
    elif mode == "combined":
        ram = (metrics.ram_used, metrics.ram_total) if config.monitor_ram_enabled else None
        vram = (metrics.vram_used, metrics.vram_total) if config.monitor_vram_enabled else None
        renderer.draw_hardware_stats(painter, metrics.cpu_usage, metrics.gpu_usage, width, height, config,
                                     metrics.cpu_temp, metrics.gpu_temp, ram, vram, layout,
                                     cpu_power=metrics.cpu_power, gpu_power=metrics.gpu_power)


def render_widget(painter: QPainter, rect: QRect, renderer: WidgetRenderer, config: RenderConfig,
                  metrics: WidgetMetrics, *, layout_mode: str = "horizontal",
                  cycle_mode: str = "network_only", network_width: Optional[int] = None,
                  font: Optional[QFont] = None, paused: bool = False) -> None:
    """
    Draw the full widget content - see _render_widget_content for the arguments.

    ``paused``: the user froze the readout (Pause). Frozen numbers alone look like a hung app, so the
    content is drawn dimmed and a two-bar pause glyph takes the place of the up/down arrow column
    (or, with no arrows drawn, sits in the free left margin when there is room; else dimming alone).
    The glyph never overlaps text and the widget's size never changes for it. The glyph rects drawn
    are left in ``renderer.pause_glyph_rects`` (device coords).
    """
    kwargs = dict(layout_mode=layout_mode, cycle_mode=cycle_mode, network_width=network_width, font=font)
    renderer.pause_glyph_rects = []
    if not paused:
        _render_widget_content(painter, rect, renderer, config, metrics, **kwargs)
        return
    painter.save()
    painter.setOpacity(constants.renderer.PAUSED_CONTENT_OPACITY)
    renderer._pause_cue = True    # rows record their arrow cell instead of drawing the arrow
    try:
        _render_widget_content(painter, rect, renderer, config, metrics, **kwargs)
    finally:
        renderer._pause_cue = False
        painter.restore()
    renderer.pause_glyph_rects = _pause_glyph_rects(rect, renderer)
    _draw_pause_glyphs(painter, renderer.pause_glyph_rects, QColor(renderer.default_color))


def _pause_glyph_rects(rect: QRect, renderer: WidgetRenderer) -> List[QRectF]:
    """Where the pause glyph goes: one per arrow column (the stacked rows' cells merged, so it is
    centered across both rows), else the left margin when the content leaves room, else nowhere."""
    r = constants.renderer
    columns: List[QRectF] = []
    # Merge only the rows of ONE stacked readout (same column, adjacent rows). Split-graph panels
    # stacked on a side bar share the column but sit either side of the divider: each gets its own
    # glyph in its own row, so a merged glyph never straddles the divider.
    adjacent = constants.renderer.TWO_ROW_GAP_PX + 1.0
    for cell in renderer._pause_slots:
        for i, col in enumerate(columns):
            if (abs(col.left() - cell.left()) <= 1.0
                    and cell.top() - col.bottom() <= adjacent and col.top() - cell.bottom() <= adjacent):
                columns[i] = col.united(cell)
                break
        else:
            columns.append(QRectF(cell))
    if columns:
        out = []
        for col in columns:
            h = min(col.height(), max(r.PAUSE_GLYPH_MIN_HEIGHT,
                                      min(r.PAUSE_GLYPH_MAX_HEIGHT, col.height() * r.PAUSE_GLYPH_HEIGHT_RATIO)))
            w = min(col.width() - 1.0, h * r.PAUSE_GLYPH_WIDTH_RATIO)
            if w >= 3.0:   # room for two bars and a gap
                out.append(QRectF(col.center().x() - w / 2.0, col.center().y() - h / 2.0, w, h))
        return out
    bounds = renderer.get_content_bounds()
    if bounds is None or bounds.isNull() or bounds.isEmpty():
        return []
    h = max(r.PAUSE_GLYPH_MIN_HEIGHT, min(r.PAUSE_GLYPH_MAX_HEIGHT, rect.height() * r.PAUSE_GLYPH_MARGIN_HEIGHT_RATIO))
    w = h * r.PAUSE_GLYPH_WIDTH_RATIO
    free = bounds.left() - rect.left()
    if free < w + 2 * r.PAUSE_GLYPH_MARGIN_PAD:
        return []          # no room beside the content: dimming alone carries the cue
    return [QRectF(rect.left() + (free - w) / 2.0, rect.top() + (rect.height() - h) / 2.0, w, h)]


def _draw_pause_glyphs(painter: QPainter, rects: List[QRectF], color: QColor) -> None:
    """Two vertical bars (the universal pause mark) filling each rect, in the text color at full
    opacity - the theme's own contrast, like the arrows it replaces."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    for g in rects:
        bar = g.width() * 0.36
        radius = min(bar / 2.0, 1.0)
        painter.drawRoundedRect(QRectF(g.left(), g.top(), bar, g.height()), radius, radius)
        painter.drawRoundedRect(QRectF(g.right() - bar, g.top(), bar, g.height()), radius, radius)
    painter.restore()


def _render_widget_content(painter: QPainter, rect: QRect, renderer: WidgetRenderer, config: RenderConfig,
                           metrics: WidgetMetrics, *, layout_mode: str = "horizontal",
                           cycle_mode: str = "network_only", network_width: Optional[int] = None,
                           font: Optional[QFont] = None) -> None:
    """
    Draw the full widget content (background + mini-graph + foreground) onto `painter`.

    This is the single shared paint path used by both the live widget and any preview.
    The caller owns the painter lifecycle and any widget-specific layers (e.g. the live
    widget's near-transparent hit-test fill); this function only draws the visible widget.

    Args:
        rect: the target rectangle (the widget/preview rect).
        renderer: a `WidgetRenderer` bound to `config`'s i18n/theme.
        metrics: the frame snapshot to draw.
        layout_mode: 'horizontal' for a TOP/BOTTOM taskbar, 'vertical' for a LEFT/RIGHT (side) taskbar
            (per NetworkSpeedWidget._layout_mode_for_edge). 'vertical' stacks the side_by_side segments
            top-to-bottom instead of in a row; every other mode draws the same either way.
        cycle_mode: which single metric to show when `config.widget_display_mode == 'cycle'`.
        network_width: width of the network segment for side_by_side graph scoping.
        font: font to apply before drawing foreground text.
    """
    width, height = rect.width(), rect.height()
    mode = _resolve_mode(config, cycle_mode)

    renderer.reset_content_bounds()   # accumulate this paint's segment union for menu centering
    renderer.draw_background(painter, rect, config)

    if layout_mode == "vertical":
        # A side taskbar is only as wide as the bar: climb down the "fits" ladder when today's layout
        # doesn't fit (side_bar_fit). When it does (a wide third-party bar), everything below draws
        # exactly what it always did.
        fit = side_bar_fit(config, renderer.metrics, renderer.arrow_metrics, renderer.i18n, width,
                           identity_reserve=renderer.identity_reserve(metrics.identity_band,
                                                                      metrics.identity_ssid),
                           mem_totals=(metrics.ram_total, metrics.vram_total))
        if not fit.classic:
            if font is not None:
                painter.setFont(font)
            _draw_side_compact(painter, renderer, width, height, config, metrics, mode, layout_mode, fit)
            return

    if config.graph_enabled:
        _draw_graph(painter, renderer, width, height, config, metrics, mode, layout_mode)

    if font is not None:
        painter.setFont(font)

    # Right-align the content so the widget's worst-case width reservation (the anti-jiggle "888.8"/"100%"
    # headroom, any hidden GPU/VRAM segment, and in cycle mode the widest-phase reserve) shows up as space
    # on the LEFT - toward the app icons, where it's invisible - instead of a gap between the content and
    # the system tray (#106). side_by_side over-reserves across segments; cycle over-reserves because it's
    # sized to the widest phase but draws one narrower phase. network_only/cpu_only/combined already fill
    # their width and vertical docking is sized to the taskbar. We MEASURE first because the renderer
    # computes its segment rects from font metrics during the draw, not before - so we run one draw onto a
    # throwaway surface (the mini-graph's point cache, keyed by data, is simply warmed by it).
    align_dx = 0
    if (mode == "side_by_side" or config.widget_display_mode == "cycle") and layout_mode != "vertical" and width > 0:
        try:
            probe = QImage(max(1, width), max(1, height), QImage.Format.Format_ARGB32_Premultiplied)
            mp = QPainter(probe)
            if font is not None:
                mp.setFont(font)
            renderer.reset_content_bounds()
            # Mode-generic probe: measures side_by_side OR the single resolved cycle phase.
            _draw_foreground(mp, renderer, width, height, config, metrics, mode, layout_mode, network_width)
            mp.end()
            bounds = renderer.get_content_bounds()
            # Anchor from the content's RIGHT EDGE, not its width: the network segment is right-aligned
            # inside its own slot (draw_network_speeds), so the bounds no longer start at x=0. Using the
            # width here double-counted that shift and pushed the readout past the tray edge, clipping the
            # unit ("Mbps" -> "Mbp"). The right edge already includes the shift, so the anchor is correct.
            content_right = bounds.x() + bounds.width()
            if 0 < content_right <= width:
                # Keep deliberate breathing room from the tray-side edge so the right-anchored readout
                # doesn't crowd the tray-overflow chevron.
                align_dx = max(0, width - content_right - constants.renderer.RIGHT_ANCHOR_PADDING)
        except Exception:
            align_dx = 0
        finally:
            renderer.reset_content_bounds()

    if align_dx > 0:
        painter.save()
        painter.translate(align_dx, 0)
        _draw_foreground(painter, renderer, width, height, config, metrics, mode, layout_mode, network_width)
        painter.restore()
        # The segment rects were measured at x_offset 0 (pre-translate); shift the accumulated bounds to
        # the on-screen position so the context menu still centers under the visible content.
        bounds = renderer.get_content_bounds()
        if bounds is not None and not bounds.isNull():
            bounds.translate(align_dx, 0)
    else:
        _draw_foreground(painter, renderer, width, height, config, metrics, mode, layout_mode, network_width)
