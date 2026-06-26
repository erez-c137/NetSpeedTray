"""GUI smoke tests for the one-time 2.0 welcome dialog (pytest-qt, offscreen)."""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.welcome_dialog import WelcomeDialog


@pytest.fixture
def i18n():
    return I18nStrings("en_US")


def test_constructs_with_title_and_default_action(qtbot, i18n):
    dlg = WelcomeDialog(i18n)
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == i18n.WELCOME_2_0_TITLE
    assert dlg.action == WelcomeDialog.ACTION_GOT_IT  # closing via X/Esc == got it


def test_whats_new_choice_sets_action(qtbot, i18n):
    dlg = WelcomeDialog(i18n)
    qtbot.addWidget(dlg)
    dlg._choose(WelcomeDialog.ACTION_WHATS_NEW)
    assert dlg.action == WelcomeDialog.ACTION_WHATS_NEW
