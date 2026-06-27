"""
HardwareActivityWorker + HardwareBarList — the Monitor Hardware tab. Verifies per-process CPU is
normalised to total-CPU and the idle process is excluded, GPU% is summed per PID from PDH instance
names, and the bar list builds/updates/sorts + handles empty/RDP states.
"""
import types

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.hardware.list import HardwareBarList, HardwareRow
from netspeedtray.views.monitor.hardware.feed import HardwareFeed
import netspeedtray.views.monitor.hardware.worker as W


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeProc:
    def __init__(self, pid, name, cpu, rss):
        self.info = {"pid": pid, "name": name}
        self._cpu, self._rss = cpu, rss

    def cpu_percent(self, _):
        return self._cpu

    def memory_info(self):
        return types.SimpleNamespace(rss=self._rss)


def _hrow(name, cpu, rss=0, gpu=0.0):
    return {"identity_key": name.casefold(), "display_name": name,
            "cpu_pct": cpu, "rss_bytes": rss, "gpu_pct": gpu}


def _hpayload(rows, gpu=True):
    return {"rows": rows, "proc_count": len(rows),
            "total_cpu_pct": sum(r["cpu_pct"] for r in rows),
            "total_rss_bytes": sum(r["rss_bytes"] for r in rows),
            "updated_at": "12:00:00", "gpu_available": gpu}


# --- worker --------------------------------------------------------------------

def test_worker_excludes_idle_and_normalises_cpu(monkeypatch, q_app):
    import psutil
    monkeypatch.setattr(W, "win32pdh", None)   # no GPU column in this test
    procs = [
        _FakeProc(0, "System Idle Process", 400, 0),       # must be excluded
        _FakeProc(10, "a.exe", 200, 100 * 1024 * 1024),
        _FakeProc(11, "a.exe", 100, 50 * 1024 * 1024),     # same identity -> summed
        _FakeProc(20, "b.exe", 40, 10 * 1024 * 1024),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    w = W.HardwareActivityWorker()
    w._cpu_count = 4
    got = []
    w.data_ready.connect(got.append)
    w.sample()
    rows = {r["display_name"]: r for r in got[-1]["rows"]}
    assert "System Idle Process" not in rows
    assert abs(rows["a.exe"]["cpu_pct"] - 75.0) < 0.1      # (200+100)/4
    assert rows["a.exe"]["rss_bytes"] == 150 * 1024 * 1024
    assert abs(rows["b.exe"]["cpu_pct"] - 10.0) < 0.1
    assert got[-1]["rows"][0]["display_name"] == "a.exe"   # sorted busiest-first
    assert got[-1]["gpu_available"] is False


def test_worker_max_gpu_per_pid_from_pdh(monkeypatch, q_app):
    """GPU% is the MAX across a PID's engines (they overlap in time), matching the system sampler."""
    import psutil

    class _PDH:
        PDH_FMT_DOUBLE = 0
        def OpenQuery(self): return "q"
        def AddCounter(self, q, path): return "c"
        def CollectQueryData(self, q): pass
        def GetFormattedCounterArray(self, c, fmt):
            return {
                "pid_10_luid_x_eng_0_engtype_3D": 30.0,
                "pid_10_luid_x_eng_1_engtype_Copy": 10.0,   # same PID -> MAX(30,10) = 30, not 40
                "pid_20_luid_x_eng_0_engtype_3D": 5.0,
                "pid_0_luid_x_engtype_3D": 99.0,            # idle pid, no proc -> ignored
            }
        def CloseQuery(self, q): pass

    monkeypatch.setattr(W, "win32pdh", _PDH())
    procs = [_FakeProc(10, "a.exe", 0, 0), _FakeProc(20, "b.exe", 0, 0)]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    w = W.HardwareActivityWorker()
    got = []
    w.data_ready.connect(got.append)
    w.sample()
    rows = {r["display_name"]: r for r in got[-1]["rows"]}
    assert abs(rows["a.exe"]["gpu_pct"] - 30.0) < 0.1       # max(30, 10) across engines
    assert abs(rows["b.exe"]["gpu_pct"] - 5.0) < 0.1
    assert got[-1]["gpu_available"] is True


def test_worker_excludes_system_and_pseudo_processes(monkeypatch, q_app):
    """PID 4 System and the Memory Compression / Registry pseudo-processes are excluded too."""
    import psutil
    monkeypatch.setattr(W, "win32pdh", None)
    procs = [
        _FakeProc(4, "System", 60, 0),
        _FakeProc(100, "MemCompression", 0, 900 * 1024 * 1024),
        _FakeProc(101, "Registry", 0, 59 * 1024 * 1024),
        _FakeProc(200, "real.exe", 8, 10 * 1024 * 1024),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    w = W.HardwareActivityWorker()
    w._cpu_count = 4
    got = []
    w.data_ready.connect(got.append)
    w.sample()
    names = {r["display_name"] for r in got[-1]["rows"]}
    assert names == {"real.exe"}   # System / MemCompression / Registry all excluded


# --- bar list ------------------------------------------------------------------

def test_list_builds_and_summarises(q_app):
    lst = HardwareBarList(I18nStrings("en_US"))
    lst.set_payload(_hpayload([_hrow("a.exe", 40, 100, 5), _hrow("b.exe", 2, 50)]))
    assert set(lst._rows.keys()) == {"a.exe", "b.exe"}
    assert "2 processes" in lst._summary.text()


def test_list_bar_relative_to_busiest(q_app):
    lst = HardwareBarList(I18nStrings("en_US"))
    lst.set_payload(_hpayload([_hrow("busy", 40), _hrow("light", 10)]))
    assert lst._rows["busy"]._bar._frac == 1.0            # 40/40
    assert abs(lst._rows["light"]._bar._frac - 0.25) < 0.01  # 10/40


def test_list_inplace_update_and_prune(q_app):
    lst = HardwareBarList(I18nStrings("en_US"))
    lst.set_payload(_hpayload([_hrow("a", 5), _hrow("b", 3)]))
    row_a = lst._rows["a"]
    lst.set_payload(_hpayload([_hrow("a", 9)]))
    assert lst._rows["a"] is row_a and "b" not in lst._rows
    assert lst._list_layout.count() == 2   # 1 row + 1 stretch


def test_list_empty_and_rdp(q_app):
    lst = HardwareBarList(I18nStrings("en_US"))
    lst.set_payload(_hpayload([]))
    assert lst._summary.text() == getattr(I18nStrings("en_US"), "HARDWARE_NO_DATA_MESSAGE", "No process data.")
    lst.set_unavailable("rdp")
    assert "Remote Desktop" in lst._summary.text() or "RDP" in lst._summary.text()


def test_gpu_column_shows_dash_when_unavailable(q_app):
    lst = HardwareBarList(I18nStrings("en_US"))
    lst.set_payload(_hpayload([_hrow("a.exe", 5, 100, 0)], gpu=False))
    assert lst._rows["a.exe"]._gpu.text() == "—"


def test_feed_rdp_degrades_without_thread(q_app, monkeypatch):
    import netspeedtray.utils.rdp_utils as rdp
    monkeypatch.setattr(rdp, "is_rdp_session", lambda: True)
    seen = []
    feed = HardwareFeed()
    feed.unavailable.connect(seen.append)
    feed.start()
    assert seen == ["rdp"] and feed._worker is None
    feed.teardown()
