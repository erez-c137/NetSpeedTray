"""
Configuration management for NetSpeedTray.

This module provides a robust ConfigManager for loading, validating, and saving application
settings to a JSON file. It ensures data integrity through atomic writes, default value
merging, and strict validation, preventing corrupted or invalid configurations from
affecting the application.
"""

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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
    """Custom exception for configuration-related errors, such as I/O or permission issues."""


class ConfigManager:
    """
    Manages loading, saving, and validation of NetSpeedTray's configuration.

    This class handles all interactions with the `netspeedtray.conf` file, ensuring
    that the application always works with a valid and complete set of settings.
    It uses atomic writes to prevent data corruption during saves.

    Attributes:
        config_path: The full path to the configuration file.
        logger: A dedicated logger instance for configuration operations.
    """

    BASE_DIR = Path(get_app_data_path())
    LOG_DIR = BASE_DIR

    def __init__(self, config_path: Optional[Union[str, Path]] = None) -> None:
        """
        Initializes the ConfigManager.

        Args:
            config_path: Optional path to the config file. Defaults to 'netspeedtray.conf'
                         in the application's data directory.
        """
        self.config_path = Path(config_path or ConfigManager.BASE_DIR / ConfigConstants.CONFIG_FILENAME)
        self.logger = logging.getLogger("NetSpeedTray.Config")
        self._last_config: Optional[Dict[str, Any]] = None

    @classmethod
    def get_log_file_path(cls) -> Path:
        """Returns the absolute path to the log file."""
        return cls.BASE_DIR / 'netspeedtray.log'

    @classmethod
    def setup_logging(cls, log_level: str = 'INFO') -> None:
        """
        Initializes logging with handlers for both a file and the console.

        Args:
            log_level: The minimum logging level to capture (e.g., 'INFO', 'DEBUG').
        """
        try:
            cls.ensure_directories()
            logger = logging.getLogger("NetSpeedTray")
            logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
            logger.handlers.clear()

            # File handler (logs INFO and above)
            file_handler = logging.FileHandler(cls.get_log_file_path(), encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

            # Console handler (for critical errors, logs ERROR and above)
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.ERROR)
            console_formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)
        except Exception as e:
            # Fallback to basic console logging if setup fails
            logging.basicConfig(level=logging.ERROR)
            logging.error("Failed to initialize file logging, falling back to console: %s", e)

    @classmethod
    def ensure_directories(cls) -> None:
        """Creates necessary application directories if they don't exist."""
        try:
            cls.BASE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigError(f"Failed to create application directory at {cls.BASE_DIR}: {e}") from e

    def _validate_numeric(self, key: str, value: Any, default: Any, min_v: float, max_v: float) -> Union[int, float]:
        """Validates a numeric value is within a given range."""
        try:
            num_value = float(value)
            if not (min_v <= num_value <= max_v):
                raise ValueError("Value out of range")
            return int(num_value) if isinstance(default, int) else num_value
        except (TypeError, ValueError):
            self.logger.warning(ConfigMessages.INVALID_NUMERIC.format(key=key, value=value, default=default))
            return default

    def _validate_boolean(self, key: str, value: Any, default: bool) -> bool:
        """Validates a value is a boolean."""
        if isinstance(value, bool):
            return value
        self.logger.warning(ConfigMessages.INVALID_BOOLEAN.format(key=key, value=value, default=default))
        return default

    def _validate_color_hex(self, key: str, value: Any, default: str) -> str:
        """Validates a value is a valid 6-digit hex color string."""
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value
        self.logger.warning(ConfigMessages.INVALID_COLOR.format(key=key, value=value, default=default))
        return default

    def _validate_choice(self, key: str, value: Any, default: str, choices: List[str]) -> str:
        """Validates a value is one of the allowed choices (case-insensitive)."""
        if isinstance(value, str) and value.lower() in [c.lower() for c in choices]:
            # Return the canonical casing from the choices list
            for choice in choices:
                if choice.lower() == value.lower():
                    return choice
        self.logger.warning(ConfigMessages.INVALID_CHOICE.format(key=key, value=value, default=default, choices=choices))
        return default

    def _validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates the configuration, merges with defaults, and sanitizes values.

        This method ensures every setting is of the correct type and within valid
        bounds. Unknown keys from the loaded file are logged and ignored.

        Args:
            config: The raw configuration dictionary loaded from the file.

        Returns:
            A sanitized, complete, and valid configuration dictionary.
        """
        # Start with a full set of default values
        validated = ConfigConstants.DEFAULT_CONFIG.copy()

        # Log any keys from the file that are not in our default template
        unknown_keys = set(config.keys()) - set(validated.keys())
        if unknown_keys:
            self.logger.warning("Ignoring unknown config fields: %s", ", ".join(unknown_keys))

        # Update defaults with values from the loaded config, only for known keys
        for key in validated:
            if key in config:
                validated[key] = config[key]

        # --- Apply Validations ---
        # Boolean toggles
        for key in ["color_coding", "graph_enabled", "smart_threshold", "free_move", 
                    "force_decimals", "dark_mode", "dynamic_update_enabled", "paused"]:
            validated[key] = self._validate_boolean(key, validated[key], ConfigConstants.DEFAULT_CONFIG[key])

        # Numeric values with ranges
        validated["update_rate"] = self._validate_numeric("update_rate", validated["update_rate"], ConfigConstants.DEFAULT_UPDATE_RATE, ConfigConstants.MINIMUM_UPDATE_RATE, 300.0)
        validated["font_size"] = self._validate_numeric("font_size", validated["font_size"], ConfigConstants.DEFAULT_FONT_SIZE, 5, 72)
        validated["font_weight"] = self._validate_numeric("font_weight", validated["font_weight"], ConfigConstants.DEFAULT_FONT_WEIGHT, 1, 1000)
        validated["high_speed_threshold"] = self._validate_numeric("high_speed_threshold", validated["high_speed_threshold"], ConfigConstants.DEFAULT_HIGH_SPEED_THRESHOLD, 0, 10000)
        validated["low_speed_threshold"] = self._validate_numeric("low_speed_threshold", validated["low_speed_threshold"], ConfigConstants.DEFAULT_LOW_SPEED_THRESHOLD, 0, 10000)
        validated["history_minutes"] = self._validate_numeric("history_minutes", validated["history_minutes"], ConfigConstants.DEFAULT_HISTORY_MINUTES, 1, 1440)
        validated["graph_opacity"] = self._validate_numeric("graph_opacity", validated["graph_opacity"], ConfigConstants.DEFAULT_GRAPH_OPACITY, 0, 100)
        validated["keep_data"] = self._validate_numeric("keep_data", validated["keep_data"], ConfigConstants.DEFAULT_HISTORY_PERIOD_DAYS, 1, 9999)
        validated["decimal_places"] = self._validate_numeric("decimal_places", validated["decimal_places"], ConfigConstants.DEFAULT_DECIMAL_PLACES, 0, 2)
        validated["min_update_rate"] = self._validate_numeric("min_update_rate", validated["min_update_rate"], ConfigConstants.DEFAULT_MIN_UPDATE_RATE, 0.1, 10.0)
        validated["max_update_rate"] = self._validate_numeric("max_update_rate", validated["max_update_rate"], ConfigConstants.DEFAULT_MAX_UPDATE_RATE, 0.1, 10.0)

        # Colors
        for key in ["default_color", "high_speed_color", "low_speed_color"]:
            validated[key] = self._validate_color_hex(key, validated[key], ConfigConstants.DEFAULT_CONFIG[key])

        # String choices
        validated["interface_mode"] = self._validate_choice("interface_mode", validated["interface_mode"], ConfigConstants.DEFAULT_INTERFACE_MODE, ConfigConstants.VALID_INTERFACE_MODES)
        validated["legend_position"] = self._validate_choice("legend_position", validated["legend_position"], ConfigConstants.DEFAULT_LEGEND_POSITION, LegendPositionConstants.UI_OPTIONS)
        validated["history_period"] = self._validate_choice("history_period", validated["history_period"], HistoryPeriodConstants.DEFAULT_PERIOD, list(HistoryPeriodConstants.PERIOD_MAP.values()))
        validated["text_alignment"] = self._validate_choice("text_alignment", validated["text_alignment"], ConfigConstants.DEFAULT_TEXT_ALIGNMENT, ["left", "center", "right"])
        validated["speed_display_mode"] = self._validate_choice("speed_display_mode", validated["speed_display_mode"], ConfigConstants.DEFAULT_SPEED_DISPLAY_MODE, ["auto", "always_mbps"])


        # Special cases and cross-field validation
        if not isinstance(validated["selected_interfaces"], list) or not all(isinstance(i, str) for i in validated["selected_interfaces"]):
            self.logger.warning(ConfigMessages.INVALID_INTERFACES.format(value=validated["selected_interfaces"]))
            validated["selected_interfaces"] = []

        if validated["low_speed_threshold"] > validated["high_speed_threshold"]:
            self.logger.warning(ConfigMessages.THRESHOLD_SWAP)
            validated["low_speed_threshold"] = validated["high_speed_threshold"]

        if validated["min_update_rate"] > validated["max_update_rate"]:
            self.logger.warning(f"min_update_rate ({validated['min_update_rate']}) > max_update_rate ({validated['max_update_rate']}). Swapping.")
            validated["min_update_rate"], validated["max_update_rate"] = validated["max_update_rate"], validated["min_update_rate"]

        for key in ["position_x", "position_y"]:
            if validated[key] is not None and not isinstance(validated[key], int):
                self.logger.warning(ConfigMessages.INVALID_POSITION.format(key=key, value=validated[key]))
                validated[key] = None

        return validated

    def load(self) -> Dict[str, Any]:
        """
        Loads the configuration from the file, validates it, and returns it.

        If the file doesn't exist, a default configuration is created and saved.
        If the file is corrupt, it's backed up and replaced with defaults.

        Returns:
            A dictionary containing the full, validated application configuration.

        Raises:
            ConfigError: If the config file cannot be read due to permissions or OS errors.
        """
        config = {}
        if not self.config_path.exists():
            self.logger.info("Configuration file not found. Creating with default settings.")
            return self.reset_to_defaults()
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            self.logger.error("Configuration file is corrupt. Backing it up and using defaults.")
            corrupt_path = self.config_path.with_suffix(".corrupt")
            shutil.move(self.config_path, corrupt_path)
            return self.reset_to_defaults()
        except PermissionError as e:
            msg = f"Permission denied reading config file: {self.config_path}"
            self.logger.critical(msg)
            raise ConfigError(msg) from e
        except OSError as e:
            msg = f"OS error reading config file {self.config_path}: {e}"
            self.logger.critical(msg)
            raise ConfigError(msg) from e

        validated_config = self._validate_config(config)
        self._last_config = validated_config.copy()
        return validated_config

    def save(self, config: Dict[str, Any]) -> None:
        """
        Atomically saves the provided configuration to the file.

        The configuration is first validated. The save is skipped if the new
        configuration is identical to the last one saved.

        Args:
            config: The configuration dictionary to save.

        Raises:
            ConfigError: If saving fails due to permissions or other OS errors.
        """
        validated_config = self._validate_config(config)
        if self._last_config == validated_config:
            self.logger.debug("Skipping save, configuration is unchanged.")
            return

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write using a temporary file
            with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=self.config_path.parent, encoding="utf-8"
            ) as temp_f:
                json.dump(validated_config, temp_f, indent=4)
                temp_path = temp_f.name
            shutil.move(temp_path, self.config_path)
            self._last_config = validated_config.copy()
            self.logger.info("Configuration saved successfully to %s", self.config_path)
        except (PermissionError, OSError) as e:
            msg = f"Failed to save configuration to {self.config_path}: {e}"
            self.logger.error(msg)
            raise ConfigError(msg) from e

    def reset_to_defaults(self) -> Dict[str, Any]:
        """Resets the configuration to factory defaults and saves it."""
        self.logger.info("Resetting configuration to default values.")
        defaults = ConfigConstants.DEFAULT_CONFIG.copy()
        self.save(defaults)
        return defaults