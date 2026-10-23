"""
App-button yield on a horizontal Win11 taskbar (#24, rc.2 W17/F11).

On a native Win11 TOP/BOTTOM bar the app buttons live in a XAML island: no ``ToolbarWindow32``, so the
classic app-icon safe-zone snap never fires and a left-aligned group that grows toward the tray ends
up under the widget (both illegible). The widget never MOVES for it (#200 anti-jitter: a widget that
followed every app would never sit still). It YIELDS: while the contiguous Start-led button run
(``uia_widgets.app_group_from_elements`` - Start/Search/Task View, the app buttons and the "..."
overflow button, class ``ToggleButton`` / AutomationId ``OverflowButton``) comes within
``TRAY_EDGE_MIN_GAP_PX`` of the widget, the widget hides through the same visibility gate as the
fullscreen hide (``_execute_refresh``), and it comes back after two clear readings at least
``APP_YIELD_SHOW_CONFIRM_MS`` apart (the buttons animate; hiding is immediate). Design of record:
``Docs/releases/v2.2/design-24-app-overlap.md``.

A yielded widget is the app's only surface (no tray icon), so there is always a way back:
Settings > Widget > "Hide while app buttons cover the widget" (``hide_when_apps_cover``) turns the
yield off, and a second launch of the app (#440's activate message) suspends it for the rest of the
session (``suspend_for_session``).

What is judged: the widget's x span - its real position while it is shown, the position it WOULD get
(PositionManager computes it) while it is hidden: a hidden window is never re-placed, so its stale
spot must not keep it hidden (rc.2 live test H3).

When it probes (no new OS hook; one UIA FindAll from the XAML host, ~10 ms on the layout worker):

* on attach (startup, a new taskbar);
* on a foreground change (a new app usually takes the foreground) - counted as one more button on
  the last measured group (half a slot at EACH end of a centred group, a whole slot at the far end of
  a left-aligned one, a whole slot at both ends when the alignment can't be read), and probed (plus
  once more ``SIDE_BAR_FOREGROUND_FOLLOWUP_MS`` later, the button slides in) only when that
  prediction is within ``SIDE_BAR_NEAR_SLOTS`` slots of the widget, or while yielded;
* from the 1 s refresh tick every ``SIDE_BAR_NEAR_RECHECK_MS`` - only while yielded or near - backing
  off (doubling) while probes find the same span (#406's M2): up to ``SIDE_BAR_NEAR_RECHECK_MAX_MS``
  when shown, ``APP_YIELD_YIELDED_RECHECK_MAX_MS`` while yielded (an app closed in the background
  must bring it back quickly);
* never more than one probe per ``SIDE_BAR_MIN_PROBE_INTERVAL_MS``.

The owner's normal centred bar (11 apps ending at x 1247, widget at 1496) is ~5.6 slots clear: it
never yields, and only several foreground changes in a row cost one probe.

A user who DROPS the widget onto the buttons keeps it there: the yield is suppressed until a probe
sees the widget clear once. That choice survives a restart: the dropped ``tray_offset_x`` is saved
(``app_overlap_drop_offset``) while it is in force, and the same offset on the next attach is the
same choice (rc.2 live test H2).

An Explorer restart keeps a yielded widget hidden (the taskbar is missing, then reappears with a new
HWND whose first probes may find no buttons yet) for up to ``APP_YIELD_TASKBAR_GONE_HOLD_MS``, instead
of flashing it for the seconds the new taskbar takes to fill.

Fail-safe: no UIA, a failed / timed-out probe, a bar without a StartButton, an ExplorerPatcher /
Win10 bar (``tasklist_rect`` set), a side bar, Free Move or free-float -> never yield (today's
behaviour). Mirrors (#434) build their PositionManager with the yield disabled: they don't yield in v1.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

from PyQt6.QtCore import QObject, QTimer

from netspeedtray import constants
from netspeedtray.core.side_bar_watcher import span_gap
from netspeedtray.utils import taskbar_utils

logger = logging.getLogger("NetSpeedTray.AppOverlapYield")

Span = Tuple[float, float]   # (left, right) along the bar, logical px, right exclusive

# Context sentinels. BUSY: the widget is dragged / its menu is open - nothing is decided (a mid-drag
# position is not where the user will leave it). NO_TASKBAR: no taskbar right now (Explorer
# restarting) - a yielded widget holds its state for a bounded time.
BUSY = "busy"
NO_TASKBAR = "no_taskbar"


@dataclass(frozen=True)
class OverlapContext:
    """What the yield needs from PositionManager, read fresh each time."""
    hwnd: int
    dpi: float
    widget: Span                       # the widget's x span (real, or computed while hidden), logical px
    centered: Optional[bool] = None    # TaskbarAl: True centred, False left, None unknown
    drop_key: Optional[int] = None     # the user's drag offset (tray_offset_x) when one is in force
    saved_drop: Optional[int] = None   # the drop offset saved as "on the buttons, keep it" (H2)


def group_span(group_rect, dpi: float) -> Optional[Span]:
    """The app group's x span in logical px from its physical UIA rect, or None."""
    rect = taskbar_utils.valid_rect(group_rect)
    if rect is None:
        return None
    scale = dpi if dpi and dpi > 0 else 1.0
    return rect[0] / scale, rect[2] / scale


