"""A LIVE display-scale change on the widget's monitor (rc.2 VM run, 2026-10-23).

100% -> 150%: the #24 yield hid the widget ("No free spot") until a restart - its log line read
``group 600..1321, widget 1195..1280``: the UIA group converted with a scale of 1.0 (physical px) next
to a widget placed for 1.0 too and clamped to the 1280-px logical screen. 150% -> 100%: the widget came
back 84x32 at x 1045 instead of 85x48 at 1613 - the dock signature read ``(BOTTOM, 32, 1.5, True)``,
i.e. a 48-px bar divided by the OLD 1.5. One root: ``get_dpi_for_monitor`` cached the scale per
HMONITOR forever (since 1.0.5), and an HMONITOR survives a scale change. A fresh start was fine only
because the cache started empty.

Also here: the yield re-judges at once after a scale change, never mixes two scales in one judgement,
and keeps a yield through a transient unknown layout (a window storm: "yield over (layout unknown)" ->
shown ~5 s -> yields again) for a bounded hold.
"""
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core.app_overlap_yield import BUSY, OverlapContext
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.utils import taskbar_utils
from netspeedtray.utils.taskbar_utils import TaskbarInfo, dock_signature
from netspeedtray.tests.unit.test_app_overlap_yield import (  # noqa: F401  (fixtures)
    HWND, Y0, Y1, _b, APP, layout, left_aligned, rig,
)

T = constants.timeouts


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


class _FakeShcore:
    """GetDpiForMonitor with a scale the test changes live."""

    def __init__(self, dpi):
        self.dpi = dpi
        self.calls = 0

    def GetDpiForMonitor(self, _mon, _kind, px, py):
        self.calls += 1
        px._obj.value = py._obj.value = self.dpi
        return 0


def test_get_dpi_for_monitor_follows_a_live_scale_change():
    shcore = _FakeShcore(96)
    with patch.object(taskbar_utils.ctypes.windll, "shcore", shcore, create=True):
        assert taskbar_utils.get_dpi_for_monitor(424242) == 1.0
        shcore.dpi = 144                              # Settings > Display > Scale: 150%
        assert taskbar_utils.get_dpi_for_monitor(424242) == 1.5
        shcore.dpi = 96                               # ... and back
        assert taskbar_utils.get_dpi_for_monitor(424242) == 1.0


def _info(dpi):
    """The VM's display 0: a 1920x1080 panel, the bar 48 physical px at 100%, 72 at 150%."""
    bar = round(48 * dpi)
    return TaskbarInfo(hwnd=HWND, tray_hwnd=None, tasklist_rect=None, rect=(0, 1080 - bar, 1920, 1080),
                       screen_name="D0", screen_geometry=(0, 0, round(1920 / dpi), round(1080 / dpi)),
                       work_area=(0, 0, 1920, 1080 - bar), dpi_scale=dpi, is_primary=True,
                       height=round(bar / dpi))


def test_the_dock_signature_follows_the_new_scale_both_ways():
    """What _resize_if_dock_changed compares: a fresh scale gives the default 48-px bar at both
    scales (a stale 1.5 at 100% made it a 'Small' 32-px bar: the 84x32 widget)."""
    with patch.object(TaskbarInfo, "get_edge_position", return_value=constants.taskbar.edge.BOTTOM):
        s100, s150 = dock_signature(_info(1.0)), dock_signature(_info(1.5))
    assert s100[1] == s150[1] == 48 and s100[2] == 1.0 and s150[2] == 1.5
    assert s100 != s150                               # a scale change still re-sizes the widget


# --- the yield ------------------------------------------------------------------------------------

def test_a_scale_change_re_judges_the_yield_at_once(rig):
    """Yielded on the old scale: the model (logical px at that scale) is stale - probe now, not on the
    backed-off near tick."""
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    assert r.y.yielded
    r.now += 0.6                                      # past the probe rate limit, well inside 3 s
    r.y.sync()
    assert not r.pending_probe()                      # nothing changed: the backed-off cadence
    r.dpi = 1.5
    r.widget_x = 1380 / 1.5
    r.y.sync()
    assert r.pending_probe()
    assert r.y._model is None                         # never judged against the old scale's span


def test_live_100_to_150_does_not_falsely_yield(rig):
    """The VM numbers: group 600..1321 physical on a centred bar; at 150% that is 400..881 logical and
    the widget sits at ~1075 logical - clear."""
    els = [_b("ToggleButton", "StartButton", 600, 645), _b(APP, "Appid: a", 645, 1321)]
    r = rig(1613, width=85)
    r.start(els)
    assert not r.y.yielded
    r.dpi, r.widget_x = 1.5, 1075
    r.now += 0.6
    r.y.sync()
    r.probe(els)
    assert not r.y.yielded and r.changes == 0


def _pm(dpi=1.0, x=1380):
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd, tb.dpi_scale, tb.rect, tb.height, tb.tasklist_rect = HWND, dpi, (0, Y0, 1920, Y1), 48, None
    tb.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
    widget = MagicMock()
    widget.width.return_value, widget.height.return_value = 150, 48
    widget.pos.return_value = QPoint(x, Y0)
    widget._dragging = widget._is_context_menu_visible = False
    pm = PositionManager(WindowState(config={}, widget=widget, taskbar_info=tb))
    pm._app_overlap_info = tb
    return pm, tb


