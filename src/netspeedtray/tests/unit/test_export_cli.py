"""
Headless export CLI - the `--export-csv` arg path. Covers the pure logic: the flag gates the whole
feature (absent -> None so the GUI proceeds), the friendly period tokens map onto real PERIOD_MAP
keys, and an unknown period is rejected with a non-zero code rather than silently exporting nothing.

Also home of the shared poll-interval normalizer tests (2.1.5 item 9): the CLI is one of the four
sites where the SMART sentinel (-1.0) leaked through `or 1.0` and stamped coverage 0.0% on every
exported row.
"""
import logging

import pytest

from netspeedtray.utils import export_cli as CLI
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds
from netspeedtray import constants


def test_absent_flag_returns_none():
    assert CLI._parse(["--foo", "bar"]) is None
    assert CLI.run_export_cli(["--shutdown"]) is None    # not our flag -> let GUI handle it


def test_parse_defaults_and_overrides():
    ns = CLI._parse(["--export-csv"])
    assert ns.export_csv and ns.period == "24h" and ns.out == "." and ns.interface is None
    ns2 = CLI._parse(["--export-csv", "--period", "week", "--out", "X:/r", "--interface", "Ethernet"])
    assert ns2.period == "week" and ns2.out == "X:/r" and ns2.interface == "Ethernet"


def test_period_tokens_map_to_real_keys():
    valid = set(constants.data.history_period.PERIOD_MAP.values())
    for token, key in CLI._PERIOD_TOKENS.items():
        assert key in valid, f"{token} -> {key} is not a real PERIOD_MAP key"
    # The dropdown's headline windows are all reachable from the CLI.
    for token in ("30m", "1h", "4h", "8h", "12h", "24h", "48h", "week", "month", "all"):
        assert token in CLI._PERIOD_TOKENS


def test_unknown_period_is_rejected(capsys):
    code = CLI.run_export_cli(["--export-csv", "--period", "fortnight"])
    assert code == 2
    assert "Unknown" in capsys.readouterr().err


def test_emit_survives_none_stream(caplog):
    """The shipped exe is built console=False, so sys.stdout/sys.stderr are None. _emit must never raise
    on a None stream (a bare .write would AttributeError) - it logs instead."""
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.ExportCLI"):
        CLI._emit(None, "the/output/path\n")            # would crash with bare .write
    assert any("the/output/path" in r.message for r in caplog.records)

    class _Sink:
        def __init__(self): self.buf = ""
        def write(self, s): self.buf += s
        def flush(self): pass
    sink = _Sink()
    CLI._emit(sink, "hello\n")
    assert sink.buf == "hello\n"                          # a real stream is written through


def test_cli_does_not_crash_when_std_streams_are_none(monkeypatch):
    """Regression: the headless export's success AND error paths must not AttributeError when the
    windowed exe has sys.stdout/sys.stderr == None - the bug that turned a successful export into exit 1."""
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    # The error path (unknown period) reaches the first guarded write - it must return 2, not crash.
    assert CLI.run_export_cli(["--export-csv", "--period", "fortnight"]) == 2


# --- the shared poll-interval normalizer (2.1.5 item 9) ------------------------

def test_resolve_poll_interval_passes_positive_rates_through():
    assert resolve_poll_interval_seconds(1.0) == 1.0
    assert resolve_poll_interval_seconds(5) == 5.0


def test_resolve_poll_interval_smart_sentinel_is_two_seconds():
    # SMART samples at SMART_MODE_INTERVAL_MS (2000 ms), NOT 1 s: the `or 1.0` idiom let the
    # truthy -1.0 sentinel straight through, and normalizing to 1.0 would be wrong by 2x.
    expected = constants.timers.SMART_MODE_INTERVAL_MS / 1000.0
    assert expected == 2.0
    assert resolve_poll_interval_seconds(-1.0) == expected
    assert resolve_poll_interval_seconds(0) == expected      # historical smart sentinel
    assert resolve_poll_interval_seconds(0.0) == expected


def test_resolve_poll_interval_garbage_falls_back_to_smart():
    expected = constants.timers.SMART_MODE_INTERVAL_MS / 1000.0
    assert resolve_poll_interval_seconds(None) == expected
    assert resolve_poll_interval_seconds("abc") == expected
    assert resolve_poll_interval_seconds(float("nan")) == expected
    assert resolve_poll_interval_seconds(float("inf")) == expected


def test_smart_mode_coverage_is_100_percent():
    """A fully-covered SMART-mode hour: 1800 samples at 2.0 s = 100% coverage - not 0.0 (the -1.0
    leak, guarded out by summaries.coverage_pct) and not 50.0 (a wrong 1.0 s normalization)."""
    from netspeedtray.utils.summaries import coverage_pct
    poll = resolve_poll_interval_seconds(-1.0)
    assert coverage_pct(1800, 3600, poll) == pytest.approx(100.0)
    assert coverage_pct(1800, 3600, -1.0) == 0.0    # what the leak used to report
    assert coverage_pct(1800, 3600, 1.0) == 50.0    # what a 1.0 s normalization would report


def test_export_cli_normalizes_smart_poll_interval(monkeypatch, tmp_path):
    """--export-csv with a SMART config must hand stats_exporter a 2.0 s poll interval, not the raw
    -1.0 sentinel (export_cli.py's `or 1.0` was one of the four leak sites)."""
    captured = {}

    class _CM:
        def load(self):
            return {"update_rate": -1.0, "language": "en_US"}

    class _WS:
        def __init__(self, config, read_only=False):
            pass

        def get_earliest_data_timestamp(self):
            return None

        def cleanup(self):
            pass

    def _fake_export(ws, start, end, label, out_dir, basename, *, machine_id="", app_version="",
                     interface=None, poll_interval=1.0):
        captured["poll"] = poll_interval
        return {}

    monkeypatch.setattr("netspeedtray.utils.config.ConfigManager", _CM)
    monkeypatch.setattr("netspeedtray.core.widget_state.WidgetState", _WS)
    monkeypatch.setattr("netspeedtray.utils.stats_exporter.export_window", _fake_export)
    monkeypatch.setattr("netspeedtray.utils.helpers.get_machine_id", lambda: "cafebabe12345678")

    code = CLI.run_export_cli(["--export-csv", "--period", "24h", "--out", str(tmp_path)])
    assert code == 0
    assert captured["poll"] == 2.0
