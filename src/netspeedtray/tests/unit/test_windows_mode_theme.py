"""
The widget re-colours when only the WINDOWS mode flips (Win11 "Custom" colours), and an open Settings
dialog does not pin the old automatic colour.

Qt's colorSchemeChanged follows the APP mode (AppsUseLightTheme); the widget's automatic text colour
follows the Windows mode (SystemUsesLightTheme) the taskbar is painted in. Flipping only the Windows
mode emitted nothing, so the widget kept white text on a light taskbar until a restart (also 2.1.7;
likely part of #332). Windows broadcasts WM_SETTINGCHANGE "ImmersiveColorSet" for that flip; an
app-wide native filter now re-reads SystemUsesLightTheme then (no polling) and emits theme_changed on
a change - deduped, so one flip is one emit whichever path (the filter or Qt's signal) sees it. And while Settings is open, the live-preview tick and Cancel re-applied the colour the dialog
was opened with, undoing the theme manager's re-colour.
"""
import ctypes
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


READ = "netspeedtray.core.system_events.read_system_uses_light_theme"


def _settingchange(text, message=0x001A):
    """(MSG struct, keep-alive buffer) for a native message with lParam -> ``text`` (or 0)."""
    from netspeedtray.core import system_events as se
    buf = ctypes.create_unicode_buffer(text) if text is not None else None
    msg = se._NativeMSG()
    msg.message = message
    msg.lParam = ctypes.addressof(buf) if buf is not None else 0
    return msg, buf


@pytest.mark.parametrize("text,message,fires", [
    ("ImmersiveColorSet", 0x001A, True),
    ("ImmersiveColorSetX", 0x001A, False),
    ("Immersive", 0x001A, False),
    ("Policy", 0x001A, False),
    (None, 0x001A, False),                       # an SPI_* change: lParam 0
    ("ImmersiveColorSet", 0x0010, False),        # not WM_SETTINGCHANGE
])
def test_native_filter_reacts_only_to_immersive_color_set(q_app, text, message, fires):
    from netspeedtray.core import system_events as se
    seen = MagicMock()
    f = se._ImmersiveColorSetFilter(seen)
    msg, _buf = _settingchange(text, message)
    assert f.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)  # observe only
    assert seen.called is fires


def test_start_installs_the_filter_and_stop_removes_it(handler):
    with patch(READ, return_value=0), patch("netspeedtray.core.system_events.get_taskbar_info"):
        handler.start()
        assert handler._theme_filter is not None
        handler.stop()
    assert handler._theme_filter is None


def test_no_theme_polling_timer(handler):
    """Event-driven: no theme polling - just the taskbar-validity, fullscreen and
    z-order-trailing timers (the last from the #327 re-seat rework)."""
    from PyQt6.QtCore import QTimer
    timers = handler.findChildren(QTimer)
    assert set(timers) == {handler._taskbar_validity_timer, handler._fullscreen_poll_timer,
                           handler._zorder_trailing_timer}


def _baseline(handler, value=0):
    seen = MagicMock()
    handler.theme_changed.connect(seen)
    with patch(READ, return_value=value):
        handler.check_shell_theme()          # first read = baseline, no emit
    return seen


def test_windows_mode_flip_emits_theme_changed_once(handler):
    seen = _baseline(handler, 0)
    with patch(READ, return_value=1):
        for _ in range(3):                   # the broadcast reaches several windows
            handler.check_shell_theme()
    assert seen.call_count == 1


def test_one_flip_seen_by_both_paths_emits_once(handler):
    from PyQt6.QtCore import Qt
    seen = _baseline(handler, 0)
    with patch(READ, return_value=1):
        handler.check_shell_theme()                          # native broadcast first
        handler._on_color_scheme_changed(Qt.ColorScheme.Light)   # then Qt's signal
    assert seen.call_count == 1
    with patch(READ, return_value=0):
        handler._on_color_scheme_changed(Qt.ColorScheme.Dark)    # Qt first this time
        handler.check_shell_theme()
    assert seen.call_count == 2


def test_steady_or_unreadable_theme_emits_nothing(handler):
    seen = _baseline(handler, 1)
    for v in (1, None, 1):
        with patch(READ, return_value=v):
            handler.check_shell_theme()
    assert seen.call_count == 0


def test_a_flip_while_paused_fires_on_resume(handler):
    seen = _baseline(handler, 0)
    with patch(READ, return_value=1):
        handler.pause()
        handler.check_shell_theme()
        assert seen.call_count == 0
        handler.resume()
    assert seen.call_count == 1


def test_a_real_settingchange_reaches_the_filter_through_qt(handler):
    """End to end on the real Windows platform plugin: a WM_SETTINGCHANGE "ImmersiveColorSet" sent to
    one of the app's (hidden, never shown) native windows goes through Qt's dispatch into the filter
    and emits theme_changed. Skipped under the offscreen platform CI uses (no native windows)."""
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QWidget
    if QGuiApplication.platformName() != "windows":
        pytest.skip("needs the windows platform plugin")
    import win32gui
    seen = MagicMock()
    handler.theme_changed.connect(seen)
    w = QWidget()
    try:
        hwnd = int(w.winId())   # native window, never shown
        with patch(READ, return_value=0), patch("netspeedtray.core.system_events.get_taskbar_info"):
            handler.start()
        with patch(READ, return_value=1):
            win32gui.SendMessage(hwnd, 0x001A, 0, "ImmersiveColorSet")
            win32gui.SendMessage(hwnd, 0x001A, 0, "Policy")
        assert seen.call_count == 1
    finally:
        handler.stop()
        w.deleteLater()


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
    # The dialog surface these paths use since the RC Settings fixes: pages via _pages() (pages that
    # failed to load are skipped, C1), and Cancel -> reject() -> _revert_live_preview() (Esc too, S1).
    fake._pages = types.MethodType(SettingsDialog._pages, fake)
    fake._update_timer = MagicMock()
    fake.reject = lambda: SettingsDialog._revert_live_preview(fake)
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
