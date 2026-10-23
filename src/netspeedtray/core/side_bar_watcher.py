"""
Side-taskbar watcher (rc.2 W1/W2): keeps the widget clear of app buttons that come and go AFTER
the side-bar placement was decided, and re-places it when the icon alignment flips live.

The placement itself (PositionManager._side_placement_for / choose_side_placement) is decided from a
UI Automation probe of the taskbar's buttons. UIA is the truth but costs a cross-process FindAll
(~10 ms) and, right after a change, reports buttons mid-animation. So the watcher decides WHEN to
re-probe, cheaply:

* Centred icons: an out-of-context EVENT_OBJECT_LOCATIONCHANGE hook, scoped to Explorer's process and
  filtered (on the hook thread) to the taskbar's legacy ``Start`` / ``ReBarWindow32`` /
  ``MSTaskSwWClass`` windows. Explorer still moves the hidden ``Start`` window to the Start button's
  FINAL spot as the group re-centres - 15-30 ms before UIA, and it never lags (Win11 26200, scratchpad
  ``p406live/b2_right_center.txt``). From that edge the group's new span is PREDICTED, and only a
  predicted collision with the widget runs a probe. The far edge of the legacy windows is stale, so
  it is never used as a position - a move of those windows only counts as "a button may have come".
* Top (non-centred) icons, or a centred group already stopped at the bar's end: the group grows
  toward the widget without moving Start. Each foreground change (a new app usually takes it) and
  each move of the legacy task-switch windows adds one button slot to the prediction; while the
  predicted group is within ``SIDE_BAR_NEAR_SLOTS`` slots of the widget the 1 s safety-net tick
  re-probes every ``SIDE_BAR_NEAR_RECHECK_MS`` (and a foreground change once more, 800 ms later).
  That interval doubles, up to ``SIDE_BAR_NEAR_RECHECK_MAX_MS``, while consecutive probes find the
  same span (a widget in a centred bar's top gap is always "near"), and resets on any hook event.
* Alignment (``TaskbarAl``): the WM_SETTINGCHANGE broadcast never arrives on 26200, so the value is
  also read on every coalesced hook burst and on the safety-net tick (one registry read). A flip
  re-decides the whole placement once the re-laid bar has settled.

Every probe keeps the last result until the new one lands (``request_taskbar_layout_probe(...,
keep_last=True)``), is rate-limited, and waits for the animation: a probe whose app group does not yet
start where the legacy Start window already is gets repeated a few times. The result goes back to
PositionManager, which moves the widget only when the fresh layout really collides with it (or the
alignment flipped). A user's drag always wins: collision re-checks are skipped while a drag offset is
in force; an alignment flip only re-clamps it. Nothing is applied while the widget is being dragged or
its menu is open (deferred to the next reposition).

By design, apps CLOSING never move the widget back to a better spot (#200 anti-jitter: a widget that
followed every app would never sit still); a restart, Reset Position, a dock change or an alignment
flip re-decides it.

Never runs for a TOP/BOTTOM taskbar or an ExplorerPatcher-style bar (PositionManager attaches it
only to a native side bar), so those layouts are untouched.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer

from netspeedtray import constants
from netspeedtray.utils import taskbar_utils

logger = logging.getLogger("NetSpeedTray.SideBarWatcher")

Span = Tuple[float, float]   # (top, bottom) along the bar, logical px, bottom exclusive


@dataclass(frozen=True)
class SideWatchContext:
    """What the watcher needs from PositionManager, read fresh each time (None = don't watch now)."""
    hwnd: int
    dpi: float
    widget: Span              # the widget's span along the bar
    user_placed: bool         # the user's drag offset is in force: collisions are theirs to keep


def span_gap(a: Span, b: Span) -> float:
    """Free space between two spans along the bar; negative when they overlap."""
    return max(a[0] - b[1], b[0] - a[1])


def predict_group(model: Span, start_top: Optional[float], assumed_growth: float) -> Span:
    """The app group's span predicted from the last UIA probe (``model``), the legacy Start window's
    CURRENT top edge (exact and early for a centred group) and ``assumed_growth`` - the slots added
    for events that move nothing we can measure.

    Growth is assumed toward BOTH ends: a centred group that grew by ``d`` at Start's end may grow up
    to ``d`` at the far end as well (a symmetric re-centre; live data shows it can also be 0 when the
    far end is pinned), so the prediction errs toward a re-check, never toward a missed collision."""
    top, bottom = model
    grew = 0.0
    if start_top is not None:
        grew = max(top - start_top, 0.0)
        top = min(top, start_top)
    return top, bottom + grew + max(assumed_growth, 0.0)


class SideBarWatcher(QObject):
    """See the module docstring. All timers and callbacks run on the GUI thread; the WinEvent hook
    runs on its own thread and only filters + forwards (WinEventHook)."""

    def __init__(self, get_context: Callable[[], Optional[SideWatchContext]],
                 on_recheck: Callable[[bool], None], parent: Optional[QObject] = None, *,
                 hook_factory: Optional[Callable] = None,
                 read_alignment: Optional[Callable[[], Optional[int]]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self._get_context = get_context
        self._on_recheck = on_recheck            # (full: bool) -> None; PositionManager re-places
        self._hook_factory = hook_factory or _default_hook_factory
        if read_alignment is None:
            from netspeedtray.core.system_events import read_taskbar_alignment
            read_alignment = read_taskbar_alignment
        self._read_alignment = read_alignment
        self._clock = clock
        t = constants.timeouts

        self._hwnd = 0
        self._start_hwnd = 0
        self._task_hwnds: frozenset = frozenset()
        self._hook = None
        self._alignment: Optional[int] = None
        # The last settled UIA probe: the group's span and the Start top it was taken at.
        self._model: Optional[Span] = None
        self._assumed_growth = 0.0
        self._task_moved = False
        # Probe bookkeeping.
        self._scheduled_full = False             # the scheduled / in-flight probe re-decides fully
        self._inflight = False
        self._inflight_full = False
        self._landed_before = 0
        self._last_probe_at = -1e9
        self._retries = 0
        self._again = False                      # another request arrived while one was in flight
        # The near re-check's current interval (backs off while nothing changes - rc.2 M2).
        self._near_interval_ms = t.SIDE_BAR_NEAR_RECHECK_MS
        # Bumped by detach(): a delayed retry from an earlier attachment must not fire (rc.2 L6).
        self._gen = 0

        self._burst_timer = self._timer(t.SIDE_BAR_COALESCE_MS, self._on_burst)
        self._probe_timer = self._timer(0, self._run_probe)
        self._poll_timer = self._timer(t.SIDE_BAR_PROBE_POLL_MS, self._poll_probe)
        self._followup_timer = self._timer(t.SIDE_BAR_FOREGROUND_FOLLOWUP_MS, self._on_followup)

    def _timer(self, ms: int, slot) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ms)
        timer.timeout.connect(slot)
        return timer

    # --- lifecycle -----------------------------------------------------------------------------

    @property
    def attached_hwnd(self) -> int:
        return self._hwnd

    def attach(self, taskbar_hwnd: int) -> None:
        """Watch ``taskbar_hwnd`` (a native side bar). Idempotent: cheap when already attached."""
        taskbar_hwnd = int(taskbar_hwnd or 0)
        if not taskbar_hwnd or taskbar_hwnd == self._hwnd:
            return
        self.detach()
        self._hwnd = taskbar_hwnd
        start, rebar, tasksw = taskbar_utils.find_classic_layout_hwnds(taskbar_hwnd)
        self._start_hwnd = start
        self._task_hwnds = frozenset(h for h in (rebar, tasksw) if h)
        try:
            self._alignment = self._read_alignment()
        except Exception:
            self._alignment = None
        watched = frozenset(h for h in (start, rebar, tasksw) if h)
        if watched:
            try:
                self._hook = self._hook_factory(taskbar_hwnd, watched, self._on_location_event)
            except Exception as e:
                self._hook = None
                logger.warning("Side-bar location hook unavailable (%s); relying on the 1 s check.", e)
        logger.debug("Side-bar watcher attached to taskbar %s (Start %s, task windows %s, hook %s).",
                     taskbar_hwnd, start, sorted(self._task_hwnds), self._hook is not None)

    def detach(self) -> None:
        """Stop watching (the widget left the side bar, Free Move, or the taskbar was re-created)."""
        self._gen += 1
        hook, self._hook = self._hook, None
        if hook is not None:
            try:
                hook.stop()
            except Exception:
                pass
        for timer in (self._burst_timer, self._probe_timer, self._poll_timer, self._followup_timer):
            timer.stop()
        self._hwnd = 0
        self._start_hwnd = 0
        self._task_hwnds = frozenset()
        self._model = None
        self._assumed_growth = 0.0
        self._task_moved = False
        self._scheduled_full = False
        self._inflight = False
        self._again = False
        self._retries = 0
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS

    # --- inputs --------------------------------------------------------------------------------

    def _on_location_event(self, hwnd: int) -> None:
        """A watched legacy window moved (already filtered on the hook thread). Coalesce the burst."""
        if not self._hwnd:
            return
        if int(hwnd or 0) in self._task_hwnds:
            self._task_moved = True
        if not self._burst_timer.isActive():
            self._burst_timer.start()

    def note_foreground_change(self) -> None:
        """A new foreground window: often a new app, whose button may grow the group toward the
        widget without moving Start (Top alignment). Count one slot, re-check if near."""
        ctx = self._context()
        if ctx is None or ctx.user_placed or self._ensure_model(ctx) is None:
            return
        self._assumed_growth += self._slot(ctx)
        if self._near(ctx):
            self._schedule(full=False, delay_ms=0)
            self._followup_timer.start()

    def note_alignment_maybe_changed(self) -> None:
        """WM_SETTINGCHANGE (any) or another hint: compare TaskbarAl and re-decide on a flip."""
        self._check_alignment()

    def on_tick(self) -> None:
        """The widget's 1 s safety-net tick: the alignment value, and the bounded near re-check."""
        if not self._hwnd:
            return
        if self._check_alignment():
            return
        ctx = self._context()
        if ctx is None or ctx.user_placed:
            return
        if (self._near(ctx) and not self._inflight and not self._probe_timer.isActive()
                and (self._clock() - self._last_probe_at) * 1000.0 >= self._near_interval_ms):
            self._schedule(full=False, delay_ms=0)

    def _on_followup(self) -> None:
        ctx = self._context()
        if ctx is not None and not ctx.user_placed and self._near(ctx):
            self._schedule(full=False, delay_ms=0)

    # --- decisions -----------------------------------------------------------------------------

    def _context(self) -> Optional[SideWatchContext]:
        if not self._hwnd:
            return None
        try:
            ctx = self._get_context()
        except Exception as e:
            logger.debug("side-bar watcher context failed: %s", e)
            return None
        if ctx is None or int(ctx.hwnd) != self._hwnd:
            return None
        return ctx

    def _check_alignment(self) -> bool:
        try:
            value = self._read_alignment()
        except Exception:
            value = None
        if value is None or value == self._alignment:
            return False
        first = self._alignment is None
        self._alignment = value
        if first:
            return False
        logger.info("Taskbar icon alignment changed (TaskbarAl=%s): re-deciding the side-bar placement "
                    "once the bar has settled.", value)
        self._model = None
        self._assumed_growth = 0.0
        self._schedule(full=True, delay_ms=constants.timeouts.SIDE_BAR_ALIGNMENT_SETTLE_MS)
        return True

    def _start_top(self, ctx: SideWatchContext) -> Optional[float]:
        """The legacy Start window's top edge, logical px (None when unknown)."""
        rect = _window_rect(self._start_hwnd)
        if rect is None:
            return None
        return rect[1] / (ctx.dpi or 1.0)

    def _slot(self, ctx: SideWatchContext) -> float:
        """One button slot along the bar, logical px: the Start window's length, else Win11's 44 px."""
        rect = _window_rect(self._start_hwnd)
        if rect is not None and rect[3] > rect[1]:
            return (rect[3] - rect[1]) / (ctx.dpi or 1.0)
        return 44.0

    def _ensure_model(self, ctx: SideWatchContext) -> Optional[Span]:
        """The model, seeded from a probe that already landed (the startup placement's)."""
        if self._model is None:
            apps, _widgets, _pending = taskbar_utils.taskbar_layout_snapshot(self._hwnd)
            if apps is not None:
                scale = ctx.dpi or 1.0
                self._model = (apps[1] / scale, apps[3] / scale)
        return self._model

    def _predicted(self, ctx: SideWatchContext) -> Optional[Span]:
        model = self._ensure_model(ctx)
        if model is None:
            return None
        return predict_group(model, self._start_top(ctx), self._assumed_growth)

    def _near(self, ctx: SideWatchContext) -> bool:
        group = self._predicted(ctx)
        if group is None:
            return False
        return span_gap(ctx.widget, group) < constants.timeouts.SIDE_BAR_NEAR_SLOTS * self._slot(ctx)

    def _on_burst(self) -> None:
        """A coalesced burst of legacy-window moves: alignment, then the collision prediction."""
        task_moved, self._task_moved = self._task_moved, False
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS   # something moved
        if self._check_alignment():
            return
        ctx = self._context()
        if ctx is None or ctx.user_placed:
            return
        model = self._ensure_model(ctx)
        if model is None:
            return
        start_top = self._start_top(ctx)
        moved_start = start_top is not None and abs(start_top - model[0]) >= 1.0
        if task_moved and not moved_start:
            # A button came or went, but not at Start's end: assume one more slot at the far end.
            self._assumed_growth += self._slot(ctx)
        group = predict_group(model, start_top, self._assumed_growth)
        margin = constants.layout.DEFAULT_PADDING + self._slot(ctx)
        if span_gap(ctx.widget, group) < margin:
            logger.debug("Side-bar watcher: predicted app group %s within %.0f px of the widget %s - "
                         "re-checking.", group, margin, ctx.widget)
            # Wait for the animation; a further burst pushes the probe back (one probe per burst).
            self._schedule(full=False, delay_ms=constants.timeouts.SIDE_BAR_SETTLE_MS, restart=True)

    # --- probing -------------------------------------------------------------------------------

    def _schedule(self, full: bool, delay_ms: int, restart: bool = False) -> None:
        if not self._hwnd:
            return
        if self._inflight:
            self._again = True
            self._scheduled_full = self._scheduled_full or full
            return
        self._scheduled_full = self._scheduled_full or full
        min_gap_ms = constants.timeouts.SIDE_BAR_MIN_PROBE_INTERVAL_MS
        since_ms = (self._clock() - self._last_probe_at) * 1000.0
        wait = max(int(delay_ms), int(max(0.0, min_gap_ms - since_ms)))
        if self._probe_timer.isActive() and not restart:
            if self._probe_timer.remainingTime() <= wait:
                return
        self._retries = 0
        self._probe_timer.start(max(0, wait))

    def _run_probe(self) -> None:
        if not self._hwnd:
            return
        full, self._scheduled_full = self._scheduled_full, False
        self._fire_probe(full)

    def _fire_probe(self, full: bool) -> None:
        self._inflight = True
        self._inflight_full = full
        self._last_probe_at = self._clock()
        # What "landed" means: the cache's last-landed stamp moves past the one seen now.
        self._landed_before = taskbar_utils.taskbar_layout_landed_count(self._hwnd)
        taskbar_utils.request_taskbar_layout_probe(self._hwnd, keep_last=True)
        self._poll_timer.start()

    def _poll_probe(self) -> None:
        if not self._hwnd or not self._inflight:
            return
        apps, _widgets, pending = taskbar_utils.taskbar_layout_snapshot(self._hwnd)
        if pending:
            self._poll_timer.start()
            return
        full = self._inflight_full
        landed = taskbar_utils.taskbar_layout_landed_count(self._hwnd) > self._landed_before
        if not landed:                       # timed out: the last layout stays, nothing to re-decide
            self._finish(apply=False, full=full)
            return
        ctx = self._context()
        if ctx is not None and apps is not None and not self._settled(ctx) \
                and self._retries < constants.timeouts.SIDE_BAR_MAX_RETRIES:
            self._retries += 1
            self._poll_timer.stop()
            gen = self._gen
            QTimer.singleShot(constants.timeouts.SIDE_BAR_RETRY_MS, lambda: self._retry(full, gen))
            return
        if ctx is not None and apps is not None:
            scale = ctx.dpi or 1.0
            model = (apps[1] / scale, apps[3] / scale)
            t = constants.timeouts
            if self._model is not None and all(abs(a - b) < 1.0 for a, b in zip(model, self._model)):
                self._near_interval_ms = min(self._near_interval_ms * 2, t.SIDE_BAR_NEAR_RECHECK_MAX_MS)
            else:
                self._near_interval_ms = t.SIDE_BAR_NEAR_RECHECK_MS
            self._model = model
            self._assumed_growth = 0.0
        self._finish(apply=True, full=full)

    def _retry(self, full: bool, gen: int) -> None:
        if gen == self._gen and self._hwnd and self._inflight:
            self._fire_probe(full)

    def _settled(self, ctx: SideWatchContext) -> bool:
        """The probe's group starts where the legacy Start window already is (it moves first and
        never lags): otherwise UIA was read mid-animation. True when that can't be told."""
        start = _window_rect(self._start_hwnd)
        group = taskbar_utils.taskbar_layout_group(self._hwnd)
        if start is None or group is None:
            return True
        return abs(group[1] - start[1]) <= 2

    def _finish(self, apply: bool, full: bool) -> None:
        self._inflight = False
        self._retries = 0
        if apply:
            try:
                self._on_recheck(full)
            except Exception as e:
                logger.error("Side-bar re-placement failed: %s", e, exc_info=True)
        if self._again:
            self._again = False
            self._schedule(full=False, delay_ms=0)


def _window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    if not hwnd:
        return None
    try:
        import win32gui
        return taskbar_utils.valid_rect(tuple(win32gui.GetWindowRect(hwnd)))
    except Exception:
        return None


def _default_hook_factory(taskbar_hwnd: int, watched: frozenset, on_event: Callable[[int], None]):
    """The real hook: EVENT_OBJECT_LOCATIONCHANGE, out of context, Explorer's process only, our own
    process skipped, filtered on the hook thread to ``watched`` windows' own moves (OBJID_WINDOW).
    Explorer is quiet otherwise (probe 2026-10-22: 0 events in an idle second, 18 for an app
    opened + closed), so the hook thread wakes only for real taskbar changes."""
    from netspeedtray.utils.win_event_hook import (
        EVENT_OBJECT_LOCATIONCHANGE, OBJID_WINDOW, WinEventHook,
    )
    hook = WinEventHook(EVENT_OBJECT_LOCATIONCHANGE, hwnd_to_watch=int(taskbar_hwnd),
                        source_hwnd_filter=frozenset(watched), skip_own_process=True,
                        object_id_filter=OBJID_WINDOW, process_scope=True)
    hook.event_triggered.connect(on_event)
    hook.start()
    return hook
