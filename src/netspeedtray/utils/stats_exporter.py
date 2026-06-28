"""
StatsExporter — the Monitor's honest "export key figures" writer.

Two files from one action, scoped to a [start, end] window (the design panel's unanimous shape):
  • SUMMARY (one row per metric × interface) — the "argument": pasted into an ISP ticket / RMM pivot.
  • RAW time-series (long format: timestamp, metric, value) — the "proof": loads into pandas/Excel,
    lets anyone recompute any percentile.
Both as CSV (UTF-8, dot decimal regardless of UI locale) with a JSON sidecar of the summary.

The honesty spine (non-negotiable): percentiles/min/stddev are EXACT only inside the raw tier; beyond
that they are written as empty with a `summary_method_note` saying so — never a fabricated p95. Every
row carries sample_count + coverage_pct. Units are human/standard at the boundary: Mbps, ms, W, °C, %.
No phone-home — bytes leave only on the user's explicit action; this module just writes local files.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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
    raw_rows: List[Tuple[int, str, str, Any]] = []   # (ts_unix, iso, metric, value)

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
        if s.count == 0:
            continue   # nothing recorded for this metric in the window — omit, don't emit zeros

        loss = None
        if key == "latency_gw":
            ls = widget_state.summarize_hardware("latency_gw_timeout", start, end, poll_interval)
            loss = round((ls.avg or 0.0) * 100.0, 2) if ls.count else None

        summary_rows.append({
            "window_start_iso": start.isoformat(), "window_end_iso": end.isoformat(),
            "window_label": window_label, "metric": key, "unit": unit, "interface": iface_label,
            "tier_resolution": s.tier, "sample_count": s.count, "coverage_pct": s.coverage_pct,
            "avg": conv(s.avg), "min": conv(s.min), "max": conv(s.max), "p50": conv(s.p50),
            "p95": conv(s.p95), "p99": conv(s.p99), "stddev": conv(s.stddev), "loss_pct": loss,
            "machine_id": machine_id, "app_version": app_version, "summary_method_note": s.note,
        })

        # Raw series (long format) for the same window.
        if kind in ("net_down", "net_up"):
            net = widget_state.get_speed_history(start, end, interface, resolution='auto')
            idx = 2 if kind == "net_down" else 1   # (ts, up, dn)
            for row in net:
                ts = row[0]
                tsec = int(ts.timestamp()) if isinstance(ts, datetime) else int(ts)
                raw_rows.append((tsec, datetime.fromtimestamp(tsec).isoformat(), key, _bps_to_mbps(row[idx])))
        else:
            for ts, val in widget_state.get_hardware_history(key, start, end):
                tsec = int(ts.timestamp()) if isinstance(ts, datetime) else int(ts)
                raw_rows.append((tsec, datetime.fromtimestamp(tsec).isoformat(), key, _r(val)))

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
        w.writerow(["timestamp_unix", "timestamp_iso", "metric", "value"])
        w.writerows(raw_rows)
    paths["raw_csv"] = rp

    return paths
