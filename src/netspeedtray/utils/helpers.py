"""
Helper utilities for NetSpeedTray.

This module provides foundational functions for directory management, logging setup,
and data formatting used across the application.
"""

import os
import sys
import logging
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple, List
from pathlib import Path
from datetime import datetime

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
            # Assuming you have an ENV_VAR_PROD_MODE in constants.app
            is_production = os.environ.get(getattr(constants.app, 'ENV_VAR_PROD_MODE', 'NETSPEEDTRAY_PROD'), "").lower() == "true"
            
            # Determine root log level
            root_log_level = constants.logs.PRODUCTION_LOG_LEVEL if is_production else logging.DEBUG
            logger.setLevel(root_log_level)
            
            log_formatter = logging.Formatter(
                fmt=constants.logs.LOG_FORMAT, datefmt=constants.logs.LOG_DATE_FORMAT
            )

            # File Handler
            try:
                # Assuming ERROR_LOG_FILENAME is defined in constants.logs
                log_filename = getattr(constants.logs, 'ERROR_LOG_FILENAME', constants.logs.LOG_FILENAME)
                log_file_path: Path = get_app_data_path() / log_filename
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
                log_filename = getattr(constants.logs, 'ERROR_LOG_FILENAME', constants.logs.LOG_FILENAME)
                print(f"CRITICAL: Failed to set up file logging at {get_app_data_path() / log_filename}: {e}. File logging will be disabled.", file=sys.stderr)

            # Console Handler
            console_handler: logging.StreamHandler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(log_formatter)
            console_log_level = constants.logs.PRODUCTION_LOG_LEVEL if is_production else constants.logs.CONSOLE_LOG_LEVEL
            console_handler.setLevel(console_log_level)
            logger.addHandler(console_handler)
            
            if any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
                 log_filename = getattr(constants.logs, 'ERROR_LOG_FILENAME', constants.logs.LOG_FILENAME)
                 logger.info("File logging target: %s, Level: %s", get_app_data_path() / log_filename, logging.getLevelName(file_log_level))
            else:
                 logger.warning("File logging is NOT active due to previous errors.")
            logger.info("Console logging active. Level: %s", logging.getLevelName(console_handler.level))
            logger.info("Application logging initialized. Production mode: %s. Root Log Level: %s.", is_production, logging.getLevelName(root_log_level))

    return logger


def format_speed(speed: float, i18n, use_megabytes: bool, *, always_mbps: bool = False, decimal_places: int = 1) -> str:
    """
    Format a speed value (in bytes/sec) into a human-readable string with dynamic units.

    Speeds below `constants.network.units.MINIMUM_DISPLAY_SPEED` bytes/sec are displayed as '0'.
    The number of decimal places for the output is controlled by `decimal_places`.
    """
    if not isinstance(speed, (int, float)):
        raise TypeError(f"Speed must be a number (int or float), got {type(speed)}")

    current_speed = max(0.0, float(speed))
    val: float
    unit: str

    if current_speed < constants.network.units.MINIMUM_DISPLAY_SPEED:
        if use_megabytes:
            unit_key = constants.network.units.MBPS_LABEL if always_mbps else constants.network.units.KBPS_LABEL
        else:
            unit_key = constants.network.units.MBITS_LABEL if always_mbps else constants.network.units.KBITS_LABEL
        unit = getattr(i18n, unit_key)
        return f"{0.0:.{decimal_places}f} {unit}"

    if always_mbps:
        if use_megabytes:
            val = current_speed / constants.network.units.MEGA_DIVISOR
            unit = getattr(i18n, constants.network.units.MBPS_LABEL)
        else:
            val = current_speed * constants.network.units.BITS_PER_BYTE / 1_000_000
            unit = getattr(i18n, constants.network.units.MBITS_LABEL)
    else:
        if use_megabytes:  # Byte-based units (B/s, KB/s, MB/s, GB/s)
            if current_speed >= constants.network.units.GIGA_DIVISOR:
                val = current_speed / constants.network.units.GIGA_DIVISOR
                unit = getattr(i18n, constants.network.units.GBPS_LABEL)
            elif current_speed >= constants.network.units.MEGA_DIVISOR:
                val = current_speed / constants.network.units.MEGA_DIVISOR
                unit = getattr(i18n, constants.network.units.MBPS_LABEL)
            elif current_speed >= constants.network.units.KILO_DIVISOR:
                val = current_speed / constants.network.units.KILO_DIVISOR
                unit = getattr(i18n, constants.network.units.KBPS_LABEL)
            else:
                val = current_speed
                unit = getattr(i18n, constants.network.units.BPS_LABEL)
        else:  # Bit-based units (bps, Kbps, Mbps, Gbps)
            speed_bits = current_speed * constants.network.units.BITS_PER_BYTE
            KILO_BITS_DIVISOR = 1000.0
            MEGA_BITS_DIVISOR = 1000.0**2
            GIGA_BITS_DIVISOR = 1000.0**3
            if speed_bits >= GIGA_BITS_DIVISOR:
                val = speed_bits / GIGA_BITS_DIVISOR
                unit = getattr(i18n, constants.network.units.GBITS_LABEL)
            elif speed_bits >= MEGA_BITS_DIVISOR:
                val = speed_bits / MEGA_BITS_DIVISOR
                unit = getattr(i18n, constants.network.units.MBITS_LABEL)
            elif speed_bits >= KILO_BITS_DIVISOR:
                val = speed_bits / KILO_BITS_DIVISOR
                unit = getattr(i18n, constants.network.units.KBITS_LABEL)
            else:
                val = speed_bits
                unit = getattr(i18n, constants.network.units.BITS_LABEL)

    # For base units (B/s, bps), always use 0 decimal places regardless of setting.
    if unit in (getattr(i18n, constants.network.units.BPS_LABEL), getattr(i18n, constants.network.units.BITS_LABEL)):
        return f"{val:.0f} {unit}"
    else:
        # The f-string handles all rounding and padding based on decimal_places.
        return f"{val:.{decimal_places}f} {unit}"

