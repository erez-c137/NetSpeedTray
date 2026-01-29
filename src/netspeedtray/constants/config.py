"""
Constants for application configuration defaults and constraints.
"""
from typing import Final, Dict, Any

# --- IMPORT OTHER CONSTANTS TO CREATE A SINGLE SOURCE OF TRUTH ---
from netspeedtray.constants.timers import timers
from netspeedtray.constants.data import data
from netspeedtray.constants.network import network
from netspeedtray.constants.app import app
from netspeedtray.constants.color import color
from netspeedtray.constants.fonts import fonts
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.constants.ui import ui as ui_constants


class ConfigMessages:
    # ... (no changes needed in this class)
    """Log message templates for configuration validation."""
    INVALID_NUMERIC: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'"
    INVALID_BOOLEAN: Final[str] = "Invalid {key} '{value}', resetting to boolean default '{default}'"
    INVALID_COLOR: Final[str] = "Invalid color '{value}' for {key}, resetting to default '{default}'"
    INVALID_CHOICE: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'. Valid choices: {choices}"
    INVALID_INTERFACES: Final[str] = "Invalid selected_interfaces value '{value}', resetting to default []"
    THRESHOLD_SWAP: Final[str] = "low_speed_threshold > high_speed_threshold, setting low to high's value"
    INVALID_POSITION: Final[str] = "Invalid {key} '{value}', resetting to None"


    def __init__(self) -> None:
        pass # Validation is not strictly necessary for simple string holders


