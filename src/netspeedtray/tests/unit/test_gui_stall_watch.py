"""
GUI-thread stall logger (#218 diagnostics, 2.2.1).

The widget is an owned window of Shell_TrayWnd, so Windows attaches the two threads' input queues:
a stall on our GUI thread can delay the taskbar's own right-clicks. Steady state is clean; this
catches the occasional stall in the field. It rides on the existing 1 s safety-net tick (no new
timer, no new Python thread): the tick measures its own lateness, and a faulthandler watchdog
re-armed on every tick dumps the stacks into the crash log when the GUI thread is stuck for
seconds.
"""
import inspect
import logging
import os
import subprocess
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import netspeedtray
from netspeedtray import constants, crash_log
from netspeedtray.core import gui_stall_watch as gsw
from netspeedtray.core.gui_stall_watch import GuiStallWatch

SRC = Path(netspeedtray.__file__).resolve().parents[1]
T = constants.timeouts


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def _watch(clock=None, dumps=False, **kw):
    """``dumps``: what the crash log answers when asked whether the watchdog wrote a dump."""
    clock = clock or FakeClock()
    arm = MagicMock(return_value=True)
    cancel = MagicMock()
    note = MagicMock()
    logger = MagicMock()
    w = GuiStallWatch(interval_ms=1000, clock=clock, arm=arm, cancel=cancel, note=note,
                      dump_written=MagicMock(return_value=dumps),
                      context=kw.pop("context", lambda: "Monitor[overview]"), logger=logger, **kw)
    return w, clock, arm, cancel, note, logger


def _warnings(logger):
    return [c.args[0] % c.args[1:] for c in logger.warning.call_args_list]


def _infos(logger):
    return [c.args[0] % c.args[1:] for c in logger.info.call_args_list]


# --------------------------------------------------------------------- constants

def test_constants_exist_and_are_sane():
    assert T.GUI_STALL_LATE_MS == 250
    assert 2.0 <= T.GUI_STALL_DUMP_TIMEOUT_SEC <= 3.0 + 1e-9
    # The dump must not fire on a tick that is merely late by the logging threshold.
    assert T.GUI_STALL_DUMP_TIMEOUT_SEC * 1000 > T.STATE_WATCHER_INTERVAL_MS + T.GUI_STALL_LATE_MS
    assert T.GUI_STALL_LOG_MAX_PER_WINDOW >= 1
    assert T.GUI_STALL_LOG_WINDOW_SEC == 3600
    assert 30 <= T.GUI_STALL_CLOCK_JUMP_SEC <= 60
    assert T.GUI_STALL_CLOCK_JUMP_SEC > T.GUI_STALL_DUMP_TIMEOUT_SEC


# --------------------------------------------------------------------- lateness detection

def test_on_time_and_coarse_jitter_ticks_log_nothing():
    w, clock, *_rest, logger = _watch()
    w.start()
    for step in (1.0, 1.05, 0.95, 1.2, 1.249):   # Qt coarse timers wander ~5%; 249 ms late is fine
        clock.advance(step)
        w.on_tick()
    assert logger.warning.call_count == 0
    assert w.stall_count == 0


def test_a_tick_late_past_the_threshold_logs_one_warning_with_context():
    w, clock, _arm, _cancel, note, logger = _watch()
    w.start()
    clock.advance(1.0); w.on_tick()
    clock.advance(1.0 + 0.400); w.on_tick()   # 400 ms late
    msgs = _warnings(logger)
    assert len(msgs) == 1, msgs
    assert "400 ms" in msgs[0] and "Monitor[overview]" in msgs[0]
    assert crash_log.CRASH_LOG_FILENAME not in msgs[0]   # too short for a stack dump
    note.assert_called_once()                            # only the "watchdog on" announcement
    assert "watchdog on" in note.call_args.args[0]
    assert w.stall_count == 1


def test_the_first_tick_only_sets_the_baseline():
    w, clock, *_rest, logger = _watch()
    w.start()
    clock.advance(30.0)   # start() -> first tick: e.g. the widget's first show took a while
    w.on_tick()
    assert logger.warning.call_count == 0


def test_a_long_stall_points_at_the_crash_log_dump_and_notes_it_there():
    w, clock, arm, _c, note, logger = _watch(dumps=True)
    w.start()
    clock.advance(1.0); w.on_tick()
    clock.advance(5.0); w.on_tick()   # 4 s late; the 3 s watchdog fired meanwhile
    msgs = _warnings(logger)
    assert len(msgs) == 1 and "4000 ms" in msgs[0]
    assert crash_log.CRASH_LOG_FILENAME in msgs[0]
    assert note.call_count >= 1 and any("4000 ms" in c.args[0] for c in note.call_args_list)


