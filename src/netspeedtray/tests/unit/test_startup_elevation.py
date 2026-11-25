"""
#308: Windows silently skips a Run-key entry whose exe is flagged "Run this program as an
administrator" - verified on a real logon on the maintainer's machine (the flagged entry never ran,
the unflagged control did, even with UAC set to elevate without prompting). Nothing reaches our log
because the app never starts, so a launch that DID happen must say it, and the support bundle must
carry the flags.

winreg is fully faked (fake_winreg.FakeReg): nothing touches the real registry - not the
compatibility flags, and not the Run or Task Manager StartupApproved keys (the launch sync reads the
latter, so the owner's real Task Manager state could change these tests' warnings).
"""
import logging
import sys
from unittest.mock import patch

import pytest

from netspeedtray.core import startup_manager
from netspeedtray.core.startup_manager import StartupManager, get_compat_layers
from netspeedtray.tests.unit import fake_winreg

_LAYERS = startup_manager._COMPAT_LAYERS_KEY
_FLAGGED = r"C:\nst-test-308\flagged\NetSpeedTray.exe"

_LAYERS = startup_manager._COMPAT_LAYERS_KEY
_FLAGGED = r"C:\nst-test-308\flagged\NetSpeedTray.exe"


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    return fake_winreg.install(monkeypatch)


@pytest.fixture
def flagged_exe(fake_registry):
    fake_registry.store[(_LAYERS, _FLAGGED)] = ("~ RUNASADMIN", 1)
    return _FLAGGED


def test_reads_the_run_as_admin_flag(flagged_exe):
    assert "RUNASADMIN" in get_compat_layers(flagged_exe)


def test_no_flags_reads_empty():
    assert get_compat_layers(r"C:\nst-test-308\not-flagged.exe") == ""


def _sync(exe, should_be_enabled, caplog):
    mgr = StartupManager()
    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "executable", exe), \
         patch.object(mgr, "_run_value_state", return_value="exact" if should_be_enabled else "missing"), \
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