def group_reaches_widget(group: Optional[Span], widget: Span,
                         gap: float = constants.layout.TRAY_EDGE_MIN_GAP_PX) -> Optional[bool]:
    """True when the group is within ``gap`` of the widget (or overlaps it); None when unknown."""
    if group is None:
        return None
    return span_gap(widget, group) < gap


def predict_group(model: Span, changes: int, centered: Optional[bool],
                  slot: float = constants.layout.APP_BUTTON_SLOT_PX) -> Span:
    """The group after ``changes`` foreground changes, each assumed to add one button: a centred group
    re-centres (half a slot at each end), a left-aligned one grows at its far end only; unknown
    alignment assumes a whole slot at both ends (errs toward a probe)."""
    left, right = model
    n = max(int(changes), 0)
    if centered is True:
        return left - n * slot / 2.0, right + n * slot / 2.0
    if centered is False:
        return left, right + n * slot
    return left - n * slot, right + n * slot


class YieldHysteresis:
    """Pure yield state: hide on the first reading that reaches the widget, show only after two
    clear readings ``confirm_ms`` apart, never yield while suppressed by a user drop, and drop the
    yield on an unknown reading (today's behaviour)."""

    def __init__(self, confirm_ms: int = constants.timeouts.APP_YIELD_SHOW_CONFIRM_MS) -> None:
        self.confirm_ms = confirm_ms
        self.yielded = False
        self.suppressed = False          # the user dropped the widget onto the buttons
        self.clear_since: Optional[float] = None

    def observe(self, reaches: Optional[bool], at: float) -> Optional[str]:
        """Feed one reading taken at ``at`` (seconds). Returns "yield", "show", "confirm" (a second
        clear reading is needed - ask again in ``confirm_wait_ms(at)``) or None (nothing changes)."""
        if reaches is None:
            self.clear_since = None
            if self.yielded:
                self.yielded = False
                return "show"
            return None
        if reaches:
            self.clear_since = None
            if self.suppressed or self.yielded:
                return None
            self.yielded = True
            return "yield"
        self.suppressed = False          # seen clear once: the drop baseline is spent
        if not self.yielded:
            return None
        if self.clear_since is None:
            self.clear_since = at
            return "confirm"
        if (at - self.clear_since) * 1000.0 >= self.confirm_ms - 0.5:   # 0.5 ms: float clock jitter
            self.clear_since = None
            self.yielded = False
            return "show"
        return "confirm"

    def confirm_wait_ms(self, now: float) -> int:
        if self.clear_since is None:
            return self.confirm_ms
        return max(0, int(self.confirm_ms - (now - self.clear_since) * 1000.0))

    def reset(self) -> None:
        self.yielded = False
        self.suppressed = False
        self.clear_since = None


ContextResult = Union[OverlapContext, str, None]


