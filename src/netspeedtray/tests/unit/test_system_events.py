"""
Unit tests for the SystemEventHandler class.
"""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QObject
from netspeedtray.core.system_events import SystemEventHandler

@pytest.fixture
def mock_hooks():
    """Patches WinEventHook to avoid real Windows API calls during tests."""
    with patch('netspeedtray.core.system_events.WinEventHook') as MockHook:
        mock_instance = MockHook.return_value
        yield MockHook, mock_instance

@pytest.fixture
def system_handler(q_app, mock_hooks):
    """Provides a SystemEventHandler instance."""
    handler = SystemEventHandler()
    yield handler
    handler.stop()

def test_initialization(system_handler):
    """Tests that the handler initializes without errors."""
    assert system_handler.foreground_hook is None
    assert system_handler.movesize_hook is None


def test_timer_connections_do_not_accumulate_across_restarts(system_handler):
    """#11/#12: repeated start/stop cycles (one per Explorer restart) must NOT add duplicate timeout
    connections — the slots are connected once in __init__, so the receiver count stays 1 no matter how
    many times the timers are (re)started. Previously each restart added another connection."""
    h = system_handler
    vt, ft = h._taskbar_validity_timer, h._fullscreen_poll_timer
    assert vt.receivers(vt.timeout) == 1 and ft.receivers(ft.timeout) == 1   # connected once at init
    for _ in range(5):          # simulate five Explorer-restart stop()/start() cycles
        h._setup_timers()
        h.stop()
    assert vt.receivers(vt.timeout) == 1 and ft.receivers(ft.timeout) == 1   # still exactly one

def test_start_creates_hooks(system_handler, mock_hooks):
    """Tests that start() initializes and starts the hooks."""
    MockHook, mock_instance = mock_hooks
    
    with patch('netspeedtray.core.system_events.get_taskbar_info') as mock_get_tb:
        mock_get_tb.return_value.hwnd = 12345
        
        system_handler.start()
        
        # Check hooks were created
        assert system_handler.foreground_hook is not None
        assert system_handler.movesize_hook is not None
        
        # Check hooks were started
        assert mock_instance.start.call_count == 2 

def test_immediate_hide_signal_emitted(system_handler):
    """Tests that the immediate hide signal is emitted when fullscreen is detected."""
    # Connect a mock slot to the signal
    mock_slot = MagicMock()
    system_handler.immediate_hide_requested.connect(mock_slot)

    # Mock the necessary taskbar/window utils. GetClassName must return a NON-shell
    # class so the early taskbar-focus path is skipped and the fullscreen check runs.
    with patch('netspeedtray.core.system_events.get_taskbar_info') as mock_get_tb, \
         patch('netspeedtray.core.system_events.is_taskbar_obstructed', return_value=True), \
         patch('win32gui.GetClassName', return_value='SomeFullscreenApp'), \
         patch('win32gui.GetWindowRect', return_value=(0, 0, 1920, 1080)), \
         patch('win32api.GetMonitorInfo', return_value={'Monitor': (0, 0, 1920, 1080)}), \
         patch('win32gui.IsWindow', return_value=True):

        # Simulate the event
        system_handler._on_foreground_change_immediate(hwnd=999)

        # Verify signal emission
        mock_slot.assert_called_once()


def test_taskbar_focus_emits_topmost_signal_not_hide(system_handler):
    """When the taskbar/shell itself gains focus, taskbar_focused must fire immediately
    (so the widget re-asserts topmost) and the fullscreen-hide path must NOT run."""
    focus_slot = MagicMock()
    hide_slot = MagicMock()
    system_handler.taskbar_focused.connect(focus_slot)
    system_handler.immediate_hide_requested.connect(hide_slot)

    with patch('win32gui.IsWindow', return_value=True), \
         patch('win32gui.GetClassName', return_value='Shell_TrayWnd'), \
         patch('netspeedtray.core.system_events.is_taskbar_obstructed') as mock_obstructed:

        system_handler._on_foreground_change_immediate(hwnd=777)

        focus_slot.assert_called_once()
        hide_slot.assert_not_called()
        # Early return before any obstruction work.
        mock_obstructed.assert_not_called()

def test_fullscreen_poll_hides_when_foreground_is_fullscreen(system_handler):
    """The fast poll must emit immediate_hide when the foreground window is a true
    fullscreen window obstructing the taskbar (the case that fires no foreground event)."""
    hide_slot = MagicMock()
    system_handler.immediate_hide_requested.connect(hide_slot)

    with patch('win32gui.GetForegroundWindow', return_value=555), \
         patch('win32gui.IsWindow', return_value=True), \
         patch('win32gui.GetWindowRect', return_value=(0, 0, 1920, 1080)), \
         patch('win32api.GetMonitorInfo', return_value={'Monitor': (0, 0, 1920, 1080)}), \
         patch('win32api.MonitorFromWindow', return_value=1), \
         patch('netspeedtray.core.system_events.get_taskbar_info'), \
         patch('netspeedtray.core.system_events.is_taskbar_obstructed', return_value=True):
        system_handler._poll_fullscreen()

    hide_slot.assert_called_once()


