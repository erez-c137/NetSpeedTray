"""Widget settings page (2.0 IA) - the controls that moved here from General (behavior) and Hardware
(layout) must round-trip, and the hardware auto-switch convenience must survive the move."""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.pages.widget import WidgetPage


@pytest.fixture
def page(qt_owned):
    return qt_owned(WidgetPage(I18nStrings("en_US"), lambda: None))


def test_layout_and_behaviour_round_trip(page):
    page.load_settings({
        "widget_display_mode": "cycle",
        "widget_display_order": ["gpu", "cpu", "network"],
        "free_move": True,
        "keep_visible_fullscreen": True,
    })
    out = page.get_settings()
    assert out["widget_display_mode"] == "cycle"
    assert out["widget_display_order"] == ["gpu", "cpu", "network"]
    assert out["free_move"] is True
    assert out["keep_visible_fullscreen"] is True
    # Tray offset is intentionally not a Widget-page control (Free Move handles repositioning).
    assert "tray_offset_x" not in out
    assert "tray_offset_y" not in out


def test_stacked_mode_encoding_round_trips(page):
    """side_by_stack is stored as widget_display_mode=side_by_side + stack_hardware_stats=True - the
    encoding must survive load->get unchanged (a corruption here silently changes the widget layout)."""
    page.load_settings({"widget_display_mode": "side_by_side", "stack_hardware_stats": True})
    out = page.get_settings()
    assert out["widget_display_mode"] == "side_by_side"
    assert out["stack_hardware_stats"] is True


def test_ensure_hardware_visible_leaves_network_only(page):
    """The convenience the Hardware page used to do itself: enabling a monitor switches the widget out
    of network-only so the new stat is visible."""
    page.load_settings({"widget_display_mode": "network_only"})
    assert page.get_settings()["widget_display_mode"] == "network_only"
    page.ensure_hardware_visible()
    assert page.get_settings()["widget_display_mode"] == "side_by_side"


def test_taskbarless_monitor_note_shows_only_when_the_widget_falls_back(page, monkeypatch):
    """rc.2 W13: a chosen monitor with no taskbar and floating off silently docked the widget to the
    primary taskbar. The note under Preferred Monitor says so - and only in that case."""
    from netspeedtray.utils import taskbar_utils
    # The offscreen platform's screen has no name, so the monitor comes from the saved setting (listed
    # as "(not connected)"); whether it has a taskbar is the patched lookup's answer.
    name = r"\\.\DISPLAY2"
    taskbarless = {name}
    monkeypatch.setattr(taskbar_utils, "get_free_float_screen",
                        lambda n: object() if n in taskbarless else None)
    page.load_settings({"preferred_monitor": name, "free_float": False})
    assert page.preferred_monitor_combo.currentData() == name
    assert page.monitor_note.isVisibleTo(page)
    assert page.i18n.FREE_FLOAT_LABEL in page.monitor_note.text()      # names the switch that helps
    page.free_float.setChecked(True)                                     # floats there instead
    assert not page.monitor_note.isVisibleTo(page)
    page.free_float.setChecked(False)
    assert page.monitor_note.isVisibleTo(page)
    page.preferred_monitor_combo.setCurrentIndex(0)                      # Primary (auto)
    assert not page.monitor_note.isVisibleTo(page)
    taskbarless.clear()                                                  # the monitor has a taskbar
    page.load_settings({"preferred_monitor": name, "free_float": False})
    assert not page.monitor_note.isVisibleTo(page)


def test_ensure_hardware_visible_respects_existing_choice(page):
    """If the user already picked a non-network-only mode, enabling a monitor must NOT override it."""
    page.load_settings({"widget_display_mode": "cycle"})
    page.ensure_hardware_visible()
    assert page.get_settings()["widget_display_mode"] == "cycle"
