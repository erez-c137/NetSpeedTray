"""
Reads issued while the DB worker is still deciding / migrating the file (the first 2.2 launch on a v7
database) return empty BY DESIGN. Step-8 rollback verification showed each one logged an ERROR with
a full traceback ("v8 history read failed: history database not available for reading"), which reads
like a failure in a support log. The not-ready case is now INFO without a traceback; a real read
failure still logs ERROR with one.
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core.db.history_reader import HistoryNotReadyError, V8HistoryReader
from netspeedtray.core.widget_state import WidgetState


def _unstarted_state(d: Path) -> WidgetState:
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=d):
        return WidgetState(cfg)


def _reads(reader) -> list:
    end = datetime.now()
    start = end - timedelta(days=1)
    return [
        reader.speed_history(start, end, None, True, "minute"),
        reader.hardware_history("cpu", start, end),
        reader.distinct_interfaces(),
        reader.earliest_timestamp(),
    ]


def test_not_ready_reads_return_empty_and_log_info_without_traceback(tmp_path, q_app, caplog):
    s = _unstarted_state(tmp_path)   # worker never started: ready is unset, like mid-migration
    try:
        assert s.history_schema_version() is None
        caplog.set_level(logging.DEBUG)
        results = _reads(s.history_reader)
        assert not any(results), results   # empty, by design
        mine = [r for r in caplog.records if "not available for reading" in r.getMessage()]
        assert mine, "the not-ready case should still be logged"
        assert all(r.levelno == logging.INFO for r in mine), [(r.levelname, r.getMessage()) for r in mine]
        assert all(r.exc_info is None for r in mine)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    finally:
        s.cleanup()


def test_not_ready_error_is_still_an_operational_error():
    # Every existing `except sqlite3.Error` / `except sqlite3.OperationalError` keeps catching it.
    assert issubclass(HistoryNotReadyError, sqlite3.OperationalError)


def test_a_real_read_failure_still_logs_error_with_traceback(caplog):
    def broken_conn():
        raise sqlite3.DatabaseError("database disk image is malformed")

    reader = V8HistoryReader(broken_conn, lambda: Path("x.db"), logging.getLogger("test.reader"))
    caplog.set_level(logging.DEBUG)
    assert not any(_reads(reader))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and all(r.exc_info is not None for r in errors)
    assert all("malformed" in r.getMessage() for r in errors)
