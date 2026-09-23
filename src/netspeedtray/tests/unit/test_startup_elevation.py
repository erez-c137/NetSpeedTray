"""
#308: Windows silently skips a Run-key entry whose exe is flagged "Run this program as an
administrator" - verified on a real logon on the maintainer's machine (the flagged entry never ran,
the unflagged control did, even with UAC set to elevate without prompting). Nothing reaches our log
because the app never starts, so a launch that DID happen must say it, and the support bundle must
carry the flags.

These use a real HKCU compatibility value for a made-up exe path, removed afterwards.
"""
import logging
import sys
import winreg
from unittest.mock import patch

import pytest

from netspeedtray.core import startup_manager
from netspeedtray.core.startup_manager import StartupManager, get_compat_layers

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")

_LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
_FAKE_EXE = r"C:\nst-test-308\NetSpeedTray.exe"


@pytest.fixture
def flagged_exe():
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _LAYERS)
    winreg.SetValueEx(key, _FAKE_EXE, 0, winreg.REG_SZ, "~ RUNASADMIN")
    winreg.CloseKey(key)
    yield _FAKE_EXE
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LAYERS, 0, winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, _FAKE_EXE)


def test_reads_the_run_as_admin_flag(flagged_exe):
    assert "RUNASADMIN" in get_compat_layers(flagged_exe)


def test_no_flags_reads_empty():
    assert get_compat_layers(r"C:\nst-test-308\not-flagged.exe") == ""


def _sync(exe, should_be_enabled, caplog):
    mgr = StartupManager()
    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "executable", exe), \
         patch.object(mgr, "_check_startup_registry", return_value=should_be_enabled), \
         patch.object(mgr, "_set_startup_registry") as set_reg, \
         caplog.at_level(logging.WARNING):
        mgr.synchronize_startup_task(should_be_enabled)
    set_reg.assert_not_called()      # never touches the real Run key
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_warns_when_startup_is_on_and_exe_needs_elevation(flagged_exe, caplog):
    warnings = _sync(flagged_exe, True, caplog)
    assert any("will NOT start at sign-in" in w for w in warnings), warnings


def test_silent_when_startup_is_off(flagged_exe, caplog):
    assert _sync(flagged_exe, False, caplog) == []


def test_silent_when_exe_is_not_flagged(caplog):
    assert _sync(r"C:\nst-test-308\not-flagged.exe", True, caplog) == []


def test_support_bundle_reports_elevation_and_flags(flagged_exe, q_app):
    from netspeedtray.utils import support_bundle
    with patch.object(sys, "executable", flagged_exe):
        info = support_bundle._collect_system_info({})
    assert "Process elevated:" in info
    assert "RUNASADMIN" in info
