"""
Constants for application configuration defaults and constraints.
"""
from typing import Final, Dict, Any, List

# --- IMPORT OTHER CONSTANTS TO CREATE A SINGLE SOURCE OF TRUTH ---
from netspeedtray.constants.timers import timers
from netspeedtray.constants.data import data
from netspeedtray.constants.network import network
from netspeedtray.constants.app import app
from netspeedtray.constants.color import color
from netspeedtray.constants.fonts import fonts
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.constants.ui import ui as ui_constants
from netspeedtray.constants.update_mode import UpdateMode


class ConfigMessages:
    # ... (no changes needed in this class)
    """Log message templates for configuration validation."""
    INVALID_NUMERIC: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'"
    INVALID_BOOLEAN: Final[str] = "Invalid {key} '{value}', resetting to boolean default '{default}'"
    INVALID_COLOR: Final[str] = "Invalid color '{value}' for {key}, resetting to default '{default}'"
    INVALID_CHOICE: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'. Valid choices: {choices}"
    INVALID_INTERFACES: Final[str] = "Invalid selected_interfaces value '{value}', resetting to default []"
    INVALID_POSITION: Final[str] = "Invalid {key} '{value}', resetting to None"


    def __init__(self) -> None:
        pass # Validation is not strictly necessary for simple string holders