def test_a_long_stall_without_a_dump_in_the_crash_log_claims_none():
    """Near the 3 s edge (or with the watchdog's wait not counting some of the gap) the clock says
    "dumped" but the file did not grow: no pointer, no note, no dump counted."""
    w, clock, _arm, _c, note, logger = _watch(dumps=False)
    w.start()
    clock.advance(1.0); w.on_tick()
    clock.advance(5.0); w.on_tick()
    msgs = _warnings(logger)
    assert len(msgs) == 1 and "4000 ms" in msgs[0]
    assert crash_log.CRASH_LOG_FILENAME not in msgs[0]
    assert not any("ended" in c.args[0] for c in note.call_args_list)
    assert w._session_dumps == 0


def test_no_crash_log_pointer_when_the_watchdog_could_not_be_armed():
    w, clock, arm, _c, _n, logger = _watch(dumps=True)
    arm.return_value = False   # crash log not open: no dump can exist
    w.start()
    clock.advance(1.0); w.on_tick()
    clock.advance(5.0); w.on_tick()
    msgs = _warnings(logger)
    assert len(msgs) == 1 and crash_log.CRASH_LOG_FILENAME not in msgs[0]


@pytest.mark.parametrize("dumps", [False, True])
def test_a_gap_past_the_clock_jump_threshold_is_one_info_line_not_a_stall(dumps):
    """Modern Standby freezes a desktop app without PBT_APMSUSPEND, and a clock can jump: a gap
    over GUI_STALL_CLOCK_JUMP_SEC is one INFO line, never a stall line or count, and the next tick
    is measured from a fresh baseline."""
    w, clock, _arm, _c, note, logger = _watch(dumps=dumps)
    w.start()
    clock.advance(1.0); w.on_tick()
    clock.advance(T.GUI_STALL_CLOCK_JUMP_SEC + 1.0); w.on_tick()
    assert logger.warning.call_count == 0
    assert w.stall_count == 0
    infos = _infos(logger)
    assert len(infos) == 1 and "gap" in infos[0]
    assert (crash_log.CRASH_LOG_FILENAME in infos[0]) is dumps
    assert not any("ended" in c.args[0] for c in note.call_args_list)
    clock.advance(1.0); w.on_tick()   # fresh baseline: on time
    assert logger.warning.call_count == 0
    # Just under the threshold it is still a stall.
    clock.advance(T.GUI_STALL_CLOCK_JUMP_SEC - 0.5); w.on_tick()
    assert len(_warnings(logger)) == 1


def test_context_failure_never_breaks_the_tick():
    def boom():
        raise RuntimeError("widget gone")
    w, clock, *_rest, logger = _watch(context=boom)
    w.start()
    clock.advance(1.0); w.on_tick()
    clock.advance(2.0); w.on_tick()
    assert len(_warnings(logger)) == 1


# --------------------------------------------------------------------- rate limiting

def test_stall_lines_are_capped_per_window_and_summarised():
    w, clock, *_rest, logger = _watch()
    cap = T.GUI_STALL_LOG_MAX_PER_WINDOW
    w.start()
    clock.advance(1.0); w.on_tick()
    for i in range(cap + 5):
        clock.advance(1.5 + i * 0.01); w.on_tick()   # each 500+ ms late
    assert len(_warnings(logger)) == cap
    assert w.stall_count == cap + 5
    # The window rolls over (quiet on-time ticks): one summary of what was not logged, with the
    # worst one (i = cap + 4: 500 + (cap + 4) * 10 ms).
    for _ in range(T.GUI_STALL_LOG_WINDOW_SEC):
        clock.advance(1.0); w.on_tick()
    msgs = _warnings(logger)
    summary = [m for m in msgs if "not logged" in m]
    assert len(summary) == 1, msgs
    assert "5 more" in summary[0] and "%d ms" % (500 + (cap + 4) * 10) in summary[0]
    # ...and a new window logs individual lines again.
    clock.advance(1.0); w.on_tick()
    clock.advance(2.0); w.on_tick()
    assert len(_warnings(logger)) == cap + 2


