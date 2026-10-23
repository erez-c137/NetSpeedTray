"""
Application entry point and lifecycle management for NetSpeedTray.
"""

# matplotlib's Qt backend setup lives at the top of `views/graph/window.py`
# (the only module that imports matplotlib). Loading it here would cost
# ~30-50 MB of RAM at startup even for users who never open the graph
# window - and most launches never do.

import warnings
# These filters are pattern-only and don't load matplotlib themselves -
# safe to register early so they're in place whenever matplotlib first runs.
warnings.filterwarnings("ignore", "Tight layout not applied")
warnings.filterwarnings("ignore", "constrained_layout not applied")

import logging
import signal
import sys
import os
import threading
import time
from typing import Callable, List, Optional

# `--shutdown` hard deadline. Must stay the same as constants.app.SHUTDOWN_DEFAULT_TIMEOUT_S (a unit
# test pins it); duplicated as a literal because this runs before anything from netspeedtray loads.
_SHUTDOWN_DEFAULT_TIMEOUT_S = 30.0
_SHUTDOWN_DEADLINE_GRACE_S = 5.0


def _arm_shutdown_deadline(argv: List[str],
                           exit_fn: Callable[[int], None] = os._exit) -> Optional[threading.Timer]:
    """For `--shutdown [--timeout N]`, arm a hard process deadline of N + 5 s before anything else
    runs. A caller waiting on this process (a script; 2.2.0-beta.1's installer also ran it, via
    Inno's Exec with no timeout of its own) must never hang on a wedge anywhere - an import, a DLL
    load, the log file, the wait itself. Past the deadline the process exits 1 ("still running")."""
    if "--shutdown" not in argv:
        return None
    timeout = _SHUTDOWN_DEFAULT_TIMEOUT_S
    if "--timeout" in argv:
        try:
            value = float(argv[argv.index("--timeout") + 1])
            if value > 0:
                timeout = value
        except (IndexError, ValueError):
            pass
    timer = threading.Timer(timeout + _SHUTDOWN_DEADLINE_GRACE_S, exit_fn, args=(1,))
    timer.daemon = True
    timer.start()
    return timer


# Armed at import, ahead of the Qt / pywin32 / app imports below.
_shutdown_deadline = _arm_shutdown_deadline(sys.argv[1:]) if __name__ == "__main__" else None

# The crash record, before the DLL-loading imports below: the windowed exe has no stderr and
# logging starts only inside main(), so a crash at logon in one of these imports (or a fault in a
# DLL) used to leave no trace at all. faulthandler + excepthooks -> NetSpeedTray_Crash.log.
if __name__ == "__main__":
    try:
        from netspeedtray import crash_log as _crash_log
        _crash_log.enable()
    except Exception:
        pass

import win32api
import win32event
import winerror
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from netspeedtray import constants
from netspeedtray.utils.config import ConfigManager, ConfigError
from netspeedtray.utils.taskbar_utils import get_taskbar_height
from netspeedtray.views.widget import NetworkSpeedWidget

class AlreadyRunningError(RuntimeError):
    """Raised when a second instance launches while one is already running. Distinct from a real
    mutex failure so the caller can exit silently (no dialog) - a duplicate launch is a no-op."""


_HEADLESS_FLAGS = ("--apply-update", "--shutdown", "--export-csv")
# A legacy (pre-2.2) instance found at startup is re-checked once after this long before the launch
# gives way to it: an old and a new copy started at the same moment (a Run key plus a pinned
# shortcut) would otherwise each see the other and BOTH exit.
_LEGACY_RECHECK_DELAY_S = 0.5      # poll interval while that copy may still be exiting
_LEGACY_RECHECK_WINDOW_S = 5.0     # give up waiting (it is running) after this long - a cold logon
                                   # can make an exiting old copy slow to go
# An old copy started this much earlier than us is an established instance, not a racing one: no
# wait at all.
_LEGACY_ESTABLISHED_S = 10.0


def _current_user_sid() -> str:
    """This process's user SID as a string (S-1-5-21-...)."""
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()


