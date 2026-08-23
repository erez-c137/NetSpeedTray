"""
Reliability/security fixes from the 2.0 audit:
- M1: update_config (GUI thread) must NOT close the monitor thread's PDH handles directly - it flags
      them for the worker thread to re-init.
- M2: _get_read_conn prunes connections left behind by dead threads (the per-Monitor-open leak).
- M12: nvidia-smi is resolved from trusted locations only - never a planted binary in the CWD.
"""
import os
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.core.widget_state import WidgetState


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# --- M1: PDH handle race -----------------------------------------------------

def test_update_config_flags_instead_of_closing_pdh_handles(q_app):
    t = StatsMonitorThread(config=dict(constants.config.defaults.DEFAULT_CONFIG))
    t._gpu_query = 12345          # a live handle the run() loop would be using
    t._thermal_query = 67890
    t.update_config(dict(constants.config.defaults.DEFAULT_CONFIG))
    assert t._hw_queries_dirty is True            # flagged for the worker thread
    assert t._gpu_query == 12345                  # NOT closed from the GUI thread
    assert t._thermal_query == 67890


# --- M2: read-connection leak ------------------------------------------------

def _make_state(tmp_path):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    with patch.object(QThread, "start", lambda self: None),          patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        ws = WidgetState(cfg)
    ws._db_path = tmp_path / "speed_history.db"
    ws.db_worker.db_path = ws._db_path
    ws.db_worker._initialize_connection()
    ws.db_worker._check_and_create_schema()
    return ws


def test_get_read_conn_reclaims_idle_connections(q_app, tmp_path: Path):
    """M2: a connection left behind by a finished worker must not leak for the whole session."""
    ws = _make_state(tmp_path)
    stale_id = max(t.ident for t in threading.enumerate()) + 999_999
    leaked = sqlite3.connect(":memory:")
    # Last used well beyond the idle threshold - i.e. nobody is querying on it.
    ws._read_conns[stale_id] = (leaked, time.monotonic() - (ws._READ_CONN_IDLE_SEC + 60))

    ws._get_read_conn()

    assert stale_id not in ws._read_conns, "the idle connection was not evicted"
    with pytest.raises(sqlite3.ProgrammingError):
        leaked.execute("SELECT 1")                 # and it really was closed
    ws.cleanup()


def test_get_read_conn_never_closes_a_connection_still_in_use(q_app, tmp_path: Path):
    """The race this replaced liveness-checking to fix.

    Pruning used to evict any connection whose thread id was absent from `threading.enumerate()`.
    Qt worker threads never appear there - they call `threading.get_ident()`, not
    `threading.current_thread()` - so the Monitor's graph worker was permanently invisible, and the
    next call from any other thread closed its connection *mid-query*:

        WidgetState.get_hardware_history - Cannot operate on a closed database

    Registering the thread instead is worse: on CPython 3.11 the `_DummyThread` never leaves
    `enumerate()` once the native thread dies, so nothing would ever be pruned and the leak above
    comes back. Idle time is the one signal a busy thread cannot trip.
    """
    ws = _make_state(tmp_path)
    worker_id = max(t.ident for t in threading.enumerate()) + 999_999   # invisible, like a QThread
    in_use = sqlite3.connect(":memory:")
    ws._read_conns[worker_id] = (in_use, time.monotonic())              # just used it

    ws._get_read_conn()                                                 # a call from another thread

    assert worker_id in ws._read_conns, "an in-use connection was evicted"
    assert in_use.execute("SELECT 1").fetchone() == (1,), (
        "the connection was closed while its thread was still querying - this is the bug")
    ws.cleanup()


def test_reusing_a_connection_refreshes_its_idle_clock(q_app, tmp_path: Path):
    """A long-lived worker must not age out just because it has been running a while."""
    ws = _make_state(tmp_path)
    first = ws._get_read_conn()
    tid = threading.get_ident()
    ws._read_conns[tid] = (first, time.monotonic() - (ws._READ_CONN_IDLE_SEC + 60))

    again = ws._get_read_conn()

    assert again is first, "the same thread should keep its own connection"
    _conn, seen = ws._read_conns[tid]
    assert (time.monotonic() - seen) < 1.0, "fetching a connection did not refresh its idle clock"
    ws.cleanup()


# --- M12: nvidia-smi binary planting -----------------------------------------

def test_nvidia_smi_not_resolved_from_current_directory(q_app, tmp_path: Path, monkeypatch):
    """A planted nvidia-smi.exe in the (user-writable) CWD must NOT be returned."""
    planted = tmp_path / "nvidia-smi.exe"
    planted.write_bytes(b"MZ")                     # a fake executable
    monkeypatch.chdir(tmp_path)
    # No trusted install + a PATH that (maliciously) contains '.' and the cwd.
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(tmp_path)]))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "nope"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nope"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "nope"))

    t = StatsMonitorThread(config=dict(constants.config.defaults.DEFAULT_CONFIG))
    t._get_cached_path.cache_clear() if hasattr(t._get_cached_path, "cache_clear") else None
    resolved = t._get_cached_path("nvidia-smi")
    assert resolved != str(planted), "resolved the planted CWD binary - binary-planting hole"
    assert resolved is None or os.path.isabs(resolved)