def format_data_size(data_bytes: int | float, i18n, precision: int = 2) -> Tuple[float, str]:
    """
    Formats a byte count into a human-readable string with units (B, KB, MB, GB, etc.).

    Uses base 1024 for units (KiB, MiB, etc. conceptually, though labels are KB, MB).
    """
    logger_instance: logging.Logger = logging.getLogger(__name__)

    if not isinstance(data_bytes, (int, float)):
        raise TypeError(f"Data_bytes must be a number (int or float), got {type(data_bytes)}")

    if data_bytes < 0:
        data_bytes = 0.0

    # Get translated units from the i18n object
    UNITS_DATA_SIZE = [
        i18n.BYTES_UNIT, i18n.KB_UNIT, i18n.MB_UNIT, i18n.GB_UNIT,
        i18n.TB_UNIT, i18n.PB_UNIT
    ]

    if data_bytes == 0:
        return 0.0, UNITS_DATA_SIZE[0] # Return "B" or its translation

    BASE_DATA_SIZE = 1024.0

    unit_index = 0
    value = float(data_bytes)

    while value >= BASE_DATA_SIZE and unit_index < len(UNITS_DATA_SIZE) - 1:
        value /= BASE_DATA_SIZE
        unit_index += 1
    
    try:
        formatted_value = round(value, precision)
    except TypeError:
        logger_instance.error("TypeError during rounding in format_data_size. Value: %s, Precision: %s", value, precision, exc_info=True)
        return round(value, 0), UNITS_DATA_SIZE[unit_index] 
        
    return formatted_value, UNITS_DATA_SIZE[unit_index]

# --- Data Processing Utilities ---

def downsample_data(data: List[Tuple[datetime, float, float]], max_points: int) -> List[Tuple[datetime, float, float]]:
    """
    Reduces the number of data points to a maximum limit for efficient plotting.

    This function uses a binning method that preserves the most significant data point
    (the one with the highest combined upload/download speed) within each bin. This
    ensures that visual spikes in network activity are not lost during downsampling.

    Args:
        data: The input data as a list of (timestamp, upload, download) tuples.
        max_points: The maximum number of points the output should contain.

    Returns:
        A downsampled list of data points, or the original list if it's already
        within the max_points limit.
    """
    if len(data) <= max_points:
        return data

    downsampled = []
    num_points = len(data)
    # Ensure bin_size is at least 1, even if max_points is > num_points (should not happen with the guard clause)
    bin_size = max(1.0, num_points / max_points)

    for i in range(max_points):
        start_index = int(i * bin_size)
        end_index = int((i + 1) * bin_size)
        if start_index >= num_points:
            break
        
        bin_data = data[start_index:end_index]
        if not bin_data:
            continue

        # Find the point with the highest combined speed in the bin to preserve spikes
        peak_point = max(bin_data, key=lambda point: point[1] + point[2])
        downsampled.append(peak_point)

    return downsampled