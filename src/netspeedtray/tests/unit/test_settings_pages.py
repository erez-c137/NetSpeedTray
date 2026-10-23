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
from netspeedtray.views.settings.pages.mini_graph_section import MiniGraphSection
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
    i18n.PAUSE_IN_MENU_LABEL = "Pause in menu"          # General > Interaction (2.2 IA change 3)
    i18n.HOVER_USAGE_CARD_LABEL = "Show usage on hover"
    i18n.HOVER_TIPS_LABEL = "Show tips"
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
    # The colour scheme (2.2 rc.2): Medium level, theme glyph names, the note, the graph colours.
    i18n.MEDIUM_SPEED_THRESHOLD_LABEL = "Medium"
    i18n.MEDIUM_SPEED_COLOR_LABEL = "Medium Color"
    i18n.MEDIUM_SPEED_COLOR_TOOLTIP = "Select Medium Color"
    i18n.MEDIUM_SPEED_RANGE_HINT = "Set Medium between Low and High"
    i18n.CONN_PLAN_OFF = "Off"
    i18n.THEME_DARK_TASKBAR = "Dark taskbar"
    i18n.THEME_LIGHT_TASKBAR = "Light taskbar"
    i18n.THEMED_COLORS_NOTE = "Each color has two versions"
    i18n.GRAPH_UPLOAD_COLOR_LABEL = "Upload graph color"
    i18n.GRAPH_DOWNLOAD_COLOR_LABEL = "Download graph color"
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
    i18n.POWER_HOVER_CARD_LABEL = "Power Hover Card"
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
    i18n.GRAPH_FIXED_SCALE_LABEL = "Fixed Graph Scale"
    i18n.GRAPH_FIXED_SCALE_DESC = "Scale to your plan speed"
    i18n.GRAPH_SCALE_HEADROOM_LABEL = "Headroom"
    i18n.GRAPH_SHOW_CEILING_LABEL = "Show Plan Speed Line"
    i18n.GRAPH_SHOW_CEILING_DESC = "Needs headroom above 0%"
    i18n.GRAPH_SPLIT_LABEL = "Separate Upload and Download Graphs"
    i18n.GRAPH_SPLIT_DESC = "Side by side or stacked"
    i18n.GRAPH_STYLE_LABEL = "Graph style"
    i18n.GRAPH_STYLE_DESC = "Detailed or Trend"
    i18n.GRAPH_STYLE_DETAILED = "Detailed"
    i18n.GRAPH_STYLE_TREND = "Trend"
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
    i18n.NO_INTERFACES_FOUND = "None"
    i18n.INTERFACE_GROUP_PHYSICAL = "Physical adapters"
    i18n.INTERFACE_GROUP_VIRTUAL = "Virtual adapters and VPNs"
    i18n.INTERFACE_ACTIVE_CONNECTION = "Active connection"
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
    i18n.BACKGROUND_OPACITY_LABEL = "Opacity"
    i18n.SHORT_UNIT_LABELS_LABEL = "Short Labels"
    i18n.KEEP_VISIBLE_FULLSCREEN_LABEL = "Keep Visible in Fullscreen"
    i18n.BEHAVIOR_GROUP_TITLE = "Behavior"
    i18n.MINI_GRAPH_SECTION_TITLE = "Mini Graph"
    i18n.HARDWARE_GRAPH_NOTE = "Hardware graphs use the same opacity and timespan"
    i18n.HARDWARE_INDICATOR_STYLE_LABEL = "Indicator Style"
    i18n.MONITORING_MODE_AUTO_SUBTITLE = "Monitors the primary active connection"
    i18n.MONITORING_MODE_PHYSICAL_SUBTITLE = "Excludes VPNs and virtual adapters"
    i18n.MONITORING_MODE_VIRTUAL_SUBTITLE = "Includes VPNs, virtual adapters, and tunnels"
    i18n.MONITORING_MODE_SELECTED_SUBTITLE = "Choose specific interfaces from the list below"
    i18n.MONITORING_MODE_AUTO_TOOLTIP = "Counts only the routing adapter. For a data cap, “{mode}”."
    i18n.MONITORING_MODE_PHYSICAL_TOOLTIP = "Adds up every real network adapter."

    # For GeneralPage update rate slider
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

    # The slowest preset round-trips too (10 s must not clamp to 5 s on a renumbered slider).
    page.load_settings({"language": "en_US", "update_rate": 10.0}, is_startup_enabled=False)
    settings_slow = page.get_settings()
    assert settings_slow["update_rate"] == 10.0
    assert settings_slow["language"] == "en_US"


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
            "high_speed_color_dark": "#00FF00", "low_speed_color_dark": "#FFFF00"}

    page.load_settings({**base, "unit_type": "bits_decimal"})
    assert page.high_speed_threshold.suffix() == " Mbps"

    page.load_settings({**base, "unit_type": "bytes_decimal"})
    assert page.high_speed_threshold.suffix() == " MB/s"
    assert page.low_speed_threshold.suffix() == " MB/s"

    page.load_settings({**base, "unit_type": "bits_binary"})
    assert page.high_speed_threshold.suffix() == " Mibps"
    assert page.medium_speed_threshold.suffix() == " Mibps"
    page.set_unit_type("bytes_decimal")
    assert page.medium_speed_threshold.suffix() == " MB/s"


