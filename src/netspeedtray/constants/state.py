"""
Constants related to application state, controller logic, and data storage.
"""
from typing import Final, List

# CORRECTED IMPORT
from .config import config

class WidgetStateConstants:
    """Constants related to the internal state of the main widget."""
    MAX_HISTORY_POINTS: Final[int] = int(round(
        (config.defaults.DEFAULT_HISTORY_MINUTES * 60) / config.defaults.DEFAULT_UPDATE_RATE
    ))
    POSITION_TOLERANCE: Final[int] = 5
    MAX_SPEED_DISPLAY_TEMPLATE: Final[str] = "↑ 999.9 GB/s"
    CSV_FILE_NAME: Final[str] = "nst_speed_history.csv"
    CSV_HEADERS: Final[List[str]] = ["timestamp", "upload_bytes", "download_bytes"]

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.MAX_HISTORY_POINTS <= 0:
            raise ValueError("MAX_HISTORY_POINTS must be positive")
        if not self.CSV_FILE_NAME:
            raise ValueError("CSV_FILE_NAME must not be empty")

class ControllerConstants:
    """Constants specific to the application's main controller."""
    SPEED_LOGGING_FREQUENCY: Final[int] = 30  # Log speed every 30 updates

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.SPEED_LOGGING_FREQUENCY <= 0:
            raise ValueError("SPEED_LOGGING_FREQUENCY must be positive")

class StateAndLogicConstants:
    """Container for state and controller logic constants."""
    def __init__(self) -> None:
        self.widget = WidgetStateConstants()
        self.controller = ControllerConstants()

# Singleton instance for easy access
state = StateAndLogicConstants()