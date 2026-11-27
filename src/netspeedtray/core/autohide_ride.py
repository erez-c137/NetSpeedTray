"""
Auto-hide taskbar ride-along (#135).

With an auto-hide taskbar the widget used to notice the bar had gone only on the 1 s state-watcher
tick, so it lingered on the desktop after the bar slid away. The widget now hooks the bar's
LOCATIONCHANGE events and this state machine turns each reported bar rect into what the widget does:

* bar sliding:   the widget rides along, offset from its docked spot by as much as the bar has slid;
* bar hidden:    the widget hides and is parked back on its docked spot;
* bar fully out: the widget goes back to its docked spot and the full refresh gate runs once
                 (fullscreen latch, #24 yield, position), which has the final word.

The docked spot is read from the widget's own position when a slide starts, so a drag, an alignment
change or a resize made just before the slide is never undone. A slide only ever starts from a
widget that is showing, or from one hidden because its bar slid away and allowed to come back
(``ride_in_allowed``: no fullscreen latch, no #24 yield, no fullscreen window over the bar).

Pure logic, no Qt: the widget applies the returned :class:`RideAction`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from netspeedtray import constants
from netspeedtray.utils.taskbar_utils import autohide_bar_hidden, autohide_bar_offset

Point = Tuple[int, int]
Rect = Tuple[int, int, int, int]

# How the widget follows its bar.
MODE_RIDE = "ride"            # docked: ride along with the slide
MODE_HIDE_ONLY = "hide_only"  # Free Move: parked by the user, so never shifted - only hidden / shown
MODE_OFF = "off"              # free-float (#188): not on this bar at all


@dataclass(frozen=True)
class RideAction:
    """What the widget should do for one bar event. Applied in order: hide, move, show, refresh."""
    hide: bool = False
    move_to: Optional[Point] = None
    show: bool = False
    refresh: bool = False


NOTHING = RideAction()


class AutohideRide:
    """See the module docstring."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.sliding = False
        self.dock: Optional[Point] = None
        self._last_event_at: Optional[float] = None
        # True while the widget is hidden only because its bar slid away (so it may ride back in).
        self.hid_widget = False

    # --- the bar moved --------------------------------------------------------------------------

    def bar_moved(self, rect: Rect, monitor: Rect, edge: constants.TaskbarEdge, dpi_scale: float, *,
                  visible: bool, pos: Point, mode: str,
                  ride_in_allowed: Callable[[], bool]) -> RideAction:
        """One LOCATIONCHANGE of the bar. ``rect`` / ``monitor`` in physical px, ``pos`` the widget's
        current (logical) position. ``ride_in_allowed`` is asked once, when a hidden widget would
        start riding back in."""
        if mode == MODE_OFF:
            return self.cancel()
        hidden = autohide_bar_hidden(rect, monitor, edge)
        offset = autohide_bar_offset(rect, monitor, edge)
        fully_out = offset <= constants.taskbar.taskbar.AUTOHIDE_TOLERANCE

        if hidden:
            back = self._end_slide()
            if visible:
                self.hid_widget = True
                return RideAction(hide=True, move_to=back)
            return RideAction(move_to=back) if back else NOTHING

        if fully_out:
            was_sliding = self.sliding
            back = self._end_slide()
            # After a slide, or with the widget still hidden, the full gate decides (fullscreen, #24
            # yield, position) - this event is only the earliest moment to ask.
            refresh = was_sliding or not visible
            return RideAction(move_to=back, refresh=True) if refresh else NOTHING

        # Mid-slide.
        if mode != MODE_RIDE:
            return NOTHING
        if not self.sliding:
            if not visible and not (self.hid_widget and ride_in_allowed()):
                return NOTHING
            self.sliding = True
            self.dock = pos
        self._last_event_at = self._clock()
        step = int(round(offset / max(dpi_scale, 0.1)))
        e = constants.taskbar.edge
        dx, dy = {e.BOTTOM: (0, step), e.TOP: (0, -step),
                  e.LEFT: (-step, 0), e.RIGHT: (step, 0)}.get(edge, (0, 0))
        dock = self.dock or pos
        return RideAction(move_to=(dock[0] + dx, dock[1] + dy), show=not visible)

    # --- ways out -------------------------------------------------------------------------------

    def stuck(self) -> bool:
        """True when a slide has had no event for AUTOHIDE_SLIDE_STUCK_MS - its last event never came
        (auto-hide turned off mid-slide, Explorer restarting). The refresh tick then cancels it."""
        if not self.sliding or self._last_event_at is None:
            return False
        return (self._clock() - self._last_event_at) * 1000.0 >= constants.timeouts.AUTOHIDE_SLIDE_STUCK_MS

    def cancel(self) -> RideAction:
        """Forget any slide and any ride-in claim (hook re-attached or detached, fullscreen hide, #24
        yield, free-float). Puts a riding widget back on its docked spot; never shows or hides it."""
        self.hid_widget = False
        back = self._end_slide()
        return RideAction(move_to=back) if back else NOTHING

    def _end_slide(self) -> Optional[Point]:
        back = self.dock if self.sliding else None
        self.sliding = False
        self.dock = None
        self._last_event_at = None
        return back
