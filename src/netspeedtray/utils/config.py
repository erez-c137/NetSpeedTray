"""
Configuration management for NetSpeedTray.

This module provides a robust ConfigManager for loading, validating, and saving application
settings to a JSON file. It ensures data integrity through atomic writes, default value
merging, and strict validation, preventing corrupted or invalid configurations from
affecting the application.
"""

import os
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .helpers import get_app_data_path
from .styles import is_dark_mode
from netspeedtray import constants


class ObfuscatingFormatter(logging.Formatter):
    """
    A custom logging formatter that automatically redacts sensitive information
    like user paths and IP addresses from all log records, including tracebacks.
    """
    # This regex is a robust pattern for matching both IPv4 and IPv6 addresses.
    IP_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[A-F0-9]{1,4}:){7}[A-F0-9]{1,4}\b", re.IGNORECASE)


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paths_to_obfuscate: List[str] = []
        self._setup_paths()


class ObfuscatingFormatter(logging.Formatter):
    """
    A custom logging formatter that automatically redacts sensitive information
    like user paths and IP addresses from all log records, including tracebacks.
    This version uses pre-compiled regexes for performance and robust normalization.
    """
    # Pre-compile the IP regex as a class attribute for efficiency.
    IP_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[A-F0-9]{1,4}:){7}[A-F0-9]{1,4}\b", re.IGNORECASE)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # This will hold our list of pre-compiled regex patterns for paths.
        self._path_regexes: List[re.Pattern] = []
        self._setup_paths()


    def _setup_paths(self):
        """
        Determines, normalizes, and pre-compiles regex patterns for all
        user-specific paths that need to be obfuscated.
        """
        import tempfile
        import sys

        paths_to_obfuscate = set()
        
        # --- 1. Gather all potential PII paths ---
        potential_paths = []
        try: potential_paths.append(str(Path.home().resolve()))
        except Exception: pass
        
        try: potential_paths.append(str(Path(get_app_data_path()).resolve()))
        except Exception: pass

        try: potential_paths.append(str(Path(tempfile.gettempdir()).resolve()))
        except Exception: pass

        if not getattr(sys, 'frozen', False):
            try:
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                potential_paths.append(project_root)
                python_exe_dir = os.path.dirname(os.path.abspath(sys.executable))
                potential_paths.append(python_exe_dir)
            except Exception: pass

        # --- 2. Normalize and filter the paths for maximum reliability ---
        for path_str in potential_paths:
            if not path_str or len(path_str) <= 3: # Ignore empty or trivial paths (e.g., "C:\")
                continue
            # Normalize path for the current OS (e.g., C:/Temp -> C:\Temp) and make it lowercase.
            # This makes the regex matching far more reliable.
            normalized_path = os.path.normcase(os.path.normpath(path_str))
            paths_to_obfuscate.add(normalized_path)

        # --- 3. Pre-compile the regexes for high-performance formatting ---
        # CRITICAL: Sort paths by length in reverse order.
        sorted_paths = sorted(list(paths_to_obfuscate), key=len, reverse=True)
        
        self._path_regexes = [re.compile(re.escape(p), re.IGNORECASE) for p in sorted_paths]

        # Use stderr for startup debugging as the logger itself is not yet fully configured.
        print(f"ObfuscatingFormatter initialized with {len(self._path_regexes)} path redaction patterns.", file=sys.stderr)


    def format(self, record: logging.LogRecord) -> str:
        """
        Formats the log record and then obfuscates sensitive information from the final string.
        """
        # First, allow the base formatter to do its work, including traceback formatting.
        formatted_message = super().format(record)
        sanitized_message = formatted_message

        # Now, run the pre-compiled regexes over the fully formatted string.
        # This is highly efficient and robust.
        for pattern in self._path_regexes:
            sanitized_message = pattern.sub("<REDACTED_PATH>", sanitized_message)

        # Redact any IP addresses found in the message.
        sanitized_message = self.IP_REGEX.sub("<REDACTED_IP>", sanitized_message)

        return sanitized_message


class ConfigError(Exception):
    """Custom exception for configuration-related errors, such as I/O or permission issues."""


