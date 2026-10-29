"""#24: the widget steps aside for app buttons on a native horizontal Win11 taskbar (rc.2 W17/F11).

Design of record: Docs/releases/v2.2/design-24-app-overlap.md (owner chose "yield", 2026-10-22).
On a left-aligned (or crowded / user-dragged centred) Win11 bar the app buttons grow into the widget
and both become illegible; Win11 has no ``ToolbarWindow32``, so the classic safe-zone snap never fires.
``core/app_overlap_yield.py`` hides the widget through ``_execute_refresh``'s visibility gate while the
contiguous Start-led button run (incl. the "..." overflow, ``ToggleButton`` / ``OverflowButton``) comes
within TRAY_EDGE_MIN_GAP_PX of it, and shows it after two clear readings >= 300 ms apart. It never
moves the widget (#200). Fixtures: the owner's bottom-centred probe (scratchpad
``d24/bottom_centered_probe.txt``, 1920x1080 @ 96 DPI, 11 apps, Start 674..719, last app ..1247).
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core import app_overlap_yield as aoy
from netspeedtray.core.app_overlap_yield import (
    BUSY, NO_TASKBAR, AppOverlapYield, OverlapContext, YieldHysteresis, group_reaches_widget, group_span,
    predict_group,
)
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.utils import taskbar_utils, uia_widgets
from netspeedtray.utils.taskbar_utils import TaskbarInfo

T = constants.timeouts
GAP = constants.layout.TRAY_EDGE_MIN_GAP_PX
BOTTOM = constants.taskbar.edge.BOTTOM
RIGHT = constants.taskbar.edge.RIGHT
HWND = 5151
Y0, Y1 = 1032, 1080
APP = "Taskbar.TaskListButtonAutomationPeer"


def _b(cls, aid, left, right):
    return (cls, aid, (left, Y0, right, Y1))


# The owner's bar, verbatim from the probe (non-button text/image children kept: they must not count).
CENTERED = [
    ("Taskbar.TaskbarFrameAutomationPeer", "TaskbarFrame", (0, 1032, 1920, 1080)),
    _b("ToggleButton", "WidgetsButton", 6, 158),
    ("NamedContainerAutomationPeer", "", (16, 1036, 152, 1076)),
    ("Image", "BadgeAnchorLargeTicker", (19, 1043, 43, 1067)),
    ("TextBlock", "", (51, 1039, 122, 1055)),
    ("TextBlock", "WidgetsBadgeText", (37, 1038, 40, 1053)),
    _b("ToggleButton", "StartButton", 674, 719),
    _b("ToggleButton", "SearchButton", 719, 763),
    ("Microsoft.UI.Xaml.Controls.AnimatedVisualPlayer", "Icon", (729, 1044, 753, 1068)),
] + [_b(APP, "Appid: app%d" % i, 763 + 44 * i, 807 + 44 * i) for i in range(11)] + [
    _b("SystemTray.NormalButton", "SystemTrayIcon", 1694, 1726),
    _b("SystemTray.NormalButton", "SystemTrayIcon", 1726, 1770),
    _b("SystemTray.AccentButton", "SystemTrayIcon", 1774, 1798),
    _b("SystemTray.OmniButtonRight", "SystemTrayIcon", 1802, 1826),
    _b("SystemTray.OmniButton", "SystemTrayIcon", 1834, 1904),
    _b("SystemTray.ShowDesktopButton", "SystemTrayIcon", 1908, 1920),
]
OWNER_WIDGET_X = 1496            # where the owner's widget sits on that bar (tray-anchored default)
WIDGET_W = 150


def left_aligned(n_apps, overflow=False, stray=None, widgets=True):
    """Win11 Left alignment: Start at ~12, apps growing right, Widgets beside the tray."""
    els = [_b("ToggleButton", "StartButton", 12, 57), _b("ToggleButton", "SearchButton", 57, 101)]
    x = 101
    for i in range(n_apps):
        els.append(_b(APP, "Appid: app%d" % i, x, x + 44))
        x += 44
    if overflow:   # the real identity of Win11's "..." button
        els.append(_b("ToggleButton", "OverflowButton", x, x + 44))
        x += 44
    if widgets:
        els.append(_b("ToggleButton", "WidgetsButton", 1530, 1690))
    if stray is not None:
        els.append(_b("ToggleButton", "SomeFutureShellButton", stray[0], stray[1]))
    els += [_b("SystemTray.OmniButton", "SystemTrayIcon", 1834, 1904),
            _b("SystemTray.ShowDesktopButton", "SystemTrayIcon", 1908, 1920)]
    return els


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def layout():
    """The REAL layout cache, its worker run synchronously on the REAL element parsing."""
    ctl = types.SimpleNamespace(elements=None, workers=[], requests=[], fail=False, bar=None)

    def start():
        ctl.workers.append(taskbar_utils._layout_worker)

    def query(_hwnd):
        if ctl.fail or ctl.elements is None:
            return None
        return uia_widgets._layout_from_elements_ext(ctl.elements, False)

    def land(elements=None, fail=False):
        ctl.elements, ctl.fail = (elements if elements is not None else ctl.elements), fail
        while ctl.workers:
            ctl.workers.pop(0)()

    real_request = taskbar_utils.request_taskbar_layout_probe

    def request(h, keep_last=False):
        ctl.requests.append(keep_last)
        real_request(h, keep_last=keep_last)

    ctl.land = land
    fresh = {"hwnd": 0, "apps": None, "widgets": None, "group": None, "overflow": None, "pending": False,
             "done": False, "gen": 0, "running": False, "started": 0.0, "landed": 0}
    taskbar_utils._layout_state.update(fresh)
    with patch.object(taskbar_utils, "_start_layout_worker", side_effect=start), \
            patch.object(taskbar_utils, "_query_taskbar_layout_blocking", side_effect=query), \
            patch.object(taskbar_utils, "request_taskbar_layout_probe", side_effect=request),             patch.object(taskbar_utils, "_taskbar_window_rect", side_effect=lambda _h: ctl.bar):
        yield ctl
    taskbar_utils._layout_state.update(fresh)


class Rig:
    """An AppOverlapYield on a fake bottom bar; the clock and the widget's x are the test's."""

    def __init__(self, layout, widget_x, *, dpi=1.0, width=WIDGET_W, centered=None, drop_key=None,
                 saved_drop=None, enabled=True):
        self.layout = layout
        self.widget_x = widget_x
        self.width = width
        self.dpi = dpi
        self.busy = False
        self.eligible = True
        self.no_taskbar = False
        self.hwnd = HWND
        self.centered = centered
        self.drop_key = drop_key
        self.saved_drop = saved_drop
        self.layout_key = None
        self.stepped = None          # rc.3: the two default spots beside a Widgets button (or None)
        self.tray_spot = None
        self.tray_spot_clear = False   # rc.3: the tray spot clears the weather text
        self.persisted = []
        self.now = 1000.0
        self.changes = 0
        self.firsts = 0
        self.y = AppOverlapYield(self._ctx, self._changed, self._first, clock=lambda: self.now,
                                 on_persist_drop=self._persist, enabled=enabled)

    def _ctx(self):
        if not self.eligible:
            return "the setting is off"
        if self.no_taskbar:
            return NO_TASKBAR
        if self.busy:
            return BUSY
        return OverlapContext(hwnd=self.hwnd, dpi=self.dpi, widget=(self.widget_x, self.widget_x + self.width),
                              centered=self.centered, drop_key=self.drop_key, saved_drop=self.saved_drop,
                              layout_key=self.layout_key, stepped=self.stepped, tray_spot=self.tray_spot,
                              tray_spot_clear=self.tray_spot_clear)

    def _persist(self, value):
        self.persisted.append(value)
        self.saved_drop = value

    def _changed(self):
        self.changes += 1

    def _first(self):
        self.firsts += 1

    def pending_probe(self):
        return self.y._probe_timer.isActive()

    def probe(self, elements=None, fail=False, advance=0.0):
        """Run the scheduled probe now (the timer's wait is the rate limit, not under test here)."""
        assert self.pending_probe(), "no probe was scheduled"
        self.now += advance
        self.y._probe_timer.stop()
        self.y._fire_probe()
        self.layout.land(elements, fail=fail)
        self.y._poll_probe()

    def start(self, elements):
        self.y.sync()
        self.probe(elements)

    def close(self):
        self.y.detach()


@pytest.fixture
def rig(layout):
    made = []

    def make(widget_x, **kw):
        r = Rig(layout, widget_x, **kw)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


# --- the group: what counts as "the app buttons" -------------------------------------------------

def test_owner_centred_bar_group_is_start_to_last_app():
    group = uia_widgets.app_group_from_elements(CENTERED, vertical=False)
    assert group == (674, 1032, 1247, 1080)            # Widgets (6..158) and the tray stay out


def test_left_aligned_group_includes_the_overflow_button_but_not_widgets():
    assert uia_widgets.app_group_from_elements(left_aligned(5), False)[2] == 101 + 5 * 44
    assert uia_widgets.app_group_from_elements(left_aligned(5, overflow=True), False)[2] == 101 + 6 * 44


def test_a_stray_button_near_the_tray_does_not_join_the_group():
    els = left_aligned(5, stray=(1700, 1744))
    assert uia_widgets.app_group_from_elements(els, False)[2] == 101 + 5 * 44


# --- the predicate -------------------------------------------------------------------------------

def test_predicate_clear_touching_overlapping_unknown():
    widget = (1000.0, 1150.0)
    assert group_reaches_widget((100.0, 1000.0 - GAP), widget) is False        # exactly GAP: fits
    assert group_reaches_widget((100.0, 1000.0 - GAP + 1), widget) is True     # inside the gap
    assert group_reaches_widget((100.0, 1100.0), widget) is True               # overlapping
    assert group_reaches_widget((1150.0 + GAP, 1500.0), widget) is False       # widget left of Start
    assert group_reaches_widget((1150.0 + GAP - 1, 1500.0), widget) is True
    assert group_reaches_widget(None, widget) is None                          # unknown: never yield


def test_group_span_converts_physical_to_logical_at_125_percent():
    assert group_span((15, 1290, 1250, 1350), 1.25) == (12.0, 1000.0)
    assert group_span(None, 1.25) is None
    assert group_span((10, 0, 10, 5), 1.0) is None                  # empty rect


# --- the hysteresis ------------------------------------------------------------------------------

def test_hysteresis_hides_at_once_and_shows_after_two_clear_readings_300ms_apart():
    h = YieldHysteresis(confirm_ms=300)
    assert h.observe(False, 0.0) is None and not h.yielded
    assert h.observe(True, 1.0) == "yield" and h.yielded
    assert h.observe(True, 1.5) is None
    assert h.observe(False, 2.0) == "confirm" and h.yielded
    assert h.observe(False, 2.1) == "confirm" and h.yielded        # only 100 ms apart
    assert h.observe(False, 2.35) == "show" and not h.yielded


def test_hysteresis_a_collision_between_the_clear_readings_restarts_the_count():
    h = YieldHysteresis(confirm_ms=300)
    h.observe(True, 0.0)
    assert h.observe(False, 1.0) == "confirm"
    assert h.observe(True, 1.2) is None and h.yielded
    assert h.observe(False, 1.5) == "confirm"
    assert h.observe(False, 1.85) == "show"


def test_hysteresis_drop_baseline_and_unknown():
    h = YieldHysteresis()
    h.suppressed = True                          # the user dropped it on the buttons
    assert h.observe(True, 0.0) is None and not h.yielded
    assert h.observe(False, 1.0) is None and not h.suppressed      # seen clear once: spent
    assert h.observe(True, 2.0) == "yield"
    assert h.observe(None, 3.0) == "show" and not h.yielded        # unknown: show (the watcher holds first)
    assert h.observe(None, 4.0) is None


# --- the watcher on the owner's real bar ---------------------------------------------------------

def test_owner_bottom_centred_bar_never_yields_on_foreground_probes(rig, layout):
    r = rig(OWNER_WIDGET_X)
    r.start(CENTERED)
    assert layout.requests == [True]             # keep_last: never falls back to the lagging HWND rect
    assert not r.y.yielded and r.changes == 0
    probes = 1
    for _ in range(30):                          # 30 alt-tabs, 2 s apart, the bar unchanged
        r.now += 2.0
        r.y.note_foreground_change()
        r.y.sync()
        if r.pending_probe():
            r.probe(CENTERED)
            probes += 1
        r.y._followup_timer.stop()
    assert not r.y.yielded and r.changes == 0 and r.firsts == 0
    # Measure each event: a newly opened uncombined button need not be a 44 px slot.
    assert probes == 31


def test_owner_bar_idle_tick_never_probes(rig, layout):
    r = rig(OWNER_WIDGET_X)
    r.start(CENTERED)
    for _ in range(120):
        r.now += 1.0
        r.y.sync()
        assert not r.pending_probe()


def test_a_live_relayout_of_the_same_bar_re_probes_once(rig, layout):
    """rc.3 review: on a top/bottom bar the yield is the only layout prober, and once the group is
    far from the widget it never probes again - so the Widgets box the default spot steps left of
    froze at its startup value. A Settings alignment flip, a tray icon appearing (the tray widens)
    or Widgets toggled re-lay the bar on the SAME HWND: each must re-probe, once."""
    r = rig(OWNER_WIDGET_X)
    r.layout_key = (0, 1770, True)
    r.start(left_aligned(3))
    for _ in range(5):
        r.now += 1.0
        r.y.sync()
        assert not r.pending_probe()                 # unchanged inputs: idle, as before
    for key in [(1, 1770, True),                     # TaskbarAl flipped to centred
                (1, 1746, True),                     # a tray icon appeared
                (1, 1746, False)]:                   # Widgets turned off
        r.layout_key = key
        r.now += 1.0
        r.y.sync()
        assert r.pending_probe(), key
        r.probe(left_aligned(3))
        r.now += 1.0
        r.y.sync()
        assert not r.pending_probe(), key            # once per change, not every tick


def test_a_live_relayout_also_re_probes_after_the_button_has_slid(rig):
    """rc.3 VM: the tray end moves first and the Widgets button slides after it; a probe taken right
    at the layout change caught the button mid-slide and nothing probed again (the widget sat 39 px
    short of the button). A layout change arms the foreground follow-ups too."""
    r = rig(OWNER_WIDGET_X)
    r.layout_key = (0, 1738, True)
    r.start(left_aligned(3))
    r.layout_key = (0, 1694, True)                   # an input-language indicator appeared
    r.now += 1.0
    r.y.sync()
    assert r.pending_probe()
    r.probe(left_aligned(3))
    assert r.y._followup_timer.isActive()
    assert r.y._followups_left == T.APP_YIELD_FOREGROUND_FOLLOWUPS
    r.y._followup_timer.stop()
    r.y._on_followup()                               # 800 ms later: one more measurement
    assert r.pending_probe()


def test_centred_bar_few_apps_never_yields_even_when_the_group_recentres(rig):
    r = rig(OWNER_WIDGET_X)
    few = [e for e in CENTERED if not e[1].startswith("Appid: app") or int(e[1][10:]) < 3]
    r.start(few)
    assert not r.y.yielded


def test_dragged_next_to_the_last_app_then_apps_open_yields_once_and_comes_back(rig):
    """The live test's same-code-path case: a centred group's right end grows ~22 px per app."""
    r = rig(1247 + GAP)                          # exactly GAP right of the last app: fits
    r.start(CENTERED)
    assert not r.y.yielded
    grown = [(c, a, (rc[0] - 22, rc[1], rc[2] - 22, rc[3])) if a in ("StartButton", "SearchButton")
             or a.startswith("Appid") else (c, a, rc) for c, a, rc in CENTERED]
    grown.append(_b(APP, "Appid: new", 1225, 1269))          # the 12th app, group 652..1269
    r.now += 2.0
    r.y.note_foreground_change()
    assert r.pending_probe()                     # 0 px away: near
    r.probe(grown)
    assert r.y.yielded and r.changes == 1 and r.firsts == 1
    # The app closes: hidden until two clear readings >= 300 ms apart.
    r.now += 2.0
    r.y.note_foreground_change()
    r.probe(CENTERED)
    assert r.y.yielded and r.pending_probe()     # the confirmation is scheduled
    r.probe(CENTERED, advance=0.5)
    assert not r.y.yielded and r.changes == 2 and r.firsts == 1


def test_left_aligned_apps_grow_into_the_widget_with_and_without_overflow(rig):
    widget_x = 1380                              # tray-anchored, left of the Widgets button's spot
    r = rig(widget_x)
    r.start(left_aligned(20))                    # ends at 981: 9 slots away, nothing polls
    assert not r.y.yielded
    fg = 0
    while not r.pending_probe():                 # opening a window must request a real measurement
        r.now += 2.0
        r.y.note_foreground_change()
        fg += 1
        assert fg < 10
    assert fg == 1                               # measurement is not gated by the slot prediction
    r.probe(left_aligned(28))                    # ends at 1333: 47 px clear
    assert not r.y.yielded
    r.y.note_foreground_change()
    r.probe(left_aligned(28, overflow=True), advance=1.0)   # the "..." button ends at 1377
    assert r.y.yielded


def test_a_stray_button_under_the_widget_never_hides_it(rig):
    r = rig(1700)
    r.start(left_aligned(5, stray=(1700, 1744), widgets=False))
    assert not r.y.yielded


def test_dpi_125_uses_logical_coordinates(rig):
    # Physical group end 1650 = logical 1320. At 1340 the widget is 20 logical px clear (but would be
    # INSIDE the group if physical px were compared); at 1325 it is 5 px away - inside GAP.
    els = [_b("ToggleButton", "StartButton", 15, 71), _b(APP, "Appid: a", 71, 1650)]
    clear = rig(1340, dpi=1.25)
    clear.start(els)
    assert not clear.y.yielded
    near = rig(1325, dpi=1.25)
    near.start(els)
    assert near.y.yielded


# --- fail-safe ------------------------------------------------------------------------------------

def test_uia_failure_never_yields_and_a_lasting_failure_while_yielded_shows_it(rig):
    r = rig(1380)
    r.y.sync()
    r.probe(fail=True)
    assert not r.y.yielded
    r.now += T.SIDE_BAR_NEAR_RECHECK_MS / 1000.0
    r.y.sync()                                   # no group known: retried on the backed-off tick
    r.probe(left_aligned(28, overflow=True))
    assert r.y.yielded
    r.now += 2.0
    r.y.note_foreground_change()
    r.probe(fail=True)
    assert r.y.yielded and r.changes == 1        # a transient unknown keeps it aside (window storm) ...
    r.probe(fail=True, advance=T.APP_YIELD_UNKNOWN_HOLD_MS / 1000.0 + 0.1)
    assert not r.y.yielded and r.changes == 2    # ... a lasting one shows it (its only surface)


def test_a_bar_without_start_button_is_unknown(rig):
    r = rig(1380)
    r.start([_b(APP, "Appid: a", 101, 1400)])
    assert not r.y.yielded


def test_timed_out_probe_never_yields(rig, layout):
    r = rig(1380)
    r.y.sync()
    r.y._probe_timer.stop()
    r.y._fire_probe()
    with patch.object(taskbar_utils.time, "monotonic",
                      return_value=taskbar_utils._layout_state["started"] + 99):
        r.y._poll_probe()                        # past LAYOUT_PROBE_TIMEOUT_S, nothing landed
    assert not r.y.yielded and not r.y._inflight


def test_ineligible_context_detaches_and_unyields(rig):
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    assert r.y.yielded
    r.eligible = False                           # side bar / Free Move / ExplorerPatcher bar
    r.y.sync()
    assert not r.y.yielded and r.y.attached_hwnd == 0


# --- the user's drop -------------------------------------------------------------------------------

def test_a_drop_onto_the_buttons_is_respected_until_seen_clear_once(rig):
    r = rig(900)
    r.start(left_aligned(5))                     # clear
    r.y.note_user_drop()                         # dropped on the buttons...
    r.probe(left_aligned(20), advance=1.0)       # ...which end at 981
    assert not r.y.yielded
    r.now += T.SIDE_BAR_NEAR_RECHECK_MS / 1000.0
    r.y.sync()
    r.probe(left_aligned(20))
    assert not r.y.yielded                       # still theirs
    r.widget_x = 1380                            # dragged away again (or apps closed): clear once
    r.y.note_user_drop()
    r.probe(left_aligned(27), advance=1.0)
    assert not r.y._state.suppressed
    r.y.note_foreground_change()
    r.probe(left_aligned(28, overflow=True), advance=1.0)
    assert r.y.yielded                           # a later collision yields again


def test_nothing_is_decided_mid_drag(rig):
    r = rig(1380)
    r.start(left_aligned(27))
    r.y.note_foreground_change()
    r.busy = True
    r.probe(left_aligned(28, overflow=True), advance=1.0)
    assert not r.y.yielded and r.pending_probe()  # re-asked once the drag is over
    r.busy = False
    r.probe(left_aligned(28, overflow=True), advance=1.0)
    assert r.y.yielded


# --- cadence ---------------------------------------------------------------------------------------

def test_rate_limit_one_probe_per_500ms(rig):
    r = rig(1380)
    r.start(left_aligned(27))                    # 1289: 91 px away -> near (< 3 slots)
    r.y.note_foreground_change()
    assert r.pending_probe()
    assert r.y._probe_timer.remainingTime() > T.SIDE_BAR_MIN_PROBE_INTERVAL_MS - 50


def test_foreground_change_probes_now_and_800ms_later_when_near(rig):
    r = rig(1380)
    r.start(left_aligned(27))
    r.now += 2.0
    r.y.note_foreground_change()
    assert r.pending_probe() and r.y._followup_timer.isActive()
    assert r.y._followup_timer.interval() == T.SIDE_BAR_FOREGROUND_FOLLOWUP_MS
    r.probe(left_aligned(27))
    r.y._followup_timer.stop()
    r.now += 0.8
    r.y._on_followup()
    assert r.pending_probe()


def test_near_tick_stays_responsive_while_nothing_changes(rig):
    r = rig(1380)
    r.start(left_aligned(27))
    intervals = []
    for _ in range(6):
        r.now += 0.5
        r.y.sync()
        waited = 0.5
        while not r.pending_probe():
            r.now += 0.5
            waited += 0.5
            r.y.sync()
            assert waited < 200
        intervals.append(waited)
        r.probe(left_aligned(27))
    assert intervals == [T.APP_YIELD_ACTIVE_RECHECK_MS / 1000.0] * 6


def test_far_widget_tick_never_probes_but_yielded_one_does(rig):
    r = rig(1700)
    r.start(left_aligned(5, widgets=False))
    for _ in range(10):
        r.now += 3.5
        r.y.sync()
    assert not r.pending_probe()
    r2 = rig(1380)
    r2.start(left_aligned(28, overflow=True))
    assert r2.y.yielded
    r2.now += T.SIDE_BAR_NEAR_RECHECK_MS / 1000.0
    r2.y.sync()
    assert r2.pending_probe()


def test_keep_last_group_answers_while_the_recheck_is_pending(layout):
    taskbar_utils.request_taskbar_layout_probe(HWND)
    layout.land(CENTERED)
    taskbar_utils.request_taskbar_layout_probe(HWND, keep_last=True)
    assert taskbar_utils.taskbar_layout_probe_pending(HWND)
    assert taskbar_utils.taskbar_layout_group(HWND) == (674, 1032, 1247, 1080)


# --- PositionManager: who is eligible --------------------------------------------------------------

def _pm(edge=BOTTOM, tasklist=None, config=None, x=1380):
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd, tb.dpi_scale, tb.rect, tb.height, tb.tasklist_rect = HWND, 1.0, (0, Y0, 1920, Y1), 48, tasklist
    tb.get_edge_position.return_value = edge
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    tb.get_screen.return_value = screen
    widget = MagicMock()
    widget.width.return_value, widget.height.return_value = WIDGET_W, 48
    widget.pos.return_value = QPoint(x, Y0)
    widget._dragging = False
    widget._is_context_menu_visible = False
    pm = PositionManager(WindowState(config=config or {}, widget=widget, taskbar_info=tb))
    return pm, tb, widget


def test_position_manager_context_only_for_native_horizontal_bars(q_app):
    pm, tb, widget = _pm()
    pm._app_overlap_info = tb
    tb.get_tray_rect.return_value = (1770, Y0, 1920, Y1)
    with patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=1),             patch.object(taskbar_utils, "widgets_button_enabled", return_value=True):
        ctx = pm._app_overlap_context()
    assert ctx == OverlapContext(hwnd=HWND, dpi=1.0, widget=(1380.0, 1380.0 + WIDGET_W), centered=True,
                                 layout_key=(1, 1770, True))
    tb.get_edge_position.return_value = RIGHT
    assert isinstance(pm._app_overlap_context(), str)              # side bars: #406's watcher
    tb.get_edge_position.return_value = BOTTOM
    tb.tasklist_rect = (0, Y0, 1200, Y1)
    assert isinstance(pm._app_overlap_context(), str)              # ExplorerPatcher / Win10
    tb.tasklist_rect = None
    pm._state.config["free_move"] = True
    assert isinstance(pm._app_overlap_context(), str)
    pm._state.config["free_move"] = False
    widget._dragging = True
    assert pm._app_overlap_context() == BUSY


