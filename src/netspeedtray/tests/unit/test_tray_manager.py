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
    i18n.SHOW_GRAPH_MENU_ITEM = "Show Graph"
    i18n.APP_USAGE_TAB_LABEL = "App Usage"
    i18n.SHOW_APP_ACTIVITY_MENU_ITEM = "Show App Usage"
    i18n.PAUSE_MENU_ITEM = "Pause"
    i18n.RESUME_MENU_ITEM = "Resume"
    i18n.CHECK_FOR_UPDATES_MENU_ITEM = "Check for Updates"
    i18n.SUPPORT_MENU_ITEM = "Support"
    i18n.EXIT_MENU_ITEM = "Exit"
    i18n.USAGE_TODAY_LABEL = "Today"
    i18n.USAGE_THIS_MONTH_LABEL = "This Month"
    # 2.0 i18n keys used by the tray (real strings so templates format correctly)
    i18n.TRAY_DATA_CAP_MENU_ITEM = "Data cap…"
    i18n.SHOW_ME_AROUND_LABEL = "Show me around"
    i18n.TRAY_HARDWARE_MONITOR_TEMPLATE = "Hardware monitor: {state}  ▸"
    i18n.TRAY_HARDWARE_STATE_ON = "On"
    i18n.TRAY_HARDWARE_STATE_OFF = "Off"
    i18n.TRAY_USAGE_GLANCE_TEMPLATE = "{label}:   ↓ {down_v:.1f} {down_u}   ↑ {up_v:.1f} {up_u}"
    i18n.TRAY_DATA_CAP_PROGRESS_TEMPLATE = "Data cap:   {used:.1f} / {cap:g} GB   ({pct:.0f}%)"
    for k, v in {"BYTES_UNIT": "B", "KB_UNIT": "KB", "MB_UNIT": "MB",
                 "GB_UNIT": "GB", "TB_UNIT": "TB", "PB_UNIT": "PB"}.items():
        setattr(i18n, k, v)
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
        
        # Check menu content (order-independent: usage rows now precede Settings)
        actions = manager.context_menu.actions()
        assert len(actions) >= 2
        assert any(a.text() == "Settings" for a in actions)
        
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

def test_toggle_pause_routes_to_pause_then_resume(mock_widget, mock_i18n, q_app):
    """The tray pause action calls widget.pause() when running and widget.resume() when paused."""
    mock_widget.pause = MagicMock()
    mock_widget.resume = MagicMock()
    mock_widget.is_paused = False

    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()

    manager._toggle_pause()                 # running -> pause
    mock_widget.pause.assert_called_once()
    mock_widget.resume.assert_not_called()

    mock_widget.is_paused = True
    manager._toggle_pause()                 # paused -> resume
    mock_widget.resume.assert_called_once()


def test_refresh_dynamic_items_toggles_pause_label(mock_widget, mock_i18n, q_app):
    """The pause action label flips between Pause/Resume with the widget's state on open."""
    mock_i18n.PAUSE_MENU_ITEM = "Pause"
    mock_i18n.RESUME_MENU_ITEM = "Resume"
    mock_widget.is_paused = False

    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()

    manager._refresh_dynamic_items()
    assert manager.pause_action.text() == "Pause"

    mock_widget.is_paused = True
    manager._refresh_dynamic_items()
    assert manager.pause_action.text() == "Resume"


def test_usage_line_formats_and_caches(mock_widget, mock_i18n, q_app):
    """The Today/This-Month usage rows show formatted totals and are cached (one DB
    query pair per TTL window, not per menu-open)."""
    for k, v in {"BYTES_UNIT": "B", "KB_UNIT": "KB", "MB_UNIT": "MB",
                 "GB_UNIT": "GB", "TB_UNIT": "TB", "PB_UNIT": "PB"}.items():
        setattr(mock_i18n, k, v)
    mock_i18n.USAGE_TODAY_LABEL = "Today"
    mock_i18n.USAGE_THIS_MONTH_LABEL = "This Month"

    state = MagicMock()
    state.get_total_bandwidth_for_period.return_value = (1024 * 1024, 2 * 1024 * 1024)  # 1 MB up, 2 MB down
    mock_widget.widget_state = state

    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()

    manager._refresh_dynamic_items()
    today_text = manager.usage_today_action.text()
    assert "Today" in today_text and "MB" in today_text
    assert state.get_total_bandwidth_for_period.call_count == 2  # today + month

    manager._refresh_dynamic_items()  # within TTL -> served from cache
    assert state.get_total_bandwidth_for_period.call_count == 2


def test_hardware_state_line_reflects_config(mock_widget, mock_i18n, q_app):
    """The self-describing hardware row reads On/Off from the widget config on open."""
    mock_widget.config = {"monitor_cpu_enabled": False, "monitor_gpu_enabled": False}
    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()

    manager._refresh_dynamic_items()
    assert "Off" in manager.hardware_action.text()

    mock_widget.config = {"monitor_gpu_enabled": True}
    manager._refresh_dynamic_items()
    assert "On" in manager.hardware_action.text()


def test_show_me_around_entry_present(mock_widget, mock_i18n, q_app):
    """The re-discovery entry exists so a returning user can re-open the tour."""
    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()
    texts = [a.text() for a in manager.context_menu.actions()]
    assert any("Show me around" in t for t in texts)


def test_usage_rows_are_clickable_doors(mock_widget, mock_i18n, q_app):
    """The usage glance rows are enabled and open the graph when clicked (unfold-the-knife)."""
    mock_widget.open_graph_window = MagicMock()
    manager = TrayIconManager(mock_widget, mock_i18n)
    manager.initialize()
    assert manager.usage_today_action.isEnabled()
    manager._open_graph()
    mock_widget.open_graph_window.assert_called_once()


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
