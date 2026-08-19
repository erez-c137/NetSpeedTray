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
    i18n.MBITS_UNIT = "Mbps"  # threshold spin-box suffix (legacy key)

    # Unit labels for get_unit_labels_for_type - the Colors threshold suffix (PR #165) + Units page
    # resolve these. The unit constants store their own key-name as value, so getattr(i18n, "MBITS_LABEL")
    # is what the helper requests. Mega-position labels get realistic units so the suffix is assertable.
    _units = constants.network.units
    _unit_label_values = {
        "BITS_LABEL": "bps", "KBITS_LABEL": "Kbps", "MBITS_LABEL": "Mbps", "GBITS_LABEL": "Gbps",
        "KIBITS_LABEL": "Kibps", "MIBITS_LABEL": "Mibps", "GIBITS_LABEL": "Gibps",
        "BPS_LABEL": "B/s", "KBPS_LABEL": "KB/s", "MBPS_LABEL": "MB/s", "GBPS_LABEL": "GB/s",
        "BIBPS_LABEL": "B/s", "KIBPS_LABEL": "KiB/s", "MIBPS_LABEL": "MiB/s", "GIBPS_LABEL": "GiB/s",
    }
    for _attr in dir(_units):
        if _attr.endswith("_LABEL"):
            _key = getattr(_units, _attr)
            if isinstance(_key, str):
                setattr(i18n, _key, _unit_label_values.get(_key, _key))

    # Click actions (PR #165) - General page reads these via getattr-with-default, so they're optional;
    # set them so the rendered combos carry readable labels.
    i18n.INTERACTION_GROUP_TITLE = "Interaction"
    i18n.DOUBLE_CLICK_ACTION_LABEL = "Double-click action"
    i18n.MIDDLE_CLICK_ACTION_LABEL = "Middle-click action"
    i18n.CLICK_ACTION_OPEN_MONITOR_LABEL = "Open Monitor"
    i18n.CLICK_ACTION_SETTINGS_LABEL = "Open Settings"
    i18n.CLICK_ACTION_PAUSE_LABEL = "Pause / Resume"
    i18n.CLICK_ACTION_NONE_LABEL = "Nothing"
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
    i18n.ARROW_STYLE_LABEL = "Arrow style"
    i18n.POSITION_GROUP = "Positioning"
    i18n.USE_CUSTOM_ARROW_FONT = "Use Custom Arrow Font"
    i18n.USE_CUSTOM_ARROW_COLORS = "Custom arrow colors"
    i18n.ARROW_UP_COLOR_LABEL = "Upload arrow"
    i18n.ARROW_DOWN_COLOR_LABEL = "Download arrow"
    i18n.FONT_WEIGHT_DEMIBOLD = "Demibold"
    i18n.FONT_WEIGHT_NORMAL = "Normal"
    i18n.FONT_WEIGHT_BOLD = "Bold"
    
    # New v1.3.0 Keys
    i18n.BACKGROUND_SETTINGS_GROUP_TITLE = "Background"
    i18n.BACKGROUND_COLOR_LABEL = "Bg Color"
    i18n.BACKGROUND_COLOR_TOOLTIP = "Select Bg"
    i18n.BACKGROUND_OPACITY_LABEL = "Opacity"
    i18n.SHORT_UNIT_LABELS_LABEL = "Short Labels"
    i18n.KEEP_VISIBLE_FULLSCREEN_LABEL = "Keep Visible in Fullscreen"
    i18n.BEHAVIOR_GROUP_TITLE = "Behavior"
    i18n.MINI_GRAPH_SECTION_TITLE = "Mini Graph"
    i18n.HARDWARE_INDICATOR_STYLE_LABEL = "Indicator Style"
    i18n.MONITORING_MODE_AUTO_SUBTITLE = "Monitors the primary active connection"
    i18n.MONITORING_MODE_PHYSICAL_SUBTITLE = "Excludes VPNs and virtual adapters"
    i18n.MONITORING_MODE_VIRTUAL_SUBTITLE = "Includes VPNs, virtual adapters, and tunnels"
    i18n.MONITORING_MODE_SELECTED_SUBTITLE = "Choose specific interfaces from the list below"

    # For GeneralPage update rate slider
    i18n.SMART_MODE_LABEL = "Smart"
    i18n.UPDATE_MODE_AGGRESSIVE_LABEL = "Aggressive"
    i18n.UPDATE_MODE_BALANCED_LABEL = "Balanced"
    i18n.UPDATE_MODE_EFFICIENT_LABEL = "Efficient"
    i18n.UPDATE_MODE_POWER_SAVER_LABEL = "Power Saver"
    i18n.CHECK_FOR_UPDATES_LABEL = "Check for updates on startup"
    i18n.PREFERRED_MONITOR_LABEL = "Preferred Monitor"
    i18n.PREFERRED_MONITOR_PRIMARY = "Primary (auto)"

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
    
    # Test with fixed update rate
    config = {
        "language": "fr_FR",
        "update_rate": 2.0,
        "free_move": True,
        "start_with_windows": True,
        "tray_offset_x": 15
    }

    page.load_settings(config, is_startup_enabled=True)

    settings = page.get_settings()
    assert settings["language"] == "fr_FR"
    assert settings["update_rate"] == 2.0
    assert settings["start_with_windows"] is True
    # free_move / tray_offset_x moved to the Widget page in the 2.0 IA - General no longer returns them.
    assert "free_move" not in settings
    assert "tray_offset_x" not in settings

    # Test with Smart mode (update_rate = -1.0)
    config_smart = {
        "language": "en_US",
        "update_rate": -1.0,  # SMART sentinel
    }

    page.load_settings(config_smart, is_startup_enabled=False)
    settings_smart = page.get_settings()
    assert settings_smart["update_rate"] == -1.0  # Smart mode
    assert settings_smart["language"] == "en_US"


