"""
Configuration management for NetSpeedTray.

This module provides a robust ConfigManager for loading, validating, and saving application
settings to a JSON file. It ensures data integrity through atomic writes, default value
merging, and strict validation, preventing corrupted or invalid configurations from
affecting the application.
"""

import hashlib
import os
import json
import logging
import logging.handlers  # RotatingFileHandler - `logging` alone doesn't import this submodule
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from netspeedtray.utils.helpers import get_app_data_path
from netspeedtray.utils.styles import is_dark_mode
from netspeedtray import constants


# Length of the hex digest kept in a stable pseudonym (e.g. "NIC-1a2b3c4d"): long
# enough that collisions among one machine's handful of adapters/displays are
# implausible, short enough to keep log lines readable.
_PSEUDONYM_HEX_LEN = 8


def stable_pseudonym(name: Optional[str], prefix: str = "NIC") -> str:
    """
    Replace a user-identifying device name with a stable, non-reversible pseudonym.

    Used for network adapter friendly names (users rename them to personal/site
    labels like "Office VPN") and display device names, both of which the support
    bundle's MANIFEST promises never ship verbatim.

    Same input -> same output across runs and processes (sha256, NOT the
    per-process-salted builtin ``hash()``), so repeated log lines still correlate -
    the edge-triggered primary-interface logging (#263) depends on being able to
    tell "changed A -> B" apart from "changed B -> A" without knowing the names.

    ``None`` maps to the string ``"None"`` so ``%s`` call sites keep their shape.
    """
    if name is None:
        return "None"
    digest = hashlib.sha256(str(name).encode("utf-8", "replace")).hexdigest()
    return f"{prefix}-{digest[:_PSEUDONYM_HEX_LEN]}"


