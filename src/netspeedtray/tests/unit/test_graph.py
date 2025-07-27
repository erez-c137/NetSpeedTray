import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from PyQt6.QtCore import QTimer
from netspeedtray.views.graph import GraphWindow
from netspeedtray.views.widget import NetworkSpeedWidget
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.constants.i18n_strings import I18nStrings
from netspeedtray.constants.constants import UIStyleConstants, ConfigConstants

class TestGraphWindow(unittest.TestCase):
    def setUp(self):
        self.parent_widget = MagicMock(spec=NetworkSpeedWidget)
        self.parent_widget.logger = MagicMock()        
        self.parent_widget.config = {
            "dark_mode": False,
            "use_megabytes": False,
            "keep_data": 30,
            "history_period": "System Uptime",
            "legend_position": "off",
            "interface_mode": "all",
            "update_rate": 1.0,
            "history_minutes": 30,
            "selected_interfaces": []
        }
        self.parent_widget.config_manager = MagicMock()
        self.parent_widget.widget_state = WidgetState(self.parent_widget.config)
        self.history_data = [
            (datetime.now() - timedelta(minutes=i), 1000 * i, 2000 * i)
            for i in range(10)
        ]
        self.parent_widget.widget_state.speed_history = self.history_data
        self.graph_window = GraphWindow(self.parent_widget)

    def test_graph_update(self):
        # Setup
        self.graph_window.update_graph(self.history_data)

        # Simulate timer timeout for throttled update
        self.graph_window._update_timer.timeout.emit()

        # Assert
        self.assertFalse(self.graph_window._graph_update_pending)
        self.assertEqual(self.graph_window.stats_bar.text(), self.graph_window.stats_bar.text())  # Check stats bar updated
        self.parent_widget.config_manager.save.assert_called()

    def test_dark_mode_toggle(self):
        # Setup
        self.graph_window.dark_mode.setValue(1)

        # Toggle dark mode
        self.graph_window._on_dark_mode_toggled(1)

        # Assert
        self.assertTrue(self.parent_widget.config["dark_mode"])
        self.assertEqual(
            self.figure.patch.get_facecolor(),
            UIStyleConstants.DARK_MODE_BG_COLOR,
        )

if __name__ == "__main__":
    unittest.main()