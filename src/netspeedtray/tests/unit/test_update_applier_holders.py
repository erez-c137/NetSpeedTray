"""
The portable swap must survive processes that still sit in the install folder after the previous
version has exited.

Found in a VM rehearsal of 2.1.7 -> 2.2.0-rc.7 (2026-11-10): 2.1.7 chdir's into its own folder and
spawns a PowerShell Widgets-button probe every ~35 s. That child inherits the CWD, can outlive 2.1.7
by 5-10 s, and Windows will not rename a folder that is any process's working directory - so the
applier's 8 s rename window ran out (WinError 32) and the update fell back to the guided copy.

The applier now (a) waits, bounded, for every process whose executable or working directory is in
the install folder - or, when that cannot be read, any child the previous version left behind - and
(b) retries the rename for longer, with backoff, naming the holder when it gives up.
"""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from netspeedtray.core import update_applier as ua


# --------------------------------------------------------------------------- fakes

class _AccessDenied(Exception):
    pass


class _NoSuchProcess(Exception):
    pass


class _FakeProc:
    def __init__(self, pid, *, name="x.exe", ppid=1, exe="", cwd=None, create_time=0.0,
                 cwd_error=None):
        self.info = {"pid": pid, "name": name, "ppid": ppid, "exe": exe, "create_time": create_time}
        self._cwd = cwd
        self._err = cwd_error

    def cwd(self):
        if self._err:
            raise self._err
        return self._cwd


def _fake_psutil(monkeypatch, procs):
    mod = SimpleNamespace(process_iter=lambda attrs=None: list(procs),
                          AccessDenied=_AccessDenied, NoSuchProcess=_NoSuchProcess)
    monkeypatch.setitem(sys.modules, "psutil", mod)
    return mod


# --------------------------------------------------------------------------- _install_holders

def test_holders_are_processes_running_from_or_sitting_in_the_install(tmp_path, monkeypatch):
    install = tmp_path / "NetSpeedTray"
    (install / "_internal").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    wait_pid, started_at, exited_at = 4208, 500.0, 1000.0
    _fake_psutil(monkeypatch, [
        _FakeProc(os.getpid(), cwd=str(install)),                        # the applier itself: never
        _FakeProc(0), _FakeProc(4),                                       # Idle / System: never
        _FakeProc(10, name="NetSpeedTray.exe", exe=str(install / ua.APP_EXE), cwd=str(other)),
        _FakeProc(11, name="powershell.exe", ppid=wait_pid, cwd=str(install)),
        _FakeProc(12, name="cmd.exe", cwd=str(install / "_internal")),    # anywhere inside counts
        _FakeProc(13, name="chrome.exe", cwd=str(other)),                 # not in it
        # Unreadable CWD: counted only if the previous version started it before it exited.
        _FakeProc(14, name="powershell.exe", ppid=wait_pid, create_time=999.0,
                  cwd_error=_AccessDenied()),
        _FakeProc(15, name="reused.exe", ppid=wait_pid, create_time=1001.0,
                  cwd_error=_AccessDenied()),                             # a recycled PID's child
        _FakeProc(16, name="svc.exe", ppid=777, cwd_error=_AccessDenied()),
        _FakeProc(17, name="gone.exe", cwd_error=_NoSuchProcess()),
        # An orphan of the previous version whose CWD is readable and elsewhere does not block.
        _FakeProc(18, name="nvidia-smi.exe", ppid=wait_pid, create_time=999.0, cwd=str(other)),
    ])
    got = {pid: why for pid, _name, why in
           ua._install_holders(str(install), wait_pid, exited_at, started_at)}
    assert got == {10: "runs from it", 11: "working directory is inside it",
                   12: "working directory is inside it",
                   14: "left running by the previous version"}


