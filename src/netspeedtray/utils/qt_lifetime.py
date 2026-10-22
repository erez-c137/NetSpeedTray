"""
Who destroys a parentless top-level window: Qt, never Python's garbage collector.

A widget created from Python with no Qt parent is OWNED by its Python wrapper: when the wrapper is
freed, sip deletes the C++ widget. When the wrapper sits in a reference cycle (``self.x = self``,
a child holding a bound method of its window, ...), it is freed by the cyclic GC - which runs on
WHATEVER thread happens to allocate at that moment: the stats QThread, the metrics server, the
update janitor. A QWidget destroyed off the GUI thread is undefined behaviour in Qt (timers,
the platform window, pending events) and can crash the process; a test run showed exactly that
(an orphaned Settings page collected during an unrelated test, access violation).

The app's self-deleting top-levels (WA_DeleteOnClose, or ``deleteLater`` on teardown) are
therefore registered with ``hand_to_qt``: a strong reference is held until Qt itself destroys the
widget (its ``destroyed`` signal), so the GC never sees it unreachable, Qt destroys it on the GUI
thread, and the Python side (overridden events, state) lives exactly as long as the window.
"""
from __future__ import annotations

import functools
import logging
from typing import Dict

logger = logging.getLogger("NetSpeedTray.QtLifetime")

# id(widget) -> widget, until Qt destroys it.
_KEPT: Dict[int, object] = {}


def _release(key: int, *_args) -> None:
    _KEPT.pop(key, None)


def hand_to_qt(widget) -> None:
    """Keep ``widget`` alive until Qt destroys it. Only for a widget Qt WILL destroy -
    WA_DeleteOnClose, deleteLater, or a Qt parent - or it is kept forever."""
    if widget is None:
        return
    try:
        from PyQt6 import sip
        if sip.isdeleted(widget):
            return
        key = id(widget)
        if key in _KEPT:
            return
        _KEPT[key] = widget
        # partial over the key only: nothing here refers back to the widget.
        widget.destroyed.connect(functools.partial(_release, key))
    except Exception:
        logger.debug("Could not hand a widget's lifetime to Qt.", exc_info=True)


def kept_count() -> int:
    """How many widgets are waiting for Qt to destroy them (diagnostics/tests)."""
    return len(_KEPT)
