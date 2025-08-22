"""
Constants related to network speeds, units, and interfaces.
"""
from typing import Final, Set, List

class UnitConstants:
    """Constants for unit conversions and labels in speed formatting."""
    BITS_PER_BYTE: Final[int] = 8
    KILO_DIVISOR: Final[int] = 1_000
    MEGA_DIVISOR: Final[int] = 1_000_000
    GIGA_DIVISOR: Final[int] = 1_000_000_000
    KILO_THRESHOLD: Final[int] = KILO_DIVISOR
    MEGA_THRESHOLD: Final[int] = MEGA_DIVISOR
    GIGA_THRESHOLD: Final[int] = GIGA_DIVISOR
    MINIMUM_DISPLAY_SPEED: Final[float] = 10_000
    BPS_LABEL: Final[str] = "B/s"
    KBPS_LABEL: Final[str] = "KB/s"
    MBPS_LABEL: Final[str] = "MB/s"
    GBPS_LABEL: Final[str] = "GB/s"
    BITS_LABEL: Final[str] = "bps"
    KBITS_LABEL: Final[str] = "Kbps"
    MBITS_LABEL: Final[str] = "Mbps"
    GBITS_LABEL: Final[str] = "Gbps"

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
    DEFAULT_UNIT_BITS: Final[str] = "bps"
    DEFAULT_UNIT_BYTES: Final[str] = "B/s"

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
    VALID_INTERFACE_MODES: Final[Set[str]] = {"all", "auto", "selected"}
    DEFAULT_EXCLUSIONS: Final[List[str]] = [
        "loopback", "teredo", "isatap", "bluetooth", "vpn", "virtual", "vmware", "vbox"
    ]
    # Maximum plausible speed in Bytes/sec (e.g., ~10 Gbps). Used as a sanity check.
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