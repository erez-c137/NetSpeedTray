"""
GUI interaction tests for the Settings dialog (pytest-qt / qtbot, headless offscreen).

These drive the REAL widgets — clicking the actual Save / Cancel buttons — and assert
the parent callback fires with the correct save_to_disk flag. This is the highest-value
slice of the manual Settings QA, now automated and CI-runnable.

The dialog is only loosely coupled to its parent (it touches config, config_manager,
handle_settings_changed and toggle_startup), so a MagicMock parent stands it up. The
QApplication + offscreen platform come from conftest.py / pytest-qt's qtbot fixture.
"""
import pytest
from unittest.mock import MagicMock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.dialog import SettingsDialog


def _save_to_disk_flag(call_args):
    """Extract the save_to_disk argument whether it was passed positionally or by keyword."""
    args, kwargs = call_args
    if "save_to_disk" in kwargs:
        return kwargs["save_to_disk"]
    return args[1] if len(args) > 1 else None


@pytest.fixture
def fake_main_widget():
    m = MagicMock()
    m.config = {}                  # save_window_position writes the window pos here
    m.config_manager = MagicMock()
    return m


@pytest.fixture
def dialog(qtbot, fake_main_widget, monkeypatch):
    # Guard the modal dialogs: offscreen they would block forever if triggered, and we
    # want a get_settings() bug to surface as a test failure rather than a hang.
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    dlg = SettingsDialog(
        main_widget=fake_main_widget,
        config=dict(constants.config.defaults.DEFAULT_CONFIG),
        version="1.3.4",
        i18n=I18nStrings("en_US"),
        available_interfaces=["Ethernet", "Wi-Fi"],
        is_startup_enabled=False,
    )
    qtbot.addWidget(dlg)
    return dlg


def test_dialog_constructs_all_pages_offscreen(dialog):
    """The full multi-page Settings dialog builds headless without error."""
    for page in ("general_page", "appearance_page", "colors_page",
                 "hardware_page", "units_page", "interfaces_page"):
        assert getattr(dialog, page, None) is not None


def test_clicking_save_applies_with_save_to_disk_true(qtbot, dialog, fake_main_widget):
    """Clicking the real Save button applies settings to the parent with save_to_disk=True."""
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)

    fake_main_widget.handle_settings_changed.assert_called()
    assert _save_to_disk_flag(fake_main_widget.handle_settings_changed.call_args) is True


def test_clicking_cancel_reverts_with_save_to_disk_false(qtbot, dialog, fake_main_widget):
    """Clicking the real Cancel button reverts to the original config without saving."""
    qtbot.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)

    fake_main_widget.handle_settings_changed.assert_called()
    assert _save_to_disk_flag(fake_main_widget.handle_settings_changed.call_args) is False


def test_changed_toggle_propagates_to_parent_on_save(qtbot, dialog, fake_main_widget):
    """A real widget change (the Free Move toggle) round-trips to the parent on Save —
    not just that Save fired, but that the changed value is in the applied settings."""
    toggle = dialog.general_page.free_move
    new_value = not toggle.isChecked()
    toggle.setChecked(new_value)

    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)

    applied = fake_main_widget.handle_settings_changed.call_args[0][0]
    assert applied["free_move"] is new_value
