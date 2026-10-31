"""
Settings IA-reshuffle safety net (C4).

The 2.0 IA moves ~settings across pages (General/Widget/Appearance/Hardware/Network/Advanced) - a
refactor where a control can silently lose its config key (dropped from get_settings) or a moved
control can return the wrong value (corrupt mapping). This test pins both:

  * COVERAGE - every mutable key the dialog manages today is still present in get_settings().
  * ROUND-TRIP - loading a config with the high-risk keys (the moved ones + the segmented enums +
    the colors) and reading it straight back returns the same values.

It is deliberately structure-agnostic: it drives the public SettingsDialog API only, so it holds
identically before and after the page reshuffle. A red here means the reshuffle dropped/corrupted a
real user's setting.
"""
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.dialog import SettingsDialog


# Every mutable key the dialog is responsible for round-tripping (control-backed). Window positions,
# onboarding flags, db-internal keys etc. are deliberately excluded - they aren't page-managed.
MANAGED_KEYS = {
    # General
    "language", "update_rate", "start_with_windows", "check_for_updates",
    # General > Interaction (moved from Advanced > Behavior, 2.2 IA pass change 3)
    "double_click_action", "middle_click_action", "pause_in_menu", "show_usage_on_hover", "show_hover_tips",
    # Widget (layout + on-taskbar placement/behavior; preferred_monitor moved from General, 2.2 IA pass)
    "preferred_monitor", "free_move", "free_float", "keep_visible_fullscreen",
    "widget_display_mode", "stack_hardware_stats", "widget_display_order",
    # Widget - mini graph (moved from Appearance, 2.2 IA pass)
    "graph_enabled", "history_minutes", "graph_opacity", "graph_fixed_scale", "graph_scale_headroom",
    "graph_show_ceiling", "graph_split_directions", "graph_upload_color_dark", "graph_upload_color_light",
    "graph_download_color_dark", "graph_download_color_light",
    # Appearance - font / color / arrows / background (the themed colours are dark/light pairs, 2.2 rc.2)
    "font_family", "font_size", "font_weight", "default_color_dark", "default_color_light",
    "arrow_up_color_dark", "arrow_up_color_light", "arrow_down_color_dark", "arrow_down_color_light",
    "background_color", "background_opacity",
    "use_separate_arrow_font", "arrow_font_family", "arrow_font_size",
    "arrow_up_symbol", "arrow_down_symbol",
    # Appearance - units / format (was the Display page)
    "unit_type", "speed_display_mode", "decimal_places", "text_alignment",
    "swap_upload_download", "hide_arrows", "hide_unit_suffix", "short_unit_labels",
    # Appearance - color coding (was the Color Coding page)
    "color_coding", "high_speed_threshold", "medium_speed_threshold", "low_speed_threshold",
    "high_speed_color_dark", "high_speed_color_light", "medium_speed_color_dark", "medium_speed_color_light",
    "low_speed_color_dark", "low_speed_color_light",
    # Hardware
    "monitor_cpu_enabled", "monitor_gpu_enabled", "monitor_ram_enabled", "monitor_vram_enabled",
    "show_hardware_temps", "show_hardware_power", "hardware_label_style",
    "cpu_load_high_threshold", "cpu_load_low_threshold",
    "gpu_load_high_threshold", "gpu_load_low_threshold", "throttle_temp_c",
    # Network - interfaces
    "interface_mode", "selected_interfaces",
    # Network - connection (latency + advertised plan)
    "latency_enabled", "latency_public_enabled", "latency_public_host", "plan_down_mbps", "plan_up_mbps",
    # Network - data cap
    "data_cap_enabled", "data_cap_gb", "data_cap_reset_day", "data_cap_count", "data_cap_alert_enabled",
    # Appearance - fixed arrow weight (constant, but must survive the load/get chain)
    "arrow_font_weight",
    # Advanced
    "keep_data", "record_hardware_history", "reduce_motion", "keep_windows_on_top",
    "metrics_endpoint_enabled", "metrics_endpoint_port",
}

# High-risk keys for the reshuffle: the ones that MOVE pages, the segmented enums (int/string
# userData mappings that silently corrupt if mis-wired), and the colors. Mapped to a valid
# non-default value we can prove round-trips.
ROUNDTRIP = {
    "free_move": True,
    "keep_visible_fullscreen": True,
    "tray_offset_x": 17,
    "widget_display_mode": "cycle",            # avoids the side_by_stack encoding; direct round-trip
    "widget_display_order": ["gpu", "cpu", "network"],
    "decimal_places": 0,
    "text_alignment": "right",
    "unit_type": "bits_binary",
    "hide_arrows": True,
    "short_unit_labels": False,
    "color_coding": True,
    "high_speed_color_dark": "#00FF00",
    "high_speed_color_light": "#007700",
    "low_speed_color_dark": "#FFFF00",
    "low_speed_color_light": "#777700",
    "medium_speed_color_light": "#123456",
    "medium_speed_threshold": 4.5,
    "graph_upload_color_light": "#224466",
    "default_color_light": "#151618",
    "high_speed_threshold": 99.0,
    "low_speed_threshold": 3.0,
    "monitor_cpu_enabled": True,
    "hardware_label_style": "text",
    "interface_mode": "all_physical",
    "keep_data": 90,
    "reduce_motion": True,
    # Moved by the 2.2 IA pass (mini graph -> Widget, throttle temp within Hardware)
    "graph_enabled": False,
    "graph_opacity": 33,
    "graph_split_directions": True,
    "graph_fixed_scale": True,
    "throttle_temp_c": 85,
    # Moved by the 2.2 IA pass change 3 (Advanced > Behavior -> General > Interaction)
    "pause_in_menu": True,
    "show_usage_on_hover": False,
    "show_hover_tips": False,
}


