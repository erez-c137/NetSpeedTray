"""
Constants for application metadata and lifecycle management.
"""

from typing import Final
import netspeedtray

class AppConstants:
    """Defines application metadata and the single-instance mutex name."""
    APP_NAME: Final[str] = "NetSpeedTray"
    VERSION: Final[str] = netspeedtray.__version__
    MUTEX_NAME: Final[str] = "Global\\NetSpeedTray_SingleInstanceMutex"
    ICON_FILENAME: Final[str] = "NetSpeedTray.ico"
    GITHUB_OWNER: Final[str] = "erez-c137"
    GITHUB_REPO: Final[str] = "NetSpeedTray"
    # Marker file the portable ZIP ships next to the EXE (added at package time in build.bat, just
    # before Compress-Archive). Its presence is how the app knows it's the portable build and should
    # use the guided (folder-copy) update flow instead of the Inno installer flow (#195). The installer
    # copy never contains it. NOTE: build.bat writes this literal name - keep the two in sync.
    PORTABLE_MARKER_FILENAME: Final[str] = "portable.marker"

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