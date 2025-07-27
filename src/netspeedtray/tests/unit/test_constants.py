"""Unit tests for NetSpeedTray constants.

Validates constant values across all constant classes for type correctness,
range validity, and consistency.
"""

import unittest
from netspeedtray.constants.constants import (
    ConfigConstants, UnitConstants, HelperConstants,
    PositionConstants, TaskbarConstants, TimerConstants, RendererConstants
)


class TestConstants(unittest.TestCase):
    """Tests for validating NetSpeedTray constants."""

    def test_config_constants(self):
        """Validate ConfigConstants properties."""
        self.assertIsInstance(ConfigConstants.DEFAULT_CONFIG, dict)
        self.assertGreaterEqual(ConfigConstants.DEFAULT_UPDATE_RATE, 0)
        self.assertTrue(all(c.startswith('#') and len(c) == 7 for c in [
            ConfigConstants.DEFAULT_COLOR, ConfigConstants.HIGH_SPEED_COLOR,
            ConfigConstants.LOW_SPEED_COLOR]))

    def test_unit_constants(self):
        """Validate UnitConstants properties."""
        self.assertGreater(UnitConstants.BITS_PER_BYTE, 0)
        self.assertTrue(all(isinstance(u, str) and u for u in [
            UnitConstants.KBPS_LABEL, UnitConstants.MBPS_LABEL, UnitConstants.GBPS_LABEL]))

    def test_helper_constants(self):
        """Validate HelperConstants properties."""
        self.assertTrue(HelperConstants.APP_NAME)
        self.assertGreater(HelperConstants.MAX_LOG_SIZE, 0)

    def test_position_constants(self):
        """Validate PositionConstants properties."""
        self.assertGreater(PositionConstants.DEFAULT_PADDING, 0)
        self.assertGreater(PositionConstants.SCREEN_EDGE_MARGIN, 0)

    def test_taskbar_constants(self):
        """Validate TaskbarConstants properties."""
        self.assertGreater(TaskbarConstants.DEFAULT_HEIGHT, 0)
        self.assertGreater(TaskbarConstants.MIN_VISIBLE_SIZE, 0)

    def test_timer_constants(self):
        """Validate TimerConstants properties."""
        self.assertGreater(TimerConstants.MIN_INTERVAL, 0)
        self.assertGreater(TimerConstants.CSV_FLUSH_INTERVAL, 0)

    def test_renderer_constants(self):
        """Validate RendererConstants properties."""
        self.assertGreater(RendererConstants.SPEED_HISTORY_MAXLEN, 0)
        self.assertGreater(RendererConstants.MIN_GRAPH_POINTS, 0)


if __name__ == "__main__":
    unittest.main()