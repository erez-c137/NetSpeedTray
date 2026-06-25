"""
Save/load round-trip tests for utils.config.ConfigManager.

These exercise the *real* atomic-write path (tempfile + shutil.move) against a
real temp directory (pytest ``tmp_path``) -- nothing in json/tempfile/shutil is
mocked. The point is to pin the invariant CLAUDE.md calls core: a config saved
by one manager and read back by a fresh manager returns the same values, and
the validation layer sanitizes bad input on the way back in.

Behavior pinned here was confirmed empirically against the current
implementation:

- Round-trip preserves explicitly-set non-default values.
- Missing keys fall back to ``rules["default"]`` from VALIDATION_SCHEMA.
- Out-of-range numbers are *reset to default*, NOT clamped (see
  ``_validate_value`` range check -> ``return default``).
- An invalid ``choices`` value (e.g. unknown interface_mode) -> default.
- A wrong-typed value (e.g. a str where bool is expected) -> default.
- ``load()`` on a missing file returns defaults without crashing AND creates
  the file (via ``reset_to_defaults`` -> ``save``).
- ``save()`` strips keys whose value is ``None`` from the on-disk JSON.
- The high>low threshold invariant resets BOTH thresholds when violated.
"""
import json
from pathlib import Path

import pytest

from netspeedtray.utils.config import ConfigManager
from netspeedtray import constants

DEFAULTS = constants.config.defaults
DEFAULT_CONFIG = DEFAULTS.DEFAULT_CONFIG


def _make(tmp_path: Path) -> ConfigManager:
    """ConfigManager pointed at an isolated temp config file."""
    return ConfigManager(tmp_path / "NetSpeedTray_Config.json")


# --- atomic-write round-trip -------------------------------------------------

def test_roundtrip_preserves_non_default_values(tmp_path):
    """
    The core invariant: save several non-default values with one manager, then
    load with a *fresh* manager (so nothing is served from the in-memory
    ``_last_config`` cache) and get those same values back.
    """
    path = tmp_path / "NetSpeedTray_Config.json"
    writer = ConfigManager(path)

    cfg = DEFAULT_CONFIG.copy()
    cfg["font_size"] = 11                      # int, within [7, 11]
    cfg["interface_mode"] = "all_physical"     # valid choice, != default "auto"
    cfg["color_coding"] = True                 # bool, != default False
    cfg["high_speed_threshold"] = 50.0         # high > low invariant holds
    cfg["low_speed_threshold"] = 5.0
    cfg["decimal_places"] = 2                  # int, within [0, 2]
    cfg["selected_interfaces"] = ["Ethernet", "Wi-Fi"]  # list[str]
    writer.save(cfg)

    # File was actually written through the atomic temp-file+rename path.
    assert path.exists()

    reader = ConfigManager(path)
    loaded = reader.load()

    assert loaded["font_size"] == 11
    assert loaded["interface_mode"] == "all_physical"
    assert loaded["color_coding"] is True
    assert loaded["high_speed_threshold"] == 50.0
    assert loaded["low_speed_threshold"] == 5.0
    assert loaded["decimal_places"] == 2
    assert loaded["selected_interfaces"] == ["Ethernet", "Wi-Fi"]


def test_saved_file_is_valid_json_on_disk(tmp_path):
    """The persisted artifact is real, parseable JSON carrying the set value."""
    path = tmp_path / "NetSpeedTray_Config.json"
    cm = ConfigManager(path)
    cfg = DEFAULT_CONFIG.copy()
    cfg["font_size"] = 10
    cm.save(cfg)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["font_size"] == 10
    # config_version is always persisted as the current schema version.
    assert raw["config_version"] == DEFAULTS.CONFIG_SCHEMA_VERSION


