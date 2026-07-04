"""#188 - free-float on a taskbar-less display: detection, the is_floating() predicate, and placement."""
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from netspeedtray.utils import taskbar_utils as tu
from netspeedtray.core.position_manager import PositionCalculator, PositionManager, WindowState, ScreenPosition


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _screen(name, geo):
    s = MagicMock()
    s.name.return_value = name
    s.availableGeometry.return_value = geo
    s.geometry.return_value = geo
    return s


def _manager(config):
    state = WindowState(config=config, widget=MagicMock())
    return PositionManager(state)


# --- get_free_float_screen -----------------------------------------------------------

def test_no_preferred_is_no_float():
    assert tu.get_free_float_screen(None) is None
    assert tu.get_free_float_screen("") is None


@patch.object(tu, "QApplication")
def test_preferred_disconnected_is_no_float(mock_qapp):
    mock_qapp.screens.return_value = [_screen("\\\\.\\DISPLAY1", QRect(0, 0, 1920, 1080))]
    assert tu.get_free_float_screen("\\\\.\\DISPLAY_GONE") is None


@patch.object(tu, "get_all_taskbar_info")
@patch.object(tu, "_select_taskbar_for_screen")
@patch.object(tu, "QApplication")
def test_preferred_with_taskbar_docks_normally(mock_qapp, mock_select, mock_all):
    scr = _screen("EDGE", QRect(1920, 0, 800, 600))
    mock_qapp.screens.return_value = [scr]
    mock_all.return_value = []
    mock_select.return_value = MagicMock()   # a taskbar exists on EDGE
    assert tu.get_free_float_screen("EDGE") is None


@patch.object(tu, "get_all_taskbar_info")
@patch.object(tu, "_select_taskbar_for_screen")
@patch.object(tu, "QApplication")
def test_preferred_taskbarless_returns_that_screen(mock_qapp, mock_select, mock_all):
    scr = _screen("EDGE", QRect(1920, 0, 800, 600))
    mock_qapp.screens.return_value = [scr]
    mock_all.return_value = []
    mock_select.return_value = None          # no taskbar on EDGE -> float on it
    assert tu.get_free_float_screen("EDGE") is scr


# --- is_floating() / refresh_float_state() -------------------------------------------

def test_free_move_is_floating():
    assert _manager({"free_move": True, "free_float": True}).is_floating() is True


def test_free_float_active_is_floating_without_free_move():
    m = _manager({"free_move": False, "free_float": True})
    m._free_float_active = True
    assert m.is_floating() is True


def test_docked_is_not_floating():
    m = _manager({"free_move": False, "free_float": True})
    m._free_float_active = False
    assert m.is_floating() is False


@patch.object(tu, "get_free_float_screen")
def test_refresh_float_state_activates(mock_gffs):
    scr = MagicMock()
    mock_gffs.return_value = scr
    m = _manager({"free_move": False, "free_float": True, "preferred_monitor": "EDGE"})
    assert m.refresh_float_state() is scr
    assert m._free_float_active is True
    assert m._free_float_screen is scr


@patch.object(tu, "get_free_float_screen")
def test_refresh_float_state_optout_skips_detection(mock_gffs):
    m = _manager({"free_move": False, "free_float": False, "preferred_monitor": "EDGE"})
    assert m.refresh_float_state() is None
    assert m._free_float_active is False
    mock_gffs.assert_not_called()   # the opt-out means we don't even query


# --- is_free_float_active() ----------------------------------------------------------

def test_is_free_float_active_true_only_for_runtime_float():
    m = _manager({"free_move": False, "free_float": True})
    m._free_float_active = True
    assert m.is_free_float_active() is True


def test_is_free_float_active_false_for_plain_free_move():
    # Free Move floats, but is NOT the taskbar-less runtime float - callers that guard the
    # off-taskbar widget (e.g. the fullscreen immediate-hide) must not treat it as such.
    m = _manager({"free_move": True, "free_float": True})
    m._free_float_active = False
    assert m.is_free_float_active() is False
    assert m.is_floating() is True   # ... but it IS floating


# --- immediate fullscreen-hide guard (#188) ------------------------------------------

def _fake_widget(keep_visible, free_float_active):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = MagicMock(spec=NetworkSpeedWidget)
    w.config = {"keep_visible_fullscreen": keep_visible}
    w.position_manager = MagicMock()
    w.position_manager.is_free_float_active.return_value = free_float_active
    return w


def test_immediate_hide_fires_when_docked_and_not_kept():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = _fake_widget(keep_visible=False, free_float_active=False)
    NetworkSpeedWidget._on_immediate_hide_requested(w)
    w.setVisible.assert_called_once_with(False)


def test_immediate_hide_skipped_when_keep_visible():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = _fake_widget(keep_visible=True, free_float_active=False)
    NetworkSpeedWidget._on_immediate_hide_requested(w)
    w.setVisible.assert_not_called()


def test_immediate_hide_skipped_when_free_floating():
    # The regression: a fullscreen app on the primary must NOT blink the off-taskbar widget out.
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = _fake_widget(keep_visible=False, free_float_active=True)
    NetworkSpeedWidget._on_immediate_hide_requested(w)
    w.setVisible.assert_not_called()


# --- default placement ---------------------------------------------------------------

def test_free_float_default_position_is_inside_the_target_screen():
    calc = PositionCalculator()
    scr = _screen("EDGE", QRect(1920, 0, 800, 600))   # a second display at x=1920, 800x600
    pos = calc.calculate_free_float_default_position(scr, (100, 40))
    assert isinstance(pos, ScreenPosition)
    # bottom-right of the *target* screen (not primary), fully on-screen
    assert 1920 <= pos.x <= (1920 + 800 - 100)
    assert 0 <= pos.y <= (600 - 40)