def _instance_mutex_name() -> str:
    """One instance per USER (2.2.0): ``Global\\`` + a per-user name. Global so the same user signed
    in to two sessions (RDS, multi-session Windows, fast user switching to their own second session)
    still gets one instance on their one config and database; per user so one user's instance
    never blocks another's - the pre-2.2 single Global\\ name did (and a SYSTEM instance an old
    installer started in session 0 blocked everyone)."""
    try:
        suffix = _current_user_sid()
    except Exception:
        # No SID (should not happen): the user name still separates users on this machine.
        suffix = "user-" + (os.environ.get("USERDOMAIN", "") + "-" + os.environ.get("USERNAME", "")
                            ).replace("\\", "_")
    return constants.app.MUTEX_NAME_PREFIX + suffix


def _is_app_process(pid: int, name: str) -> bool:
    """A NetSpeedTray.exe other than this process, and not a headless run (the portable applier
    showing its notice, `--shutdown`, `--export-csv`). An unreadable command line counts."""
    if pid == os.getpid() or str(name).lower() != "netspeedtray.exe":
        return False
    try:
        import psutil
        cmdline = psutil.Process(pid).cmdline()
    except Exception:
        cmdline = []
    return not any(flag in cmdline for flag in _HEADLESS_FLAGS)


def _same_user_instances_wts(my_sid: str) -> List[int]:
    """PIDs of this user's NetSpeedTray instances in ANY session. WTSEnumerateProcesses reports
    each process's user SID without opening it, so an ELEVATED old instance of the same user
    (2.1.6/2.1.7 in-app updates relaunched elevated) is seen too."""
    import win32security
    import win32ts
    found = []
    for _session, pid, name, sid in win32ts.WTSEnumerateProcesses(
            win32ts.WTS_CURRENT_SERVER_HANDLE, 1, 0):
        if sid is None or not _is_app_process(pid, name):
            continue
        if win32security.ConvertSidToStringSid(sid) == my_sid:
            found.append(pid)
    return found


def _same_user_instances_psutil(my_sid: str) -> List[int]:
    """Fallback without win32ts: psutil names each process's user when it may read the token; one
    it may not read (elevated, or another user's) counts only when it is in OUR session."""
    import ctypes
    import psutil
    import win32security
    me_name = win32security.LookupAccountSid(None, win32security.ConvertStringSidToSid(my_sid))
    me_user = ("%s\\%s" % (me_name[1], me_name[0])).lower()
    k32 = ctypes.windll.kernel32

    def session_of(pid: int) -> Optional[int]:
        out = ctypes.c_ulong(0)
        return out.value if k32.ProcessIdToSessionId(pid, ctypes.byref(out)) else None

    mine = session_of(os.getpid())
    found = []
    for proc in psutil.process_iter(["pid", "name"]):
        pid, name = proc.info["pid"], proc.info["name"] or ""
        if not _is_app_process(pid, name):
            continue
        try:
            if proc.username().lower() == me_user:
                found.append(pid)
            continue
        except Exception:
            pass
        sess = session_of(pid)
        if sess is not None and sess == mine:
            found.append(pid)
    return found


def _legacy_instance_of_this_user() -> bool:
    """Is a NetSpeedTray app of THIS user (any session) running besides us? Asked only when the
    legacy Global\\ mutex is held: a pre-2.2 instance of ours shares our config and database, while
    another user's (or a SYSTEM one in session 0) must not stop us.

    WTSEnumerateProcesses first, psutil as the fallback; only when neither can answer is an
    instance assumed (two instances on one database is the worse failure). A copy that started well
    before us (``_LEGACY_ESTABLISHED_S``) is running, full stop; a younger one may be an old copy
    launched at the same moment, which exits on seeing ours - it is polled for up to
    ``_LEGACY_RECHECK_WINDOW_S`` (cold logons are slow) so it does not take this one down with it."""
    log = logging.getLogger("NetSpeedTray.SingleInstanceChecker")
    try:
        my_sid = _current_user_sid()
    except Exception:
        log.warning("Could not read this process's user; assuming an older NetSpeedTray is running.",
                    exc_info=True)
        return True

    def scan() -> Optional[List[int]]:
        for probe in (_same_user_instances_wts, _same_user_instances_psutil):
            try:
                return probe(my_sid)
            except Exception:
                log.debug("Instance scan %s failed.", getattr(probe, "__name__", probe), exc_info=True)
        return None

    first = scan()
    if first is None:
        log.warning("Could not list processes; assuming an older NetSpeedTray is running.")
        return True
    if not first:
        return False
    if _any_established(first):
        return True                                   # confirmed: it was running before we started
    deadline = time.monotonic() + _LEGACY_RECHECK_WINDOW_S
    remaining = set(first)
    while True:
        time.sleep(_LEGACY_RECHECK_DELAY_S)
        again = scan()
        if again is None:
            return True
        remaining &= set(again)
        if not remaining:
            return False                              # the racing copy has gone
        if time.monotonic() >= deadline:
            return True


