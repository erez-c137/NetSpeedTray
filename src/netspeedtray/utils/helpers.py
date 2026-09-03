"""
Helper utilities for NetSpeedTray.

This module provides foundational functions for directory management, logging setup,
and data formatting used across the application.
"""

import math
import os
import sys
import logging
from typing import Optional, Tuple, List
from pathlib import Path
from datetime import datetime

# numpy is imported lazily inside calculate_monotone_cubic_interpolation -
# loading it here adds ~20 MB RSS to every NST process, even when the
# widget's mini-graph (the only consumer of curve interpolation) is off.

from netspeedtray import constants


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

_app_data_path_cache: Optional[Path] = None


def get_app_data_path() -> Path:
    """
    Retrieve the application data directory path on Windows. Memoized: APPDATA is stable for the life
    of a process and this is called from many constructors, so the mkdir + write-and-delete writability
    probe only runs once instead of on every call.
    """
    global _app_data_path_cache
    if _app_data_path_cache is not None:
        return _app_data_path_cache
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
        _app_data_path_cache = path
        return path
    except PermissionError as e:
        logger.error("Permission denied creating/writing to app data directory %s: %s", path, e)
        raise PermissionError(f"Cannot access app data directory: {path}. Please check permissions.") from e
    except OSError as e:
        logger.error("Failed to create or verify app data directory %s: %s", path, e)
        raise OSError(f"Error with app data directory: {path}. Check disk space or path validity.") from e


# An integrated GPU has no dedicated video memory, and the PDH counter dutifully reports 0.0 GB for
# it. Rendering that as "0.0G" spends widget width to tell the user nothing - worse, it reads as a
# measurement rather than an absence. Treat a reading this small as "there is nothing here".
VRAM_READING_FLOOR_GB: float = 0.05


def has_dedicated_vram(used: Optional[float]) -> bool:
    """True when a GPU reports dedicated video memory worth displaying.

    Kept in one place because three call sites need the same answer and had drifted: the Monitor's
    Overview tile already hid itself on an iGPU, while the widget and the Hardware telemetry strip
    both showed a flat "0.0 GB".
    """
    try:
        return used is not None and float(used) >= VRAM_READING_FLOOR_GB
    except (TypeError, ValueError):
        return False


def is_portable_install() -> bool:
    """
    True when the app is running as the portable ZIP build.

    The portable ZIP ships a ``portable.marker`` file next to the executable (added at package time -
    see build.bat); the Inno installer build never contains it. A non-frozen (development) run is not
    "portable". Used by the updater to pick the guided folder-copy update flow instead of launching the
    installer, which can't update a portable folder in place (#195).
    """
    import sys
    if not getattr(sys, "frozen", False):
        return False
    try:
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.isfile(os.path.join(app_dir, constants.app.PORTABLE_MARKER_FILENAME))
    except Exception:
        return False


def get_machine_id() -> str:
    """
    A stable per-install identifier for exported stats (so an MSP can tell two machines' CSVs apart).
    A random UUID generated once and cached at %APPDATA%/NetSpeedTray/machine_id - NOT a hardware
    fingerprint (no MAC/serial), so it identifies the install, not the person. Falls back to a
    volatile UUID if the file can't be written (export still works, just not stable across runs).
    """
    import uuid
    logger = logging.getLogger(__name__)
    try:
        path = get_app_data_path() / "machine_id"
        if path.exists():
            mid = path.read_text(encoding="utf-8").strip()
            if mid:
                return mid
        mid = uuid.uuid4().hex
        path.write_text(mid, encoding="utf-8")
        return mid
    except (OSError, PermissionError) as e:
        logger.warning("Could not persist machine_id (%s); using a volatile id this run.", e)
        return uuid.uuid4().hex


