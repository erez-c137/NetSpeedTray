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

def test_get_read_conn_prunes_dead_thread_connections(q_app, tmp_path: Path):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    with patch.object(QThread, "start", lambda self: None), \
         patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        ws = WidgetState(cfg)
    ws._db_path = tmp_path / "speed_history.db"
    ws.db_worker.db_path = ws._db_path
    ws.db_worker._initialize_connection()
    ws.db_worker._check_and_create_schema()

    # Inject a connection as if a now-dead worker thread had opened it (a fake, non-live thread id).
    dead_id = max(t.ident for t in threading.enumerate()) + 999_999
    real = sqlite3.connect(":memory:")
    ws._read_conns[dead_id] = real

    ws._get_read_conn()                            # any call prunes dead-thread entries

    assert dead_id not in ws._read_conns, "stale dead-thread connection was not evicted"
    with pytest.raises(sqlite3.ProgrammingError):  # the leaked connection was closed
        real.execute("SELECT 1")
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
