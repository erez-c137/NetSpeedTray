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
        assert validated_config["low_speed_threshold"] == constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD
        assert validated_config["high_speed_threshold"] == constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD

def test_validate_config_handles_equal_thresholds(config_manager):
    equal_config = { "low_speed_threshold": 10.0, "high_speed_threshold": 10.0 }
    with patch.object(config_manager.logger, 'warning'):
        validated_config = config_manager._validate_config(equal_config)
        assert validated_config["low_speed_threshold"] == constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD
        assert validated_config["high_speed_threshold"] == constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD

def test_validate_config_preserves_high_thresholds_when_valid(config_manager):
    """High threshold values are preserved as long as low < high invariant holds."""
    inflated_config = { "low_speed_threshold": 1.0, "high_speed_threshold": 10000.0 }
    validated_config = config_manager._validate_config(inflated_config)
    assert validated_config["low_speed_threshold"] == 1.0
    assert validated_config["high_speed_threshold"] == 10000.0

def test_validate_config_accepts_high_speed_thresholds(config_manager):
    """Valid thresholds on a multi-gigabit link are preserved without resetting."""
    high_speed_config = { "low_speed_threshold": 100.0, "high_speed_threshold": 2500.0 }
    validated_config = config_manager._validate_config(high_speed_config)
    assert validated_config["low_speed_threshold"] == 100.0
    assert validated_config["high_speed_threshold"] == 2500.0


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
    """v2.1.5 item 4a: an unparseable config_version must NOT wipe the user's settings.

    Field-level migrations are idempotent and version-independent, so they run
    regardless; the version string is treated like an unknown-but-newer one and
    left untouched. DEFAULT_CONFIG.copy() is reachable only from load()'s
    unreadable-file branch (which makes a .corrupt backup first).
    """
    corrupted_config = {
        "config_version": "INVALID_VERSION",
        "update_rate": 1.5,
        "font_size": 12,
    }

    # Should not raise, and must NOT reset to defaults
    result = config_manager._migrate_config(corrupted_config)

    assert result["update_rate"] == 1.5
    assert result["font_size"] == 12
    # The unparseable version string is left as-is, never stamped over.
    assert result["config_version"] == "INVALID_VERSION"


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


def test_keep_data_legacy_value_snaps_to_nearest_not_year(config_manager):
    """#3: a retention value from the OLD ladder (e.g. 7-day) must snap to the nearest CURRENT ladder
    value (the 31-day minimum), not get reset to the 1-year default by validation on upgrade."""
    valid = list(constants.data.retention.DAYS_MAP.values())
    # Old short retentions -> the new minimum, not 365
    for old in (1, 7, 14, 30):
        result = config_manager._migrate_config({"keep_data": old})
        assert result["keep_data"] == min(valid), f"old keep_data {old} -> {result['keep_data']}"
    # Values still on the current ladder are untouched
    for keep in (90, 365):
        assert config_manager._migrate_config({"keep_data": keep})["keep_data"] == keep


def test_config_migration_with_non_string_version(config_manager):
    """Verify migration handles non-string version values (edge case).

    v2.1.5 item 4a: unparseable == keep the user's values. The bogus version is
    left for _validate_config to coerce (its schema type is str), so migration
    itself must not touch it - and must not wipe anything else.
    """
    invalid_config = {
        "config_version": 123,  # Integer instead of string
        "update_rate": 1.5,
    }

    # Should handle gracefully, preserving user values
    result = config_manager._migrate_config(invalid_config)
    assert result["update_rate"] == 1.5
    assert result["config_version"] == 123


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


# ============================================================================
# v2.1.5 item 4: config round-trip safety
# 4a - unparseable version keeps user settings; 4b - never stamp the version
# DOWNWARD; 4c - unknown keys survive load -> mutate -> save; plus the
# version-mismatch backup (`config.json.bak.v<loaded>`).
# ============================================================================

def _write_config_file(tmp_path, payload):
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_unparseable_version_preserves_user_settings(tmp_path):
    """4a: config_version='2.0-beta' (a rollback from a prerelease build) must load
    with ALL user settings intact - reproducing the exact wipe from the action plan."""
    path = _write_config_file(tmp_path, {
        "config_version": "2.0-beta",
        "language": "de_DE",
        "keep_data": 36500,
        "data_cap_gb": 250.0,
        "plan_down_mbps": 500,
        "tray_offset_x": -120,
    })
    loaded = ConfigManager(path).load()

    assert loaded["language"] == "de_DE"
    assert loaded["keep_data"] == 36500
    assert loaded["data_cap_gb"] == 250.0
    assert loaded["plan_down_mbps"] == 500
    assert loaded["tray_offset_x"] == -120


def test_unparseable_version_string_survives_load(tmp_path):
    """4a: the unparseable version string itself is left alone, so the config still
    looks like what wrote it (a prerelease build) instead of getting restamped."""
    path = _write_config_file(tmp_path, {"config_version": "2.0-beta", "font_size": 10})
    loaded = ConfigManager(path).load()
    assert loaded["config_version"] == "2.0-beta"
    assert loaded["font_size"] == 10


def test_newer_version_is_not_stamped_down(config_manager):
    """4b: a config_version that parses HIGHER than the schema version must not be
    stamped downward - '1.2' in must stay '1.2', not become '1.1'."""
    result = config_manager._migrate_config({"config_version": "1.2"})
    assert result["config_version"] == "1.2"


