"""
Constants for application metadata and lifecycle management.
"""

from typing import Final
import netspeedtray

class AppConstants:
    """Defines application metadata and the single-instance mutex name."""
    APP_NAME: Final[str] = "NetSpeedTray"
    VERSION: Final[str] = netspeedtray.__version__
    # One instance per Windows USER (2.2.0): this prefix + the user's SID (monitor._instance_mutex_name),
    # in Global\ so the same user's second session sees it. The single shared name below let the first
    # user's instance (or a SYSTEM one in session 0) silently block every other user's. setup.iss
    # cannot know the SID: it checks the legacy name and looks for the process instead.
    MUTEX_NAME_PREFIX: Final[str] = "Global\\NetSpeedTray_Instance_"
    # The name every build before 2.2.0 used. Still claimed when free and checked when held, so an
    # old copy and a new one of the same user never both run during the transition (monitor.py).
    LEGACY_MUTEX_NAME: Final[str] = "Global\\NetSpeedTray_SingleInstanceMutex"
    ICON_FILENAME: Final[str] = "NetSpeedTray.ico"
    GITHUB_OWNER: Final[str] = "erez-c137"
    GITHUB_REPO: Final[str] = "NetSpeedTray"
    # Marker file the portable ZIP ships next to the EXE (added at package time in build.bat, just
    # before Compress-Archive). Its presence is how the app knows it's the portable build and should
    # use the guided (folder-copy) update flow instead of the Inno installer flow (#195). The installer
    # copy never contains it. NOTE: build.bat writes this literal name - keep the two in sync.
    PORTABLE_MARKER_FILENAME: Final[str] = "portable.marker"
    # Machine policy (U1, rc.2): IT turns update checks off with a DWORD under the standard Policies
    # key - where Group Policy, Intune (ADMX ingest / OMA-URI) or a plain `reg add` writes it. Nonzero
    # = off: no automatic check, no "Check for Updates" menu item, the Settings toggle is locked.
    # Read-only from the app (core/update_checker.update_check_disabled_by_policy): HKLM first, then
    # HKCU (a user-scope GPO). Both live under Policies, which standard users cannot write. Documented
    # in DEPLOYING.md - keep the two in sync.
    POLICY_REGISTRY_KEY: Final[str] = r"SOFTWARE\Policies\NetSpeedTray"
    POLICY_DISABLE_UPDATE_CHECK_VALUE: Final[str] = "DisableUpdateCheck"
    # Graceful-shutdown channel (core/shutdown_channel.py). The running app registers this window
    # message and quits through the same clean path as tray Exit when it arrives; the installer and
    # uninstaller post it from setup.iss's own [Code] before any taskkill (and `--shutdown` can, by
    # hand). The window property marks the one HWND that listens, so the sender targets it instead of
    # broadcasting. Both strings are a wire protocol between the app and every installer, duplicated
    # in setup.iss (ShutdownMessageName / ShutdownWindowProp) - bump the _vN suffix in both rather
    # than editing them in place.
    SHUTDOWN_MESSAGE_NAME: Final[str] = "NetSpeedTray_Shutdown_v1"
    SHUTDOWN_WINDOW_PROP: Final[str] = "NetSpeedTray_ShutdownTarget_v1"
    # Same channel, second message (#234, rc.2 W14): a second launch of this user's app posts it to
    # the marked window instead of exiting silently, and the running widget shows itself again
    # (un-hide, re-dock, re-seat above the taskbar, back to the docked spot if it is off-screen).
    # App-to-app only - setup.iss never sends it. Same _vN rule.
    ACTIVATE_MESSAGE_NAME: Final[str] = "NetSpeedTray_Activate_v1"
    # How long `--shutdown` waits for the app to exit (seconds) unless `--timeout N` overrides it.
    # A normal clean quit is ~1 s. The worst legitimate one lands mid v7 -> v8 migration: the latency
    # probe join (1.5 s) + the DB thread's bounded wait (timeouts.DB_SHUTDOWN_WAIT_MS, 15 s), after
    # which monitor.py exits hard on its own. So this must clear ~17 s with margin, or Setup would
    # taskkill a quit that was about to finish. setup.iss waits the same value (ShutdownTimeoutSec).
    SHUTDOWN_DEFAULT_TIMEOUT_S: Final[float] = 30.0
    # How long after startup the post-update cleanup (update_applier.start_update_janitor) begins.
    # It removes the previous version's folder after a portable update, and that folder is the
    # rollback until the new version has shown it can start - so not immediately (#260).
    UPDATE_JANITOR_DELAY_MS: Final[int] = 10_000
    # How long after the event loop starts the crash log's fault handler stays on (netspeedtray/
    # crash_log.py): long enough to cover the widget's first show and stats ticks, short enough
    # that handled access violations later in the run are never logged as crashes.
    CRASH_FAULT_WINDOW_MS: Final[int] = 5_000

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate the constants to ensure they meet constraints."""
        if not self.APP_NAME:
            raise ValueError("APP_NAME must not be empty")
        if not self.VERSION:
            raise ValueError("VERSION must not be empty")
        if not self.MUTEX_NAME_PREFIX or not self.LEGACY_MUTEX_NAME:
            raise ValueError("mutex names must not be empty")
        if not self.ICON_FILENAME:
            raise ValueError("ICON_FILENAME must not be empty")

# Singleton instance for easy access
app = AppConstants()