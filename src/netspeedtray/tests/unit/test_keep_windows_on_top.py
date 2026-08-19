"""
Always-on-top for the Settings and Monitor windows (#213).

Two things here are easy to get wrong and are what these tests actually guard:

1. **The apply path is not a commit path.** `ConfigController.apply_all_settings` is what the live
   settings PREVIEW calls - `SettingsDialog` re-emits `settings_changed` on a 250 ms throttle while
   you drag a slider - so an unguarded native `SetWindowPos` there would fire several times a
   second for the whole time a settings page is open. It must act only on an actual change.

2. **Qt's `setWindowFlags` is the wrong mechanism.** On a visible window it destroys and re-creates
   the native handle, which is precisely what makes frame-vs-client geometry drift (the failure
   `window_state.save_window_geometry` already carries a warning about). The flag is flipped through
   `SetWindowPos` on the existing HWND instead.
"""

import pytest
from unittest.mock import MagicMock, patch

from netspeedtray import constants
from netspeedtray.core.config_controller import ConfigController


@pytest.fixture
def controller():
    widget = MagicMock()
    widget.config = dict(constants.config.defaults.DEFAULT_CONFIG)
    return ConfigController(widget, MagicMock())


def _visible_window():
    w = MagicMock()
    w.isVisible.return_value = True
    return w


class TestTheLiveApplyIsGuarded:

    def test_repeated_applies_with_no_change_touch_the_window_once(self, controller):
        """The regression this is really about: the preview path fires ~4x/second."""
        controller.widget.settings_dialog = _visible_window()
        controller.widget.monitor_window = None

        with patch("netspeedtray.core.config_controller.set_window_always_on_top") as flip:
            for _ in range(20):
                controller._apply_keep_on_top(True)
            assert flip.call_count == 1, \
                f"native flip ran {flip.call_count}x for one state change - the guard is not holding"

    def test_a_real_change_is_applied_each_time_it_flips(self, controller):
        controller.widget.settings_dialog = _visible_window()
        controller.widget.monitor_window = None

        with patch("netspeedtray.core.config_controller.set_window_always_on_top") as flip:
            controller._apply_keep_on_top(True)
            controller._apply_keep_on_top(True)
            controller._apply_keep_on_top(False)
            controller._apply_keep_on_top(False)
            controller._apply_keep_on_top(True)
            assert [c.args[1] for c in flip.call_args_list] == [True, False, True]

    def test_both_windows_are_updated(self, controller):
        controller.widget.settings_dialog = _visible_window()
        controller.widget.monitor_window = _visible_window()

        with patch("netspeedtray.core.config_controller.set_window_always_on_top") as flip:
            controller._apply_keep_on_top(True)
            assert flip.call_count == 2

    def test_hidden_or_absent_windows_are_skipped(self, controller):
        """A hidden Settings dialog picks the state up from its own showEvent on the next open, so
        poking it here would be a pointless native call on a window nobody is looking at."""
        hidden = MagicMock()
        hidden.isVisible.return_value = False
        controller.widget.settings_dialog = hidden
        controller.widget.monitor_window = None

        with patch("netspeedtray.core.config_controller.set_window_always_on_top") as flip:
            controller._apply_keep_on_top(True)
            flip.assert_not_called()


class TestConfigPlumbing:

    def test_default_is_off(self):
        assert constants.config.defaults.DEFAULT_CONFIG["keep_windows_on_top"] is False

    def test_key_is_in_both_config_structures(self):
        assert "keep_windows_on_top" in constants.config.defaults.DEFAULT_CONFIG
        assert "keep_windows_on_top" in constants.config.defaults.VALIDATION_SCHEMA

    def test_advanced_page_round_trip(self, q_app):
        from netspeedtray.views.settings.pages.advanced import AdvancedPage

        # A real I18nStrings, not a mock: the Advanced page reads ~15 keys and a spec'd MagicMock
        # raises on each one. This also proves the new label key actually exists in en_US.json.
        page = AdvancedPage(constants.i18n.I18nStrings("en_US"), MagicMock(), MagicMock())
        page.load_settings({"keep_windows_on_top": True})
        assert page.keep_windows_on_top.isChecked() is True
        assert page.get_settings()["keep_windows_on_top"] is True

        page.load_settings({"keep_windows_on_top": False})
        assert page.get_settings()["keep_windows_on_top"] is False


class TestTheNativeHelper:

    def test_it_uses_setwindowpos_not_qt_window_flags(self, q_app):
        """Flipping WS_EX_TOPMOST must not go through Qt, which would re-create the HWND."""
        from netspeedtray.utils import window_state

        window = MagicMock()
        window.winId.return_value = 12345
        with patch("win32gui.SetWindowPos") as spwp, patch("win32gui.IsWindow", return_value=True):
            assert window_state.set_window_always_on_top(window, True) is True
            spwp.assert_called_once()
        window.setWindowFlags.assert_not_called()
        window.setWindowFlag.assert_not_called()

    def test_it_never_raises(self, q_app):
        """Cosmetic preference - it must never be able to stop a window from opening."""
        from netspeedtray.utils import window_state

        window = MagicMock()
        window.winId.side_effect = RuntimeError("no native handle yet")
        assert window_state.set_window_always_on_top(window, True) is False

    def test_an_invalid_hwnd_is_a_no_op(self, q_app):
        from netspeedtray.utils import window_state

        window = MagicMock()
        window.winId.return_value = 999
        with patch("win32gui.IsWindow", return_value=False), patch("win32gui.SetWindowPos") as spwp:
            assert window_state.set_window_always_on_top(window, True) is False
            spwp.assert_not_called()
