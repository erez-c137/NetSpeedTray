"""GUI smoke tests for the update-available dialog (pytest-qt, offscreen)."""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.update_dialog import UpdateDialog


@pytest.fixture
def i18n():
    return I18nStrings("en_US")


def test_shows_inert_release_notes(qtbot, i18n):
    dlg = UpdateDialog(i18n, "1.9.0", "2.0.0", "## What's new\n- native taskbar embed")
    qtbot.addWidget(dlg)
    assert hasattr(dlg, "notes")
    assert "native taskbar embed" in dlg.notes.toPlainText()
    assert dlg.notes.isReadOnly()  # inert: read-only plain text, no rich rendering


def test_no_notes_pane_when_body_blank(qtbot, i18n):
    dlg = UpdateDialog(i18n, "1.9.0", "2.0.0", "   ")
    qtbot.addWidget(dlg)
    assert not hasattr(dlg, "notes")


def test_default_action_is_dismiss_and_choose_updates_it(qtbot, i18n):
    dlg = UpdateDialog(i18n, "1.9.0", "2.0.0", "notes")
    qtbot.addWidget(dlg)
    assert dlg.action == UpdateDialog.ACTION_DISMISS  # closing via X/Esc = dismiss
    dlg._choose(UpdateDialog.ACTION_DOWNLOAD)
    assert dlg.action == UpdateDialog.ACTION_DOWNLOAD
