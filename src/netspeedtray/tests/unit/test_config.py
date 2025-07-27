"""
Unit tests for the ConfigManager class in the NetSpeedTray application.

This module contains comprehensive tests for the ConfigManager class, focusing
on configuration loading, saving, validation, and error handling.

Requires:
    - pytest
    - pytest-mock
"""

import pytest  # type: ignore
from unittest.mock import patch, mock_open
import json
from pathlib import Path
import os
from netspeedtray.utils.config import ConfigManager
from netspeedtray.constants.constants import (
    ConfigConstants,
    HistoryPeriodConstants,
    LegendPositionConstants,
    DataRetentionConstants,
)


class TestConfigManager:
    """Test suite for the ConfigManager class."""

    @pytest.fixture
    def config_manager(self, tmp_path):
        """
        Fixture to create a ConfigManager instance with a temporary path.

        Args:
            tmp_path: Pytest fixture providing a temporary directory.

        Returns:
            ConfigManager: Instance with config path in tmp_path.
        """
        config_path = tmp_path / "netspeedtray.conf"
        return ConfigManager(str(config_path))

    def test_load_with_valid_config(self, config_manager):
        """
        Test loading a valid configuration file.

        Verifies merging with defaults and validation.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        mock_content = json.dumps({"update_rate": 0.5, "font_size": 10})
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "open", mock_open(read_data=mock_content)):
                config = config_manager.load()

        assert config["update_rate"] == 0.5, "Should load update_rate from file"
        assert config["font_size"] == 10, "Should load font_size from file"
        assert config["font_weight"] == ConfigConstants.DEFAULT_CONFIG["font_weight"], "Should use default font_weight"

    def test_load_with_missing_file(self, config_manager):
        """
        Test loading when the config file is missing.

        Verifies default config is returned and saved.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        with patch.object(Path, "exists", return_value=False):
            with patch.object(config_manager, "save") as mock_save:
                config = config_manager.load()
                mock_save.assert_called_once_with(ConfigConstants.DEFAULT_CONFIG)

        assert config == ConfigConstants.DEFAULT_CONFIG, "Should return default config"

    def test_load_with_invalid_json(self, config_manager):
        """
        Test loading with invalid JSON.

        Verifies default config is returned and corrupted file is overwritten.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "open", mock_open(read_data="{invalid json")):
                with patch.object(config_manager, "save") as mock_save:
                    config = config_manager.load()
                    mock_save.assert_called_once_with(ConfigConstants.DEFAULT_CONFIG)

        assert config == ConfigConstants.DEFAULT_CONFIG, "Should revert to default on invalid JSON"

    def test_load_permission_error(self, config_manager):
        """
        Test loading with a permission error.

        Verifies PermissionError is raised.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "open", side_effect=PermissionError("Access denied")):
                with pytest.raises(PermissionError):
                    config_manager.load()

    def test_load_os_error(self, config_manager):
        """
        Test loading with an OS error (other than permission error).

        Verifies OSError is raised.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "open", side_effect=OSError("Disk full")):
                with pytest.raises(OSError):
                    config_manager.load()

    def test_save_with_valid_config(self, config_manager):
        """
        Test saving a valid configuration.

        Verifies atomic write using tempfile and shutil.move.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        test_config = {"update_rate": 0.5, "font_size": 10}
        with patch("tempfile.NamedTemporaryFile") as mock_temp:
            mock_file = mock_open()
            mock_temp.return_value.__enter__.return_value = mock_file.return_value
            with patch("shutil.move") as mock_move:
                config_manager.save(test_config)

            mock_file().write.assert_called_once()
            written_content = mock_file().write.call_args[0][0]
            assert json.loads(written_content)["update_rate"] == 0.5, "Should write correct update_rate"
            mock_move.assert_called_once()

    def test_save_permission_error(self, config_manager):
        """
        Test saving with a permission error.

        Verifies PermissionError is raised.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        with patch("tempfile.NamedTemporaryFile", side_effect=PermissionError("No access")):
            with pytest.raises(PermissionError):
                config_manager.save({"update_rate": 0.5})

    def test_save_unchanged_config(self, config_manager):
        """
        Test saving an unchanged configuration.

        Verifies that saving is skipped if the config hasn't changed.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        test_config = {"update_rate": 0.5, "font_size": 10}
        # First save
        with patch("tempfile.NamedTemporaryFile") as mock_temp:
            mock_file = mock_open()
            mock_temp.return_value.__enter__.return_value = mock_file.return_value
            with patch("shutil.move") as mock_move:
                config_manager.save(test_config)
                mock_move.assert_called_once()

        # Second save (unchanged)
        with patch("tempfile.NamedTemporaryFile") as mock_temp:
            with patch("shutil.move") as mock_move:
                config_manager.save(test_config)
                mock_temp.assert_not_called()
                mock_move.assert_not_called()

    def test_reset_to_defaults(self, config_manager):
        """
        Test resetting to default configuration.

        Verifies defaults are saved and returned.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        with patch.object(config_manager, "save") as mock_save:
            config = config_manager.reset_to_defaults()
            mock_save.assert_called_once_with(ConfigConstants.DEFAULT_CONFIG)
            assert config == ConfigConstants.DEFAULT_CONFIG, "Should return default config"

    def test_validate_config_invalid_values(self, config_manager):
        """
        Test validation of a config with invalid values.

        Verifies that invalid values are corrected to defaults.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        invalid_config = {
            "update_rate": -1,
            "font_size": 0,
            "font_weight": -100,
            "default_color": "invalid",
            "high_speed_threshold": -10,
            "low_speed_threshold": -5,
            "history_minutes": 0,
            "graph_opacity": 150,
            "keep_data": 0,
            "smart_threshold": -1,
            "dark_mode": "invalid",
            "selected_interfaces": "not a list",
            "interface_mode": "invalid",
            "history_period": "invalid",
            "legend_position": "invalid",
            "use_megabytes": "invalid",
        }
        validated_config = config_manager._validate_config(invalid_config)

        assert validated_config["update_rate"] == ConfigConstants.DEFAULT_UPDATE_RATE, "Should correct invalid update_rate"
        assert validated_config["font_size"] == ConfigConstants.DEFAULT_FONT_SIZE, "Should correct invalid font_size"
        assert validated_config["font_weight"] == ConfigConstants.DEFAULT_FONT_WEIGHT, "Should correct invalid font_weight"
        assert validated_config["default_color"] == ConfigConstants.DEFAULT_CONFIG["default_color"], "Should correct invalid color"
        assert validated_config["high_speed_threshold"] == ConfigConstants.HIGH_SPEED_THRESHOLD, "Should correct invalid high_speed_threshold"
        assert validated_config["low_speed_threshold"] == ConfigConstants.LOW_SPEED_THRESHOLD, "Should correct invalid low_speed_threshold"
        assert validated_config["history_minutes"] == ConfigConstants.DEFAULT_HISTORY_MINUTES, "Should correct invalid history_minutes"
        assert validated_config["graph_opacity"] == ConfigConstants.DEFAULT_GRAPH_OPACITY, "Should correct invalid graph_opacity"
        assert validated_config["keep_data"] == ConfigConstants.DEFAULT_KEEP_DATA_DAYS, "Should correct invalid keep_data"
        assert validated_config["smart_threshold"] == ConfigConstants.DEFAULT_SMART_THRESHOLD, "Should correct invalid smart_threshold"
        assert validated_config["dark_mode"] is False, "Should correct invalid dark_mode"
        assert validated_config["selected_interfaces"] == [], "Should correct invalid selected_interfaces"
        assert validated_config["interface_mode"] == "all", "Should correct invalid interface_mode"
        assert validated_config["history_period"] == HistoryPeriodConstants.PERIOD_MAP[0], "Should correct invalid history_period"
        assert validated_config["legend_position"] == "off", "Should correct invalid legend_position"
        assert validated_config["use_megabytes"] is False, "Should correct invalid use_megabytes"

    def test_validate_config_threshold_swap(self, config_manager):
        """
        Test validation when low_speed_threshold > high_speed_threshold.

        Verifies that thresholds are swapped.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        config = {
            "low_speed_threshold": 100,
            "high_speed_threshold": 50,
        }
        validated_config = config_manager._validate_config(config)
        assert validated_config["low_speed_threshold"] == 50, "Should swap low_speed_threshold"
        assert validated_config["high_speed_threshold"] == 100, "Should swap high_speed_threshold"

    def test_validate_key_valid(self, config_manager):
        """
        Test validating a single valid key.

        Verifies that the value is returned unchanged if valid.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        value = config_manager.validate_key("update_rate", 0.5)
        assert value == 0.5, "Should return valid update_rate unchanged"

    def test_validate_key_invalid(self, config_manager):
        """
        Test validating a single invalid key.

        Verifies that the value is corrected to the default.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        value = config_manager.validate_key("update_rate", -1)
        assert value == ConfigConstants.DEFAULT_UPDATE_RATE, "Should correct invalid update_rate"

    def test_validate_key_unrecognized(self, config_manager):
        """
        Test validating an unrecognized key.

        Verifies that a ValueError is raised.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        with pytest.raises(ValueError, match="Unrecognized configuration key: invalid_key"):
            config_manager.validate_key("invalid_key", 123)

    def test_validate_config_missing_key_raises(self, config_manager):
        """
        Test validation with a missing required key.

        Verifies that a KeyError is raised if validation accesses a missing key.

        Args:
            config_manager: Fixture providing a ConfigManager instance.
        """
        # Simulate a config with missing keys that validation will try to access
        config = {}
        with pytest.raises(KeyError):
            config_manager._validate_config(config)