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
* on every foreground change, followed by a bounded burst of three follow-ups spaced by
  ``SIDE_BAR_FOREGROUND_FOLLOWUP_MS``. Uncombined buttons can be much wider than a 44 px slot;
  a prediction must not suppress their measurement;
* from the 1 s refresh tick every ``APP_YIELD_ACTIVE_RECHECK_MS`` while yielded or near. Unchanged
  nearby spans keep that cadence for ``APP_YIELD_ACTIVE_WINDOW_MS`` after anything moved, then back
  off (doubling) to ``APP_YIELD_IDLE_RECHECK_MAX_MS``: a background close still brings the widget
  back within seconds, and a bar left crowded for hours costs a probe every 8 s, not every second.
  Unknown layouts while shown still back off up to ``SIDE_BAR_NEAR_RECHECK_MAX_MS``;
* never more than one probe per ``SIDE_BAR_MIN_PROBE_INTERVAL_MS``.

The owner's normal centred bar (11 apps ending at x 1247, widget at 1496) is ~5.6 slots clear: it
never yields and performs no periodic probes after its foreground follow-up burst ends.

A user who DROPS the widget onto the buttons keeps it there: the yield is suppressed until a probe
sees the widget clear once. That choice survives a restart: the dropped ``tray_offset_x`` is saved
(``app_overlap_drop_offset``) while it is in force, and the same offset on the next attach is the
same choice (rc.2 live test H2).

An Explorer restart keeps a yielded widget hidden. While the taskbar is MISSING, #440's hold owns it
(``PositionManager.holding_for_taskbar``: ``_execute_refresh`` hides the widget and returns before
the yield is even synced, up to ``TASKBAR_ABSENT_HOLD_MS``); the yield adds no hold of its own there,
and if #440's hold runs out (still no taskbar) the yield simply lets go. Once the NEW taskbar (a new
HWND) exists, its first probes may find no buttons yet (live C5: ~3 s): a yield is kept through those
unknown readings for up to ``APP_YIELD_NEW_TASKBAR_HOLD_MS`` - one bounded hold per phase, never two
at once - instead of flashing the widget for the seconds the new taskbar takes to fill. On the SAME
taskbar a transient unknown reading (a window storm timing UIA out) keeps a yield too, for up to
``APP_YIELD_UNKNOWN_HOLD_MS`` (rc.2 live run: "yield over (layout unknown)", shown ~5 s, yielded again).

Units: the UIA group is physical px, the widget span logical px; ``group_span`` converts with the
context's ``dpi``, which PositionManager reads from the taskbar's monitor at the moment of judging (a
live scale change: BUSY until its taskbar snapshot agrees, never two scales in one judgement). A scale
change drops the measured group (logical px at the old scale) and re-probes at once.

