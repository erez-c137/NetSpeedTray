"""
Shared helpers to persist and restore top-level window positions across sessions.

Used by SettingsDialog, GraphWindow, and AppActivityWindow so all three remember
where the user last left them. Positions are stored in the app config under a
per-window key (e.g. ``settings_window_pos``) as ``{"x": int, "y": int}`` and are
always clamped back onto a currently-available screen on restore, so a window
saved on a now-disconnected monitor still opens on-screen.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

# Debounce delay before a move is persisted, so dragging a window doesn't write
# the config file on every pixel of movement.
_MOVE_SAVE_DEBOUNCE_MS = 500


def restore_window_position(window: QWidget, config: Dict[str, Any], key: str) -> bool:
    """Move ``window`` to the saved ``{x, y}`` at ``config[key]``, clamped to its
    current screen's available geometry.

    Returns ``True`` if a valid saved position was found and applied, so callers
    can fall back to centering when it returns ``False``.
    """
    try:
        saved = config.get(key)
        if not isinstance(saved, dict):
            return False
        x, y = saved.get("x"), saved.get("y")
        if x is None or y is None:
            return False
        x, y = int(x), int(y)
        # Resolve the target screen from the SAVED point, not window.screen(): an
        # unshown top-level widget reports the PRIMARY screen, so clamping to that
        # would yank a position saved on a secondary monitor back onto the primary
        # one. Fall back to the window's screen / primary only when the saved point
        # is on no currently-connected monitor (e.g. that display was unplugged).
        screen = QApplication.screenAt(QPoint(x, y)) or window.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            # Keep the whole window on the resolved screen even if it was saved off
            # the edge or on a monitor that is no longer connected.
            x = max(avail.left(), min(x, avail.right() - window.width()))
            y = max(avail.top(), min(y, avail.bottom() - window.height()))
        window.move(x, y)
        return True
    except Exception as e:  # never let a restore failure block opening a window
        logger.debug("restore_window_position(%s) failed: %s", key, e)
        return False


class _PositionMemory(QObject):
    """Event filter that debounce-saves a window's position whenever it moves.

    Installed via :func:`attach_position_memory`. Parented to the window, so it is
    torn down with it. The debounce means a drag persists once it settles, not on
    every intermediate move event.
    """

    def __init__(self, window: QWidget, main_widget: Optional[QWidget], key: str) -> None:
        super().__init__(window)
        self._window = window
        self._main_widget = main_widget
        self._key = key
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_MOVE_SAVE_DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush)
        window.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Only persist user-driven moves once the window is actually on screen;
        # this skips the programmatic move() done while restoring/centering on open.
        if event.type() == QEvent.Type.Move and self._window.isVisible():
            self._timer.start()
        return False

    def _flush(self) -> None:
        save_window_position(self._window, self._main_widget, self._key)


def attach_position_memory(window: QWidget, main_widget: Optional[QWidget], key: str) -> _PositionMemory:
    """Auto-save ``window``'s position to ``config[key]`` whenever the user moves it
    (debounced). Pair with :func:`restore_window_position` on open for full memory.

    Returns the filter (parented to ``window``); callers can ignore the return value.
    """
    return _PositionMemory(window, main_widget, key)


def save_window_position(window: QWidget, main_widget: Optional[QWidget], key: str) -> None:
    """Persist ``window``'s current top-left to ``config[key]`` via the main
    widget's ``ConfigManager``.

    A no-op (logged at debug) on any failure, so it can be called freely from a
    ``closeEvent`` without risk of blocking the close.
    """
    try:
        if main_widget is None:
            return
        cfg = getattr(main_widget, "config", None)
        mgr = getattr(main_widget, "config_manager", None)
        if cfg is None or mgr is None:
            return
        pos = window.pos()
        cfg[key] = {"x": pos.x(), "y": pos.y()}
        mgr.save(cfg)
    except Exception as e:
        logger.debug("save_window_position(%s) failed: %s", key, e)
