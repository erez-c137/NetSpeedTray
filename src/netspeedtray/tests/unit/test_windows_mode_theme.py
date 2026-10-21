"""
The widget re-colours when only the WINDOWS mode flips (Win11 "Custom" colours), and an open Settings
dialog does not pin the old automatic colour.

Qt's colorSchemeChanged follows the APP mode (AppsUseLightTheme); the widget's automatic text colour
follows the Windows mode (SystemUsesLightTheme) the taskbar is painted in. Flipping only the Windows
mode emitted nothing, so the widget kept white text on a light taskbar until a restart (also 2.1.7;
likely part of #332). SystemEventHandler now polls SystemUsesLightTheme and emits theme_changed on a
change. And while Settings is open, the live-preview tick and Cancel re-applied the colour the dialog
was opened with, undoing the theme manager's re-colour.
"""
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core.system_events import SystemEventHandler
from netspeedtray.views.settings.dialog import SettingsDialog
from netspeedtray.views.widget.theme import WidgetThemeManager

WHITE, BLACK = constants.color.WHITE, constants.color.BLACK


@pytest.fixture
def handler(q_app):
    with patch("netspeedtray.core.system_events.WinEventHook"):
        h = SystemEventHandler()
        yield h
        h.stop()


def _poll_with(handler, values):
    """Start the timers with the first registry value, then poll once per remaining value."""
    seen = MagicMock()
    handler.theme_changed.connect(seen)
    reads = iter(values)
    with patch("netspeedtray.core.system_events.read_system_uses_light_theme", side_effect=lambda: next(reads)):
        handler._setup_timers()
        for _ in values[1:]:
            handler._poll_shell_theme()
    return seen


def test_windows_mode_flip_emits_theme_changed_once(handler):
    seen = _poll_with(handler, [0, 0, 1, 1, 1])
    assert seen.call_count == 1


def test_steady_theme_emits_nothing(handler):
    assert _poll_with(handler, [1, 1, 1, 1]).call_count == 0


def test_unreadable_theme_is_ignored(handler):
    assert _poll_with(handler, [0, None, None, 0]).call_count == 0


def test_poll_timer_runs_while_started(handler):
    with patch("netspeedtray.core.system_events.read_system_uses_light_theme", return_value=0):
        handler._setup_timers()
    assert handler._shell_theme_timer.isActive()
    assert handler._shell_theme_timer.interval() == constants.timeouts.SHELL_THEME_POLL_INTERVAL_MS
    handler.stop()
    assert not handler._shell_theme_timer.isActive()


def test_a_flip_while_paused_fires_on_resume(handler):
    seen = MagicMock()
    handler.theme_changed.connect(seen)
    with patch("netspeedtray.core.system_events.read_system_uses_light_theme", return_value=0):
        handler._setup_timers()
    with patch("netspeedtray.core.system_events.read_system_uses_light_theme", return_value=1):
        handler._is_paused = True
        handler._poll_shell_theme()
        assert seen.call_count == 0
        handler._is_paused = False
        handler._poll_shell_theme()
    assert seen.call_count == 1


def test_theme_manager_recolours_from_the_windows_mode():
    widget = types.SimpleNamespace(config={"color_is_automatic": True, "default_color": WHITE},
                                   renderer=MagicMock(), update=MagicMock())
    with patch("netspeedtray.views.widget.theme.shell_theme_text_color", return_value=BLACK):
        WidgetThemeManager(widget).on_theme_changed()
    assert widget.config["default_color"] == BLACK


# --- Settings dialog --------------------------------------------------------------------------------

def _fake_dialog(opened_with=WHITE, automatic=True, user_chose=False):
    original = {"default_color": opened_with, "color_is_automatic": automatic, "font_size": 9}
    fake = types.SimpleNamespace(
        original_config=dict(original), config=dict(original), _user_chose_default_color=user_chose,
        parent_widget=MagicMock(), hide=MagicMock(), logger=MagicMock(),
        pos=lambda: types.SimpleNamespace(x=lambda: 0, y=lambda: 0))
    for page in ("general_page", "widget_page", "hardware_page", "interfaces_page", "advanced_page"):
        setattr(fake, page, types.SimpleNamespace(get_settings=lambda: {}))
    fake.appearance_page = types.SimpleNamespace(get_settings=lambda: {"default_color": opened_with})
    # Pre-fix there is no such method: an identity stand-in, so the behaviour checks below fail.
    method = SettingsDialog.__dict__.get("_with_live_auto_color", lambda self, cfg: cfg)
    fake._with_live_auto_color = types.MethodType(method, fake)
    return fake


@pytest.fixture
def theme_is_light():
    with patch("netspeedtray.views.settings.dialog.shell_theme_text_color", return_value=BLACK, create=True):
        yield


def test_preview_tick_does_not_pin_the_stale_automatic_colour(theme_is_light):
    fake = _fake_dialog()
    settings = SettingsDialog.get_settings(fake)
    assert settings["default_color"] == BLACK
    assert settings["color_is_automatic"] is True


def test_cancel_does_not_restore_the_stale_automatic_colour(theme_is_light):
    fake = _fake_dialog()
    with patch("netspeedtray.views.settings.dialog.save_window_position"):
        SettingsDialog._cancel_and_close(fake)
    (reverted,), kwargs = fake.parent_widget.handle_settings_changed.call_args
    assert reverted["default_color"] == BLACK
    assert reverted["font_size"] == 9 and kwargs == {"save_to_disk": False}
    assert fake.original_config["default_color"] == WHITE   # the snapshot itself is not mutated


@pytest.mark.parametrize("case", ["manual_colour", "picked_in_dialog", "typed_in_field"])
def test_a_colour_the_user_owns_is_kept(theme_is_light, case):
    fake = _fake_dialog(automatic=(case != "manual_colour"), user_chose=(case == "picked_in_dialog"))
    if case == "typed_in_field":
        fake.appearance_page = types.SimpleNamespace(get_settings=lambda: {"default_color": "#00FF00"})
    expected = "#00FF00" if case == "typed_in_field" else WHITE
    assert SettingsDialog.get_settings(fake)["default_color"] == expected
