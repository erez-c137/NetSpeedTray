"""
Constants for hardware readings sanity-checked against real-world firmware.

Guard thresholds live here so the pollers in core/ and utils/ read from one
place instead of inventing their own magic numbers.
"""
from typing import Final


class PowerConstants:
    """Battery power thresholds.

    The flow ceiling is expressed in MILLIWATTS - the unit Win32_BatteryStatus
    reports DischargeRate/ChargeRate in, and the same unit this ceiling is
    compared against at the poll site.
    """
    # Above every real laptop discharge and every real adapter's charge output,
    # so it drops only firmware garbage - some packs emit nonsense for a few
    # seconds around an AC transition. A real transient over-read below this
    # ceiling stays admissible; the stats history absorbs those.
    IMPLAUSIBLE_MW: Final[int] = 500_000

    # Sane laptop-pack design capacity range (mWh) - BatteryStaticData values
    # outside it are bare sensors misreporting, not packs.
    DESIGN_CAPACITY_MIN_MWH: Final[int] = 10_000
    DESIGN_CAPACITY_MAX_MWH: Final[int] = 200_000

    # How long a failed WMI battery read waits before trying again. A transient
    # error (RPC unavailable right after resume) must not end battery readings
    # until restart; a missing WMI class is permanent and is not retried at all.
    BATTERY_WMI_RETRY_SEC: Final[float] = 180.0
    # Same for the design-capacity read behind the runtime projection.
    CAPACITY_RETRY_SEC: Final[float] = 600.0

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.IMPLAUSIBLE_MW <= 0:
            raise ValueError("IMPLAUSIBLE_MW must be positive")
        if not (self.DESIGN_CAPACITY_MIN_MWH < self.DESIGN_CAPACITY_MAX_MWH):
            raise ValueError("design-capacity range must be increasing")
        if self.BATTERY_WMI_RETRY_SEC <= 0 or self.CAPACITY_RETRY_SEC <= 0:
            raise ValueError("battery retry backoffs must be positive")


class TemperatureConstants:
    """Display unit for temperatures (rc.2 F8). Every reading and every stored value is Celsius;
    only the formatter (utils.helpers.format_temperature) converts."""
    CELSIUS: Final[str] = "celsius"
    FAHRENHEIT: Final[str] = "fahrenheit"
    SYMBOLS: Final[dict] = {CELSIUS: "°C", FAHRENHEIT: "°F"}
    # Widest number each unit's readout reserves room for. The widget is sized ONCE from reference
    # strings, never from live values (CLAUDE.md), so these must cover a real reading: in Celsius
    # two digits (99°C, as before - 100°C+ is past every CPU's throttle point); in Fahrenheit every
    # reading from 38°C up is three digits (100°F), up to 301°F at the 150°C sanity ceiling the
    # sensor paths enforce. All 9s: UI fonts draw digits at one width, so "999" costs nothing over
    # "301", and in a proportional font a 9 is among the widest digits.
    WIDGET_REFERENCE_NUMBER: Final[dict] = {CELSIUS: "99", FAHRENHEIT: "999"}
    # The hover card's fixed-width column (it already reserved three digits for Celsius).
    HOVER_REFERENCE_NUMBER: Final[dict] = {CELSIUS: "100", FAHRENHEIT: "999"}

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for table in (self.SYMBOLS, self.WIDGET_REFERENCE_NUMBER, self.HOVER_REFERENCE_NUMBER):
            if set(table) != {self.CELSIUS, self.FAHRENHEIT}:
                raise ValueError("temperature tables must cover exactly celsius and fahrenheit")


class HardwareConstants:
    """Container for hardware-related constant groups."""
    def __init__(self) -> None:
        self.power = PowerConstants()
        self.temperature = TemperatureConstants()

# Singleton instance for easy access
hardware = HardwareConstants()
