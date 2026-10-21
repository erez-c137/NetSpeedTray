"""
The Win11 Widgets-button probe (#200) must never spawn a process (2.2.0-beta.1 lifecycle review).

It used to run a hidden ``powershell.exe`` every 30 s for the whole session, reached from
get_all_taskbar_info via the 1 Hz state watcher. It is now an in-process UI Automation call, skipped
entirely when the Widgets button is switched off (HKCU ...\\Explorer\\Advanced\\TaskbarDa = 0) or once
the one-time overlap nudge it exists for has been shown.
"""
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from netspeedtray.utils import taskbar_utils as tu


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(tu, "_widgets_state", {"rect": None, "ts": 0.0, "querying": False})
    monkeypatch.setattr(tu, "_widgets_probe_enabled", True, raising=False)
    yield


def _wait_refresh(timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with tu._widgets_lock:
            if not tu._widgets_state["querying"] and tu._widgets_state["ts"] > 0:
                return
        time.sleep(0.01)
    raise AssertionError("refresh never completed")


def _no_spawn(*_a, **_k):
    raise AssertionError("the Widgets probe spawned a process")


def test_refresh_spawns_no_process(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    monkeypatch.setattr(tu, "_widgets_button_enabled", lambda: True, raising=False)
    calls = []
    monkeypatch.setattr("netspeedtray.utils.uia_widgets.query_widgets_rect",
                        lambda hwnd: calls.append(hwnd) or (10, 1032, 150, 1080))
    tu.get_widgets_rect(0x1234)
    _wait_refresh()
    assert calls == [0x1234]
    assert tu.get_widgets_rect(0x1234) == (10, 1032, 150, 1080)


def test_widgets_off_in_registry_skips_the_probe(monkeypatch):
    monkeypatch.setattr(tu, "_widgets_button_enabled", lambda: False, raising=False)
    monkeypatch.setattr("netspeedtray.utils.uia_widgets.query_widgets_rect", _no_spawn)
    tu.get_widgets_rect(0x1234)
    _wait_refresh()
    assert tu.get_widgets_rect(0x1234) is None


def test_disabled_probe_never_starts_a_thread(monkeypatch):
    """After the one-time nudge has been shown nothing consumes the rect - no probe at all."""
    tu.set_widgets_probe_enabled(False)
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    assert tu.get_widgets_rect(0x1234) is None
    assert started == []


def test_registry_reader_treats_missing_value_as_on(monkeypatch):
    import winreg

    def _open(*_a, **_k):
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", _open)
    assert tu._widgets_button_enabled() is True


def test_native_probe_never_raises_on_a_bogus_hwnd():
    from netspeedtray.utils.uia_widgets import query_widgets_rect
    out = []
    t = threading.Thread(target=lambda: out.append(query_widgets_rect(0x7FFF0001)))
    t.start()
    t.join(10)
    assert out == [None]


def test_widget_turns_the_probe_off_once_the_nudge_was_shown():
    import types
    from unittest.mock import MagicMock
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake = types.SimpleNamespace(_widgets_nudge_shown=False, logger=MagicMock(),
                                 config={"widgets_overlap_nudge_shown": True})
    NetworkSpeedWidget._maybe_nudge_widgets_overlap(fake, None)
    assert tu._widgets_probe_enabled is False