class ConfigManager:
    """
    Manages loading, saving, and validation of NetSpeedTray's configuration.
    """
    BASE_DIR = Path(get_app_data_path())
    LOG_DIR = BASE_DIR


    def __init__(self, config_path: Optional[Union[str, Path]] = None) -> None:
        """
        Initializes the ConfigManager.
        """
        self.config_path = Path(config_path or self.BASE_DIR / constants.config.defaults.CONFIG_FILENAME)
        self.logger = logging.getLogger("NetSpeedTray.Config")
        self._last_config: Optional[Dict[str, Any]] = None


    @classmethod
    def get_log_file_path(cls) -> Path:
        """Returns the absolute path to the log file."""
        return cls.BASE_DIR / constants.logs.LOG_FILENAME


    @classmethod
    def setup_logging(cls, log_level: str = 'INFO') -> None:
        """
        Initializes logging with handlers for both a file and the console.
        """
        try:
            cls.ensure_directories()
            logger = logging.getLogger("NetSpeedTray")
            logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
            logger.handlers.clear()

            file_handler = logging.FileHandler(cls.get_log_file_path(), encoding='utf-8')
            file_handler.setLevel(logging.INFO)

            # Instantiate our new, robust ObfuscatingFormatter.
            file_formatter = ObfuscatingFormatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )

            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.ERROR)
            console_formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)

        except Exception as e:
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
            self.logger.warning(constants.config.messages.INVALID_NUMERIC.format(key=key, value=value, default=default))
            return default


    def _validate_boolean(self, key: str, value: Any, default: bool) -> bool:
        """Validates a value is a boolean."""
        if isinstance(value, bool):
            return value
        self.logger.warning(constants.config.messages.INVALID_BOOLEAN.format(key=key, value=value, default=default))
        return default


    def _validate_color_hex(self, key: str, value: Any, default: str) -> str:
        """Validates a value is a valid 6-digit hex color string."""
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value
        self.logger.warning(constants.config.messages.INVALID_COLOR.format(key=key, value=value, default=default))
        return default


    def _validate_choice(self, key: str, value: Any, default: str, choices: List[str]) -> str:
        """Validates a value is one of the allowed choices (case-insensitive)."""
        if isinstance(value, str) and value.lower() in [c.lower() for c in choices]:
            for choice in choices:
                if choice.lower() == value.lower():
                    return choice
        self.logger.warning(constants.config.messages.INVALID_CHOICE.format(key=key, value=value, default=default, choices=choices))
        return default


    def _validate_config(self, loaded_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates the configuration, merges it with defaults for missing keys,
        and sanitizes all values.
        """
        # --- Start with the default config, then merge the user's saved config over it. ---
        validated = constants.config.defaults.DEFAULT_CONFIG.copy()
        validated.update(loaded_config) # This applies the user's saved values.

        unknown_keys = set(loaded_config.keys()) - set(constants.config.defaults.DEFAULT_CONFIG.keys())
        if unknown_keys:
            self.logger.warning("Ignoring unknown config fields: %s", ", ".join(unknown_keys))

        # --- Apply Validations (using the merged dictionary) ---
        default_ref = constants.config.defaults.DEFAULT_CONFIG

        for key in ["color_coding", "graph_enabled", "dynamic_update_enabled", "free_move", 
                    "force_decimals", "dark_mode", "paused", "start_with_windows"]:
            validated[key] = self._validate_boolean(key, validated.get(key), default_ref[key])

        validated["update_rate"] = self._validate_numeric("update_rate", validated.get("update_rate"), constants.config.defaults.DEFAULT_UPDATE_RATE, constants.config.defaults.MINIMUM_UPDATE_RATE, 300.0)
        validated["font_size"] = self._validate_numeric("font_size", validated.get("font_size"), constants.config.defaults.DEFAULT_FONT_SIZE, 5, 72)
        validated["font_weight"] = self._validate_numeric("font_weight", validated.get("font_weight"), constants.config.defaults.DEFAULT_FONT_WEIGHT, 1, 1000)
        validated["high_speed_threshold"] = self._validate_numeric("high_speed_threshold", validated.get("high_speed_threshold"), constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD, 0, 10000)
        validated["low_speed_threshold"] = self._validate_numeric("low_speed_threshold", validated.get("low_speed_threshold"), constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD, 0, 10000)
        validated["history_minutes"] = self._validate_numeric("history_minutes", validated.get("history_minutes"), constants.config.defaults.DEFAULT_HISTORY_MINUTES, 1, 1440)
        validated["graph_opacity"] = self._validate_numeric("graph_opacity", validated.get("graph_opacity"), constants.config.defaults.DEFAULT_GRAPH_OPACITY, 0, 100)
        validated["keep_data"] = self._validate_numeric("keep_data", validated.get("keep_data"), constants.config.defaults.DEFAULT_HISTORY_PERIOD_DAYS, 1, 9999)
        validated["decimal_places"] = self._validate_numeric("decimal_places", validated.get("decimal_places"), constants.config.defaults.DEFAULT_DECIMAL_PLACES, 0, 2)
        validated["min_update_rate"] = self._validate_numeric("min_update_rate", validated.get("min_update_rate"), constants.config.defaults.DEFAULT_MIN_UPDATE_RATE, 0.1, 10.0)
        validated["max_update_rate"] = self._validate_numeric("max_update_rate", validated.get("max_update_rate"), constants.config.defaults.DEFAULT_MAX_UPDATE_RATE, 0.1, 10.0)

        for key in ["default_color", "high_speed_color", "low_speed_color"]:
            validated[key] = self._validate_color_hex(key, validated.get(key), default_ref[key])

        validated["interface_mode"] = self._validate_choice("interface_mode", validated.get("interface_mode"), constants.config.defaults.DEFAULT_INTERFACE_MODE, list(constants.network.interface.VALID_INTERFACE_MODES))
        validated["legend_position"] = self._validate_choice("legend_position", validated.get("legend_position"), constants.config.defaults.DEFAULT_LEGEND_POSITION, constants.data.legend_position.UI_OPTIONS)
        validated["history_period"] = self._validate_choice("history_period", validated.get("history_period"), constants.data.history_period.DEFAULT_PERIOD, list(constants.data.history_period.PERIOD_MAP.values()))
        validated["text_alignment"] = self._validate_choice("text_alignment", validated.get("text_alignment"), constants.config.defaults.DEFAULT_TEXT_ALIGNMENT, ["left", "center", "right"])
        validated["speed_display_mode"] = self._validate_choice("speed_display_mode", validated.get("speed_display_mode"), constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE, ["auto", "always_mbps"])
        validated["history_period_slider_value"] = self._validate_numeric("history_period_slider_value", validated.get("history_period_slider_value"), 0, 0, 10)

        # Get the list of supported language codes from the i18n constants
        supported_languages = list(constants.i18n.I18nStrings.LANGUAGE_MAP.keys())
        if validated.get("language") is not None and validated.get("language") not in supported_languages:
            self.logger.warning(
                f"Invalid language '{validated.get('language')}' in config. "
                f"Resetting to None (will use OS default). Valid choices: {supported_languages}"
            )
            validated["language"] = None

        # Special cases and cross-field validation
        if not isinstance(validated.get("selected_interfaces"), list) or not all(isinstance(i, str) for i in validated.get("selected_interfaces", [])):
            self.logger.warning(constants.config.messages.INVALID_INTERFACES.format(value=validated.get("selected_interfaces")))
            validated["selected_interfaces"] = []

        if validated["low_speed_threshold"] > validated["high_speed_threshold"]:
            self.logger.warning(constants.config.messages.THRESHOLD_SWAP)
            validated["low_speed_threshold"] = validated["high_speed_threshold"]

        for key in ["position_x", "position_y"]:
            if validated.get(key) is not None and not isinstance(validated.get(key), int):
                self.logger.warning(constants.config.messages.INVALID_POSITION.format(key=key, value=validated.get(key)))
                validated[key] = None
        
        if validated.get("graph_window_pos") is not None:
            pos = validated["graph_window_pos"]
            if not (isinstance(pos, dict) and 'x' in pos and 'y' in pos and isinstance(pos.get('x'), int) and isinstance(pos.get('y'), int)):
                self.logger.warning(constants.config.messages.INVALID_POSITION.format(key='graph_window_pos', value=pos))
                validated["graph_window_pos"] = None
        
        # Prune any keys that are not in the default config, in case of old config files
        final_config = {key: validated[key] for key in default_ref if key in validated}

        return final_config


    def load(self) -> Dict[str, Any]:
        """Loads and validates the configuration from the file."""
        if not self.config_path.exists():
            self.logger.info("Configuration file not found. Creating with default settings.")
            return self.reset_to_defaults()
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            self.logger.error("Configuration file is corrupt. Backing it up and using defaults.")
            try:
                corrupt_path = self.config_path.with_name(f"{self.config_path.name}.corrupt")
                shutil.move(self.config_path, corrupt_path)
            except Exception:
                self.logger.exception("Failed to back up corrupt config file.")
            return self.reset_to_defaults()
        except OSError as e:
            msg = f"OS error reading config file {self.config_path}: {e}"
            self.logger.critical(msg)
            raise ConfigError(msg) from e

        validated_config = self._validate_config(config)
        self._last_config = validated_config.copy()
        return validated_config


    def save(self, config: Dict[str, Any]) -> None:
        """Atomically saves the provided configuration to the file."""
        validated_config = self._validate_config(config)
        
        config_to_save = { key: value for key, value in validated_config.items() if value is not None }
        last_config_to_compare = { k: v for k, v in self._last_config.items() if v is not None } if self._last_config else None

        if last_config_to_compare == config_to_save:
            self.logger.debug("Skipping save, configuration is unchanged.")
            return

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=self.config_path.parent, encoding="utf-8"
            ) as temp_f:
                json.dump(config_to_save, temp_f, indent=4)
                temp_path = temp_f.name
            shutil.move(temp_path, self.config_path)
            self._last_config = validated_config.copy()
            self.logger.info("Configuration saved successfully to %s", self.config_path)
        except OSError as e:
            msg = f"Failed to save configuration to {self.config_path}: {e}"
            self.logger.error(msg)
            raise ConfigError(msg) from e


    def reset_to_defaults(self) -> Dict[str, Any]:
        """Resets the configuration to factory defaults and saves it."""
        self.logger.info("Resetting configuration to default values.")
        defaults = constants.config.defaults.DEFAULT_CONFIG.copy()
        self.save(defaults)
        return defaults