def test_a_stale_parent_pid_is_not_an_orphan_of_the_previous_version(tmp_path, monkeypatch):
    """Review M1: Windows never rewrites a process's parent PID after the parent dies, and PIDs are
    reused - csrss/wininit/winlogon report parents that died at boot. If the old app was given such
    a PID, those system processes (CWD unreadable) must not look like its orphans, or every update
    waits out the whole bound. Created before the old app started = not its child."""
    install = tmp_path / "NetSpeedTray"
    install.mkdir()
    wait_pid, started_at, exited_at = 740, 5000.0, 9000.0
    _fake_psutil(monkeypatch, [
        _FakeProc(600, name="csrss.exe", ppid=wait_pid, create_time=100.0,       # booted long ago
                  cwd_error=_AccessDenied()),
        _FakeProc(601, name="powershell.exe", ppid=wait_pid, create_time=6000.0,
                  cwd_error=_AccessDenied()),                                      # a real orphan
    ])
    got = [pid for pid, _n, _w in
           ua._install_holders(str(install), wait_pid, exited_at, started_at=started_at)]
    assert got == [601]
    # The old app's creation time could not be read: the orphan rule is off altogether.
    assert ua._install_holders(str(install), wait_pid, exited_at, started_at=None) == []


def test_apply_update_reads_the_old_apps_creation_time_before_waiting(tmp_path, monkeypatch):
    install = _make(tmp_path / "Apps", "NetSpeedTray", "old")
    staged = _make(tmp_path / "staging", "NetSpeedTray-9.9.9", "new")
    order, seen = [], []
    monkeypatch.setattr(ua, "_process_create_time",
                        lambda pid: order.append("create_time") or 1234.0, raising=False)
    monkeypatch.setattr(ua, "_wait_for_exit",
                        lambda pid, timeout=None: order.append("wait") or True)
    monkeypatch.setattr(ua, "relaunch", lambda exe: True)
    monkeypatch.setattr(ua, "_install_holders",
                        lambda inst, wait_pid=None, exited_at=None, started_at=None:
                        seen.append(started_at) or [], raising=False)
    assert ua.apply_update(str(install), 4208, str(staged)) == 0
    assert order == ["create_time", "wait"]
    assert seen == [1234.0]


def test_a_sibling_with_the_same_prefix_is_not_inside(tmp_path, monkeypatch):
    install = tmp_path / "NetSpeedTray"
    install.mkdir()
    sibling = tmp_path / "NetSpeedTray.previous-1794310119"
    sibling.mkdir()
    _fake_psutil(monkeypatch, [_FakeProc(20, cwd=str(sibling))])
    assert ua._install_holders(str(install)) == []


def test_no_psutil_means_no_known_holders(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)          # import psutil -> ImportError
    assert ua._install_holders(str(tmp_path)) == []


# --------------------------------------------------------------------------- the wait

def test_waits_until_a_left_behind_child_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(ua, "_HOLDER_POLL_SEC", 0.01, raising=False)
    calls = []

    def holders(install, wait_pid=None, exited_at=None, started_at=None):
        calls.append(wait_pid)
        return [(548, "powershell.exe", "working directory is inside it")] if len(calls) < 5 else []
    monkeypatch.setattr(ua, "_install_holders", holders, raising=False)
    assert ua._wait_for_install_holders(str(tmp_path), 4208, 1.0, timeout=5.0) == []
    assert calls == [4208] * 5


def test_the_wait_is_bounded_and_kills_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ua, "_HOLDER_POLL_SEC", 0.01, raising=False)
    stuck = [(9, "chrome.exe", "working directory is inside it")]
    monkeypatch.setattr(ua, "_install_holders", lambda *a, **k: stuck)
    t0 = time.monotonic()
    assert ua._wait_for_install_holders(str(tmp_path), 1, 1.0, timeout=0.2) == stuck
    assert time.monotonic() - t0 < 2.0


# --------------------------------------------------------------------------- the rename retry

def _locked_rename(monkeypatch, until: float):
    real = os.rename

    def rename(src, dst):
        if time.monotonic() < until:
            raise PermissionError(32, "The process cannot access the file because it is being "
                                      "used by another process")
        real(src, dst)
    monkeypatch.setattr(ua.os, "rename", rename)