class ConfigConstants:
    """Defines default values and constraints for all application settings."""
    # --- Schema Versioning ---
    # When the config structure changes, increment this version.
    # The migration system will use this to determine which upgrades to apply.
    # Current version history:
    #   v1.0: Initial schema with all current fields (as of 2026-02-18)
    CONFIG_SCHEMA_VERSION: Final[str] = "1.1"
    
    # --- Default Values for Individual Settings (referencing other constants) ---
    DEFAULT_UPDATE_RATE: Final[float] = 1.0
    MINIMUM_UPDATE_RATE: Final[float] = timers.MINIMUM_INTERVAL_MS / 1000.0
    DEFAULT_FONT_FAMILY: Final[str] = fonts.DEFAULT_FONT
    DEFAULT_FONT_SIZE: Final[int] = 9
    DEFAULT_FONT_WEIGHT: Final[int] = fonts.WEIGHT_NORMAL
    DEFAULT_USE_SEPARATE_ARROW_FONT: Final[bool] = True
    DEFAULT_ARROW_FONT_FAMILY: Final[str] = fonts.DEFAULT_FONT
    DEFAULT_ARROW_FONT_SIZE: Final[int] = 10
    DEFAULT_ARROW_FONT_WEIGHT: Final[int] = fonts.WEIGHT_DEMIBOLD
    DEFAULT_COLOR: Final[str] = color.WHITE # Referencing color palette
    DEFAULT_COLOR_CODING: Final[bool] = False
    DEFAULT_HIGH_SPEED_THRESHOLD: Final[float] = 10.0
    DEFAULT_LOW_SPEED_THRESHOLD: Final[float] = 1.0
    DEFAULT_HIGH_SPEED_COLOR: Final[str] = color.GREEN # Referencing color palette
    DEFAULT_LOW_SPEED_COLOR: Final[str] = color.ORANGE # Referencing color palette
    DEFAULT_BACKGROUND_COLOR: Final[str] = "#000000" # Black
    DEFAULT_BACKGROUND_OPACITY: Final[int] = 0 # Percentage (0-100), default transparent
    DEFAULT_GRAPH_ENABLED: Final[bool] = False
    DEFAULT_HISTORY_MINUTES: Final[int] = 3
    DEFAULT_GRAPH_OPACITY: Final[int] = 66
    DEFAULT_INTERFACE_MODE: Final[str] = network.interface.DEFAULT_MODE
    DEFAULT_KEEP_DATA_DAYS: Final[int] = data.retention.DEFAULT_DAYS # 365 days (1 Year) default
    DEFAULT_DARK_MODE: Final[bool] = True
    DEFAULT_DYNAMIC_UPDATE_ENABLED: Final[bool] = True
    DEFAULT_SPEED_DISPLAY_MODE: Final[str] = "always_mbps"  # Prevents constant B/KB/MB jumping
    DEFAULT_UNIT_TYPE: Final[str] = "bits_decimal"  # Default to Bits (Kbps, Mbps)
    DEFAULT_SWAP_UPLOAD_DOWNLOAD: Final[bool] = False
    DEFAULT_HIDE_ARROWS: Final[bool] = False
    DEFAULT_HIDE_UNIT_SUFFIX: Final[bool] = False
    DEFAULT_SHORT_UNIT_LABELS: Final[bool] = False
    DEFAULT_DECIMAL_PLACES: Final[int] = 1
    DEFAULT_TEXT_ALIGNMENT: Final[str] = "center"
    DEFAULT_FREE_MOVE: Final[bool] = False
    DEFAULT_KEEP_VISIBLE_FULLSCREEN: Final[bool] = False
    DEFAULT_FORCE_DECIMALS: Final[bool] = True
    DEFAULT_START_WITH_WINDOWS: Final[bool] = False
    DEFAULT_TRAY_OFFSET_X: Final[int] = 0
    DEFAULT_TRAY_OFFSET_Y: Final[int] = 3
    DEFAULT_LEGEND_POSITION: Final[str] = data.legend_position.DEFAULT_LEGEND_POSITION
    DEFAULT_SHOW_LEGEND: Final[bool] = False
    
    # --- CPU/GPU Monitoring ---
    DEFAULT_MONITOR_CPU_ENABLED: Final[bool] = False
    DEFAULT_MONITOR_GPU_ENABLED: Final[bool] = False
    DEFAULT_MONITOR_RAM_ENABLED: Final[bool] = False
    DEFAULT_MONITOR_VRAM_ENABLED: Final[bool] = False
    DEFAULT_SHOW_HARDWARE_TEMPS: Final[bool] = True
    DEFAULT_SHOW_HARDWARE_POWER: Final[bool] = False
    DEFAULT_STACK_HARDWARE_STATS: Final[bool] = True
    DEFAULT_HARDWARE_LABEL_STYLE: Final[str] = "icons_monochrome"
    DEFAULT_WIDGET_DISPLAY_MODE: Final[str] = "side_by_side" # Choices: network_only, cycle, side_by_side
    DEFAULT_WIDGET_DISPLAY_ORDER: Final[List[str]] = ["network", "cpu", "gpu"]
    DEFAULT_WIDGET_CYCLE_INTERVAL: Final[int] = 3 # Seconds
    DEFAULT_CPU_LOAD_HIGH_THRESHOLD: Final[float] = 80.0
    DEFAULT_CPU_LOAD_LOW_THRESHOLD: Final[float] = 50.0
    DEFAULT_GPU_LOAD_HIGH_THRESHOLD: Final[float] = 80.0
    DEFAULT_GPU_LOAD_LOW_THRESHOLD: Final[float] = 50.0

    CONFIG_FILENAME: Final[str] = "NetSpeedTray_Config.json"
    
    DEFAULT_CONFIG: Final[Dict[str, Any]] = {
        "config_version": CONFIG_SCHEMA_VERSION,
        "start_with_windows": DEFAULT_START_WITH_WINDOWS,
        "language": None,  # None means auto-detect
        "update_rate": DEFAULT_UPDATE_RATE,
        "font_family": DEFAULT_FONT_FAMILY,
        "font_size": DEFAULT_FONT_SIZE,
        "font_weight": DEFAULT_FONT_WEIGHT,
        "color_coding": DEFAULT_COLOR_CODING,
        "default_color": DEFAULT_COLOR,
        "color_is_automatic": True,
        "high_speed_threshold": DEFAULT_HIGH_SPEED_THRESHOLD,
        "low_speed_threshold": DEFAULT_LOW_SPEED_THRESHOLD,
        "high_speed_color": DEFAULT_HIGH_SPEED_COLOR,
        "low_speed_color": DEFAULT_LOW_SPEED_COLOR,
        "graph_enabled": DEFAULT_GRAPH_ENABLED,
        "history_minutes": DEFAULT_HISTORY_MINUTES,
        "graph_opacity": DEFAULT_GRAPH_OPACITY,
        "interface_mode": DEFAULT_INTERFACE_MODE,
        "selected_interfaces": [],
        "excluded_interfaces": network.interface.DEFAULT_EXCLUSIONS,
        "keep_data": DEFAULT_KEEP_DATA_DAYS,
        "reduce_motion": False,            # app-wide: disable preview/hold-still animations
        "show_usage_on_hover": True,       # the hover card's data-usage rows (Today / This month)
        "show_hover_tips": True,           # the hover card's right-click/double-click gesture hint
        "pause_in_menu": False,            # opt-in: surface Pause/Resume in the right-click menu
        "dark_mode": DEFAULT_DARK_MODE,
        "history_period": data.history_period.DEFAULT_PERIOD,
        "legend_position": DEFAULT_LEGEND_POSITION,
        "position_x": None,
        "position_y": None,
        "paused": False,
        "dynamic_update_enabled": DEFAULT_DYNAMIC_UPDATE_ENABLED,
        "speed_display_mode": DEFAULT_SPEED_DISPLAY_MODE,
        "decimal_places": DEFAULT_DECIMAL_PLACES,
        "text_alignment": DEFAULT_TEXT_ALIGNMENT,
        "free_move": DEFAULT_FREE_MOVE,
        "hardware_label_style": DEFAULT_HARDWARE_LABEL_STYLE,
        "keep_visible_fullscreen": DEFAULT_KEEP_VISIBLE_FULLSCREEN,
        "force_decimals": DEFAULT_FORCE_DECIMALS,
        "unit_type": DEFAULT_UNIT_TYPE,
        "swap_upload_download": DEFAULT_SWAP_UPLOAD_DOWNLOAD,
        "hide_arrows": DEFAULT_HIDE_ARROWS,
        "hide_unit_suffix": DEFAULT_HIDE_UNIT_SUFFIX,
        "background_color": DEFAULT_BACKGROUND_COLOR,
        "background_opacity": DEFAULT_BACKGROUND_OPACITY,
        "short_unit_labels": DEFAULT_SHORT_UNIT_LABELS,
        "tray_offset_x": DEFAULT_TRAY_OFFSET_X,
        "tray_offset_y": DEFAULT_TRAY_OFFSET_Y,
        "graph_window_pos": None,
        "settings_window_pos": None,
        "app_activity_window_pos": None,
        "monitor_window_pos": None,
        "monitor_active_tab": None,    # last Monitor tab the user left (overview/network/hardware)
        # Monitor Hardware graph (6.x). Colors None == vendor-auto (AMD red / Intel blue / Nvidia green).
        "monitor_hw_graph_mode": "combined",   # combined | separate | toggle
        "monitor_cpu_graph_color": None,
        "monitor_gpu_graph_color": None,
        "monitor_graph_legend": True,          # show the CPU/GPU legend on the combined graph
        "monitor_graph_smoothing": False,      # smooth (monotone-cubic) vs raw lines on the hw graph
        "monitor_graph_fixed_axis": True,      # hw graph y-axis fixed 0-100% vs auto-scale to the data
        "history_period_slider_value": 0,  # UI-specific state
        "show_legend": DEFAULT_SHOW_LEGEND,
        "use_separate_arrow_font": DEFAULT_USE_SEPARATE_ARROW_FONT,
        "arrow_font_family": DEFAULT_ARROW_FONT_FAMILY,
        "arrow_font_size": DEFAULT_ARROW_FONT_SIZE,
        "arrow_font_weight": DEFAULT_ARROW_FONT_WEIGHT,
        # Custom arrow glyphs (#129). Empty = the native i18n arrow (the Windows default).
        "arrow_up_symbol": "",
        "arrow_down_symbol": "",
        "monitor_cpu_enabled": DEFAULT_MONITOR_CPU_ENABLED,
        "monitor_gpu_enabled": DEFAULT_MONITOR_GPU_ENABLED,
        "monitor_ram_enabled": DEFAULT_MONITOR_RAM_ENABLED,
        "monitor_vram_enabled": DEFAULT_MONITOR_VRAM_ENABLED,
        "show_hardware_temps": DEFAULT_SHOW_HARDWARE_TEMPS,
        "show_hardware_power": DEFAULT_SHOW_HARDWARE_POWER,
        # Record cheap CPU/GPU/RAM utilisation to the DB always (not just while the widget displays it),
        # so the Monitor's history graphs have real data to show for past periods. Cheap (psutil +
        # one PDH read); temps/power are NOT recorded here (those stay gated/forced-only).
        "record_hardware_history": True,
        # Latency: ping the default GATEWAY (LAN-only, never leaves the network) so the Monitor shows
        # connection latency + loss history. The public anchor (true internet latency) is STRICTLY
        # opt-in — pinging an external host is the one thing that "phones home" for a privacy-first app.
        "latency_enabled": True,
        "latency_public_enabled": False,
        "latency_public_host": "1.1.1.1",
        # Pro-stats thresholds (0 = unset, so the related context line stays hidden). The advertised-plan
        # speeds drive "% of time below plan" in the network Stats-detail sheet; the throttle temp drives
        # "above N°C for X" so a thermal-throttling case is provable.
        "plan_down_mbps": 0,
        "plan_up_mbps": 0,
        "throttle_temp_c": 0,
        "stack_hardware_stats": DEFAULT_STACK_HARDWARE_STATS,
        "widget_display_mode": DEFAULT_WIDGET_DISPLAY_MODE,
        "widget_display_order": DEFAULT_WIDGET_DISPLAY_ORDER,
        "widget_cycle_interval": DEFAULT_WIDGET_CYCLE_INTERVAL,
        "cpu_load_high_threshold": DEFAULT_CPU_LOAD_HIGH_THRESHOLD,
        "cpu_load_low_threshold": DEFAULT_CPU_LOAD_LOW_THRESHOLD,
        "gpu_load_high_threshold": DEFAULT_GPU_LOAD_HIGH_THRESHOLD,
        "gpu_load_low_threshold": DEFAULT_GPU_LOAD_LOW_THRESHOLD,
        "check_for_updates": True,
        "skipped_version": None,
        "last_update_check": None,
        "first_run_v2_seen": False,
        "first_run_ever": True,
        "tooltip_hint_shown_count": 0,     # gesture-hint tooltip fades after a few sessions
        "temp_onboarding_dismissed": False,  # user opted out of the no-sensor temp explainer
        "preferred_monitor": None,
        # --- Data-usage / data-cap ---
        "data_cap_enabled": False,
        "data_cap_gb": 0.0,                # the cap in GB (0 = unset)
        "data_cap_reset_day": 1,           # billing reset day-of-month (1-28)
        "data_cap_count": "total",         # total | download | upload
        "data_cap_alert_enabled": True,    # opt-in toast at 80% / 100%
        "usage_alert_state": "",           # internal: "<period_key>:<levels>" for restart-safe alerts
    }
    
    # --- Schema Definition for Modern Config Validation ---
    VALIDATION_SCHEMA: Final[Dict[str, Dict[str, Any]]] = {
        "config_version": {"type": str, "default": CONFIG_SCHEMA_VERSION},
        "start_with_windows": {"type": bool, "default": DEFAULT_START_WITH_WINDOWS},
        "language": {"type": (str, type(None)), "default": None, "choices": list(I18nStrings.LANGUAGE_MAP.keys()) + [None]},
        # Allow -1.0 sentinel for SMART/adaptive mode in addition to positive intervals
        "update_rate": {"type": (int, float), "default": DEFAULT_UPDATE_RATE, "min": -1.0, "max": timers.MAXIMUM_UPDATE_RATE_SECONDS},
        "font_family": {"type": str, "default": DEFAULT_FONT_FAMILY},
        "font_size": {"type": int, "default": DEFAULT_FONT_SIZE, "min": fonts.FONT_SIZE_MIN, "max": fonts.FONT_SIZE_MAX},
        "font_weight": {"type": int, "default": DEFAULT_FONT_WEIGHT, "min": 1, "max": 1000},
        "color_coding": {"type": bool, "default": DEFAULT_COLOR_CODING},
        "default_color": {"type": str, "default": DEFAULT_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "color_is_automatic": {"type": bool, "default": True},
        "high_speed_threshold": {"type": (int, float), "default": DEFAULT_HIGH_SPEED_THRESHOLD, "min": 0, "max": ui_constants.sliders.SPEED_THRESHOLD_MAX_HIGH},
        "low_speed_threshold": {"type": (int, float), "default": DEFAULT_LOW_SPEED_THRESHOLD, "min": 0, "max": ui_constants.sliders.SPEED_THRESHOLD_MAX_LOW},
        "high_speed_color": {"type": str, "default": DEFAULT_HIGH_SPEED_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "low_speed_color": {"type": str, "default": DEFAULT_LOW_SPEED_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "graph_enabled": {"type": bool, "default": DEFAULT_GRAPH_ENABLED},
        "history_minutes": {"type": int, "default": DEFAULT_HISTORY_MINUTES, "min": 1, "max": 1440}, # Range from manual check
        "graph_opacity": {"type": (int, float), "default": DEFAULT_GRAPH_OPACITY, "min": ui_constants.sliders.OPACITY_MIN, "max": ui_constants.sliders.OPACITY_MAX},
        "interface_mode": {"type": str, "default": DEFAULT_INTERFACE_MODE, "choices": list(network.interface.VALID_INTERFACE_MODES)},
        "selected_interfaces": {"type": list, "default": [], "item_type": str},
        "excluded_interfaces": {"type": list, "default": network.interface.DEFAULT_EXCLUSIONS, "item_type": str},
        "keep_data": {"type": int, "default": DEFAULT_KEEP_DATA_DAYS, "choices": list(data.retention.DAYS_MAP.values()), "min": min(data.retention.DAYS_MAP.values()), "max": max(data.retention.DAYS_MAP.values())},
        "reduce_motion": {"type": bool, "default": False},
        "show_usage_on_hover": {"type": bool, "default": True},
        "show_hover_tips": {"type": bool, "default": True},
        "pause_in_menu": {"type": bool, "default": False},
        "dark_mode": {"type": bool, "default": DEFAULT_DARK_MODE},
        "history_period": {"type": str, "default": data.history_period.DEFAULT_PERIOD, "choices": list(data.history_period.PERIOD_MAP.values())},
        "legend_position": {"type": str, "default": DEFAULT_LEGEND_POSITION, "choices": data.legend_position.UI_OPTIONS},
        "position_x": {"type": (int, type(None)), "default": None},
        "position_y": {"type": (int, type(None)), "default": None},
        "paused": {"type": bool, "default": False},
        "dynamic_update_enabled": {"type": bool, "default": DEFAULT_DYNAMIC_UPDATE_ENABLED},
        "speed_display_mode": {"type": str, "default": DEFAULT_SPEED_DISPLAY_MODE, "choices": ["auto", "always_mbps"]},
        "decimal_places": {"type": int, "default": DEFAULT_DECIMAL_PLACES, "min": 0, "max": 2},
        "text_alignment": {"type": str, "default": DEFAULT_TEXT_ALIGNMENT, "choices": ["left", "center", "right"]},
        "use_separate_arrow_font": {"type": bool, "default": DEFAULT_USE_SEPARATE_ARROW_FONT},
        "arrow_font_family": {"type": str, "default": DEFAULT_ARROW_FONT_FAMILY},
        "arrow_font_size": {"type": int, "default": DEFAULT_ARROW_FONT_SIZE, "min": fonts.FONT_SIZE_MIN, "max": fonts.FONT_SIZE_MAX},
        "arrow_font_weight": {"type": int, "default": DEFAULT_ARROW_FONT_WEIGHT, "min": 1, "max": 1000},
        "arrow_up_symbol": {"type": str, "default": ""},
        "arrow_down_symbol": {"type": str, "default": ""},
        "free_move": {"type": bool, "default": DEFAULT_FREE_MOVE},
        "hardware_label_style": {"type": str, "default": DEFAULT_HARDWARE_LABEL_STYLE, "choices": ["icons_colored", "icons_monochrome", "text"]},
        "keep_visible_fullscreen": {"type": bool, "default": DEFAULT_KEEP_VISIBLE_FULLSCREEN},
        "force_decimals": {"type": bool, "default": DEFAULT_FORCE_DECIMALS},
        "unit_type": {"type": str, "default": DEFAULT_UNIT_TYPE, "choices": ["bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary"]},
        "swap_upload_download": {"type": bool, "default": DEFAULT_SWAP_UPLOAD_DOWNLOAD},
        "hide_arrows": {"type": bool, "default": DEFAULT_HIDE_ARROWS},
        "hide_unit_suffix": {"type": bool, "default": DEFAULT_HIDE_UNIT_SUFFIX},
        "background_color": {"type": str, "default": DEFAULT_BACKGROUND_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "background_opacity": {"type": int, "default": DEFAULT_BACKGROUND_OPACITY, "min": 0, "max": 100},
        "short_unit_labels": {"type": bool, "default": DEFAULT_SHORT_UNIT_LABELS},
        "tray_offset_x": {"type": int, "default": DEFAULT_TRAY_OFFSET_X, "min": 0, "max": 500},
        "tray_offset_y": {"type": int, "default": DEFAULT_TRAY_OFFSET_Y, "min": 0, "max": 500},
        "graph_window_pos": {"type": (dict, type(None)), "default": None},
        "settings_window_pos": {"type": (dict, type(None)), "default": None},
        "app_activity_window_pos": {"type": (dict, type(None)), "default": None},
        "monitor_window_pos": {"type": (dict, type(None)), "default": None},
        "monitor_active_tab": {"type": (str, type(None)), "default": None},
        "monitor_hw_graph_mode": {"type": str, "default": "combined", "choices": ["combined", "separate", "toggle"]},
        "monitor_cpu_graph_color": {"type": (str, type(None)), "default": None},
        "monitor_gpu_graph_color": {"type": (str, type(None)), "default": None},
        "monitor_graph_legend": {"type": bool, "default": True},
        "monitor_graph_smoothing": {"type": bool, "default": False},
        "monitor_graph_fixed_axis": {"type": bool, "default": True},
        "history_period_slider_value": {"type": int, "default": 0, "min": 0, "max": len(data.history_period.PERIOD_MAP) - 1},
        "show_legend": {"type": bool, "default": DEFAULT_SHOW_LEGEND},
        "monitor_cpu_enabled": {"type": bool, "default": DEFAULT_MONITOR_CPU_ENABLED},
        "monitor_gpu_enabled": {"type": bool, "default": DEFAULT_MONITOR_GPU_ENABLED},
        "monitor_ram_enabled": {"type": bool, "default": DEFAULT_MONITOR_RAM_ENABLED},
        "monitor_vram_enabled": {"type": bool, "default": DEFAULT_MONITOR_VRAM_ENABLED},
        "show_hardware_temps": {"type": bool, "default": DEFAULT_SHOW_HARDWARE_TEMPS},
        "record_hardware_history": {"type": bool, "default": True},
        "latency_enabled": {"type": bool, "default": True},
        "latency_public_enabled": {"type": bool, "default": False},
        "latency_public_host": {"type": str, "default": "1.1.1.1"},
        "plan_down_mbps": {"type": (int, float), "default": 0, "min": 0, "max": 100000},
        "plan_up_mbps": {"type": (int, float), "default": 0, "min": 0, "max": 100000},
        "throttle_temp_c": {"type": (int, float), "default": 0, "min": 0, "max": 130},
        "show_hardware_power": {"type": bool, "default": DEFAULT_SHOW_HARDWARE_POWER},
        "stack_hardware_stats": {"type": bool, "default": DEFAULT_STACK_HARDWARE_STATS},
        "widget_display_mode": {"type": str, "default": DEFAULT_WIDGET_DISPLAY_MODE, "choices": ["network_only", "cycle", "side_by_side"]},
        "widget_display_order": {"type": list, "default": DEFAULT_WIDGET_DISPLAY_ORDER, "item_type": str},
        "widget_cycle_interval": {"type": int, "default": DEFAULT_WIDGET_CYCLE_INTERVAL, "min": 1, "max": 60},
        "cpu_load_high_threshold": {"type": (int, float), "default": DEFAULT_CPU_LOAD_HIGH_THRESHOLD, "min": 0, "max": 100},
        "cpu_load_low_threshold": {"type": (int, float), "default": DEFAULT_CPU_LOAD_LOW_THRESHOLD, "min": 0, "max": 100},
        "gpu_load_high_threshold": {"type": (int, float), "default": DEFAULT_GPU_LOAD_HIGH_THRESHOLD, "min": 0, "max": 100},
        "gpu_load_low_threshold": {"type": (int, float), "default": DEFAULT_GPU_LOAD_LOW_THRESHOLD, "min": 0, "max": 100},
        "check_for_updates": {"type": bool, "default": True},
        "skipped_version": {"type": (str, type(None)), "default": None},
        "last_update_check": {"type": (str, type(None)), "default": None},
        "first_run_v2_seen": {"type": bool, "default": False},
        "first_run_ever": {"type": bool, "default": True},
        "tooltip_hint_shown_count": {"type": int, "default": 0, "min": 0},
        "temp_onboarding_dismissed": {"type": bool, "default": False},
        # QScreen.name() identifier (e.g. "\\.\DISPLAY1"). None = use primary.
        # If the saved screen isn't found at runtime, fall back to primary.
        "preferred_monitor": {"type": (str, type(None)), "default": None},
        # --- Data-usage / data-cap ---
        "data_cap_enabled": {"type": bool, "default": False},
        "data_cap_gb": {"type": (int, float), "default": 0.0, "min": 0.0, "max": 1_000_000.0},
        "data_cap_reset_day": {"type": int, "default": 1, "min": 1, "max": 28},
        "data_cap_count": {"type": str, "default": "total", "choices": ["total", "download", "upload"]},
        "data_cap_alert_enabled": {"type": bool, "default": True},
        "usage_alert_state": {"type": str, "default": ""},
    }


    def __init__(self) -> None:
        self.validate()


    def validate(self) -> None:
        if self.DEFAULT_FONT_SIZE < 1:
            raise ValueError("DEFAULT_FONT_SIZE must be positive")
        if not (0 <= self.DEFAULT_GRAPH_OPACITY <= 100):
            raise ValueError("DEFAULT_GRAPH_OPACITY must be between 0 and 100")
        if not self.CONFIG_FILENAME:
             raise ValueError("CONFIG_FILENAME must not be empty")

        actual_keys = set(self.DEFAULT_CONFIG.keys())
        expected_keys = set(self.VALIDATION_SCHEMA.keys())

        if actual_keys != expected_keys:
            missing = expected_keys - actual_keys
            extra = actual_keys - expected_keys
            raise ValueError(f"DEFAULT_CONFIG key mismatch. Missing: {missing or 'None'}. Extra: {extra or 'None'}.")


class ConfigurationConstants:
    """Container for configuration-related constant groups."""
    def __init__(self) -> None:
        self.defaults = ConfigConstants()
        self.messages = ConfigMessages()

# Singleton instance for easy access
config = ConfigurationConstants()
