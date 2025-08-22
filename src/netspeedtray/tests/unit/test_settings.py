"""
Unit tests for the SettingsDialog class.
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants

@pytest.fixture(scope="session")
def q_app():
    """Provides a QApplication instance for the test session."""
    return QApplication.instance() or QApplication([])

@pytest.fixture
def mock_parent_widget():
    """Creates a mock parent widget with default configuration."""
    parent = MagicMock()
    # Use the new constants namespace to get the default config
    parent.config = constants.config.defaults.DEFAULT_CONFIG.copy()
    parent.get_available_interfaces.return_value = ["Ethernet 1", "Wi-Fi"]
    parent.is_startup_enabled.return_value = False
    return parent

@pytest.fixture
def settings_dialog(q_app, mock_parent_widget):
    """
    Creates an instance of the SettingsDialog for testing, properly handling
    Qt parentage and mocking.
    """
    from netspeedtray.views.settings import SettingsDialog
    
    dialog = SettingsDialog(
        main_widget=mock_parent_widget, # Pass the mock to the correct argument
        config=mock_parent_widget.config.copy(),
        version="1.1.2",
        i18n=constants.i18n.get_i18n(),
        available_interfaces=mock_parent_widget.get_available_interfaces(),
        is_startup_enabled=mock_parent_widget.is_startup_enabled()
    )

    yield dialog
    
    dialog.deleteLater()

def test_get_settings_translates_ui_state_to_config(settings_dialog):
    """
    Tests if the get_settings method correctly translates UI state back into
    a configuration dictionary.
    """
    # Arrange: Simulate user interaction
    settings_dialog.update_rate.setValue(5)
    # Simulate the user choosing to select specific interfaces
    settings_dialog.selected_interfaces_radio.setChecked(True) 
    settings_dialog.interface_checkboxes["Wi-Fi"].setChecked(True)
    settings_dialog.interface_checkboxes["Ethernet 1"].setChecked(False)

    # Act
    new_settings = settings_dialog.get_settings()

    # Assert
    assert new_settings["update_rate"] == 2.5
    assert new_settings["interface_mode"] == "selected"
    assert set(new_settings["selected_interfaces"]) == {"Wi-Fi"}