def test_rename_outlasts_a_lock_longer_than_the_old_8s_window(tmp_path, monkeypatch):
    """The rehearsal's failure: the folder stayed locked past 8 s. A lock that lifts within the
    (longer) window must not fail the swap. Time is compressed, the ratio is the point."""
    monkeypatch.setattr(ua, "_SWAP_RETRY_STEP", 0.01)
    monkeypatch.setattr(ua, "_SWAP_RETRY_STEP_MAX", 0.05, raising=False)
    src, dst = tmp_path / "a", tmp_path / "b"
    src.mkdir()
    scale = 0.05                                       # 1 "second" = 50 ms
    _locked_rename(monkeypatch, time.monotonic() + 12 * scale)   # locked for 12 "s"
    ua._rename_with_retry(str(src), str(dst), timeout=ua._SWAP_RETRY_SEC * scale)
    assert dst.is_dir() and not src.exists()


def test_rename_locked_for_good_fails_within_the_bound_and_names_the_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(ua, "_SWAP_RETRY_STEP", 0.01)
    monkeypatch.setattr(ua, "_SWAP_RETRY_STEP_MAX", 0.02, raising=False)
    monkeypatch.setattr(ua, "_install_holders",
                        lambda *a, **k: [(548, "powershell.exe", "working directory is inside it")])
    src, dst = tmp_path / "a", tmp_path / "b"
    src.mkdir()
    _locked_rename(monkeypatch, float("inf"))
    t0 = time.monotonic()
    with pytest.raises(RuntimeError) as ei:
        ua._rename_with_retry(str(src), str(dst), timeout=0.2)
    assert time.monotonic() - t0 < 2.0
    assert "WinError 32" in str(ei.value) or "used by another process" in str(ei.value)
    assert "powershell.exe (pid 548" in str(ei.value)
    assert src.is_dir() and not dst.exists()


def test_the_retry_window_is_longer_than_the_one_that_failed_in_the_rehearsal():
    assert ua._SWAP_RETRY_SEC >= 20.0
    assert ua._HOLDER_WAIT_SEC >= 30.0


# --------------------------------------------------------------------------- apply_update

def _make(root, name, payload):
    d = root / name
    (d / "_internal").mkdir(parents=True)
    (d / ua.APP_EXE).write_text(payload, encoding="utf-8")
    (d / "_internal" / "data.bin").write_text(payload, encoding="utf-8")
    (d / ua.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")
    return d


def test_apply_update_waits_for_the_holders_of_the_previous_version(tmp_path, monkeypatch):
    """End to end through apply_update: the rename is LOCKED for as long as the fake holder lives,
    and the holder outlives the old 8 s window. The swap must wait it out and succeed."""
    install = _make(tmp_path / "Apps", "NetSpeedTray", "old")
    staged = _make(tmp_path / "staging", "NetSpeedTray-9.9.9", "new")
    monkeypatch.setattr(ua, "_wait_for_exit", lambda pid, timeout=None: True)
    monkeypatch.setattr(ua, "relaunch", lambda exe: True)
    monkeypatch.setattr(ua, "_HOLDER_POLL_SEC", 0.01, raising=False)
    monkeypatch.setattr(ua, "_SWAP_RETRY_STEP", 0.01)
    real_retry = ua._rename_with_retry                       # the rename alone cannot outlast it
    monkeypatch.setattr(ua, "_rename_with_retry",
                        lambda src, dst, timeout=None: real_retry(src, dst, timeout=0.1))
    release_at = time.monotonic() + 0.5
    seen = []

    def holders(inst, wait_pid=None, exited_at=None, started_at=None):
        seen.append((wait_pid, exited_at is not None))
        return ([(548, "powershell.exe", "working directory is inside it")]
                if time.monotonic() < release_at else [])
    monkeypatch.setattr(ua, "_install_holders", holders, raising=False)
    _locked_rename(monkeypatch, release_at)

    assert ua.apply_update(str(install), 4208, str(staged)) == 0
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "new"
    assert seen and seen[0] == (4208, True)


# --------------------------------------------------------------------------- real processes

@pytest.mark.skipif(sys.platform != "win32", reason="Windows CWD locking semantics")
def test_a_real_child_sitting_in_the_folder_is_found_and_waited_out(tmp_path):
    """No fakes: a real process whose CWD is the install folder blocks the rename (the defect),
    is reported by _install_holders, and once it exits the rename goes through."""
    install = _make(tmp_path / "Apps", "NetSpeedTray", "old")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                             cwd=str(install))
    try:
        time.sleep(0.3)
        with pytest.raises(OSError):
            os.rename(str(install), str(install) + ".probe")     # Windows refuses: it is a CWD
        found = ua._install_holders(str(install))
        assert any(pid == child.pid for pid, _n, _w in found), found
        assert ua._wait_for_install_holders(str(install), timeout=10.0) == []
        ua._rename_with_retry(str(install), str(install) + ".previous-1", timeout=5.0)
        assert os.path.isdir(str(install) + ".previous-1")
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(5)


