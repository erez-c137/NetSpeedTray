"""
Unit tests for the SettingsDialog class.
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication, QWidget

# The new, single, correct way to import all constants
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
    # Import locally to avoid issues with Qt event loop in pytest
    from netspeedtray.views.settings import SettingsDialog
    
    # Create a real, but simple, QWidget to act as the Qt parent.
    actual_qt_parent = QWidget() 

    # Instantiate the dialog, passing the REAL QWidget as the parent.
    dialog = SettingsDialog(
        parent=actual_qt_parent, 
        config=mock_parent_widget.config.copy(),
        version="1.1.1-beta.1",
        # Pass the singleton strings instance directly
        i18n=constants.strings,
        available_interfaces=mock_parent_widget.get_available_interfaces(),
        is_startup_enabled=mock_parent_widget.is_startup_enabled()
    )
    # Patch the dialog's logical parent widget to use our mock.
    dialog.parent_widget = mock_parent_widget

    yield dialog
    
    # Cleanup Qt resources
    dialog.deleteLater()
    actual_qt_parent.deleteLater()

def test_get_settings_translates_ui_state_to_config(settings_dialog):
    """
    Tests if the get_settings method correctly translates UI state back into
    a configuration dictionary.
    """
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