def test_position_manager_yield_end_to_end_and_reset(layout, q_app):
    pm, tb, widget = _pm()
    pm.sync_app_overlap(tb)
    pm._app_yield._probe_timer.stop()
    pm._app_yield._fire_probe()
    layout.land(left_aligned(28, overflow=True))
    pm._app_yield._poll_probe()
    assert pm.is_yielding_to_apps() is True
    pm.stop_app_overlap()
    assert pm.is_yielding_to_apps() is False


def test_position_manager_forwards_drop_and_foreground(q_app):
    pm, _tb, _w = _pm()
    with patch.object(pm._app_yield, "note_user_drop") as drop, \
            patch.object(pm._app_yield, "note_foreground_change") as fg:
        pm.note_user_drop()
        pm.note_foreground_change()
    drop.assert_called_once()
    fg.assert_called_once()


def test_drag_release_tells_the_position_manager(q_app):
    from netspeedtray.core.input_handler import InputHandler
    from PyQt6.QtCore import Qt
    handler = InputHandler.__new__(InputHandler)
    handler.widget = MagicMock()
    handler.position_manager = MagicMock()
    handler.logger = MagicMock()
    handler._is_dragging = True
    handler._swallow_release = False
    handler._drag_start_pos = QPoint(0, 0)
    handler._save_dragged_position = MagicMock()
    event = MagicMock()
    event.button.return_value = Qt.MouseButton.LeftButton
    handler.handle_mouse_release(event)
    handler.position_manager.note_user_drop.assert_called_once()


