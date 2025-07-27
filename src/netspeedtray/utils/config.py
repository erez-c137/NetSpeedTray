"""
Configuration management for NetSpeedTray.

This module provides utilities to load, save, and validate application settings stored in a JSON file.
The `ConfigManager` class ensures atomic writes and robust validation against predefined constants.
It removes deprecated fields (e.g., widget_pos_x, widget_pos_y) and unknown fields to maintain a clean configuration.
"""

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .helpers import get_app_data_path
from ..constants import HelperConstants
from ..constants.constants import (
    ConfigConstants,
    ConfigMessages,
    DataRetentionConstants,
    HistoryPeriodConstants,
    LegendPositionConstants,
)


class ConfigError(Exception):
    """Exception raised for configuration-related errors, such as permission issues or file corruption."""


class ConfigManager:
    """Manages NetSpeedTray configuration persistence and validation.

    Handles loading from and saving to a JSON file in the app data directory, ensuring all settings
    are valid and complete by merging with defaults. Removes deprecated or unknown fields to maintain
    a clean configuration. Uses atomic writes to prevent data corruption.

    Attributes:
        config_path: Path to the configuration file.
        logger: Logger instance for tracking configuration errors and warnings.
        model: Optional Model instance for validating network interfaces (not implemented here).
        _last_config: Cached last saved configuration to skip redundant writes.
    """

    BASE_DIR = Path(get_app_data_path())
    LOG_DIR = BASE_DIR

    def __init__(self, config_path: Optional[str] = None, model: Optional[Any] = None) -> None:
        """Initialize the ConfigManager.

        Args:
            config_path: Optional custom path for the config file. Defaults to 'netspeedtray.conf'
                in the app data directory if None.
            model: Optional Model instance for interface validation (currently unused).
        """
        self.config_path = Path(config_path or os.path.join(get_app_data_path(), ConfigConstants.CONFIG_FILENAME))
        self.logger = logging.getLogger("NetSpeedTray.Config")
        self.model = model
        self._last_config: Optional[Dict[str, Any]] = None

    @classmethod
    def get_log_file_path(cls) -> Path:
        """Returns the path to the log file.

        Returns:
            Path: Path to netspeedtray.log in the AppData folder.
        """
        return cls.BASE_DIR / 'netspeedtray.log'

    @classmethod
    def setup_logging(cls, log_level: str = 'INFO') -> None:
        """Initializes logging with a file handler in AppData and a console handler.

        Args:
            log_level: Logging level (e.g., 'INFO', 'ERROR'). Defaults to 'INFO' unless overridden.
        """
        try:
            cls.ensure_directories()
            logger = logging.getLogger("NetSpeedTray")
            logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
            logger.handlers.clear()

            # File handler
            log_file_path = cls.get_log_file_path()
            file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

            # Console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.ERROR)
            console_formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)
        except Exception as e:
            # Fallback to console logging
            logging.basicConfig(level=logging.ERROR)
            logging.error("Failed to initialize logging: %s", e)

    @classmethod
    def ensure_directories(cls) -> None:
        """Creates the base and log directories in AppData if they don't exist."""
        try:
            cls.LOG_DIR.mkdir(parents=True, exist_ok=True)
            cls.BASE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ConfigError(f"Failed to create directories: {e}")

    def _validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and correct configuration values, removing unknown fields.

        Ensures settings conform to expected types and ranges, falling back to defaults if invalid.
        Logs warnings only for significant issues (e.g., repeated invalid config values).

        Args:
            config: Configuration dictionary to validate.

        Returns:
            Validated configuration dictionary with only supported fields.
        """
        validated_config = config.copy() # Use a different variable name to avoid confusion with parameter
        default_keys = set(ConfigConstants.DEFAULT_CONFIG.keys())

        # Remove unknown fields first
        removed_keys = [key for key in validated_config if key not in default_keys]
        if removed_keys:
            self.logger.warning("Removed unknown config fields: %s", ", ".join(removed_keys))
            for key in removed_keys:
                del validated_config[key]

        # Ensure all default keys are present, merging with defaults
        # This also handles cases where a valid key might be missing from a loaded (but incomplete) config
        current_config_with_defaults = ConfigConstants.DEFAULT_CONFIG.copy()
        current_config_with_defaults.update(validated_config) # Update defaults with loaded values
        validated_config = current_config_with_defaults # Now validated_config has all keys, with loaded values overriding defaults

        # --- Individual Field Validations ---

        # Update Rate
        update_rate = validated_config.get("update_rate")
        if not isinstance(update_rate, (int, float)) or update_rate < ConfigConstants.MINIMUM_UPDATE_RATE:
            self.logger.warning(ConfigMessages.INVALID_UPDATE_RATE, update_rate, ConfigConstants.DEFAULT_UPDATE_RATE)
            validated_config["update_rate"] = ConfigConstants.DEFAULT_UPDATE_RATE

        # Font Settings
        if not isinstance(validated_config.get("font_family"), str) or not validated_config.get("font_family"):
            self.logger.warning(ConfigMessages.INVALID_FONT_FAMILY, validated_config.get("font_family"), HelperConstants.DEFAULT_FONT)
            validated_config["font_family"] = HelperConstants.DEFAULT_FONT
        font_size = validated_config.get("font_size")
        if not isinstance(font_size, (int, float)) or font_size <= 0: # Allow float then cast if settings UI uses it
            self.logger.warning(ConfigMessages.INVALID_FONT_SIZE, font_size, ConfigConstants.DEFAULT_FONT_SIZE)
            validated_config["font_size"] = ConfigConstants.DEFAULT_FONT_SIZE
        else:
            validated_config["font_size"] = int(font_size) # Ensure it's an int

        font_weight = validated_config.get("font_weight")
        if not isinstance(font_weight, int) or not (0 <= font_weight <= 1000): # QFont weights
            self.logger.warning(ConfigMessages.INVALID_FONT_WEIGHT, font_weight, ConfigConstants.DEFAULT_FONT_WEIGHT)
            validated_config["font_weight"] = ConfigConstants.DEFAULT_FONT_WEIGHT

        # Colors
        hex_pattern = re.compile(r"^#[0-9a-fA-F]{6}$") # Standard 6-digit hex
        for key in ["default_color", "high_speed_color", "low_speed_color"]:
            color_value = validated_config.get(key)
            if not isinstance(color_value, str) or not hex_pattern.match(color_value):
                self.logger.warning(ConfigMessages.INVALID_COLOR.format(value=color_value, key=key))
                validated_config[key] = ConfigConstants.DEFAULT_CONFIG[key]

        # Speed Thresholds
        high_thresh = validated_config.get("high_speed_threshold")
        low_thresh = validated_config.get("low_speed_threshold")
        if not isinstance(high_thresh, (int, float)) or high_thresh < 0:
            self.logger.warning(ConfigMessages.INVALID_HIGH_THRESHOLD, high_thresh, ConfigConstants.DEFAULT_HIGH_SPEED_THRESHOLD)
            validated_config["high_speed_threshold"] = ConfigConstants.DEFAULT_HIGH_SPEED_THRESHOLD
        if not isinstance(low_thresh, (int, float)) or low_thresh < 0:
            self.logger.warning(ConfigMessages.INVALID_LOW_THRESHOLD, low_thresh, ConfigConstants.DEFAULT_LOW_SPEED_THRESHOLD)
            validated_config["low_speed_threshold"] = ConfigConstants.DEFAULT_LOW_SPEED_THRESHOLD
        
        # Ensure low is not greater than high after individual validation
        if validated_config["low_speed_threshold"] > validated_config["high_speed_threshold"]:
            self.logger.warning(ConfigMessages.THRESHOLD_SWAP)
            # Option: Swap them, or reset low to be less than high
            validated_config["low_speed_threshold"] = validated_config["high_speed_threshold"] 

        # History Minutes
        history_min = validated_config.get("history_minutes")
        if not isinstance(history_min, int) or history_min < 1:
            self.logger.warning(ConfigMessages.INVALID_HISTORY, history_min, ConfigConstants.DEFAULT_HISTORY_MINUTES)
            validated_config["history_minutes"] = ConfigConstants.DEFAULT_HISTORY_MINUTES

        # Graph Opacity
        opacity = validated_config.get("graph_opacity")
        if not isinstance(opacity, int) or not (0 <= opacity <= 100):
            self.logger.warning(ConfigMessages.INVALID_OPACITY, opacity, ConfigConstants.DEFAULT_GRAPH_OPACITY)
            validated_config["graph_opacity"] = ConfigConstants.DEFAULT_GRAPH_OPACITY

        # Data Retention (keep_data stores number of days)
        keep_data_days = validated_config.get("keep_data")
        try:
            keep_data_days = int(keep_data_days)
            # Check if this number of days is a valid value in DAYS_MAP.values()
            if keep_data_days not in DataRetentionConstants.DAYS_MAP.values():
                raise ValueError("Invalid number of days for data retention")
            validated_config["keep_data"] = keep_data_days
        except (TypeError, ValueError):
            self.logger.warning(ConfigMessages.INVALID_KEEP_DATA, validated_config.get("keep_data"), ConfigConstants.DEFAULT_HISTORY_PERIOD_DAYS)
            validated_config["keep_data"] = ConfigConstants.DEFAULT_HISTORY_PERIOD_DAYS

        # Smart Threshold
        smart_thresh = validated_config.get("smart_threshold")
        if not isinstance(smart_thresh, (int, float)) or smart_thresh <= 0: # Assuming it should be positive
            self.logger.warning(ConfigMessages.INVALID_SMART_THRESHOLD, smart_thresh, ConfigConstants.DEFAULT_SMART_THRESHOLD)
            validated_config["smart_threshold"] = ConfigConstants.DEFAULT_SMART_THRESHOLD

        # Boolean Settings
        for key in ["dark_mode", "color_coding", "graph_enabled", "use_megabytes", "dynamic_update_enabled", "paused"]:
            if not isinstance(validated_config.get(key), bool):
                default_bool_val = ConfigConstants.DEFAULT_CONFIG.get(key, False) # Fallback for safety
                self.logger.warning(getattr(ConfigMessages, f"INVALID_{key.upper()}"), validated_config.get(key), default_bool_val)
                validated_config[key] = default_bool_val

        # Interface Mode and Selection
        if validated_config.get("interface_mode") not in ConfigConstants.VALID_INTERFACE_MODES:
            self.logger.warning(ConfigMessages.INVALID_INTERFACE_MODE, validated_config.get("interface_mode"), ConfigConstants.DEFAULT_INTERFACE_MODE)
            validated_config["interface_mode"] = ConfigConstants.DEFAULT_INTERFACE_MODE
        if not isinstance(validated_config.get("selected_interfaces"), list):
            self.logger.warning(ConfigMessages.INVALID_INTERFACES, validated_config.get("selected_interfaces"), [])
            validated_config["selected_interfaces"] = []
        else: # Ensure all items in selected_interfaces are strings
            validated_config["selected_interfaces"] = [str(item) for item in validated_config["selected_interfaces"] if isinstance(item, str)]


        # History Period (string value like "System Uptime")
        if validated_config.get("history_period") not in HistoryPeriodConstants.PERIOD_MAP.values():
            self.logger.warning(ConfigMessages.INVALID_HISTORY_PERIOD, validated_config.get("history_period"), HistoryPeriodConstants.DEFAULT_PERIOD)
            validated_config["history_period"] = HistoryPeriodConstants.DEFAULT_PERIOD
        
        # --- CORRECTED LEGEND POSITION VALIDATION ---
        legend_pos_from_file = validated_config.get("legend_position", ConfigConstants.DEFAULT_LEGEND_POSITION)
        
        # Standardize by checking lowercase against lowercase list of options
        valid_legend_options_lower = [opt.lower() for opt in LegendPositionConstants.UI_OPTIONS]
        
        if legend_pos_from_file.lower() not in valid_legend_options_lower:
            self.logger.warning(
                ConfigMessages.INVALID_LEGEND_POSITION,
                legend_pos_from_file,
                ConfigConstants.DEFAULT_LEGEND_POSITION
            )
            validated_config["legend_position"] = ConfigConstants.DEFAULT_LEGEND_POSITION
        else:
            # Ensure canonical casing is stored (e.g., if "off" was in file, store "Off")
            canonical_pos = LegendPositionConstants.UI_OPTIONS[valid_legend_options_lower.index(legend_pos_from_file.lower())]
            validated_config["legend_position"] = canonical_pos
        # --- END CORRECTION ---

        # Dynamic Update Rates
        min_rate = validated_config.get("min_update_rate")
        max_rate = validated_config.get("max_update_rate")
        if not isinstance(min_rate, (int, float)) or min_rate <= 0 or min_rate < ConfigConstants.MINIMUM_UPDATE_RATE:
            self.logger.warning(ConfigMessages.INVALID_MIN_UPDATE_RATE, min_rate, ConfigConstants.DEFAULT_MIN_UPDATE_RATE)
            validated_config["min_update_rate"] = ConfigConstants.DEFAULT_MIN_UPDATE_RATE
        if not isinstance(max_rate, (int, float)) or max_rate <= 0 or max_rate > ConfigConstants.DEFAULT_MAX_UPDATE_RATE: # Check against constant
            self.logger.warning(ConfigMessages.INVALID_MAX_UPDATE_RATE, max_rate, ConfigConstants.DEFAULT_MAX_UPDATE_RATE)
            validated_config["max_update_rate"] = ConfigConstants.DEFAULT_MAX_UPDATE_RATE
        
        if validated_config["min_update_rate"] > validated_config["max_update_rate"]:
            self.logger.warning(f"min_update_rate ({validated_config['min_update_rate']}) was greater than max_update_rate ({validated_config['max_update_rate']}). Swapping.")
            validated_config["min_update_rate"], validated_config["max_update_rate"] = validated_config["max_update_rate"], validated_config["min_update_rate"]


        # Position Coordinates
        for key in ["position_x", "position_y"]:
            value = validated_config.get(key)
            if value is not None: # Only validate if not None
                try:
                    # Allow only positive integers or None for position
                    pos_val = int(value)
                    if pos_val < -32768 or pos_val > 32767: # Reasonable screen coordinate limits
                         raise ValueError("Position out of reasonable bounds")
                    validated_config[key] = pos_val
                except (TypeError, ValueError):
                    self.logger.warning(getattr(ConfigMessages, f"INVALID_{key.upper()}"), value, None)
                    validated_config[key] = None # Reset to None if invalid

        return validated_config

    def load(self) -> Dict[str, Any]:
        """Load and validate the configuration from the JSON file.

        Merges loaded settings with defaults, removes deprecated/unknown fields, and saves defaults
        if the file doesn’t exist.

        Returns:
            Validated configuration dictionary with only supported fields.

        Raises:
            ConfigError: If loading fails due to permissions, OS errors, or unexpected issues.
        """
        config = ConfigConstants.DEFAULT_CONFIG.copy()
        try:
            if self.config_path.exists():
                with self.config_path.open("r", encoding="utf-8") as f:
                    config.update(json.load(f))
            else:
                self.save(config)
            validated_config = self._validate_config(config)
            self._last_config = validated_config.copy()
            return validated_config
        except PermissionError as e:
            self.logger.error("Permission denied reading config file %s: %s", self.config_path, e)
            raise ConfigError(f"Permission denied reading config file: {e}") from e
        except json.JSONDecodeError as e:
            self.logger.warning("Invalid JSON in config file %s, reverting to defaults: %s", self.config_path, e)
            self.save(config)
            self._last_config = config.copy()
            return config
        except OSError as e:
            self.logger.error("OS error reading config file %s: %s", self.config_path, e)
            raise ConfigError(f"OS error reading config file: {e}") from e
        except Exception as e:
            self.logger.error("Unexpected error loading config from %s: %s", self.config_path, e)
            raise ConfigError(f"Unexpected error loading config: {e}") from e

    def save(self, config: Dict[str, Any]) -> None:
        """Save the configuration to the JSON file atomically.

        Skips saving if the configuration is unchanged. Uses a temporary file for atomicity.

        Args:
            config: Configuration dictionary to save.

        Raises:
            ConfigError: If saving fails due to permissions or OS errors.
        """
        validated_config = self._validate_config(config)
        if self._last_config == validated_config:
            return
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, dir=self.config_path.parent, suffix=".tmp", encoding="utf-8"
            ) as temp_file:
                json.dump(validated_config, temp_file, indent=4)
            shutil.move(temp_file.name, self.config_path)
            self._last_config = validated_config.copy()
        except (PermissionError, OSError) as e:
            self.logger.error("Failed to save config to %s: %s", self.config_path, e)
            raise ConfigError(f"Failed to save config: {e}") from e
        except Exception as e:
            self.logger.error("Unexpected error saving config to %s: %s", self.config_path, e)
            raise ConfigError(f"Unexpected error saving config: {e}") from e

    def reset_to_defaults(self) -> Dict[str, Any]:
        """Reset the configuration to defaults and save it.

        Returns:
            Default configuration dictionary with only supported fields.

        Raises:
            ConfigError: If saving the defaults fails.
        """
        defaults = ConfigConstants.DEFAULT_CONFIG.copy()
        self.save(defaults)
        return defaults