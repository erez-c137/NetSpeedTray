"""
Build the metrics endpoint's immutable snapshot from the live app state (2.2 scope B).

GUI THREAD ONLY. This is the one place that reads the widget, the controller and WidgetState for
the endpoint; it runs in the stats_ready slot, and the result is a frozen ``MetricsSnapshot`` the
server thread can read without touching any of those objects (see ``core/metrics_endpoint.py``).

Honesty rules (release plan D5 and scope B):
  * volume comes from the odometer only (WidgetState.get_usage_this_period / get_usage_lifetime) -
    never from the history tiers' rate x interval reconstruction;
  * a hardware value is exported only while the user monitors it - the endpoint never turns polling
    on, and a value the open Monitor window forced is not exported with the switch off;
  * the public-anchor latency is exported only while the public probe is enabled;
  * a missing reading is an absent series, never a zero.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from netspeedtray import constants
from netspeedtray.core.metrics_endpoint import LatencyHop, MetricsSnapshot, Rates

logger = logging.getLogger("NetSpeedTray.MetricsSnapshot")

_GIB = 1024 ** 3        # the monitor thread reports RAM/VRAM in GiB
_CAP_GB = 1000 ** 3     # the data cap is decimal GB everywhere else in the app (usage_alerts, hover card)


def _finite(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _period_start_ts(period_key: str) -> Optional[float]:
    try:
        d = date.fromisoformat(str(period_key))
    except (TypeError, ValueError):
        return None
    return datetime(d.year, d.month, d.day).timestamp()   # local midnight


def _rates(controller: Any) -> Tuple[Optional[Rates], Tuple[Tuple[str, Rates], ...]]:
    live = getattr(controller, "live_rates", None)
    if not live:
        return None, ()
    per_display, per_measured, (agg_up, agg_down), (m_up, m_down) = live
    agg = Rates(rx=float(agg_down), tx=float(agg_up), measured_rx=float(m_down), measured_tx=float(m_up))
    try:
        monitored = controller.monitored_interface_names(per_display)
    except Exception:
        monitored = set()
    rows = []
    for name in sorted(per_display):
        if name not in monitored:
            continue
        up, down = per_display[name]
        mu, md = per_measured.get(name, (up, down))
        rows.append((str(name), Rates(rx=float(down), tx=float(up), measured_rx=float(md), measured_tx=float(mu))))
    return agg, tuple(rows)


def _latency(widget: Any, config: Dict[str, Any]) -> Tuple[LatencyHop, ...]:
    if not config.get("latency_enabled", True) or getattr(widget, "latency_probe", None) is None:
        return ()
    from netspeedtray.core.latency_probe import HOP_ANCHOR, HOP_GATEWAY
    windows = getattr(widget, "_latency_loss_windows", None)
    if windows is None:
        return ()

    def hop(name: str, loss_pct: Optional[float]) -> LatencyHop:
        loss = _finite(loss_pct)
        return LatencyHop(name, _finite(windows.last_ms(name)), None if loss is None else loss / 100.0)

    hops = [hop(HOP_GATEWAY, windows.gw_loss)]
    if config.get("latency_public_enabled", False):
        hops.append(hop(HOP_ANCHOR, windows.anchor_loss))
    return tuple(h for h in hops if h.rtt_ms is not None or h.loss_ratio is not None)


def build_snapshot(widget: Any, now: Optional[float] = None) -> MetricsSnapshot:
    """Read the live state into a frozen snapshot. Never raises - a section that fails is left out."""
    config: Dict[str, Any] = dict(getattr(widget, "config", None) or {})
    controller = getattr(widget, "controller", None)
    ws = getattr(widget, "widget_state", None)
    fields: Dict[str, Any] = {"version": constants.app.VERSION,
                              "taken_at": time.time() if now is None else now,
                              "interface_mode": str(config.get("interface_mode", "auto"))}

    if controller is not None:
        try:
            fields["aggregate"], fields["interfaces"] = _rates(controller)
        except Exception as e:
            logger.debug("metrics snapshot: rates unavailable: %s", e)

    if ws is not None:
        try:
            # get_usage_lifetime is None until the odometer has loaded from the DB, while
            # get_usage_this_period returns (0, 0) then: exporting that 0 would make Prometheus'
            # increase()/rate() count the whole period as new traffic once the real total arrives.
            lifetime = ws.get_usage_lifetime()
            if lifetime is not None:
                fields["lifetime_bytes"] = (float(lifetime[1]), float(lifetime[0]))
                up, down = ws.get_usage_this_period()
                fields["period_bytes"] = (float(down), float(up))
                fields["period_start"] = _period_start_ts(ws.get_usage_period_key())
        except Exception as e:
            logger.debug("metrics snapshot: odometer unavailable: %s", e)

    cap_gb = _finite(config.get("data_cap_gb", 0)) or 0.0
    if config.get("data_cap_enabled") and cap_gb > 0:
        fields["data_cap_bytes"] = cap_gb * _CAP_GB
        fields["data_cap_counts"] = str(config.get("data_cap_count", "total"))

    try:
        fields["latency"] = _latency(widget, config)
    except Exception as e:
        logger.debug("metrics snapshot: latency unavailable: %s", e)

    cpu_on = bool(config.get("monitor_cpu_enabled", False))
    gpu_on = bool(config.get("monitor_gpu_enabled", False))
    if cpu_on:
        cpu = _finite(getattr(widget, "cpu_usage", None))
        fields["cpu_ratio"] = None if cpu is None else cpu / 100.0
        if config.get("monitor_ram_enabled", False):
            used, total = _finite(getattr(widget, "ram_used", None)), _finite(getattr(widget, "ram_total", None))
            if used is not None and total and total > 0:
                fields["ram_used_bytes"], fields["ram_total_bytes"] = used * _GIB, total * _GIB
    if gpu_on:
        gpu = _finite(getattr(widget, "gpu_usage", None))
        fields["gpu_ratio"] = None if gpu is None else gpu / 100.0
        if config.get("monitor_vram_enabled", False):
            used, total = _finite(getattr(widget, "vram_used", None)), _finite(getattr(widget, "vram_total", None))
            if used is not None:
                fields["vram_used_bytes"] = used * _GIB
            if total is not None and total > 0:
                fields["vram_total_bytes"] = total * _GIB
            if getattr(widget, "vram_is_shared", False) is True:
                fields["vram_kind"] = "shared"

    return MetricsSnapshot(**fields)