# --- the visibility gate ---------------------------------------------------------------------------

@pytest.fixture
def refresh(monkeypatch):
    from netspeedtray.views.widget import main as widget_main
    state = {"obstructed": False}
    monkeypatch.setattr(widget_main, "get_taskbar_info",
                        lambda preferred_screen_name=None: types.SimpleNamespace(hwnd=HWND))
    monkeypatch.setattr(widget_main, "is_taskbar_visible", lambda tb: True)
    monkeypatch.setattr(widget_main, "is_taskbar_obstructed", lambda tb, hwnd: state["obstructed"])
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: 0)

    def run(yielded, keep_visible=False, obstructed=False, visible=True):
        state["obstructed"] = obstructed
        w = MagicMock()
        w.config = {"preferred_monitor": None, "free_move": False, "keep_visible_fullscreen": keep_visible}
        w._is_context_menu_visible = False
        w._dragging = False
        w._taskbar_lost_count = 0
        shown = {"v": visible}
        w.isVisible.side_effect = lambda: shown["v"]
        w.setVisible.side_effect = lambda v: shown.update(v=v)
        w.position_manager.refresh_float_state.return_value = None     # not free-floating
        w.position_manager.is_yielding_to_apps.return_value = yielded
        widget_main.NetworkSpeedWidget._execute_refresh(w)
        w.position_manager.sync_app_overlap.assert_called_once()
        return w
    return run


