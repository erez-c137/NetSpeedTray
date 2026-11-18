"""Temperature onboarding dialog - defaults, action capture, and the dismiss-forever opt-out."""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog


@pytest.fixture
def dlg(qt_owned):
    return qt_owned(TempOnboardingDialog(I18nStrings("en_US")))


def test_default_action_is_dismiss(dlg):
    assert dlg.action == TempOnboardingDialog.ACTION_DISMISS


def test_get_lhm_link_sets_action_and_opens_the_releases_page(q_app, qt_owned):
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL
    opened = []
    dlg = qt_owned(TempOnboardingDialog(I18nStrings("en_US"), open_url=opened.append))
    dlg.get_lhm_link.click()
    assert dlg.action == TempOnboardingDialog.ACTION_GET_LHM
    assert opened == [LHM_RELEASES_URL]


def test_dont_remind_defaults_off_and_toggles(dlg):
    assert dlg.dismissed_forever() is False
    dlg.dont_remind.setChecked(True)
    assert dlg.dismissed_forever() is True


def test_releases_url_is_official_lhm(dlg):
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL
    assert LHM_RELEASES_URL.startswith("https://github.com/LibreHardwareMonitor/")


def test_releases_url_is_the_current_release(dlg):
    """v0.9.5+ is read over LHM's web server (#187), so the button no longer pins v0.9.4."""
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL
    assert LHM_RELEASES_URL.endswith("/releases/latest")


def test_step_two_names_the_lhm_option_and_port(dlg):
    """2.2.2 D6: the web server is a numbered step now, not a paragraph under the body."""
    from netspeedtray import constants
    text = dlg.steps[1].text()
    assert "Remote Web Server" in text
    assert str(constants.lhm.HTTP_PORT) in text
    assert "{port}" not in text
