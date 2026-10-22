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

When it probes (no new OS hook; one UIA FindAll from the XAML host, ~10 ms on the layout worker):

* on attach (startup, a new taskbar);
* on a foreground change (a new app usually takes the foreground) - counted as one more button slot
  on the last measured group, and probed (plus once more ``SIDE_BAR_FOREGROUND_FOLLOWUP_MS`` later,
  the button slides in) only when that prediction is within ``SIDE_BAR_NEAR_SLOTS`` slots of the
  widget, or while yielded;
* from the 1 s refresh tick every ``SIDE_BAR_NEAR_RECHECK_MS`` - only while yielded or near - backing
  off (doubling up to ``SIDE_BAR_NEAR_RECHECK_MAX_MS``) while probes find the same span (#406's M2);
* never more than one probe per ``SIDE_BAR_MIN_PROBE_INTERVAL_MS``.

A centred group grows at both ends (half a slot each per app), so the prediction grows both ends by a
whole slot - it errs toward a probe, never toward a missed collision. The owner's normal centred bar
(11 apps ending at x 1247, widget at 1496) is ~5.6 slots clear: it never yields and only a few
foreground changes in a row cost one probe.

Fail-safe: no UIA, a failed / timed-out probe, a bar without a StartButton, an ExplorerPatcher /
Win10 bar (``tasklist_rect`` set), a side bar, Free Move or free-float -> never yield (today's
behaviour). A user who DROPS the widget onto the buttons keeps it there: the yield is suppressed until
a probe sees the widget clear once.
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

# Returned by the context getter while the widget is dragged / its menu is open: nothing is decided
# then (a mid-drag position is not where the user will leave it), but the watcher stays attached.
BUSY = "busy"


@dataclass(frozen=True)
class OverlapContext:
    """What the yield needs from PositionManager, read fresh each time."""
    hwnd: int
    dpi: float
    widget: Span              # the widget's x span, logical px


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
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self._get_context = get_context
        self._on_change = on_change              # the yield flipped: re-run the visibility gate
        self._on_first_yield = on_first_yield    # once per session; the widget keeps the ever-flag
        self._clock = clock
        t = constants.timeouts
        self._hwnd = 0
        self._state = YieldHysteresis()
        self._model: Optional[Span] = None       # the last measured group span (logical px)
        self._model_known = False                # a probe has landed (its group may be None)
        self._growth = 0.0                       # slots assumed for foreground changes since
        self._inflight = False
        self._fired_at = 0.0
        self._landed_before = 0
        self._last_probe_at = -1e9
        self._again = False
        self._near_interval_ms = t.SIDE_BAR_NEAR_RECHECK_MS
        self._first_yield_done = False
        self._gen = 0
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
        return bool(self._hwnd) and self._state.yielded

    @property
    def attached_hwnd(self) -> int:
        return self._hwnd

    def detach(self) -> None:
        """Stop watching (not a native horizontal bar any more, Free Move, shutdown). Never yielded
        afterwards - the caller's visibility gate reads ``yielded`` right after."""
        self._gen += 1
        for timer in (self._probe_timer, self._poll_timer, self._followup_timer):
            timer.stop()
        if self._state.yielded:
            logger.info("App-button yield off (the widget is no longer on a native horizontal bar).")
        self._hwnd = 0
        self._state.reset()
        self._model = None
        self._model_known = False
        self._growth = 0.0
        self._inflight = False
        self._again = False
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS

    def sync(self) -> None:
        """The 1 s refresh tick (and every refresh): attach / detach from the current context, then
        the bounded re-check while yielded or near."""
        ctx = self._raw_context()
        if ctx is None:
            if self._hwnd:
                self.detach()
            return
        if ctx == BUSY:
            return
        if int(ctx.hwnd) != self._hwnd:
            self.detach()
            self._hwnd = int(ctx.hwnd)
            logger.debug("App-button yield watching taskbar %s.", self._hwnd)
            self._schedule(0)
            return
        if self._inflight or self._probe_timer.isActive():
            return
        # No group known yet (or the last probe found none): retry on the backed-off interval, so a
        # transient UIA failure at startup doesn't switch the yield off for the session.
        if not (self._state.yielded or self._model is None or self._near(ctx)):
            return
        if (self._clock() - self._last_probe_at) * 1000.0 >= self._near_interval_ms:
            self._schedule(0)

    # --- inputs ------------------------------------------------------------------------------

    def note_foreground_change(self) -> None:
        """A new foreground window, often a new app: one more slot on the group; probe if near."""
        ctx = self._context()
        if ctx is None or not self._model_known:
            return
        self._growth += constants.layout.APP_BUTTON_SLOT_PX
        if self._state.yielded or self._near(ctx):
            self._schedule(0)
            self._followup_timer.start()

    def note_user_drop(self) -> None:
        """The user dropped the widget: if that is ON the buttons it's their choice - no yield until
        a probe sees the widget clear once."""
        if not self._hwnd:
            return
        self._state.suppressed = True
        self._state.clear_since = None
        self._near_interval_ms = constants.timeouts.SIDE_BAR_NEAR_RECHECK_MS
        self._schedule(0)

    def note_position_reset(self) -> None:
        """Reset Position: the widget is back by the tray; judge it afresh."""
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
        if not self._hwnd:
            return None
        ctx = self._raw_context()
        if ctx is None or ctx == BUSY or int(ctx.hwnd) != self._hwnd:
            return None
        return ctx

    def _near(self, ctx: OverlapContext) -> bool:
        if self._model is None:
            return False
        grown = (self._model[0] - self._growth, self._model[1] + self._growth)
        slot = constants.layout.APP_BUTTON_SLOT_PX
        return span_gap(ctx.widget, grown) < constants.timeouts.SIDE_BAR_NEAR_SLOTS * slot

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
        ctx = raw if (raw is not None and int(raw.hwnd) == self._hwnd) else None
        if ctx is None:
            self._finish()
            return
        span = group_span(group_rect, ctx.dpi) if landed else None
        if landed:
            t = constants.timeouts
            same = (self._model_known and (span is None) == (self._model is None)
                    and (span is None or all(abs(a - b) < 1.0 for a, b in zip(span, self._model))))
            self._near_interval_ms = (min(self._near_interval_ms * 2, t.SIDE_BAR_NEAR_RECHECK_MAX_MS)
                                      if same else t.SIDE_BAR_NEAR_RECHECK_MS)
            self._model = span
            self._model_known = True
            self._growth = 0.0
        reaches = group_reaches_widget(span, ctx.widget)
        self._apply(self._state.observe(reaches, self._fired_at), span, ctx)
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