class AppOverlapYield(QObject):
    """See the module docstring. GUI thread only (the UIA probe runs on taskbar_utils' worker)."""

    def __init__(self, get_context: Callable[[], ContextResult], on_change: Callable[[], None],
                 on_first_yield: Optional[Callable[[], None]] = None, parent: Optional[QObject] = None,
                 *, on_persist_drop: Optional[Callable[[Optional[int]], None]] = None,
                 enabled: bool = True, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self._get_context = get_context
        self._on_change = on_change              # the yield flipped: re-run the visibility gate
        self._on_first_yield = on_first_yield    # once per session; the widget keeps the ever-flag
        self._on_persist_drop = on_persist_drop  # save / clear the "dropped on the buttons" offset
        self._enabled = bool(enabled)            # False: a mirror's PositionManager (#434) - inert
        self._clock = clock
        t = constants.timeouts
        self._hwnd = 0
        self._state = YieldHysteresis()
        self._model: Optional[Span] = None       # the last measured group span (logical px)
        self._model_known = False                # a probe has landed (its group may be None)
        self._changes = 0                        # foreground changes since the last landed probe
        self._inflight = False
        self._fired_at = 0.0
        self._landed_before = 0
        self._last_probe_at = -1e9
        self._again = False
        self._near_interval_ms = t.SIDE_BAR_NEAR_RECHECK_MS
        self._first_yield_done = False
        self._suspended = False                  # a second launch asked for the widget: off this session
        self._hold_until = 0.0                   # unknown readings keep a yield until then (Explorer restart)
        self._gone_since: Optional[float] = None
        self._persisted_drop: Optional[int] = None
        self._probe_timer = self._timer(0, self._fire_probe)
        self._poll_timer = self._timer(t.SIDE_BAR_PROBE_POLL_MS, self._poll_probe)
        self._followup_timer = self._timer(t.SIDE_BAR_FOREGROUND_FOLLOWUP_MS, self._on_followup)

    def _timer(self, ms: int, slot) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ms)
        timer.timeout.connect(slot)
        return timer

    # --- state -------------------------------------------------------------------------------

    @property
    def yielded(self) -> bool:
        return self._enabled and not self._suspended and bool(self._hwnd) and self._state.yielded

    @property
    def attached_hwnd(self) -> int:
        return self._hwnd

    @property
    def suspended(self) -> bool:
        return self._suspended

    def detach(self, reason: str = "the widget is not on a native horizontal taskbar") -> None:
        """Stop watching. Never yielded afterwards - the caller's visibility gate reads ``yielded``
        right after. ``reason`` is only for the log line (written when a yield ends here)."""
        for timer in (self._probe_timer, self._poll_timer, self._followup_timer):
            timer.stop()
        if self._hwnd and self._state.yielded:
            logger.info("App-button yield off: %s.", reason)
        self._hwnd = 0
        self._state.reset()
        self._model = None
        self._model_known = False
        self._changes = 0
        self._inflight = False
        self._again = False
        self._gone_since = None
        self._hold_until = 0.0
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS

    def suspend_for_session(self) -> bool:
        """A second launch asked for the widget: no yield for the rest of this session. True when it
        was yielded (the caller then explains why it had stepped aside)."""
        was = self.yielded
        self._suspended = True
        self.detach("NetSpeedTray was started again - staying visible for this session")
        return was

    def sync(self) -> None:
        """The 1 s refresh tick (and every refresh): attach / detach from the current context, then
        the bounded re-check while yielded or near."""
        if not self._enabled or self._suspended:
            return
        ctx = self._raw_context()
        if ctx == NO_TASKBAR:
            self._hold_without_taskbar()
            return
        self._gone_since = None
        if ctx == BUSY:
            return
        if not isinstance(ctx, OverlapContext):   # None, or the reason it must not yield (a string)
            if self._hwnd:
                self.detach(ctx if isinstance(ctx, str) else "the widget is not on a native horizontal taskbar")
            return
        if int(ctx.hwnd) != self._hwnd:
            self._attach(ctx)
            return
        if self._inflight or self._probe_timer.isActive():
            return
        # No group known yet (or the last probe found none): retry on the backed-off interval, so a
        # transient UIA failure at startup doesn't switch the yield off for the session.
        if not (self._state.yielded or self._model is None or self._near(ctx)):
            return
        if (self._clock() - self._last_probe_at) * 1000.0 >= self._near_interval_ms:
            self._schedule(0)

    def _attach(self, ctx: OverlapContext) -> None:
        """A (new) taskbar. A re-created one (Explorer restart: a new HWND) keeps a yield for the hold
        window; a saved "dropped on the buttons" offset that is still in force suppresses it."""
        keep = bool(self._hwnd) and self._state.yielded
        for timer in (self._probe_timer, self._poll_timer, self._followup_timer):
            timer.stop()
        suppressed = self._state.suppressed
        self._state.reset()
        self._model = None
        self._model_known = False
        self._changes = 0
        self._inflight = False
        self._again = False
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS
        self._hwnd = int(ctx.hwnd)
        if keep:
            self._state.yielded = True
            self._hold_until = self._clock() + constants.timeouts.APP_YIELD_TASKBAR_GONE_HOLD_MS / 1000.0
            logger.debug("App-button yield: the taskbar was re-created (%s); staying aside until it is "
                         "probed.", self._hwnd)
        if suppressed or (ctx.saved_drop is not None and ctx.saved_drop == ctx.drop_key):
            self._state.suppressed = True
            self._persisted_drop = ctx.saved_drop
        self._gone_since = None
        logger.debug("App-button yield watching taskbar %s.", self._hwnd)
        self._schedule(0)

    def _hold_without_taskbar(self) -> None:
        """No taskbar (Explorer restarting): keep a yield for a bounded time, then give up on it."""
        if not self._hwnd:
            return
        now = self._clock()
        if self._gone_since is None:
            self._gone_since = now
            for timer in (self._probe_timer, self._poll_timer, self._followup_timer):
                timer.stop()
            self._inflight = False
        if not self._state.yielded or (now - self._gone_since) * 1000.0 >= constants.timeouts.APP_YIELD_TASKBAR_GONE_HOLD_MS:
            self.detach("the taskbar went away")

    # --- inputs ------------------------------------------------------------------------------

    def note_foreground_change(self) -> None:
        """A new foreground window, often a new app: one more button on the group; probe if near."""
        ctx = self._context()
        if ctx is None or not self._model_known:
            return
        self._changes += 1
        if self._state.yielded or self._near(ctx):
            self._schedule(0)
            self._followup_timer.start()

    def note_user_drop(self) -> None:
        """The user dropped the widget: if that is ON the buttons it's their choice - no yield until
        a probe sees the widget clear once (saved while in force, so a restart keeps it)."""
        if not self._hwnd:
            return
        self._state.suppressed = True
        self._state.clear_since = None
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS
        self._schedule(0)

    def note_position_reset(self) -> None:
        """Reset Position: the widget is back by the tray; judge it afresh (a saved drop is dropped -
        a stale one elsewhere can never match: the default offset is never a drop key)."""
        self._persist_drop(None)
        if not self._hwnd:
            return
        self._state.suppressed = False
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS
        self._schedule(0)

    def _on_followup(self) -> None:
        ctx = self._context()
        if ctx is not None and (self._state.yielded or self._near(ctx)):
            self._schedule(0)

    # --- decisions ---------------------------------------------------------------------------

    def _raw_context(self) -> ContextResult:
        try:
            return self._get_context()
        except Exception as e:
            logger.debug("app-overlap context failed: %s", e)
            return None

    def _context(self) -> Optional[OverlapContext]:
        """The context for THIS taskbar, None when busy / not watching."""
        if not self._hwnd or not self._enabled or self._suspended:
            return None
        ctx = self._raw_context()
        if not isinstance(ctx, OverlapContext) or int(ctx.hwnd) != self._hwnd:
            return None
        return ctx

    def _near(self, ctx: OverlapContext) -> bool:
        if self._model is None:
            return False
        grown = predict_group(self._model, self._changes, ctx.centered)
        slot = constants.layout.APP_BUTTON_SLOT_PX
        return span_gap(ctx.widget, grown) < constants.timeouts.SIDE_BAR_NEAR_SLOTS * slot

    def _persist_drop(self, value: Optional[int], force: bool = False) -> None:
        if not force and value == self._persisted_drop:
            return
        self._persisted_drop = value
        if self._on_persist_drop is not None:
            try:
                self._on_persist_drop(value)
            except Exception as e:
                logger.debug("saving the drop baseline failed: %s", e)

    # --- probing -----------------------------------------------------------------------------

    def _schedule(self, delay_ms: int) -> None:
        if not self._hwnd:
            return
        if self._inflight:
            self._again = True
            return
        min_gap_ms = constants.timeouts.SIDE_BAR_MIN_PROBE_INTERVAL_MS
        since_ms = (self._clock() - self._last_probe_at) * 1000.0
        wait = max(int(delay_ms), int(max(0.0, min_gap_ms - since_ms)))
        if self._probe_timer.isActive() and self._probe_timer.remainingTime() <= wait:
            return
        self._probe_timer.start(max(0, wait))

    def _fire_probe(self) -> None:
        if not self._hwnd or self._inflight:
            return
        self._inflight = True
        self._fired_at = self._last_probe_at = self._clock()
        self._landed_before = taskbar_utils.taskbar_layout_landed_count(self._hwnd)
        # keep_last: the last group answers until this one lands (never the lagging HWND rect).
        taskbar_utils.request_taskbar_layout_probe(self._hwnd, keep_last=True)
        self._poll_timer.start()

    def _poll_probe(self) -> None:
        if not self._hwnd or not self._inflight:
            return
        _apps, _widgets, pending = taskbar_utils.taskbar_layout_snapshot(self._hwnd)
        if pending:
            self._poll_timer.start()
            return
        self._inflight = False
        landed = taskbar_utils.taskbar_layout_landed_count(self._hwnd) > self._landed_before
        group_rect = taskbar_utils.taskbar_layout_group(self._hwnd) if landed else None
        raw = self._raw_context()
        if raw == BUSY:
            self._again = True               # judge it once the drag / menu is over
            self._finish()
            return
        ctx = raw if (isinstance(raw, OverlapContext) and int(raw.hwnd) == self._hwnd) else None
        if ctx is None:
            self._finish()
            return
        span = group_span(group_rect, ctx.dpi) if landed else None
        if landed:
            t = constants.timeouts
            same = (self._model_known and (span is None) == (self._model is None)
                    and (span is None or all(abs(a - b) < 1.0 for a, b in zip(span, self._model))))
            cap = t.APP_YIELD_YIELDED_RECHECK_MAX_MS if self._state.yielded else t.SIDE_BAR_NEAR_RECHECK_MAX_MS
            self._near_interval_ms = (min(self._near_interval_ms * 2, cap)
                                      if same else t.SIDE_BAR_NEAR_RECHECK_MS)
            self._model = span
            self._model_known = True
            self._changes = 0
        reaches = group_reaches_widget(span, ctx.widget)
        if reaches is None and self._state.yielded and self._clock() < self._hold_until:
            # A re-created taskbar with no buttons reported yet: stay aside, ask again shortly.
            self._finish()
            self._schedule(constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS // 3)
            return
        if reaches is not None:
            self._hold_until = 0.0
        suppressed_before = self._state.suppressed
        self._apply(self._state.observe(reaches, self._fired_at), span, ctx)
        if suppressed_before and reaches is True and ctx.drop_key is not None:
            self._persist_drop(ctx.drop_key)          # on the buttons by choice: keep it after a restart
        elif reaches is False and (self._persisted_drop is not None or ctx.saved_drop is not None):
            self._persist_drop(None, force=True)      # seen clear: the baseline is spent
        self._finish()

    def _apply(self, action: Optional[str], span: Optional[Span], ctx: OverlapContext) -> None:
        if action == "confirm":
            self._schedule(self._state.confirm_wait_ms(self._clock()))
            return
        if action not in ("yield", "show"):
            return
        if action == "yield":
            logger.info("App buttons reach the widget (group %.0f..%.0f, widget %.0f..%.0f): stepping "
                        "aside until there is room.", span[0], span[1], ctx.widget[0], ctx.widget[1])
            # While yielded the re-check backs off only up to the shorter cap.
            self._near_interval_ms = min(self._near_interval_ms,
                                         constants.timeouts.APP_YIELD_YIELDED_RECHECK_MAX_MS)
        else:
            logger.info("App-button yield over (%s): showing the widget again.",
                        "room again" if span is not None else "layout unknown")
        try:
            self._on_change()
        except Exception as e:
            logger.error("App-yield visibility update failed: %s", e, exc_info=True)
        if action == "yield" and not self._first_yield_done and self._on_first_yield is not None:
            self._first_yield_done = True
            try:
                self._on_first_yield()
            except Exception as e:
                logger.debug("first-yield notice failed: %s", e)

    def _finish(self) -> None:
        if self._again:
            self._again = False
            self._schedule(0)
