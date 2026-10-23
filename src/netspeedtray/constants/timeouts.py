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
    # Quitting while Settings > Advanced > Back up history runs (#334): its SQLite statement is
    # interrupted and its thread gets this long to delete the unfinished copy (+ -journal) and end.
    # A copy is read-only on the live file, so a thread still running past this loses nothing.
    HISTORY_BACKUP_CANCEL_WAIT_MS: Final[int] = 1500
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
    # Burial recovery (#314, #238): the minimum spacing of the widget's Z-order checks when
    # EVENT_OBJECT_REORDER / foreground changes arrive in a burst. The first change is checked
    # at once; the rest of a burst collapses into one check at the end of the window.
    # One frame at 60 Hz (pasharm, PR #424 Win10 report): a maximize / restore fires a burst of
    # 6-15 desktop reorders, and the burial itself lands 10-20 ms in - AFTER the leading-edge
    # check found the widget still on top. At 100 ms that burial waited for the trailing edge:
    # a steady ~80 ms blink. At 16 ms it is re-seated within a frame (his run: median 6.2 ms,
    # max 17.6 ms, no backoff, no errors). The cost is at most ~60 checks a second during a
    # sustained reorder storm instead of ~10; one check is a few GetWindow calls (a dozen-step
    # Z-order walk), and a quiet desktop fires no events at all.
    ZORDER_RECHECK_MIN_INTERVAL_MS: Final[int] = 16
    # Burial "fight window" (pasharm, PR #327 rc.1 report on Win10): only a burial found within
    # this long of OUR last re-seat is the shell fighting back and is charged to the re-seat
    # backoff. A real fight re-buries within a frame or two (~16-33 ms) and is checked at the
    # latest at the coalescer's trailing edge (within ZORDER_RECHECK_MIN_INTERVAL_MS of the
    # shell's reorder, ~50 ms after our re-seat at worst); the rc.1 live fight probe re-buries on
    # a 0.2 s poll. A user
    # can't bury it that fast again: every maximize / restore buries the widget on Win10, and the
    # shortest re-seat -> user burial gap in his runs was ~420 ms on rc.1 and 0.30 s with the
    # 16 ms coalescer while toggling deliberately fast. Anything later is a fresh burial:
    # re-seated at once, free.
    BURIAL_FIGHT_WINDOW_MS: Final[int] = 250
    # Fresh-burial rate cap (#424 review): something that re-buries the widget just OUTSIDE the
    # fight window (260-400 ms after each re-seat), or a 1-frame fight seen only by the 1 s tick
    # (no Z-order hook), would re-seat for free ~1-3x a second forever. More than
    # BURIAL_FRESH_RATE_MAX fresh burials within BURIAL_FRESH_RATE_WINDOW_MS is not a person: the
    # next one is charged as a fight, and stays so until the widget holds on top for the healthy
    # stretch. pasharm's real Win10 maximize/restore pace: up to 10 burials in a 90 s run, bursts
    # of 4 in ~1.7 s - a user would have to toggle every ~1.25 s for 15 s straight to reach it.
    # (His deliberately fast 16 ms-coalescer test peaked at exactly 12 in 15 s: still free.)
    # A tick-only fight trips it after ~12 s, a 300 ms re-burier after ~4 s.
    BURIAL_FRESH_RATE_MAX: Final[int] = 12
    BURIAL_FRESH_RATE_WINDOW_MS: Final[int] = 15000
    # The one INFO "recovered from taskbar burial" line per episode is meant for a REAL burial
    # (#327 note, rc.2 W8). The widget is also buried for a moment when it first docks (startup,
    # an Explorer restart) and when a tray icon arrives - the shell re-lays the taskbar and the
    # tray rect changes within the next ~1 s refresh. A recovery within this long of either is
    # logged at DEBUG and leaves the INFO line for a later burial; an INFO line logged this close
    # BEFORE a tray change is handed back the same way.
    BURIAL_LOG_SETTLE_MS: Final[int] = 5000
    # Side-taskbar re-placement (core/side_bar_watcher, rc.2 W1/W2). Live data, Win11 26200 right bar
    # (scratchpad p406live/b2_right_center.txt): opening an app moves the legacy Start window to its
    # FINAL spot at once; UI Automation reports the new button 15-30 ms later and then animates it for
    # another ~300-550 ms. A burst of LOCATIONCHANGE events is coalesced for SIDE_BAR_COALESCE_MS
    # (hook probe 2026-10-22, bottom bar: one app = 2-4 events within ~5 ms, sometimes a second step
    # ~0.5 s later; Explorer is otherwise silent), and the UIA re-check runs SIDE_BAR_SETTLE_MS after the last one, so it measures the
    # settled layout. A re-check whose app-group start still disagrees with the legacy Start window
    # (mid-animation) is repeated after SIDE_BAR_RETRY_MS, at most SIDE_BAR_MAX_RETRIES times.
    SIDE_BAR_COALESCE_MS: Final[int] = 40
    SIDE_BAR_SETTLE_MS: Final[int] = 600
    SIDE_BAR_RETRY_MS: Final[int] = 250
    SIDE_BAR_MAX_RETRIES: Final[int] = 3
    # Explorer re-lays the whole bar (with a longer animation) after an icon-alignment flip.
    SIDE_BAR_ALIGNMENT_SETTLE_MS: Final[int] = 1200
    # How often the landed probe is looked for (GUI thread; the probe itself is ~10 ms on its worker).
    SIDE_BAR_PROBE_POLL_MS: Final[int] = 50
    # Never more than one UIA layout probe per this interval (~10 ms cross-process each).
    SIDE_BAR_MIN_PROBE_INTERVAL_MS: Final[int] = 500
    # A non-centred group (Top alignment, or a centred one already stopped at the bar's end) grows
    # toward the widget WITHOUT moving Start, so nothing in the hook sees it: re-check after a
    # foreground change (a new app usually takes the foreground) and once more after
    # SIDE_BAR_FOREGROUND_FOLLOWUP_MS (its button slides in), and from the 1 s safety-net tick every
    # SIDE_BAR_NEAR_RECHECK_MS - only while the group is within SIDE_BAR_NEAR_SLOTS button slots of
    # the widget (#24 design, 2026-10-22).
    SIDE_BAR_FOREGROUND_FOLLOWUP_MS: Final[int] = 800
    SIDE_BAR_NEAR_RECHECK_MS: Final[int] = 3000
    # ...doubling (3 -> 6 -> 12 ... s) while consecutive re-checks find the same app span, up to this,
    # and back to SIDE_BAR_NEAR_RECHECK_MS on any hook event or a changed span. A widget parked in
    # the top gap of a centred bar is always "near" (rc.2 live test M2: a probe every ~3.3 s forever).
    SIDE_BAR_NEAR_RECHECK_MAX_MS: Final[int] = 60000
    # The FIRST side-bar placement of a dock waits this long on the GUI thread for its UIA probe
    # (~10-50 ms when Explorer is healthy), so the widget appears once at the right spot instead of
    # at the lagging HWND rect's spot and hopping ~1 s later (rc.2 L1). Bounded: a hung Explorer
    # costs at most this, once, and the provisional decision is re-done as soon as the probe lands.
    SIDE_BAR_FIRST_LAYOUT_WAIT_MS: Final[int] = 250
    SIDE_BAR_NEAR_SLOTS: Final[int] = 3
    TASKBAR_RESTART_RECOVERY_DELAY_MS: Final[int] = 1000
    TASKBAR_RESTART_RETRIES: Final[int] = 5
    # Explorer restart (rc.2 W5): between the old taskbar going away and the new one existing there
    # is NO taskbar at all, and the widget used to float at the screen-fallback spot (bottom-right,
    # off the bar) for ~2 s. A docked widget that has had a real taskbar hides and keeps its last
    # position for up to this long instead; the new taskbar re-shows it (#419 then places it from the
    # last good tray geometry). Past it (Explorer gone for good, a replacement shell) the old
    # behaviour applies: shown at the fallback spot. A restart takes ~2-5 s; a crashed Explorer that
    # Windows relaunches can take longer.
    TASKBAR_ABSENT_HOLD_MS: Final[int] = 15000
    # ...and it hides only from this many CONSECUTIVE no-taskbar refreshes (~1 s apart): the first one
    # just holds the position, so a single transient failed lookup can't flicker it (#440 review L5).
    TASKBAR_ABSENT_HIDE_AFTER_TICKS: Final[int] = 2

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
