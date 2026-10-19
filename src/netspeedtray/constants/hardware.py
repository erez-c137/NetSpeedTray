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

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.IMPLAUSIBLE_MW <= 0:
            raise ValueError("IMPLAUSIBLE_MW must be positive")
        if not (self.DESIGN_CAPACITY_MIN_MWH < self.DESIGN_CAPACITY_MAX_MWH):
            raise ValueError("design-capacity range must be increasing")


class HardwareConstants:
    """Container for hardware-related constant groups."""
    def __init__(self) -> None:
        self.power = PowerConstants()

# Singleton instance for easy access
hardware = HardwareConstants()
