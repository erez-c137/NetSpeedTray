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
    # Graceful-shutdown channel (core/shutdown_channel.py). The running app registers this window
    # message and quits through the same clean path as tray Exit when it arrives; `--shutdown` (run by
    # the installer/uninstaller before any taskkill) posts it. The window property marks the one HWND
    # that listens, so the sender targets it instead of broadcasting. Both strings are a wire protocol
    # between two installed versions - bump the _vN suffix rather than editing them in place.
    SHUTDOWN_MESSAGE_NAME: Final[str] = "NetSpeedTray_Shutdown_v1"
    SHUTDOWN_WINDOW_PROP: Final[str] = "NetSpeedTray_ShutdownTarget_v1"
    # How long `--shutdown` waits for the app to exit (seconds) unless `--timeout N` overrides it.
    # A normal clean quit is ~1 s. The worst legitimate one lands mid v7 -> v8 migration: the latency
    # probe join (1.5 s) + the DB thread's bounded wait (timeouts.DB_SHUTDOWN_WAIT_MS, 15 s), after
    # which monitor.py exits hard on its own. So this must clear ~17 s with margin, or Setup would
    # taskkill a quit that was about to finish. setup.iss passes the same value (ShutdownTimeoutSec).
    SHUTDOWN_DEFAULT_TIMEOUT_S: Final[float] = 30.0

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