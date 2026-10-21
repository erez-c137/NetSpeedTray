
import logging
import math
from typing import TYPE_CHECKING, Dict, Any, Optional

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from netspeedtray import constants
from netspeedtray.utils.taskbar_utils import (is_small_taskbar, get_taskbar_info, horizontal_taskbar_strip,
                                              vertical_taskbar_strip, dock_signature)
from netspeedtray.utils.helpers import memory_label_width


if TYPE_CHECKING:
    from netspeedtray.views.widget.main import NetworkSpeedWidget

class WidgetLayoutManager:
    """
    Manages layout, font, and window properties for the NetworkSpeedWidget.
    Extracts sizing and property logic from the main widget class.
    """

    def __init__(self, widget: "NetworkSpeedWidget"):
        self.widget = widget
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.LayoutManager")
        self.metrics: Optional[QFontMetrics] = None
        self.arrow_metrics: Optional[QFontMetrics] = None
        self._font: Optional[QFont] = None
        self._arrow_font: Optional[QFont] = None
        # widget_paint.SideBarFit of the last side-taskbar sizing (None on a TOP/BOTTOM bar).
        self._side_fit = None

    def setup_window_properties(self) -> None:
        """Set Qt window flags and attributes for proper Windows integration."""
        self.logger.debug("Setting window properties...")
        self.widget.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.widget.setMouseTracking(True)
        self.logger.debug("Window properties set")

    def init_font(self) -> None:
        """Initialize the font and set initial widget size."""
        self.logger.debug("Initializing font...")
        self.set_font(resize=False)

    def set_font(self, resize: bool = True) -> None:
        """Apply font settings from config."""
        self.logger.debug("Setting font...")
        config = self.widget.config
        
        font_family = config.get("font_family", constants.config.defaults.DEFAULT_FONT_FAMILY)
        font_size = config.get("font_size", constants.config.defaults.DEFAULT_FONT_SIZE)
        font_weight_val = config.get("font_weight", constants.config.defaults.DEFAULT_FONT_WEIGHT)
        
        if isinstance(font_weight_val, int):
             # FIX for #89: Directly use integer weight (e.g., 400, 700)
             font_weight = font_weight_val
        elif isinstance(font_weight_val, str):
            font_weight = {
                "normal": QFont.Weight.Normal, 
                "bold": QFont.Weight.Bold
            }.get(font_weight_val.lower(), QFont.Weight.Normal)
        else:
            font_weight = QFont.Weight.Normal

        font = QFont(font_family, font_size, font_weight)
        self.widget.setFont(font)
        self.widget.current_font = font # Update public attribute
        
        self.metrics = QFontMetrics(font)
        self.widget.current_metrics = self.metrics # Update public attribute
        
        # Arrow font handling
        if config.get("use_separate_arrow_font", False):
            arrow_family = config.get("arrow_font_family", font_family)
            arrow_size = config.get("arrow_font_size", font_size)
            arrow_weight_raw = config.get("arrow_font_weight", font_weight_val)
            
            # Simplified weight logic for layout manager (mirrors main font logic above if needed)
            if isinstance(arrow_weight_raw, int):
                arrow_weight = arrow_weight_raw
            else:
                arrow_weight = {
                    "normal": QFont.Weight.Normal, 
                    "bold": QFont.Weight.Bold
                }.get(str(arrow_weight_raw).lower(), QFont.Weight.Normal)
                
            arrow_font = QFont(arrow_family, arrow_size, arrow_weight)
        else:
            arrow_font = font
            
        self.arrow_metrics = QFontMetrics(arrow_font)
        # Kept for the thin-strip fit check, which re-measures them at each display scale (#322).
        self._font, self._arrow_font = font, arrow_font
        
        self.logger.debug(f"Font set: {font_family}, {font_size}px, Weight: {font_weight}")
        
        if resize:
            self.resize_widget_for_font()

    def resize_widget_for_font(self) -> None:
        """Calculates and sets the widget's fixed dimensions."""
        self.logger.debug("Resizing widget based on layout...")
        if not self.metrics:
            raise RuntimeError("FontMetrics not initialized.")
        if not hasattr(self.widget, 'renderer'):
            raise RuntimeError("Renderer not initialized before resizing.")

        # A re-size (settings, dock or identity change) can change what the hover card should hold;
        # close an open one rather than leave it showing the old layout's sections.
        hide_card = getattr(self.widget, "_hide_usage_hover_card", None)
        if callable(hide_card) and getattr(self.widget, "_hover_card", None) is not None:
            try:
                hide_card()
            except Exception:
                pass
        try:
            # #188: when the preferred monitor is a taskbar-less display we free-float on, there's no
            # taskbar to size against - render a normal compact horizontal readout with a font-derived
            # height (below), so it renders crisp on that screen regardless of the primary's DPI.
            from netspeedtray.utils.taskbar_utils import get_free_float_screen
            float_screen = get_free_float_screen(self.widget.config.get("preferred_monitor")) \
                if self.widget.config.get("free_float", True) else None

            # The widget's OWN taskbar (preferred monitor, #72) - the same one it is positioned on and
            # the same one the taskbar-change check reads, so the two never disagree (#322).
            taskbar_info = get_taskbar_info(preferred_screen_name=self.widget.config.get("preferred_monitor"))
            # Record which dock this size is for; NetworkSpeedWidget._resize_if_dock_changed re-sizes
            # when the live taskbar's signature differs. None (sized from a fallback taskbar or while
            # free-floating) makes the first real taskbar count as a change.
            try:
                self.widget._dock_sig_seen = None if float_screen is not None else dock_signature(taskbar_info)
            except Exception:
                self.widget._dock_sig_seen = None
            dpi_scale = taskbar_info.dpi_scale if taskbar_info.dpi_scale > 0 else 1.0
            if float_screen is not None:
                edge = constants.TaskbarEdge.BOTTOM   # nominal; free-float doesn't dock to an edge
                is_horizontal = True
                is_small = False
            else:
                edge = taskbar_info.get_edge_position()
                is_horizontal = edge in (constants.TaskbarEdge.TOP, constants.TaskbarEdge.BOTTOM)
                is_small = is_small_taskbar(taskbar_info)
            self.logger.debug(f"Taskbar edge: {edge}, Small: {is_small}, FreeFloat: {float_screen is not None}")

            precision = self.widget.config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES)
            margin = constants.renderer.TEXT_MARGIN
            hide_arrows = self.widget.config.get("hide_arrows", False)
            hide_units = self.widget.config.get("hide_unit_suffix", False)
            # FIX for Issue #106: Read short_unit_labels config to ensure consistency
            # Bug: Layout width calculation wasn't using same label format as render-time formatter
            #      This caused text truncation when layout predicted smaller width than actual render
            # Solution: Pass short_labels consistently to both layout calculation and actual rendering
            short_labels = self.widget.config.get("short_unit_labels", constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS)

            # v2.1: reserve the fixed worst-case width for the network-identity band tag. It's part of
            # the network segment, so adding it to the base network width flows correctly into
            # network_only / side_by_side / cycle, and is correctly dropped by the hw-only modes that
            # rebuild calculated_width from scratch (they show no network segment). Same constants the
            # renderer draws with, so reserve == draw (no #106 clip). SSID (item 2) reserves separately.
            _identity_reserve = 0
            if hasattr(self.widget, "_identity_reserve_px"):
                _identity_reserve = self.widget._identity_reserve_px(self.metrics)

            if is_horizontal:
                # --- HORIZONTAL TASKBAR (TOP/BOTTOM) ---
                if is_small:
                    # Small Horizontal Layout Width Calculation
                    # Uses stable reference values rather than a non-existent renderer method.
                    unit_type = self.widget.config.get("unit_type", constants.config.defaults.DEFAULT_UNIT_TYPE)
                    always_mbps = self.widget.config.get("speed_display_mode", constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE) == "always_mbps"

                    from netspeedtray.utils.helpers import get_reference_value_string, get_unit_labels_for_type
                    ref_val_str = get_reference_value_string(always_mbps, precision, unit_type)
                    unit_labels = get_unit_labels_for_type(self.widget.i18n, unit_type, short_labels)
                    ref_unit = unit_labels[2]  # Mega unit as reference (most common display unit)

                    def get_part_width(arrow_char, val, unit):
                        w = 0
                        if not hide_arrows:
                            w += self.arrow_metrics.horizontalAdvance(arrow_char) + self.arrow_metrics.horizontalAdvance(" ")
                        w += self.metrics.horizontalAdvance(val)
                        if not hide_units:
                            w += self.metrics.horizontalAdvance(" ") + self.metrics.horizontalAdvance(unit)
                        return w

                    up_arrow = self.widget.config.get("arrow_up_symbol") or self.widget.i18n.UPLOAD_ARROW
                    down_arrow = self.widget.config.get("arrow_down_symbol") or self.widget.i18n.DOWNLOAD_ARROW
                    up_width = get_part_width(up_arrow, ref_val_str, ref_unit)
                    down_width = get_part_width(down_arrow, ref_val_str, ref_unit)

                    # The renderer draws upload OVER download (two stacked rows), not side-by-side, so
                    # reserve the single-column width (the wider line). Reserving up + separator + down
                    # left dead space the render never fills (#8); HORIZONTAL_LAYOUT_SEPARATOR is unused.
                    calculated_width = max(up_width, down_width) + (margin * 2) + _identity_reserve
                else:
                    # Large Horizontal Layout Width Calculation (Vertical Mode)
                    always_mbps = self.widget.config.get("speed_display_mode", constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE) == "always_mbps"
                    
                    from netspeedtray.utils.helpers import get_unit_labels_for_type, get_reference_value_string

                    unit_type = self.widget.config.get("unit_type", constants.config.defaults.DEFAULT_UNIT_TYPE)
                    # Use stable reference string instead of live values (which start at 0.0 and change every tick)
                    ref_str = get_reference_value_string(always_mbps, precision, unit_type)
                    max_number_width = self.metrics.horizontalAdvance(ref_str)

                    # Reserve only for the units the SELECTED unit_type can actually display, not the
                    # widest label across every unit system (which over-reserved for e.g. "Mibps" on a
                    # bits_decimal user, leaving dead space to the right of the widget). Matches the
                    # small-taskbar branch above.
                    possible_units = get_unit_labels_for_type(self.widget.i18n, unit_type, short_labels)
                    max_unit_width = max(self.metrics.horizontalAdvance(unit) for unit in possible_units) if not hide_units else 0
                    
                    _up_glyph = self.widget.config.get("arrow_up_symbol") or self.widget.i18n.UPLOAD_ARROW
                    _down_glyph = self.widget.config.get("arrow_down_symbol") or self.widget.i18n.DOWNLOAD_ARROW
                    arrow_width = max(self.metrics.horizontalAdvance(_up_glyph), self.metrics.horizontalAdvance(_down_glyph)) if not hide_arrows else 0
                    arrow_gap = constants.renderer.ARROW_NUMBER_GAP if not hide_arrows else 0
                    unit_gap = constants.renderer.VALUE_UNIT_GAP if not hide_units else 0

                    calculated_width = (margin + arrow_width + arrow_gap +
                                        max_number_width + unit_gap +
                                        max_unit_width + margin + _identity_reserve)

                # Save calculated network width for other layout consumers (e.g. mini-graph)
                self._network_width = calculated_width
                self._side_fit = None

                # Separate upload/download graphs: the readout becomes two panels side by side, each one
                # network block wide (_network_width keeps meaning one block - the renderer sizes each
                # panel from it). Only the last panel carries the identity badge, so its reserve is
                # counted once. Auto-Cycle rotates upload and download as single-panel phases instead,
                # so its network width stays one block.
                if self._split_graphs() and self.widget.config.get("widget_display_mode", "network_only") in (
                        "network_only", "side_by_side"):
                    calculated_width = 2 * calculated_width - _identity_reserve + constants.renderer.GRAPH_SPLIT_GAP

                # --- SIDE-BY-SIDE MODE ADJUSTMENT ---
                display_mode = self.widget.config.get("widget_display_mode", "network_only")
                monitor_ram = self.widget.config.get("monitor_ram_enabled", False)
                monitor_vram = self.widget.config.get("monitor_vram_enabled", False)
                # Hoisted so every display-mode branch below has it defined. Previously
                # only the side_by_side block assigned it, so the cpu_only/gpu_only/
                # combined branch referenced an undefined local (latent UnboundLocalError;
                # the #131 crash class).
                _hw_label_style = self.widget.config.get('hardware_label_style', 'icons_colored')
                label_offset = self.metrics.horizontalAdvance("CPU ") if _hw_label_style == "text" else 14
                # #250: the RAM/VRAM label cell, reserved only when the labels are on (same helper
                # the renderer paints with). Zero when off, so no existing widget changes width.
                mem_label_w = (memory_label_width(self.metrics)
                               if self.widget.config.get("show_memory_labels", False) else 0)
                if display_mode == "side_by_side":
                    active_segments = 0
                    monitor_cpu = self.widget.config.get("monitor_cpu_enabled", False)
                    monitor_gpu = self.widget.config.get("monitor_gpu_enabled", False)
                    stack_hw = self.widget.config.get("stack_hardware_stats", False)
                    
                    display_order = self.widget.config.get("widget_display_order", ["network", "cpu", "gpu"])
                    if "network" in display_order: active_segments += 1
                    
                    if stack_hw and monitor_cpu and monitor_gpu:
                        active_segments += 1  # CPU and GPU stack in 1 column
                    else:
                        if "cpu" in display_order and monitor_cpu: active_segments += 1
                        if "gpu" in display_order and monitor_gpu: active_segments += 1
                    
                    # Ensure at least 1 segment
                    active_segments = max(1, active_segments)
                    
                    calculated_width_accum = 0
                    if "network" in display_order:
                        calculated_width_accum += calculated_width
                        
                    # Calculate Sub-Widths (label_offset hoisted above the branches)
                    show_temps = bool(self.widget.config.get("show_hardware_temps", False))
                    show_power = bool(self.widget.config.get("show_hardware_power", False))
                    # Compute suffix width based on which extras are enabled
                    # Use 2-digit temp (99°C) and 3+1 power (250.0W) as realistic worst-case
                    if show_temps and show_power:
                        hw_suffix_width = self.metrics.horizontalAdvance(" (99°C, 250.0W)")
                    elif show_power:
                        hw_suffix_width = self.metrics.horizontalAdvance(" (250.0W)")
                    elif show_temps:
                        hw_suffix_width = self.metrics.horizontalAdvance(" (99°C)")
                    else:
                        hw_suffix_width = 0

                    # Worst-case memory column, reserved for BOTH stacked rows so they align and never
                    # clip. Uses the machine TOTAL (used can never exceed it), not a live used value: a
                    # live ram_used is None at cold start, which previously SKIPPED this reservation and
                    # clipped the readout (e.g. "11.6/15.7G" -> "1") until the user toggled hardware off/on.
                    # Falls back to psutil RAM / a generous default when a total isn't known yet; any slack
                    # lands on the LEFT (toward the app icons), not as a gap. Matches the renderer's fixed
                    # memory column (utils/widget_renderer.draw_hardware_stats), which uses max(totals).
                    mem_total = 0.0
                    if monitor_ram:
                        rt = getattr(self.widget, 'ram_total', 0.0) or 0.0
                        if rt <= 0:   # RAM total is knowable synchronously - use it so we reserve tight, not huge
                            try:
                                import psutil
                                rt = psutil.virtual_memory().total / (1024 ** 3)
                            except Exception:
                                rt = 32.0
                        mem_total = max(mem_total, rt)
                    if monitor_vram:
                        # Use the real VRAM total when known; do NOT bloat to a 99.9 default when it isn't -
                        # the renderer shows VRAM used-only and aligns it to the (known) RAM column, so a big
                        # default just widened the whole widget for nothing.
                        vt = getattr(self.widget, 'vram_total', 0.0) or 0.0
                        if vt > 0:
                            mem_total = max(mem_total, vt)
                    # Only if nothing is known yet (e.g. VRAM-only at cold start) fall back to a modest column.
                    if (monitor_ram or monitor_vram) and mem_total <= 0:
                        mem_total = 32.0
                    mem_ref = f"{mem_total:.1f}/{mem_total:.1f}G" if mem_total > 0 else ""

                    cpu_width = 0
                    if "cpu" in display_order and monitor_cpu:
                        cpu_width = label_offset + self.metrics.horizontalAdvance(" 100%")
                        if hw_suffix_width:
                            cpu_width += hw_suffix_width
                        if monitor_ram and mem_ref:
                            if stack_hw: # inline
                                cpu_width += self.metrics.horizontalAdvance(f" | {mem_ref}") + mem_label_w
                            else: # row
                                cpu_width = max(cpu_width, self.metrics.horizontalAdvance(mem_ref) + mem_label_w)
                        cpu_width += margin # Reclaim Left Margin offset budget from draw_hardware_stats

                    gpu_width = 0
                    if "gpu" in display_order and monitor_gpu:
                        gpu_width = label_offset + self.metrics.horizontalAdvance(" 100%")
                        if hw_suffix_width:
                            gpu_width += hw_suffix_width
                        if monitor_vram and mem_ref:
                            if stack_hw: # inline
                                gpu_width += self.metrics.horizontalAdvance(f" | {mem_ref}") + mem_label_w
                            else: # row
                                gpu_width = max(gpu_width, self.metrics.horizontalAdvance(mem_ref) + mem_label_w)
                        gpu_width += margin # Reclaim Left Margin offset budget
                            
                    if stack_hw and monitor_cpu and monitor_gpu:
                        calculated_width_accum += max(cpu_width, gpu_width)
                    else:
                        calculated_width_accum += cpu_width + gpu_width
                        
                    calculated_width_accum += margin # Add padding for the rightmost edge of the window frame
                        
                    gaps = 0
                    if "network" in display_order and (monitor_cpu or monitor_gpu):
                        gaps += constants.layout.WIDGET_SEGMENT_GAP_AFTER_NETWORK_PX # Gap after Network
                    if monitor_cpu and monitor_gpu and not stack_hw:
                        gaps += constants.layout.WIDGET_SEGMENT_GAP_BETWEEN_HARDWARE_PX  # Gap between CPU and GPU
                    calculated_width = calculated_width_accum + gaps
                elif display_mode in ["cpu_only", "gpu_only", "combined"]:
                    calculated_width = 0
                    show_temps = bool(self.widget.config.get("show_hardware_temps", False))
                    show_power = bool(self.widget.config.get("show_hardware_power", False))
                    if show_temps and show_power:
                        hw_suffix_w = self.metrics.horizontalAdvance(" (99°C, 250.0W)")
                    elif show_power:
                        hw_suffix_w = self.metrics.horizontalAdvance(" (250.0W)")
                    elif show_temps:
                        hw_suffix_w = self.metrics.horizontalAdvance(" (99°C)")
                    else:
                        hw_suffix_w = 0

                    if display_mode in ["cpu_only", "combined"]:
                        cpu_width = label_offset + self.metrics.horizontalAdvance(" 100%")
                        if hw_suffix_w:
                            cpu_width += hw_suffix_w
                        if monitor_ram and getattr(self.widget, 'ram_used', None) is not None:
                            cpu_width += self.metrics.horizontalAdvance(" | 16.0/16.0G") + mem_label_w
                        calculated_width = max(calculated_width, cpu_width)

                    if display_mode in ["gpu_only", "combined"]:
                        gpu_width = label_offset + self.metrics.horizontalAdvance(" 100%")
                        if hw_suffix_w:
                            gpu_width += hw_suffix_w
                        if monitor_vram and getattr(self.widget, 'vram_used', None) is not None:
                            gpu_width += self.metrics.horizontalAdvance(" | 16.0/16.0G") + mem_label_w
                        calculated_width = max(calculated_width, gpu_width)
                elif display_mode == "cycle":
                    # Cycle shows one metric at a time (network, then each enabled HW
                    # metric), so the widget must be as wide as the WIDEST phase or the
                    # hardware phases clip. calculated_width starts as the network width.
                    monitor_cpu = self.widget.config.get("monitor_cpu_enabled", False)
                    monitor_gpu = self.widget.config.get("monitor_gpu_enabled", False)
                    show_temps = bool(self.widget.config.get("show_hardware_temps", False))
                    show_power = bool(self.widget.config.get("show_hardware_power", False))
                    if show_temps and show_power:
                        hw_suffix_w = self.metrics.horizontalAdvance(" (99°C, 250.0W)")
                    elif show_power:
                        hw_suffix_w = self.metrics.horizontalAdvance(" (250.0W)")
                    elif show_temps:
                        hw_suffix_w = self.metrics.horizontalAdvance(" (99°C)")
                    else:
                        hw_suffix_w = 0
                    if monitor_cpu:
                        cpu_width = label_offset + self.metrics.horizontalAdvance(" 100%") + hw_suffix_w
                        if monitor_ram and getattr(self.widget, 'ram_used', None) is not None:
                            cpu_width += self.metrics.horizontalAdvance(" | 16.0/16.0G") + mem_label_w
                        calculated_width = max(calculated_width, cpu_width)
                    if monitor_gpu:
                        gpu_width = label_offset + self.metrics.horizontalAdvance(" 100%") + hw_suffix_w
                        if monitor_vram and getattr(self.widget, 'vram_used', None) is not None:
                            gpu_width += self.metrics.horizontalAdvance(" | 16.0/16.0G") + mem_label_w
                        calculated_width = max(calculated_width, gpu_width)
                
                
                if float_screen is not None:
                    # No taskbar to match on a taskbar-less display: font-derived height in LOGICAL px, so
                    # Qt scales it correctly on that screen regardless of the primary monitor's DPI (#188).
                    calculated_height = self.metrics.height() * 2 + (margin * 4)
                else:
                    # Visible taskbar height for horizontal docking (#104/PR #110), with a #221
                    # fallback + two-row floor so a work area Windows doesn't inset for the taskbar
                    # (availableGeometry() == geometry()) can't collapse the widget below the readout.
                    calculated_height = self._horizontal_dock_height(edge, taskbar_info, dpi_scale)

            else:
                # Width is the TRUE visible taskbar width for vertical docking (Fixes #104/PR #110): the
                # strip the position code centres in (inset capped by the window; the window when
                # there is no inset - auto-hide / #221), floored to whole px so it never hangs over.
                _, visible_tb_width = vertical_taskbar_strip(taskbar_info, edge, dpi_scale)
                calculated_width = math.floor(visible_tb_width) if visible_tb_width > 0 else 0
                # Height: today's two text rows + padding (side_by_side stacks a band per segment,
                # split graphs two panels) when that layout fits the bar; else the compact "fits"
                # ladder the paint path draws - one pure decision from the same width, shared with the
                # Settings preview (widget_paint.side_bar_height).
                from netspeedtray.utils.widget_paint import side_bar_height
                # No thickness known (a fallback taskbar at logon): the #88 floor width below, which
                # is also the width the paint path will decide from.
                self._side_fit = self._side_bar_fit(calculated_width if calculated_width > 0 else 50,
                                                    _identity_reserve)
                calculated_height = side_bar_height(self._render_config(), self._side_fit, self.metrics.height())
                # The network block is the whole widget (None = the rect, which is what a fresh start on
                # a side bar always passed). Never the stale horizontal width from an earlier sizing: it
                # right-aligned the side bar's readout in a slot from the last TOP/BOTTOM layout.
                self._network_width = None

            # FIX for #88: Ensure minimum size to prevents widget from disappearing. Not on a side bar
            # whose thickness is known: the floor hung a 48 px widget-wide 50 px widget off it.
            if not is_horizontal and calculated_width > 0:
                final_width = calculated_width
            else:
                final_width = max(calculated_width, 50)
            final_height = max(calculated_height, 20)

            self.widget.setFixedSize(math.ceil(final_width), math.ceil(final_height))
            self.logger.debug(f"Widget resized to: {self.widget.width()}x{self.widget.height()}px")
            
            if hasattr(self.widget, 'update_position'):
                self.widget.update_position()

        except Exception as e:
            self.logger.error(f"Failed to resize widget: {e}", exc_info=True)
            self.widget.setFixedSize(150, 40)
            self.logger.warning("Applied fallback widget size: 150x40px")

    def _render_config(self):
        """The widget config as the paint path reads it (RenderConfig)."""
        from netspeedtray.utils.widget_renderer import RenderConfig
        return RenderConfig.from_dict(self.widget.config)

    def _side_bar_fit(self, width: int, identity_reserve: int):
        """``widget_paint.side_bar_fit`` for a side-bar widget ``width`` px wide - the same decision
        render_widget makes from the rect, so the reserved height is the one that gets painted. None
        (keep today's sizing) if it can't be evaluated."""
        try:
            from netspeedtray.utils.widget_paint import side_bar_fit
            ram = getattr(self.widget, 'ram_total', None)
            vram = getattr(self.widget, 'vram_total', None)
            totals = tuple(v if isinstance(v, (int, float)) else None for v in (ram, vram))
            reserve = identity_reserve if isinstance(identity_reserve, (int, float)) else 0
            return side_bar_fit(self._render_config(), self.metrics, self.arrow_metrics or self.metrics,
                                self.widget.i18n, width, identity_reserve=reserve,
                                mem_totals=totals)
        except Exception as e:
            self.logger.warning("Side-taskbar fit check failed (%s); keeping the classic layout.", e)
            return None

    def _split_graphs(self) -> bool:
        """Separate upload/download graphs are on (they need the mini graph itself on) - same rule as
        widget_paint.split_active, read from the widget's config dict."""
        cfg = self.widget.config
        return bool(cfg.get("graph_enabled", False) and cfg.get("graph_split_directions", False))

    def _horizontal_dock_height(self, edge: "constants.TaskbarEdge", taskbar_info: Any, dpi_scale: float) -> float:
        """Visible taskbar height (logical px) to size a horizontal-taskbar widget to.

        Normally this is the work-area inset - the slice of the screen the taskbar covers, i.e.
        ``screen.geometry()`` minus ``screen.availableGeometry()`` (the "TRUE visible" height from
        #104/PR #110). Two robustness guards were added for #221 (text cropped top and bottom):

        - Part A (fallback): Windows does not always inset the work area for the taskbar - on some
          Win11 26200 setups ``availableGeometry() == geometry()``, so the diff is 0 and the widget
          would collapse onto the ``max(..., 20)`` floor. Fall back to the validated LOGICAL taskbar
          height (``TaskbarInfo.height``, > 0 for a real taskbar) - the value ``is_small_taskbar()``
          trusts, and the same ``<= 0`` guard ``position_manager`` already uses to POSITION the
          widget. This only brings the sizing into line with the positioning already happening.
        - Part B (floor): the renderer always stacks two rows (``draw_network_speeds``:
          ``total_height = 2*line_height + 1``), so any docked height below that crops the glyphs.
          Floor to the two-row content (+1px for the ``int()`` rounding in ``top_y``) so text can
          never crop, without the generous margin pad the free-float/side branches use.

        #322: the strip comes from ``horizontal_taskbar_strip`` - the work-area inset capped by the
        taskbar window's height, the same value the position code centres in. When the floor makes
        the widget taller than a thin strip, ``PositionCalculator._clamp_into_strip`` sits it flush
        with the screen edge the bar is on, so the overhang is the minimum and faces the desktop.
        """
        if edge in (constants.TaskbarEdge.TOP, constants.TaskbarEdge.BOTTOM):
            _, visible_tb_height = horizontal_taskbar_strip(taskbar_info, edge, dpi_scale)
        else:
            visible_tb_height = (taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale

        if visible_tb_height <= 0:                      # Part A (#221)
            visible_tb_height = taskbar_info.height
        return max(visible_tb_height, self._two_row_floor(visible_tb_height))   # Part B (#221/#322)

    def _ink_reference(self) -> "tuple[list, list]":
        """(text strings, arrow glyphs) the docked readout can paint: the fixed hardware/number
        strings, the active locale's unit labels for the configured unit type (long and short; CJK
        units come from a fallback font, and are measured through it), and the arrows."""
        cfg = self.widget.config
        strings = list(constants.renderer.INK_REFERENCE_STRINGS)
        try:
            from netspeedtray.utils.helpers import get_unit_labels_for_type
            unit_type = cfg.get("unit_type", constants.config.defaults.DEFAULT_UNIT_TYPE)
            for short in (False, True):
                strings += [u for u in get_unit_labels_for_type(self.widget.i18n, unit_type, short)
                            if isinstance(u, str) and u]
        except Exception:
            strings.append("KB/s MB/s GB/s Kbps Mbps Gbps")
        try:
            arrows = [cfg.get("arrow_up_symbol") or self.widget.i18n.UPLOAD_ARROW,
                      cfg.get("arrow_down_symbol") or self.widget.i18n.DOWNLOAD_ARROW]
            if not all(isinstance(g, str) and g for g in arrows):
                raise TypeError("arrow glyphs unavailable")
        except Exception:
            arrows = ["↑", "↓", "▲", "▼"]
        return strings, arrows

    def _ink_extents(self) -> "tuple[int, int]":
        """(pixels above, pixels below) the baseline that any docked readout glyph can paint at 100%,
        from ``tightBoundingRect`` of each reference string as drawn (a string's rect is not the union
        of its glyphs' rects), plus the CPU/GPU icon box. Not the font's ``ascent``/``descent``, which
        include empty internal leading above the capitals. The no-font fallback of ``_two_row_floor``.
        """
        strings, arrows = self._ink_reference()
        above = below = 0
        for metrics, items in ((self.metrics, strings), (self.arrow_metrics or self.metrics, arrows)):
            for s in items:
                r = metrics.tightBoundingRect(s)
                above, below = max(above, -r.top()), max(below, r.bottom())
        # The icon box spans baseline-(size-1)..baseline; its antialiased stroke spills a row each way.
        return max(above, constants.renderer.HW_ICON_SIZE_PX), max(below, 1)

    def _row_baselines(self, height: int) -> "tuple[int, ...]":
        """The baselines the renderer paints at in a ``height`` px widget: the network rows
        (``draw_network_speeds``: box ``2*line_height + gap`` centred) and the hardware rows
        (``draw_hardware_stats``: ``2*line_height``, no gap)."""
        line_h, asc, gap = self.metrics.height(), self.metrics.ascent(), constants.renderer.TWO_ROW_GAP_PX
        net = int((height - (2 * line_h + gap)) / 2 + asc)
        hw = int((height - 2 * line_h) / 2 + asc)
        return net, net + line_h + gap, hw, hw + line_h

    def _two_rows_fit(self, height: int, ink_above: int, ink_below: int) -> bool:
        """100% metrics model: would every glyph stay inside ``height`` px (1 px clear on top)?"""
        b = self._row_baselines(height)
        return (min(b) - ink_above >= constants.renderer.INK_FIT_TOP_MARGIN_PX
                and max(b) + ink_below <= height - 1)

    def _paint_fits(self, height: int, scale: float, strings: list, arrows: list) -> bool:
        """Paint every reference glyph at every row baseline into a ``height`` px band at device pixel
        ratio ``scale`` - the same text engine and hinting the widget paints with - and check that no
        pixel lands outside the band (1 device px clear on top). Exact where a metrics model is not:
        at 125/150% hinting moves glyphs by a device row either way (#322 review)."""
        pad = 8
        font, arrow_font = self._font, (self._arrow_font or self._font)
        fm, afm = self.metrics, (self.arrow_metrics or self.metrics)
        icon = constants.renderer.HW_ICON_SIZE_PX
        width = (sum(fm.horizontalAdvance(s) + 4 for s in strings)
                 + sum(afm.horizontalAdvance(g) + 4 for g in arrows) + icon + 4)
        img = QImage(math.ceil(width * scale), math.ceil((height + 2 * pad) * scale),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(scale)
        img.fill(0)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            p.setPen(QPen(QColor(255, 255, 255), 1))
            p.translate(0, pad)
            for y in self._row_baselines(height):
                x = 0
                p.setFont(font)
                for s in strings:
                    p.drawText(x, y, s)
                    x += fm.horizontalAdvance(s) + 4
                p.setFont(arrow_font)
                for g in arrows:
                    p.drawText(x, y, g)
                    x += afm.horizontalAdvance(g) + 4
                p.drawRect(QRect(x, y - icon + 1, icon, icon))     # the CPU/GPU icon box
        finally:
            p.end()
        bpl = img.bytesPerLine()
        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        data = bytes(ptr)
        blank = bytes(bpl)
        rows = [y for y in range(img.height()) if data[y * bpl:(y + 1) * bpl] != blank]
        if not rows:
            return True
        band_top = round(pad * scale)
        band_bottom = band_top + math.floor(height * scale) - 1
        margin = constants.renderer.INK_FIT_TOP_MARGIN_PX
        return rows[0] >= band_top + margin and rows[-1] <= band_bottom

    def _two_row_floor(self, strip: float) -> int:
        """The smallest docked height (>= the strip) at which both rows paint uncropped (#221/#322).

        #221 floored at ``metrics.height()*2 + 2`` - the font's line boxes - which on Win11's 32 px
        "Small" taskbar made a 9 pt widget 34 px and hung 2 px off the bar, although the INK of the two
        rows fits in 32. So: the strip itself when the ink fits; else the first height above it that
        fits; never more than the old floor (unchanged for any strip that already met it).

        "Fits" is decided by painting the reference glyphs at every standard display scale
        (``_paint_fits``), since hinting at 125/150% moves ink by a device row that a 96 DPI metrics
        model can't see. Without the font object (unit tests that set only ``metrics``) the 100%
        metrics model is used.
        """
        old_floor = self.metrics.height() * 2 + 2
        start = max(1, math.ceil(strip))
        if start >= old_floor:
            return old_floor
        try:
            if self._font is None:
                above, below = self._ink_extents()
                fits = lambda h: self._two_rows_fit(h, above, below)   # noqa: E731
            else:
                strings, arrows = self._ink_reference()
                fits = lambda h: all(self._paint_fits(h, s, strings, arrows)   # noqa: E731
                                     for s in constants.renderer.INK_FIT_SCALES)
            for h in range(start, old_floor):
                if fits(h):
                    return h
        except Exception as e:
            self.logger.debug("Thin-strip fit check failed (%s); keeping the two-row floor.", e)
        return old_floor
        scales = constants.renderer.INK_FIT_SCALES if self._font is not None else (1.0,)
        try:
            extents = [(s, *self._ink_extents(s)) for s in scales]
        except Exception:
            return old_floor
        for h in range(start, old_floor):
            if all(self._two_rows_fit(h, above, below, s) for s, above, below in extents):
                return h
        return old_floor
