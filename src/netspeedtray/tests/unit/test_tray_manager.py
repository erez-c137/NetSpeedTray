"""
Unit tests for the TrayIconManager class.
"""

import pytest
from unittest.mock import MagicMock, patch, ANY
from PyQt6.QtWidgets import QMenu, QApplication, QWidget
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QPoint, QRect
from netspeedtray.core.tray_manager import TrayIconManager

@pytest.fixture
def mock_widget(q_app):
    """Provides a mock parent widget."""
    widget = QWidget()
    
    # Monkeypatch methods needed by the manager
    widget.rect = MagicMock()
    widget.rect.return_value = QRect(0, 0, 100, 100)
    
    # Mock mapToGlobal to return a predictable point
    def map_to_global(point):
        return QPoint(point.x() + 10, point.y() + 10)
    widget.mapToGlobal = MagicMock(side_effect=map_to_global)
    
    widget.screen = MagicMock(return_value=None)
    widget.setWindowIcon = MagicMock()
    widget._execute_refresh = MagicMock()
    
    return widget

@pytest.fixture
def mock_i18n():
    """Provides mock translations."""
    i18n = MagicMock()
    i18n.SETTINGS_MENU_ITEM = "Settings"
    i18n.EXIT_MENU_ITEM = "Exit"
    return i18n

@pytest.fixture(scope="session")
def q_app():
    """Provides a QApplication instance for the test session."""
    return QApplication.instance() or QApplication([])

def test_initialization_loads_icon_and_menu(mock_widget, mock_i18n, q_app):
    """Tests that initialization loads the icon and creates the menu."""
    with patch('os.path.exists', return_value=True):
        manager = TrayIconManager(mock_widget, mock_i18n)
        manager.initialize()
        
        # Verify icon was set
        mock_widget.setWindowIcon.assert_called_once()
        
        # Verify menu was created
        assert manager.context_menu is not None
        assert isinstance(manager.context_menu, QMenu)
        
        # Check menu content
        actions = manager.context_menu.actions()
        assert len(actions) >= 2
        assert actions[0].text() == "Settings"
        
        # Verify settings connection
        # (Qt signals are hard to verify without triggering, but we can check the mock call logic involved)
        # Here we just check the list of actions

def test_show_context_menu_calls_exec(mock_widget, mock_i18n, q_app):
    """Tests that show_context_menu calculates position and executes the menu."""
    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()
    
    # Mock the menu exec method
    manager.context_menu.exec = MagicMock()
    
    # Mock renderer for position calculation
    mock_renderer = MagicMock()
    mock_renderer.get_last_text_rect.return_value = QRect(0, 0, 100, 20)
    mock_widget.renderer = mock_renderer
    
    manager.show_context_menu()
    
    # Assertions
    manager.context_menu.exec.assert_called_once()
    # Ensure it refreshed the widget after closing
    mock_widget._execute_refresh.assert_called_once()

def test_menu_position_calculation_fallback(mock_widget, mock_i18n, q_app):
    """Tests that menu position falls back gracefully if renderer is missing."""
    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()
    
    # Remove renderer to force fallback
    if hasattr(mock_widget, 'renderer'):
        del mock_widget.renderer
    
    manager.context_menu.exec = MagicMock()
    manager.show_context_menu()
    
    manager.context_menu.exec.assert_called_once()
    # Just ensure it didn't crash
