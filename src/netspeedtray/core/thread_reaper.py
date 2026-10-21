"""
Threads that outlived their bounded wait at quit.

A quit must never hang, so every worker gets a bounded wait - but some work cannot be interrupted
from outside (a DNS lookup in gethostbyname, urlopen's 10 s update check, a WMI call). A QThread
still running when Python tears the process down aborts it ("QThread: Destroyed while thread is
still running" -> a crash dialog on top of a normal quit). Such threads are handed to ``outlived``:
kept referenced so nothing destroys them, and ``any_running`` tells monitor.py's ``_finish`` to
end the process with ``os._exit`` instead - the same answer database.py gives a migration that
outlived its wait. Nothing they do at that point matters: config and history are already saved.
"""
from __future__ import annotations

import logging
import threading
from typing import List

logger = logging.getLogger("NetSpeedTray.ThreadReaper")

_OUTLIVED: List[object] = []
_lock = threading.Lock()


def _is_running(thread) -> bool:
    try:
        if hasattr(thread, "isRunning"):
            return thread.isRunning() is True
        return thread.is_alive() is True
    except RuntimeError:                    # its C++ side is already gone
        return False


def outlived(thread, what: str) -> bool:
    """Record ``thread`` if it is still running. True when it was."""
    if thread is None or not _is_running(thread):
        return False
    try:
        # A Qt parent would delete it (C++ side) when the parent goes, whatever Python holds.
        if hasattr(thread, "setParent"):
            thread.setParent(None)
    except RuntimeError:
        pass
    with _lock:
        if not any(t is thread for t in _OUTLIVED):
            _OUTLIVED.append(thread)
    logger.warning("%s did not stop within its wait at quit; the process will exit without "
                   "waiting for it.", what)
    return True


def any_running() -> bool:
    with _lock:
        _OUTLIVED[:] = [t for t in _OUTLIVED if _is_running(t)]
        return bool(_OUTLIVED)
