"""
Timer utilities for NetSpeedTray.

Provides reusable functions for timer management, including interval calculations,
timer creation, and cleanup. These utilities are used across the application to ensure
consistent timer handling with proper error management and logging.

Key features:
- Calculates timer intervals from update rates with smart mode support.
- Creates and configures QTimer instances with callbacks.
- Safely cleans up timers to prevent resource leaks.

This module is intended for low-level timer operations and can be used by any component
in the application that needs to create or manage timers. For managing the speed update
timer specifically, use the `SpeedTimerManager` class in `timer_manager.py`.
"""

from typing import Optional, Callable
import logging
from PyQt6.QtCore import QTimer, QObject

from ..constants.constants import TimerConstants

logger = logging.getLogger("NetSpeedTray.TimerUtils")


def calculate_timer_interval(update_rate: float) -> int:
    """
    Calculate a timer interval in milliseconds based on the desired update rate.

    The update rate is converted to milliseconds, ensuring it meets the minimum interval
    defined in `TimerConstants.MINIMUM_INTERVAL_MS`. If the update rate is 0, the function
    returns the smart mode interval defined in `TimerConstants.SMART_MODE_INTERVAL_MS`.

    Args:
        update_rate: The desired update frequency in seconds. Use 0 to enable smart mode.

    Returns:
        int: The calculated interval in milliseconds, capped at the minimum value.

    Raises:
        ValueError: If `update_rate` is negative.

    Examples:
        >>> calculate_timer_interval(1.0)
        1000
        >>> calculate_timer_interval(0)
        500  # Assuming SMART_MODE_INTERVAL_MS is 500
    """
    if update_rate < 0:
        logger.error("Update rate cannot be negative: %.2f", update_rate)
        raise ValueError(f"Update rate cannot be negative: {update_rate}")

    if update_rate == 0:
        interval = TimerConstants.SMART_MODE_INTERVAL_MS
        logger.debug("Using smart mode interval: %dms", interval)
    else:
        interval = max(TimerConstants.MINIMUM_INTERVAL_MS, int(update_rate * 1000))
        logger.debug("Calculated timer interval: %dms from update_rate %.2fs", interval, update_rate)
    return interval


def create_timer(parent: QObject, callback: Callable[[], None], interval: int, single_shot: bool = False) -> QTimer:
    """
    Create and configure a QTimer instance with the specified callback and interval.

    The timer is configured to emit its `timeout` signal to the provided callback. The
    interval is set in milliseconds, and the timer can be configured as single-shot or
    repeating.

    Args:
        parent: The parent QObject for the timer, ensuring proper memory management.
        callback: The function to call when the timer triggers.
        interval: The timer interval in milliseconds (must be non-negative).
        single_shot: If True, the timer runs once; if False, it repeats indefinitely.

    Returns:
        QTimer: The configured timer instance, ready to be started.

    Raises:
        ValueError: If `interval` is negative or `callback` is not callable.
        RuntimeError: If timer creation or configuration fails due to Qt or system issues.

    Examples:
        >>> parent = QObject()
        >>> def callback(): print("Tick")
        >>> timer = create_timer(parent, callback, 1000)
        >>> timer.interval()
        1000
    """
    if not callable(callback):
        logger.error("Callback must be callable, got %s", type(callback))
        raise ValueError(f"Callback must be callable, got {type(callback)}")
    if interval < 0:
        logger.error("Interval cannot be negative: %d", interval)
        raise ValueError(f"Interval cannot be negative: {interval}")

    try:
        timer = QTimer(parent)
        timer.timeout.connect(callback)
        timer.setInterval(interval)
        timer.setSingleShot(single_shot)
        logger.debug("Timer created with interval %dms, single_shot=%s", interval, single_shot)
        return timer
    except Exception as e:
        logger.error("Failed to create timer: %s", e)
        raise RuntimeError(f"Failed to create timer: {e}")


def cleanup_timer(timer: Optional[QTimer]) -> None:
    """
    Safely clean up a QTimer by stopping it, disconnecting its signals, and scheduling it for deletion.

    This function ensures that the timer is stopped, all slots are disconnected from its `timeout`
    signal, and the timer is scheduled for deletion using `deleteLater()` to prevent memory leaks
    and dangling connections.

    Args:
        timer: The QTimer to stop and delete. If None, the function does nothing.

    Raises:
        RuntimeError: If timer cleanup fails unexpectedly (e.g., due to Qt or system issues).
    """
    if timer is None:
        logger.debug("No timer provided for cleanup")
        return

    try:
        if timer.isActive():
            timer.stop()
            logger.debug("Stopped timer")
        try:
            timer.timeout.disconnect()
            logger.debug("Disconnected all slots from timer")
        except TypeError:
            logger.debug("No slots were connected to timer")
        timer.deleteLater()
        logger.debug("Timer scheduled for deletion")
    except Exception as e:
        logger.error("Failed to clean up timer: %s", e, exc_info=True)
        raise RuntimeError(f"Failed to clean up timer: {e}")