def test_newer_version_survives_full_load(tmp_path):
    """4b, end to end: load() of a newer-versioned config keeps the version."""
    path = _write_config_file(tmp_path, {"config_version": "1.2", "font_size": 10})
    loaded = ConfigManager(path).load()
    assert loaded["config_version"] == "1.2"
    assert loaded["font_size"] == 10


def test_older_version_still_stamped_forward(config_manager):
    """Guard: 4b must not break the normal upgrade stamp (older -> current)."""
    result = config_manager._migrate_config({"config_version": "1.0"})
    assert result["config_version"] == constants.config.defaults.CONFIG_SCHEMA_VERSION


def test_unknown_key_survives_load_mutate_save(tmp_path):
    """4c: keys written by a newer build must ride through a full
    load -> mutate -> save round trip untouched, or a rollback erases them."""
    current = constants.config.defaults.CONFIG_SCHEMA_VERSION
    path = _write_config_file(tmp_path, {
        "config_version": current,
        "font_size": 10,
        "metrics_endpoint_enabled": True,       # hypothetical 2.2-era keys
        "beta_opt_in": {"channel": "beta"},
    })
    manager = ConfigManager(path)
    loaded = manager.load()

    assert loaded["metrics_endpoint_enabled"] is True
    assert loaded["beta_opt_in"] == {"channel": "beta"}

    loaded["font_size"] = 9                     # a realistic settings edit
    manager.save(loaded)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["font_size"] == 9
    assert on_disk["metrics_endpoint_enabled"] is True
    assert on_disk["beta_opt_in"] == {"channel": "beta"}


def test_unknown_key_rides_through_noop_save_shortcut(tmp_path):
    """4c blast radius: save() filters on `is not None` and compares against
    _last_config for its no-op shortcut. Unknown keys must be present on BOTH
    sides of that comparison, or the shortcut stops firing."""
    current = constants.config.defaults.CONFIG_SCHEMA_VERSION
    path = _write_config_file(tmp_path, {
        "config_version": current,
        "font_size": 10,
        "future_key": "x",
    })
    manager = ConfigManager(path)
    loaded = manager.load()
    assert loaded["future_key"] == "x"

    mtime_before = path.stat().st_mtime_ns
    manager.save(dict(loaded))  # nothing changed -> the no-op shortcut must fire
    assert path.stat().st_mtime_ns == mtime_before, "no-op save rewrote the file"

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["future_key"] == "x"


def test_version_mismatch_creates_config_backup(tmp_path):
    """A config written by a DIFFERENT schema version is copied to
    `<name>.bak.v<loaded_version>` before migration touches it."""
    path = _write_config_file(tmp_path, {"config_version": "1.0", "font_size": 10})
    ConfigManager(path).load()

    backup = path.with_name(f"{path.name}.bak.v1.0")
    assert backup.exists(), "no pre-migration config backup was created"
    raw = json.loads(backup.read_text(encoding="utf-8"))
    assert raw["config_version"] == "1.0"
    assert raw["font_size"] == 10


def test_same_version_creates_no_backup(tmp_path):
    """No version change -> no backup churn on every launch."""
    current = constants.config.defaults.CONFIG_SCHEMA_VERSION
    path = _write_config_file(tmp_path, {"config_version": current, "font_size": 10})
    ConfigManager(path).load()
    assert not list(tmp_path.glob("*.bak.v*"))


def test_backup_filename_sanitizes_hostile_version_string(tmp_path):
    """A corrupted version string must still produce a usable backup filename."""
    path = _write_config_file(tmp_path, {"config_version": "2.0/beta:*?", "font_size": 10})
    loaded = ConfigManager(path).load()
    assert loaded["font_size"] == 10           # 4a: still no wipe
    backups = list(tmp_path.glob("*.bak.v*"))
    assert len(backups) == 1, "hostile version string prevented the backup"

# --- adversarial-review regressions (C2, L4) ---------------------------------------------------

def test_config_backup_is_not_overwritten_by_a_second_load(tmp_path):
    """Review C2: the FIRST backup per source version is the pristine one. Launch 2 of a
    rolled-back build must not overwrite it with the by-then-mutated config."""
    path = _write_config_file(tmp_path, {"config_version": "9.9", "font_size": 10})
    ConfigManager(path).load()
    backup = path.with_name(f"{path.name}.bak.v9.9")
    pristine = backup.read_text(encoding="utf-8")

    mutated = json.loads(path.read_text(encoding="utf-8"))
    mutated["font_size"] = 22                   # the exit-save of session 1
    path.write_text(json.dumps(mutated), encoding="utf-8")
    ConfigManager(path).load()                  # session 2

    assert backup.read_text(encoding="utf-8") == pristine, (
        "the pristine pre-migration backup was overwritten by a later load")


def test_unknown_null_key_survives_save(tmp_path):
    """Review L4: for a newer build's key, null and absent are different things - a JSON-null
    unknown key must ride through a real save, not be dropped by the is-not-None filter."""
    path = _write_config_file(
        tmp_path, {"config_version": "9.9", "font_size": 10, "future_null_key": None})
    mgr = ConfigManager(path)
    cfg = mgr.load()
    assert "future_null_key" in cfg
    cfg["font_size"] = 22                       # a real change, so the no-op shortcut cannot hide it
    mgr.save(cfg)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "future_null_key" in on_disk and on_disk["future_null_key"] is None