# --------------------------------------------------------------------------- 2.2's own children
# The same hazard for 2.2 -> 2.2.x: whatever the app starts must not sit in its own folder (the
# app chdir's there at startup and a child inherits that CWD for as long as it lives).

def test_open_external_starts_in_the_system_folder(monkeypatch):
    from netspeedtray.utils import helpers
    calls = []
    monkeypatch.setattr(os, "startfile", lambda *a, **k: calls.append((a, k)), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    helpers.open_external("https://example.invalid/release")
    assert calls == [(("https://example.invalid/release",), {"cwd": helpers.neutral_cwd()})]
    assert helpers.neutral_cwd() and os.path.isdir(helpers.neutral_cwd())


def test_open_external_never_falls_back_to_a_child_in_the_inherited_cwd(monkeypatch):
    """Review L3: webbrowser's other candidates are browsers started as plain children in the
    app's CWD - the process that holds the install folder. On Windows a failed startfile is
    logged, not retried that way."""
    from netspeedtray.utils import helpers
    import webbrowser

    def boom(*a, **k):
        raise OSError("no handler")
    opened = []
    monkeypatch.setattr(os, "startfile", boom, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    helpers.open_external("https://example.invalid/x")
    helpers.open_external(r"C:\some\folder")
    assert opened == []


def test_neutral_cwd_falls_back_past_an_empty_or_bogus_systemroot(tmp_path, monkeypatch):
    """Review L4: an empty SystemRoot counts as missing; then %windir%, then GetSystemDirectoryW."""
    from netspeedtray.utils import helpers
    win = tmp_path / "Win"
    (win / "System32").mkdir(parents=True)
    monkeypatch.setenv("SystemRoot", "")
    monkeypatch.setenv("windir", str(win))
    assert helpers.neutral_cwd() == str(win / "System32")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "nope"))
    monkeypatch.setenv("windir", "  ")
    if sys.platform == "win32":
        got = helpers.neutral_cwd()                         # from GetSystemDirectoryW
        assert got and os.path.isdir(got) and got.lower().endswith("system32")


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe")
def test_the_hardware_helper_child_does_not_inherit_the_app_folder(tmp_path, monkeypatch):
    """nvidia-smi is run through StatsMonitorThread._run_child; a real child reports its CWD."""
    import threading
    from netspeedtray.core.monitor_thread import StatsMonitorThread
    from netspeedtray.utils import helpers
    monkeypatch.chdir(tmp_path)                               # stand-in for the app folder
    fake = SimpleNamespace(_child_lock=threading.Lock(), _is_running=True, _child=None)
    out = StatsMonitorThread._run_child(fake, [os.environ.get("ComSpec", "cmd.exe"), "/c", "cd"],
                                        10.0)
    assert os.path.normcase(out.strip()) == os.path.normcase(helpers.neutral_cwd())