A group rect that does not fit inside the taskbar's own window rect (read around the probe) is an
unknown reading, not a collision: right after a live scale change Explorer can report its buttons in
a stale scale's coordinates for seconds (rc.2 final VM run: a 10 s false step-aside after
150 -> 100 % on an auto-hide bar, the group read at 1.5x its real position).

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
# position is not where the user will leave it). NO_TASKBAR: no taskbar right now - only seen once
# #440's missing-taskbar hold has run out (it returns before syncing while it holds): let go.
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
    # What re-lays the bar's tray end without a new HWND: (TaskbarAl, tray left in physical px, Widgets
    # on). A change re-probes, so the Widgets box the default spot steps left of (rc.3) never goes stale.
    layout_key: Optional[tuple] = None
    # A Widgets button beside the tray gives the DEFAULT spot two candidates (rc.3): left of the button
    # (``stepped``, the normal one) and the rc.2 spot by the tray (``tray_spot``, on the button's
    # padding). Logical px, the widget's width; both None when they coincide (no such button, a centred
    # bar, a user's drag).
    stepped: Optional[Span] = None
    tray_spot: Optional[Span] = None
    # The tray spot clears the Widgets button's weather TEXT/icon by the usual gap (rc.3 verify: with
    # "Mostly cloudy" the rc.2 spot drew the arrow and "0.0" over the text). False also when the
    # content extent is unknown - hiding is better than colliding: the yield then steps aside instead.
    tray_spot_clear: bool = False


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
    yield on an unknown reading (AppOverlapYield holds a yield through a bounded run of unknowns
    first: APP_YIELD_NEW_TASKBAR_HOLD_MS / APP_YIELD_UNKNOWN_HOLD_MS)."""

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
        self._followups_left = 0
        self._active_until = 0.0                 # the 1 s re-check holds until then (see _mark_active)
        self._near_interval_ms = t.SIDE_BAR_NEAR_RECHECK_MS
        self._first_yield_done = False
        self._suspended = False                  # a second launch asked for the widget: off this session
        self._hold_until = 0.0                   # unknown readings keep a yield until then (0: no hold yet)
        self._model_dpi: Optional[float] = None  # the scale ``_model`` was converted at
        self._persisted_drop: Optional[int] = None
        self._misfit_logged = False              # "buttons outside the taskbar" logged for this run
        self._last_group_rect = None             # the last judged UIA group (physical px), for the log
        self._layout_key: Optional[tuple] = None  # OverlapContext.layout_key at the last (re)probe
        # The full-bar fallback (rc.3): the app buttons reach the stepped spot but not the tray spot, so
        # the widget sits at the tray spot (visible, as in rc.2) instead of yielding. Back to the
        # stepped spot only after two clear readings of it APP_YIELD_SHOW_CONFIRM_MS apart - the same
        # rule as showing after a yield, so the widget never flips between the two spots per probe.
        self._retreated = False
        self._step_clear_since: Optional[float] = None
        self._spot_gone_since: Optional[float] = None   # first retreated reading with no second spot
        self._relayout_burst_at = -1e9           # when a layout change last armed the follow-up burst
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
    def retreated(self) -> bool:
        """True while the default spot is the rc.2 tray spot because the app buttons reach the spot
        left of a Widgets button beside the tray (PositionManager places with widgets_step=False)."""
        return self._enabled and not self._suspended and bool(self._hwnd) and self._retreated

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
        self._retreated = False
        self._step_clear_since = None
        self._spot_gone_since = None
        self._model = None
        self._model_known = False
        self._changes = 0
        self._inflight = False
        self._again = False
        self._followups_left = 0
        self._active_until = 0.0
        self._hold_until = 0.0
        self._model_dpi = None
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
            if self._hwnd:
                self.detach("no taskbar after the missing-taskbar hold")
            return
        if ctx == BUSY:
            return
        if not isinstance(ctx, OverlapContext):   # None, or the reason it must not yield (a string)
            if self._hwnd:
                self.detach(ctx if isinstance(ctx, str) else "the widget is not on a native horizontal taskbar")
            return
        if int(ctx.hwnd) != self._hwnd:
            self._attach(ctx)
            return
        if self._model_known and self._model_dpi is not None and abs(ctx.dpi - self._model_dpi) > 1e-6:
            # A live display-scale change: the measured group is logical px at the OLD scale (and the
            # widget is being re-sized / re-placed for the new one) - forget it and judge afresh now.
            logger.debug("App-button yield: display scale %.3g -> %.3g; re-probing.", self._model_dpi, ctx.dpi)
            self._model = None
            self._model_known = False
            self._model_dpi = None
            self._changes = 0
            # The fallback's pending confirmations were timed against the old scale's readings.
            self._step_clear_since = None
            self._spot_gone_since = None
            self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS
            self._schedule(0)
            return
        if ctx.layout_key is not None and ctx.layout_key != self._layout_key:
            # The alignment flipped, the tray widened / narrowed or Widgets was toggled - all on the
            # SAME taskbar HWND (a Settings change re-lays the bar live). The last probe's Widgets box
            # (and group) no longer describe it: probe once (rate-limited by _schedule).
            had_key = self._layout_key is not None
            self._layout_key = ctx.layout_key
            if had_key:
                logger.debug("App-button yield: taskbar layout inputs changed (%s); re-probing.",
                             ctx.layout_key)
                self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS
                self._schedule(0)
                # ...and the foreground follow-ups: the tray end moves first and the Widgets button
                # SLIDES after it (rc.3 VM: a probe right after an input-language indicator went away
                # caught the button mid-slide, and nothing probed again - the widget sat 39 px short).
                # At most one burst per APP_YIELD_ACTIVE_WINDOW_MS: a tray that flaps (an icon blinking
                # in and out) must not keep a far, centred bar probing 4x per flap.
                now = self._clock()
                if (now - self._relayout_burst_at) * 1000.0 >= constants.timeouts.APP_YIELD_ACTIVE_WINDOW_MS:
                    self._relayout_burst_at = now
                    self._followups_left = constants.timeouts.APP_YIELD_FOREGROUND_FOLLOWUPS
                    self._followup_timer.start()
                return
        if self._inflight or self._probe_timer.isActive():
            return
        # No group known yet (or the last probe found none): retry on the backed-off interval, so a
        # transient UIA failure at startup doesn't switch the yield off for the session.
        # A retreated widget (rc.3 full-bar fallback) sits ~a Widgets button away from the group - past
        # SIDE_BAR_NEAR_SLOTS - yet must notice a background close to return: watched like a yield.
        if not (self._state.yielded or self._retreated or self._model is None or self._near(ctx)):
            return
        # A stale span near the widget is not evidence that the next minute is safe. Likewise a
        # hidden widget must notice background closes promptly, without another foreground event:
        # 1 s while something moved recently, then the bounded idle back-off (_poll_probe).
        t = constants.timeouts
        interval = self._near_interval_ms
        if (self._state.yielded or self._retreated or self._near(ctx)) and self._clock() < self._active_until:
            interval = t.APP_YIELD_ACTIVE_RECHECK_MS
        if (self._clock() - self._last_probe_at) * 1000.0 >= interval - t.APP_YIELD_TICK_SLACK_MS:
            self._schedule(0)

    def _attach(self, ctx: OverlapContext) -> None:
        """A (new) taskbar. A re-created one (Explorer restart: a new HWND) keeps a yield for the hold
        window; a saved "dropped on the buttons" offset that is still in force suppresses it."""
        keep = bool(self._hwnd) and self._state.yielded
        for timer in (self._probe_timer, self._poll_timer, self._followup_timer):
            timer.stop()
        suppressed = self._state.suppressed
        self._state.reset()
        self._retreated = False
        self._step_clear_since = None
        self._spot_gone_since = None
        self._model = None
        self._model_known = False
        self._changes = 0
        self._inflight = False
        self._again = False
        self._followups_left = 0
        self._active_until = 0.0
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS
        self._model_dpi = None
        self._hold_until = 0.0
        self._hwnd = int(ctx.hwnd)
        self._layout_key = ctx.layout_key
        if keep:
            self._state.yielded = True
            self._hold_until = self._clock() + constants.timeouts.APP_YIELD_NEW_TASKBAR_HOLD_MS / 1000.0
            logger.debug("App-button yield: the taskbar was re-created (%s); staying aside until it is "
                         "probed.", self._hwnd)
        if suppressed or (ctx.saved_drop is not None and ctx.saved_drop == ctx.drop_key):
            self._state.suppressed = True
            self._persisted_drop = ctx.saved_drop
        logger.debug("App-button yield watching taskbar %s.", self._hwnd)
        self._schedule(0)

    # --- inputs ------------------------------------------------------------------------------

    def note_foreground_change(self) -> None:
        """Measure after a foreground change, including wide/uncombined app buttons.

        The 44 px prediction is only a proximity hint, not a safe reason to skip this measurement.
        A new labelled button can be wider, and Explorer may finish animating after the first probe.
        """
        ctx = self._context()
        if ctx is None:
            return
        self._changes += 1
        self._mark_active()
        self._followups_left = constants.timeouts.APP_YIELD_FOREGROUND_FOLLOWUPS
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

    def _mark_active(self) -> None:
        """Something moved: re-check every APP_YIELD_ACTIVE_RECHECK_MS for APP_YIELD_ACTIVE_WINDOW_MS."""
        t = constants.timeouts
        self._near_interval_ms = t.APP_YIELD_ACTIVE_RECHECK_MS
        self._active_until = self._clock() + t.APP_YIELD_ACTIVE_WINDOW_MS / 1000.0

    def _on_followup(self) -> None:
        ctx = self._context()
        if ctx is not None and self._followups_left > 0:
            self._followups_left -= 1
            self._schedule(0)
            if self._followups_left:
                self._followup_timer.start()

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
        if group_rect is not None:
            bar_rect = taskbar_utils.taskbar_layout_bar(self._hwnd)
            if not taskbar_utils.group_fits_bar(group_rect, bar_rect):
                # Not in the bar's coordinate space - Explorer mid display-scale change (see
                # APP_GROUP_BAR_FIT_TOLERANCE_PX): an unknown reading, never a reason to yield.
                if not self._misfit_logged:
                    logger.info("App-button yield: the taskbar reported its buttons outside its own "
                                "window (group %s, taskbar %s) - ignoring that reading.", group_rect, bar_rect)
                    self._misfit_logged = True
                group_rect = None
            else:
                self._misfit_logged = False
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
        self._last_group_rect = group_rect
        if landed:
            t = constants.timeouts
            same = (self._model_known and (span is None) == (self._model is None)
                    and (span is None or all(abs(a - b) < 1.0 for a, b in zip(span, self._model))))
            near = (span is not None and span_gap(ctx.widget, span)
                    < t.SIDE_BAR_NEAR_SLOTS * constants.layout.APP_BUTTON_SLOT_PX)
            if self._state.yielded or self._retreated or near:
                if not same:
                    self._mark_active()
                elif self._clock() >= self._active_until:   # unchanged for a while: idle back-off
                    self._near_interval_ms = min(max(self._near_interval_ms, t.APP_YIELD_ACTIVE_RECHECK_MS) * 2,
                                                 t.APP_YIELD_IDLE_RECHECK_MAX_MS)
            elif same:
                self._near_interval_ms = min(self._near_interval_ms * 2, t.SIDE_BAR_NEAR_RECHECK_MAX_MS)
            else:
                self._near_interval_ms = t.SIDE_BAR_NEAR_RECHECK_MS
            self._model = span
            self._model_known = True
            self._model_dpi = ctx.dpi
            self._changes = 0
        widget = ctx.widget
        if landed or span is not None:
            moved = self._judge_fallback(span, ctx, self._fired_at)
            if ctx.stepped is not None and ctx.tray_spot is not None:
                # Judge the spot the widget is (about to be) placed at, not a window still moving.
                widget = ctx.tray_spot if self._retreated else ctx.stepped
            if moved:
                try:
                    self._on_change()
                except Exception as e:
                    logger.error("App-yield placement update failed: %s", e, exc_info=True)
        gap = constants.layout.TRAY_EDGE_MIN_GAP_PX
        if self._state.yielded and ctx.stepped is not None and not self._retreated:
            # Hidden for the stepped spot (no clear fallback): back with the same room to spare as a
            # return from the fallback, so a group end at the threshold can't flash the widget.
            gap += constants.layout.WIDGETS_FALLBACK_RETURN_MARGIN_PX
        reaches = group_reaches_widget(span, widget, gap)
        if reaches is None and self._state.yielded:
            if not self._hold_until:
                # A transient unknown on this taskbar (window storm, UIA timed out): one bounded hold,
                # never a second one after the new-taskbar hold ran out (that one is still set).
                self._hold_until = self._clock() + constants.timeouts.APP_YIELD_UNKNOWN_HOLD_MS / 1000.0
            if self._clock() < self._hold_until:
                # No buttons reported (a re-created taskbar, or a busy one): stay aside, ask again shortly.
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

    def _judge_fallback(self, span: Optional[Span], ctx: OverlapContext, at: float) -> bool:
        """The full-bar fallback (rc.3, owner decision): with a Widgets button beside the tray, app
        buttons that reach the spot left of it while the rc.2 tray spot is still clear put the widget
        at the tray spot instead of hiding it. It returns to the stepped spot after two clear readings
        of that spot APP_YIELD_SHOW_CONFIRM_MS apart. An unknown reading changes nothing. True when the
        spot changed (the caller re-places the widget)."""
        if ctx.stepped is None or ctx.tray_spot is None:
            # No second spot (no button beside the tray any more, a drag, a centred bar): nothing held.
            self._step_clear_since = None
            if not self._retreated:
                return False
            # ...but ONE such reading is no proof: the snapshot behind it can lack the Widgets box for a
            # moment (the layout cache answering for another bar, a probe that missed the button). With
            # no second spot the hold changes nothing about the placement, while dropping it on a
            # transient re-placed the widget at the stepped spot, on the app buttons, until the next
            # (backed-off, up to 8 s) probe retreated again. Let go after two readings, as for a return.
            if self._spot_gone_since is None:
                self._spot_gone_since = at
                self._schedule(self._state.confirm_ms)
                return False
            if (at - self._spot_gone_since) * 1000.0 < self._state.confirm_ms - 0.5:
                self._schedule(max(0, int(self._state.confirm_ms - (at - self._spot_gone_since) * 1000.0)))
                return False
            self._retreated = False
            self._spot_gone_since = None
            logger.info("Widgets-button fallback over: the default spot has no alternative here.")
            return True
        self._spot_gone_since = None
        if span is None:
            return False
        if not ctx.tray_spot_clear:
            # The tray spot would draw over the weather text (or where that text ends is unknown): no
            # fallback - the caller judges the stepped spot, so the yield steps the widget aside.
            changed = self._retreated
            self._retreated = False
            self._step_clear_since = None
            if changed:
                logger.info("The spot by the tray would cover the Widgets button's text: stepping aside "
                            "instead.")
            return changed
        if not self._retreated:
            if group_reaches_widget(span, ctx.stepped) and not group_reaches_widget(span, ctx.tray_spot):
                self._retreated = True
                self._step_clear_since = None
                logger.info("App buttons reach the spot left of the Widgets button (group %.0f..%.0f, "
                            "spot %.0f..%.0f): staying by the tray instead (%.0f..%.0f).", span[0], span[1],
                            ctx.stepped[0], ctx.stepped[1], ctx.tray_spot[0], ctx.tray_spot[1])
                self._mark_active()
                return True
            return False
        # Back only with room to spare (spatial hysteresis): a group end hovering at the threshold
        # must not swing the widget a whole Widgets button left and right on every probe.
        if group_reaches_widget(span, ctx.stepped, constants.layout.TRAY_EDGE_MIN_GAP_PX
                                + constants.layout.WIDGETS_FALLBACK_RETURN_MARGIN_PX):
            self._step_clear_since = None
            return False
        if self._step_clear_since is None:
            self._step_clear_since = at
            self._schedule(self._state.confirm_ms)
            return False
        if (at - self._step_clear_since) * 1000.0 >= self._state.confirm_ms - 0.5:
            self._retreated = False
            self._step_clear_since = None
            logger.info("Room left of the Widgets button again: back to the default spot.")
            self._mark_active()
            return True
        self._schedule(max(0, int(self._state.confirm_ms - (at - self._step_clear_since) * 1000.0)))
        return False

    def _apply(self, action: Optional[str], span: Optional[Span], ctx: OverlapContext) -> None:
        if action == "confirm":
            self._schedule(self._state.confirm_wait_ms(self._clock()))
            return
        if action not in ("yield", "show"):
            return
        if action == "yield":
            logger.info("App buttons reach the widget (group %.0f..%.0f, widget %.0f..%.0f; UIA %s at "
                        "scale %.3g): stepping aside until there is room.", span[0], span[1],
                        ctx.widget[0], ctx.widget[1], self._last_group_rect, ctx.dpi)
        else:
            logger.info("App-button yield over (%s): showing the widget again.",
                        "room again" if span is not None else "layout unknown")
        # Background closes must be noticed promptly while yielded, and a fresh return re-checked.
        self._mark_active()
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
