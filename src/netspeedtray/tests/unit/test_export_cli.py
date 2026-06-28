"""
Headless export CLI — the `--export-csv` arg path. Covers the pure logic: the flag gates the whole
feature (absent -> None so the GUI proceeds), the friendly period tokens map onto real PERIOD_MAP
keys, and an unknown period is rejected with a non-zero code rather than silently exporting nothing.
"""
from netspeedtray.utils import export_cli as CLI
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
