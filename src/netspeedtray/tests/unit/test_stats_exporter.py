"""
StatsExporter - the Monitor's "export key figures" writer. Verifies the two-file shape (summary +
raw), the JSON sidecar, unit conversion at the boundary (bytes/sec -> Mbps), and the honesty spine:
rollup-tier metrics write avg+max with blank percentiles and a method note, never a fabricated p95.
"""
import csv
import json
import os
from datetime import datetime, timedelta

import pytest

from netspeedtray.utils import stats_exporter as SE
from netspeedtray.utils.summaries import summarize_raw, summarize_rollup


class _WS:
    """Stand-in WidgetState: download is raw (exact), cpu is a rollup (avg+max only), upload empty."""
    def __init__(self):
        self._t0 = datetime(2026, 6, 28, 12, 0, 0)

    def summarize_network(self, direction, start, end, iface, poll):
        if direction == "download":
            return summarize_raw([10_000_000.0, 20_000_000.0, 30_000_000.0])   # bytes/sec
        return summarize_raw([])   # upload: nothing recorded -> omitted

    def summarize_hardware(self, stat, start, end, poll):
        if stat == "cpu":
            return summarize_rollup([20.0, 40.0], [55.0, 80.0], [60, 60], tier="minute")
        if stat == "latency_gw":
            return summarize_raw([5.0, 9.0, 7.0])
        if stat == "latency_gw_timeout":
            return summarize_raw([0.0, 1.0, 0.0])   # 1/3 timed out
        return summarize_raw([])

    def get_speed_history(self, start, end, iface, resolution='auto'):
        return [(self._t0, 1_000_000.0, 10_000_000.0), (self._t0 + timedelta(seconds=1), 2_000_000.0, 20_000_000.0)]

    def get_hardware_history(self, stat, start, end):
        if stat == "cpu":
            return [(self._t0, 33.0), (self._t0 + timedelta(seconds=1), 44.0)]
        if stat == "latency_gw":
            return [(self._t0, 7.0)]
        return []


@pytest.fixture
def out(tmp_path):
    return str(tmp_path)


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_writes_three_files(out):
    paths = SE.export_window(_WS(), datetime(2026, 6, 28, 11), datetime(2026, 6, 28, 12),
                             "Last hour", out, "nst_test", machine_id="M1", app_version="2.0.0")
    for k in ("summary_csv", "summary_json", "raw_csv"):
        assert os.path.exists(paths[k])


def test_zip_bundles_the_three_files(out):
    """The interactive export ships ONE .zip containing the summary/raw/json (not three loose files)."""
    import zipfile
    zip_path = os.path.join(out, "nst_export.zip")
    res = SE.export_window_zip(_WS(), datetime(2026, 6, 28, 11), datetime(2026, 6, 28, 12),
                              "Last hour", zip_path, "nst_test", machine_id="M1", app_version="2.0.0")
    assert res == zip_path and os.path.exists(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert {"nst_test.summary.csv", "nst_test.raw.csv", "nst_test.summary.json"} == set(names)


def test_download_converted_to_mbps_and_exact(out):
    paths = SE.export_window(_WS(), datetime(2026, 6, 28, 11), datetime(2026, 6, 28, 12),
                             "Last hour", out, "nst", machine_id="M1", app_version="2.0.0")
    rows = {r["metric"]: r for r in _rows(paths["summary_csv"])}
    dl = rows["download"]
    assert dl["unit"] == "Mbps"
    assert float(dl["avg"]) == pytest.approx(160.0)     # mean(10,20,30) MB/s * 8 = 160 Mbps
    assert float(dl["max"]) == pytest.approx(240.0)     # 30 MB/s * 8
    assert dl["tier_resolution"] == "raw"
    assert dl["p95"] != ""                              # exact percentiles present in raw tier


def test_rollup_metric_has_blank_percentiles_and_note(out):
    paths = SE.export_window(_WS(), datetime(2026, 6, 25), datetime(2026, 6, 28),
                             "Last 3 days", out, "nst", machine_id="M1", app_version="2.0.0")
    rows = {r["metric"]: r for r in _rows(paths["summary_csv"])}
    cpu = rows["cpu"]
    assert cpu["tier_resolution"] == "minute"
    assert cpu["p50"] == "" and cpu["p95"] == "" and cpu["p99"] == "" and cpu["stddev"] == ""
    assert float(cpu["max"]) == 80.0
    assert "rollup" in cpu["summary_method_note"] or "percentile" in cpu["summary_method_note"]


def test_empty_metric_is_omitted(out):
    paths = SE.export_window(_WS(), datetime(2026, 6, 28, 11), datetime(2026, 6, 28, 12),
                             "Last hour", out, "nst")
    metrics = {r["metric"] for r in _rows(paths["summary_csv"])}
    assert "upload" not in metrics      # no samples -> not a row of zeros
    assert "download" in metrics


def test_latency_loss_pct(out):
    paths = SE.export_window(_WS(), datetime(2026, 6, 28, 11), datetime(2026, 6, 28, 12),
                             "Last hour", out, "nst")
    rows = {r["metric"]: r for r in _rows(paths["summary_csv"])}
    assert float(rows["latency_gw"]["loss_pct"]) == pytest.approx(33.33, abs=0.1)


def test_json_sidecar_matches(out):
    paths = SE.export_window(_WS(), datetime(2026, 6, 28, 11), datetime(2026, 6, 28, 12),
                             "Last hour", out, "nst", machine_id="M1", app_version="2.0.0")
    with open(paths["summary_json"], encoding="utf-8") as f:
        data = json.load(f)
    assert data["machine_id"] == "M1" and data["app_version"] == "2.0.0"
    metrics = {r["metric"] for r in data["rows"]}
    assert "download" in metrics


def test_raw_is_long_format_sorted(out):
    paths = SE.export_window(_WS(), datetime(2026, 6, 28, 11), datetime(2026, 6, 28, 12),
                             "Last hour", out, "nst")
    rows = _rows(paths["raw_csv"])
    assert set(rows[0].keys()) == {"timestamp_unix", "timestamp_iso", "metric", "value"}
    ts = [int(r["timestamp_unix"]) for r in rows]
    assert ts == sorted(ts)
    # download raw is in Mbps too (10 MB/s -> 80 Mbps)
    dl = [r for r in rows if r["metric"] == "download"]
    assert float(dl[0]["value"]) == pytest.approx(80.0)
