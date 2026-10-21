"""
A crash at logon used to leave no trace: the windowed exe has no stderr, logging starts only after
Qt/pywin32 load, and nothing armed faulthandler or a thread excepthook. The crash log fixes that.
The fault and exception cases run in a child process - they really crash it.
"""
import inspect
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import netspeedtray
from netspeedtray import crash_log

SRC = Path(netspeedtray.__file__).resolve().parents[1]


def _run_child(tmp_path, body: str) -> Path:
    env = dict(os.environ, APPDATA=str(tmp_path), PYTHONPATH=str(SRC))
    code = (
        "import ctypes, sys\n"
        # No Windows Error Reporting dialog for the deliberate fault.
        "ctypes.windll.kernel32.SetErrorMode(0x0002 | 0x0001)\n" if os.name == "nt" else ""
    ) + "from netspeedtray import crash_log\ncrash_log.enable()\n" + body
    subprocess.run([sys.executable, "-c", code], env=env, timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tmp_path / "NetSpeedTray" / crash_log.CRASH_LOG_FILENAME


def test_a_hard_fault_writes_python_stacks(tmp_path):
    log = _run_child(tmp_path, "import faulthandler\nfaulthandler._read_null()\n")
    text = log.read_text(encoding="utf-8", errors="replace")
    assert " start pid " in text
    assert "fatal" in text.lower() and "Current thread" in text, text


def test_an_unhandled_exception_on_the_main_thread_is_recorded(tmp_path):
    log = _run_child(tmp_path, "raise ImportError('DLL load failed while importing win32api')\n")
    text = log.read_text(encoding="utf-8")
    assert "unhandled exception" in text and "DLL load failed while importing win32api" in text


def test_an_unhandled_exception_on_a_thread_is_recorded(tmp_path):
    log = _run_child(tmp_path, (
        "import threading\n"
        "def boom(): raise RuntimeError('worker died')\n"
        "t = threading.Thread(target=boom, name='NST-Worker'); t.start(); t.join()\n"))
    text = log.read_text(encoding="utf-8")
    assert "in thread NST-Worker" in text and "worker died" in text


_HANDLED_AV = (
    "import ctypes\n"
    "k = ctypes.windll.kernel32\n"
    "k.IsBadReadPtr.argtypes = [ctypes.c_void_p, ctypes.c_size_t]\n"
    "k.IsBadReadPtr(0x7ff000000000, 1)\n"          # an access violation kernel32 handles itself
)


@pytest.mark.skipif(os.name != "nt", reason="Windows SEH")
def test_handled_access_violations_after_startup_are_not_logged_as_crashes(tmp_path):
    """faulthandler on Windows sees first-chance SEH exceptions, handled ones included: left on,
    a driver/PDH/WMI probe read like a crash in every support bundle (review)."""
    log = _run_child(tmp_path, "crash_log.startup_complete()\n" + _HANDLED_AV
                     + "raise RuntimeError('real one')\n")
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "startup complete" in text
    assert "fatal exception" not in text.lower(), text
    assert "real one" in text, "the exception hooks must stay on"


def test_monitor_ends_the_fault_window_once_started():
    src = (SRC / "monitor.py").read_text(encoding="utf-8")
    assert "crash_log.startup_complete()" in src
    assert "QTimer.singleShot(constants.app.CRASH_FAULT_WINDOW_MS, _startup_complete)" in src


def test_a_large_crash_log_is_rotated_at_start(tmp_path):
    path = tmp_path / crash_log.CRASH_LOG_FILENAME
    path.write_bytes(b"x" * (crash_log.MAX_BYTES + 1))
    crash_log._rotate(str(path))
    assert not path.exists() and (tmp_path / (crash_log.CRASH_LOG_FILENAME + ".1")).exists()
    small = tmp_path / "small.log"
    small.write_text("x", encoding="utf-8")
    crash_log._rotate(str(small))
    assert small.exists()


def test_monitor_arms_the_crash_log_before_loading_any_dll():
    src = (SRC / "monitor.py").read_text(encoding="utf-8")
    assert src.index("_crash_log.enable()") < src.index("import win32api")
    assert src.index("_crash_log.enable()") < src.index("from PyQt6")


@pytest.fixture
def restore_hooks():
    saved = sys.excepthook, threading.excepthook
    yield
    sys.excepthook, threading.excepthook = saved


def test_main_logs_unhandled_thread_exceptions(restore_hooks, caplog):
    import monitor
    monitor._install_exception_hooks(logging.getLogger("test.main"))
    with caplog.at_level(logging.CRITICAL):
        t = threading.Thread(target=lambda: 1 / 0, name="NST-Boom")
        t.start()
        t.join()
    assert any("thread NST-Boom" in r.getMessage() for r in caplog.records)


def test_a_startup_failure_stops_the_started_widget_without_saving():
    import monitor
    widget = MagicMock()
    monitor._stop_started_widget(widget, logging.getLogger("test"))
    widget.cleanup.assert_called_once_with(save_config=False)
    monitor._stop_started_widget(None, logging.getLogger("test"))   # nothing built: no-op


def _cleanup_stub():
    stub = MagicMock()
    stub._cleanup_done = False
    stub._cleanup_running = False
    stub._retired_latency_probes = []
    return stub


def test_cleanup_without_saving_still_stops_threads():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    stub = _cleanup_stub()
    NetworkSpeedWidget.cleanup(stub, save_config=False)
    stub.monitor_thread.stop.assert_called_once()
    stub.widget_state.cleanup.assert_called_once()
    stub.config_manager.save.assert_not_called()

    stub = _cleanup_stub()
    NetworkSpeedWidget.cleanup(stub)
    stub.config_manager.save.assert_called_once()


def test_a_failed_widget_init_cleans_up_what_it_started():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    assert "self.cleanup(save_config=False)" in inspect.getsource(NetworkSpeedWidget.__init__)
