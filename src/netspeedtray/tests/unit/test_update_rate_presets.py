"""
2.2 scope C: SMART mode removal - the update rate is one of four fixed presets, 1 / 2 / 5 / 10 s.

SMART was never adaptive: it was the sentinel ``update_rate = -1.0`` and it sampled at a fixed
2.0 s (the deleted ``SMART_MODE_INTERVAL_MS = 2000``). Its removal has three halves that must land
together, or one of them leaves a live bug:

* the Settings slider renumbered to four positions (0..3 = 1/2/5/10 s);
* a config migration: any persisted non-positive rate (SMART's -1.0, the historical 0 sentinel)
  becomes **2.0** - the cadence that user actually had - and any other off-preset rate snaps to
  the nearest preset (ties go to the faster one, like the keep_data ladder snap). Reading 10 s
  back must stay 10 s - the "10 -> 5 s clamp trap" the plan warns about;
* every ``<= 0`` sentinel idiom deleted, so the resolvers are pure clamps.
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath("src"))
from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode
from netspeedtray.utils.config import ConfigManager
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds, snap_update_rate

PRESETS = (1.0, 2.0, 5.0, 10.0)
SRC = Path(__file__).resolve().parents[2]          # src/netspeedtray


# --- the preset ladder ------------------------------------------------------------------------

def test_presets_are_one_two_five_ten_seconds():
    assert tuple(UpdateMode.PRESETS) == PRESETS
    assert (UpdateMode.AGGRESSIVE, UpdateMode.BALANCED, UpdateMode.EFFICIENT,
            UpdateMode.POWER_SAVER) == PRESETS
    assert not hasattr(UpdateMode, "SMART")
    assert not hasattr(constants.timers, "SMART_MODE_INTERVAL_MS")


def test_default_rate_is_a_preset():
    assert constants.config.defaults.DEFAULT_UPDATE_RATE in PRESETS


# --- the migration ----------------------------------------------------------------------------

MIGRATION_TABLE = [
    # (persisted value, migrated value, why)
    (-1.0, 2.0, "SMART sentinel -> its real 2.0 s cadence"),
    (-1, 2.0, "SMART written as an int"),
    (0, 2.0, "historical 0 sentinel also sampled at 2.0 s"),
    (0.0, 2.0, "historical 0 sentinel (float)"),
    (-0.5, 2.0, "schema-valid negative under the old min of -1.0"),
    (-7.0, 2.0, "any negative"),
    (1.0, 1.0, "slider 1 s"),
    (2.0, 2.0, "slider 2 s"),
    (5.0, 5.0, "slider 5 s"),
    (10.0, 10.0, "slider 10 s - must NOT clamp to 5 s"),
    (1, 1.0, "int preset"),
    (10, 10.0, "int preset"),
    (0.1, 1.0, "old MINIMUM_UPDATE_RATE floor, hand-edited"),
    (0.5, 1.0, "sub-second hand edit snaps up to the 1 s floor"),
    (1.5, 1.0, "tie between 1 and 2 -> the faster preset"),
    (3.0, 2.0, "nearest"),
    (3.5, 2.0, "tie between 2 and 5 -> the faster preset"),
    (4.0, 5.0, "nearest"),
    (7.5, 5.0, "tie between 5 and 10 -> the faster preset"),
    (8.0, 10.0, "nearest"),
    (15.0, 10.0, "above the old max: nearest is 10 s (was: reset to 1 s)"),
]


@pytest.fixture
def cm(tmp_path):
    return ConfigManager(tmp_path / "NetSpeedTray_Config.json")


@pytest.mark.parametrize("persisted, expected, why", MIGRATION_TABLE)
def test_migration_table(cm, persisted, expected, why):
    migrated = cm._migrate_config({"config_version": "1.1", "update_rate": persisted})
    assert migrated["update_rate"] == expected, why
    assert isinstance(migrated["update_rate"], float)


@pytest.mark.parametrize("persisted, expected, why", MIGRATION_TABLE)
def test_migration_is_idempotent(cm, persisted, expected, why):
    once = cm._migrate_config({"config_version": "1.1", "update_rate": persisted})
    twice = cm._migrate_config(dict(once))
    assert twice["update_rate"] == once["update_rate"] == expected


@pytest.mark.parametrize("garbage", [
    float("nan"), float("inf"), float("-inf"), "2", "fast", None, True, False, [2.0], {"s": 2},
])
def test_malformed_rate_loads_as_the_default(tmp_path, garbage):
    """Malformed values (non-finite, wrong type, bool) never reach the sampler: they load as the
    default 1 s. NaN in particular sails through min/max checks (every comparison is False)."""
    path = tmp_path / "NetSpeedTray_Config.json"
    # json.dumps writes NaN/Infinity tokens, which json.load (and so ConfigManager.load) accepts.
    path.write_text(json.dumps({"config_version": "1.1", "update_rate": garbage}), encoding="utf-8")
    loaded = ConfigManager(path).load()
    assert loaded["update_rate"] == constants.config.defaults.DEFAULT_UPDATE_RATE
    assert type(loaded["update_rate"]) is float


def test_missing_rate_loads_as_the_default(tmp_path):
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(json.dumps({"config_version": "1.1", "font_size": 9}), encoding="utf-8")
    assert ConfigManager(path).load()["update_rate"] == constants.config.defaults.DEFAULT_UPDATE_RATE


def _a_2_1_7_config(update_rate):
    """The shape a 2.1.7 install writes: schema 1.1, every key present, SMART stored as -1.0."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg = {k: v for k, v in cfg.items() if v is not None}
    cfg.update({"config_version": "1.1", "update_rate": update_rate,
                "speed_display_mode": "always_mbps"})   # SMART required Force-MB on in 2.1.7
    return cfg


