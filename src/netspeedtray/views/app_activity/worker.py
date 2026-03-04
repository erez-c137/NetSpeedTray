"""
Background worker for per-application network activity sampling.

The worker runs in a dedicated QThread and only samples while the App Activity
window is open, keeping the main taskbar widget fast and unaffected.
"""

from __future__ import annotations

import logging
import socket
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

import psutil
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class AppActivityWorker(QObject):
    """
    Collects active network connections grouped by process and estimates
    per-process upload/download rates using process I/O deltas.
    """

    data_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    ENDPOINT_PREVIEW_LIMIT = 3

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger("NetSpeedTray.AppActivityWorker")
        self._last_sample_time: float | None = None
        self._last_io_counters: Dict[int, Tuple[float, float]] = {}
        self._process_name_cache: Dict[int, str] = {}

    @pyqtSlot()
    def sample(self) -> None:
        """Capture one snapshot and emit normalized payload for the UI."""
        try:
            pid_connections = self._collect_connections_by_pid()

            now = time.monotonic()
            elapsed = 0.0 if self._last_sample_time is None else max(0.001, now - self._last_sample_time)

            rows: List[Dict[str, Any]] = []
            next_io_counters: Dict[int, Tuple[float, float]] = {}

            for pid, endpoints in pid_connections.items():
                process_name, download_bps, upload_bps, io_snapshot = self._collect_process_snapshot(
                    pid=pid,
                    elapsed=elapsed,
                )
                if io_snapshot is not None:
                    next_io_counters[pid] = io_snapshot

                unique_endpoints = list(dict.fromkeys(endpoints))
                preview_items = unique_endpoints[: self.ENDPOINT_PREVIEW_LIMIT]
                preview = "; ".join(preview_items) if preview_items else "-"
                if len(unique_endpoints) > self.ENDPOINT_PREVIEW_LIMIT:
                    preview = f"{preview}; (+{len(unique_endpoints) - self.ENDPOINT_PREVIEW_LIMIT} more)"

                rows.append(
                    {
                        "process_name": process_name,
                        "pid": pid,
                        "download_bps": download_bps,
                        "upload_bps": upload_bps,
                        "connection_count": len(unique_endpoints),
                        "endpoint_preview": preview,
                        "endpoints": unique_endpoints,
                    }
                )

            rows.sort(
                key=lambda row: (
                    float(row.get("download_bps", 0.0)) + float(row.get("upload_bps", 0.0)),
                    int(row.get("connection_count", 0)),
                ),
                reverse=True,
            )

            self._last_sample_time = now
            self._last_io_counters = next_io_counters

            total_down_bps = sum(float(row["download_bps"]) for row in rows)
            total_up_bps = sum(float(row["upload_bps"]) for row in rows)

            self.data_ready.emit(
                {
                    "updated_at": datetime.now().strftime("%H:%M:%S"),
                    "rows": rows,
                    "total_down_bps": total_down_bps,
                    "total_up_bps": total_up_bps,
                }
            )
        except Exception as exc:
            self.logger.error("Failed to sample app activity: %s", exc, exc_info=True)
            self.error.emit(str(exc))

    def _collect_connections_by_pid(self) -> Dict[int, List[str]]:
        grouped: Dict[int, List[str]] = defaultdict(list)
        for conn in psutil.net_connections(kind="inet"):
            pid = getattr(conn, "pid", None)
            if pid is None:
                continue
            endpoint = self._format_connection(conn)
            if endpoint:
                grouped[int(pid)].append(endpoint)
        return grouped

    def _collect_process_snapshot(
        self,
        pid: int,
        elapsed: float,
    ) -> Tuple[str, float, float, Tuple[float, float] | None]:
        """
        Returns process name, download/upload estimates, and latest io snapshot.
        """
        process_name = self._process_name_cache.get(pid, f"PID {pid}")
        download_bps = 0.0
        upload_bps = 0.0

        process: psutil.Process | None = None
        try:
            process = psutil.Process(pid)
            process_name = process.name() or process_name
            self._process_name_cache[pid] = process_name
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            self._process_name_cache.pop(pid, None)
            return process_name, download_bps, upload_bps, None
        except (psutil.AccessDenied, OSError):
            pass

        if process is None:
            return process_name, download_bps, upload_bps, None

        try:
            io = process.io_counters()
            read_bytes = float(getattr(io, "read_bytes", 0.0) or 0.0)
            write_bytes = float(getattr(io, "write_bytes", 0.0) or 0.0)
            io_snapshot = (read_bytes, write_bytes)

            previous = self._last_io_counters.get(pid)
            if previous is not None and elapsed > 0:
                download_bps = max(0.0, (read_bytes - previous[0]) / elapsed)
                upload_bps = max(0.0, (write_bytes - previous[1]) / elapsed)
            return process_name, download_bps, upload_bps, io_snapshot
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            self._process_name_cache.pop(pid, None)
            return process_name, download_bps, upload_bps, None
        except (psutil.AccessDenied, OSError):
            # Keep row visible but with zeroed speed if process counters are inaccessible.
            return process_name, download_bps, upload_bps, None

    def _format_connection(self, conn: Any) -> str:
        protocol = self._get_protocol_name(getattr(conn, "type", 0))
        local_addr = self._format_address(getattr(conn, "laddr", None))
        remote_addr = self._format_address(getattr(conn, "raddr", None))
        status = str(getattr(conn, "status", "") or "").strip()
        suffix = f" {status}" if status and status.upper() != "NONE" else ""
        return f"{protocol} {local_addr} -> {remote_addr}{suffix}"

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
