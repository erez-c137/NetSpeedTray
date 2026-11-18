"""Enter in Settings presses Save - never "Export support bundle" (found by the 2.2.2 live VM test, 2026-11-18).

On the VM, Enter on a focused switch opened "Save support bundle" 4/4 times, a freshly opened dialog
too. ``save_button.setDefault(True)`` ran before the button had the dialog as an ancestor, so QDialog
never recorded it as its default, and on show it made the FIRST auto-default push button the default:
Export, the left-most footer button. Every QPushButton in a QDialog is auto-default unless told
otherwise.
"""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from netspeedtray.tests.unit.test_rc_settings_fixes import _make


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    # A wrongly pressed Export must fail the test, never block it on a modal file dialog.
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))


def _shown(qtbot):
    dlg, _main = _make(qtbot)
    dlg.show()
    QApplication.processEvents()
    return dlg


def test_save_is_the_only_default_button(qtbot):
    dlg = _shown(qtbot)
    assert dlg.save_button.isDefault()
    assert not dlg.export_log_button.isDefault() and not dlg.export_log_button.autoDefault()
    assert not dlg.cancel_button.isDefault() and not dlg.cancel_button.autoDefault()


def test_enter_on_a_focused_switch_presses_save_not_export(qtbot):
    dlg = _shown(qtbot)
    pressed = []
    dlg.export_log_button.clicked.connect(lambda: pressed.append("export"))
    dlg.cancel_button.clicked.connect(lambda: pressed.append("cancel"))
    dlg.save_button.clicked.connect(lambda: pressed.append("save"))
    switch = dlg.general_page.start_with_windows.checkbox
    switch.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    QTest.keyClick(switch, Qt.Key.Key_Return)
    assert pressed == ["save"]