def test_yield_hides_the_widget_on_the_refresh_path(refresh):
    w = refresh(yielded=True)
    w.setVisible.assert_called_once_with(False)
    w.position_manager.update_position.assert_not_called()          # it never moves for the apps


def test_keep_visible_fullscreen_does_not_override_the_yield(refresh):
    refresh(yielded=True, keep_visible=True).setVisible.assert_called_once_with(False)


def test_the_yield_ending_does_not_override_a_fullscreen_hide(refresh):
    refresh(yielded=False, obstructed=True).setVisible.assert_called_once_with(False)


def test_not_yielded_is_todays_behaviour(refresh):
    w = refresh(yielded=False, visible=False)
    w.setVisible.assert_called_once_with(True)


# --- the one-time heads-up -------------------------------------------------------------------------

def test_first_yield_notice_is_the_non_activating_flyout_once_ever(q_app):
    from netspeedtray.views.widget import main as main_mod
    i18n = types.SimpleNamespace(SIDE_BAR_NO_ROOM_TITLE="t", APP_OVERLAP_YIELD_BODY="b {setting}",
                                 HIDE_WHEN_APPS_COVER_LABEL="S", SETTINGS_WINDOW_TITLE="W", WIDGET_SETTINGS_GROUP="P")
    stub = types.SimpleNamespace(config={}, logger=MagicMock(), update_config=MagicMock(), i18n=i18n,
                                 _show_notice_flyout=MagicMock())
    stub._app_overlap_text = lambda t: main_mod.NetworkSpeedWidget._app_overlap_text(stub, t)
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        main_mod.NetworkSpeedWidget._maybe_show_app_overlap_notice(stub)
        main_mod.NetworkSpeedWidget._maybe_show_app_overlap_notice(stub)
    stub._show_notice_flyout.assert_called_once_with("t", "b S")
    stub.update_config.assert_called_once_with({"app_overlap_notice_shown": True})


def test_notice_flag_is_kept_by_reset_all_and_validated():
    from netspeedtray.views.settings.dialog import SettingsDialog
    assert "app_overlap_notice_shown" in SettingsDialog.RESET_ALL_KEEPS
    assert constants.config.defaults.DEFAULT_CONFIG["app_overlap_notice_shown"] is False
    assert constants.config.defaults.VALIDATION_SCHEMA["app_overlap_notice_shown"]["type"] is bool

# --- rc.2 review of #438: a way back (H1) ---------------------------------------------------------

def test_the_setting_off_means_no_yield_and_ends_a_yield(rig):
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    assert r.y.yielded
    r.eligible = False                           # Settings > Widget > Hide while app buttons cover...
    r.y.sync()
    assert not r.y.yielded and r.y.attached_hwnd == 0


def test_position_manager_reads_the_setting_default_on(q_app):
    pm, tb, _w = _pm()
    pm._app_overlap_info = tb
    assert isinstance(pm._app_overlap_context(), OverlapContext)   # absent key = the default, on
    pm._state.config["hide_when_apps_cover"] = False
    assert "setting" in pm._app_overlap_context()
    assert constants.config.defaults.DEFAULT_CONFIG["hide_when_apps_cover"] is True
    assert constants.config.defaults.VALIDATION_SCHEMA["hide_when_apps_cover"]["type"] is bool


def test_settings_widget_page_has_the_switch(q_app):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.pages.widget import WidgetPage
    page = WidgetPage(I18nStrings(), lambda: None)
    page.load_settings({"hide_when_apps_cover": False})
    assert page.get_settings()["hide_when_apps_cover"] is False
    page.load_settings({})
    assert page.get_settings()["hide_when_apps_cover"] is True


def test_second_launch_suspends_the_yield_for_the_session(rig):
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    assert r.y.yielded
    assert r.y.suspend_for_session() is True
    assert not r.y.yielded and r.y.suspended
    for _ in range(5):                           # nothing re-attaches or probes for this session
        r.now += 5.0
        r.y.sync()
        r.y.note_foreground_change()
        assert not r.pending_probe() and not r.y.yielded
    assert r.y.suspend_for_session() is False    # a second one: it was not yielded


def _second_launch_widget(pm, *, fullscreen=False, visible=False):
    """A stand-in widget running the REAL second-launch methods (#440's show_from_second_launch and the
    #24 stand-down / explanation), recording the order things happen in."""
    from netspeedtray.views.widget import main as main_mod
    W = main_mod.NetworkSpeedWidget
    order = []
    i18n = types.SimpleNamespace(SIDE_BAR_NO_ROOM_TITLE="t", APP_OVERLAP_ACTIVATE_BODY="a {setting} {settings} {page}",
                                 HIDE_WHEN_APPS_COVER_LABEL="S", SETTINGS_WINDOW_TITLE="W", WIDGET_SETTINGS_GROUP="P")
    w = types.SimpleNamespace(logger=MagicMock(), i18n=i18n, config={}, position_manager=pm,
                              _taskbar_lost_count=3, _ACTIVATE_FLYOUT_DELAY_MS=W._ACTIVATE_FLYOUT_DELAY_MS)
    shown = {"v": visible}
    w.isVisible = lambda: shown["v"]
    w.setVisible = lambda v: (order.append(("setVisible", v)), shown.update(v=v))
    w._is_off_screen = lambda: False
    w._execute_refresh = lambda *a: order.append(("refresh", pm.is_yielding_to_apps()))
    w._show_notice_flyout = lambda title, body: order.append(("flyout", title, body))
    w.system_event_handler = types.SimpleNamespace(is_fullscreen_active=lambda: fullscreen)
    for name in ("show_from_second_launch", "_stand_down_app_yield", "_explain_app_yield_stand_down",
                 "_app_overlap_text"):
        setattr(w, name, getattr(W, name).__get__(w))
    return w, order


def _yielded_pm(layout):
    pm, tb, _widget = _pm()
    pm.sync_app_overlap(tb)
    pm._app_yield._probe_timer.stop()
    pm._app_yield._fire_probe()
    layout.land(left_aligned(28, overflow=True))
    pm._app_yield._poll_probe()
    assert pm.is_yielding_to_apps() is True
    for name in ("reset_reseat_state", "update_position", "ensure_topmost", "reseat_above_taskbar"):
        setattr(pm, name, MagicMock(name=name))
    pm.is_buried_behind_taskbar = MagicMock(return_value=False)
    return pm, tb


def test_second_launch_un_yields_first_then_shows_then_explains(layout, q_app):
    from netspeedtray.views.widget import main as main_mod
    pm, _tb = _yielded_pm(layout)
    pm.update_position.side_effect = lambda *a, **k: order.append(("update_position", pm.is_yielding_to_apps()))
    w, order = _second_launch_widget(pm)
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        w.show_from_second_launch()
    # 1. the yield is off before anything is shown; 2. #440's show + re-place; 3. the flyout, last.
    assert order[0] == ("setVisible", True)
    assert ("update_position", False) in order and ("refresh", False) in order
    assert order[-1] == ("flyout", "t", "a S W P")
    assert pm._app_yield.suspended and pm.is_yielding_to_apps() is False


def test_second_launch_without_a_yield_shows_no_flyout(layout, q_app):
    from netspeedtray.views.widget import main as main_mod
    pm, tb, _widget = _pm()
    for name in ("reset_reseat_state", "update_position", "ensure_topmost", "reseat_above_taskbar"):
        setattr(pm, name, MagicMock(name=name))
    pm.is_buried_behind_taskbar = MagicMock(return_value=False)
    w, order = _second_launch_widget(pm)
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        w.show_from_second_launch()
    assert ("setVisible", True) in order
    assert not [o for o in order if o[0] == "flyout"]
    assert pm._app_yield.suspended                 # still off for the session (cheap, consistent)


