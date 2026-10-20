"""
A read interrupted by WidgetState.interrupt_reads() (Monitor close / quit during a >700 ms graph query)
is an expected outcome, not a failure: INFO without a traceback, never ERROR + exc_info (PR #377 review).
Any other OperationalError is still a real failure and stays at ERROR.
"""
import logging
import sqlite3
from datetime import datetime, timedelta

import pytest

from netspeedtray.core.db.history_reader import V7HistoryReader, V8HistoryReader


class _RaisingConn:
    in_transaction = False

    def __init__(self, msg):
        self.msg = msg

    def execute(self, *_a, **_k):
        raise sqlite3.OperationalError(self.msg)

    def cursor(self):
        return self


def _reader(cls, msg):
    log = logging.getLogger("test.interrupt")
    r = cls(lambda: _RaisingConn(msg), lambda: ":memory:", log)
    if hasattr(r, "allow_fallback"):
        r.allow_fallback = False
    return r


@pytest.mark.parametrize("cls", [V7HistoryReader, V8HistoryReader])
def test_interrupted_read_logs_info_without_traceback(cls, caplog):
    r = _reader(cls, "interrupted")
    end = datetime.now()
    with caplog.at_level(logging.DEBUG, logger="test.interrupt"):
        assert r.speed_history(end - timedelta(hours=1), end) == []
        assert r.hardware_history("cpu", end - timedelta(hours=1), end) == []
    recs = [x for x in caplog.records if x.name == "test.interrupt"]
    assert recs, "the interrupted read was not logged at all"
    assert all(x.levelno < logging.WARNING for x in recs), [(x.levelname, x.getMessage()) for x in recs]
    assert all(x.exc_info is None for x in recs)


def test_other_operational_errors_still_log_error(caplog):
    r = _reader(V8HistoryReader, "disk I/O error")
    end = datetime.now()
    with caplog.at_level(logging.DEBUG, logger="test.interrupt"):
        r.hardware_history("cpu", end - timedelta(hours=1), end)
    assert any(x.levelno == logging.ERROR and x.exc_info for x in caplog.records
               if x.name == "test.interrupt")