def test_stop_summarises_suppressed_stalls_and_the_session():
    w, clock, *_rest, logger = _watch()
    w.start()
    clock.advance(1.0); w.on_tick()
    for _ in range(T.GUI_STALL_LOG_MAX_PER_WINDOW + 2):
        clock.advance(2.0); w.on_tick()
    w.stop()
    assert any("2 more" in m and "not logged" in m for m in _warnings(logger))
    assert any("GUI stalls this session" in m for m in _infos(logger))


def test_stop_without_stalls_is_quiet():
    w, clock, *_rest, logger = _watch()
    w.start()
    clock.advance(1.0); w.on_tick()
    w.stop()
    assert logger.warning.call_count == 0 and logger.info.call_count == 0


# --------------------------------------------------------------------- faulthandler arm / cancel

def test_start_only_sets_the_baseline_and_every_tick_re_arms():
    """A slow startup must never be dumped before the watch has announced itself: start() arms
    nothing; the first tick arms (and writes the announcement), and so does every later one."""
    w, clock, arm, cancel, note, _l = _watch()
    w.start()
    arm.assert_not_called()
    note.assert_not_called()
    for _ in range(3):
        clock.advance(1.0); w.on_tick()
    assert arm.call_count == 3
    note.assert_called_once()
    for c in arm.call_args_list:
        assert c.args == (T.GUI_STALL_DUMP_TIMEOUT_SEC,)
    cancel.assert_not_called()


def test_stop_cancels_and_later_ticks_do_nothing():
    w, clock, arm, cancel, _n, logger = _watch()
    w.start()
    w.stop()
    cancel.assert_called_once()
    n = arm.call_count
    clock.advance(10.0); w.on_tick()
    assert arm.call_count == n and logger.warning.call_count == 0
    w.stop()   # idempotent: one cancel per stop is fine, but never raises
    assert cancel.call_count >= 1


def test_suspend_cancels_the_watchdog_and_the_sleep_is_not_a_stall():
    w, clock, arm, cancel, _n, logger = _watch()
    w.start()
    clock.advance(1.0); w.on_tick()
    w.suspend()
    cancel.assert_called_once()
    n = arm.call_count
    clock.advance(0.5); w.on_tick()   # a tick between the notice and the actual sleep
    assert arm.call_count == n          # not re-armed while suspended
    clock.advance(3600.0)               # asleep for an hour
    w.resume()
    assert arm.call_count == n          # resume handling may be slow: the next tick arms
    clock.advance(1.0); w.on_tick()     # first tick after resume: baseline + arm
    assert arm.call_count == n + 1
    clock.advance(1.0); w.on_tick()
    assert logger.warning.call_count == 0 and logger.info.call_count == 0


def test_a_suspend_without_resume_notice_heals_on_its_own():
    w, clock, arm, _c, _n, logger = _watch()
    w.start()
    clock.advance(1.0); w.on_tick()
    w.suspend()
    clock.advance(T.GUI_STALL_SUSPEND_HOLD_SEC + 1); w.on_tick()   # no resume message ever came
    n = arm.call_count
    clock.advance(1.0); w.on_tick()
    assert arm.call_count == n + 1   # watching again
    clock.advance(2.0); w.on_tick()
    assert len(_warnings(logger)) == 1


def test_dumps_are_capped_per_window_then_resume_after_it_rolls():
    """A dump is a few KB and the crash log rotates only at start: past the cap the watchdog is
    left disarmed (lines still logged), and re-armed once the window rolls over."""
    w, clock, arm, cancel, _n, logger = _watch(dumps=True)
    cap = T.GUI_STALL_DUMP_MAX_PER_WINDOW
    w.start()
    clock.advance(1.0); w.on_tick()
    for _ in range(cap):
        clock.advance(5.0); w.on_tick()   # each one dumped
    assert sum(crash_log.CRASH_LOG_FILENAME in m for m in _warnings(logger)) == cap
    n = arm.call_count
    cancel.assert_called()                 # the armed watchdog was dropped at the cap
    clock.advance(5.0); w.on_tick()        # still logged, but no dump and no re-arm
    assert arm.call_count == n
    assert crash_log.CRASH_LOG_FILENAME not in _warnings(logger)[-1]
    for _ in range(T.GUI_STALL_LOG_WINDOW_SEC):
        clock.advance(1.0); w.on_tick()
    assert arm.call_count > n              # a new window: watching again


