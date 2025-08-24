"""
Helper utilities for NetSpeedTray.

This module provides foundational functions for directory management, logging setup,
and speed formatting used across the application.
"""

import os
import sys
import logging
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple
from pathlib import Path

from netspeedtray import constants

# Thread lock for logging setup
_logging_lock: threading.Lock = threading.Lock()


def get_app_asset_path(asset_name: str) -> Path:
    """
    Get the path to an application asset in the assets directory.
    This function is robust for both development and PyInstaller-packaged modes.
    """
    # Check if the application is running in a PyInstaller bundle
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # In a bundle, assets are located in the _MEIPASS temporary directory
        base_path = Path(sys._MEIPASS)
    else:
        current_path = Path(__file__).resolve()
        project_root = current_path
        while project_root.name != 'src':
            project_root = project_root.parent
            if project_root == project_root.parent: # Reached the filesystem root
                raise FileNotFoundError("Could not find the 'src' directory to determine project root.")
        base_path = project_root.parent # The project root is one level above 'src'

    return base_path / "assets" / asset_name

def get_app_data_path() -> Path:
    """
    Retrieve the application data directory path on Windows.
    """
    logger: logging.Logger = logging.getLogger(__name__) # Use __name__ for module-specific logger
    appdata: Optional[str] = os.getenv("APPDATA")
    if not appdata:
        appdata = os.path.expanduser("~")
        logger.warning("APPDATA environment variable not set, using home directory: %s", appdata)
    path: Path = Path(appdata) / constants.app.APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
        # Test writability more robustly
        test_file_name = f".nst_write_test_{os.getpid()}" # Unique name to avoid race conditions
        test_file = path / test_file_name
        with open(test_file, 'w') as f:
            f.write("test")
        test_file.unlink()
        logger.debug("App data path ensured and writable: %s", path)
        return path
    except PermissionError as e:
        logger.error("Permission denied creating/writing to app data directory %s: %s", path, e)
        raise PermissionError(f"Cannot access app data directory: {path}. Please check permissions.") from e
    except OSError as e:
        logger.error("Failed to create or verify app data directory %s: %s", path, e)
        raise OSError(f"Error with app data directory: {path}. Check disk space or path validity.") from e


def setup_logging() -> logging.Logger:
    """
    Configure logging with both a rotating file handler and a console handler in a thread-safe manner.
    """
    logger: logging.Logger = logging.getLogger(constants.app.APP_NAME) # Use app name consistently for logger
    with _logging_lock:
        if not logger.handlers: # Check if handlers are already configured
            is_production = os.environ.get(constants.app.ENV_VAR_PROD_MODE, "").lower() == "true" # More robust check
            
            # Determine root log level
            root_log_level = constants.logs.PRODUCTION_LOG_LEVEL if is_production else logging.DEBUG
            logger.setLevel(root_log_level)
            
            log_formatter = logging.Formatter(
                fmt=constants.logs.LOG_FORMAT, datefmt=constants.logs.LOG_DATE_FORMAT
            )

            # File Handler
            try:
                log_file_path: Path = get_app_data_path() / constants.logs.ERROR_LOG_FILENAME
                file_handler: RotatingFileHandler = RotatingFileHandler(
                    log_file_path,
                    maxBytes=constants.logs.MAX_LOG_SIZE,
                    backupCount=constants.logs.LOG_BACKUP_COUNT,
                    encoding='utf-8',
                    delay=True # Delays opening the file until the first log message
                )
                file_handler.setFormatter(log_formatter)
                file_log_level = constants.logs.PRODUCTION_LOG_LEVEL if is_production else constants.logs.FILE_LOG_LEVEL
                file_handler.setLevel(file_log_level)
                logger.addHandler(file_handler)
    
            except (PermissionError, OSError, FileNotFoundError) as e:
                print(f"CRITICAL: Failed to set up file logging at {get_app_data_path() / constants.logs.ERROR_LOG_FILENAME}: {e}. File logging will be disabled.", file=sys.stderr)
                # We don't raise here, allow console logging to proceed

            # Console Handler
            console_handler: logging.StreamHandler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(log_formatter)
            console_log_level = constants.logs.PRODUCTION_LOG_LEVEL if is_production else constants.logs.CONSOLE_LOG_LEVEL
            console_handler.setLevel(console_log_level)
            logger.addHandler(console_handler)
            
            # Now that console handler is up, we can log startup messages
            if any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
                 logger.info("File logging target: %s, Level: %s", get_app_data_path() / constants.logs.ERROR_LOG_FILENAME, logging.getLevelName(file_log_level))
            else:
                 logger.warning("File logging is NOT active due to previous errors.")
            logger.info("Console logging active. Level: %s", logging.getLevelName(console_handler.level))
            logger.info("Application logging initialized. Production mode: %s. Root Log Level: %s.", is_production, logging.getLevelName(root_log_level))

    return logger


