"""
StatsExporter - the Monitor's honest "export key figures" writer.

Two files from one action, scoped to a [start, end] window (the design panel's unanimous shape):
  • SUMMARY (one row per metric × interface) - the "argument": pasted into an ISP ticket / RMM pivot.
  • RAW time-series (long format: timestamp, metric, value) - the "proof": loads into pandas/Excel,
    lets anyone recompute any percentile.
Both as CSV (UTF-8, dot decimal regardless of UI locale) with a JSON sidecar of the summary.

The honesty spine (non-negotiable): percentiles/min/stddev are EXACT only inside the raw tier; beyond
that they are written as empty with a `summary_method_note` saying so - never a fabricated p95. Every
row carries sample_count + coverage_pct. Units are human/standard at the boundary: Mbps, ms, W, °C, %.
Temperatures stay °C whatever the display unit (temperature_unit, rc.2 F8): an export is
machine-readable data whose `unit` column says °C, so files from different settings stay comparable
(and stddev, unlike the other figures, does not survive a plain °F offset).
No phone-home - bytes leave only on the user's explicit action; this module just writes local files.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from netspeedtray import constants

_LOSS_SERIES_FOR: Dict[str, str] = dict(constants.network.latency.LOSS_SERIES_FOR)

# (key, kind, unit, label). kind: "net_down"/"net_up" read via summarize_network; "hw" via summarize_hardware.
_METRICS: List[Tuple[str, str, str, str]] = [
    ("download", "net_down", "Mbps", "Download"),
    ("upload", "net_up", "Mbps", "Upload"),
    ("latency_gw", "hw", "ms", "Gateway latency"),
    ("latency_anchor", "hw", "ms", "Internet latency"),
    ("cpu", "hw", "%", "CPU"),
    ("gpu", "hw", "%", "GPU"),
    ("ram", "hw", "%", "RAM"),
    ("cpu_temp", "hw", "°C", "CPU temperature"),
    ("gpu_temp", "hw", "°C", "GPU temperature"),
    ("cpu_power", "hw", "W", "CPU power"),
    ("gpu_power", "hw", "W", "GPU power"),
    ("total_power", "hw", "W", "CPU+GPU power"),
    ("system_power", "hw", "W", "System power"),
]

_SUMMARY_FIELDS = [
    "window_start_iso", "window_end_iso", "window_label", "metric", "unit", "interface",
    "tier_resolution", "sample_count", "coverage_pct",
    "avg", "min", "max", "p50", "p95", "p99", "stddev", "loss_pct",
    "machine_id", "app_version", "summary_method_note",
]


# The time-series file. "aggregation" says what each value IS (M8): "sample" = one stored reading;
# "minute_max" / "hour_max" = the peak of a stored network rollup bucket (the download/upload rollups
# keep per-bucket maxima; across all interfaces it is the sum of each one's peak, so an upper bound
# on the combined peak); "minute_avg" / "hour_avg" = a hardware bucket's mean. For windows past
# the raw tier the file used to hold minute peaks under a bare "value" header - with zero rows
# padded onto both window edges - beside a summary computed from raw samples. A bucket that starts
# before the window but holds rows inside it (the first minute / hour of the window) is kept, with
# its timestamp clipped to the window start; its value comes from in-window rows only.
_RAW_FIELDS = ["timestamp_unix", "timestamp_iso", "metric", "value", "aggregation"]


def _ts(ts) -> int:
    return int(ts.timestamp()) if isinstance(ts, datetime) else int(ts)


def _net_resolution(summary_tier: Optional[str], start: datetime, end: datetime) -> Tuple[str, str]:
    """(read resolution, aggregation label) for a network metric: the raw samples when the window's
    summary was computed from them, else the graph's rollup tier for that window length."""
    if summary_tier == "raw":
        return "raw", "sample"
    if summary_tier in ("minute", "hour"):
        return summary_tier, f"{summary_tier}_max"   # the same tier the summary was computed from
    res = constants.data.history_period.get_target_resolution(start, end)
    if res == "raw":
        return "raw", "sample"
    return res, f"{res}_max"


def _hw_aggregation(start: datetime, end: datetime) -> str:
    """The hardware reader bins by window length (raw <= 6 h, else minute / hour buckets averaged)."""
    secs = (end - start).total_seconds()
    if secs <= constants.data.history_period.RES_RAW_THRESHOLD:
        return "sample"
    return "minute_avg" if secs <= 30 * 86400 else "hour_avg"


def _net_rows(widget_state, start: datetime, end: datetime, interface: Optional[str], res: str):
    """Stored network rows for [start, end] at ``res``, WITHOUT the reader's graph padding. The reader
    reads only rows inside the window (so even the first bucket's value is in-window), then pads a
    bounded window with zero rows at its edges for the graph's gap detection. The history never
    stores an all-zero row (the v8 writer skips them), so an all-zero row here is that padding."""
    rows = widget_state.get_speed_history(start, end, interface, resolution=res, wait_for_flush=False)
    return [r for r in rows if (r[1] or r[2])]