def test_second_launch_under_fullscreen_stands_down_but_shows_nothing(layout, q_app):
    from netspeedtray.views.widget import main as main_mod
    pm, _tb = _yielded_pm(layout)
    w, order = _second_launch_widget(pm, fullscreen=True)
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        w.show_from_second_launch()
    assert order == []                             # no show, no flyout over the fullscreen app
    assert pm.is_yielding_to_apps() is False       # but it comes back when the fullscreen app ends


def test_the_real_activate_message_runs_the_real_second_launch_path(layout, q_app):
    """End to end: the REAL registered NetSpeedTray_Activate_v1 atom through nativeEvent into the real
    show_from_second_launch, which un-yields the real AppOverlapYield."""
    import ctypes
    from netspeedtray.core import shutdown_channel
    from netspeedtray.views.widget import main as main_mod
    atom = shutdown_channel.activate_message_id()
    assert atom and atom == main_mod._WM_NST_ACTIVATE
    assert atom >= 0xC000                          # a RegisterWindowMessage atom, not a fixed WM_*
    pm, _tb = _yielded_pm(layout)
    w, order = _second_launch_widget(pm)
    msg = main_mod._NativeMSG()
    msg.message = atom
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        result = main_mod.NetworkSpeedWidget.nativeEvent(w, b"windows_generic_MSG", ctypes.addressof(msg))
    assert result == (True, 0)                     # #440 consumes its own message
    assert pm.is_yielding_to_apps() is False and pm._app_yield.suspended
    assert order[0] == ("setVisible", True) and order[-1][0] == "flyout"


def test_notices_say_how_to_get_the_widget_back_in_every_locale():
    import json
    import pathlib
    folder = pathlib.Path(constants.__file__).parent / "locales"
    en = json.loads((folder / "en_US.json").read_text(encoding="utf-8"))
    assert "start NetSpeedTray again" in en["APP_OVERLAP_YIELD_BODY"]
    for f in folder.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        for key in ("APP_OVERLAP_YIELD_BODY", "APP_OVERLAP_ACTIVATE_BODY"):
            text = d[key].format(setting="<S>", settings="<W>", page="<P>")
            assert "<S>" in text and "<W>" in text and "<P>" in text, (f.name, key)


# --- H2: the drop baseline survives a restart -------------------------------------------------------

def test_a_drop_on_the_buttons_is_saved_and_kept_after_a_restart(rig):
    r = rig(900, drop_key=-500)
    r.start(left_aligned(5))
    r.y.note_user_drop()
    r.probe(left_aligned(20), advance=1.0)       # on the buttons, by choice
    assert not r.y.yielded and r.persisted == [-500]
    r.close()
    # Restart: a fresh watcher, the saved offset still matches the user's offset.
    again = rig(900, drop_key=-500, saved_drop=-500)
    again.start(left_aligned(20))
    assert not again.y.yielded                   # no instant hide on launch (live: x 905)
    # Seen clear once (apps closed): the baseline is spent and cleared from the config.
    again.y.note_foreground_change()
    again.probe(left_aligned(3), advance=1.0)
    assert again.persisted[-1] is None
    while not again.pending_probe():             # apps opening again, one foreground change each
        again.now += 2.0
        again.y.note_foreground_change()
    again.probe(left_aligned(20), advance=1.0)
    assert again.y.yielded


def test_a_different_offset_than_the_saved_drop_is_not_suppressed(rig):
    r = rig(900, drop_key=-480, saved_drop=-500)  # dragged elsewhere since
    r.start(left_aligned(20))
    assert r.y.yielded


def test_reset_position_clears_the_saved_drop(rig):
    r = rig(900, drop_key=-500, saved_drop=-500)
    r.start(left_aligned(20))
    r.y.note_position_reset()
    assert r.persisted[-1] is None


def test_position_manager_persists_the_drop_through_the_widget(q_app):
    pm, _tb, _w = _pm()
    saved = []
    pm.on_app_yield_persist = saved.append
    pm._persist_app_drop(-500)
    assert pm._state.config["app_overlap_drop_offset"] == -500 and saved == [{"app_overlap_drop_offset": -500}]
    from netspeedtray.views.settings.dialog import SettingsDialog
    assert "app_overlap_drop_offset" in SettingsDialog.RESET_ALL_KEEPS
    schema = constants.config.defaults.VALIDATION_SCHEMA["app_overlap_drop_offset"]
    assert schema["type"] == (int, type(None)) and schema["default"] is None


def test_position_manager_context_carries_the_drop_key_and_saved_drop(q_app):
    pm, tb, _w = _pm(config={"tray_offset_x": -500, "app_overlap_drop_offset": -500})
    pm._app_overlap_info = tb
    ctx = pm._app_overlap_context()
    assert ctx.drop_key == -500 and ctx.saved_drop == -500
    pm._state.config["tray_offset_x"] = constants.config.defaults.DEFAULT_TRAY_OFFSET_X
    assert pm._app_overlap_context().drop_key is None


# --- H3: a hidden widget is judged where it WOULD be ------------------------------------------------

def test_hidden_widget_is_judged_at_its_computed_position(q_app):
    from netspeedtray.core.position_manager import ScreenPosition
    pm, tb, widget = _pm(x=1227)                  # the stale window spot (live: 1255 -> 1227)
    pm._app_overlap_info = tb
    widget.isVisible.return_value = False
    with patch.object(pm._calculator, "calculate_position", return_value=ScreenPosition(1255, Y0)) as calc:
        ctx = pm._app_overlap_context()
    assert ctx.widget == (1255.0, 1255.0 + WIDGET_W)
    assert calc.called                            # (rc.3: also asked for both default spots)
    widget.isVisible.return_value = True          # shown: its real position
    assert pm._app_overlap_context().widget[0] == 1227.0


def test_a_stale_hidden_spot_does_not_keep_it_hidden(rig):
    r = rig(1227)
    r.start(CENTERED)                             # group ends 1247: the stale spot overlaps
    assert r.y.yielded
    r.widget_x = 1255                             # the would-be spot: clear (exactly GAP)
    r.now += T.SIDE_BAR_NEAR_RECHECK_MS / 1000.0
    r.y.sync()
    r.probe(CENTERED)
    r.probe(CENTERED, advance=0.5)
    assert not r.y.yielded


# --- M4: mirrors don't yield --------------------------------------------------------------------------

def test_a_disabled_yield_never_probes_or_hides(rig):
    r = rig(1380, enabled=False)
    for _ in range(5):
        r.y.sync()
        r.y.note_foreground_change()
        r.now += 5.0
    assert not r.pending_probe() and not r.y.yielded and r.y.attached_hwnd == 0


def test_position_manager_can_be_built_without_the_yield(q_app):
    tb = _pm()[1]
    widget = MagicMock()
    pm = PositionManager(WindowState(config={}, widget=widget, taskbar_info=tb), app_yield_enabled=False)
    pm.sync_app_overlap(tb)
    assert pm.is_yielding_to_apps() is False and pm._app_yield.attached_hwnd == 0


# --- lows --------------------------------------------------------------------------------------------

def test_explorer_restart_keeps_a_yield_instead_of_flashing(rig):
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    assert r.y.yielded and r.changes == 1
    # Explorer gone: #440's holding_for_taskbar hides the widget and _execute_refresh returns
    # BEFORE syncing the yield - so nothing reaches it for those seconds (no second hold).
    r.now += 3.0
    r.hwnd = HWND + 1                             # the re-created taskbar
    r.y.sync()
    assert r.y.yielded and r.y.attached_hwnd == HWND + 1
    r.probe(fail=True, advance=0.5)               # its first probe sees no buttons yet (live C5)
    assert r.y.yielded and r.pending_probe()
    r.probe(left_aligned(28, overflow=True), advance=1.0)
    assert r.y.yielded and r.changes == 1         # never shown in between


def test_no_taskbar_after_440s_hold_lets_go_at_once(rig):
    """The yield only sees NO_TASKBAR once #440's missing-taskbar hold has run out (15 s): it must not
    start a second 15 s hold of its own."""
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    r.no_taskbar = True
    r.y.sync()
    assert not r.y.yielded and r.y.attached_hwnd == 0


def test_execute_refresh_never_syncs_the_yield_while_440_holds(monkeypatch):
    from netspeedtray.views.widget import main as widget_main
    monkeypatch.setattr(widget_main, "get_taskbar_info", lambda preferred_screen_name=None: types.SimpleNamespace(hwnd=0))
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: 0)
    w = MagicMock()
    w.config = {"preferred_monitor": None, "free_move": False, "keep_visible_fullscreen": False}
    w._is_context_menu_visible = w._dragging = False
    w._taskbar_lost_count = 0
    w._no_taskbar_ticks = 0
    w.position_manager.refresh_float_state.return_value = None
    w.position_manager.holding_for_taskbar.return_value = True
    widget_main.NetworkSpeedWidget._execute_refresh(w)
    w.position_manager.sync_app_overlap.assert_not_called()


def test_unknown_after_the_hold_shows_it(rig):
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    r.hwnd = HWND + 1
    r.y.sync()
    r.now += T.APP_YIELD_NEW_TASKBAR_HOLD_MS / 1000.0 + 1
    r.probe(fail=True)
    assert not r.y.yielded