def _any_established(pids: List[int]) -> bool:
    """Did any of ``pids`` start ``_LEGACY_ESTABLISHED_S`` or more before this process?"""
    try:
        import psutil
        mine = psutil.Process(os.getpid()).create_time()
        for pid in pids:
            try:
                if psutil.Process(pid).create_time() <= mine - _LEGACY_ESTABLISHED_S:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


class SingleInstanceChecker:
    """
    Ensures that only one instance of the application runs per Windows USER.

    The mutex is ``Global\\NetSpeedTray_Instance_<user SID>`` since 2.2.0 (``_instance_mutex_name``).
    It was one ``Global\\`` name for everyone, so the first user's instance - or a SYSTEM instance
    an old installer started in session 0 - silently stopped every other signed-in user's from
    starting until it exited. Each user has their own config and history, so one instance per
    user (across all their sessions) is the real invariant.

    Transition: 2.1.x and the 2.2 betas only know the shared ``Global\\`` name. So it is also
    claimed when free (an old copy started later then exits, as it always would), and when it is
    already held, this launch exits only if a NetSpeedTray process of the same user is running
    (``_legacy_instance_of_this_user``).

    Name squatting (known, accepted): any process can create these names first and so make the app
    believe it is already running - a local denial of service, not an escalation (nothing is ever
    read from, or trusted in, the object). The per-user name can be squatted only by that user or an
    administrator; the legacy name by anyone, which a launch survives (only the same user's
    NetSpeedTray process blocks it). Checking that the holder IS NetSpeedTray would need the
    owner's process, which a mutex does not reveal.

    This class is designed to be used as a context manager.

    Raises:
        AlreadyRunningError: If another instance is already running (caller exits silently) - including
            one we may not open (an elevated instance: ERROR_ACCESS_DENIED).
        RuntimeError: If the mutex itself cannot be created.
    """
    def __init__(self):
        self.mutex = None
        self.legacy_mutex = None
        self.logger = logging.getLogger("NetSpeedTray.SingleInstanceChecker")
        self._acquire_session_mutex()
        try:
            self._claim_legacy_mutex()
        except AlreadyRunningError:
            self._close(self.mutex)
            self.mutex = None
            raise

    def _claim_legacy_mutex(self) -> None:
        held_elsewhere = False
        try:
            handle = win32event.CreateMutex(None, False, constants.app.LEGACY_MUTEX_NAME)
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                self._close(handle)
                held_elsewhere = True
            else:
                self.legacy_mutex = handle
        except win32api.error as e:
            if e.winerror == winerror.ERROR_ACCESS_DENIED:
                held_elsewhere = True               # another user's, or an elevated instance's
            else:
                self.logger.warning("Could not check the legacy mutex: %s", e)
        if held_elsewhere and _legacy_instance_of_this_user():
            self.logger.info("An older NetSpeedTray of this user is already running; this launch "
                             "will exit quietly.")
            raise AlreadyRunningError("An older instance of this user is already running.")

    def _close(self, handle) -> None:
        if handle:
            try:
                win32api.CloseHandle(handle)
            except win32api.error as e:
                self.logger.error("Failed to release mutex: %s", e)

    def _acquire_session_mutex(self) -> None:
        try:
            self.mutex = win32event.CreateMutex(None, False, _instance_mutex_name())
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                # Expected, not an error: a duplicate launch while the app is already running. Log it and
                # let the caller exit quietly - no dialog (per design: re-launch is a silent no-op).
                self.logger.info("NetSpeedTray is already running; this duplicate launch will exit quietly.")
                raise AlreadyRunningError("Application is already running.")
        except win32api.error as e:
            if e.winerror == winerror.ERROR_ACCESS_DENIED:
                # The mutex exists but we may not open it: it was created by an ELEVATED instance (its
                # default DACL admits only Administrators/SYSTEM and its High integrity label blocks a
                # Medium-IL caller) of this same user. CreateMutexW
                # can only be denied on an object that already exists, so this is the same fact as
                # ERROR_ALREADY_EXISTS - another instance is running - and gets the same quiet exit.
                self.logger.info("NetSpeedTray is already running (mutex held by an elevated "
                                 "instance); this duplicate launch will exit quietly.")
                raise AlreadyRunningError("Application is already running.") from e
            self.logger.error("Failed to create mutex: %s", e)
            raise RuntimeError(f"Failed to create mutex: {e}") from e

    def __enter__(self):
        """Enter the context manager, acquiring the mutex."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager, releasing the mutex handles."""
        self._close(self.mutex)
        self._close(self.legacy_mutex)

