"""
Background worker for per-application network activity sampling (v2 - honest model).

Windows can't attribute network *bytes* to a process without admin/ETW, so this worker no
longer dresses up disk-I/O deltas as "download/upload speed" (the old lie). Instead it reports
only what it can measure exactly and for free: the live network *connections* each app holds -
their count, how many are established, the distinct remote hosts, and TCP/UDP split - rolled up
by application identity (one row per program, not per PID). Every number here is exact.

Runs in a dedicated QThread, only while the App Activity window is open.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import psutil
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class AppActivityWorker(QObject):
    """Collects live network connections grouped by application identity. No byte estimates."""

    data_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger("NetSpeedTray.AppActivityWorker")
        self._name_cache: Dict[int, str] = {}

    @pyqtSlot()
    def sample(self) -> None:
        """Capture one snapshot of live connections and emit an honest, identity-grouped payload."""
        try:
            pid_conns, access_limited = self._collect_connections_by_pid()

            # Aggregate connections by application identity (roll up all PIDs of one program).
            agg: Dict[str, Dict[str, Any]] = defaultdict(self._new_agg)
            for pid, conns in pid_conns.items():
                name = self._process_name(pid)
                key = name.casefold()
                a = agg[key]
                a["display_name"] = name
                a["pids"].add(pid)
                for conn in conns:
                    a["conn_count"] += 1
                    proto = self._get_protocol_name(getattr(conn, "type", 0))
                    if proto == "TCP":
                        a["tcp_count"] += 1
                    elif proto == "UDP":
                        a["udp_count"] += 1
                    status = str(getattr(conn, "status", "") or "").upper()
                    if status == "ESTABLISHED":
                        a["established_count"] += 1
                    host = self._remote_host(conn)
                    if host:
                        a["hosts"].add(host)
                    a["endpoints"].append(self._format_connection(conn))

            rows: List[Dict[str, Any]] = []
            for key, a in agg.items():
                hosts = sorted(a["hosts"])
                rows.append({
                    "identity_key": key,
                    "display_name": a["display_name"],
                    "pids": sorted(a["pids"]),
                    "conn_count": a["conn_count"],
                    "tcp_count": a["tcp_count"],
                    "udp_count": a["udp_count"],
                    "established_count": a["established_count"],
                    "distinct_hosts": hosts,
                    "host_count": len(hosts),
                    "endpoints": a["endpoints"],
                    "is_idle": a["established_count"] == 0,
                })

            rows.sort(key=self._sort_key)

            self.data_ready.emit({
                "updated_at": datetime.now().strftime("%H:%M:%S"),
                "rows": rows,
                "app_count": len(rows),
                "active_app_count": sum(1 for r in rows if r["established_count"] > 0),
                "total_conn_count": sum(r["conn_count"] for r in rows),
                "access_limited": access_limited,
            })
        except Exception as exc:
            self.logger.error("Failed to sample app activity: %s", exc, exc_info=True)
            self.error.emit(str(exc))

    @staticmethod
    def _new_agg() -> Dict[str, Any]:
        return {
            "display_name": "", "pids": set(), "conn_count": 0, "tcp_count": 0,
            "udp_count": 0, "established_count": 0, "hosts": set(), "endpoints": [],
        }

    @staticmethod
    def _sort_key(row: Dict[str, Any]) -> Tuple[int, int, int, str]:
        # Active apps first, then by live connections, distinct hosts, then name (stable).
        return (
            0 if row["established_count"] > 0 else 1,
            -row["conn_count"],
            -row["host_count"],
            row["display_name"].casefold(),
        )

    def _process_name(self, pid: int) -> str:
        cached = self._name_cache.get(pid)
        if cached:
            return cached
        try:
            name = psutil.Process(pid).name() or f"PID {pid}"
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            self._name_cache.pop(pid, None)
            return f"PID {pid}"
        except (psutil.AccessDenied, OSError):
            name = f"PID {pid}"
        self._name_cache[pid] = name
        return name

    def _collect_connections_by_pid(self) -> Tuple[Dict[int, List[Any]], bool]:
        """Group raw psutil connection objects by PID (with a 2s guard against a hung syscall)."""
        result: List = []

        def _fetch() -> None:
            try:
                result.append(("ok", psutil.net_connections(kind="inet")))
            except Exception as exc:
                result.append(("err", exc))

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=2.0)

        if t.is_alive():
            self.logger.warning("psutil.net_connections() timed out after 2s - skipping this cycle")
            return defaultdict(list), False
        if not result:
            self.logger.error("net_connections thread exited without a result - skipping cycle")
            return defaultdict(list), False

        status, value = result[0]
        if status == "ok":
            grouped: Dict[int, List[Any]] = defaultdict(list)
            for conn in value:
                pid = getattr(conn, "pid", None)
                if pid is None:
                    continue
                grouped[int(pid)].append(conn)
            return grouped, False
        self.logger.info(
            "Global net_connections access denied/unavailable. Falling back to best-effort: %s", value
        )
        return self._collect_connections_by_pid_best_effort(), True

    def _collect_connections_by_pid_best_effort(self) -> Dict[int, List[Any]]:
        """Non-admin fallback: only the processes/connections this session can read."""
        grouped: Dict[int, List[Any]] = defaultdict(list)
        for proc in psutil.process_iter(["pid"]):
            try:
                pid = int(proc.pid)
                for conn in proc.net_connections(kind="inet"):
                    grouped[pid].append(conn)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError):
                continue
        return grouped

    def _format_connection(self, conn: Any) -> str:
        protocol = self._get_protocol_name(getattr(conn, "type", 0))
        local_addr = self._format_address(getattr(conn, "laddr", None))
        remote_addr = self._format_address(getattr(conn, "raddr", None))
        status = str(getattr(conn, "status", "") or "").strip()
        suffix = f" {status}" if status and status.upper() != "NONE" else ""
        return f"{protocol} {local_addr} -> {remote_addr}{suffix}"

    @staticmethod
    def _remote_host(conn: Any) -> Optional[str]:
        """The remote IP (no port), or None for loopback/link-local/unspecified (not a real remote)."""
        raddr = getattr(conn, "raddr", None)
        if not raddr:
            return None
        host = getattr(raddr, "ip", None)
        if host is None and isinstance(raddr, (tuple, list)) and raddr:
            host = raddr[0]
        if not host:
            return None
        host = str(host)
        if (host.startswith("127.") or host.startswith("169.254.")
                or host in ("0.0.0.0", "::1", "::") or host.startswith("fe80:")):
            return None
        return host

    @staticmethod
    def _get_protocol_name(sock_type: int) -> str:
        if sock_type == socket.SOCK_STREAM:
            return "TCP"
        if sock_type == socket.SOCK_DGRAM:
            return "UDP"
        return "IP"

    @staticmethod
    def _format_address(address: Any) -> str:
        if not address:
            return "-"
        host = getattr(address, "ip", None)
        port = getattr(address, "port", None)
        if host is None and isinstance(address, (tuple, list)):
            host = address[0] if len(address) > 0 else None
            port = address[1] if len(address) > 1 else None
        if host is None:
            return "-"
        if port is None:
            return str(host)
        return f"{host}:{port}"
