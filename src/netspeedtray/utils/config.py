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

from netspeedtray.utils.helpers import get_app_data_path
from netspeedtray.utils.styles import is_dark_mode
from netspeedtray import constants


class ObfuscatingFormatter(logging.Formatter):
    """
    A custom logging formatter that automatically redacts sensitive information
    like user paths and IP addresses from all log records, including tracebacks.
    This version uses pre-compiled regexes for performance and robust normalization.
    """
    IP_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[A-F0-9]{1,4}:){7}[A-F0-9]{1,4}\b", re.IGNORECASE)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._path_regexes: List[re.Pattern] = []
        self._setup_paths()

    def _setup_paths(self):
        import sys
        paths_to_obfuscate = set()
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
        for path_str in potential_paths:
            if not path_str or len(path_str) <= 3: continue
            normalized_path = os.path.normcase(os.path.normpath(path_str))
            paths_to_obfuscate.add(normalized_path)
        sorted_paths = sorted(list(paths_to_obfuscate), key=len, reverse=True)
        self._path_regexes = [re.compile(re.escape(p), re.IGNORECASE) for p in sorted_paths]
        print(f"ObfuscatingFormatter initialized with {len(self._path_regexes)} path redaction patterns.", file=sys.stderr)

    def format(self, record: logging.LogRecord) -> str:
        formatted_message = super().format(record)
        sanitized_message = formatted_message
        for pattern in self._path_regexes:
            sanitized_message = pattern.sub("<REDACTED_PATH>", sanitized_message)
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


    def _migrate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrates old configuration fields to the current schema.
        This ensures a smooth transition and preserves user settings when fields are renamed.
        """
        migration_map = {
            "monitoring_mode": "interface_mode",
            "tray_icon_offset": "tray_offset_x",
            "tray_offset": "tray_offset_x",
            "dynamic_update_rate": "dynamic_update_enabled",
            "color_coding_enabled": "color_coding",
            "history_duration": "history_minutes",
            "fixed_width_values": None # Explicitly drop removed field
        }

        migrated = config.copy()
        changes_made = False

        for old_key, new_key in migration_map.items():
            if old_key in migrated:
                val = migrated.pop(old_key)
                if new_key:
                    # Only move if the new key doesn't already exist or has a default-like value
                    if new_key not in migrated:
                        migrated[new_key] = val
                        self.logger.info(f"Migrated config field: '{old_key}' -> '{new_key}'")
                        changes_made = True
                else:
                    self.logger.debug(f"Dropped obsolete config field: '{old_key}'")
                    changes_made = True
        
        # Unit type migration (old short names to new explicit names)
        unit_migration = {
            "bytes": "bytes_binary",
            "bits": "bits_decimal"
        }
        current_unit = migrated.get("unit_type")
        if current_unit in unit_migration:
            migrated["unit_type"] = unit_migration[current_unit]
            self.logger.info(f"Migrated unit_type: '{current_unit}' -> '{migrated['unit_type']}'")
            changes_made = True

        return migrated


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
            # Set the root logger level to the most verbose level we will use.
            logger.setLevel(logging.DEBUG)
            logger.handlers.clear()

            # Create and configure the rotating file handler
            file_handler = logging.handlers.RotatingFileHandler(
                cls.get_log_file_path(),
                maxBytes=constants.logs.MAX_LOG_SIZE,
                backupCount=constants.logs.LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            file_handler.setLevel(constants.logs.FILE_LOG_LEVEL)

            file_formatter = ObfuscatingFormatter(
                constants.logs.LOG_FORMAT,
                datefmt=constants.logs.LOG_DATE_FORMAT
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

            # Create and configure the console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(constants.logs.CONSOLE_LOG_LEVEL)
            console_formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)

            logger.info("Logging initialized successfully.")
        except Exception as e:
            logging.basicConfig(level=logging.ERROR)
            logging.error("Failed to initialize file logging, falling back to basic console: %s", e)


    @classmethod
    def ensure_directories(cls) -> None:
        """Creates necessary application directories if they don't exist."""
        try:
            cls.BASE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigError(f"Failed to create application directory at {cls.BASE_DIR}: {e}") from e


    def _validate_value(self, key: str, value: Any, rules: Dict[str, Any]) -> Any:
        """
        Validates a single value against its schema rules.
        Returns the valid value (sanitized/coerced) or the default if invalid.
        """
        default = rules["default"]
        
        # 1. Type Check
        expected_type = rules.get("type")
        if expected_type:
            # Handle Optional types (e.g. (int, type(None)))
            if not isinstance(value, expected_type):
                # Special case: float to int conversion if safe?
                # For now, strict type check as per schema.
                self.logger.warning(f"Invalid type for {key}: expected {expected_type}, got {type(value)}. Resetting to default.")
                return default

        # If value is None and allowed (via type), return it early unless default is not None?
        # If type allows None, and value is None, it is valid. 
        # Check specific constraints only if value is not None.
        if value is None:
            return value

        # 2. Choice Check
        choices = rules.get("choices")
        if choices:
            # Case-insensitive string match if applicable
            if isinstance(value, str) and isinstance(choices[0], str):
                 norm_value = value.lower()
                 # Find matching choice
                 for choice in choices:
                     if choice and choice.lower() == norm_value:
                         return choice
                 # If None is a valid choice
                 if None in choices and value is None: 
                     return None
                 
                 self.logger.warning(constants.config.messages.INVALID_CHOICE.format(key=key, value=value, default=default, choices=choices))
                 return default
            elif value not in choices:
                 self.logger.warning(constants.config.messages.INVALID_CHOICE.format(key=key, value=value, default=default, choices=choices))
                 return default

        # 3. Range Check (Min/Max)
        if isinstance(value, (int, float)):
            min_v = rules.get("min")
            max_v = rules.get("max")
            if min_v is not None and value < min_v:
                self.logger.warning(f"{key} {value} is below minimum {min_v}. Resetting to default.")
                return default # Or clamp? Previous logic reset to default or clamped? 
                               # _validate_numeric previously reset to default if out of range.
            if max_v is not None and value > max_v:
                self.logger.warning(f"{key} {value} is above maximum {max_v}. Resetting to default.")
                return default

        # 4. Regex Check
        regex = rules.get("regex")
        if regex and isinstance(value, str):
            if not re.fullmatch(regex, value):
                self.logger.warning(f"Invalid format for {key} ('{value}'). Resetting to default.")
                return default
        
        # 5. List Item Type Check
        item_type = rules.get("item_type")
        if item_type and isinstance(value, list):
            if not all(isinstance(i, item_type) for i in value):
                self.logger.warning(f"Invalid list items for {key}. Resetting to default.")
                return default

        return value

    def _validate_config(self, loaded_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates the configuration using the central VALIDATION_SCHEMA.
        """
        validated = {}
        schema = constants.config.defaults.VALIDATION_SCHEMA
        
        # Iterate over schema to ensure all expected keys are present and valid
        for key, rules in schema.items():
            loaded_value = loaded_config.get(key)
            
            # Use default if key missing
            if key not in loaded_config:
                validated[key] = rules["default"]
                continue
                
            validated[key] = self._validate_value(key, loaded_value, rules)

        # Handle specific cross-field logic (Business Rules)
        # Rule: Low Threshold <= High Threshold
        try:
             low = validated.get("low_speed_threshold", 0)
             high = validated.get("high_speed_threshold", 0)
             if low > high:
                 self.logger.warning(constants.config.messages.THRESHOLD_SWAP)
                 validated["low_speed_threshold"] = high
        except Exception: 
            pass

        # Warn about unknown keys
        extra_keys = set(loaded_config.keys()) - set(schema.keys())
        if extra_keys:
            self.logger.warning("Ignoring unknown config fields: %s", ", ".join(extra_keys))

        return validated


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

        migrated_config = self._migrate_config(config)
        validated_config = self._validate_config(migrated_config)
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
            self.logger.debug("Configuration saved successfully to %s", self.config_path)
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