def get_unit_labels_for_type(i18n, unit_type: str, short_labels: bool = False) -> List[str]:
    """
    Returns a list of translated unit labels [base, kilo, mega, giga] for the given unit type.
    """
    units = constants.network.units
    is_binary = unit_type.endswith("_binary")
    is_bytes = unit_type.startswith("bytes")
    
    if is_bytes:
        if is_binary:
            keys = (units.BIBPS_SHORT_LABEL, units.KIBPS_SHORT_LABEL, units.MIBPS_SHORT_LABEL, units.GIBPS_SHORT_LABEL) if short_labels else \
                   (units.BIBPS_LABEL, units.KIBPS_LABEL, units.MIBPS_LABEL, units.GIBPS_LABEL)
        else:
            keys = (units.BPS_SHORT_LABEL, units.KBPS_SHORT_LABEL, units.MBPS_SHORT_LABEL, units.GBPS_SHORT_LABEL) if short_labels else \
                   (units.BPS_LABEL, units.KBPS_LABEL, units.MBPS_LABEL, units.GBPS_LABEL)
    else:  # bits
        if is_binary:
            keys = (units.BITS_SHORT_LABEL, units.KIBITS_SHORT_LABEL, units.MIBITS_SHORT_LABEL, units.GIBITS_SHORT_LABEL) if short_labels else \
                   (units.BITS_LABEL, units.KIBITS_LABEL, units.MIBITS_LABEL, units.GIBITS_LABEL)
        else:
            keys = (units.BITS_SHORT_LABEL, units.KBITS_SHORT_LABEL, units.MBITS_SHORT_LABEL, units.GBITS_SHORT_LABEL) if short_labels else \
                   (units.BITS_LABEL, units.KBITS_LABEL, units.MBITS_LABEL, units.GBITS_LABEL)
    
    return [getattr(i18n, key) for key in keys]


from typing import Optional, Tuple, List

def get_all_possible_unit_labels(i18n, short_labels: Optional[bool] = None) -> List[str]:
    """
    Returns all unique translated unit labels across all unit types and formats.
    Used for calculating reference widths in the UI.
    """
    all_labels = set()
    for ut in ["bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary"]:
        if short_labels is None:
            all_labels.update(get_unit_labels_for_type(i18n, ut, False))
            all_labels.update(get_unit_labels_for_type(i18n, ut, True))
        else:
            all_labels.update(get_unit_labels_for_type(i18n, ut, short_labels))
    return sorted(list(all_labels))


def get_reference_value_string(force_mega_unit: bool, decimal_places: int, unit_type: str = "bits_decimal", min_digits: int = 3) -> str:
    """
    Returns a reference number string (e.g., '888.8' or '8888.88') used to 
    calculate the maximum width needed for speed values in the UI.
    """
    # Base integer part depends on min_digits requested
    integer_part = "8" * min_digits

    # In always_mbps mode the value is shown in the mega unit, which reaches four
    # integer digits at multi-gig speeds (e.g. 1250 MB/s or ~1192 MiB/s at 10GbE,
    # 10000 Mbps). Reserve 4 digits for BOTH bits and bytes so the widget is sized
    # wide enough and the renderer doesn't clip the text (issue #106). `is_bytes` is
    # intentionally not excluded here - that exclusion was the truncation bug.
    if min_digits < 4 and force_mega_unit:
        integer_part = "8888"

    if decimal_places > 0:
        return f"{integer_part}.{'8' * decimal_places}"
    return integer_part


# Adaptive decimal floor for force-mega display (2.1.5 item 11c). At the shipped defaults
# (always_mbps, decimal_places=1) any traffic under ~6.25 KB/s rounds to "0.0 Mbps" - Discord
# idle at 3,000 B/s read as dead air. When the forced-mega value rounds to zero at the
# configured precision but the TRUE value is at least this fraction of the mega unit
# (0.001 Mbps = 1 kbps in bits_decimal), extend the decimals just enough to show the first
# significant digit. Below the floor, plain zero at the configured precision is honest.
FORCE_MEGA_ZERO_FLOOR: float = 0.001
# Cap on the borrowed decimals ("0.003" at most): further digits report noise, not traffic.
FORCE_MEGA_MAX_ADAPTIVE_DECIMALS: int = 3


