"""
The widget's fullscreen latch (#503).

The fullscreen hide used to be re-decided from scratch on every refresh from ONE foreground reading,
and any reading that was not a covering window read as "not covered": a NULL foreground during an
activation hand-off, the stale hwnd a debounced foreground event carried, a window already closed.
One such reading every ~24 s showed the widget over a fullscreen Java player for up to a second (and,
the other way round, an invisible monitor-sized window hid it on a plain desktop).

``FullscreenLatch`` keeps the decision between readings:

- a reading from a foreground that is not on screen (NULL, dead, invisible, DWM-cloaked) is no
  information - the latch holds, except that while covered it re-judges the window that covered, so a
  fullscreen app closing with nothing taking the foreground still releases it. That hold is bounded:
  after ``hold_ms`` of nothing but no-information readings the latch releases even if the window that
  covered still looks drawn and monitor-sized (a shell surface during Show desktop left the widget
  hidden until a desktop click in testing), so a bad cover can cost at most a short absence;
- covered is immediate (a real, drawn covering window hides the widget at once);
- uncovered needs confirmation: two agreeing readings at least ``confirm_ms`` apart, or the desktop
  itself in the foreground (Win+D / Show desktop), which is a deliberate exit.

Pure logic: the window probes are passed in, so the widget's module-level helpers (and the tests'
stand-ins for them) stay the single source of truth.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from netspeedtray import constants


class FullscreenLatch:
    """See the module docstring. ``judge`` is called by the widget's visibility gate."""

    # ``reason`` values of a reading that just released the latch (the widget may show).
    RELEASE_REASONS = frozenset({"desktop-foreground", "exit-confirmed", "cover-gone",
                                 "no-foreground-timeout"})

    def __init__(self, confirm_ms: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic,
                 hold_ms: Optional[int] = None) -> None:
        self.confirm_ms = (constants.timeouts.FULLSCREEN_EXIT_CONFIRM_MS
                           if confirm_ms is None else int(confirm_ms))
        self.hold_ms = (constants.timeouts.FULLSCREEN_NO_FOREGROUND_HOLD_MS
                        if hold_ms is None else int(hold_ms))
        self._clock = clock
        self.covered = False
        self.cover_hwnd = 0
        self._exit_first_at: Optional[float] = None
        self._no_info_since: Optional[float] = None
        # Set by judge(): True while an uncovered reading waits for its confirmation, so the caller
        # schedules a re-check instead of waiting for the 1 s tick.
        self.recheck_wanted = False
        # Why the last judge() returned what it did, and which window it judged (for the debug log).
        self.reason = "init"
        self.probe_hwnd = 0

    def note_covered(self, hwnd: int) -> None:
        """The immediate fullscreen hide fired for ``hwnd`` (already judged covering). Ignored when
        no window is known: a latch covered by nothing could never be re-checked, so it would hold
        the widget hidden until some real foreground came along."""
        hwnd = int(hwnd or 0) or self.cover_hwnd
        if not hwnd:
            return
        self.covered = True
        self.cover_hwnd = hwnd
        self._exit_first_at = None
        self._no_info_since = None
        self.recheck_wanted = False

    def judge(self, foreground: int, *, on_screen: Callable[[int], bool],
              obstructs: Callable[[int], bool], is_desktop: Callable[[int], bool]) -> bool:
        """One reading. True = treat the taskbar as covered by a fullscreen window now."""
        self.recheck_wanted = False
        foreground = int(foreground or 0)
        if on_screen(foreground):
            probe = foreground
            self._no_info_since = None
        elif self.covered and self.cover_hwnd:
            now = self._clock()
            if self._no_info_since is None:
                self._no_info_since = now
            elif (now - self._no_info_since) * 1000.0 >= self.hold_ms:
                self.probe_hwnd = self.cover_hwnd
                return self._release("no-foreground-timeout")
            probe = self.cover_hwnd   # no information from the foreground: is the cover still there?
        else:
            self.reason, self.probe_hwnd = "no-information", foreground
            return self.covered
        self.probe_hwnd = probe

        if on_screen(probe) and obstructs(probe):
            self.reason = "covered" if not self.covered else "still-covered"
            self.covered, self.cover_hwnd, self._exit_first_at = True, probe, None
            return True
        if not self.covered:
            self.reason = "not-covered"
            return False

        if probe == foreground and is_desktop(foreground):
            return self._release("desktop-foreground")
        now = self._clock()
        if self._exit_first_at is None:
            self._exit_first_at = now
            self.reason, self.recheck_wanted = "exit-unconfirmed", True
            return True
        if (now - self._exit_first_at) * 1000.0 < self.confirm_ms:
            self.reason, self.recheck_wanted = "exit-unconfirmed", True
            return True
        return self._release("exit-confirmed" if probe == foreground else "cover-gone")

    def _release(self, reason: str) -> bool:
        self.covered, self.cover_hwnd, self._exit_first_at = False, 0, None
        self._no_info_since = None
        self.reason = reason
        return False
