"""
Timeouts and Intervals Constants Module.

This module defines constant values for various timeouts, intervals, and sleep durations
used throughout the application to avoid magic numbers.
"""

from typing import Final


class TimeoutConstants:
    """Defines all timeout values used across the application."""
    # Database related timeouts (seconds)
    DB_FLUSH_BATCH_SYNC_SLEEP: Final[float] = 0.1
    DB_BUSY_TIMEOUT_MS: Final[int] = 250
    # "Delete all history": the WAL checkpoint that moves the VACUUMed file back into the .db returns
    # busy while a reader (the Monitor's graph worker) is mid-query. Retry briefly on the worker thread,
    # then keep retrying on the worker's idle tick until it lands.
    DB_CHECKPOINT_RETRIES: Final[int] = 5
    DB_CHECKPOINT_RETRY_SLEEP: Final[float] = 0.2
    DB_COMPACT_RETRY_INTERVAL_SEC: Final[float] = 5.0
    # A database backup "Delete all history" could not remove (held open, read-only, AV): retried with
    # exponential backoff from DB_COMPACT_RETRY_INTERVAL_SEC up to this cap, at most
    # DB_BACKUP_RETRY_MAX_ATTEMPTS times a session; after that, at the next start (the "2" marker).
    DB_BACKUP_RETRY_MAX_SEC: Final[float] = 3600.0
    DB_BACKUP_RETRY_MAX_ATTEMPTS: Final[int] = 8
    # How long Settings waits for the worker's answer before giving the Delete button back.
    DB_DELETE_HISTORY_UI_TIMEOUT_MS: Final[int] = 120000
    # Opening the history file at startup: how long one attempt waits for a write lock another process
    # holds. Five attempts (backing off) bound a held lock at ~12 s before the session goes read-only;
    # once open, normal writes wait the usual 5 s.
    DB_INIT_LOCK_TIMEOUT_MS: Final[int] = 2000
    # How long quitting waits for the database thread (a v7 -> v8 migration or the first maintenance
    # pass can still be running). Past it the thread is detached and the process exits without
    # destroying it - a hard exit there is what the migration's journal and kill tests are built for.
    DB_SHUTDOWN_WAIT_MS: Final[int] = 15000

    # System Event related intervals (milliseconds)
    TASKBAR_VALIDITY_CHECK_INTERVAL_MS: Final[int] = 3000
    STATE_WATCHER_INTERVAL_MS: Final[int] = 1000
    # Fast poll purely for the fullscreen-HIDE case: an app fullscreening while
    # already focused fires no foreground event, so without this the hide waits
    # for the 1s state-watcher tick (a visible ~1s lag). Cheap: short-circuits
    # before any taskbar query unless the foreground window is actually fullscreen.
    FULLSCREEN_POLL_INTERVAL_MS: Final[int] = 250
    TASKBAR_RESTART_RECOVERY_DELAY_MS: Final[int] = 1000
    TASKBAR_RESTART_RETRIES: Final[int] = 5

    # Widget / UI Delays (milliseconds)
    WIDGET_INIT_DELAY_MS: Final[int] = 500
    GRAPH_CLOSE_REFRESH_DELAY_MS: Final[int] = 300
    
    # Thread / App Lifecycle (milliseconds/seconds)
    APP_CLOSE_WAIT_MS: Final[int] = 2000
    MONITOR_THREAD_STOP_WAIT_MS: Final[int] = 1000
    # Closing the Monitor (or quitting with it open): how long to wait for the graph worker thread.
    # It first gets GRAPH_THREAD_QUIT_WAIT_MS to finish on its own; then its in-flight SQLite query is
    # interrupted and it gets GRAPH_THREAD_STOP_WAIT_MS more. Past that it is detached (kept
    # referenced, never destroyed while running) instead of blocking the UI - this wait used to be
    # unbounded.
    GRAPH_THREAD_QUIT_WAIT_MS: Final[int] = 700
    GRAPH_THREAD_STOP_WAIT_MS: Final[int] = 2000
    DB_INITIALIZATION_RETRY_DELAY_SEC: Final[float] = 2.0

    # Hardware monitoring subprocess timeouts (seconds). 0.5s was too tight -
    # a cold nvidia-smi can exceed it, intermittently dropping temp/power.
    NVIDIA_SMI_TIMEOUT_SEC: Final[float] = 1.5

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate that all timeouts are positive."""
        for attr_name in dir(self):
            if attr_name.endswith(("_MS", "_SLEEP", "_SEC")):
                value = getattr(self, attr_name)
                if not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"{attr_name} must be a non-negative number.")


# Singleton instance for easy access
timeouts = TimeoutConstants()