def format_speed(
    speed: float,
    i18n,
    use_megabytes: bool = False,  # Deprecated
    *,
    force_mega_unit: bool = False,
    decimal_places: int = 1,
    unit_type: str = "bits_decimal",
    fixed_width: bool = False,
    short_labels: bool = False,
    split_unit: bool = False
) -> str | Tuple[str, str]:
    """
    Format a speed value (in bytes/sec) into human-readable components.
    
    Returns:
        If split_unit is True: Tuple[str, str] (formatted_value, unit)
        If split_unit is False: str "formatted_value unit"
    """
    if not isinstance(speed, (int, float)):
        raise TypeError(f"Speed must be a number (int or float), got {type(speed)}")

    # Self-clamp decimal_places: production config already constrains it, but this is a public
    # helper and a negative value would make the f-string ".{-1}f" raise ValueError.
    decimal_places = max(0, min(2, int(decimal_places)))

    current_speed = max(0.0, float(speed))
    if not math.isfinite(current_speed):
        current_speed = 0.0   # reject NaN/inf so we never render the literal "inf"/"nan" (#14)
    val: float
    unit: str
    network_consts = constants.network.units

    # Select divisors based on binary vs decimal
    is_binary = unit_type.endswith("_binary")
    is_bytes = unit_type.startswith("bytes")
    
    if is_binary:
        kilo_div = network_consts.KIBI_DIVISOR
        mega_div = network_consts.MEBI_DIVISOR
        giga_div = network_consts.GIBI_DIVISOR
    else:
        kilo_div = network_consts.KILO_DIVISOR
        mega_div = network_consts.MEGA_DIVISOR
        giga_div = network_consts.GIGA_DIVISOR
    
    # Get translated labels [base, kilo, mega, giga]
    labels = get_unit_labels_for_type(i18n, unit_type, short_labels)
    
    # Select numeric value based on bytes vs bits
    speed_value = current_speed if is_bytes else current_speed * network_consts.BITS_PER_BYTE

    # Determine scale and unit. The unit is chosen from the value as it will be DISPLAYED
    # (i.e. after rounding), not from the raw value: 999.95..999.999 in a unit used to round
    # up to "1000.0" and render four integer digits in a slot sized for three (2.1.5 item 11a).
    # The tier-to-tier ratio is uniform within a family: 1000 decimal, 1024 binary.
    step_ratio = float(kilo_div)
    divisors = (1.0, float(kilo_div), float(mega_div), float(giga_div))
    tier: int

    if current_speed < network_consts.MINIMUM_DISPLAY_SPEED:
        tier = 2 if force_mega_unit else 1
        val = 0.0
        unit = labels[tier]
    elif force_mega_unit:
        tier = 2
        val = speed_value / mega_div
        # Owner decision D4 (no widget widening): once the ROUNDED mega value reaches the
        # mega->giga ratio, promote to the giga unit ("10000.0 Mbps" -> "10.0 Gbps", which
        # fits the existing '8888.8' reservation). Applies to the bytes modes too
        # (1250 MB/s -> GB/s). The reference string is deliberately unchanged.
        if round(val, decimal_places) >= step_ratio:
            tier = 3
            val = speed_value / giga_div
        unit = labels[tier]
    else:
        if speed_value >= giga_div:
            tier = 3
        elif speed_value >= mega_div:
            tier = 2
        elif speed_value >= kilo_div:
            tier = 1
        else:
            tier = 0
        val = speed_value / divisors[tier]
        # Round-first promotion: a value that ROUNDS to the next divisor at its display
        # precision (the base unit renders %.0f) shows as "1.0" of the next unit, never as
        # "1000.0" of this one. A loop, not a single check, because the base tier's %.0f
        # verdict feeds the kilo tier's decimal_places verdict. Giga is the top unit, so
        # ~1000 Gbps still (honestly) renders "1000.0".
        while tier < 3:
            display_dp = 0 if tier == 0 else decimal_places
            if round(val, display_dp) < step_ratio:
                break
            tier += 1
            val = speed_value / divisors[tier]
        unit = labels[tier]

    # Adaptive decimal floor (2.1.5 item 11c): only in force-mega mode, only when the value
    # rounds to zero at the configured precision yet real traffic is flowing (>= the 1 kbps
    # floor). Extend to the first significant digit, capped at FORCE_MEGA_MAX_ADAPTIVE_DECIMALS
    # and at the reference-string width so the widget never grows ("0.02", "0.003"). The
    # condition is on the VALUE - a config alone never changes precision. Auto mode is
    # untouched: it never renders zero for nonzero traffic.
    effective_decimal_places = decimal_places
    if (
        force_mega_unit
        and tier == 2
        and val >= FORCE_MEGA_ZERO_FLOOR
        and round(val, decimal_places) == 0
    ):
        ref_len = len(get_reference_value_string(force_mega_unit, decimal_places, unit_type=unit_type))
        max_dp = min(FORCE_MEGA_MAX_ADAPTIVE_DECIMALS, ref_len - 2)  # "0." + decimals must fit the reference
        for extra_dp in range(decimal_places + 1, max_dp + 1):
            if round(val, extra_dp) > 0:
                effective_decimal_places = extra_dp
                break

    # Format numeric part
    if tier == 0:
        formatted_val = f"{val:.0f}"
    else:
        formatted_val = f"{val:.{effective_decimal_places}f}"

    if fixed_width:
        # Use reference string to match logic in layout/renderer
        ref_val = get_reference_value_string(force_mega_unit, decimal_places, unit_type=unit_type)
        formatted_val = formatted_val.rjust(len(ref_val))

    # Apply locale-specific decimal separator (e.g. ',' for de_DE, fr_FR, ru_RU, etc.)
    decimal_sep = getattr(i18n, 'DECIMAL_SEPARATOR', '.')
    if decimal_sep != '.':
        formatted_val = formatted_val.replace('.', decimal_sep)

    if split_unit:
        return formatted_val, unit

    return f"{formatted_val} {unit}"


