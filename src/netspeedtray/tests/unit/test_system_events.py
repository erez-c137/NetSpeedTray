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
    
    # Mock the necessary taskbar/window utils
    with patch('netspeedtray.core.system_events.get_taskbar_info') as mock_get_tb, \
         patch('netspeedtray.core.system_events.is_taskbar_obstructed', return_value=True), \
         patch('win32gui.GetWindowRect', return_value=(0, 0, 1920, 1080)), \
         patch('win32api.GetMonitorInfo', return_value={'Monitor': (0, 0, 1920, 1080)}), \
         patch('win32gui.IsWindow', return_value=True):
         
        # Simulate the event
        system_handler._on_foreground_change_immediate(hwnd=999)
        
        # Verify signal emission
        mock_slot.assert_called_once()

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
