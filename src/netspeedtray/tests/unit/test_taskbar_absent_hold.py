"""
rc.2 W5: Explorer restart. Between the old taskbar going away and the new one existing,
get_taskbar_info() finds NO taskbar and returns the fallback TaskbarInfo (hwnd 0). The widget used to
be placed at the screen-fallback spot (bottom-right of the screen, off the bar) and float there for
~2 s. Now a docked widget that has had a real taskbar keeps its last position and hides, for at most
TASKBAR_ABSENT_HOLD_MS; the new taskbar shows it again. Past the hold, and in every case that never
had a taskbar to lose (a start with no shell), Free Move and free-float, nothing changes.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QPoint, QRect

from netspeedtray import constants
from netspeedtray.core import position_manager as pm_mod
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.views.widget import main as widget_main

HOLD_S = constants.timeouts.TASKBAR_ABSENT_HOLD_MS / 1000.0


class _Clock:
    def __init__(self, t=500.0):
        self.t = t

    def monotonic(self):
        return self.t


def _taskbar(hwnd=2557336):
    screen = MagicMock()
    screen.name.return_value = "DISPLAY1"
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = hwnd
    tb.rect = (0, 1032, 1920, 1080) if hwnd else (0, 1040, 1920, 1080)
    tb.dpi_scale = 1.0
    tb.tasklist_rect = None
    tb.widgets_rect = None
    tb.get_tray_rect.return_value = (1694, 1032, 1920, 1080) if hwnd else None
    tb.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
    tb.get_screen.return_value = screen
    return tb


def _manager(config=None):
    widget = MagicMock()
    widget.width.return_value = 190
    widget.height.return_value = 48
    widget.isVisible.return_value = True
    widget.pos.return_value = QPoint(-1, -1)
    return PositionManager(WindowState(config=config or {}, widget=widget, taskbar_info=None)), widget


def _tick(m, info):
    m.update_position(fresh_taskbar_info=info, _float_refreshed=True)


def test_explorer_restart_keeps_the_last_position_instead_of_the_fallback_spot():
    clock = _Clock()
    m, widget = _manager()
    with patch.object(pm_mod.time, "monotonic", side_effect=clock.monotonic), \
            patch.object(pm_mod.PositionCalculator, "_get_safe_fallback_position",
                         side_effect=AssertionError("moved to the fallback spot")):
        _tick(m, _taskbar())                       # docked on the real taskbar
        docked_moves = widget.move.call_count
        assert docked_moves == 1
        for _ in range(5):                         # Explorer gone: one refresh a second
            clock.t += 1
            assert m.holding_for_taskbar(_taskbar(hwnd=0)) is True
            _tick(m, _taskbar(hwnd=0))
        assert widget.move.call_count == docked_moves
        clock.t += 1
        _tick(m, _taskbar(hwnd=777))               # the new taskbar
        assert m.holding_for_taskbar(_taskbar(hwnd=777)) is False


def test_hold_is_bounded_then_the_old_fallback_applies(caplog):
    clock = _Clock()
    m, widget = _manager()
    with patch.object(pm_mod.time, "monotonic", side_effect=clock.monotonic):
        _tick(m, _taskbar())
        assert m.holding_for_taskbar(_taskbar(hwnd=0)) is True
        clock.t += HOLD_S - 0.1
        assert m.holding_for_taskbar(_taskbar(hwnd=0)) is True
        clock.t += 0.2
        assert m.holding_for_taskbar(_taskbar(hwnd=0)) is False
        with patch.object(m, "_apply_calculated_position", return_value=True) as calc:
            _tick(m, _taskbar(hwnd=0))
        calc.assert_called_once()                  # placed as before (the fallback spot)
        # A later restart gets a fresh hold once a real taskbar was seen again.
        _tick(m, _taskbar())
        clock.t += 100
        assert m.holding_for_taskbar(_taskbar(hwnd=0)) is True


def test_no_hold_without_a_taskbar_to_lose():
    """A start with no shell at all keeps today's fallback placement."""
    m, _widget = _manager()
    assert m.holding_for_taskbar(_taskbar(hwnd=0)) is False
    with patch.object(m, "_apply_calculated_position", return_value=True) as calc:
        _tick(m, _taskbar(hwnd=0))
    calc.assert_called_once()


def test_no_hold_for_free_move():
    m, _widget = _manager({"free_move": True})
    _tick(m, _taskbar())
    assert m.holding_for_taskbar(_taskbar(hwnd=0)) is False


# --- the widget's refresh hides it while the hold lasts -------------------------------------------


def _widget_self(holding, visible=True):
    w = MagicMock()
    w.config = {"preferred_monitor": None, "free_move": False, "keep_visible_fullscreen": False}
    w._is_context_menu_visible = False
    w._dragging = False
    w._taskbar_lost_count = 0
    w.isVisible.return_value = visible
    w.position_manager.refresh_float_state.return_value = None
    w.position_manager.holding_for_taskbar.return_value = holding
    return w


def _patched(monkeypatch, hwnd):
    monkeypatch.setattr(widget_main, "get_taskbar_info",
                        lambda preferred_screen_name=None: SimpleNamespace(hwnd=hwnd))
    monkeypatch.setattr(widget_main, "is_taskbar_visible", lambda tb: True)
    monkeypatch.setattr(widget_main, "is_taskbar_obstructed", lambda tb, hwnd: False)
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: 0)


def test_refresh_hides_the_widget_while_there_is_no_taskbar(monkeypatch):
    """Hidden from the SECOND consecutive no-taskbar tick; the first only holds (#440 review L5)."""
    _patched(monkeypatch, hwnd=0)
    w = _widget_self(holding=True)
    w._no_taskbar_ticks = 0
    widget_main.NetworkSpeedWidget._execute_refresh(w)
    w.setVisible.assert_not_called()
    widget_main.NetworkSpeedWidget._execute_refresh(w)
    w.setVisible.assert_called_once_with(False)
    w.position_manager.update_position.assert_not_called()


def test_single_transient_no_taskbar_tick_never_hides(monkeypatch):
    """One failed lookup between good ones: held in place, never hidden - no flicker (#440 L5)."""
    w = _widget_self(holding=True)
    w._no_taskbar_ticks = 0
    for hwnd in (0, 4242, 0, 4242):
        _patched(monkeypatch, hwnd=hwnd)
        w.position_manager.holding_for_taskbar.return_value = hwnd == 0
        widget_main.NetworkSpeedWidget._execute_refresh(w)
    assert (False,) not in [c.args for c in w.setVisible.call_args_list]


def test_refresh_shows_and_places_the_widget_once_the_taskbar_is_back(monkeypatch):
    _patched(monkeypatch, hwnd=4242)
    w = _widget_self(holding=False, visible=False)
    widget_main.NetworkSpeedWidget._execute_refresh(w)
    w.setVisible.assert_called_once_with(True)


def test_refresh_does_not_hold_a_free_floating_widget(monkeypatch):
    """Free-float sits on its own taskbar-less display: an Explorer restart must not hide it."""
    _patched(monkeypatch, hwnd=0)
    w = _widget_self(holding=True)
    w.position_manager.refresh_float_state.return_value = object()   # floating
    widget_main.NetworkSpeedWidget._execute_refresh(w)
    w.setVisible.assert_not_called()