def set_app_working_directory():
    """
    Sets the Current Working Directory (CWD) to the application's root directory.
    This ensures that relative paths (like 'assets/') work correctly regardless
    of how the application was launched (e.g., via Registry Run key).
    """
    try:
        if getattr(sys, 'frozen', False):
            # PyInstaller: sys.executable is the exe path
            app_dir = os.path.dirname(sys.executable)
        else:
            # Dev mode: sys.argv[0] is the script path (D:\...\src\monitor.py)
            # We want the project root, not 'src'.
            # monitor.py is in 'src', so we go up one level.
            script_path = os.path.abspath(sys.argv[0])
            src_dir = os.path.dirname(script_path)
            # Check if we are in 'src' and need to go up, or if we should stay in 'src' based on asset logic
            # helpers.get_app_asset_path traverses up looking for 'src', so if we set CWD to project root...
            # The previous logic seemed to rely on running FROM project root in dev.
            # Let's align with that. If we are in 'src/monitor.py', project root is parent.
            app_dir = os.path.dirname(src_dir) 
        
        # Determine strict asset location via helpers first to be sure? 
        # No, setting CWD to the EXE/Script dir is standard practice.
        # However, helpers.py traverses UP to find 'src'.
        # If we set CWD to project root, helpers will work.
        
        os.chdir(app_dir)
        # logging isn't setup yet in main(), but we can print carefully or rely on main's logging later.
    except Exception:
        pass # If this fails, we proceed with original CWD and hope for the best.

