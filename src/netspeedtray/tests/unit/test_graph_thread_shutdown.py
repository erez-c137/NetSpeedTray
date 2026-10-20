"""
The Monitor's graph worker thread at close/quit (2.2.0-beta.1 lifecycle review, finding 1).

* GraphHost.teardown's wait was unbounded (``wait(700)`` then a bare ``wait()``): a slow query held the
  UI for its whole duration. Now: 700 ms, then the query is interrupted, then 2 s, then detach.
* The worker's read connection is closed on the worker's own thread as it exits (``finished``,
  DirectConnection), not left for WidgetState.cleanup() to close from the UI thread mid-query.
"""
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.views.monitor import graph_host as GH


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _make_state(tmp_path):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    with patch.object(QThread, "start", lambda self: None), \
         patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        ws = WidgetState(cfg)
    from netspeedtray.tests.state_helpers import prepare_v8
    prepare_v8(ws, tmp_path / "speed_history.db")
    return ws


class _StuckThread:
    """A QThread stand-in whose run never ends: wait(ms) times out, a bare wait() blocks ~forever."""
    def __init__(self):
        self.finished = MagicMock()
        self.deleted = False

    def quit(self):
        pass

    def wait(self, ms=None):
        time.sleep(10.0 if ms is None else ms / 1000.0)
        return False

    def isRunning(self):
        return True

    def deleteLater(self):
        self.deleted = True


def test_teardown_is_bounded_when_the_worker_never_stops(q_app, monkeypatch):
    from netspeedtray.core import database
    monkeypatch.setattr(constants.timeouts, "GRAPH_THREAD_QUIT_WAIT_MS", 100, raising=False)
    monkeypatch.setattr(constants.timeouts, "GRAPH_THREAD_STOP_WAIT_MS", 200, raising=False)
    monkeypatch.setattr(database, "_DETACHED_WORKERS", [])
    mw = MagicMock()
    host = GH.GraphHost(mw, {}, I18nStrings("en_US"))
    stuck = _StuckThread()
    host._thread, host.worker, host._thread_ident = stuck, MagicMock(), 4242

    t0 = time.perf_counter()
    host.teardown()
    took = time.perf_counter() - t0

    assert took < 2.0, f"teardown blocked the UI {took:.1f} s on a stuck graph thread"
    mw.widget_state.interrupt_reads.assert_called_once_with(thread_id=4242)
    assert stuck in database._DETACHED_WORKERS     # kept alive; monitor.py exits hard if it still runs
    assert not stuck.deleted                       # never destroy a running QThread


class _Reader(QObject):
    go = pyqtSignal(str)

    def __init__(self, ws):
        super().__init__()
        self.ws = ws
        self.box = {}
        self.started = threading.Event()
        self.done = threading.Event()

    def run_query(self, sql):
        # Like GraphDataWorker.process_data: several queries in a row, each fetching its connection.
        self.box["errs"] = []
        for _ in range(3 if "RECURSIVE" in sql else 1):
            try:
                conn = self.ws._get_read_conn()
                self.box["conn"] = conn
                self.started.set()
                conn.execute(sql).fetchone()
                self.box["errs"].append(None)
            except sqlite3.OperationalError as e:   # HistoryNotReadyError is one
                self.box["errs"].append(f"{type(e).__name__}: {e}")
        self.box["err"] = self.box["errs"][0]
        self.done.set()


def _start(ws):
    thread = QThread()
    reader = _Reader(ws)
    reader.moveToThread(thread)
    reader.go.connect(reader.run_query)
    ident = {}
    GH.bind_reader_thread(thread, ws, lambda i: ident.setdefault("tid", i))
    thread.start()
    return thread, reader, ident


def test_worker_closes_its_own_connection_on_exit(q_app, tmp_path: Path):
    ws = _make_state(tmp_path)
    thread, reader, ident = _start(ws)
    reader.go.emit("SELECT 1")
    assert reader.done.wait(5)
    assert GH.stop_reader_thread(thread, reader, ws, ident.get("tid"), MagicMock())
    assert ident.get("tid") is not None
    assert ws._read_conns == {}, "the worker's connection outlived the worker"
    with pytest.raises(sqlite3.ProgrammingError):
        reader.box["conn"].execute("SELECT 1")
    ws.cleanup()


def test_stopping_mid_query_interrupts_instead_of_waiting_it_out(q_app, tmp_path: Path):
    """Quit with the Monitor open while its graph query runs: bounded, no closed-under-it error."""
    ws = _make_state(tmp_path)
    thread, reader, ident = _start(ws)
    reader.go.emit("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 300000000) "
                   "SELECT count(*) FROM c")
    assert reader.started.wait(5)
    time.sleep(0.1)
    t0 = time.perf_counter()
    stopped = GH.stop_reader_thread(thread, reader, ws, ident.get("tid"), MagicMock())
    took = time.perf_counter() - t0
    assert stopped
    assert took < 1.5, f"UI blocked {took:.2f} s"
    errs = reader.box["errs"]
    assert "interrupt" in (errs[0] or "").lower()
    # The rest of the refresh is refused at once instead of running its next slow query.
    assert all(e and e.startswith("HistoryNotReadyError") for e in errs[1:]), errs
    assert ws._read_conns == {}
    assert ws._reads_refused == set()      # cleared on the thread's own release
    ws.cleanup()
