"""
The Monitor's graph worker runs on its own thread and, for session views, reads WidgetState's live
hardware deques while the GUI thread appends to them via add_hardware_stat().

Regression for the audit High: the worker iterated cpu_history/gpu_history/ram_history *directly*, which
raises "deque mutated during iteration" (the GIL doesn't protect a Python-level loop that yields between
iterations) — swallowed and re-emitted as a generic worker error, so the session Hardware/Overview graph
intermittently failed to render. The fix routes through the copy getters; list(deque) copies under one
GIL-held C call, so it's safe. This test hammers the deques from another thread while the worker processes
session requests and asserts it never errors.
"""
import threading
from collections import deque
from datetime import datetime, timedelta

import pytest

from netspeedtray.core.widget_state import HardwareStatSnapshot
from netspeedtray.views.graph.worker import GraphDataWorker
from netspeedtray.views.graph.request import DataRequest


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeWS:
    """Just the surface the worker's session-hwcombined path reads — real deques + real copy getters."""
    def __init__(self):
        self.cpu_history = deque(maxlen=5000)
        self.gpu_history = deque(maxlen=5000)
        self.ram_history = deque(maxlen=5000)

    def get_cpu_history(self):
        return list(self.cpu_history)

    def get_gpu_history(self):
        return list(self.gpu_history)

    def get_ram_history(self):
        return list(self.ram_history)


def test_copy_getters_snapshot_so_concurrent_append_is_safe(q_app):
    ws = _FakeWS()
    now = datetime.now()
    for i in range(3):
        ws.cpu_history.append(HardwareStatSnapshot(float(i), now))
    snap = ws.get_cpu_history()
    ws.cpu_history.append(HardwareStatSnapshot(99.0, now))   # mutate after snapshotting
    assert len(snap) == 3, "getter must return a detached copy, not a live view of the deque"


def test_worker_session_path_survives_concurrent_appends(q_app):
    ws = _FakeWS()
    worker = GraphDataWorker(ws)
    errors, payloads = [], []
    worker.error.connect(errors.append)
    worker.data_ready.connect(lambda *a: payloads.append(a))

    stop = threading.Event()

    def hammer():
        # Tight append loop on the GUI-equivalent thread — the mutation the worker used to trip over.
        while not stop.is_set():
            t = datetime.now()
            ws.cpu_history.append(HardwareStatSnapshot(50.0, t))
            ws.gpu_history.append(HardwareStatSnapshot(40.0, t))
            ws.ram_history.append(HardwareStatSnapshot(60.0, t))

    appender = threading.Thread(target=hammer, daemon=True)
    appender.start()
    try:
        now = datetime.now()
        for i in range(300):
            worker.process_data(DataRequest(
                start_time=now - timedelta(minutes=5), end_time=now + timedelta(minutes=5),
                interface_name=None, is_session_view=True, sequence_id=i + 1, stat_type="hwcombined"))
    finally:
        stop.set()
        appender.join(timeout=2.0)

    assert not errors, f"worker errored on the live-deque race: {errors[:3]}"
    assert payloads, "worker never emitted a session payload"
