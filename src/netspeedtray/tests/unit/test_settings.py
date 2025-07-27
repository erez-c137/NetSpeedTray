import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QPoint
from netspeedtray.views.settings import SettingsDialog
from netspeedtray.views.widget import NetworkSpeedWidget

class TestSettingsDialog(unittest.TestCase):
    def setUp(self):
        self.parent_widget = MagicMock(spec=NetworkSpeedWidget)
        self.parent_widget.logger = MagicMock()
        self.parent_widget.APP_NAME = "NetSpeedTray"
        self.parent_widget.config = {
            "update_rate": 1.0,
            "font_size": 10,
            "font_weight": 400,
            "font_family": "Arial",
            "color_coding": False,
            "default_color": "#FFFFFF",
            "high_speed_threshold": 5.0,
            "low_speed_threshold": 1.0,
            "high_speed_color": "#00FF00",
            "low_speed_color": "#FF0000",
            "graph_enabled": False,
            "history_minutes": 30,
            "graph_opacity": 30,
            "use_megabytes": False,
            "interface_mode": "all",
            "selected_interfaces": []
        }
        self.parent_widget.get_available_interfaces.return_value = ["eth0", "wlan0"]
        self.parent_widget.is_startup_enabled.return_value = False
        self.config = self.parent_widget.config.copy()
        self.version = "1.0.0"
        self.initial_pos = QPoint(0, 0)
        self.dialog = SettingsDialog(
            self.parent_widget, self.config, self.version, self.initial_pos
        )

    def test_settings_update(self):
        # Setup
        self.dialog.setup_ui()
        self.dialog.update_rate.setValue(4)  # 2.0 seconds
        self.dialog.font_size.setValue(12)
        self.dialog.use_megabytes.setValue(1)

        # Simulate timer timeout for throttled signal
        self.dialog._update_timer.timeout.emit()

        # Assert
        self.assertEqual(self.dialog.config["update_rate"], 2.0)
        self.assertEqual(self.dialog.config["font_size"], 12)
        self.assertTrue(self.dialog.config["use_megabytes"])
        self.parent_widget.apply_all_settings.assert_called()

    def test_signal_emission(self):
        # Setup
        self.dialog.setup_ui()
        self.dialog.settings_changed.connect = MagicMock()

        # Change a setting
        self.dialog.update_rate.setValue(4)  # 2.0 seconds

        # Simulate timer timeout
        self.dialog._update_timer.timeout.emit()

        # Assert
        self.dialog.settings_changed.emit.assert_called_once()

if __name__ == "__main__":
    unittest.main()