def test_appearance_page(q_app, mock_i18n, mock_callback):
    """Test AppearancePage."""
    font_cb = MagicMock()
    color_cb = MagicMock()
    page = AppearancePage(mock_i18n, mock_callback, font_cb, color_cb)
    
    config = {
        "font_family": "Arial",
        "font_size": 10,
        "font_weight": 600,
        "default_color_dark": "#FF0000",
        "default_color_light": "#00FF00",
        "background_color": "#000000",
        "background_opacity": 50,
        "use_separate_arrow_font": False,
        "arrow_font_family": "Arial",
        "arrow_font_size": 10,
    }

    with patch("PyQt6.QtGui.QFontDatabase.styles", return_value=["Normal", "Bold"]):
         page.load_settings(config)

    settings = page.get_settings()
    assert settings["font_family"] == "Arial"
    assert settings["font_size"] == 10
    assert (settings["default_color_dark"], settings["default_color_light"]) == ("#FF0000", "#00FF00")
    # The Mini Graph section moved to the Widget page (2.2 IA pass) - Appearance no longer owns its keys.
    assert not {"graph_enabled", "history_minutes", "graph_opacity"} & set(settings)


def test_mini_graph_section_round_trip(q_app, mock_i18n, mock_callback):
    """The basic graph keys (was part of test_appearance_page) round-trip through the moved section."""
    page = MiniGraphSection(mock_i18n, mock_callback)
    page.load_settings({"graph_enabled": False, "history_minutes": 30, "graph_opacity": 80,
                        "graph_upload_color_light": "#112233", "graph_download_color_dark": "#445566"})
    settings = page.get_settings()
    assert settings["graph_enabled"] is False
    assert settings["history_minutes"] == 30
    assert settings["graph_opacity"] == 80
    # The line colours (#117); a key the file lacks shows its factory default.
    assert (settings["graph_upload_color_light"], settings["graph_download_color_dark"]) == ("#112233", "#445566")
    D = constants.config.defaults.DEFAULT_CONFIG
    assert (settings["graph_upload_color_dark"], settings["graph_download_color_light"]) == \
        (D["graph_upload_color_dark"], D["graph_download_color_light"])

def test_colors_page(q_app, mock_i18n, mock_callback):
    """Test ColorsPage."""
    color_cb = MagicMock()
    page = ColorsPage(mock_i18n, mock_callback, color_cb)
    
    config = {
        "color_coding": True,
        "high_speed_threshold": 50,
        "medium_speed_threshold": 20,
        "low_speed_threshold": 10,
        "high_speed_color_dark": "#00FF00",
        "high_speed_color_light": "#007700",
        "medium_speed_color_light": "#AA8800",
        "low_speed_color_dark": "#FFFF00",
    }

    page.load_settings(config)
    settings = page.get_settings()

    assert settings["color_coding"] is True
    assert settings["high_speed_threshold"] == 50
    assert settings["medium_speed_threshold"] == 20
    assert settings["low_speed_threshold"] == 10
    assert (settings["high_speed_color_dark"], settings["high_speed_color_light"]) == ("#00FF00", "#007700")
    assert settings["medium_speed_color_light"] == "#AA8800"
    assert settings["low_speed_color_dark"] == "#FFFF00"

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
                        "arrow_up_color_dark": "#123456",
                        "arrow_down_color_light": "#654321"})
    assert page.use_custom_arrow_colors.isChecked() is True
    assert page.arrow_colors_container.isVisibleTo(page) is True

    out = page.get_settings()
    assert out["use_custom_arrow_colors"] is True
    assert out["arrow_up_color_dark"] == "#123456"
    assert out["arrow_down_color_light"] == "#654321"
    # The white-on-light fix: the light half defaults to black, not the dark half's white.
    assert (out["arrow_down_color_dark"], out["arrow_up_color_light"]) == ("#FFFFFF", "#007A00")

    # Off hides the swatches but must still round-trip the stored colors.
    page.load_settings({"use_custom_arrow_colors": False,
                        "arrow_up_color_light": "#ABCDEF",
                        "arrow_down_color_dark": "#FEDCBA"})
    assert page.arrow_colors_container.isVisibleTo(page) is False
    out = page.get_settings()
    assert out["use_custom_arrow_colors"] is False
    assert out["arrow_up_color_light"] == "#ABCDEF"


