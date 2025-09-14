"""
Constants related to network speeds, units, and interfaces.
"""
from typing import Final, Set, List

class UnitConstants:
    """Constants for unit conversions and i18n keys for labels."""
    BITS_PER_BYTE: Final[int] = 8
    KILO_DIVISOR: Final[int] = 1_000
    MEGA_DIVISOR: Final[int] = 1_000_000
    GIGA_DIVISOR: Final[int] = 1_000_000_000
    KILO_THRESHOLD: Final[int] = KILO_DIVISOR
    MEGA_THRESHOLD: Final[int] = MEGA_DIVISOR
    GIGA_THRESHOLD: Final[int] = GIGA_DIVISOR
    MINIMUM_DISPLAY_SPEED: Final[float] = 10_000
    
    # These are now i18n keys
    BPS_LABEL: Final[str] = "BPS_LABEL"
    KBPS_LABEL: Final[str] = "KBPS_LABEL"
    MBPS_LABEL: Final[str] = "MBPS_LABEL"
    GBPS_LABEL: Final[str] = "GBPS_LABEL"
    BITS_LABEL: Final[str] = "BITS_LABEL"
    KBITS_LABEL: Final[str] = "KBITS_LABEL"
    MBITS_LABEL: Final[str] = "MBITS_LABEL"
    GBITS_LABEL: Final[str] = "GBITS_LABEL"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.BITS_PER_BYTE != 8:
            raise ValueError("BITS_PER_BYTE must be 8")
        if not (self.KILO_THRESHOLD < self.MEGA_THRESHOLD < self.GIGA_THRESHOLD):
            raise ValueError("Thresholds must be in increasing order")

class NetworkSpeedConstants:
    """Constants for network speed calculations."""
    DEFAULT_SPEED: Final[float] = 0.0
    MIN_TIME_DIFF: Final[float] = 1e-6
    MIN_RECORDABLE_SPEED_BPS: Final[float] = 1.0
    
    # These are now i18n keys
    DEFAULT_UNIT_BITS: Final[str] = "BITS_LABEL"
    DEFAULT_UNIT_BYTES: Final[str] = "BPS_LABEL"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.DEFAULT_SPEED < 0:
            raise ValueError("DEFAULT_SPEED must be non-negative")
        if self.MIN_TIME_DIFF <= 0:
            raise ValueError("MIN_TIME_DIFF must be positive")

class InterfaceConstants:
    """Constants related to network interface management."""
    DEFAULT_MODE: Final[str] = "auto"
    VALID_INTERFACE_MODES: Final[Set[str]] = {"auto", "all_physical", "all_virtual", "selected"}
    
    # The default config must be JSON serializable, so this must be a list.
    DEFAULT_EXCLUSIONS: Final[List[str]] = [
        "loopback", "teredo", "isatap", "bluetooth", "vpn", "virtual", "vmware", "vbox"
    ]
    
    # Maximum plausible speed in bytes per second (10 Gbps) to filter out anomalies.
    MAX_REASONABLE_SPEED_BPS: Final[int] = 1_250_000_000

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.DEFAULT_MODE not in self.VALID_INTERFACE_MODES:
            raise ValueError(f"DEFAULT_MODE '{self.DEFAULT_MODE}' must be in VALID_INTERFACE_MODES")

class NetworkConstants:
    """Container for network-related constant groups."""
    def __init__(self) -> None:
        self.units = UnitConstants()
        self.speed = NetworkSpeedConstants()
        self.interface = InterfaceConstants()

# Singleton instance for easy access
network = NetworkConstants()