def test_language_none_round_trips_as_auto_detect(q_app, mock_i18n, mock_callback):
    """#2: config language=None (auto-detect) must round-trip - loading selects the Auto-detect row and
    saving returns None, NOT 'en_US' (which silently switched non-English users to English on any Save)."""
    page = GeneralPage(mock_i18n, mock_callback)
    page.load_settings({"language": None, "update_rate": 1.0}, is_startup_enabled=False)
    assert page.language_combo.currentIndex() == 0            # the "Auto-detect (system)" row is first
    assert page.get_settings()["language"] is None            # None round-trips, not 'en_US'
    # An explicit language still selects + returns its own code
    page.load_settings({"language": "fr_FR", "update_rate": 1.0}, is_startup_enabled=False)
    assert page.get_settings()["language"] == "fr_FR"


def _language_card_description(page):
    """The muted description on the Language SettingCard, or '' when there is none."""
    from netspeedtray.utils.components import SettingCard
    for card in page.findChildren(SettingCard):
        if card._title_label.text() == "Language":
            return getattr(card, "_desc_label", None).text() if hasattr(card, "_desc_label") else ""
    raise AssertionError("Language SettingCard not found")


def test_detected_language_is_named_on_the_language_card(q_app, mock_i18n, mock_callback):
    """#234: the auto-detect affordance must name what it resolved to. Without it the row is
    unfalsifiable - a user whose language silently fell back to English sees a correct-looking
    "Auto-detect (system)" and has no way to know, which is how this hid for a year.

    It lives on the card description, not the combo row: the combo is pinned to 220px and hard
    clips, so a composed row label (420-684px across our locales) was invisible everywhere."""
    mock_i18n.LANGUAGE_AUTO_DETECT = "Auto-detect (system)"
    mock_i18n.LANGUAGE_DETECTED_NOTE = "Detected: {language}"
    mock_i18n.detect_system_language.return_value = "fr_FR"

    page = GeneralPage(mock_i18n, mock_callback)

    assert page.language_combo.itemData(0) is None            # still the auto-detect row
    assert page.language_combo.itemText(0) == "Auto-detect (system)"   # row stays short
    assert _language_card_description(page) == "Detected: French"


