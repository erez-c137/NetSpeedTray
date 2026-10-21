"""
Read-connection lifecycle at quit (2.2.0-beta.1 threads/lifecycle review, finding 1).

Quitting with the Monitor open ran WidgetState.cleanup() - which closed EVERY thread's read
connection from the UI thread - while the Monitor's graph worker could still be mid-query on its
own. Closing a sqlite3 connection another thread is executing on is undefined behaviour (it
segfaulted 4/4 in a plain repro on 3.13); the lucky outcome was "Cannot operate on a closed
database". The table was then cleared with no closed flag, so any later read opened a fresh
read-write connection nobody ever closed.

Now: each thread closes its OWN connection (release_read_conn, wired to the worker's exit), cleanup()
closes only the caller's and interrupts the others' in-flight statements (sqlite3_interrupt is the one
cross-thread call SQLite documents as safe), and reads are refused after cleanup.
"""
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core.db.history_reader import HistoryNotReadyError
from netspeedtray.core.widget_state import WidgetState


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


_SLOW = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 200000000) "
         "SELECT count(*) FROM c")


def test_cleanup_does_not_close_another_threads_connection(q_app, tmp_path: Path):
    ws = _make_state(tmp_path)
    got, go, done = threading.Event(), threading.Event(), threading.Event()
    box = {}

    def worker():
        box["conn"] = ws._get_read_conn()
        got.set()
        go.wait(5)
        try:
            box["after"] = box["conn"].execute("SELECT 1").fetchone()[0]
        except Exception as e:   # noqa: BLE001
            box["after"] = e
        ws.release_read_conn()
        done.set()

    t = threading.Thread(target=worker)
    t.start()
    assert got.wait(5)
    ws.cleanup()                       # UI thread, while the worker still holds its connection
    go.set()
    assert done.wait(5)
    t.join(5)
    assert box["after"] == 1, f"cleanup closed the worker's connection under it: {box['after']!r}"
    with pytest.raises(sqlite3.ProgrammingError):
        box["conn"].execute("SELECT 1")          # ...and the worker's own release closed it
    assert ws._read_conns == {}


def test_cleanup_interrupts_an_in_flight_query_promptly(q_app, tmp_path: Path):
    ws = _make_state(tmp_path)
    started, box = threading.Event(), {}

    def worker():
        conn = ws._get_read_conn()
        started.set()
        t0 = time.perf_counter()
        try:
            conn.execute(_SLOW).fetchone()
            box["err"] = None
        except sqlite3.OperationalError as e:
            box["err"] = str(e)
        box["ran"] = time.perf_counter() - t0
        ws.release_read_conn()

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(5)
    time.sleep(0.2)                    # let the statement get going
    t0 = time.perf_counter()
    ws.cleanup()
    blocked = time.perf_counter() - t0
    t.join(10)
    assert not t.is_alive()
    assert box["err"] and "interrupt" in box["err"].lower()
    assert box["ran"] < 3.0
    assert blocked < 1.0


def test_reads_are_refused_after_cleanup(q_app, tmp_path: Path):
    ws = _make_state(tmp_path)
    ws._get_read_conn()
    ws.cleanup()
    assert ws._read_conns == {}
    with pytest.raises(HistoryNotReadyError):
        ws._get_read_conn()
    assert ws._read_conns == {}, "a read after cleanup opened a connection nobody will close"
    # The public readers degrade to empty rather than raising.
    assert ws.get_speed_history(wait_for_flush=False) == []


def test_release_read_conn_closes_only_the_callers(q_app, tmp_path: Path):
    ws = _make_state(tmp_path)
    mine = ws._get_read_conn()
    other = sqlite3.connect(":memory:", check_same_thread=False)
    ws._read_conns[123456789] = (other, time.monotonic())
    ws.release_read_conn()
    with pytest.raises(sqlite3.ProgrammingError):
        mine.execute("SELECT 1")
    assert other.execute("SELECT 1").fetchone() == (1,)
    assert list(ws._read_conns) == [123456789]
    ws.release_read_conn()                       # idempotent: nothing registered for this thread
    other.close()
    ws._read_conns.clear()
    ws.cleanup()


def test_interrupt_reads_targets_one_thread(q_app, tmp_path: Path):
    ws = _make_state(tmp_path)

    class _Conn:
        def __init__(self):
            self.interrupted = 0

        def interrupt(self):
            self.interrupted += 1

    a, b = _Conn(), _Conn()
    ws._read_conns[1] = (a, time.monotonic())
    ws._read_conns[2] = (b, time.monotonic())
    ws.interrupt_reads(thread_id=2)
    assert (a.interrupted, b.interrupted) == (0, 1)
    ws.interrupt_reads()
    assert (a.interrupted, b.interrupted) == (1, 2)
    ws._read_conns.clear()
    ws.cleanup()
