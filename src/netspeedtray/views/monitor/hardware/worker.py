r"""
HardwareActivityWorker — per-process CPU / RAM / GPU sampler for the Monitor's Hardware tab.

Honest, admin-free, and every number measured (not estimated):
  * CPU%  — psutil per-process, summed across a program's PIDs, normalised to total-CPU (0-100%).
  * RAM   — psutil USS (Unique Set Size = private resident memory), summed across PIDs. This is what
            Task Manager's "Memory" column shows; rss would double-count shared DLLs across a
            multi-process app and read 2-3x high. USS is heavier to read, so it's refreshed every
            ~6s (every 3rd poll) and cached, while CPU%/GPU% stay on the 2s cadence.
  * GPU%  — Windows PDH "\GPU Engine(*)\Utilization Percentage", parsed pid_<PID>_..._engtype_ and
            reduced with MAX across that PID's engines — the per-engine busy fractions overlap in the
            same wall-clock interval (a frame uses 3D + Copy + Video at once) so they are NOT
            additive; max mirrors the app's system-wide _poll_gpu_hybrid. No admin, no ETW. Absent
            gracefully when the GPU Engine counter set isn't present (non-Windows / odd drivers).

Runs in a dedicated QThread, only while the Hardware tab is visible. The first sample is a baseline
(psutil cpu_percent needs two reads, PDH rate counters need two collects), so CPU/GPU read ~0 on the
very first emit and settle on the next.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict

import psutil
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

try:
    import win32pdh
except ImportError:  # pragma: no cover - non-Windows / missing pywin32
    win32pdh = None

_PID_RE = re.compile(r"pid_(\d+)_")
_GPU_COUNTER_PATH = r"\GPU Engine(*)\Utilization Percentage"

# Pseudo-processes Task Manager keeps out of the normal per-app list: the Idle process reports idle
# time as "CPU"; System (PID 4) is kernel DPC/ISR time; Memory Compression / Registry / Secure System
# are rolled into System. Including any of them distorts the list order and the CPU/RAM totals.
_SKIP_PIDS = {0, 4}
_SKIP_NAMES = {"system idle process", "system", "secure system", "registry",
               "memcompression", "memory compression"}


class HardwareActivityWorker(QObject):
    """Collects per-application CPU%, RAM, and GPU% grouped by program identity."""

    data_ready = pyqtSignal(object)
    error = pyqtSignal(str)

    #: Reading USS (memory_full_info) for every process is ~2.5x the cost of a plain rss sweep, but
    #: memory moves slowly — so refresh it only every Nth poll and cache it between (CPU%/GPU% still
    #: update every poll). With the feed's 2s cadence that's a USS refresh roughly every 6s.
    _USS_EVERY_N_POLLS: int = 3

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger("NetSpeedTray.HardwareActivityWorker")
        self._cpu_count = psutil.cpu_count(logical=True) or 1
        self._gpu_query = None
        self._gpu_counter = None
        self._poll_count = 0
        self._uss_cache: Dict[str, int] = {}   # identity_key -> summed USS, refreshed every Nth poll

    @pyqtSlot()
    def sample(self) -> None:
        try:
            gpu_by_pid = self._sample_gpu_by_pid()
            agg: Dict[str, Dict[str, Any]] = defaultdict(self._new_agg)

            # USS (Unique Set Size) = a process's private *resident* memory — exactly what Task Manager's
            # "Memory" column shows. rss (the full working set) includes shared DLLs, so summing it across
            # a multi-process app's children (Code's 16 procs) double-counts the shared pages and reads
            # 2-3x high. USS is ~2.5x heavier to read, so we only refresh it every Nth poll and cache it;
            # the first poll always refreshes so memory is correct on the very first emit.
            self._poll_count += 1
            refresh_uss = (self._poll_count == 1) or (self._poll_count % self._USS_EVERY_N_POLLS == 0)

            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    pid = int(proc.info["pid"])
                    name = proc.info["name"] or f"PID {pid}"
                    name_key = name.casefold()
                    if pid in _SKIP_PIDS or name_key in _SKIP_NAMES:
                        continue
                    cpu = proc.cpu_percent(None)          # delta on psutil's cached Process object
                    # Read USS on a full refresh, OR for a program we have no cached USS for yet (e.g.
                    # one that just launched between refreshes) — otherwise a new multi-process app would
                    # fall back to summed rss and read 2-3x high until the next refresh.
                    uss = None
                    if refresh_uss or name_key not in self._uss_cache:
                        try:
                            fi = proc.memory_full_info()
                            rss, uss = fi.rss, fi.uss     # one call yields both; uss is the displayed value
                        except (psutil.AccessDenied, OSError):
                            rss = proc.memory_info().rss  # USS denied (protected proc) → fall back to rss
                    else:
                        rss = proc.memory_info().rss      # cheap; this program already has a cached USS
                except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
                    continue
                a = agg[name_key]
                a["display_name"] = name
                a["pids"].add(pid)
                a["cpu"] += float(cpu)
                a["rss"] += int(rss)
                a["gpu"] += float(gpu_by_pid.get(pid, 0.0))
                if uss is not None:
                    a["uss"] += uss
                    a["has_uss"] = True

            # On a full refresh, rebuild the cache from scratch (drops programs that have gone away, so it
            # can't grow unbounded). Otherwise augment it with any programs measured this poll (the newly
            # launched ones), keeping the existing cached values for the rest. The cached value lags by
            # ≤6s between refreshes, which is invisible for slow-moving memory.
            if refresh_uss:
                self._uss_cache = {k: a["uss"] for k, a in agg.items() if a.get("has_uss")}
            else:
                for k, a in agg.items():
                    if a.get("has_uss"):
                        self._uss_cache[k] = a["uss"]

            rows = []
            for key, a in agg.items():
                rows.append({
                    "identity_key": key,
                    "display_name": a["display_name"],
                    "pids": sorted(a["pids"]),
                    # Normalise summed-across-cores CPU to a 0-100% share of the whole CPU.
                    "cpu_pct": min(100.0, a["cpu"] / self._cpu_count),
                    "rss_bytes": self._uss_cache.get(key, a["rss"]),  # accurate USS if cached, else rss
                    "gpu_pct": min(100.0, a["gpu"]),   # max-of-engines is already 0-100; clamp is belt-and-braces
                })
            rows.sort(key=lambda r: (-r["cpu_pct"], -r["gpu_pct"], r["display_name"].casefold()))

            total_cpu = min(100.0, sum(r["cpu_pct"] for r in rows))
            self.data_ready.emit({
                "updated_at": datetime.now().strftime("%H:%M:%S"),
                "rows": rows,
                "proc_count": len(rows),
                "total_cpu_pct": total_cpu,
                "total_rss_bytes": sum(r["rss_bytes"] for r in rows),
                "gpu_available": bool(gpu_by_pid) or self._gpu_query is not None,
            })
        except Exception as exc:
            self.logger.error("Failed to sample hardware activity: %s", exc, exc_info=True)
            self.error.emit(str(exc))

    @staticmethod
    def _new_agg() -> Dict[str, Any]:
        return {"display_name": "", "pids": set(), "cpu": 0.0, "rss": 0, "gpu": 0.0,
                "uss": 0, "has_uss": False}

    # --- GPU% via PDH (per the verified spike) ----------------------------------
    def _ensure_gpu_query(self) -> None:
        if self._gpu_query is not None or win32pdh is None:
            return
        try:
            self._gpu_query = win32pdh.OpenQuery()
            self._gpu_counter = win32pdh.AddCounter(self._gpu_query, _GPU_COUNTER_PATH)
            win32pdh.CollectQueryData(self._gpu_query)   # prime (rate counters need two collects)
        except Exception as exc:
            self.logger.debug("Per-PID GPU PDH unavailable: %s", exc)
            self._gpu_query = None
            self._gpu_counter = None

    def _sample_gpu_by_pid(self) -> Dict[int, float]:
        if win32pdh is None:
            return {}
        self._ensure_gpu_query()
        if self._gpu_query is None:
            return {}
        try:
            win32pdh.CollectQueryData(self._gpu_query)
            arr = win32pdh.GetFormattedCounterArray(self._gpu_counter, win32pdh.PDH_FMT_DOUBLE)
        except Exception as exc:
            self.logger.debug("Per-PID GPU PDH read failed: %s", exc)
            return {}
        per_pid: Dict[int, float] = defaultdict(float)
        for inst, val in arr.items():
            m = _PID_RE.search(inst)
            if m and isinstance(val, (int, float)) and val > 0:
                pid = int(m.group(1))
                per_pid[pid] = max(per_pid[pid], float(val))   # engines overlap in time -> MAX, not sum
        return per_pid

    def close_gpu_query(self) -> None:
        """Release the PDH query. Called on the worker thread before it stops (see HardwareFeed)."""
        if win32pdh is None or self._gpu_query is None:
            return
        try:
            win32pdh.CloseQuery(self._gpu_query)
        except Exception:
            pass
        self._gpu_query = None
        self._gpu_counter = None