class ObfuscatingFormatter(logging.Formatter):
    """
    Logging formatter that redacts sensitive information from log records.

    Redacted patterns:
    - User paths (Windows backslash and forward-slash forms, case-insensitive)
    - IPv4 addresses
    - IPv6 addresses (full, compressed, link-local with zone IDs, IPv4-mapped)
    - Hostname / computer name
    - MAC addresses (colon and dash separated)
    - Windows network interface GUIDs
    - Windows display device names (pseudonymized, not blanked)
    - Network adapter friendly names in KNOWN message shapes (pseudonymized)

    All regexes are pre-compiled at construction time.
    """
    IPV4_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    # --- Message-shape rules (v2.1.5 item 7) --------------------------------
    # Known log lines whose operands are NETWORK ADAPTER FRIENDLY NAMES - free
    # text users rename to personal/site labels ("Office VPN"). We deliberately
    # do NOT enumerate psutil.net_if_addrs() here: names change at runtime, and
    # an adapter literally named "Ethernet" would redact that word everywhere.
    # Instead, each known message shape has its operands replaced with the SAME
    # stable pseudonym the live code logs (see core/controller.py), so lines
    # written by old builds and scrubbed here still correlate with lines the
    # current build writes (#263's edge-triggered logging needs that).
    NIC_CHANGED_REGEX = re.compile(
        r"(Primary network interface changed: )(.+?) -> (.+)$", re.MULTILINE
    )
    NIC_RESOLVED_REGEX = re.compile(
        r"(Determined primary interface: )(.+?)( with IP .*)$", re.MULTILINE
    )
    # v1.1.9-v1.2.6 logged "Found new primary interface: '<name>' (Gateway: <ip>)" at INFO
    # (removed in v1.3.1, commit 66e8b30) - long-lived logs still carry those lines, so the
    # bundle re-scrub must cover the shape (review C4). The gateway suffix stays OUT of the
    # hashed operand so the pseudonym correlates with modern lines for the same adapter; the
    # generic IPv4 rule redacts the gateway afterwards.
    NIC_FOUND_REGEX = re.compile(
        r"(Found new primary interface: )(.+?)(\s*\(Gateway: .*\))?$", re.MULTILINE
    )
    # An operand already safe to keep as-is: a None marker or a pseudonym.
    # Keeping pseudonyms untouched makes the shape rules idempotent, so lines
    # written by the current build are never double-hashed by the bundle scrub.
    _SAFE_OPERAND_REGEX = re.compile(r"^(?:None|[A-Z]+-[0-9a-f]{%d})$" % _PSEUDONYM_HEX_LEN)

    # Windows display device names ('\\.\DISPLAY2'). Unlike adapter names this
    # is an OS-generated shape, so a global pattern IS safe. Pseudonymized (not
    # blanked) so multi-monitor log lines keep telling displays apart.
    DISPLAY_NAME_REGEX = re.compile(r"\\\\\.\\DISPLAY\d+", re.IGNORECASE)

    # IPv6 covering full, compressed (::), and link-local with zone IDs (%5).
    # Boundaries use negative lookarounds for hex chars and colons to avoid
    # partial matches in larger hex strings.
    IPV6_REGEX = re.compile(
        r"(?<![0-9A-Fa-f:])"
        r"(?:"
        r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"                 # full form
        r"|"
        r"(?:[0-9A-Fa-f]{1,4}:){1,7}:"                              # trailing ::
        r"|"
        r":(?::[0-9A-Fa-f]{1,4}){1,7}"                              # leading ::
        r"|"
        r"(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}"     # middle ::
        r"|"
        r"::"                                                       # bare ::
        r")"
        r"(?:%[0-9A-Za-z_-]+)?"                                     # optional zone id
        r"(?![0-9A-Fa-f:])"
    )

    # MAC addresses: 6 groups of 2 hex chars separated by : or -.
    # Anchored by either end so we don't snip the middle of longer hex runs.
    MAC_REGEX = re.compile(r"\b[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}\b")

    # Windows network interface GUIDs: {12345678-1234-1234-1234-123456789012}
    GUID_REGEX = re.compile(
        r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._path_regexes: List[re.Pattern] = []
        self._hostname_regex: Optional[re.Pattern] = None
        self._setup_paths()
        self._setup_hostname()

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
            # Also register the forward-slash form: pathlib.Path.__repr__
            # and many third-party error messages use C:/Users/... on Windows.
            paths_to_obfuscate.add(normalized_path.replace("\\", "/"))
        # Sort longest-first so AppData paths get matched before the user home prefix.
        sorted_paths = sorted(list(paths_to_obfuscate), key=len, reverse=True)
        self._path_regexes = [re.compile(re.escape(p), re.IGNORECASE) for p in sorted_paths]

    def _setup_hostname(self):
        try:
            import socket
            hostname = socket.gethostname()
            if hostname and len(hostname) > 3:
                # Bound by non-word chars so we don't snip a substring of an
                # unrelated identifier.
                self._hostname_regex = re.compile(
                    r"\b" + re.escape(hostname) + r"\b", re.IGNORECASE
                )
        except Exception:
            self._hostname_regex = None

    @classmethod
    def _pseudonymize_operand(cls, operand: str) -> str:
        """One adapter-name operand -> its stable pseudonym.

        Accepts the %r-quoted form historical log lines carry, the bare form,
        ``None``, and already-pseudonymized values (returned unchanged, so
        write-time formatting and the bundle re-scrub never double-hash).
        """
        raw = operand.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        if cls._SAFE_OPERAND_REGEX.match(raw):
            return raw
        return stable_pseudonym(raw, "NIC")

    def _apply_message_shape_rules(self, message: str) -> str:
        """Pseudonymize adapter/display names in the known message shapes."""
        message = self.NIC_CHANGED_REGEX.sub(
            lambda m: f"{m.group(1)}{self._pseudonymize_operand(m.group(2))} -> "
                      f"{self._pseudonymize_operand(m.group(3))}",
            message,
        )
        message = self.NIC_RESOLVED_REGEX.sub(
            lambda m: f"{m.group(1)}{self._pseudonymize_operand(m.group(2))}{m.group(3)}",
            message,
        )
        message = self.NIC_FOUND_REGEX.sub(
            lambda m: f"{m.group(1)}{self._pseudonymize_operand(m.group(2))}{m.group(3) or ''}",
            message,
        )
        message = self.DISPLAY_NAME_REGEX.sub(
            lambda m: stable_pseudonym(m.group(0), "DISPLAY"), message
        )
        return message

    def format(self, record: logging.LogRecord) -> str:
        formatted_message = super().format(record)
        sanitized_message = formatted_message
        # Shape rules FIRST: they hash the ORIGINAL operand, so the pseudonym
        # for a given adapter is identical whether a line is written live or
        # scrubbed from history - the generic rules below must not get a chance
        # to alter the operand before it is hashed.
        sanitized_message = self._apply_message_shape_rules(sanitized_message)
        # Order matters: paths first (most specific), then narrower patterns.
        for pattern in self._path_regexes:
            sanitized_message = pattern.sub("<REDACTED_PATH>", sanitized_message)
        # MAC and GUID before IPv6 - they contain hex/colons that could be
        # partially matched by the IPv6 regex if processed in the wrong order.
        sanitized_message = self.MAC_REGEX.sub("<REDACTED_MAC>", sanitized_message)
        sanitized_message = self.GUID_REGEX.sub("<REDACTED_GUID>", sanitized_message)
        sanitized_message = self.IPV4_REGEX.sub("<REDACTED_IP>", sanitized_message)
        sanitized_message = self.IPV6_REGEX.sub("<REDACTED_IP>", sanitized_message)
        if self._hostname_regex is not None:
            sanitized_message = self._hostname_regex.sub("<REDACTED_HOST>", sanitized_message)
        return sanitized_message


class ConfigError(Exception):
    """
    Custom exception for configuration-related errors.
    
    Raised when:
    - Configuration version strings are invalid or malformed
    - Configuration file I/O operations fail
    - Configuration migration encounters critical issues
    - Configuration data is corrupted or unrecoverable
    
    By raising ConfigError instead of silently failing, we ensure that
    configuration issues are caught early and logged explicitly, preventing
    silent data corruption or incompatibilities.
    """


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
        Handles both field renaming and version-based schema upgrades.
        
        Migration strategy:
        1. Extract the config version (defaults to "1.0" if missing)
        2. Parse the version to learn its direction (never resets on failure)
        3. Apply field migrations (support for renamed/removed fields)
        4. Apply version-based migrations if needed
        5. Stamp config_version forward - never downward (see below)

        Args:
            config: Configuration dictionary to migrate

        Returns:
            Migrated configuration dictionary

        Version handling (v2.1.5 item 4):
        - Unparseable version (4a): keep ALL user values and the version string
          itself; the field-level migrations below are idempotent and
          version-independent, so they still run. DEFAULT_CONFIG.copy() is
          reachable only from load()'s unreadable-file branch, which backs the
          original up as `.corrupt` first.
        - Version parses HIGHER than this build's schema (4b): a rollback from a
          newer build. Leave the version untouched - stamping it down would make
          an already-migrated config look un-migrated and re-run every
          version-gated migration on the next upgrade.
        """
        current_version = constants.config.defaults.CONFIG_SCHEMA_VERSION
        loaded_version = config.get("config_version", "1.0")  # Default to 1.0 for configs predating versioning

        if loaded_version != current_version:
            self.logger.info(f"Migrating config from version {loaded_version} to {current_version}")
        else:
            self.logger.debug(f"Config version {loaded_version} is up to date")

        # Parse the version ONCE to learn its direction. Neither outcome may reset
        # the user's settings (v2.1.5 item 4a).
        version_parses = True
        loaded_is_newer = False
        try:
            loaded_is_newer = self._version_less_than(current_version, loaded_version)
        except ConfigError:
            version_parses = False
            self.logger.warning(
                "Unparseable config_version %r (written by a pre-release build?): "
                "keeping all user settings; running field-level migrations only.",
                loaded_version,
            )

        # Field renaming / removal (legacy migrations)
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

        # keep_data: the retention ladder values changed between versions (the old 1/7/14/30-day options
        # were replaced by 31/90/180/...). Snap any legacy value not on the CURRENT ladder to the nearest
        # current value, so an old short-retention choice maps to the new minimum (31d) instead of being
        # reset by validation to the 1-year default - which silently expanded a privacy-conscious user's
        # 7-day retention to a full year (#3). Runs before _validate_config (see load()).
        try:
            valid_days = list(constants.data.retention.DAYS_MAP.values())
            kd = migrated.get("keep_data")
            if isinstance(kd, (int, float)) and not isinstance(kd, bool) and int(kd) not in valid_days:
                nearest = min(valid_days, key=lambda v: abs(v - kd))
                self.logger.info("Migrated keep_data %s -> %s (nearest on the current retention ladder)", kd, nearest)
                migrated["keep_data"] = nearest
                changes_made = True
        except Exception as e:
            self.logger.debug("keep_data migration skipped: %s", e)

        # Version-based migrations (applied if loaded_version < target version)
        # Example structure for future versions:
        # if self._version_less_than(loaded_version, "2.0"):
        #     migrated = self._migrate_to_v2_0(migrated)
        #     changes_made = True
        
        # Update config_version to current - but NEVER stamp it DOWNWARD or over an
        # unparseable value (v2.1.5 items 4a/4b, see docstring).
        if version_parses and not loaded_is_newer:
            if migrated.get("config_version") != current_version:
                migrated["config_version"] = current_version
                if not changes_made:
                    self.logger.info(f"Updated config_version from {loaded_version} to {current_version}")
                else:
                    self.logger.info(f"Updated config_version from {loaded_version} to {current_version} (with other migrations)")
                changes_made = True
        elif loaded_is_newer:
            self.logger.info(
                "Config version %s is newer than this build's schema %s (rollback?): leaving it untouched.",
                loaded_version, current_version,
            )

        if changes_made and loaded_version != current_version:
            self.logger.info(f"Config migration completed. User should be notified of any breaking changes.")

        return migrated
    
    def _version_less_than(self, version_a: str, version_b: str) -> bool:
        """
        Compare two semantic versions. Returns True if version_a < version_b.
        
        Args:
            version_a: Version string (e.g., "1.0", "2.1")
            version_b: Version string to compare against
        
        Returns:
            True if version_a < version_b, False otherwise
        
        Raises:
            ConfigError: If either version string is invalid or malformed.
                Prevents silent failures that could skip critical migrations.
        
        Examples:
            _version_less_than("1.0", "2.0") → True
            _version_less_than("2.0", "1.0") → False
            _version_less_than("invalid", "1.0") → raises ConfigError
        """
        try:
            parts_a = tuple(map(int, version_a.split(".")))
            parts_b = tuple(map(int, version_b.split(".")))
            return parts_a < parts_b
        except (ValueError, AttributeError) as e:
            error_msg = f"Invalid version format: version_a={version_a}, version_b={version_b}"
            self.logger.error(error_msg)
            raise ConfigError(error_msg) from e


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
            # Use the root logger to catch all logs in the process (including netspeedtray and NetSpeedTray)
            logger = logging.getLogger()
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

            # Create and configure the console handler. Uses ObfuscatingFormatter
            # too so dev-mode console output never leaks paths/IPs accidentally
            # pasted into bug reports or screenshots.
            console_handler = logging.StreamHandler()
            console_handler.setLevel(constants.logs.CONSOLE_LOG_LEVEL)
            console_formatter = ObfuscatingFormatter('%(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)

            logger.debug("Logging initialized successfully.")
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
        # Rule: high_speed_threshold must be strictly greater than low_speed_threshold.
        # Per-field schema validation already enforces each field's range, so we only
        # need to check the invariant between them. If invalid (swapped, equal, or
        # non-numeric), reset both to defaults so color coding works on every load.
        try:
            low = validated.get("low_speed_threshold")
            high = validated.get("high_speed_threshold")
            low_f = float(low) if low is not None else None
            high_f = float(high) if high is not None else None
            if low_f is None or high_f is None or not (low_f < high_f):
                self.logger.warning(
                    "Invalid speed thresholds (high=%s, low=%s): resetting both to defaults.",
                    high, low,
                )
                validated["high_speed_threshold"] = constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD
                validated["low_speed_threshold"] = constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD
        except (TypeError, ValueError) as exc:
            self.logger.warning("Failed to evaluate threshold pair: %s. Resetting to defaults.", exc)
            validated["high_speed_threshold"] = constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD
            validated["low_speed_threshold"] = constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD

        # Preserve unknown keys VERBATIM (v2.1.5 item 4c). They are usually settings
        # written by a NEWER build; a rollback must not strip them from disk. They
        # ride through save() too: save() drops None values and compares against
        # `_last_config` for its no-op shortcut, and both sides of that comparison
        # carry the same preserved keys, so the shortcut still fires.
        extra_keys = set(loaded_config.keys()) - set(schema.keys())
        if extra_keys:
            self.logger.warning(
                "Preserving unknown config fields (written by a newer version?): %s",
                ", ".join(sorted(extra_keys)),
            )
            for key in extra_keys:
                validated[key] = loaded_config[key]

        return validated


    # `config.json.bak.v<version>` backups: keep filesystem-safe characters only,
    # and cap the suffix length so a corrupted version string cannot produce an
    # unusable filename.
    _BACKUP_VERSION_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
    _BACKUP_VERSION_MAX_LEN = 32

    def _backup_config_if_version_differs(self, config: Any) -> None:
        """
        Copy the on-disk config to `<name>.bak.v<loaded_version>` when the loaded
        config_version differs from the current schema version (v2.1.5 item 4).

        Best-effort: a failed backup is logged and never blocks loading. The FIRST
        copy per source version is kept and later loads leave it untouched (review
        C2): a rolled-back build re-loads a differing version on every launch, and
        by launch 2 the on-disk config is already the old-schema-mangled one - the
        pristine copy is the whole point of the backup.
        """
        try:
            if not isinstance(config, dict):
                return
            loaded_version = str(config.get("config_version", "1.0"))
            if loaded_version == constants.config.defaults.CONFIG_SCHEMA_VERSION:
                return
            safe_version = self._BACKUP_VERSION_SAFE_CHARS.sub("_", loaded_version)
            safe_version = safe_version[: self._BACKUP_VERSION_MAX_LEN] or "unknown"
            backup_path = self.config_path.with_name(f"{self.config_path.name}.bak.v{safe_version}")
            if backup_path.exists():
                self.logger.debug("Config backup %s already exists; keeping the pristine copy.",
                                  backup_path.name)
                return
            shutil.copy2(self.config_path, backup_path)
            self.logger.info(
                "Backed up config written by schema version %s to %s before migrating.",
                loaded_version, backup_path.name,
            )
        except Exception:
            self.logger.exception("Failed to back up config before migration; continuing with load.")

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

        # Keep a pristine pre-migration copy whenever the file on disk was written
        # by a DIFFERENT schema version (older, newer after a rollback, or
        # unparseable). This is the only config backup that exists anywhere.
        self._backup_config_if_version_differs(config)

        migrated_config = self._migrate_config(config)
        validated_config = self._validate_config(migrated_config)
        self._last_config = validated_config.copy()
        return validated_config


    def save(self, config: Dict[str, Any]) -> None:
        """Atomically saves the provided configuration to the file."""
        validated_config = self._validate_config(config)
        
        # For SCHEMA-KNOWN keys, None means "use the default" and absence is equivalent - they
        # are filtered so defaults never pin themselves to disk. An UNKNOWN key (a newer build's,
        # preserved by item 4c) must ride through even when null: for a schema we do not know,
        # null and absent may mean different things (review L4).
        known_keys = set(constants.config.defaults.VALIDATION_SCHEMA)
        config_to_save = { key: value for key, value in validated_config.items()
                           if value is not None or key not in known_keys }
        last_config_to_compare = ({ k: v for k, v in self._last_config.items()
                                    if v is not None or k not in known_keys }
                                  if self._last_config else None)

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