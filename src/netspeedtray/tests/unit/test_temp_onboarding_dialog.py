"""Temperature onboarding dialog - defaults, action capture, and the dismiss-forever opt-out."""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog


@pytest.fixture
def dlg(q_app):
    return TempOnboardingDialog(I18nStrings("en_US"))


def test_default_action_is_dismiss(dlg):
    assert dlg.action == TempOnboardingDialog.ACTION_DISMISS


def test_get_lhm_button_sets_action(dlg):
    dlg._choose(TempOnboardingDialog.ACTION_GET_LHM)
    assert dlg.action == TempOnboardingDialog.ACTION_GET_LHM


def test_dont_remind_defaults_off_and_toggles(dlg):
    assert dlg.dismissed_forever() is False
    dlg.dont_remind.setChecked(True)
    assert dlg.dismissed_forever() is True


def test_releases_url_is_official_lhm(dlg):
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL
    assert LHM_RELEASES_URL.startswith("https://github.com/LibreHardwareMonitor/")