def test_language_card_degrades_when_detection_fails(q_app, mock_i18n, mock_callback):
    """The note is cosmetic - a detection failure must never break the settings page."""
    mock_i18n.LANGUAGE_AUTO_DETECT = "Auto-detect (system)"
    mock_i18n.LANGUAGE_DETECTED_NOTE = "Detected: {language}"
    mock_i18n.detect_system_language.side_effect = OSError("no windll")

    page = GeneralPage(mock_i18n, mock_callback)

    assert page.language_combo.itemData(0) is None
    assert page.language_combo.itemText(0) == "Auto-detect (system)"
    assert _language_card_description(page) == ""
    assert page.get_settings()["language"] is None


@pytest.mark.parametrize("locale_code", list(constants.i18n.I18nStrings.LANGUAGE_MAP))
def test_combo_rows_are_never_composed_with_the_detected_language(q_app, mock_callback, locale_code):
    """The reason the detected language lives on the card description instead of the combo row.

    The combo is pinned to 220px and never grows to fit, and its closed label is hard-clipped
    rather than elided - a row composed as "<auto-detect> - <endonym>" measured 420-684px across
    our locales and was invisible in every one of them. Pixel widths depend on the platform font,
    so this asserts the structural property instead: every row is exactly one label."""
    i18n = constants.i18n.I18nStrings(locale_code)
    page = GeneralPage(i18n, mock_callback)

    expected = [i18n.LANGUAGE_AUTO_DETECT] + list(i18n.LANGUAGE_MAP.values())
    actual = [page.language_combo.itemText(r) for r in range(page.language_combo.count())]
    assert actual == expected


def test_language_dropdown_shows_every_language(q_app, mock_callback):
    """#237: Qt defaults to 10 visible rows and we ship 13 languages plus auto-detect, so the
    last entries sat below the fold - a zh_CN user could not see their own language."""
    real_i18n = constants.i18n.I18nStrings("en_US")
    page = GeneralPage(real_i18n, mock_callback)

    expected = len(real_i18n.LANGUAGE_MAP) + 1               # + the auto-detect row
    assert page.language_combo.count() == expected
    assert page.language_combo.maxVisibleItems() >= expected, \
        "every shipped language must be reachable without arrow-keying past the fold"

def test_general_click_actions_round_trip(q_app, mock_i18n, mock_callback):
    """#165: double/middle-click actions load + save through the General page."""
    page = GeneralPage(mock_i18n, mock_callback)

    # Defaults when the config omits them: double-click=open_monitor, middle-click=none.
    page.load_settings({"language": None, "update_rate": 1.0}, is_startup_enabled=False)
    s = page.get_settings()
    assert s["double_click_action"] == constants.config.defaults.DEFAULT_DOUBLE_CLICK_ACTION
    assert s["middle_click_action"] == constants.config.defaults.DEFAULT_MIDDLE_CLICK_ACTION

    # Explicit values round-trip.
    page.load_settings(
        {"language": None, "update_rate": 1.0,
         "double_click_action": "settings", "middle_click_action": "pause_resume"},
        is_startup_enabled=False)
    s = page.get_settings()
    assert s["double_click_action"] == "settings"
    assert s["middle_click_action"] == "pause_resume"


def test_colors_threshold_suffix_follows_unit_type(q_app, mock_i18n, mock_callback):
    """#165: the threshold spin-box suffix tracks the active unit type (was hardcoded ' Mbps')."""
    color_cb = MagicMock()
    page = ColorsPage(mock_i18n, mock_callback, color_cb)

    base = {"color_coding": True, "high_speed_threshold": 50, "low_speed_threshold": 10,
            "high_speed_color": "#00FF00", "low_speed_color": "#FFFF00"}

    page.load_settings({**base, "unit_type": "bits_decimal"})
    assert page.high_speed_threshold.suffix() == " Mbps"

    page.load_settings({**base, "unit_type": "bytes_decimal"})
    assert page.high_speed_threshold.suffix() == " MB/s"
    assert page.low_speed_threshold.suffix() == " MB/s"

    page.load_settings({**base, "unit_type": "bits_binary"})
    assert page.high_speed_threshold.suffix() == " Mibps"


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
        "arrow_font_size": 10,
        "graph_enabled": False,
        "history_minutes": 30,
        "graph_opacity": 80
    }

    with patch("PyQt6.QtGui.QFontDatabase.styles", return_value=["Normal", "Bold"]):
         page.load_settings(config)

    settings = page.get_settings()
    assert settings["font_family"] == "Arial"
    assert settings["font_size"] == 10
    assert settings["default_color"] == "#FF0000"
    assert settings["graph_enabled"] is False
    assert settings["history_minutes"] == 30
    assert settings["graph_opacity"] == 80

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

