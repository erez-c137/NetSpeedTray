"""
Give back the memory Qt's font database grew while Settings or the Monitor was open.

Qt keeps every family it has populated - and once anything resolves a fallback chain, the styles of
EVERY installed family (~38 MB on a machine with ~320 families) - for the life of the process. The
widget itself never needs that (#487: one face, known script runs), but the Settings and Monitor
windows can trigger it (the font picker, QSS family lists, text a face lacks).

Qt has no public "forget" call, but registering an application font invalidates the whole database
(QFontDatabasePrivate::invalidate: font cache, fallback cache and every populated family cleared),
and removing it invalidates it again. Adding and removing a font that is already installed - a
system font file - changes nothing anyone can see, and leaves a database that re-populates lazily,
only what the next paint asks for. Measured (#487 spike): 51 -> 15 MB in isolation; in the app
Settings-closed 100.7 -> 68.2 MB; the widget's next paints do not reload it; 10 cycles, no leak.

The trade-off: Qt's font cache is cleared, so every open window rebuilds its fonts once on its next
paint. That is why the reset only runs after the window is gone, deferred to the event loop, and
is skipped while another of those windows is still open.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from PyQt6.QtCore import QTimer

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.FontRelease")

# Fonts every Windows install ships; the first that exists is the probe. Marlett is tiny (the
# caption-button glyph font) and nothing in the app draws with it.
_PROBE_FILES = ("marlett.ttf", "segoeui.ttf", "arial.ttf", "tahoma.ttf")

_state: dict = {"timer": None, "still_needed": None}


def _probe_font_path() -> Optional[str]:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    for name in _PROBE_FILES:
        path = os.path.join(root, "Fonts", name)
        if os.path.isfile(path):
            return path
    return None


def release_font_database() -> bool:
    """Reset Qt's font database now (add + remove an installed font). True when it ran.

    Never raises. Not for the widget's paint path: the next paint of every open window rebuilds
    its fonts."""
    try:
        from PyQt6.QtGui import QFontDatabase
        path = _probe_font_path()
        if path is None:
            logger.debug("Font release skipped: no probe font found.")
            return False
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id < 0:
            logger.debug("Font release skipped: Qt refused the probe font %s.", path)
            return False
        QFontDatabase.removeApplicationFont(font_id)
        logger.debug("Qt font database released (probe %s).", os.path.basename(path))
        return True
    except Exception as e:
        logger.debug("Font release failed: %s", e, exc_info=True)
        return False


def _run_scheduled() -> None:
    check = _state["still_needed"]
    _state["still_needed"] = None
    try:
        if check is not None and check():
            logger.debug("Font release skipped: Settings or the Monitor is open.")
            return
    except Exception as e:
        logger.debug("Font release skipped: the open-window check failed: %s", e)
        return
    release_font_database()


def schedule_font_release(still_needed: Callable[[], bool],
                          delay_ms: Optional[int] = None) -> None:
    """Run release_font_database() on the event loop ``delay_ms`` from now, unless ``still_needed()``
    says a window that uses the full database (Settings, the Monitor) is open by then. A call made
    while one is pending restarts it (the latest ``still_needed`` is used)."""
    if delay_ms is None:
        delay_ms = constants.timeouts.FONT_RELEASE_DELAY_MS
    try:
        timer = _state["timer"]
        if timer is None:
            timer = QTimer()            # one per process, module-owned; never on a widget's thread
            timer.setSingleShot(True)
            timer.timeout.connect(_run_scheduled)
            _state["timer"] = timer
        _state["still_needed"] = still_needed
        timer.start(max(0, int(delay_ms)))
    except Exception as e:
        logger.debug("Could not schedule the font release: %s", e)


def release_pending() -> bool:
    """A release is scheduled and has not run yet (tests / diagnostics)."""
    timer = _state["timer"]
    return timer is not None and timer.isActive()


def cancel_pending_release() -> None:
    """Drop a scheduled release (app shutdown, tests)."""
    timer = _state["timer"]
    if timer is not None:
        timer.stop()
    _state["still_needed"] = None