def format_duration_short(secs: float, i18n) -> str:
    """A compact, localized duration: '2h 30m', '45m', or '30s'. Units come from i18n (DURATION_*_SHORT)
    so a locale can use 'min'/'std'/etc. Consolidates the duplicate _fmt_dur helpers in the Monitor."""
    h_u = str(getattr(i18n, "DURATION_HOURS_SHORT", "h")) if i18n is not None else "h"
    m_u = str(getattr(i18n, "DURATION_MINUTES_SHORT", "m")) if i18n is not None else "m"
    s_u = str(getattr(i18n, "DURATION_SECONDS_SHORT", "s")) if i18n is not None else "s"
    secs = max(0, int(secs))
    h, m = secs // 3600, (secs % 3600) // 60
    if h:
        return f"{h}{h_u} {m}{m_u}"
    if m:
        return f"{m}{m_u}"
    return f"{secs}{s_u}"


def format_decimal(value: float, i18n, places: int = 1) -> str:
    """Format a number with the locale's decimal separator (de/fr/ru/… use ',' not '.').

    Use this for any decimal shown next to format_speed/format_data_size output so a single window never
    mixes separators (e.g. the Hardware list / telemetry RAM was '12.0 GB' beside the Overview's '12,0')."""
    s = f"{value:.{places}f}"
    sep = getattr(i18n, "DECIMAL_SEPARATOR", ".")
    return s.replace(".", sep) if sep and sep != "." else s


