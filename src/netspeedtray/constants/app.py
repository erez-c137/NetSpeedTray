"""
Constants for application metadata and lifecycle management.
"""

from typing import Final

class AppConstants:
    """Defines application metadata and the single-instance mutex name."""
    APP_NAME: Final[str] = "NetSpeedTray"
    VERSION: Final[str] = "1.2.0"
    MUTEX_NAME: Final[str] = "Global\\NetSpeedTray_SingleInstanceMutex"
    ICON_FILENAME: Final[str] = "NetSpeedTray.ico"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate the constants to ensure they meet constraints."""
        if not self.APP_NAME:
            raise ValueError("APP_NAME must not be empty")
        if not self.VERSION:
            raise ValueError("VERSION must not be empty")
        if not self.MUTEX_NAME:
            raise ValueError("MUTEX_NAME must not be empty")
        if not self.ICON_FILENAME:
            raise ValueError("ICON_FILENAME must not be empty")

# Singleton instance for easy access
app = AppConstants()