def test_the_context_reads_the_monitor_scale_now_and_never_mixes_two():
    """The taskbar snapshot is up to a tick old; the monitor is read at the moment of judging. When
    they disagree the scale is changing under us: nothing is decided until the next tick (BUSY)."""
    pm, tb = _pm(dpi=1.0)
    with patch.object(taskbar_utils, "current_dpi_for_hwnd", return_value=1.0) as cur:
        assert isinstance(pm._app_overlap_context(), OverlapContext)
    cur.assert_called_with(HWND)
    with patch.object(taskbar_utils, "current_dpi_for_hwnd", return_value=1.5):
        assert pm._app_overlap_context() == BUSY
    with patch.object(taskbar_utils, "current_dpi_for_hwnd", return_value=None):  # can't tell
        assert pm._app_overlap_context().dpi == 1.0


def test_current_dpi_for_hwnd_is_none_for_a_dead_window():
    with patch.object(taskbar_utils.win32gui, "IsWindow", return_value=False):
        assert taskbar_utils.current_dpi_for_hwnd(HWND) is None


# --- bug 3: a transient unknown layout keeps the yield --------------------------------------------

def test_a_transient_unknown_layout_keeps_the_yield(rig):
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    assert r.y.yielded and r.changes == 1
    r.now += 2.0
    r.y.note_foreground_change()
    r.probe(fail=True)                                # a window storm: the probe found nothing
    assert r.y.yielded and r.changes == 1 and r.pending_probe()
    r.probe(left_aligned(28, overflow=True), advance=1.0)
    assert r.y.yielded and r.changes == 1             # never flashed


def test_a_lasting_unknown_layout_still_lets_go(rig):
    """Fail-safe: a yielded widget is the app's only surface - UIA that stays broken shows it."""
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    r.now += 2.0
    r.y.note_foreground_change()
    r.probe(fail=True)
    assert r.y.yielded
    r.probe(fail=True, advance=T.APP_YIELD_UNKNOWN_HOLD_MS / 1000.0 + 0.1)
    assert not r.y.yielded and r.changes == 2


def test_a_timed_out_probe_while_yielded_keeps_the_yield(rig, layout):
    r = rig(1380)
    r.start(left_aligned(28, overflow=True))
    r.now += 2.0
    r.y.note_foreground_change()
    r.y._probe_timer.stop()
    r.y._fire_probe()
    with patch.object(taskbar_utils.time, "monotonic",
                      return_value=taskbar_utils._layout_state["started"] + 99):
        r.y._poll_probe()                             # "re-check timed out after 5 s"
    assert r.y.yielded and r.changes == 1 and r.pending_probe()


# --- bug 2, second half: Qt catches up with the OS after the re-size --------------------------------

def test_a_resize_reruns_when_qts_scale_for_the_widget_catches_up():
    """The OS scale (the dock signature) and Qt's device pixel ratio for the widget change at
    different moments; a re-size in between measured the text at the old scale (VM: 87 px at 100%
    after 100 -> 150 -> 100, fresh start 85). Once Qt's ratio moves, re-size once more - and only once."""
    import types
    from netspeedtray.views.widget import main as widget_main
    tb = _info(1.0)
    with patch.object(TaskbarInfo, "get_edge_position", return_value=constants.taskbar.edge.BOTTOM):
        sig = dock_signature(tb)
        qt = {"dpr": 1.5}                              # Qt still on 150% when the OS said 100%
        w = types.SimpleNamespace(logger=MagicMock(), layout_manager=MagicMock(), position_manager=MagicMock(),
                                  _dock_sig_seen=sig, _qt_scale_seen=1.5,
                                  devicePixelRatioF=lambda: qt["dpr"])
        w.position_manager.is_free_float_active.return_value = False
        check = types.MethodType(widget_main.NetworkSpeedWidget._resize_if_dock_changed, w)
        assert check(tb) is False                      # nothing moved
        qt["dpr"] = 1.0                                # Qt catches up
        assert check(tb) is True
        assert w.layout_manager.resize_widget_for_font.call_count == 1
        assert check(tb) is False                      # once


def test_a_mirror_resizes_when_qts_scale_for_it_catches_up():
    """The #434 mirrors share the layout manager but had their own dock check: after a live scale
    change on a mirror's display it kept the size measured at Qt's old ratio. Same rule as the main
    widget - re-size once when Qt's ratio moves, and only once."""
    import types
    from netspeedtray.views.widget.mirror import MirrorWidget
    tb = _info(1.0)
    with patch.object(TaskbarInfo, "get_edge_position", return_value=constants.taskbar.edge.BOTTOM):
        sig = dock_signature(tb)
        qt = {"dpr": 1.5}
        m = types.SimpleNamespace(logger=MagicMock(), layout_manager=MagicMock(), _tag="mirror test",
                                  _dock_sig_seen=sig, _identity_sig_seen="id", _qt_scale_seen=1.5,
                                  _main_identity_signature=lambda: "id",
                                  devicePixelRatioF=lambda: qt["dpr"])
        check = types.MethodType(MirrorWidget._resize_if_needed, m)
        assert check(tb) is False
        qt["dpr"] = 1.0
        assert check(tb) is True
        assert m.layout_manager.resize_widget_for_font.call_count == 1
        assert check(tb) is False
