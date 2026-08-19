"""
Regression tests for the "restart required" prompt on a language change (#234).

The prompt should fire exactly when a restart would actually change the displayed language. Two
ways to get that wrong, and each naive fix hits one:

* The shipped 2.1.2 code used `selected_language and (selected_language != initial_language)`.
  `None` - the legitimate "Auto-detect (system)" value - is falsy, so switching *to* auto-detect
  never prompted. The user stayed on the old language with nothing telling them to restart.
* A plain `!=` on the raw config values prompts whenever the stored value changes but the language
  does not - e.g. a Korean user pinning the ko_KR that auto-detect had already resolved. That is
  the common path now that auto-detect works, so it would be a busier bug than the one it fixes.

The fix compares EFFECTIVE locales against `i18n.language`, the locale actually loaded in this
process. That baseline also can't go stale: SettingsDialog is constructed once and cached on the
widget, so anything captured in __init__ drifts once `reset_with_config` runs.
"""

import pytest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings


@pytest.fixture(scope="session")
def q_app():
    return QApplication.instance() or QApplication([])


def _make_dialog(config_language, i18n=None):
    """A SettingsDialog whose stored config carries `config_language`."""
    from netspeedtray.views.settings import SettingsDialog

    parent = MagicMock()
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    config["language"] = config_language
    parent.config = config
    parent.get_available_interfaces.return_value = ["Ethernet 1"]
    parent.is_startup_enabled.return_value = False

    return SettingsDialog(
        main_widget=parent,
        config=config.copy(),
        version="2.1.3",
        i18n=i18n if i18n is not None else I18nStrings("en_US"),
        available_interfaces=["Ethernet 1"],
        is_startup_enabled=False,
    )


def _save_and_capture_prompt(dialog, selected_language, system_lcid=1033):
    """Run _save_and_close with a stubbed get_settings; report whether the prompt was shown.

    `system_lcid` drives what auto-detect resolves to, which the new comparison consults whenever
    the selection is None.
    """
    settings = constants.config.defaults.DEFAULT_CONFIG.copy()
    settings["language"] = selected_language

    windll = MagicMock()
    windll.kernel32.GetUserDefaultUILanguage.return_value = system_lcid

    with patch("ctypes.windll", windll, create=True), \
         patch.object(dialog, "get_settings", return_value=settings), \
         patch("netspeedtray.views.settings.dialog.QMessageBox") as msg_box, \
         patch("netspeedtray.views.settings.dialog.save_window_position"):
        dialog._save_and_close()
        return msg_box.information.called


class TestPromptFiresWhenTheLanguageActuallyChanges:

    def test_switching_to_auto_detect_that_resolves_differently_prompts(self, q_app):
        """The #234 bug. Running Korean explicitly, user picks Auto-detect on an English machine:
        a restart WILL switch them to English, so they must be told. `None` being falsy meant the
        old code stayed silent."""
        dialog = _make_dialog("ko_KR", i18n=I18nStrings("ko_KR"))
        try:
            assert _save_and_capture_prompt(dialog, None, system_lcid=1033) is True
        finally:
            dialog.deleteLater()

    def test_switching_from_auto_detect_to_a_different_language_prompts(self, q_app):
        dialog = _make_dialog(None, i18n=I18nStrings("en_US"))
        try:
            assert _save_and_capture_prompt(dialog, "ko_KR") is True
        finally:
            dialog.deleteLater()

    def test_changing_between_two_languages_prompts(self, q_app):
        dialog = _make_dialog("fr_FR", i18n=I18nStrings("fr_FR"))
        try:
            assert _save_and_capture_prompt(dialog, "de_DE") is True
        finally:
            dialog.deleteLater()

    def test_auto_detect_resolving_somewhere_new_prompts(self, q_app):
        """Running Korean, user picks Auto-detect, and the system reports Japanese."""
        dialog = _make_dialog("ko_KR", i18n=I18nStrings("ko_KR"))
        try:
            assert _save_and_capture_prompt(dialog, None, system_lcid=1041) is True
        finally:
            dialog.deleteLater()


class TestPromptStaysQuietWhenNothingWouldChange:

    def test_saving_without_touching_the_language_does_not_prompt(self, q_app):
        dialog = _make_dialog("fr_FR", i18n=I18nStrings("fr_FR"))
        try:
            assert _save_and_capture_prompt(dialog, "fr_FR") is False
        finally:
            dialog.deleteLater()

    def test_auto_detect_user_saving_unrelated_settings_does_not_prompt(self, q_app):
        dialog = _make_dialog(None, i18n=I18nStrings("en_US"))
        try:
            assert _save_and_capture_prompt(dialog, None) is False
        finally:
            dialog.deleteLater()

    def test_pinning_the_language_auto_detect_already_chose_does_not_prompt(self, q_app):
        """The false positive a raw-value comparison introduces, and the one most likely to be hit:
        a Korean user finally sees auto-detect work, pins ko_KR explicitly to be safe, and must not
        be told to restart for a no-op."""
        dialog = _make_dialog(None, i18n=I18nStrings("ko_KR"))
        try:
            assert _save_and_capture_prompt(dialog, "ko_KR", system_lcid=1042) is False
        finally:
            dialog.deleteLater()

    def test_switching_to_auto_detect_that_resolves_the_same_does_not_prompt(self, q_app):
        """The mirror case: explicitly on Korean, switches to Auto-detect on a Korean machine."""
        dialog = _make_dialog("ko_KR", i18n=I18nStrings("ko_KR"))
        try:
            assert _save_and_capture_prompt(dialog, None, system_lcid=1042) is False
        finally:
            dialog.deleteLater()

    def test_the_baseline_does_not_go_stale_when_the_dialog_is_reused(self, q_app):
        """SettingsDialog is constructed once and cached (views/widget/main.py), so every reopen
        goes through reset_with_config. A baseline captured in __init__ would drift and re-prompt
        on every later save; comparing against i18n.language cannot."""
        dialog = _make_dialog("ko_KR", i18n=I18nStrings("ko_KR"))
        try:
            assert _save_and_capture_prompt(dialog, None, system_lcid=1033) is True

            # User postpones the restart. The dialog is reopened with the saved config.
            reopened = constants.config.defaults.DEFAULT_CONFIG.copy()
            reopened["language"] = None
            dialog.reset_with_config(reopened, is_startup_enabled=False)

            # They change nothing about the language and save again. The running locale is still
            # ko_KR, so the restart is still pending and the prompt is still correct...
            assert _save_and_capture_prompt(dialog, None, system_lcid=1033) is True
            # ...but pinning the language they are actually running must stay quiet.
            assert _save_and_capture_prompt(dialog, "ko_KR", system_lcid=1033) is False
        finally:
            dialog.deleteLater()