def test_dumps_are_capped_per_session():
    w, clock, arm, *_ = _watch(dumps=True)
    w.start()
    clock.advance(1.0); w.on_tick()
    # One dumped stall per window (the window cap never trips), more than the session allows.
    for _ in range(T.GUI_STALL_DUMP_MAX_PER_SESSION + 5):
        clock.advance(5.0); w.on_tick()
        for _ in range(T.GUI_STALL_LOG_WINDOW_SEC):
            clock.advance(1.0); w.on_tick()
    assert w._session_dumps == T.GUI_STALL_DUMP_MAX_PER_SESSION
    n = arm.call_count
    for _ in range(5):
        clock.advance(1.0); w.on_tick()
    assert arm.call_count == n   # session cap reached: never re-armed


def test_arm_failure_is_swallowed():
    w, clock, arm, *_ = _watch()
    arm.side_effect = OSError("bad fd")
    w.start()
    clock.advance(1.0); w.on_tick()   # must not raise


# --------------------------------------------------------------------- crash_log arm / cancel

def test_crash_log_arm_uses_dump_traceback_later_on_the_crash_file(monkeypatch):
    fake_fh = MagicMock()
    fake_fh.fileno.side_effect = OSError("not a real file")   # size unknown: still armed
    fh_mod = MagicMock()
    monkeypatch.setattr(crash_log, "faulthandler", fh_mod)
    monkeypatch.setattr(crash_log, "_file", fake_fh)
    assert crash_log.arm_stall_dump(2.5) is True
    fh_mod.dump_traceback_later.assert_called_once_with(2.5, repeat=False, file=fake_fh)
    crash_log.cancel_stall_dump()
    fh_mod.cancel_dump_traceback_later.assert_called_once()


def test_stall_dump_written_reads_the_file_not_the_clock(tmp_path, monkeypatch):
    """arm_stall_dump records the crash log's size; stall_dump_written is True only once the file
    grew by a faulthandler "Timeout (" block - an exception record appended meanwhile is not one."""
    fh_mod = MagicMock()
    monkeypatch.setattr(crash_log, "faulthandler", fh_mod)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "NetSpeedTray").mkdir()
    fh = open(crash_log.crash_log_path(), "a", encoding="utf-8", buffering=1)
    try:
        fh.write("2026-11-18 10:00:00 start pid 1\n")
        monkeypatch.setattr(crash_log, "_file", fh)
        monkeypatch.setattr(crash_log, "_stall_armed_size", None)
        assert crash_log.stall_dump_written() is False          # never armed
        assert crash_log.arm_stall_dump(3.0) is True
        assert crash_log.stall_dump_written() is False          # nothing written
        fh.write("2026-11-18 10:00:01 unhandled exception in thread X\n")
        assert crash_log.stall_dump_written() is False          # grew, but not a dump
        fh.write("Timeout (0:00:03)!\nThread 0x1 (most recent call first):\n")
        assert crash_log.stall_dump_written() is True
        assert crash_log.arm_stall_dump(3.0) is True            # re-arm: measured from here
        assert crash_log.stall_dump_written() is False
        crash_log.cancel_stall_dump()
        assert crash_log.stall_dump_written() is False
    finally:
        fh.close()


def test_crash_log_arm_without_a_crash_file_is_a_no_op(monkeypatch):
    fh_mod = MagicMock()
    monkeypatch.setattr(crash_log, "faulthandler", fh_mod)
    monkeypatch.setattr(crash_log, "_file", None)
    assert crash_log.arm_stall_dump(2.5) is False
    fh_mod.dump_traceback_later.assert_not_called()


def test_startup_complete_does_not_cancel_the_stall_watchdog(monkeypatch):
    """The startup fault handler (first 5 s) and the stall watchdog are separate faulthandler
    features; turning the first off must leave the second armed."""
    fh_mod = MagicMock()
    fh_mod.is_enabled.return_value = True
    monkeypatch.setattr(crash_log, "faulthandler", fh_mod)
    crash_log.startup_complete()
    fh_mod.disable.assert_called_once()
    fh_mod.cancel_dump_traceback_later.assert_not_called()


