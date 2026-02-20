"""
Unit tests for the ConfigManager class in the NetSpeedTray application.
"""
import pytest
from unittest.mock import patch, mock_open
import json
from pathlib import Path
from netspeedtray import constants
from netspeedtray.utils.config import ConfigManager, ConfigError

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
        "update_rate": -2,
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


# ============================================================================
# P0.1: Tests for Config Version Validation (NEW)
# ============================================================================

def test_version_less_than_valid_versions(config_manager):
    """Verify _version_less_than correctly compares valid versions."""
    # Valid comparisons
    assert config_manager._version_less_than("1.0", "2.0") is True
    assert config_manager._version_less_than("1.0", "1.1") is True
    assert config_manager._version_less_than("1.5", "2.0") is True
    assert config_manager._version_less_than("2.0", "1.9") is False
    assert config_manager._version_less_than("1.0", "1.0") is False


def test_version_less_than_invalid_format_raises_error(config_manager):
    """Verify _version_less_than raises ConfigError on invalid version strings."""
    # Invalid first parameter
    with pytest.raises(ConfigError, match="Invalid version format"):
        config_manager._version_less_than("invalid", "1.0")
    
    # Invalid second parameter
    with pytest.raises(ConfigError, match="Invalid version format"):
        config_manager._version_less_than("1.0", "not_a_version")
    
    # Non-numeric components
    with pytest.raises(ConfigError, match="Invalid version format"):
        config_manager._version_less_than("1.0.alpha", "2.0")
    
    # Too many components (non-numeric)
    with pytest.raises(ConfigError, match="Invalid version format"):
        config_manager._version_less_than("1.0.0.0.too.many", "2.0")


def test_version_less_than_empty_string_raises_error(config_manager):
    """Verify _version_less_than rejects empty version strings."""
    with pytest.raises(ConfigError, match="Invalid version format"):
        config_manager._version_less_than("", "1.0")
    
    with pytest.raises(ConfigError, match="Invalid version format"):
        config_manager._version_less_than("1.0", "")


def test_config_migration_with_corrupted_version(config_manager):
    """Verify migration gracefully handles corrupted version strings."""
    corrupted_config = {
        "config_version": "INVALID_VERSION",
        "update_rate": 1.5,
        "font_size": 12,
    }
    
    # Should not raise, should reset to defaults
    result = config_manager._migrate_config(corrupted_config)
    
    # Should reset to defaults, not crash
    assert result["config_version"] == constants.config.defaults.CONFIG_SCHEMA_VERSION
    # Verify it's a proper default config (has all required keys)
    assert "update_rate" in result
    assert "font_size" in result


def test_config_migration_with_valid_version(config_manager):
    """Verify migration succeeds with valid version strings."""
    current_version = constants.config.defaults.CONFIG_SCHEMA_VERSION
    valid_config = {
        "config_version": "1.0",
        "update_rate": 1.5,
        "font_size": 12,
    }
    
    # Should not raise, should migrate successfully
    result = config_manager._migrate_config(valid_config)
    
    # Should maintain the migrated version
    assert result["config_version"] == current_version
    # Original values should be preserved (validated)
    assert "update_rate" in result
    assert "font_size" in result


def test_config_migration_with_non_string_version(config_manager):
    """Verify migration handles non-string version values (edge case)."""
    invalid_config = {
        "config_version": 123,  # Integer instead of string
        "update_rate": 1.5,
    }
    
    # Should handle gracefully
    result = config_manager._migrate_config(invalid_config)
    assert result["config_version"] == constants.config.defaults.CONFIG_SCHEMA_VERSION


def test_config_migration_missing_version_defaults_to_1_0(config_manager):
    """Verify migration defaults to version 1.0 if config_version is missing."""
    config_without_version = {
        "update_rate": 1.5,
        "font_size": 12,
    }
    
    # Should not raise, should default to 1.0 and migrate
    result = config_manager._migrate_config(config_without_version)
    
    # Should set to current version
    assert result["config_version"] == constants.config.defaults.CONFIG_SCHEMA_VERSION