def _tick_gaps(r, elements, seconds, step=0.25):
    """Drive the 1 s tick in ``step`` s increments for ``seconds``; the gaps between probes."""
    gaps, last, end = [], r.now, r.now + seconds
    while r.now < end:
        r.now += step
        r.y.sync()
        if r.pending_probe():
            gaps.append(round(r.now - last, 2))
            last = r.now
            r.probe(elements)
    return gaps


def test_yielded_rechecks_every_second_while_active_then_back_off_to_the_idle_cap(rig):
    r = rig(1380)
    crowded = left_aligned(28, overflow=True)
    r.start(crowded)
    assert r.y.yielded
    gaps = _tick_gaps(r, crowded, 120.0)
    active = T.APP_YIELD_ACTIVE_WINDOW_MS / 1000.0
    assert set(gaps[:int(active) - 1]) == {T.APP_YIELD_ACTIVE_RECHECK_MS / 1000.0}
    assert max(gaps) <= T.APP_YIELD_IDLE_RECHECK_MAX_MS / 1000.0 + 0.25
    assert gaps[-1] == pytest.approx(T.APP_YIELD_IDLE_RECHECK_MAX_MS / 1000.0, abs=0.3)
    # Hours crowded: a probe every 8 s, not every second (one probe ~19 ms of CPU incl. Explorer's).
    assert len(gaps) < 120.0 / 2


def test_a_long_idle_yield_still_returns_within_the_idle_cap_on_a_background_close(rig):
    r = rig(1380)
    crowded = left_aligned(28, overflow=True)
    r.start(crowded)
    _tick_gaps(r, crowded, 300.0)
    assert r.y.yielded
    waited = 0.0
    while not r.pending_probe():                 # an app closed in the background: no foreground hint
        r.now += 0.25
        waited += 0.25
        r.y.sync()
    assert waited <= T.APP_YIELD_IDLE_RECHECK_MAX_MS / 1000.0
    r.probe(left_aligned(20))
    r.probe(left_aligned(20), advance=0.5)
    assert not r.y.yielded


def test_foreground_change_restores_the_one_second_cadence_after_idle(rig):
    r = rig(1380)
    near = left_aligned(27)                       # 91 px clear: near, never yields
    r.start(near)
    _tick_gaps(r, near, 120.0)
    assert r.y._near_interval_ms == T.APP_YIELD_IDLE_RECHECK_MAX_MS
    r.y.note_foreground_change()
    r.probe(near)
    r.y._followup_timer.stop()
    r.y._followups_left = 0
    assert set(_tick_gaps(r, near, 10.0)) == {T.APP_YIELD_ACTIVE_RECHECK_MS / 1000.0}


def test_a_tick_slightly_under_one_second_after_the_probe_still_rechecks(rig):
    """The tick is a 1000 ms QTimer and its probe fires a few ms after it: without slack the next
    tick sees 0.99x s and skips (real-QTimer measurement: median gap 2.0 s, not 1 s)."""
    r = rig(1380)
    near = left_aligned(27)
    r.start(near)
    r.now += 0.995
    r.y.sync()
    assert r.pending_probe()


def test_centred_prediction_grows_half_a_slot_at_each_end():
    slot = constants.layout.APP_BUTTON_SLOT_PX
    assert predict_group((674.0, 1247.0), 2, True) == (674.0 - slot, 1247.0 + slot)
    assert predict_group((12.0, 981.0), 2, False) == (12.0, 981.0 + 2 * slot)
    assert predict_group((12.0, 981.0), 1, None) == (12.0 - slot, 981.0 + slot)


def test_even_a_far_centred_bar_is_measured_after_one_foreground_change(rig):
    r = rig(OWNER_WIDGET_X, centered=True)
    r.start(CENTERED)
    r.now += 2.0
    r.y.note_foreground_change()
    assert r.pending_probe()


def test_shutdown_and_restart_log_their_own_reason(rig, caplog):
    import logging
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.AppOverlapYield"):
        r.hwnd = HWND + 1                         # Explorer restart: no "off" line at all
        r.y.sync()
        assert not [x for x in caplog.messages if "yield off" in x]
        r.y.detach("shutting down")
    assert any("shutting down" in x for x in caplog.messages)
    assert not any("no longer" in x for x in caplog.messages)


def test_wide_labelled_button_is_measured_even_when_old_model_is_far(rig):
    r = rig(500, centered=False)
    r.start(left_aligned(1, widgets=False))       # last app ends at 145, well outside the near band
    wide = [_b("ToggleButton", "StartButton", 12, 57),
            _b("ToggleButton", "SearchButton", 57, 101),
            _b(APP, "Appid: wide labelled window", 101, 601)]
    r.now += 1.0
    r.y.note_foreground_change()
    assert r.pending_probe()
    r.probe(wide)
    assert r.y.yielded


def test_followup_detects_layout_that_changes_after_first_800ms(rig):
    r = rig(1380, centered=False)
    original = left_aligned(27, widgets=False)
    r.start(original)
    r.now += 1.0
    r.y.note_foreground_change()
    r.probe(original)
    r.y._followup_timer.stop()
    r.now += 0.8
    r.y._on_followup()
    r.probe(original)                            # first follow-up still sees the old layout
    assert not r.y.yielded and r.y._followup_timer.isActive()
    r.y._followup_timer.stop()
    r.now += 0.8
    r.y._on_followup()
    r.probe(left_aligned(30, widgets=False))      # final layout only arrives after 800 ms
    assert r.y.yielded


def test_far_bar_followup_burst_is_bounded_and_then_idle(rig, layout):
    r = rig(OWNER_WIDGET_X, centered=True)
    r.start(CENTERED)
    r.now += 1.0
    r.y.note_foreground_change()
    r.probe(CENTERED)
    for _ in range(T.APP_YIELD_FOREGROUND_FOLLOWUPS):
        r.y._followup_timer.stop()
        r.now += 0.8
        r.y._on_followup()
        r.probe(CENTERED)
    assert not r.y._followup_timer.isActive() and r.y._followups_left == 0
    assert len(layout.requests) == 2 + T.APP_YIELD_FOREGROUND_FOLLOWUPS
    for _ in range(60):
        r.now += 1.0
        r.y.sync()
        assert not r.pending_probe()


def test_background_close_returns_widget_without_a_foreground_change(rig):
    r = rig(1380)
    crowded = left_aligned(28, overflow=True)
    r.start(crowded)
    for _ in range(12):                          # do not accumulate a long idle back-off
        r.now += 1.0
        r.y.sync()
        r.probe(crowded)
    assert r.y.yielded
    r.now += 1.0                                # background close: no foreground hint
    r.y.sync()
    r.probe(left_aligned(20))
    assert r.y.yielded and r.pending_probe()     # still require the anti-flicker confirmation
    r.probe(left_aligned(20), advance=0.5)
    assert not r.y.yielded


def test_foreground_event_resets_old_backoff_and_works_before_first_layout(rig):
    r = rig(1380)
    r.y.sync()                                  # attach, initial probe has not landed yet
    r.y._near_interval_ms = 60000
    r.y.note_foreground_change()
    assert r.y._near_interval_ms == T.APP_YIELD_ACTIVE_RECHECK_MS
    assert r.y._followups_left == T.APP_YIELD_FOREGROUND_FOLLOWUPS
    assert r.pending_probe() and r.y._followup_timer.isActive()


def test_detach_discards_pending_foreground_followups(rig):
    r = rig(OWNER_WIDGET_X)
    r.start(CENTERED)
    r.y.note_foreground_change()
    r.y.detach()
    assert r.y._followups_left == 0 and not r.y._followup_timer.isActive()
    r.y._on_followup()
    assert not r.pending_probe()


# --- a live scale change: Explorer's buttons in a stale scale's coordinates (rc.2 final VM run) ---

def _centred_group(scale, n_apps=11, screen=1920, y0=Y0, y1=Y1, factor=1.0):
    """Start, Search and ``n_apps`` apps centred on the bar at ``scale``; every coordinate times
    ``factor`` (factor 1.5 at scale 1.0: the stale-scale report seen right after 150 -> 100 %)."""
    slot = 44 * scale
    width = (n_apps + 2) * slot
    x = (screen - width) / 2
    names = [("StartButton", "ToggleButton"), ("SearchButton", "ToggleButton")] +         [("Appid: app%d" % i, APP) for i in range(n_apps)]
    els = []
    for aid, cls in names:
        els.append((cls, aid, tuple(int(round(v * factor)) for v in (x, y0, x + slot, y1))))
        x += slot
    return els