def _dialog(qtbot, config):
    m = MagicMock()
    m.config = {}
    m.config_manager = MagicMock()
    dlg = SettingsDialog(
        main_widget=m, config=config, version="2.0.0", i18n=I18nStrings("en_US"),
        available_interfaces=["Ethernet", "Wi-Fi"], is_startup_enabled=False,
    )
    qtbot.addWidget(dlg)
    return dlg


def test_no_managed_key_is_dropped(qtbot):
    """Every page-managed key must survive into get_settings - a dropped key silently reverts a
    user's setting to default on the next save."""
    dlg = _dialog(qtbot, dict(constants.config.defaults.DEFAULT_CONFIG))
    out = set(dlg.get_settings())
    missing = MANAGED_KEYS - out
    assert not missing, f"get_settings dropped managed keys: {sorted(missing)}"


def test_highrisk_keys_round_trip(qtbot):
    """Load a config with the moved + segmented + color keys at non-defaults; read them straight
    back. A corrupt mapping (e.g. a segmented control losing its userData) returns the wrong value."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(ROUNDTRIP)
    dlg = _dialog(qtbot, cfg)
    got = dlg.get_settings()
    bad = {k: (got.get(k), v) for k, v in ROUNDTRIP.items() if got.get(k) != v}
    assert not bad, f"keys did not round-trip (got, expected): {bad}"


# Config keys that exist in DEFAULT_CONFIG but have no settings-page control (dormant by design). They
# must NOT be lost when settings are saved: get_settings() starts from a full config copy, so a page
# that doesn't manage a key leaves it untouched at its loaded value. This guards against a future
# refactor dropping the config.copy() and silently resetting these on the next save.
# tray_offset_x/y are deliberately UI-less - Free Move (drag) is the reposition path; the position
# engine still reads them from config at their defaults.
UNMANAGED_PRESERVED = {
    "tray_offset_x": 12,
    "tray_offset_y": 7,
    "show_legend": False,
    "force_decimals": True,
    "dynamic_update_enabled": False,
}


def test_unmanaged_keys_are_preserved_through_get_settings(qtbot):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(UNMANAGED_PRESERVED)
    dlg = _dialog(qtbot, cfg)
    got = dlg.get_settings()
    bad = {k: (got.get(k), v) for k, v in UNMANAGED_PRESERVED.items() if got.get(k) != v}
    assert not bad, f"unmanaged keys not preserved (got, expected): {bad}"


# --- integration tests for the cross-page wiring the UI/UX audit flagged as untested -----------------

def test_color_picker_routes_to_the_right_embedded_section(qtbot, monkeypatch):
    """Appearance embeds the old Colors page as colors_section. The dialog's color picker must route
    high/low-speed colors to that section and default/background to Appearance itself - a mis-wired
    branch would silently paint the wrong swatch."""
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog
    dlg = _dialog(qtbot, dict(constants.config.defaults.DEFAULT_CONFIG))

    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#123456"))
    dlg._open_color_dialog("high_speed_color_light")
    assert dlg.appearance_page.colors_section.high_speed_color_light_input.text().upper() == "#123456"

    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#ABCDEF"))
    dlg._open_color_dialog("default_color_dark")
    assert dlg.appearance_page.default_color_dark_input.text().upper() == "#ABCDEF"

    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#FEDCBA"))
    dlg._open_color_dialog("background_color")
    assert dlg.appearance_page.background_color_input.text().upper() == "#FEDCBA"


def test_enabling_a_hardware_monitor_switches_widget_out_of_network_only(qtbot):
    """The Hardware page's monitor toggles emit hardware_enabled, which the dialog forwards to the
    Widget page so the widget leaves network-only and the new stat is actually visible. A broken
    connection would leave a freshly-enabled CPU/GPU readout invisible."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"widget_display_mode": "network_only", "stack_hardware_stats": False})
    dlg = _dialog(qtbot, cfg)
    assert dlg.get_settings()["widget_display_mode"] == "network_only"
    dlg.hardware_page.hardware_enabled.emit()
    assert dlg.get_settings()["widget_display_mode"] == "side_by_side"


def test_picking_a_text_colour_writes_only_that_taskbars_half(qtbot, monkeypatch):
    """Was: picking the default colour cleared color_is_automatic so the theme stopped overwriting it.
    Since the 2.2 rc.2 colour scheme the theme never overwrites a colour; a pick sets the one half it
    was made in, and nothing writes the retired flag."""
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog
    dlg = _dialog(qtbot, dict(constants.config.defaults.DEFAULT_CONFIG))
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#FF8800"))
    dlg._open_color_dialog("default_color_light")
    s = dlg.get_settings()
    assert (s["default_color_dark"], s["default_color_light"]) == ("#FFFFFF", "#FF8800")
    assert "color_is_automatic" not in s
