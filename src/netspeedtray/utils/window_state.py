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

from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)


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
        screen = window.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            # Keep the whole window on-screen even if it was saved off the edge
            # or on a monitor that is no longer connected.
            x = max(avail.left(), min(x, avail.right() - window.width()))
            y = max(avail.top(), min(y, avail.bottom() - window.height()))
        window.move(x, y)
        return True
    except Exception as e:  # never let a restore failure block opening a window
        logger.debug("restore_window_position(%s) failed: %s", key, e)
        return False


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