def test_a_stale_scale_group_after_150_to_100_never_yields(rig, layout):
    """The 10 s false step-aside: 150 -> 100 % (auto-hide bar), the widget re-placed at 100 %
    (1611..1698, as logged), and the first probes report the centred group at 1.5x its real place -
    group 1007..1873 in the log. That rect does not fit the bar's window (0,1032)-(1920,1080)."""
    layout.bar = (0, Y0, 1920, Y1)
    r = rig(1611, width=87, dpi=1.5, centered=True)
    r.start(_centred_group(1.5))
    assert not r.y.yielded

    r.dpi = 1.0                                    # the live change reaches the snapshot
    r.now += 1.0
    r.y.sync()                                     # #444: the old group is dropped, re-probe at once
    stale = _centred_group(1.0, factor=1.5)
    left, _t, right, _b = uia_widgets.app_group_from_elements(stale, False)
    assert abs(left - 1007) < 8 and abs(right - 1873) < 8          # ~ the logged span
    r.probe(stale, advance=0.6)
    assert not r.y.yielded and r.changes == 0, "a rect outside the bar is not a collision"

    r.now += 3.0
    r.y.sync()
    r.probe(_centred_group(1.0), advance=0.6)      # Explorer caught up: the real 671..1249
    assert not r.y.yielded and r.changes == 0


def test_an_auto_hide_bar_slid_off_screen_still_yields_for_real_buttons(rig, layout):
    """The fit check is against the bar's OWN rect, wherever it is: a hidden auto-hide bar (slid
    down to y 1078) with buttons that really reach the widget still yields."""
    layout.bar = (0, 1078, 1920, 1126)
    els = [(c, a, (r[0], 1078, r[2], 1126)) for c, a, r in left_aligned(30, widgets=False)]
    r = rig(1380)
    r.start(els)
    assert r.y.yielded


def test_group_fits_bar():
    bar = (0, 1032, 1920, 1080)
    assert taskbar_utils.group_fits_bar((671, 1032, 1249, 1080), bar)
    assert taskbar_utils.group_fits_bar((671, 1029, 1249, 1083), bar)               # rounding slack
    assert not taskbar_utils.group_fits_bar((1007, 1548, 1873, 1620), bar)          # every coord x1.5
    assert not taskbar_utils.group_fits_bar((1007, 1032, 1873, 1104), bar)          # sizes x1.5
    assert not taskbar_utils.group_fits_bar((1500, 1032, 2100, 1080), bar)          # past the bar's end
    assert taskbar_utils.group_fits_bar((671, 1032, 1249, 1080), None)              # unknown: no check
    assert taskbar_utils.group_fits_bar(None, bar)


def test_the_bar_rect_spans_a_slide_during_the_probe(layout):
    """Read before AND after the UIA call: a bar sliding meanwhile is covered by the union."""
    rects = iter([(0, 1032, 1920, 1080), (0, 1060, 1920, 1108)])
    layout.elements = CENTERED
    with patch.object(taskbar_utils, "_taskbar_window_rect", side_effect=lambda _h: next(rects)):
        taskbar_utils.request_taskbar_layout_probe(HWND)
        layout.land()
    assert taskbar_utils.taskbar_layout_bar(HWND) == (0, 1032, 1920, 1108)


# --- rc.3: the full-bar fallback - stay at the rc.2 tray spot instead of hiding -------------------------
# left_aligned(): Widgets button 1530..1690, apps from 101 in 44 px slots. Default spots for a 150 px
# widget: left of the button (1372..1522, the rc.3 step) and the rc.2 tray spot (1676..1826).
STEPPED = (1372.0, 1522.0)
TRAY_SPOT = (1676.0, 1826.0)


def _fallback_rig(rig):
    r = rig(STEPPED[0])
    r.stepped, r.tray_spot, r.tray_spot_clear = STEPPED, TRAY_SPOT, True
    return r


def test_a_full_bar_keeps_the_widget_at_the_tray_spot_instead_of_hiding_it(rig):
    """Owner decision (rc.3): app buttons reaching the stepped spot while the rc.2 spot is clear move
    the widget there (visible on the button's padding, as rc.2) - never a yield."""
    r = _fallback_rig(rig)
    r.start(left_aligned(28))                    # ends 1333: 39 px clear of the stepped spot
    assert not r.y.retreated and not r.y.yielded
    r.y.note_foreground_change()
    r.probe(left_aligned(28, overflow=True), advance=1.0)   # "..." ends 1377: reaches the stepped spot
    assert r.y.retreated and not r.y.yielded and r.changes == 1   # re-placed, still shown
    r.widget_x = TRAY_SPOT[0]                    # PositionManager places it with widgets_step=False
    for _ in range(3):                           # same full bar: it stays put, no flapping
        r.y.note_foreground_change()
        r.probe(left_aligned(28, overflow=True), advance=1.0)
        assert r.y.retreated and not r.y.yielded
    assert r.changes == 1


def test_the_fallback_returns_after_two_clear_readings_and_never_flips_per_probe(rig):
    r = _fallback_rig(rig)
    r.start(left_aligned(30))                    # ends 1421: reaches the stepped spot at once
    assert r.y.retreated and not r.y.yielded
    r.widget_x = TRAY_SPOT[0]
    r.y.note_foreground_change()
    r.probe(left_aligned(20), advance=1.0)       # apps closed: one clear reading is not enough
    assert r.y.retreated and r.pending_probe()   # the confirmation is scheduled
    r.probe(left_aligned(30), advance=0.1)       # back again before the confirmation: count restarts
    assert r.y.retreated
    r.y.note_foreground_change()
    r.probe(left_aligned(20), advance=1.0)
    assert r.y.retreated
    r.probe(left_aligned(20), advance=0.5)       # second clear reading >= 300 ms later
    assert not r.y.retreated and not r.y.yielded
    assert r.changes == 2                        # retreat + return: one re-place each


def test_a_retreated_widget_is_rechecked_on_the_tick_to_notice_a_background_close(rig):
    """Retreated, the widget sits a whole Widgets button (~150 px) right of the group - more than
    SIDE_BAR_NEAR_SLOTS slots - so 'near' alone would stop the tick re-checks and only a foreground
    change could bring it back. It is watched like a yield."""
    r = rig(STEPPED[0], width=60)
    r.stepped, r.tray_spot = (1460.0, 1520.0), (1766.0, 1826.0)   # a narrow widget: 246 px apart
    r.tray_spot_clear = True
    r.start(left_aligned(31))                    # ends 1465: reaches the stepped spot only
    assert r.y.retreated
    r.widget_x = 1766.0
    r.now += T.SIDE_BAR_NEAR_RECHECK_MS / 1000.0 + 0.1
    r.y.sync()
    assert r.pending_probe()


def test_the_fallback_returns_only_with_a_margin_of_room(rig):
    """rc.3 review: a group end hovering at the threshold (1362 clear / 1368 reaching, stepped spot
    from 1372) swung the widget 304 px every couple of probes. Returning needs the usual gap PLUS
    WIDGETS_FALLBACK_RETURN_MARGIN_PX of room."""
    margin = constants.layout.WIDGETS_FALLBACK_RETURN_MARGIN_PX
    r = _fallback_rig(rig)
    r.start(left_aligned(28, stray=(1333, 1368)))   # ends 1368: reaches the stepped spot
    assert r.y.retreated
    r.widget_x = TRAY_SPOT[0]
    for _ in range(3):                               # 1362: clear by 10 - but not by gap + margin
        r.y.note_foreground_change()
        r.probe(left_aligned(28, stray=(1333, 1362)), advance=1.0)
        assert r.y.retreated and r.y._step_clear_since is None   # not even a first clear reading
        r.y.note_foreground_change()
        r.probe(left_aligned(28, stray=(1333, 1368)), advance=1.0)
        assert r.y.retreated
    end = int(STEPPED[0] - GAP - margin)             # exactly gap + margin clear
    r.y.note_foreground_change()
    r.probe(left_aligned(28, stray=(1333, end)), advance=1.0)
    r.probe(left_aligned(28, stray=(1333, end)), advance=0.5)
    assert not r.y.retreated


def test_a_flapping_tray_arms_the_follow_up_burst_at_most_once_per_active_window(rig):
    """rc.3 review: every layout-key change armed 1 probe + 3 follow-ups, even on a far centred bar - a
    tray icon flapping every 2 s meant ~89 probes a minute. The burst is armed at most once per
    APP_YIELD_ACTIVE_WINDOW_MS; the single immediate (rate-limited) probe stays."""
    r = rig(OWNER_WIDGET_X)
    r.layout_key = (1, 1770, True)
    r.start(CENTERED)
    bursts = probes = 0
    for i in range(30):                              # 60 s of a flapping tray
        r.layout_key = (1, 1770 if i % 2 else 1746, True)
        r.now += 2.0
        r.y.sync()
        if r.y._followup_timer.isActive():
            bursts += 1
            r.y._followup_timer.stop()
        if r.pending_probe():
            probes += 1
            r.probe(CENTERED)
    window_s = T.APP_YIELD_ACTIVE_WINDOW_MS / 1000.0
    assert bursts <= int(60 / window_s) + 1
    assert probes == 30                              # one immediate re-probe per change, as before


def test_a_display_scale_change_restarts_the_fallback_confirmations(rig):
    r = _fallback_rig(rig)
    r.start(left_aligned(30))
    assert r.y.retreated
    r.y.note_foreground_change()
    r.probe(left_aligned(20), advance=1.0)           # first clear reading: confirmation pending
    assert r.y._step_clear_since is not None
    r.dpi = 1.25
    r.now += 1.0
    r.y.sync()
    assert r.y._step_clear_since is None and r.y._spot_gone_since is None


