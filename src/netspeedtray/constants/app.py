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
    # Same key (2.2.2 D1/D2): nonzero = no first-run tip and no one-time "what's new" after an update
    # and no one-time "rate / star / support" card in the Monitor (D8) - for managed fleets. Only switches notices OFF;
    # nothing else changes. Documented in DEPLOYING.md - keep the two in sync.
    POLICY_DISABLE_TIPS_VALUE: Final[str] = "DisableTips"
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
    # The activate message's wParam (#373): 0 = the user started the app again (the default, and all
    # that any 2.2 build before rc.4 sends); ACTIVATE_WPARAM_SIGN_IN_DUPLICATE = the SECOND process saw
    # that this session signed in less than SIGN_IN_DUPLICATE_WINDOW_S ago, so it is a duplicate
    # autostart (the Run key plus a Startup-folder shortcut, say), not a request: the running widget
    # then leaves the #24 app-button yield alone. Receivers before rc.4 ignore wParam (a plain activate).
    # 2.2.2 (D3): a USER request also opens the "NetSpeedTray is running" panel after the widget is
    # back. ACTIVATE_WPARAM_RELAUNCH = the second process was started by the updater (``--relaunch``)
    # while the old instance still ran: the widget comes back as for the user (what every earlier build
    # did with wParam 0), but no window opens. Receivers before 2.2.2 read any value but 1 as 0.
    # ACTIVATE_WPARAM_BACKGROUND (combined review C2) = nothing says the user asked (no shortcut, not
    # started by Explorer - a scheduled task, a script): the widget comes back, no window opens.
    # SIGN_IN_DUPLICATE now also covers an autostart (Run key, Startup folder) at any time after sign-in.
    ACTIVATE_WPARAM_USER: Final[int] = 0
    ACTIVATE_WPARAM_SIGN_IN_DUPLICATE: Final[int] = 1
    ACTIVATE_WPARAM_RELAUNCH: Final[int] = 2
    ACTIVATE_WPARAM_BACKGROUND: Final[int] = 3
    # Sign-in startup items can run well after the desktop appears on a slow disk (Windows also delays
    # Run-key apps by ~10 s); 2 minutes covers that. Measured from WTSINFO.LogonTime to its CurrentTime
    # (one wall-clock reading pair from the same query - nothing in our process to drift across sleep).
    SIGN_IN_DUPLICATE_WINDOW_S: Final[float] = 120.0
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
    # Automatic update checks while the app keeps running (a PC that sleeps instead of rebooting can
    # run one instance for weeks). Each tick only asks UpdateChecker.should_check(), which still
    # enforces the 24 h interval, the user's toggle and the IT policy - so this is how promptly a due
    # check happens, not how often GitHub is contacted.
    UPDATE_RECHECK_INTERVAL_MS: Final[int] = 3 * 60 * 60 * 1000
    # After a wake from sleep the network is often not back yet; wait this long before the check.
    UPDATE_RESUME_CHECK_DELAY_MS: Final[int] = 60_000
    # A periodic / after-resume check offers an update with a non-modal flyout (never a dialog the
    # user did not ask for); it stays this long. While a fullscreen app, a modal dialog or a nested
    # event loop is up, the offer waits and is retried this often.
    UPDATE_FLYOUT_DISMISS_MS: Final[int] = 30_000
    UPDATE_OFFER_RETRY_MS: Final[int] = 60_000
    # ...and also while nobody is there to see it (#508 review): the session locked / a screen saver
    # (SHQueryUserNotificationState), or no keyboard/mouse input for this long. An auto-dismissing
    # flyout shown to an empty room - after an unattended wake, say - is an offer lost for a day.
    UPDATE_OFFER_MAX_IDLE_S: Final[int] = 120
    # "Not now" in the update dialog snoozes that version's automatic offers this long (seconds).
    UPDATE_NOT_NOW_SNOOZE_S: Final[int] = 3 * 24 * 60 * 60
    # First-run tip / one-time "what's new" (2.2.2 D1, D2; views/widget/startup_notices.py). Shown
    # this long after the widget is really visible (never in the same instant it appears), and behind
    # the update offer's gates plus a sign-in quiet period (the first SIGN_IN_DUPLICATE_WINDOW_S after
    # the session signed in: the launch storm of every autostart app). A blocked attempt retries
    # this often - the notice is deferred, never lost.
    STARTUP_NOTICE_DELAY_MS: Final[int] = 5_000
    STARTUP_NOTICE_RETRY_MS: Final[int] = 30_000
    # The one-time "rate / star / support" card in the Monitor (2.2.2 D8; core/support_prompt.py):
    # offered on the first Monitor open at least this many days after first_seen_at (the first run
    # of 2.2.2+), never in the first SUPPORT_PROMPT_MIN_UPTIME_S of the app's run ("never on launch"),
    # and counted as shown once it was on screen this long while the Monitor was the active,
    # un-minimized window (or any button was pressed). A clock below the floor (the feature's own
    # birth) is wrong: nothing is written or due then, so a clock set back never shortens the wait.
    SUPPORT_PROMPT_AFTER_DAYS: Final[int] = 30
    SUPPORT_PROMPT_MIN_UPTIME_S: Final[float] = 120.0
    SUPPORT_PROMPT_SEEN_MS: Final[int] = 4_500
    SUPPORT_PROMPT_COLLAPSE_MS: Final[int] = 150
    SUPPORT_PROMPT_CLOCK_FLOOR: Final[str] = "2026-11-01T00:00:00Z"
    # Where Setup records a Microsoft Store install (setup.iss, when run with /STORE - the Store
    # payload's installerParameters). Only then is "Rate it in the Store" offered: the Store lets
    # only users who have the app in their library rate it. Keep in sync with setup.iss.
    INSTALL_SOURCE_REGISTRY_KEY: Final[str] = r"SOFTWARE\NetSpeedTray"
    INSTALL_SOURCE_VALUE: Final[str] = "InstalledFrom"
    INSTALL_SOURCE_STORE: Final[str] = "Store"
    # The Store listing (a Win32 EXE listing; CLAUDE.md): the Store app's review page, and its web
    # page for Windows without the Store app (LTSC, Server, a removed Store).
    STORE_PRODUCT_ID: Final[str] = "XP9MHWQZJPLQM8"
    STORE_REVIEW_URL_TEMPLATE: Final[str] = "ms-windows-store://review/?ProductId={product_id}"
    STORE_WEB_URL_TEMPLATE: Final[str] = "https://apps.microsoft.com/detail/{product_id}"
    # The GitHub release page of one exact version ("What's new" in the one-time notice).
    RELEASE_TAG_URL_TEMPLATE: Final[str] = "https://github.com/{owner}/{repo}/releases/tag/v{version}"
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