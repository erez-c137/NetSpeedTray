"""
Unit tests for the SettingsDialog class.
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QWidget
from netspeedtray.constants import ConfigConstants

@pytest.fixture(scope="session")
def q_app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def mock_parent_widget():
    parent = MagicMock()
    parent.config = ConfigConstants.DEFAULT_CONFIG.copy()
    parent.get_available_interfaces.return_value = ["Ethernet 1", "Wi-Fi"]
    parent.is_startup_enabled.return_value = False
    return parent

@pytest.fixture
def settings_dialog(q_app, mock_parent_widget):
    from netspeedtray.views.settings import SettingsDialog
    from netspeedtray.constants.i18n_strings import I18nStrings
    
    # 1. Create a real, but simple, QWidget to act as the Qt parent.
    actual_qt_parent = QWidget() 

    # 2. Instantiate the dialog, but pass the REAL QWidget as the parent.
    dialog = SettingsDialog(
        parent=actual_qt_parent, 
        config=mock_parent_widget.config.copy(),
        version="1.0.8",
        i18n=I18nStrings(),
        available_interfaces=mock_parent_widget.get_available_interfaces(),
        is_startup_enabled=mock_parent_widget.is_startup_enabled()
    )
    # 3. Patch the dialog's parent widget to use the mock.
    dialog.parent_widget = mock_parent_widget

    yield dialog
    dialog.deleteLater()
    actual_qt_parent.deleteLater()

def test_get_settings_translates_ui_state_to_config(settings_dialog):
    # Arrange: Simulate user interaction
    settings_dialog.update_rate.setValue(5)
    settings_dialog.all_interfaces.setChecked(False)
    settings_dialog.interface_checkboxes["Wi-Fi"].setChecked(True)
    settings_dialog.interface_checkboxes["Ethernet 1"].setChecked(False)

    # Act
    new_settings = settings_dialog.get_settings()

    # Assert
    assert new_settings["update_rate"] == 2.5
    assert new_settings["interface_mode"] == "selected"
    assert set(new_settings["selected_interfaces"]) == {"Wi-Fi"}