def test_buttons_reaching_the_tray_spot_too_still_yield(rig):
    r = _fallback_rig(rig)
    r.start(left_aligned(36))                    # ends 1685: covers both spots
    assert r.y.yielded and not r.y.retreated


def test_no_second_spot_means_todays_yield(rig):
    """A centred bar, no Widgets button beside the tray or a user's drag: stepped/tray_spot are None
    and the yield is exactly rc.2's."""
    r = rig(STEPPED[0])
    r.start(left_aligned(30))
    assert r.y.yielded and not r.y.retreated


def test_the_fallback_ends_when_the_second_spot_goes_away(rig):
    r = _fallback_rig(rig)
    r.start(left_aligned(30))
    assert r.y.retreated
    r.stepped = r.tray_spot = None               # e.g. Widgets turned off: one default spot again
    r.widget_x = TRAY_SPOT[0]
    r.y.note_foreground_change()
    r.probe(left_aligned(30), advance=1.0)
    assert r.y.retreated and r.pending_probe()   # one reading is no proof: confirmation scheduled
    r.probe(left_aligned(30), advance=0.5)
    assert not r.y.retreated and r.changes == 2   # retreat + release


def test_one_reading_without_the_second_spot_keeps_the_fallback(rig):
    """Review of #463: a single context without the Widgets box (the snapshot taken while the layout
    cache answered for another bar) dropped the hold; the next refresh then placed the widget at the
    stepped spot - ON the app buttons - until the backed-off probe (up to 8 s) retreated again."""
    r = _fallback_rig(rig)
    r.start(left_aligned(30))
    assert r.y.retreated and r.changes == 1
    r.widget_x = TRAY_SPOT[0]
    r.stepped = r.tray_spot = None               # one snapshot without the Widgets box
    r.y.note_foreground_change()
    r.probe(left_aligned(30), advance=1.0)
    assert r.y.retreated and r.changes == 1      # still held: no re-place onto the buttons
    r.stepped, r.tray_spot = STEPPED, TRAY_SPOT  # the box is back
    r.probe(left_aligned(30), advance=0.5)
    assert r.y.retreated and r.changes == 1


def test_position_manager_places_at_the_tray_spot_while_retreated(q_app):
    """The yield's fallback drives the calculator: widgets_step=False while retreated, and the context
    carries both default spots when a Widgets button sits beside the tray."""
    pm, tb, widget = _pm(config={"tray_offset_x": 0})
    pm._app_overlap_info = tb
    screen = tb.get_screen.return_value
    screen.availableGeometry.return_value = QRect(0, 0, 1920, Y0)
    tb.get_tray_rect.return_value = (1834, Y0, 1920, Y1)
    tb.toolbars_rect = None
    tb.widgets_extent = (1530, Y0, 1690, Y1)
    tb.widgets_rect = None
    with patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=0),             patch.object(taskbar_utils, "widgets_button_enabled", return_value=True):
        ctx = pm._app_overlap_context()
        assert ctx.stepped == (1372.0, 1522.0) and ctx.tray_spot == (1676.0, 1826.0)
        assert pm.get_calculated_position().x == 1372
        pm._app_yield._hwnd, pm._app_yield._retreated = HWND, True
        assert pm.get_calculated_position().x == 1676
        widget.isVisible.return_value = False      # hidden: judged where it WOULD go (the tray spot)
        assert pm._app_overlap_context().widget[0] == 1676.0
    pm._app_yield._hwnd = 0
    pm._state.config["tray_offset_x"] = 10        # a pre-1.3.0 default is no drop key (rc.3 review)
    assert pm._app_overlap_context().drop_key is None


# --- rc.3 verify: the fallback never draws over the weather text ----------------------------------------
# VM (Win11 26300, 100 %, left-aligned): WidgetsButton 1606..1758, its NamedContainer 1614..1750, the icon
# 1617..1641, "Sunny" ending 1682; with "Mostly cloudy" the rc.2 spot (1613..1698 on that bar) put the
# arrow and "0.0" over the text. The fallback is used only when the tray spot clears the TEXT.
WIDGETS_VM = [
    _b("ToggleButton", "WidgetsButton", 1606, 1758),
    ("NamedContainerAutomationPeer", "", (1614, 1036, 1750, 1076)),
    ("Microsoft.UI.Xaml.Controls.AnimatedVisualPlayer", "BadgeAnchorLargeTicker", (1617, 1043, 1641, 1067)),
    ("TextBlock", "", (1649, 1039, 1672, 1055)),
    ("TextBlock", "", (1649, 1055, 1682, 1071)),
]


def test_widgets_content_is_the_icon_and_text_not_the_container():
    assert uia_widgets.widgets_content_from_elements(WIDGETS_VM) == (1617, 1039, 1682, 1071)
    assert uia_widgets.widgets_content_from_elements(WIDGETS_VM[:2]) is None    # nothing visible
    assert uia_widgets.widgets_content_from_elements(CENTERED[6:]) is None      # no Widgets button
    stray = WIDGETS_VM + [("TextBlock", "", (1800, 1039, 1850, 1071))]         # a tray label: outside
    assert uia_widgets.widgets_content_from_elements(stray) == (1617, 1039, 1682, 1071)


def test_the_layout_probe_keeps_the_widgets_content(layout):
    taskbar_utils.request_taskbar_layout_probe(HWND)
    layout.land(left_aligned(3, widgets=False) + WIDGETS_VM)
    assert taskbar_utils.taskbar_layout_widgets_content(HWND) == (1617, 1039, 1682, 1071)


def test_no_clear_tray_spot_means_the_yield_steps_aside_and_returns_with_the_margin(rig):
    """The tray spot would cover the weather text (or the text's extent is unknown): no fallback - the
    #24 yield hides the widget for the STEPPED spot (Windows shrinks a left-aligned bar's buttons before
    they ever reach the tray spot), and shows it again with the fallback's return margin."""
    margin = constants.layout.WIDGETS_FALLBACK_RETURN_MARGIN_PX
    r = rig(STEPPED[0])
    r.stepped, r.tray_spot, r.tray_spot_clear = STEPPED, TRAY_SPOT, False
    r.start(left_aligned(28, overflow=True))         # "..." ends 1377: reaches the stepped spot only
    assert r.y.yielded and not r.y.retreated and r.changes == 1
    r.y.note_foreground_change()
    r.probe(left_aligned(28, stray=(1333, 1362)), advance=1.0)   # clear by 10 only: stays hidden
    assert r.y.yielded and r.y._state.clear_since is None   # not even a first clear reading
    end = int(STEPPED[0] - GAP - margin)
    r.y.note_foreground_change()
    r.probe(left_aligned(28, stray=(1333, end)), advance=1.0)
    assert r.y.yielded                                # first clear reading: confirmation pending
    r.probe(left_aligned(28, stray=(1333, end)), advance=0.5)
    assert not r.y.yielded


def test_weather_text_growing_under_a_held_widget_steps_it_aside_at_once(rig):
    r = _fallback_rig(rig)
    r.start(left_aligned(30))                         # reaches the stepped spot: held by the tray
    assert r.y.retreated and not r.y.yielded
    r.widget_x = TRAY_SPOT[0]
    r.tray_spot_clear = False                         # "Sunny" -> "Mostly cloudy"
    r.y.note_foreground_change()
    r.probe(left_aligned(30), advance=1.0)
    assert not r.y.retreated and r.y.yielded          # same reading: hidden, never on the buttons


def _pm_left_bar(content):
    pm, tb, widget = _pm(config={"tray_offset_x": 0})
    widget.width.return_value = 85
    pm._app_overlap_info = tb
    tb.get_screen.return_value.availableGeometry.return_value = QRect(0, 0, 1920, Y0)
    tb.get_tray_rect.return_value = (1770, Y0, 1920, Y1)
    tb.toolbars_rect = None
    tb.widgets_extent = (1606, Y0, 1758, Y1)
    tb.widgets_rect = None
    with patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=0), \
            patch.object(taskbar_utils, "widgets_button_enabled", return_value=True), \
            patch.object(taskbar_utils, "taskbar_layout_widgets_content", return_value=content):
        return pm._app_overlap_context()


@pytest.mark.parametrize("content,clear", [
    ((1617, 1039, 1682, 1071), False),   # "Sunny" ends 1682: the rc.2 spot (1677) would cover it
    ((1617, 1039, 1745, 1071), False),   # "Mostly cloudy"
    ((1617, 1039, 1669, 1071), True),    # text ending 1669: 1677 clears it by exactly the gap
    (None, False),                       # unknown: hide rather than collide
])
def test_position_manager_allows_the_fallback_only_clear_of_the_text(q_app, content, clear):
    ctx = _pm_left_bar(content)
    assert ctx.stepped == (1513.0, 1598.0) and ctx.tray_spot == (1677.0, 1762.0)
    assert ctx.tray_spot_clear is clear