def _install_exception_hooks(logger: logging.Logger) -> None:
    """Log every unhandled exception - on the main thread AND on any threading.Thread (whose
    default hook prints to a stderr the windowed exe does not have) - and keep it in the crash
    log too, which survives when the log itself never got written."""
    from netspeedtray import crash_log

    def excepthook(exc_type, exc_value, exc_tb):
        logger.critical("Unhandled exception:", exc_info=(exc_type, exc_value, exc_tb))
        crash_log.record_exception(exc_type, exc_value, exc_tb)

    def thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "?") if args.thread is not None else "?"
        logger.critical("Unhandled exception in thread %s:", name,
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        crash_log.record_exception(args.exc_type, args.exc_value, args.exc_traceback,
                                   "thread %s" % name)

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def _stop_started_widget(widget, logger: logging.Logger) -> None:
    """After a startup failure: stop whatever the widget already started (monitor thread, DB
    worker, latency probe, event hooks), so the process does not exit with QThreads still
    running - which aborts it with a second crash on top of the first. No config save: the state
    is not one to persist."""
    if widget is None:
        return
    try:
        widget.cleanup(save_config=False)
    except Exception:
        logger.error("Cleanup after the startup failure failed too.", exc_info=True)


def main() -> int:
    """
    Main entry point for the NetSpeedTray application.

    Orchestrates the application's startup sequence:
    1. Sets up logging.
    2. Ensures single-instance execution (or handles --shutdown command).
    3. Loads configuration.
    4. Initializes internationalization with the user's chosen language.
    5. Creates the main widget and runs the application event loop.

    Returns:
        An integer exit code.
    """
    # 1. Set up logging immediately so that any subsequent errors can be recorded.
    # Set CWD first to ensure log files (if relative) go to the right place
    set_app_working_directory()
    ConfigManager.setup_logging()
    logger = logging.getLogger("NetSpeedTray.Main")
    
    _install_exception_hooks(logger)

    # Graceful-close path: `--shutdown [--timeout N]`, for manual / scripted use (setup.iss now posts
    # the same message from its own [Code] and never runs this exe - see core/shutdown_channel.py).
    # Asks the running instance to quit through its tray-Exit path and waits for it to exit.
    # Headless and first: no QApplication, no mutex, and none of the update sweeps below - whatever
    # runs this may itself be executing from an update temp dir.
    try:
        from netspeedtray.core.shutdown_channel import run_shutdown_cli
        shutdown_code = run_shutdown_cli()
        if shutdown_code is not None:
            return shutdown_code
    except Exception as e:
        logger.error("Shutdown command errored: %s", e, exc_info=True)
        return 2

    # Clear any orphaned update temp dir left by a past in-app update - its installer ran from the dir,
    # so the success path couldn't delete it; sweep it here so they don't pile up in %TEMP% (#19).
    try:
        from netspeedtray.core.update_installer import sweep_stale_update_dirs
        sweep_stale_update_dirs()
    except Exception:
        pass

    # Apply-update path: `--apply-update <install_dir> --wait-pid <pid>`. We are the freshly
    # downloaded, checksum-verified copy; the previous version launched us so we can replace the
    # folder it was running from, which it could not do itself. Headless and short-lived - it must
    # run before the QApplication and before the single-instance mutex, because the old instance may
    # still be shutting down and holding both.
    try:
        from netspeedtray.core.update_applier import run_apply_update_cli
        apply_code = run_apply_update_cli()
        if apply_code is not None:
            return apply_code
    except Exception as e:
        logger.error("Apply-update errored: %s", e, exc_info=True)
        return 1

    # Headless export path: `--export-csv --period 24h --out <dir>` writes the two-file stats export
    # and exits, without ever creating the GUI QApplication (so it can run alongside the live app).
    try:
        from netspeedtray.utils.export_cli import run_export_cli
        export_code = run_export_cli()
        if export_code is not None:
            return export_code
    except Exception as e:
        logger.error("Headless export errored: %s", e, exc_info=True)
        return 1

    # The QApplication must be created before any UI elements.
    app = QApplication(sys.argv)

    i18n_strings: Optional[constants.i18n.I18nStrings] = None
    widget: Optional[NetworkSpeedWidget] = None

    try:
        # 2. Use the context manager to ensure only one instance is running.
        with SingleInstanceChecker():
            # 3. Load the application configuration from the file.
            config_manager = ConfigManager()
            config = config_manager.load()

            # 4. Initialize the internationalization module with the user's saved language.
            i18n_strings = constants.i18n.get_i18n(config.get("language"))

            # 4b. Flip the whole app to right-to-left when a Hebrew/RTL locale is active. This mirrors
            # every Qt layout (settings pages, Monitor, flyouts) for free; the widget's pixel-positioned
            # rendering is mirrored separately. Language changes require a restart, so applying this once
            # at startup is sufficient.
            from PyQt6.QtCore import Qt
            app.setLayoutDirection(
                Qt.LayoutDirection.RightToLeft if i18n_strings.is_rtl else Qt.LayoutDirection.LeftToRight)

            # 5. Create and configure the main widget.
            taskbar_height = get_taskbar_height()
            widget = NetworkSpeedWidget(
                taskbar_height=taskbar_height,
                config=config,
                i18n=i18n_strings
            )
            widget.set_app_version(constants.app.VERSION)
            
            # 6. Configure the application to behave like a tray utility.
            # Set global app icon so all windows (including QMessageBox) show the logo
            from netspeedtray.utils.helpers import get_app_asset_path
            from PyQt6.QtGui import QIcon
            icon_path = get_app_asset_path(constants.app.ICON_FILENAME)
            if icon_path.exists():
                app.setWindowIcon(QIcon(str(icon_path)))

            app.setQuitOnLastWindowClosed(False)
            app.aboutToQuit.connect(widget.cleanup)

            # 7. Set up signal handlers to gracefully call the widget's own cleanup routine.
            signal.signal(signal.SIGINT, lambda s, f: QApplication.instance().quit())
            signal.signal(signal.SIGTERM, lambda s, f: QApplication.instance().quit())

            # 8. Show the widget after a short delay to ensure the event loop is running.
            QTimer.singleShot(500, widget.show)

            # 8a. Startup has succeeded once the widget is up and the first stats ticks have run:
            # turn the crash log's fault handler off (it also reports access violations that
            # drivers/PDH/WMI/AV handle themselves); the exception hooks stay.
            def _startup_complete() -> None:
                from netspeedtray import crash_log
                crash_log.startup_complete()
            QTimer.singleShot(constants.app.CRASH_FAULT_WINDOW_MS, _startup_complete)

            # 8b. Post-update cleanup (#260), once this version has demonstrably STARTED: the
            # previous version's folder is the rollback until then. Runs INSIDE the single-instance
            # mutex (review C1) - the --apply-update process exits long before this point, so the
            # applier never sweeps its own ground - and on a background thread, because it waits
            # for the applier to exit and retries files AV still holds. Only what an update
            # RECORDED is removed, plus the <install>.old-<unix time> leftovers older appliers
            # left (strictly checked - see update_applier.sweep_old_backups); anything that merely
            # looks like a staged copy or a backup - e.g. a rollback copy like
            # NetSpeedTray-2.1.5-backup kept beside the install - is never touched.
            def _start_update_janitor() -> None:
                try:
                    from netspeedtray.core.update_applier import start_update_janitor
                    start_update_janitor()
                except Exception:
                    logger.debug("Post-update cleanup not started.", exc_info=True)
            QTimer.singleShot(constants.app.UPDATE_JANITOR_DELAY_MS, _start_update_janitor)

            # 9. Start the application event loop.
            return app.exec()

    except AlreadyRunningError:
        # A second instance: no dialog, exit 0 - but first ask the running widget to show itself
        # (#234): a hidden or off-screen widget used to be recoverable only through Task Manager.
        # A pre-2.2 instance (legacy mutex) does not listen; the post is then simply ignored.
        try:
            from netspeedtray.core.shutdown_channel import request_activate
            request_activate()
        except Exception as e:
            logger.debug("Could not signal the running instance: %s", e)
        return 0
    except Exception as e:
        # This is a global catch-all for any critical error during startup.
        logger.critical("A critical error occurred during startup: %s", e, exc_info=True)
        try:
            from netspeedtray import crash_log
            crash_log.record_exception(type(e), e, e.__traceback__, "startup")
        except Exception:
            pass
        # A widget that failed inside its own __init__ has already stopped what it started.
        _stop_started_widget(widget, logger)
        title = i18n_strings.ERROR_WINDOW_TITLE if i18n_strings else "Application Error"
        QMessageBox.critical(None, title, f"A critical error occurred and NetSpeedTray must close:\n\n{e}")
        return 1

def _finish(code: int) -> int:
    """The exit code to leave with - or, while a database thread quit gave up waiting for is still
    running (a long v7 -> v8 migration), a hard exit: letting interpreter shutdown destroy a running
    QThread aborts the process with a crash dialog. A hard exit mid-migration is safe by design (the
    journal + recover() at the next start; kill-tested)."""
    from netspeedtray.core.database import detached_workers_running
    from netspeedtray.core import thread_reaper
    # Also any worker that outlived its bounded wait at quit (a DNS lookup, the update check's
    # urlopen, a WMI call): same abort if it is destroyed while running (core/thread_reaper.py).
    if detached_workers_running() or thread_reaper.any_running():
        logging.shutdown()
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(_finish(main()))