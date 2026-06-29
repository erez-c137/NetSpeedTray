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

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

# Debounce delay before a move is persisted, so dragging a window doesn't write
# the config file on every pixel of movement.
_MOVE_SAVE_DEBOUNCE_MS = 500

# Grace period after a window is shown before move-saving arms. Some window
# managers / DPI setups emit a frame-adjustment Move right after show() that is
# not user-driven; ignoring moves during this window avoids saving a drifted
# position the user never chose.
_MOVE_SAVE_ARM_MS = 400


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
        self._armed = False  # moves are ignored until shortly after the window is shown
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_MOVE_SAVE_DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush)
        window.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        et = event.type()
        if et == QEvent.Type.Show:
            # Re-arm a moment after each show so the WM's initial frame-adjustment
            # Move isn't saved as the user's chosen position.
            self._armed = False
            QTimer.singleShot(_MOVE_SAVE_ARM_MS, self._arm)
        elif et == QEvent.Type.Move and self._armed and self._window.isVisible():
            # Only persist user-driven moves once the window is on screen and armed;
            # this also skips the programmatic move() done while restoring on open.
            self._timer.start()
        return False

    def _arm(self) -> None:
        self._armed = True

    def _flush(self) -> None:
        save_window_position(self._window, self._main_widget, self._key)


def attach_position_memory(window: QWidget, main_widget: Optional[QWidget], key: str) -> _PositionMemory:
    """Auto-save ``window``'s position to ``config[key]`` whenever the user moves it
    (debounced). Pair with :func:`restore_window_position` on open for full memory.

    Returns the filter (parented to ``window``); callers can ignore the return value.
    """
    return _PositionMemory(window, main_widget, key)


def restore_window_geometry(window: QWidget, config: Dict[str, Any], key: str) -> bool:
    """Like :func:`restore_window_position` but also restores SIZE and maximized/fullscreen state.
    Reads ``config[key]`` as ``{x, y, w, h, maximized}`` (a superset of the position-only shape, so it
    transparently upgrades an older position-only save). Applies size first, clamps the position onto a
    live screen, then re-maximizes if that's how the window was left. Returns ``True`` if applied."""
    try:
        saved = config.get(key)
        if not isinstance(saved, dict):
            return False
        x, y = saved.get("x"), saved.get("y")
        if x is None or y is None:
            return False
        x, y = int(x), int(y)
        w, h = saved.get("w"), saved.get("h")
        if w and h:
            window.resize(int(w), int(h))   # min size (setMinimumSize) still wins, so this stays safe
        screen = QApplication.screenAt(QPoint(x, y)) or window.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = max(avail.left(), min(x, avail.right() - window.width()))
            y = max(avail.top(), min(y, avail.bottom() - window.height()))
        window.move(x, y)
        if saved.get("maximized"):
            # Setting the state before show() makes the window open maximized; un-maximizing then
            # returns to the (x, y, w, h) normal geometry restored above. A freshly-built window is in
            # the Normal state, so setting Maximized directly is enough (no need to OR prior flags).
            window.setWindowState(Qt.WindowState.WindowMaximized)
        return True
    except Exception as e:
        logger.debug("restore_window_geometry(%s) failed: %s", key, e)
        return False


def save_window_geometry(window: QWidget, main_widget: Optional[QWidget], key: str) -> None:
    """Persist ``window``'s position + size + maximized state to ``config[key]``. While maximized we
    store the NORMAL (un-maximized) geometry so un-maximizing restores the right size, plus the
    maximized flag so the next open re-maximizes. No-op on any failure (safe from closeEvent)."""
    try:
        if main_widget is None:
            return
        cfg = getattr(main_widget, "config", None)
        mgr = getattr(main_widget, "config_manager", None)
        if cfg is None or mgr is None:
            return
        maximized = bool(window.isMaximized() or window.isFullScreen())
        if maximized:
            g = window.normalGeometry()
            x, y, w, h = g.x(), g.y(), g.width(), g.height()
        else:
            # Save the FRAME top-left (pos()), NOT geometry()'s CLIENT top-left. restore_window_geometry
            # repositions with move(), which sets the frame top-left — geometry().y() is the client top
            # (frame + title-bar), so mixing the two drifts the window DOWN by the title-bar height on
            # every reopen. pos() <-> move() is the consistent, drift-free round-trip. Size stays the
            # client size (what resize() expects).
            p = window.pos()
            s = window.geometry()
            x, y, w, h = p.x(), p.y(), s.width(), s.height()
        cfg[key] = {"x": x, "y": y, "w": w, "h": h, "maximized": maximized}
        mgr.save(cfg)
    except Exception as e:
        logger.debug("save_window_geometry(%s) failed: %s", key, e)


class _GeometryMemory(QObject):
    """Like :class:`_PositionMemory` but debounce-saves full geometry (pos + size + maximized) on
    move, resize, AND maximize/restore. Parented to the window; armed shortly after show so the WM's
    initial frame adjustment isn't mistaken for the user's choice."""

    def __init__(self, window: QWidget, main_widget: Optional[QWidget], key: str) -> None:
        super().__init__(window)
        self._window = window
        self._main_widget = main_widget
        self._key = key
        self._armed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_MOVE_SAVE_DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush)
        window.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        et = event.type()
        if et == QEvent.Type.Show:
            self._armed = False
            QTimer.singleShot(_MOVE_SAVE_ARM_MS, self._arm)
        elif (et in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.WindowStateChange)
              and self._armed and self._window.isVisible()):
            self._timer.start()
        return False

    def _arm(self) -> None:
        self._armed = True

    def _flush(self) -> None:
        save_window_geometry(self._window, self._main_widget, self._key)


def attach_geometry_memory(window: QWidget, main_widget: Optional[QWidget], key: str) -> _GeometryMemory:
    """Auto-save ``window``'s geometry (pos + size + maximized) whenever the user moves, resizes, or
    maximizes it (debounced). Pair with :func:`restore_window_geometry` on open for full memory."""
    return _GeometryMemory(window, main_widget, key)


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
