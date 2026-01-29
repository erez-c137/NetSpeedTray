"""
Unit tests for the decomposed Settings Pages.
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtGui import QFont

from netspeedtray.views.settings.pages.general import GeneralPage
from netspeedtray.views.settings.pages.appearance import AppearancePage
from netspeedtray.views.settings.pages.graph_config import GraphPage
from netspeedtray.views.settings.pages.units import UnitsPage
from netspeedtray.views.settings.pages.interfaces import InterfacesPage
from netspeedtray.views.settings.pages.colors import ColorsPage
from netspeedtray import constants

@pytest.fixture(scope="session")
def q_app():
    """Provides a QApplication instance for the test session."""
    return QApplication.instance() or QApplication([])

@pytest.fixture
def mock_i18n():
    i18n = MagicMock(spec=constants.I18nStrings)
    # Mock necessary attributes as strings to avoid PyQt TypeError
    i18n.LANGUAGE_MAP = {"en_US": "English", "fr_FR": "French"}
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
    i18n.SCALING_LABEL = "Scaling"
    i18n.INTERFACE_LAYOUT_GROUP = "Interface Layout"
    i18n.ARROW_STYLING_GROUP = "Arrow Styling"
    i18n.POSITION_GROUP = "Positioning"
    i18n.USE_CUSTOM_ARROW_FONT = "Use Custom Arrow Font"
    i18n.FONT_WEIGHT_DEMIBOLD = "Demibold"
    i18n.FONT_WEIGHT_NORMAL = "Normal"
    i18n.FONT_WEIGHT_BOLD = "Bold"
    
    # New v1.3.0 Keys
    i18n.BACKGROUND_SETTINGS_GROUP_TITLE = "Background"
    i18n.BACKGROUND_COLOR_LABEL = "Bg Color"
    i18n.BACKGROUND_COLOR_TOOLTIP = "Select Bg"
    i18n.BACKGROUND_OPACITY_LABEL = "Opacity"
    i18n.SHORT_UNIT_LABELS_LABEL = "Short Labels"

    # Font Weight Labels (used in Win11Slider.setValueText)
    for key in constants.fonts.WEIGHT_MAP.values():
        if not hasattr(i18n, key):
            setattr(i18n, key, key.replace("FONT_WEIGHT_", "").capitalize())

    return i18n

@pytest.fixture
def mock_callback():
    return MagicMock()

def test_general_page(q_app, mock_i18n, mock_callback):
    """Test GeneralPage load and get settings."""
    page = GeneralPage(mock_i18n, mock_callback)
    
    config = {
        "language": "fr_FR",
        "update_rate": 1.5,
        "dynamic_update_enabled": True,
        "free_move": True,
        "start_with_windows": True 
    }
    
    page.load_settings(config, is_startup_enabled=True)
    
    settings = page.get_settings()
    assert settings["language"] == "fr_FR"
    assert settings["update_rate"] == 1.5
    assert settings["dynamic_update_enabled"] is True
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
        "font_weight": 600,
        "default_color": "#FF0000",
        "background_color": "#000000",
        "background_opacity": 50,
        "use_separate_arrow_font": False,
        "arrow_font_family": "Arial",
        "arrow_font_size": 10
    }
    
    with patch("PyQt6.QtGui.QFontDatabase.styles", return_value=["Normal", "Bold"]):
         page.load_settings(config)
    
    settings = page.get_settings()
    assert settings["font_family"] == "Arial"
    assert settings["font_size"] == 10
    assert settings["default_color"] == "#FF0000"

def test_colors_page(q_app, mock_i18n, mock_callback):
    """Test ColorsPage."""
    color_cb = MagicMock()
    page = ColorsPage(mock_i18n, mock_callback, color_cb)
    
    config = {
        "color_coding": True,
        "high_speed_threshold": 50,
        "low_speed_threshold": 10,
        "high_speed_color": "#00FF00",
        "low_speed_color": "#FFFF00"
    }
    
    page.load_settings(config)
    settings = page.get_settings()
    
    assert settings["color_coding"] is True
    assert settings["high_speed_threshold"] == 50
    assert settings["low_speed_threshold"] == 10
    assert settings["high_speed_color"] == "#00FF00"
    assert settings["low_speed_color"] == "#FFFF00"

def test_graph_page(q_app, mock_i18n, mock_callback):
    """Test GraphPage."""
    page = GraphPage(mock_i18n, mock_callback)
    
    config = {
        "graph_enabled": False,
        "history_minutes": 30,
        "graph_opacity": 80
    }
    
    page.load_settings(config)
    settings = page.get_settings()
    
    assert settings["graph_enabled"] is False
    assert settings["history_minutes"] == 30
    assert settings["graph_opacity"] == 80

def test_units_page(q_app, mock_i18n, mock_callback):
    """Test UnitsPage."""
    page = UnitsPage(mock_i18n, mock_callback)
    
    config = {
        "unit_type": "bits_binary",
        "speed_display_mode": "always_mbps",
        "decimal_places": 2,
        "text_alignment": "center",
        "swap_upload_download": False,
        "hide_arrows": True,
        "hide_unit_suffix": True,
        "short_unit_labels": False,
        "tray_offset_x": 10
    }
    
    page.load_settings(config)
    settings = page.get_settings()
    
    assert settings["unit_type"] == "bits_binary"
    assert settings["speed_display_mode"] == "always_mbps"
    assert settings["decimal_places"] == 2
    assert settings["text_alignment"] == "center"
    assert settings["swap_upload_download"] is False
    assert settings["hide_arrows"] is True
    assert settings["hide_unit_suffix"] is True
    assert settings["short_unit_labels"] is False
    assert settings["tray_offset_x"] == 10

def test_interfaces_page(q_app, mock_i18n, mock_callback):
    """Test InterfacesPage."""
    available = ["Ethernet", "Wi-Fi"]
    page = InterfacesPage(mock_i18n, available, mock_callback)
    
    config = {
        "interface_mode": "selected",
        "selected_interfaces": ["Ethernet"]
    }
    
    page.load_settings(config)
    settings = page.get_settings()
    
    assert settings["interface_mode"] == "selected"
    assert "Ethernet" in settings["selected_interfaces"]
    assert "Wi-Fi" not in settings["selected_interfaces"]
