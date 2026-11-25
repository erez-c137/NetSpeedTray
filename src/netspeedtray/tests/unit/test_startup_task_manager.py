"""
#308, the real cause: Task Manager's Startup apps had NetSpeedTray **Disabled**, while our own
"Start with Windows" toggle read ON. Windows keeps that switch in a separate key
(Explorer\\StartupApproved\\Run, first byte 02 = enabled, 03 = disabled); our Run value still existed,
so Settings reported ON and the app never started. The reporter fixed it by enabling it in Task Manager.

- The Settings toggle must show the truth (Task-Manager-disabled = off).
- Turning it ON in Settings is an explicit request and must clear Task Manager's disable.
- The launch-time sync must NOT silently undo a Task Manager disable - it only warns.

winreg is fully faked (fake_winreg.FakeReg): no test touches the real HKCU Run or StartupApproved
keys. These used real values under a throwaway name; a crash mid-test or a parallel run could leave
junk there, next to the owner's real NetSpeedTray value.
"""
import logging
import sys
import winreg
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.core import startup_manager as sm
from netspeedtray.core.startup_manager import StartupManager, get_startup_approved_state
from netspeedtray.tests.unit import fake_winreg

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_NAME = "NST-Test-308-TaskManager"
_ENABLED = bytes([2] + [0] * 11)
_DISABLED = bytes([3, 0, 0, 0, 0x6C, 0x2B, 0x01, 0xAA, 0x8D, 0xCB, 0xDA, 0x01])
_REG = None


def _set(path, name, kind, value):
    _REG.store[(path, name)] = (value, kind)


def _get(path, name):
    return _REG.get(path, name)


@pytest.fixture
def mgr(monkeypatch):
    """A StartupManager over a fake registry (every winreg call goes to a dict)."""
    global _REG
    _REG = fake_winreg.install(monkeypatch)
    with patch.object(constants.app, "APP_NAME", _NAME):
        yield StartupManager()


def _register(m):
    _set(_RUN, _NAME, winreg.REG_SZ, m._get_executable_path())


def test_reads_task_manager_states(mgr):
    assert get_startup_approved_state(_NAME) == "not set"
    _set(_APPROVED, _NAME, winreg.REG_BINARY, _ENABLED)
    assert get_startup_approved_state(_NAME) == "enabled"
    _set(_APPROVED, _NAME, winreg.REG_BINARY, _DISABLED)
    assert get_startup_approved_state(_NAME) == "disabled"


def test_toggle_reads_off_when_task_manager_disabled_it(mgr):
    _register(mgr)
    _set(_APPROVED, _NAME, winreg.REG_BINARY, _DISABLED)
    assert mgr.is_startup_enabled() is False          # 2.1.6 said True: the #308 lie


def test_toggle_reads_on_when_task_manager_allows_it(mgr):
    _register(mgr)
    assert mgr.is_startup_enabled() is True
    _set(_APPROVED, _NAME, winreg.REG_BINARY, _ENABLED)
    assert mgr.is_startup_enabled() is True


def test_turning_it_on_in_settings_clears_the_task_manager_disable(mgr):
    _register(mgr)
    _set(_APPROVED, _NAME, winreg.REG_BINARY, _DISABLED)
    assert mgr.toggle_startup(True) is True
    assert get_startup_approved_state(_NAME) != "disabled"


def test_turning_it_off_removes_both_entries(mgr):
    _register(mgr)
    _set(_APPROVED, _NAME, winreg.REG_BINARY, _ENABLED)
    mgr.toggle_startup(False)
    assert _get(_RUN, _NAME) is None and _get(_APPROVED, _NAME) is None


def test_launch_sync_respects_a_task_manager_disable_and_warns(mgr, caplog):
    _register(mgr)
    _set(_APPROVED, _NAME, winreg.REG_BINARY, _DISABLED)
    with patch.object(sys, "frozen", True, create=True), caplog.at_level(logging.WARNING):
        mgr.synchronize_startup_task(True)
    assert get_startup_approved_state(_NAME) == "disabled"      # not silently re-enabled
    assert any("Task Manager" in r.getMessage() for r in caplog.records), caplog.text