@pytest.mark.parametrize("persisted, expected", [(-1.0, 2.0), (1.0, 1.0), (2.0, 2.0),
                                                 (5.0, 5.0), (10.0, 10.0)])
def test_2_1_7_config_full_load_save_reload(tmp_path, persisted, expected):
    """A real 2.1.7 file at each old slider position (SMART, 1, 2, 5, 10 s): load -> save -> reload
    keeps the right rate, and the file on disk ends up with it."""
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(json.dumps(_a_2_1_7_config(persisted), indent=4), encoding="utf-8")

    first = ConfigManager(path)
    loaded = first.load()
    assert loaded["update_rate"] == expected
    loaded["font_size"] = loaded["font_size"] + 1       # force a real (non-shortcut) save
    first.save(loaded)
    assert json.loads(path.read_text(encoding="utf-8"))["update_rate"] == expected

    assert ConfigManager(path).load()["update_rate"] == expected


def test_validator_refuses_a_sentinel_at_save(tmp_path):
    """Belt and braces: the schema allows only presets now, so a stray -1.0 handed to save() lands
    on disk as the default, never as a sentinel."""
    path = tmp_path / "NetSpeedTray_Config.json"
    cm = ConfigManager(path)
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg["update_rate"] = -1.0
    cm.save(cfg)
    assert json.loads(path.read_text(encoding="utf-8"))["update_rate"] == \
        constants.config.defaults.DEFAULT_UPDATE_RATE


# --- the shared snap and the resolver ---------------------------------------------------------

@pytest.mark.parametrize("persisted, expected, why", MIGRATION_TABLE)
def test_snap_update_rate_matches_the_migration(persisted, expected, why):
    assert snap_update_rate(persisted) == expected, why


@pytest.mark.parametrize("rate", PRESETS)
def test_resolver_passes_presets_through(rate):
    assert resolve_poll_interval_seconds(rate) == rate


@pytest.mark.parametrize("rate, expected", [(0.5, 1.0), (0.0, 1.0), (-1.0, 1.0), (3.0, 3.0),
                                            (12.0, 10.0)])
def test_resolver_is_a_pure_clamp(rate, expected):
    """No sentinel branch is left: out-of-range values clamp into [1, 10] s. Non-positive values
    are unreachable after load (the migration rewrote them), so they just hit the floor."""
    assert resolve_poll_interval_seconds(rate) == expected


@pytest.mark.parametrize("garbage", [None, "abc", float("nan"), float("inf"), True])
def test_resolver_garbage_is_the_default(garbage):
    assert resolve_poll_interval_seconds(garbage) == constants.config.defaults.DEFAULT_UPDATE_RATE


# --- poll cadence for each slider value -------------------------------------------------------

@pytest.mark.parametrize("rate", PRESETS)
def test_settings_save_sets_the_sampler_to_each_preset(rate):
    from netspeedtray.core.config_controller import ConfigController
    w = MagicMock()
    w.config = {"update_rate": rate}
    ConfigController(w, MagicMock()).apply_all_settings()
    w.monitor_thread.set_interval.assert_called_once_with(rate)


@pytest.mark.parametrize("rate", PRESETS)
def test_startup_starts_the_sampler_at_each_preset(monkeypatch, rate):
    """`NetworkSpeedWidget._init_core_components` builds the monitor thread at the configured
    preset - and no longer builds a SpeedTimerManager."""
    from netspeedtray.views.widget import main as M
    captured = {}

    class _Thread:
        def __init__(self, interval, config):
            captured["interval"] = interval

    monkeypatch.setattr(M, "StatsMonitorThread", _Thread)
    monkeypatch.setattr(M, "CoreWidgetState", lambda cfg: MagicMock())
    monkeypatch.setattr(M, "CoreController", lambda config, widget_state: MagicMock())
    monkeypatch.setattr(M, "CoreWidgetRenderer", lambda cfg, i18n: MagicMock())
    fake = MagicMock()
    fake.config = {"update_rate": rate}
    M.NetworkSpeedWidget._init_core_components(fake)
    assert captured["interval"] == rate