def format_data_size(data_bytes: int | float, i18n, precision: int = 2) -> Tuple[float, str]:
    """
    Formats a byte count into a human-readable string with units (B, KB, MB, GB, etc.).

    Uses base 1000 (decimal KB/MB/GB) - NOT 1024 - to match the data-cap accounting, which is
    decimal GB (1000**3, the ISP convention). The usage card and the cap must agree, so both go
    through this function. (See the BASE_DATA_SIZE comment below.)
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

    # DECIMAL base (1000), not binary (1024): the labels are KB/MB/GB (decimal), and this must
    # match the data-cap accounting which is in decimal GB (1000**3, the ISP convention). Using
    # 1024 here made the "data used" glance read ~7%/tier off from the cap it's compared against.
    BASE_DATA_SIZE = 1000.0

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

    # Rounding can push a just-under-boundary value up to the base: e.g. 999_999 B
    # scales to 999.999 KB, which rounds to 1000.0 - so without this we'd show
    # "1000.0 KB" instead of "1.0 MB" (and "1000.0 MB" instead of "1.0 GB", etc.).
    # Promote one more tier. A single re-check suffices: after promotion the value
    # is < 1 and can't reach the base again. Guard against the top unit (PB).
    if formatted_value >= BASE_DATA_SIZE and unit_index < len(UNITS_DATA_SIZE) - 1:
        value /= BASE_DATA_SIZE
        unit_index += 1
        formatted_value = round(value, precision)

    return formatted_value, UNITS_DATA_SIZE[unit_index]


def format_retention_label(days: int, i18n) -> str:
    """
    Human label for a data-retention value, shared by the Settings page and the graph slider so
    the two can never drift. Renders the curated ladder: '1 month', '3 months', '6 months',
    '1 year', '2 years', '5 years', and 'Keep everything' for the unlimited sentinel.
    """
    from netspeedtray import constants
    if days >= constants.data.retention.UNLIMITED_DAYS:
        return i18n.RETENTION_KEEP_EVERYTHING
    plural = getattr(i18n, "PLURAL_SUFFIX", "s")
    if days >= 365:
        years = max(1, round(days / 365))
        return (i18n.RETENTION_YEAR_SINGULAR if years == 1
                else i18n.RETENTION_YEARS_TEMPLATE.format(years=years))
    months = max(1, round(days / 30))
    return i18n.RETENTION_MONTHS_TEMPLATE.format(months=months, plural=(plural if months > 1 else ""))


# --- Data Processing Utilities ---

def calculate_monotone_cubic_interpolation(x_coords: List[float], y_coords: List[float], density: int = 10) -> Tuple[List[float], List[float]]:
    """
    Computes a Monotone Cubic Spline for smooth, non-overshooting interpolation.

    Args:
        x_coords: List of X values (must be strictly increasing).
        y_coords: List of Y values.
        density: Number of interpolated points to generate *between* each pair of original points.

    Returns:
        tuple(interp_x, interp_y): Dense arrays of smoothed points.
    """
    # Lazy import: avoids loading numpy at startup (~20 MB RSS) for users
    # who never enable the mini-graph in the widget.
    import numpy as np

    x = np.array(x_coords, dtype=float)
    y = np.array(y_coords, dtype=float)
    n = len(x)
    
    if n < 2:
        return list(x), list(y)
        
    # 1. Calculate linear slopes (secants)
    dx = np.diff(x)
    dy = np.diff(y)
    
    # Avoid division by zero
    dx[dx == 0] = 1e-9
    secants = dy / dx
    
    # 2. Initialize tangents
    tangents = np.zeros(n)
    
    # 3. Calculate inner tangents (Fritsch-Carlson)
    # The tangent at k is determined by the secants on either side.
    # If secants have different signs, tangent is 0 (local extrema).
    for i in range(1, n-1):
        m_prev = secants[i-1]
        m_next = secants[i]
        
        if m_prev * m_next <= 0:
            tangents[i] = 0.0
        else:
            # Harmonic mean ensures the tangent doesn't cause overshoot
            tangents[i] = (3 * m_prev * m_next) / (max(m_next, m_prev) + 2 * min(m_next, m_prev))
            
    # 4. Boundary conditions (One-sided diffs)
    tangents[0] = secants[0]
    tangents[-1] = secants[-1]
    
    # 5. Generate high-density points (Vectorized)
    # We want 'density' points between each pair of knots.
    # Total new points: (n-1) * density
    
    # Create T vector [0, 1/d, 2/d, ... (d-1)/d] for each segment
    # Shape: (density, )
    t = np.linspace(0, 1, density + 1)[:-1] 
    
    # Precompute Hermite basis functions for all t
    # Shape: (density, )
    t2 = t*t
    t3 = t*t*t
    
    h00 = 2*t3 - 3*t2 + 1
    h10 = t3 - 2*t2 + t
    h01 = -2*t3 + 3*t2
    h11 = t3 - t2
    
    # Prepare segment arrays
    # Shape: (n-1, 1) for broadcasting against (density,)
    x_start = x[:-1, np.newaxis]
    x_end   = x[1:, np.newaxis]
    y_start = y[:-1, np.newaxis]
    y_end   = y[1:, np.newaxis]
    m_start = tangents[:-1, np.newaxis]
    m_end   = tangents[1:, np.newaxis]
    
    seg_dx = x_end - x_start
    
    # Interpolate Y
    # Result shape: (n-1, density)
    # h00 * y0 + h10 * dx * m0 + h01 * y1 + h11 * dx * m1
    # Broadcasting: (n-1, 1) * (density,) -> (n-1, density)
    
    seg_y = (y_start * h00) + (seg_dx * m_start * h10) + (y_end * h01) + (seg_dx * m_end * h11)
    
    # Interpolate X
    # x0 + t * dx
    seg_x = x_start + t * seg_dx
    
    # Flatten and append final point
    interp_x = seg_x.flatten().tolist()
    interp_y = seg_y.flatten().tolist()
    
    interp_x.append(x[-1])
    interp_y.append(y[-1])
    
    return interp_x, interp_y