def test_a_real_stall_dumps_the_gui_stack_into_the_crash_log(tmp_path):
    """End to end with the real faulthandler, in a child process: a 1.5 s block on the main thread
    with a 0.5 s watchdog writes a 'Timeout' block naming the blocking frame; a cancelled watchdog
    writes nothing."""
    env = dict(os.environ, APPDATA=str(tmp_path), PYTHONPATH=str(SRC))
    code = (
        "import time\n"
        "from netspeedtray import crash_log\n"
        "crash_log.enable(); crash_log.startup_complete()\n"
        "def stuck_in_a_slot():\n"
        "    time.sleep(1.5)\n"
        "assert crash_log.arm_stall_dump(0.5)\n"
        "assert not crash_log.stall_dump_written()\n"
        "stuck_in_a_slot()\n"
        "crash_log.note('written=%s' % crash_log.stall_dump_written())\n"
        "crash_log.arm_stall_dump(0.5); crash_log.cancel_stall_dump()\n"
        "time.sleep(1.0)\n"
    )
    subprocess.run([sys.executable, "-c", code], env=env, timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    text = (tmp_path / "NetSpeedTray" / crash_log.CRASH_LOG_FILENAME).read_text(
        encoding="utf-8", errors="replace")
    assert text.count("Timeout (") == 1, text
    assert "stuck_in_a_slot" in text
    assert "written=True" in text


# --------------------------------------------------------------------- cost: no timer, no thread

def test_the_watch_owns_no_timer_and_no_thread():
    src = inspect.getsource(gsw)
    for forbidden in ("QTimer", "threading", "QThread", "time.sleep", "Thread("):
        assert forbidden not in src, forbidden
    before = threading.active_count()
    w, clock, *_ = _watch()
    w.start()
    for _ in range(5):
        clock.advance(1.0); w.on_tick()
    w.stop()
    assert threading.active_count() == before


def test_widget_drives_the_watch_from_the_existing_safety_net_tick():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    src = inspect.getsource(NetworkSpeedWidget._setup_timers)
    hook = "self._state_watcher_timer.timeout.connect(self._stall_watch.on_tick)"
    refresh = "self._state_watcher_timer.timeout.connect(self._execute_refresh)"
    assert hook in src and refresh in src
    assert src.index(hook) < src.index(refresh)   # measures lateness before the refresh runs
    assert "constants.timeouts.STATE_WATCHER_INTERVAL_MS" in src
    # No new timer for this feature: the wiring between the import and the refresh hook builds none.
    block = src[src.index("GuiStallWatch"):src.index(refresh)]
    assert "QTimer" not in block and "Thread" not in block


def test_cleanup_stops_the_watch_first():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    stub = MagicMock()
    stub._cleanup_done = False
    stub._cleanup_running = False
    stub._retired_latency_probes = []
    order = []
    stub._stall_watch.stop.side_effect = lambda: order.append("stall")
    stub._mirrors = None
    stub.position_manager.stop_monitoring.side_effect = lambda: order.append("pm")
    NetworkSpeedWidget.cleanup(stub, save_config=False)
    stub._stall_watch.stop.assert_called_once()
    assert order[0] == "stall", order


def test_power_notices_suspend_and_resume_the_watch(monkeypatch):
    import ctypes
    from netspeedtray.views.widget import main as M
    monkeypatch.setattr(M.QTimer, "singleShot", lambda *_a, **_k: None)
    fake = types.SimpleNamespace(logger=MagicMock(), _on_environment_changed=MagicMock(),
                                 _stall_watch=MagicMock())

    def send(wparam):
        m = M._NativeMSG()
        m.message = M._WM_POWERBROADCAST
        m.wParam = wparam
        assert M.NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", ctypes.addressof(m)) == (False, 0)

    send(M._PBT_APMSUSPEND)
    fake._stall_watch.suspend.assert_called_once()
    send(M._PBT_APMRESUMEAUTOMATIC)
    fake._stall_watch.resume.assert_called_once()


def test_pbt_apmsuspend_is_pinned():
    from netspeedtray.views.widget import main as M
    assert M._PBT_APMSUSPEND == 4
    assert M._PBT_APMRESUMESUSPEND == 7 and M._PBT_APMRESUMEAUTOMATIC == 0x12


def test_session_end_suspends_the_watch_first():
    """Sign-out blocks the GUI thread on purpose (the essential saves): not a stall to log or dump."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    order = []
    fake = types.SimpleNamespace(logger=MagicMock(), _stall_watch=MagicMock(),
                                 widget_state=MagicMock(), config_manager=MagicMock(), config={},
                                 _capture_position_config=lambda: order.append("position"))
    fake._stall_watch.suspend.side_effect = lambda: order.append("suspend")
    fake.config_manager.save.side_effect = lambda *_a: order.append("save")
    NetworkSpeedWidget._save_session_essentials(fake, "end-session")
    fake._stall_watch.suspend.assert_called_once()
    assert order[0] == "suspend", order


def test_power_notices_without_a_watch_are_harmless(monkeypatch):
    import ctypes
    from netspeedtray.views.widget import main as M
    monkeypatch.setattr(M.QTimer, "singleShot", lambda *_a, **_k: None)
    fake = types.SimpleNamespace(logger=MagicMock(), _on_environment_changed=MagicMock())
    m = M._NativeMSG()
    m.message = M._WM_POWERBROADCAST
    m.wParam = M._PBT_APMSUSPEND
    assert M.NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", ctypes.addressof(m)) == (False, 0)
    fake.logger.debug.assert_not_called()   # no swallowed AttributeError either


# --------------------------------------------------------------------- support bundle

def test_a_stall_dump_ships_scrubbed_in_the_support_bundle(q_app, tmp_path, monkeypatch):
    import zipfile
    from netspeedtray.utils import support_bundle, helpers
    home = str(Path.home().resolve())
    base = tmp_path / "appdata"
    base.mkdir()
    monkeypatch.setattr(support_bundle, "get_app_data_path", lambda: base)
    (base / crash_log.CRASH_LOG_FILENAME).write_text(
        "2026-11-18 10:00:00 GUI stall watchdog on (3.0 s)\n"
        "Timeout (0:00:03)!\n"
        "Thread 0x00001234 (most recent call first):\n"
        f'  File "{home}\\AppData\\Local\\Programs\\NetSpeedTray\\x.py", line 7 in _execute_refresh\n'
        "2026-11-18 10:00:04 GUI stall of 4000 ms ended (the Timeout block above)\n",
        encoding="utf-8")
    dest = tmp_path / "bundle.zip"
    support_bundle.build_support_bundle(dest, {})
    with zipfile.ZipFile(dest) as zf:
        text = zf.read(f"logs/{crash_log.CRASH_LOG_FILENAME}").decode("utf-8")
    assert "Timeout (0:00:03)!" in text and "_execute_refresh" in text
    assert home.lower() not in text.lower()


@pytest.mark.parametrize("name", ["\u0141ukasz \u017b\u00f3\u0142\u0107", "\u05e9\u05dc\u05d5\u05dd"])
def test_escaped_non_ascii_user_paths_are_scrubbed_from_a_real_dump(q_app, tmp_path, monkeypatch, name):
    """faulthandler writes non-ASCII as \\xhh / \\uhhhh / \\Uhhhhhhhh, so a "C:\\Users\\\u0141ukasz" home
    shows up as C:\\Users\\\\u0141ukasz in a stall dump - the plain-path rule never sees it. The bundle
    scrub must catch that spelling (path and bare user name)."""
    import zipfile
    from netspeedtray.utils import support_bundle
    import netspeedtray.utils.config as cfg
    home = tmp_path / name
    home.mkdir()
    (home / "m.py").write_text(
        "import faulthandler, sys\n"
        "def stuck():\n"
        "    faulthandler.dump_traceback(open(sys.argv[1], 'w'), all_threads=False)\n"
        "stuck()\n", encoding="utf-8")
    dump = tmp_path / "dump.txt"
    subprocess.run([sys.executable, "-I", str(home / "m.py"), str(dump)], timeout=60, check=True)
    raw = dump.read_text(encoding="ascii")
    escaped = cfg.ObfuscatingFormatter.faulthandler_escape(name)
    assert escaped in raw and name not in raw   # the real dump really is escaped like this

    monkeypatch.setattr(cfg.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("USERNAME", name)
    base = tmp_path / "appdata"
    base.mkdir()
    monkeypatch.setattr(support_bundle, "get_app_data_path", lambda: base)
    (base / crash_log.CRASH_LOG_FILENAME).write_text(
        "Timeout (0:00:03)!\n" + raw + "user %s was here\n" % escaped, encoding="utf-8")
    dest = tmp_path / "bundle.zip"
    support_bundle.build_support_bundle(dest, {})
    with zipfile.ZipFile(dest) as zf:
        text = zf.read(f"logs/{crash_log.CRASH_LOG_FILENAME}").decode("utf-8")
    assert "line 3 in stuck" in text
    for part in escaped.split("\\"):
        if len(part) >= 4:
            assert part.lower() not in text.lower(), text


def test_monitor_window_reports_its_current_tab():
    from netspeedtray.views.monitor.window import MonitorWindow
    fake = types.SimpleNamespace(_stack=MagicMock(), _descriptors=[types.SimpleNamespace(tab_id="overview"),
                                                                   types.SimpleNamespace(tab_id="hardware")])
    fake._stack.currentIndex.return_value = 1
    assert MonitorWindow.current_tab_id(fake) == "hardware"
    fake._stack.currentIndex.return_value = 7
    assert MonitorWindow.current_tab_id(fake) == "?"