@pytest.mark.parametrize("rate", PRESETS)
def test_history_buffers_size_from_each_preset(rate):
    """The in-memory deques and the renderer's sample budget divide by the preset directly."""
    from netspeedtray.core.widget_state import WidgetState
    from netspeedtray.utils.widget_renderer import RenderConfig
    fake = MagicMock()
    fake.config = {"update_rate": rate, "history_minutes": 30}
    assert WidgetState._get_max_history_points(fake) == max(10, min(int(round(1800 / rate)), 5000))
    rc = RenderConfig.from_dict({**constants.config.defaults.DEFAULT_CONFIG,
                                 "update_rate": rate, "history_minutes": 30})
    assert rc.max_samples == int(1800 / rate)


# --- the Settings slider ----------------------------------------------------------------------

@pytest.fixture
def general_page(q_app):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.pages.general import GeneralPage
    return GeneralPage(I18nStrings("en_US"), MagicMock())


def test_slider_has_four_positions(general_page):
    slider = general_page.update_rate.slider
    assert (slider.minimum(), slider.maximum()) == (0, 3)


@pytest.mark.parametrize("position, rate", list(enumerate(PRESETS)))
def test_slider_positions_map_to_presets(general_page, position, rate):
    general_page.update_rate.setValue(position)
    assert general_page.get_settings()["update_rate"] == rate


@pytest.mark.parametrize("rate", PRESETS)
def test_each_preset_round_trips_through_the_slider(general_page, rate):
    general_page.load_settings({"update_rate": rate, "language": "en_US"}, is_startup_enabled=False)
    assert general_page.get_settings()["update_rate"] == rate


@pytest.mark.parametrize("rate, expected", [(3.0, 2.0), (4.0, 5.0), (8.0, 10.0), (0.5, 1.0),
                                            (-1.0, 2.0)])
def test_off_preset_rate_snaps_to_nearest_on_the_slider(general_page, rate, expected):
    general_page.load_settings({"update_rate": rate, "language": "en_US"}, is_startup_enabled=False)
    assert general_page.get_settings()["update_rate"] == expected


@pytest.mark.parametrize("position, key", [(0, "UPDATE_MODE_AGGRESSIVE_LABEL"),
                                           (1, "UPDATE_MODE_BALANCED_LABEL"),
                                           (2, "UPDATE_MODE_EFFICIENT_LABEL"),
                                           (3, "UPDATE_MODE_POWER_SAVER_LABEL")])
def test_slider_labels(general_page, position, key):
    assert general_page._format_update_rate_label(position) == getattr(general_page.i18n, key)


def test_force_mb_off_leaves_the_rate_alone(qtbot):
    """The Force-MB coupling is gone: with nothing to forbid, turning Force MB off keeps every rate."""
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.dialog import SettingsDialog
    for rate in PRESETS:
        cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
        cfg.update({"speed_display_mode": "auto", "update_rate": rate})
        m = MagicMock()
        m.config = {}
        m.config_manager = MagicMock()
        dlg = SettingsDialog(main_widget=m, config=cfg, version="2.2.0", i18n=I18nStrings("en_US"),
                             available_interfaces=["Ethernet"], is_startup_enabled=False)
        qtbot.addWidget(dlg)
        dlg.units_page.speed_display_mode.setChecked(True)
        dlg.units_page.speed_display_mode.setChecked(False)
        assert dlg.get_settings()["update_rate"] == rate
        assert not hasattr(dlg, "_on_force_mb_toggled")


# --- nothing of SMART is left -----------------------------------------------------------------

def test_speed_timer_manager_is_deleted():
    assert importlib.util.find_spec("netspeedtray.core.timer_manager") is None


def test_no_locale_carries_a_smart_key():
    for path in sorted((SRC / "constants" / "locales").glob("*.json")):
        keys = json.loads(path.read_text(encoding="utf-8")).keys()
        assert not [k for k in keys if "SMART" in k.upper()], path.name


# The migration in utils/config.py has to name what it migrates; nothing else may.
_SMART_ALLOWED = {"utils/config.py"}
# Upper-case SMART is the mode's name; "smart mode" in any case is its prose. Unrelated uses of the
# word ("Smart Polling", `_set_smart_y_ticks`) are not SMART mode and stay.
_SMART_PATTERNS = (re.compile(r"\bSMART\b|SMART_MODE"), re.compile(r"smart[ _-]?mode", re.IGNORECASE))


def test_no_source_file_references_smart_mode():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("tests/") or rel in _SMART_ALLOWED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(line) for p in _SMART_PATTERNS):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


# ...and the one place the non-positive rule now lives is the shared snap the migration calls.
_SENTINEL_ALLOWED = _SMART_ALLOWED | {"utils/timer_utils.py"}


def test_the_sentinel_idioms_are_gone():
    """`if rate <= 0: rate = 1.0` style guards on update_rate are unreachable after the migration."""
    pattern = re.compile(r"(update_rate|rate)\w*\s*<=?\s*0\b")
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("tests/") or rel in _SENTINEL_ALLOWED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
