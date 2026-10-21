"""
A crash record that exists before logging does - stdlib only, armed first thing by src/monitor.py.

The windowed exe has no console (``sys.stderr`` is None), and logging starts only once Qt, pywin32
and the app's own modules are imported. A crash at logon - a DLL that fails to load, an access
violation inside one, an exception in an import - therefore left no trace anywhere. This module:

- turns on ``faulthandler`` into ``%APPDATA%\\NetSpeedTray\\NetSpeedTray_Crash.log`` (all threads),
  so a hard fault (access violation, stack overflow, abort) writes its Python stacks - during
  STARTUP only: ``startup_complete()`` turns it off once the widget is up, because on Windows it
  also reports access violations that the code raising them handles (see there);
- installs a ``sys.excepthook`` / ``threading.excepthook`` that append any unhandled exception's
  traceback there, until (and also after) logging takes over.

Each start appends one line, so a fault can be placed in time. The file is kept small: when it is
over ``MAX_BYTES`` at start it is rotated to ``.1`` (one generation). The support bundle ships both,
scrubbed like the logs. It is never written anywhere else, and every failure here is swallowed - a
crash recorder must never be what crashes.

The module lives directly in the ``netspeedtray`` package (whose ``__init__`` only sets
``__version__``) so importing it pulls in nothing heavy.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback
from typing import Optional, TextIO

CRASH_LOG_FILENAME = "NetSpeedTray_Crash.log"
MAX_BYTES = 256 * 1024

_file: Optional[TextIO] = None
_lock = threading.Lock()


def crash_log_dir() -> str:
    """The app-data folder (same as helpers.get_app_data_path(), without importing it)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "NetSpeedTray")


def crash_log_path() -> str:
    return os.path.join(crash_log_dir(), CRASH_LOG_FILENAME)


def _rotate(path: str) -> None:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def _write(text: str) -> None:
    fh = _file
    if fh is None:
        return
    try:
        with _lock:
            fh.write(text)
            fh.flush()
    except Exception:
        pass


def record_exception(exc_type, exc_value, exc_tb, where: str = "") -> None:
    """Append one unhandled exception to the crash log (no-op when it is not armed)."""
    try:
        head = "%s unhandled exception%s (pid %d)\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), " in " + where if where else "", os.getpid())
        body = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write(head + body)
    except Exception:
        pass


def _sys_hook(exc_type, exc_value, exc_tb) -> None:
    record_exception(exc_type, exc_value, exc_tb)


def _thread_hook(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", "?") if args.thread is not None else "?"
    record_exception(args.exc_type, args.exc_value, args.exc_traceback, "thread %s" % name)


def startup_complete() -> None:
    """The app has come up: turn faulthandler OFF, keep the exception hooks.

    faulthandler's Windows handler is a vectored exception handler, so it sees first-chance SEH
    exceptions - including ones that are HANDLED: drivers, PDH, WMI providers, Qt and AV hooks
    probe memory and catch the access violation themselves (IsBadReadPtr is the textbook case).
    Left on for the whole run it logged each of those as "Windows fatal exception" with every
    thread's stack, which reads as a crash in a support bundle when nothing crashed. The window
    that matters - DLL loads and imports at logon, before logging exists - is covered by then."""
    try:
        if faulthandler.is_enabled():
            faulthandler.disable()
        _write("%s startup complete pid %d; fault handler off\n"
               % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    except Exception:
        pass


def enable(argv=None) -> Optional[str]:
    """Arm the crash log. Returns its path, or None if it could not be opened. Idempotent."""
    global _file
    if _file is not None:
        return crash_log_path()
    try:
        folder = crash_log_dir()
        os.makedirs(folder, exist_ok=True)
        path = crash_log_path()
        _rotate(path)
        # Line-buffered text in append mode; faulthandler writes to its fd directly.
        _file = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        args = " ".join(list(sys.argv[1:] if argv is None else argv))[:200]
        _write("%s start pid %d%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(),
                                        " args: " + args if args else ""))
        faulthandler.enable(file=_file, all_threads=True)
        sys.excepthook = _sys_hook
        threading.excepthook = _thread_hook
        return path
    except Exception:
        _file = None
        return None
