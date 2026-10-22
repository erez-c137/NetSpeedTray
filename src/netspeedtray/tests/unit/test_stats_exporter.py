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

    def get_speed_history(self, start, end, iface, resolution='auto', **_kw):
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
    assert set(rows[0].keys()) == {"timestamp_unix", "timestamp_iso", "metric", "value", "aggregation"}
    ts = [int(r["timestamp_unix"]) for r in rows]
    assert ts == sorted(ts)
    # download raw is in Mbps too (10 MB/s -> 80 Mbps)
    dl = [r for r in rows if r["metric"] == "download"]
    assert float(dl[0]["value"]) == pytest.approx(80.0)


def test_raw_file_has_tier_rows_only_no_edge_padding(out):
    """RC M8: past 6 h the "raw" file held per-bucket PEAKS under a bare "value" header, plus zero rows
    padded onto both window edges, beside a summary computed from raw samples. Now: the summary's own
    tier, every row labelled, and nothing outside the stored rows."""
    start, end = datetime(2026, 6, 27, 12), datetime(2026, 6, 28, 12)   # 24 h
    seen = {}

    class _Padded(_WS):
        def get_speed_history(self, s, e, iface, resolution='auto', **_kw):
            seen["res"] = resolution
            # what the reader does: real rows + zero pads exactly at the REQUESTED edges
            real = [(start + timedelta(hours=1), 1e6, 1e7), (start + timedelta(hours=2), 2e6, 2e7)]
            return [(s, 0.0, 0.0)] + real + [(e, 0.0, 0.0)]

    paths = SE.export_window(_Padded(), start, end, "24 h", out, "nst")
    dl = [r for r in _rows(paths["raw_csv"]) if r["metric"] == "download"]
    assert seen["res"] == "raw"                       # the summary for download is raw-tier exact
    assert [float(r["value"]) for r in dl] == [80.0, 160.0]   # no zero edge rows
    assert {r["aggregation"] for r in dl} == {"sample"}


def test_rollup_window_labels_network_values_as_bucket_peaks(out):
    start, end = datetime(2026, 6, 21), datetime(2026, 6, 28)   # a week: rollup tier

    class _Week(_WS):
        def summarize_network(self, direction, s, e, iface, poll):
            return summarize_rollup([1e6], [5e6], [3600], tier="hour") if direction == "download" \
                else summarize_raw([])

        def get_speed_history(self, s, e, iface, resolution='auto', **_kw):
            return [(start + timedelta(hours=5), 1e6, 5e6)]

    paths = SE.export_window(_Week(), start, end, "Week", out, "nst")
    dl = [r for r in _rows(paths["raw_csv"]) if r["metric"] == "download"]
    assert dl and {r["aggregation"] for r in dl} == {"hour_max"}


def test_first_bucket_straddling_the_window_start_is_kept(out):
    """Review item 8: the reader bins by floor, so a 10:37 window's first hour bucket is labelled
    10:00 - and the time-series file dropped it (and its traffic). Kept, clipped to the window start."""
    start, end = datetime(2026, 6, 21, 10, 37), datetime(2026, 6, 28, 10, 37)

    class _Week(_WS):
        def summarize_network(self, direction, s, e, iface, poll):
            return summarize_rollup([1e6], [5e6], [3600], tier="hour") if direction == "download" \
                else summarize_raw([])

        def get_speed_history(self, s, e, iface, resolution='auto', **_kw):
            first = datetime(2026, 6, 21, 10, 0)       # floor(10:37) - holds 10:37-10:59 rows only
            return [(first, 2e6, 9e6), (first + timedelta(hours=1), 1e6, 4e6), (e, 0.0, 0.0)]   # + end pad

        def get_hardware_history(self, stat, s, e):
            return [(datetime(2026, 6, 21, 10, 0), 50.0), (datetime(2026, 6, 21, 11, 0), 60.0)] \
                if stat == "cpu" else []

    paths = SE.export_window(_Week(), start, end, "Week", out, "nst")
    rows = _rows(paths["raw_csv"])
    dl = [r for r in rows if r["metric"] == "download"]
    lo = str(int(start.timestamp()))
    assert dl[0]["timestamp_unix"] == lo and float(dl[0]["value"]) == pytest.approx(72.0)
    assert all(float(r["value"]) > 0 for r in dl)            # the zero edge pads are gone
    cpu = [r for r in rows if r["metric"] == "cpu"]
    assert cpu[0]["timestamp_unix"] == lo and float(cpu[0]["value"]) == 50.0


def test_export_never_waits_for_the_db_flush(tmp_path):
    """The interactive export runs on the GUI thread: no flush barrier per network metric
    (2.2.0-beta.1 lifecycle review)."""
    calls = []

    class _WSNoWait(_WS):
        def get_speed_history(self, start, end, iface, resolution='auto', wait_for_flush=True, **_kw):
            calls.append(wait_for_flush)
            return super().get_speed_history(start, end, iface, resolution)

    start, end = datetime(2026, 6, 28, 12), datetime(2026, 6, 28, 21)
    SE.export_window_zip(_WSNoWait(), start, end, "w", str(tmp_path / "x.zip"), "x")
    assert calls and not any(calls)
