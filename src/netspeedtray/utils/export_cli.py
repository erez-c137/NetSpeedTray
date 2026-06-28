"""
Headless export CLI — `NetSpeedTray.exe --export-csv --period 24h --out C:\\reports`.

The MSP / power-user path: pull the same honest two-file stats export the Monitor's Stats-detail sheet
writes, without opening any window — schedulable from Task Scheduler / an RMM. It reads the existing
history DB in READ-ONLY mode (no second write thread against the live app, no maintenance/VACUUM), so it
is safe to run while NetSpeedTray is already running.

Returns an exit code when `--export-csv` is present (the caller then exits), or None to let the normal
GUI launch proceed.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import List, Optional

# Friendly period tokens -> PERIOD_MAP timeline keys (a superset of the dropdown's windows).
_PERIOD_TOKENS = {
    "session": "TIMELINE_SESSION",
    "boot": "TIMELINE_SYSTEM_UPTIME", "uptime": "TIMELINE_SYSTEM_UPTIME",
    "30m": "TIMELINE_30_MIN",
    "1h": "TIMELINE_1_HOUR",
    "4h": "TIMELINE_4_HOURS",
    "8h": "TIMELINE_8_HOURS",
    "12h": "TIMELINE_12_HOURS",
    "24h": "TIMELINE_24_HOURS", "1d": "TIMELINE_24_HOURS", "day": "TIMELINE_24_HOURS",
    "48h": "TIMELINE_48_HOURS", "2d": "TIMELINE_48_HOURS",
    "week": "TIMELINE_WEEK", "7d": "TIMELINE_WEEK",
    "month": "TIMELINE_MONTH", "30d": "TIMELINE_MONTH",
    "all": "TIMELINE_ALL",
}


def _parse(argv: List[str]) -> Optional[argparse.Namespace]:
    if "--export-csv" not in argv:
        return None
    p = argparse.ArgumentParser(prog="NetSpeedTray", add_help=False)
    p.add_argument("--export-csv", action="store_true")
    p.add_argument("--period", default="24h",
                   help="session|30m|1h|4h|8h|12h|24h|48h|week|month|all (default 24h)")
    p.add_argument("--out", default=".", help="output folder (default: current directory)")
    p.add_argument("--interface", default=None, help="interface name (default: all)")
    p.add_argument("--basename", default=None, help="override the export file basename")
    ns, _unknown = p.parse_known_args(argv)
    return ns


def run_export_cli(argv: Optional[List[str]] = None) -> Optional[int]:
    """If --export-csv is present, run the headless export and return an exit code; else return None."""
    argv = list(sys.argv[1:] if argv is None else argv)
    ns = _parse(argv)
    if ns is None:
        return None

    logger = logging.getLogger("NetSpeedTray.ExportCLI")
    token = str(ns.period).lower().strip()
    period_key = _PERIOD_TOKENS.get(token)
    if period_key is None:
        sys.stderr.write(f"Unknown --period '{ns.period}'. Valid: {', '.join(sorted(_PERIOD_TOKENS))}\n")
        return 2

    # Imports kept inside the function so the GUI path never pays for them.
    from netspeedtray import constants, __version__
    from netspeedtray.utils.config import ConfigManager
    from netspeedtray.utils.helpers import get_machine_id
    from netspeedtray.utils import stats_exporter
    from netspeedtray.core.widget_state import WidgetState

    try:
        config = ConfigManager().load()
    except Exception as e:
        sys.stderr.write(f"Could not load config: {e}\n")
        return 1

    # WidgetState is a QObject with (unstarted) QTimers + a QThread worker; a QCoreApplication must
    # exist for those to construct cleanly. This path always exits afterwards, so it never collides
    # with the GUI's QApplication (which is only created when --export-csv is absent).
    from PyQt6.QtCore import QCoreApplication
    _app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    ws = WidgetState(config, read_only=True)
    try:
        now = datetime.now()
        hp = constants.data.history_period
        earliest = None
        if period_key in ("TIMELINE_ALL", "TIMELINE_SYSTEM_UPTIME"):
            try:
                earliest = ws.get_earliest_data_timestamp()
            except Exception:
                earliest = None
        start = hp.get_start_time(period_key, now, None, None, earliest)
        if start is None:
            # SESSION/boot have no meaning headlessly -> fall back to all available history.
            start = ws.get_earliest_data_timestamp() or now.replace(hour=0, minute=0, second=0, microsecond=0)

        label = str(getattr(constants.i18n.get_i18n(config.get("language")), period_key,
                            period_key.replace("TIMELINE_", "").replace("_", " ").title()))
        machine = get_machine_id()[:8]
        ts = now.strftime("%Y%m%d-%H%M%S")
        basename = ns.basename or f"nst_export_{machine}_{token}_{ts}"
        out_dir = os.path.abspath(ns.out)
        poll = float(config.get("update_rate", 1.0) or 1.0)

        paths = stats_exporter.export_window(
            ws, start, now, label, out_dir, basename,
            machine_id=machine, app_version=__version__, interface=ns.interface, poll_interval=poll)
    except Exception as e:
        logger.error("Headless export failed: %s", e, exc_info=True)
        sys.stderr.write(f"Export failed: {e}\n")
        return 1
    finally:
        try:
            ws.cleanup()
        except Exception:
            pass

    for p in paths.values():
        sys.stdout.write(p + "\n")
    return 0