def test_save_strips_none_valued_keys_from_disk(tmp_path):
    """
    ``save`` filters out every key whose value is ``None`` before writing.
    These are still present (as None) when loaded, because the schema supplies
    them as defaults on read.
    """
    path = tmp_path / "NetSpeedTray_Config.json"
    cm = ConfigManager(path)
    cm.save(DEFAULT_CONFIG.copy())

    raw = json.loads(path.read_text(encoding="utf-8"))
    # language / position_x default to None -> not serialized.
    assert "language" not in raw
    assert "position_x" not in raw

    # But the round-tripped dict still carries them as None (schema default).
    loaded = ConfigManager(path).load()
    assert loaded["language"] is None
    assert loaded["position_x"] is None


def test_non_none_position_round_trips(tmp_path):
    """A position that is *not* None is persisted and read back intact."""
    path = tmp_path / "NetSpeedTray_Config.json"
    cfg = DEFAULT_CONFIG.copy()
    cfg["position_x"] = 1234
    cfg["position_y"] = 56
    ConfigManager(path).save(cfg)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["position_x"] == 1234
    assert raw["position_y"] == 56

    loaded = ConfigManager(path).load()
    assert loaded["position_x"] == 1234
    assert loaded["position_y"] == 56


# --- missing keys fall back to defaults --------------------------------------

def test_missing_keys_fall_back_to_defaults(tmp_path):
    """A sparse file on disk is filled out with schema defaults on load."""
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(json.dumps({"font_size": 11}), encoding="utf-8")

    loaded = ConfigManager(path).load()

    # The one key that was present survives.
    assert loaded["font_size"] == 11
    # Everything else comes from defaults.
    assert loaded["decimal_places"] == DEFAULTS.DEFAULT_DECIMAL_PLACES
    assert loaded["interface_mode"] == DEFAULTS.DEFAULT_INTERFACE_MODE
    assert loaded["color_coding"] == DEFAULTS.DEFAULT_COLOR_CODING


def test_load_returns_every_schema_key(tmp_path):
    """Loaded config always exposes the full schema key set, never a subset."""
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(json.dumps({"font_size": 9}), encoding="utf-8")

    loaded = ConfigManager(path).load()
    schema_keys = set(constants.config.defaults.VALIDATION_SCHEMA.keys())
    assert set(loaded.keys()) == schema_keys


# --- invalid values are reset to defaults (NOT clamped) ----------------------

@pytest.mark.parametrize(
    "key, bad_value, expected_default",
    [
        # Out-of-range numerics -> default (range check returns default, not clamp).
        ("font_size", 9999, DEFAULTS.DEFAULT_FONT_SIZE),     # max is 11
        ("font_size", 1, DEFAULTS.DEFAULT_FONT_SIZE),        # min is 7
        ("decimal_places", -5, DEFAULTS.DEFAULT_DECIMAL_PLACES),   # min 0
        ("decimal_places", 99, DEFAULTS.DEFAULT_DECIMAL_PLACES),   # max 2
        ("background_opacity", 500, DEFAULTS.DEFAULT_BACKGROUND_OPACITY),  # max 100
        # Invalid choice -> default.
        ("interface_mode", "bogus_mode", DEFAULTS.DEFAULT_INTERFACE_MODE),
        ("unit_type", "furlongs_per_fortnight", DEFAULTS.DEFAULT_UNIT_TYPE),
        # Wrong type -> default.
        ("color_coding", "yes", DEFAULTS.DEFAULT_COLOR_CODING),
        ("font_size", "big", DEFAULTS.DEFAULT_FONT_SIZE),
        # Bad regex (color must be #RRGGBB) -> default.
        ("default_color", "not-a-color", DEFAULTS.DEFAULT_COLOR),
    ],
)
def test_invalid_value_reset_to_default(tmp_path, key, bad_value, expected_default):
    """
    The validator does NOT clamp out-of-range / invalid values; it discards them
    and substitutes the schema default. Pin that exact behavior across the
    different rule types (range, choice, type, regex).
    """
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(json.dumps({key: bad_value}), encoding="utf-8")

    loaded = ConfigManager(path).load()
    assert loaded[key] == expected_default


