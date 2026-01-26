"""
Unit tests for the decomposed Settings Pages.
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from netspeedtray.views.settings.pages.general import GeneralPage
from netspeedtray.views.settings.pages.appearance import AppearancePage
from netspeedtray.views.settings.pages.graph_config import GraphPage
from netspeedtray.views.settings.pages.units import UnitsPage
from netspeedtray.views.settings.pages.interfaces import InterfacesPage

@pytest.fixture
def mock_i18n():
    i18n = MagicMock()
    # Mock necessary attributes
    i18n.LANGUAGE_MAP = {"en": "English", "fr": "French"}
    i18n.LANGUAGE_LABEL = "Language"
    i18n.UPDATE_RATE_GROUP_TITLE = "Update Rate"
    i18n.UPDATE_INTERVAL_LABEL = "Interval"
    i18n.OPTIONS_GROUP_TITLE = "Options"
    i18n.DYNAMIC_UPDATE_RATE_LABEL = "Dynamic Update"
    i18n.START_WITH_WINDOWS_LABEL = "Start with Windows"
    i18n.FREE_MOVE_LABEL = "Free Move"
    i18n.FONT_SETTINGS_GROUP_TITLE = "Font"
    i18n.FONT_FAMILY_LABEL = "Family"
    i18n.SELECT_FONT_BUTTON = "Select"
    i18n.DEFAULT_COLOR_LABEL = "Color"
    i18n.DEFAULT_COLOR_TOOLTIP = "Select Color"
    i18n.FONT_SIZE_LABEL = "Size"
    i18n.FONT_WEIGHT_LABEL = "Weight"
    i18n.COLOR_CODING_GROUP = "Color Coding"
    i18n.ENABLE_COLOR_CODING_LABEL = "Enable"
    i18n.HIGH_SPEED_THRESHOLD_LABEL = "High"
    i18n.LOW_SPEED_THRESHOLD_LABEL = "Low"
    i18n.HIGH_SPEED_COLOR_LABEL = "High Color"
    i18n.HIGH_SPEED_COLOR_TOOLTIP = "Select High Color"
    i18n.LOW_SPEED_COLOR_LABEL = "Low Color"
    i18n.LOW_SPEED_COLOR_TOOLTIP = "Select Low Color"
    i18n.MINI_GRAPH_SETTINGS_GROUP = "Graph"
    i18n.ENABLE_GRAPH_LABEL = "Enable"
    i18n.GRAPH_NOTE_TEXT = "Note"
    i18n.HISTORY_DURATION_LABEL = "History"
    i18n.GRAPH_OPACITY_LABEL = "Opacity"
    i18n.UNIT_TYPE_LABEL = "Unit Type"
    i18n.UNIT_TYPE_BITS_DECIMAL = "Bits (Dec)"
    i18n.UNIT_TYPE_BITS_BINARY = "Bits (Bin)"
    i18n.UNIT_TYPE_BYTES_DECIMAL = "Bytes (Dec)"
    i18n.UNIT_TYPE_BYTES_BINARY = "Bytes (Bin)"
    i18n.DECIMAL_PLACES_LABEL = "Decimals"
    i18n.TEXT_ALIGNMENT_LABEL = "Align"
    i18n.SWAP_UPLOAD_DOWNLOAD_LABEL = "Swap"
    i18n.FIXED_WIDTH_VALUES_LABEL = "Fixed Width"
    i18n.HIDE_ARROWS_LABEL = "Hide Arrows"
    i18n.HIDE_UNIT_SUFFIX_LABEL = "Hide Units"
    i18n.TRAY_OFFSET_LABEL = "Offset"
    i18n.NETWORK_INTERFACES_GROUP = "Interfaces"
    i18n.MONITORING_MODE_LABEL = "Mode"
    i18n.MONITORING_MODE_AUTO = "Auto"
    i18n.MONITORING_MODE_PHYSICAL = "Physical"
    i18n.MONITORING_MODE_VIRTUAL = "Virtual"
    i18n.MONITORING_MODE_SELECTED = "Selected"
    i18n.MONITORING_MODE_AUTO_TOOLTIP = "Auto Tooltip"
    i18n.MONITORING_MODE_PHYSICAL_TOOLTIP = "Physical Tooltip"
    i18n.MONITORING_MODE_VIRTUAL_TOOLTIP = "Virtual Tooltip"
    i18n.MONITORING_MODE_SELECTED_TOOLTIP = "Selected Tooltip"
    i18n.EXPORT_ERROR_LOG_TOOLTIP = "Export Log"
    i18n.NO_INTERFACES_FOUND = "None"
    i18n.TROUBLESHOOTING_GROUP = "Troubleshooting"
    i18n.EXPORT_ERROR_LOG_BUTTON = "Export"
    i18n.DISPLAY_FORMAT_GROUP = "Data Format"
    i18n.SCALING_LABEL = "Scaling" # Added
    i18n.INTERFACE_LAYOUT_GROUP = "Interface Layout"
    i18n.POSITION_GROUP = "Positioning"
    
    # New v1.3.0 Keys
    i18n.BACKGROUND_SETTINGS_GROUP_TITLE = "Background"
    i18n.BACKGROUND_COLOR_LABEL = "Bg Color"
    i18n.BACKGROUND_COLOR_TOOLTIP = "Select Bg"
    i18n.BACKGROUND_OPACITY_LABEL = "Opacity"
    i18n.SHORT_UNIT_LABELS_LABEL = "Short Labels"

    return i18n

@pytest.fixture
def mock_callback():
    return MagicMock()

def test_general_page(q_app, mock_i18n, mock_callback):
    """Test GeneralPage load and get settings."""
    page = GeneralPage(mock_i18n, mock_callback)
    
    config = {
        "language": "fr",
        "update_rate": 1.5,
        "dynamic_update_rate": True,
        "free_move": True,
        "start_with_windows": True 
    }
    
    page.load_settings(config, is_startup_enabled=True)
    
    settings = page.get_settings()
    assert settings["language"] == "fr"
    assert settings["update_rate"] == 1.5
    assert settings["dynamic_update_rate"] is True
    assert settings["free_move"] is True
    assert settings["start_with_windows"] is True

def test_appearance_page(q_app, mock_i18n, mock_callback):
    """Test AppearancePage."""
    font_cb = MagicMock()
    color_cb = MagicMock()
    page = AppearancePage(mock_i18n, mock_callback, font_cb, color_cb)
    
    config = {
        "font_family": "Arial",
        "font_size": 10,
        "font_weight": 50, # Arbitrary int we map
        "default_color": "#FF0000",
        "color_coding_enabled": True,
        "high_speed_threshold": 50,
        "low_speed_threshold": 10
    }
    
    with patch("PyQt6.QtGui.QFontDatabase.styles", return_value=["Normal", "Bold"]):
         page.load_settings(config)
    
    settings = page.get_settings()
    assert settings["font_family"] == "Arial"
    assert settings["font_size"] == 10
    assert settings["default_color"] == "#FF0000"
    assert settings["color_coding_enabled"] is True

def test_graph_page(q_app, mock_i18n, mock_callback):
    """Test GraphPage."""
    page = GraphPage(mock_i18n, mock_callback)
    
    config = {
        "graph_enabled": False,
        "history_duration": 30,
        "graph_opacity": 80
    }
    
    page.load_settings(config)
    settings = page.get_settings()
    
    assert settings["graph_enabled"] is False
    assert settings["history_duration"] == 30
    assert settings["graph_opacity"] == 80

def test_units_page(q_app, mock_i18n, mock_callback):
    """Test UnitsPage."""
    assert isinstance(mock_i18n.DISPLAY_FORMAT_GROUP, str), f"Mock bad: {mock_i18n.DISPLAY_FORMAT_GROUP}"
    page = UnitsPage(mock_i18n, mock_callback)
    
    config = {
        "unit_type": "bytes_decimal",
        "speed_display_mode": "always_mbps",
        "decimal_places": 1,
        "text_alignment": "right",
        "swap_upload_download": True
    }
    
    page.load_settings(config)
    settings = page.get_settings()
    
    assert settings["unit_type"] == "bytes_decimal"
    assert settings["speed_display_mode"] == "always_mbps"
    assert settings["decimal_places"] == 1
    assert settings["text_alignment"] == "right"
    assert settings["swap_upload_download"] is True

def test_interfaces_page(q_app, mock_i18n, mock_callback):
    """Test InterfacesPage."""
    page = InterfacesPage(mock_i18n, ["Eth0", "Wlan0"], mock_callback)
    
    config = {
        "monitoring_mode": "selected",
        "selected_interfaces": ["Eth0"]
    }
    
    page.load_settings(config)
    settings = page.get_settings()
    
    assert settings["monitoring_mode"] == "selected"
    assert settings["selected_interfaces"] == ["Eth0"]
    
    # Test update list
    page.update_interface_list(["Eth1"])
    assert "Eth1" in page.interface_checkboxes
    assert "Eth0" not in page.interface_checkboxes
