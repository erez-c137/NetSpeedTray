"""The font picker's Select is its recorded default button - the same ordering as the Settings Save
bug (``setDefault(True)`` ran before the button was inside the dialog, so QDialog never recorded it).
Latent here: Select is the dialog's FIRST push button, so QDialog's own fallback picked it anyway.
These tests guard the behaviour (Enter selects; a focused Cancel cancels), not a reproduced failure."""
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.font_picker import FontPickerDialog
from netspeedtray.tests.unit.test_settings_font_memory import fake_fonts  # noqa: F401 (fixture)


def _shown(qt_owned):
    dlg = qt_owned(FontPickerDialog(I18nStrings("en_US"), "Arial", 9, 400, preview_config=None))
    dlg.show()
    dlg.activateWindow()
    QTest.qWaitForWindowActive(dlg, 3000)
    QApplication.processEvents()
    return dlg


def test_select_is_the_recorded_default(q_app, qt_owned, fake_fonts):
    dlg = _shown(qt_owned)
    assert dlg.ok_button.isDefault() and not dlg.cancel_button.isDefault()


def test_enter_in_the_search_box_selects_after_focus_visited_cancel(q_app, qt_owned, fake_fonts):
    dlg = _shown(qt_owned)
    done = []
    dlg.accepted.connect(lambda: done.append("accept"))
    dlg.rejected.connect(lambda: done.append("reject"))
    dlg.cancel_button.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    dlg.search.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert dlg.ok_button.isDefault() and dlg.ok_button.isEnabled()
    QTest.keyClick(dlg.search, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert done == ["accept"]


def test_enter_on_a_focused_cancel_cancels(q_app, qt_owned, fake_fonts):
    dlg = _shown(qt_owned)
    done = []
    dlg.accepted.connect(lambda: done.append("accept"))
    dlg.rejected.connect(lambda: done.append("reject"))
    dlg.cancel_button.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    QTest.keyClick(dlg.cancel_button, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert done == ["reject"]