def test_valid_boundary_values_survive(tmp_path):
    """
    The min/max boundaries themselves are *valid* (the check is strict <, >),
    so exact boundary values must round-trip unchanged. Guards against the test
    above accidentally implying all extremes are rejected.
    """
    path = tmp_path / "NetSpeedTray_Config.json"
    # font_size [7, 11]; decimal_places [0, 2]; background_opacity [0, 100]
    path.write_text(
        json.dumps(
            {
                "font_size": 7,
                "decimal_places": 2,
                "background_opacity": 100,
            }
        ),
        encoding="utf-8",
    )

    loaded = ConfigManager(path).load()
    assert loaded["font_size"] == 7
    assert loaded["decimal_places"] == 2
    assert loaded["background_opacity"] == 100


# --- cross-field threshold invariant -----------------------------------------

def test_swapped_thresholds_reset_both_to_defaults(tmp_path):
    """
    high must be strictly greater than low. When the file violates that (here
    low > high), the validator resets BOTH thresholds to their defaults so color
    coding stays functional.
    """
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(
        json.dumps({"high_speed_threshold": 2.0, "low_speed_threshold": 8.0}),
        encoding="utf-8",
    )

    loaded = ConfigManager(path).load()
    assert loaded["high_speed_threshold"] == DEFAULTS.DEFAULT_HIGH_SPEED_THRESHOLD
    assert loaded["low_speed_threshold"] == DEFAULTS.DEFAULT_LOW_SPEED_THRESHOLD


def test_equal_thresholds_reset_both_to_defaults(tmp_path):
    """Equal thresholds violate the strict ``low < high`` rule -> both reset."""
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(
        json.dumps({"high_speed_threshold": 5.0, "low_speed_threshold": 5.0}),
        encoding="utf-8",
    )

    loaded = ConfigManager(path).load()
    assert loaded["high_speed_threshold"] == DEFAULTS.DEFAULT_HIGH_SPEED_THRESHOLD
    assert loaded["low_speed_threshold"] == DEFAULTS.DEFAULT_LOW_SPEED_THRESHOLD


# --- missing / corrupt file --------------------------------------------------

def test_load_missing_file_yields_defaults_and_creates_it(tmp_path):
    """
    Loading when no config file exists must not crash: it returns the full
    defaults and (via reset_to_defaults -> save) materializes the file.
    """
    path = tmp_path / "does_not_exist.json"
    assert not path.exists()

    cm = ConfigManager(path)
    loaded = cm.load()

    assert loaded["font_size"] == DEFAULTS.DEFAULT_FONT_SIZE
    assert loaded["interface_mode"] == DEFAULTS.DEFAULT_INTERFACE_MODE
    # The defaults were persisted as a side effect.
    assert path.exists()


def test_load_corrupt_json_backs_up_and_returns_defaults(tmp_path):
    """
    A non-JSON file is treated as corrupt: it is moved aside to ``*.corrupt``
    and defaults are returned (and re-saved) rather than raising.
    """
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text("{ this is not valid json ", encoding="utf-8")

    cm = ConfigManager(path)
    loaded = cm.load()

    # Defaults returned, no exception.
    assert loaded["font_size"] == DEFAULTS.DEFAULT_FONT_SIZE
    # Corrupt original was preserved next to the config.
    corrupt = path.with_name(f"{path.name}.corrupt")
    assert corrupt.exists()
    # And a fresh, valid default config was written back.
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["config_version"] == DEFAULTS.CONFIG_SCHEMA_VERSION


# --- modify -> save -> reload (full edit cycle) ------------------------------

def test_edit_then_reload_persists_change_across_managers(tmp_path):
    """
    Simulate a real settings edit: load defaults, change a value, save, then a
    brand-new manager reads the changed value back.
    """
    path = tmp_path / "NetSpeedTray_Config.json"

    cm = ConfigManager(path)
    cfg = cm.load()                  # creates file with defaults
    assert cfg["dark_mode"] == DEFAULTS.DEFAULT_DARK_MODE

    cfg["dark_mode"] = not DEFAULTS.DEFAULT_DARK_MODE
    cfg["font_size"] = 8
    cm.save(cfg)

    reloaded = ConfigManager(path).load()
    assert reloaded["dark_mode"] == (not DEFAULTS.DEFAULT_DARK_MODE)
    assert reloaded["font_size"] == 8