def test_fullscreen_poll_is_noop_and_cheap_when_not_fullscreen(system_handler):
    """When the foreground window is NOT fullscreen, the poll must NOT hide and must
    short-circuit BEFORE any taskbar query (the cheap common path)."""
    hide_slot = MagicMock()
    system_handler.immediate_hide_requested.connect(hide_slot)

    with patch('win32gui.GetForegroundWindow', return_value=555), \
         patch('win32gui.IsWindow', return_value=True), \
         patch('win32gui.GetWindowRect', return_value=(100, 100, 800, 600)), \
         patch('win32api.GetMonitorInfo', return_value={'Monitor': (0, 0, 1920, 1080)}), \
         patch('win32api.MonitorFromWindow', return_value=1), \
         patch('netspeedtray.core.system_events.is_taskbar_obstructed') as mock_obstructed:
        system_handler._poll_fullscreen()

    hide_slot.assert_not_called()
    mock_obstructed.assert_not_called()  # short-circuited before the taskbar query


def test_fullscreen_poll_shows_on_exit_edge(system_handler):
    """When fullscreen ends (edge True->False), the poll re-evaluates visibility via
    foreground_app_changed so the widget returns without waiting for the 1s tick."""
    refresh_slot = MagicMock()
    system_handler.foreground_app_changed.connect(refresh_slot)
    system_handler._was_fullscreen = True  # pretend we were fullscreen

    with patch('win32gui.GetForegroundWindow', return_value=42), \
         patch('win32gui.IsWindow', return_value=True), \
         patch('win32gui.GetWindowRect', return_value=(100, 100, 800, 600)), \
         patch('win32api.GetMonitorInfo', return_value={'Monitor': (0, 0, 1920, 1080)}), \
         patch('win32api.MonitorFromWindow', return_value=1):
        system_handler._poll_fullscreen()

    refresh_slot.assert_called_once_with(42)
    assert system_handler._was_fullscreen is False


def test_fullscreen_poll_quiet_when_state_unchanged(system_handler):
    """No edge -> no signals at all (quiet during steady state)."""
    hide_slot, refresh_slot = MagicMock(), MagicMock()
    system_handler.immediate_hide_requested.connect(hide_slot)
    system_handler.foreground_app_changed.connect(refresh_slot)
    system_handler._was_fullscreen = False

    with patch('win32gui.GetForegroundWindow', return_value=42), \
         patch('win32gui.IsWindow', return_value=True), \
         patch('win32gui.GetWindowRect', return_value=(100, 100, 800, 600)), \
         patch('win32api.GetMonitorInfo', return_value={'Monitor': (0, 0, 1920, 1080)}), \
         patch('win32api.MonitorFromWindow', return_value=1):
        system_handler._poll_fullscreen()

    hide_slot.assert_not_called()
    refresh_slot.assert_not_called()


def test_fullscreen_poll_swallows_exceptions(system_handler):
    """A win32 failure inside the poll must NOT escape the timer slot (it would log a
    traceback every 250ms / break the poll's reliability). Regression for the dropped
    try/except in the edge-trigger refactor."""
    hide_slot = MagicMock()
    system_handler.immediate_hide_requested.connect(hide_slot)

    with patch('win32gui.GetForegroundWindow', side_effect=RuntimeError("boom")):
        system_handler._poll_fullscreen()  # must not raise

    hide_slot.assert_not_called()


def test_taskbar_restarted_signal(system_handler, mock_hooks):
    """Tests that taskbar_restarted is emitted when the handle becomes invalid."""
    MockHook, mock_instance = mock_hooks
    mock_slot = MagicMock()
    system_handler.taskbar_restarted.connect(mock_slot)

    # Create the hook mock with a fake HWND
    mock_instance.hwnd_to_watch = 12345
    system_handler.movesize_hook = mock_instance

    with patch('win32gui.IsWindow', return_value=False): # Force invalid window check
        system_handler._check_taskbar_validity()

        mock_slot.assert_called_once()


def test_color_scheme_change_emits_theme_changed_signal(system_handler):
    """Regression for #62: Qt's colorSchemeChanged must re-emit theme_changed.

    Bypasses the actual Qt signal wiring (which requires a running OS theme
    change) and calls the handler directly to confirm the re-emit path works.
    """
    mock_slot = MagicMock()
    system_handler.theme_changed.connect(mock_slot)

    # Simulate Qt firing the colorSchemeChanged signal
    from PyQt6.QtCore import Qt
    system_handler._on_color_scheme_changed(Qt.ColorScheme.Dark)

    mock_slot.assert_called_once()


def test_color_scheme_change_suppressed_when_paused(system_handler):
    """When events are paused (e.g. settings dialog open), theme changes
    should not fire — same contract as other system events."""
    mock_slot = MagicMock()
    system_handler.theme_changed.connect(mock_slot)
    system_handler._is_paused = True

    from PyQt6.QtCore import Qt
    system_handler._on_color_scheme_changed(Qt.ColorScheme.Light)

    mock_slot.assert_not_called()