def _in_window(rows, start: datetime, end: datetime):
    """(timestamp, row) for rows whose bucket overlaps [start, end]. The reader bins by floor, so the
    window's first bucket can start before the window (e.g. 10:00 for a 10:37 start); it holds only
    in-window rows and is kept, its timestamp clipped to the window start (review item 8)."""
    lo, hi = int(start.timestamp()), int(end.timestamp())
    for row in rows:
        tsec = _ts(row[0])
        if tsec <= hi:
            yield max(tsec, lo), row


def _bps_to_mbps(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v) * 8.0 / 1_000_000.0, 3)


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 2)


def export_window(widget_state, start: datetime, end: datetime, window_label: str,
                  out_dir: str, basename: str, machine_id: str = "", app_version: str = "",
                  interface: Optional[str] = None, poll_interval: float = 1.0) -> Dict[str, str]:
    """Write {basename}.summary.csv / .raw.csv / .summary.json into out_dir. Returns the paths written."""
    os.makedirs(out_dir, exist_ok=True)
    iface_label = interface or "all"

    summary_rows: List[Dict[str, Any]] = []
    raw_rows: List[Tuple[int, str, str, Any, str]] = []   # (ts_unix, iso, metric, value, aggregation)

    for key, kind, unit, _label in _METRICS:
        if kind == "net_down":
            s = widget_state.summarize_network("download", start, end, interface, poll_interval)
            conv = _bps_to_mbps
        elif kind == "net_up":
            s = widget_state.summarize_network("upload", start, end, interface, poll_interval)
            conv = _bps_to_mbps
        else:
            s = widget_state.summarize_hardware(key, start, end, poll_interval)
            conv = _r
        # A latency hop's loss comes from its own timeout series (gateway or internet anchor). It is
        # read before the empty-row check: a hop that lost EVERY probe in the window has no RTT
        # samples at all, and that is exactly the row an ISP complaint needs.
        loss = None
        loss_series = _LOSS_SERIES_FOR.get(key)
        if loss_series:
            ls = widget_state.summarize_hardware(loss_series, start, end, poll_interval)
            loss = round((ls.avg or 0.0) * 100.0, 2) if ls.count else None

        if s.count == 0 and loss is None:
            continue   # nothing recorded for this metric in the window - omit, don't emit zeros

        summary_rows.append({
            "window_start_iso": start.isoformat(), "window_end_iso": end.isoformat(),
            "window_label": window_label, "metric": key, "unit": unit, "interface": iface_label,
            "tier_resolution": s.tier, "sample_count": s.count, "coverage_pct": s.coverage_pct,
            "avg": conv(s.avg), "min": conv(s.min), "max": conv(s.max), "p50": conv(s.p50),
            "p95": conv(s.p95), "p99": conv(s.p99), "stddev": conv(s.stddev), "loss_pct": loss,
            "machine_id": machine_id, "app_version": app_version, "summary_method_note": s.note,
        })

        # The time series (long format) for the same window - stored tier rows only (M8).
        if kind in ("net_down", "net_up"):
            res, agg = _net_resolution(s.tier, start, end)
            idx = 2 if kind == "net_down" else 1   # (ts, up, dn)
            for tsec, row in _in_window(_net_rows(widget_state, start, end, interface, res), start, end):
                raw_rows.append((tsec, datetime.fromtimestamp(tsec).isoformat(), key,
                                 _bps_to_mbps(row[idx]), agg))
        else:
            agg = _hw_aggregation(start, end)
            for tsec, (_ts, val) in _in_window(widget_state.get_hardware_history(key, start, end), start, end):
                raw_rows.append((tsec, datetime.fromtimestamp(tsec).isoformat(), key, _r(val), agg))

    paths: Dict[str, str] = {}
    sp = os.path.join(out_dir, f"{basename}.summary.csv")
    with open(sp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_SUMMARY_FIELDS)
        w.writeheader()
        for r in summary_rows:
            w.writerow(r)
    paths["summary_csv"] = sp

    jp = os.path.join(out_dir, f"{basename}.summary.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump({"generated_iso": end.isoformat(), "machine_id": machine_id,
                   "app_version": app_version, "rows": summary_rows}, f, ensure_ascii=False, indent=2)
    paths["summary_json"] = jp

    rp = os.path.join(out_dir, f"{basename}.raw.csv")
    raw_rows.sort(key=lambda r: (r[0], r[2]))
    with open(rp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_RAW_FIELDS)
        w.writerows(raw_rows)
    paths["raw_csv"] = rp

    return paths


def export_window_zip(widget_state, start: datetime, end: datetime, window_label: str,
                      zip_path: str, basename: str, machine_id: str = "", app_version: str = "",
                      interface: Optional[str] = None, poll_interval: float = 1.0) -> str:
    """Write the same summary/raw/json export as :func:`export_window`, but bundle the three files into a
    single ``.zip`` at ``zip_path`` (one tidy archive instead of three loose files in a folder). Returns
    the zip path. The files are built in a temp dir and deflated in; the temp dir is always cleaned up."""
    tmp = tempfile.mkdtemp(prefix="nst_export_")
    try:
        paths = export_window(widget_state, start, end, window_label, tmp, basename,
                              machine_id=machine_id, app_version=app_version,
                              interface=interface, poll_interval=poll_interval)
        parent = os.path.dirname(zip_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths.values():
                zf.write(p, arcname=os.path.basename(p))
        return zip_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
