"""
Unit tests for the ConfigManager class in the NetSpeedTray application.
"""
import pytest
from unittest.mock import patch, mock_open
import json
from pathlib import Path
from netspeedtray import constants
from netspeedtray.utils.config import ConfigManager

@pytest.fixture
def config_manager(tmp_path):
    config_path = tmp_path / "netspeedtray_test.conf"
    return ConfigManager(config_path)

def test_load_creates_default_config_if_missing(config_manager):
    with patch.object(Path, "exists", return_value=False):
        with patch.object(config_manager, "save") as mock_save:
            config = config_manager.load()
            mock_save.assert_called_once()
            assert mock_save.call_args[0][0] == constants.config.defaults.DEFAULT_CONFIG
            assert config == constants.config.defaults.DEFAULT_CONFIG

def test_load_valid_config_merges_with_defaults(config_manager):
    mock_content = json.dumps({"update_rate": 0.5, "font_size": 10})
    with patch.object(Path, "exists", return_value=True):
        with patch.object(Path, "open", mock_open(read_data=mock_content)):
            config = config_manager.load()
    assert config["update_rate"] == 0.5
    assert config["font_size"] == 10
    assert config["font_weight"] == constants.config.defaults.DEFAULT_CONFIG["font_weight"]

def test_save_removes_null_keys_from_file(config_manager):
    config_to_save = constants.config.defaults.DEFAULT_CONFIG.copy()
    assert config_to_save["position_x"] is None

    with patch("json.dump") as mock_json_dump:
        with patch("tempfile.NamedTemporaryFile", mock_open()):
            with patch("shutil.move"):
                config_manager.save(config_to_save)
                written_data = mock_json_dump.call_args[0][0]
                assert "position_x" not in written_data
                assert "graph_window_pos" not in written_data

def test_validate_config_corrects_invalid_values(config_manager):
    invalid_config = {
        "update_rate": -1,
        "default_color": "not-a-hex-code",
        "color_coding": "not-a-boolean",
        "selected_interfaces": "not-a-list",
    }
    with patch.object(config_manager.logger, 'warning'):
        validated_config = config_manager._validate_config(invalid_config)
    
    assert validated_config["update_rate"] == constants.config.defaults.DEFAULT_UPDATE_RATE
    assert validated_config["default_color"] == constants.config.defaults.DEFAULT_COLOR
    assert validated_config["color_coding"] == constants.config.defaults.DEFAULT_COLOR_CODING
    assert validated_config["selected_interfaces"] == []

def test_validate_config_handles_threshold_swap(config_manager):
    swapped_config = { "low_speed_threshold": 100.0, "high_speed_threshold": 50.0 }
    with patch.object(config_manager.logger, 'warning'):
        validated_config = config_manager._validate_config(swapped_config)
        assert validated_config["low_speed_threshold"] == 50.0
        assert validated_config["high_speed_threshold"] == 50.0