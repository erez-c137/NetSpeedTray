"""
GUI-thread stall logger (#218).

The widget is an OWNED window of Shell_TrayWnd (the Z-order fix, core/position_manager.py), and
Windows attaches the input queues of an owner/owned pair's threads: any stall on our GUI thread can
delay the taskbar's own clicks (a tray right-click that opens late). The steady state is clean
(a message round trip p99 2.8 ms); this catches the occasional stall in the field.

How, without a timer or thread of its own:

- The heartbeat is the widget's existing 1 s safety-net tick (``_state_watcher_timer``); ``on_tick``
  is connected to it ahead of the refresh. No timer, no thread of its own. Each tick measures its
  own lateness against the interval with ``time.monotonic()``; a tick ``GUI_STALL_LATE_MS`` late is
  one WARNING line: how long, and which of our windows were open (Monitor + tab, Settings, flyouts,
  menus) - class names only, never titles.
- To see WHAT the thread was doing, every tick re-arms ``faulthandler.dump_traceback_later`` (via
  ``crash_log.arm_stall_dump``) on the crash log. Its C watchdog thread blocks on a timed lock wait
  and never wakes while ticks keep re-arming it; when the GUI thread is stuck for
  ``GUI_STALL_DUMP_TIMEOUT_SEC`` it writes every thread's stack there once ("Timeout (...)!") and
  the late tick's log line points at it - only when the crash log really holds a new "Timeout (...)!"
  block since that arm (``crash_log.stall_dump_written``), never inferred from the clock. Cost: each
  re-arm ends the previous watchdog thread and starts a new one (~0.23 ms of process CPU per tick,
  two extra thread dispatches a second, all inside the tick that is already running). Re-arming
  every tick keeps the detection exact (a dump means "stuck GUI_STALL_DUMP_TIMEOUT_SEC since the
  last tick"); every 2nd tick would halve that cost but make the threshold depend on the phase.
- The first tick after start / resume only sets the baseline and arms: a slow startup is never
  dumped before the watch has announced itself in the crash log.
- A gap over ``GUI_STALL_CLOCK_JUMP_SEC`` is sleep without a notice (Modern Standby), a clock jump or
  a very long hang: one INFO line, not a stall, fresh baseline.
- Lines are rate-limited (``GUI_STALL_LOG_MAX_PER_WINDOW`` per ``GUI_STALL_LOG_WINDOW_SEC``); the
  rest are counted and summarised with the worst one. Dumps are capped too
  (``GUI_STALL_DUMP_MAX_PER_WINDOW`` / ``_PER_SESSION``): past the cap the watchdog stays disarmed. Sleep pauses the watch (``suspend``/
  ``resume`` from WM_POWERBROADCAST), so a night asleep is not a stall and the watchdog is not left
  armed across it.

Everything here is best effort: a failure is swallowed, never the tick's problem.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from netspeedtray import constants, crash_log


class GuiStallWatch:
    def __init__(self, interval_ms: int, *,
                 clock: Callable[[], float] = time.monotonic,
                 arm: Optional[Callable[[float], bool]] = None,
                 cancel: Optional[Callable[[], None]] = None,
                 note: Optional[Callable[[str], None]] = None,
                 dump_written: Optional[Callable[[], bool]] = None,
                 context: Optional[Callable[[], str]] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        t = constants.timeouts
        self._interval_s = interval_ms / 1000.0
        self._late_s = t.GUI_STALL_LATE_MS / 1000.0
        self._dump_s = float(t.GUI_STALL_DUMP_TIMEOUT_SEC)
        self._max_lines = t.GUI_STALL_LOG_MAX_PER_WINDOW
        self._window_s = float(t.GUI_STALL_LOG_WINDOW_SEC)
        self._suspend_hold_s = float(t.GUI_STALL_SUSPEND_HOLD_SEC)
        self._jump_s = float(t.GUI_STALL_CLOCK_JUMP_SEC)
        self._dump_max_window = t.GUI_STALL_DUMP_MAX_PER_WINDOW
        self._dump_max_session = t.GUI_STALL_DUMP_MAX_PER_SESSION
        self._clock = clock
        self._arm_fn = arm or crash_log.arm_stall_dump
        self._cancel_fn = cancel or crash_log.cancel_stall_dump
        self._note_fn = note or crash_log.note
        self._dump_written_fn = dump_written or crash_log.stall_dump_written
        self._context = context
        self._log = logger or logging.getLogger(f"{constants.app.APP_NAME}.GuiStallWatch")

        self._running = False
        self._last: Optional[float] = None         # the previous tick (None: next tick is a baseline)
        self._armed_at: Optional[float] = None     # when the watchdog was last armed (None: not armed)
        self._suspended_at: Optional[float] = None
        self._announced = False
        # Rate limit: lines logged in the current window, and stalls counted but not logged.
        self._window_start: Optional[float] = None
        self._window_lines = 0
        self._window_dumps = 0
        self._session_dumps = 0
        self._suppressed = 0
        self._suppressed_worst_ms = 0
        # Session totals (one INFO line at stop).
        self.stall_count = 0
        self._worst_ms = 0

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._running = True
        self._last = None
        self._suspended_at = None
        # Baseline only: the first tick arms (and announces), so a slow startup is never dumped.

    def stop(self) -> None:
        """Quit: disarm first (the quit's own bounded waits must not dump), then summarise."""
        was_running, self._running = self._running, False
        self._cancel()
        if not was_running:
            return
        try:
            self._flush_suppressed()
            if self.stall_count:
                self._log.info("GUI stalls this session: %d (worst %d ms).", self.stall_count, self._worst_ms)
        except Exception:
            pass

    def suspend(self) -> None:
        """The PC is going to sleep: disarm and ignore ticks until resume (or the hold runs out)."""
        if not self._running:
            return
        self._cancel()
        self._suspended_at = self._clock()
        self._last = None

    def resume(self) -> None:
        """Awake again: the next tick is a fresh baseline and re-arms."""
        if not self._running:
            return
        self._suspended_at = None
        self._last = None

    # ------------------------------------------------------------------ the heartbeat

    def on_tick(self) -> None:
        if not self._running:
            return
        try:
            now = self._clock()
            if self._suspended_at is not None:
                if now - self._suspended_at < self._suspend_hold_s:
                    self._last = None
                    return
                self._suspended_at = None   # no resume notice came: watch again from here
                self._last = now
                self._arm()
                return
            last, self._last = self._last, now
            self._roll_window(now)
            if last is not None:
                gap_s = now - last
                late_s = gap_s - self._interval_s
                if gap_s > self._jump_s:
                    self._on_gap(gap_s)
                elif late_s >= self._late_s:
                    self._on_stall(int(round(late_s * 1000)), self._dump_happened())
            self._arm()
        except Exception:
            pass

    # ------------------------------------------------------------------ internals

    def _dump_happened(self) -> bool:
        """Did the armed watchdog really write a dump? Read from the crash log, not the clock."""
        if self._armed_at is None:
            return False
        try:
            return bool(self._dump_written_fn())
        except Exception:
            return False

    def _count_dump(self) -> None:
        self._window_dumps += 1
        self._session_dumps += 1

    def _on_gap(self, gap_s: float) -> None:
        """Sleep without a notice (Modern Standby), a clock jump, or a very long hang: not a stall.
        A dump written meanwhile still counts toward the caps (it is in the crash log)."""
        dumped = self._dump_happened()
        if dumped:
            self._count_dump()
        self._log.info("GUI heartbeat gap of %d s (sleep, a clock jump or a hang) - not counted as a "
                       "stall%s", int(gap_s),
                       "; a stack dump was written meanwhile, see %s" % crash_log.CRASH_LOG_FILENAME
                       if dumped else "")

    def _on_stall(self, late_ms: int, dumped: bool) -> None:
        self.stall_count += 1
        self._worst_ms = max(self._worst_ms, late_ms)
        if dumped:
            self._count_dump()
            self._note("GUI stall of %d ms ended (the Timeout block above)" % late_ms)
        if self._window_lines >= self._max_lines:
            self._suppressed += 1
            self._suppressed_worst_ms = max(self._suppressed_worst_ms, late_ms)
            return
        self._window_lines += 1
        where = "?"
        if self._context is not None:
            try:
                where = self._context() or "-"
            except Exception:
                where = "?"
        self._log.warning(
            "GUI thread stalled: heartbeat %d ms late (open: %s)%s", late_ms, where,
            " - stacks in %s" % crash_log.CRASH_LOG_FILENAME if dumped else "")

    def _roll_window(self, now: float) -> None:
        if self._window_start is None:
            self._window_start = now
        elif now - self._window_start >= self._window_s:
            self._flush_suppressed()
            self._window_start = now
            self._window_lines = 0
            self._window_dumps = 0

    def _flush_suppressed(self) -> None:
        if self._suppressed:
            self._log.warning("GUI thread stalled %d more times, not logged (cap %d lines per %d min; worst %d ms).",
                              self._suppressed, self._max_lines, int(self._window_s // 60),
                              self._suppressed_worst_ms)
            self._suppressed = 0
            self._suppressed_worst_ms = 0

    def _arm(self) -> None:
        if (self._window_dumps >= self._dump_max_window
                or self._session_dumps >= self._dump_max_session):
            if self._armed_at is not None:
                self._cancel()
            return
        try:
            ok = self._arm_fn(self._dump_s)
        except Exception:
            ok = False
        self._armed_at = self._clock() if ok else None
        if ok and not self._announced:
            self._announced = True
            self._note("GUI stall watchdog on (%.1f s): a 'Timeout (...)!' block below is the stacks of a "
                       "stuck GUI thread, not a crash" % self._dump_s)

    def _cancel(self) -> None:
        self._armed_at = None
        try:
            self._cancel_fn()
        except Exception:
            pass

    def _note(self, text: str) -> None:
        try:
            self._note_fn(text)
        except Exception:
            pass