def test_units_page(q_app, mock_i18n, mock_callback):
    """Test UnitsPage."""
    page = UnitsPage(mock_i18n, mock_callback)
    
    config = {
        "unit_type": "bits_binary",
        "speed_display_mode": "auto",
        "decimal_places": 2,
        "swap_upload_download": False,
        "hide_arrows": True,
        "hide_unit_suffix": True,
        "short_unit_labels": False
    }

    page.load_settings(config)
    settings = page.get_settings()

    assert settings["unit_type"] == "bits_binary"
    assert settings["speed_display_mode"] == "auto"
    assert settings["decimal_places"] == 2
    # text_alignment was removed in 2.0 (the control never affected rendering)
    assert "text_alignment" not in settings
    assert settings["swap_upload_download"] is False
    assert settings["hide_arrows"] is True
    assert settings["hide_unit_suffix"] is True
    assert settings["short_unit_labels"] is False
    assert "tray_offset_x" not in settings

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


def test_display_enums_are_segmented_and_round_trip(q_app, mock_i18n, mock_callback):
    """Decimal Places is a 3-value enum - a segmented control, not a slider - and must round-trip its
    canonical int values (0/1/2). (Text Alignment was removed in 2.0; it never affected rendering.)"""
    from netspeedtray.views.settings.pages.units import UnitsPage
    from netspeedtray.utils.components import Win11Segmented
    p = UnitsPage(mock_i18n, mock_callback)
    assert isinstance(p.decimal_places, Win11Segmented)
    p.load_settings({"decimal_places": 2})
    got = p.get_settings()
    assert got["decimal_places"] == 2
    p.load_settings({"decimal_places": 0})
    got = p.get_settings()
    assert got["decimal_places"] == 0


def test_arrow_colors_round_trip(q_app, mock_i18n, mock_callback):
    """#168: the toggle and both swatches persist, and the swatch rows only appear when enabled."""
    page = AppearancePage(mock_i18n, mock_callback, MagicMock(), MagicMock())

    page.load_settings({"use_custom_arrow_colors": True,
                        "arrow_up_color": "#123456",
                        "arrow_down_color": "#654321"})
    assert page.use_custom_arrow_colors.isChecked() is True
    assert page.arrow_colors_container.isVisibleTo(page) is True

    out = page.get_settings()
    assert out["use_custom_arrow_colors"] is True
    assert out["arrow_up_color"] == "#123456"
    assert out["arrow_down_color"] == "#654321"

    # Off hides the swatches but must still round-trip the stored colours.
    page.load_settings({"use_custom_arrow_colors": False,
                        "arrow_up_color": "#ABCDEF",
                        "arrow_down_color": "#FEDCBA"})
    assert page.arrow_colors_container.isVisibleTo(page) is False
    out = page.get_settings()
    assert out["use_custom_arrow_colors"] is False
    assert out["arrow_up_color"] == "#ABCDEF"


def test_arrow_color_widgets_follow_the_dialog_naming_convention(q_app, mock_i18n, mock_callback):
    """SettingsDialog routes every non-threshold colour back through set_color_input by the
    "{key}_color_button" / "{key}_color_input" attribute convention. Naming them anything else
    silently breaks the colour picker with no test to catch it."""
    page = AppearancePage(mock_i18n, mock_callback, MagicMock(), MagicMock())
    for key in ("arrow_up", "arrow_down"):
        assert hasattr(page, f"{key}_color_button")
        assert hasattr(page, f"{key}_color_input")
    page.set_color_input("arrow_up", "#0F0F0F")
    assert page.arrow_up_color_input.text() == "#0F0F0F"