class ConfigConstants:
    """Defines default values and constraints for all application settings."""
    # --- Default Values for Individual Settings (referencing other constants) ---
    DEFAULT_UPDATE_RATE: Final[float] = 1.0
    MINIMUM_UPDATE_RATE: Final[float] = timers.MINIMUM_INTERVAL_MS / 1000.0
    DEFAULT_FONT_FAMILY: Final[str] = fonts.DEFAULT_FONT
    DEFAULT_FONT_SIZE: Final[int] = 9
    DEFAULT_FONT_WEIGHT: Final[int] = fonts.WEIGHT_DEMIBOLD
    DEFAULT_USE_SEPARATE_ARROW_FONT: Final[bool] = False
    DEFAULT_ARROW_FONT_FAMILY: Final[str] = fonts.DEFAULT_FONT
    DEFAULT_ARROW_FONT_SIZE: Final[int] = 9
    DEFAULT_ARROW_FONT_WEIGHT: Final[int] = fonts.WEIGHT_DEMIBOLD
    DEFAULT_COLOR: Final[str] = color.WHITE # Referencing color palette
    DEFAULT_COLOR_CODING: Final[bool] = False
    DEFAULT_HIGH_SPEED_THRESHOLD: Final[float] = 5.0
    DEFAULT_LOW_SPEED_THRESHOLD: Final[float] = 1.0
    DEFAULT_HIGH_SPEED_COLOR: Final[str] = color.GREEN # Referencing color palette
    DEFAULT_LOW_SPEED_COLOR: Final[str] = color.ORANGE # Referencing color palette
    DEFAULT_BACKGROUND_COLOR: Final[str] = "#000000" # Black
    DEFAULT_BACKGROUND_OPACITY: Final[int] = 0 # Percentage (0-100), default transparent
    DEFAULT_GRAPH_ENABLED: Final[bool] = False
    DEFAULT_HISTORY_MINUTES: Final[int] = 30
    DEFAULT_GRAPH_OPACITY: Final[int] = 30
    DEFAULT_INTERFACE_MODE: Final[str] = network.interface.DEFAULT_MODE
    DEFAULT_KEEP_DATA_DAYS: Final[int] = data.retention.DAYS_MAP[6] # 365 days (1 Year) default
    DEFAULT_DARK_MODE: Final[bool] = True
    DEFAULT_DYNAMIC_UPDATE_ENABLED: Final[bool] = True
    DEFAULT_SPEED_DISPLAY_MODE: Final[str] = "always_mbps"  # Prevents constant B/KB/MB jumping
    DEFAULT_UNIT_TYPE: Final[str] = "bytes_binary"  # Most users prefer MB/s (Bytes) akin to file downloads
    DEFAULT_SWAP_UPLOAD_DOWNLOAD: Final[bool] = True # Download on top is standard convention
    DEFAULT_HIDE_ARROWS: Final[bool] = False
    DEFAULT_HIDE_UNIT_SUFFIX: Final[bool] = False
    DEFAULT_SHORT_UNIT_LABELS: Final[bool] = True  # Shorter labels ("MB" vs "MB/s") for cleaner look
    DEFAULT_DECIMAL_PLACES: Final[int] = 1  # One decimal is sufficient and cleaner
    DEFAULT_TEXT_ALIGNMENT: Final[str] = "center"
    DEFAULT_FREE_MOVE: Final[bool] = False
    DEFAULT_FORCE_DECIMALS: Final[bool] = True
    DEFAULT_START_WITH_WINDOWS: Final[bool] = True
    DEFAULT_TRAY_OFFSET_X: Final[int] = 1
    DEFAULT_LEGEND_POSITION: Final[str] = data.legend_position.DEFAULT_LEGEND_POSITION
    DEFAULT_SHOW_LEGEND: Final[bool] = True

    CONFIG_FILENAME: Final[str] = "NetSpeedTray_Config.json"
    
    DEFAULT_CONFIG: Final[Dict[str, Any]] = {
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
        "force_decimals": DEFAULT_FORCE_DECIMALS,
        "unit_type": DEFAULT_UNIT_TYPE,
        "swap_upload_download": DEFAULT_SWAP_UPLOAD_DOWNLOAD,
        "hide_arrows": DEFAULT_HIDE_ARROWS,
        "hide_unit_suffix": DEFAULT_HIDE_UNIT_SUFFIX,
        "background_color": DEFAULT_BACKGROUND_COLOR,
        "background_opacity": DEFAULT_BACKGROUND_OPACITY,
        "short_unit_labels": DEFAULT_SHORT_UNIT_LABELS,
        "tray_offset_x": DEFAULT_TRAY_OFFSET_X,
        "graph_window_pos": None,
        "history_period_slider_value": 0,  # UI-specific state
        "show_legend": False,
        "use_separate_arrow_font": DEFAULT_USE_SEPARATE_ARROW_FONT,
        "arrow_font_family": DEFAULT_ARROW_FONT_FAMILY,
        "arrow_font_size": DEFAULT_ARROW_FONT_SIZE,
        "arrow_font_weight": DEFAULT_ARROW_FONT_WEIGHT,
    }
    
    # --- Schema Definition for Modern Config Validation ---
    VALIDATION_SCHEMA: Final[Dict[str, Dict[str, Any]]] = {
        "start_with_windows": {"type": bool, "default": DEFAULT_START_WITH_WINDOWS},
        "language": {"type": (str, type(None)), "default": None, "choices": list(I18nStrings.LANGUAGE_MAP.keys()) + [None]},
        "update_rate": {"type": (int, float), "default": DEFAULT_UPDATE_RATE, "min": MINIMUM_UPDATE_RATE, "max": timers.MAXIMUM_UPDATE_RATE_SECONDS},
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
        "free_move": {"type": bool, "default": DEFAULT_FREE_MOVE},
        "force_decimals": {"type": bool, "default": DEFAULT_FORCE_DECIMALS},
        "unit_type": {"type": str, "default": DEFAULT_UNIT_TYPE, "choices": ["bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary"]},
        "swap_upload_download": {"type": bool, "default": DEFAULT_SWAP_UPLOAD_DOWNLOAD},
        "hide_arrows": {"type": bool, "default": DEFAULT_HIDE_ARROWS},
        "hide_unit_suffix": {"type": bool, "default": DEFAULT_HIDE_UNIT_SUFFIX},
        "background_color": {"type": str, "default": DEFAULT_BACKGROUND_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "background_opacity": {"type": int, "default": DEFAULT_BACKGROUND_OPACITY, "min": 0, "max": 100},
        "short_unit_labels": {"type": bool, "default": DEFAULT_SHORT_UNIT_LABELS},
        "tray_offset_x": {"type": int, "default": DEFAULT_TRAY_OFFSET_X, "min": 0, "max": 500},
        "graph_window_pos": {"type": (dict, type(None)), "default": None},
        "history_period_slider_value": {"type": int, "default": 0, "min": 0, "max": len(data.history_period.PERIOD_MAP) - 1},
        "show_legend": {"type": bool, "default": DEFAULT_SHOW_LEGEND},
    }


    def __init__(self) -> None:
        self.validate()


    def validate(self) -> None:
        if self.DEFAULT_UPDATE_RATE < self.MINIMUM_UPDATE_RATE:
            raise ValueError(f"DEFAULT_UPDATE_RATE must be >= {self.MINIMUM_UPDATE_RATE}")
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