def test_graph_fixed_scale_defaults_off(q_app, mock_i18n, mock_callback):
    """A config from before the setting existed loads as automatic, with the sub-options hidden."""
    page = MiniGraphSection(mock_i18n, mock_callback)
    page.load_settings({})
    assert page.graph_fixed_scale.isChecked() is False
    assert page.graph_scale_container.isVisibleTo(page) is False
    out = page.get_settings()
    assert out["graph_fixed_scale"] is False
    assert out["graph_scale_headroom"] == constants.config.defaults.DEFAULT_GRAPH_SCALE_HEADROOM
    assert out["graph_show_ceiling"] is constants.config.defaults.DEFAULT_GRAPH_SHOW_CEILING


def test_graph_fixed_scale_round_trip(q_app, mock_i18n, mock_callback):
    """Toggle, headroom and plan-speed line persist; headroom + line only show with the fixed scale on."""
    page = MiniGraphSection(mock_i18n, mock_callback)
    page.load_settings({"graph_fixed_scale": True, "graph_scale_headroom": 30, "graph_show_ceiling": False})
    assert page.graph_scale_container.isVisibleTo(page) is True
    out = page.get_settings()
    assert out["graph_fixed_scale"] is True
    assert out["graph_scale_headroom"] == 30
    assert out["graph_show_ceiling"] is False

    page.graph_fixed_scale.setChecked(False)
    assert page.graph_scale_container.isVisibleTo(page) is False


def test_plan_speed_line_grayed_out_at_zero_headroom(q_app, mock_i18n, mock_callback):
    """At 0% headroom the plan speed is the graph's top edge and no line is drawn, so the line toggle is
    unavailable - and comes back as soon as there is headroom to show it in. Its saved value survives."""
    page = MiniGraphSection(mock_i18n, mock_callback)
    page.load_settings({"graph_fixed_scale": True, "graph_scale_headroom": 0, "graph_show_ceiling": True})
    assert page._ceiling_card.isEnabled() is False
    assert page.get_settings()["graph_show_ceiling"] is True

    page.graph_scale_headroom.setCurrentIndex(page.graph_scale_headroom.findData(20))
    assert page._ceiling_card.isEnabled() is True
    assert page.get_settings()["graph_scale_headroom"] == 20


def test_graph_headroom_outside_the_choices_falls_back_to_default(q_app, mock_i18n, mock_callback):
    """A hand-edited headroom the combo doesn't offer must not leave the combo blank."""
    page = MiniGraphSection(mock_i18n, mock_callback)
    page.load_settings({"graph_fixed_scale": True, "graph_scale_headroom": 25})
    assert page.get_settings()["graph_scale_headroom"] == constants.config.defaults.DEFAULT_GRAPH_SCALE_HEADROOM


def test_graph_split_round_trip(q_app, mock_i18n, mock_callback):
    """Separate upload/download graphs: off by default, persists when on."""
    page = MiniGraphSection(mock_i18n, mock_callback)
    page.load_settings({"graph_enabled": True})
    assert page.get_settings()["graph_split_directions"] is False
    page.load_settings({"graph_enabled": True, "graph_split_directions": True})
    assert page.graph_split.isChecked() is True
    assert page.get_settings()["graph_split_directions"] is True


def test_graph_split_grayed_out_without_the_mini_graph(q_app, mock_i18n, mock_callback):
    """It splits the mini graph, so it is unavailable while the graph is off - and its stored value survives."""
    page = MiniGraphSection(mock_i18n, mock_callback)
    page.load_settings({"graph_enabled": False, "graph_split_directions": True})
    assert page._split_card.isEnabled() is False
    assert page.get_settings()["graph_split_directions"] is True
    page.enable_graph.setChecked(True)
    assert page._split_card.isEnabled() is True


def test_arrow_color_widgets_follow_the_dialog_naming_convention(q_app, mock_i18n, mock_callback):
    """SettingsDialog routes a picked colour back through set_color_input(<full config key>); the
    widgets are "<key>_button" / "<key>_input" (e.g. arrow_up_color_light_input). Naming them anything
    else silently breaks the color picker with no test to catch it."""
    page = AppearancePage(mock_i18n, mock_callback, MagicMock(), MagicMock())
    for key in ("arrow_up_color_dark", "arrow_up_color_light", "arrow_down_color_dark", "arrow_down_color_light"):
        assert hasattr(page, f"{key}_button")
        assert hasattr(page, f"{key}_input")
    page.set_color_input("arrow_up_color_light", "#0F0F0F")
    assert page.arrow_up_color_light_input.text() == "#0F0F0F"
    assert page.arrow_up_color_dark_input.text() != "#0F0F0F"