def format_speed(speed: float, use_megabytes: bool, *, always_mbps: bool = False, decimal_places: int = 1) -> str:
    """
    Format a speed value (in bytes/sec) into a human-readable string with dynamic units.

    Speeds below `constants.network.units.MINIMUM_DISPLAY_SPEED` bytes/sec are displayed as '0 KBps' or '0 Kbps'.
    For higher speeds, the function selects the appropriate unit (Bps/KBps/MBps/GBps or bps/Kbps/Mbps/Gbps)
    based on the magnitude and the `use_megabytes` flag.
    """
    
    if not isinstance(speed, (int, float)):
        raise TypeError(f"Speed must be a number (int or float), got {type(speed)}")

    # Ensure speed is non-negative
    current_speed = max(0.0, float(speed))

    # Handle speeds below the minimum display threshold
    if current_speed < constants.network.units.MINIMUM_DISPLAY_SPEED:
        # Always show 0 Mbps/MBps if always_mbps is set
        if always_mbps:
            if use_megabytes:
                return f"0 {constants.network.units.MBPS_LABEL}"
            else:
                return f"0 {constants.network.units.MBITS_LABEL}"
        return f"0 {constants.network.units.KBPS_LABEL}" if use_megabytes else f"0 {constants.network.units.KBITS_LABEL}"

    val: float
    unit: str

    if always_mbps:
        if use_megabytes:
            val = current_speed / constants.network.units.MEGA_DIVISOR
            unit = constants.network.units.MBPS_LABEL
        else:
            val = current_speed * constants.network.units.BITS_PER_BYTE / 1_000_000
            unit = constants.network.units.MBITS_LABEL
    else:
        if use_megabytes: # Byte-based units (B/s, KB/s, MB/s, GB/s)
            if current_speed >= constants.network.units.GIGA_DIVISOR:
                val = current_speed / constants.network.units.GIGA_DIVISOR
                unit = constants.network.units.GBPS_LABEL
            elif current_speed >= constants.network.units.MEGA_DIVISOR:
                val = current_speed / constants.network.units.MEGA_DIVISOR
                unit = constants.network.units.MBPS_LABEL
            elif current_speed >= constants.network.units.KILO_DIVISOR:
                val = current_speed / constants.network.units.KILO_DIVISOR
                unit = constants.network.units.KBPS_LABEL
            else:
                val = current_speed
                unit = constants.network.units.BPS_LABEL
        else: # Bit-based units (bps, Kbps, Mbps, Gbps)
            speed_bits = current_speed * constants.network.units.BITS_PER_BYTE
            KILO_BITS_DIVISOR = 1000.0
            MEGA_BITS_DIVISOR = 1000.0**2
            GIGA_BITS_DIVISOR = 1000.0**3
            if speed_bits >= GIGA_BITS_DIVISOR:
                val = speed_bits / GIGA_BITS_DIVISOR
                unit = constants.network.units.GBITS_LABEL
            elif speed_bits >= MEGA_BITS_DIVISOR:
                val = speed_bits / MEGA_BITS_DIVISOR
                unit = constants.network.units.MBITS_LABEL
            elif speed_bits >= KILO_BITS_DIVISOR:
                val = speed_bits / KILO_BITS_DIVISOR
                unit = constants.network.units.KBITS_LABEL
            else:
                val = speed_bits
                unit = constants.network.units.BITS_LABEL

    # Formatting the output value
    # For base units (B/s, bps), no decimal. For others, use decimal_places.
    if unit in (constants.network.units.BPS_LABEL, constants.network.units.BITS_LABEL):
        return f"{val:.0f} {unit}"
    else:
        fmt = f"{{val:.{decimal_places}f}} {{unit}}"
        return fmt.format(val=val, unit=unit)

def format_data_size(data_bytes: int | float, precision: int = 2) -> Tuple[float, str]:
    """
    Formats a byte count into a human-readable string with units (B, KB, MB, GB, etc.).

    Uses base 1024 for units (KiB, MiB, etc. conceptually, though labels are KB, MB).
    """
    logger_instance: logging.Logger = logging.getLogger(__name__) # Or constants.app.APP_NAME

    if not isinstance(data_bytes, (int, float)):
        # logger_instance.error("Invalid type for data_bytes: %s. Must be int or float.", type(data_bytes))
        raise TypeError(f"Data_bytes must be a number (int or float), got {type(data_bytes)}")

    if data_bytes < 0:
        # logger_instance.warning("Negative data_bytes received: %s. Treating as 0.", data_bytes)
        data_bytes = 0.0 # Or raise ValueError if negative is strictly an error

    if data_bytes == 0:
        return 0.0, "B"

    # Units for data size (powers of 1024)
    # Consider if constants.network.units should provide these as well for consistency
    UNITS_DATA_SIZE = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
    BASE_DATA_SIZE = 1024.0

    unit_index = 0
    value = float(data_bytes)

    while value >= BASE_DATA_SIZE and unit_index < len(UNITS_DATA_SIZE) - 1:
        value /= BASE_DATA_SIZE
        unit_index += 1
    
    try:
        formatted_value = round(value, precision)
    except TypeError: # Should be caught by initial type check, but as a safeguard
        logger_instance.error("TypeError during rounding in format_data_size. Value: %s, Precision: %s", value, precision, exc_info=True)
        # Fallback: return unrounded value with correct unit, or a default
        return round(value, 0), UNITS_DATA_SIZE[unit_index] 
        
    return formatted_value, UNITS_DATA_SIZE[unit_index]