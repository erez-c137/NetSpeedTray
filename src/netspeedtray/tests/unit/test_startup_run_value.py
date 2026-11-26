"""
The Run-key value (core/startup_manager), with winreg FULLY faked - never the real HKCU Run key.

2.2.2 combined review C2 follow-up (the verifier's probe, folded in):
- the value is ``"<exe>" --autostart``; a legacy value for THIS exe is rewritten (enabled) or removed
  (Start with Windows off), once - idempotent after that;
- Task Manager's StartupApproved switch is never touched by the sync;
- a value naming ANOTHER exe that still exists on disk (an installed copy, while a portable one
  runs - or the other way round) is left alone and logged; one naming an exe that is gone (the app
  moved) is taken over; the explicit Settings toggle may take over, and says so in the log.
"""
import logging

import pytest

from netspeedtray.core import startup_manager as sm

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = sm._STARTUP_APPROVED_KEY
NAME = "NetSpeedTray"
INSTALLED = r"C:\Program Files\NetSpeedTray\NetSpeedTray.exe"
PORTABLE = r"C:\Users\X\Downloads\NetSpeedTray-portable\NetSpeedTray.exe"
NEW = f'"{INSTALLED}" --autostart'
LEGACY = f'"{INSTALLED}"'


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeReg:
    def __init__(self):
        self.store, self.writes = {}, []

    def OpenKey(self, hive, path, *a):
        return FakeKey(path)

    def CloseKey(self, k):
        pass

    def QueryValueEx(self, k, name):
        try:
            return self.store[(k.path, name)]
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, k, name, _r, kind, value):
        self.writes.append(("set", k.path, name, value))
        self.store[(k.path, name)] = (value, kind)

    def DeleteValue(self, k, name):
        self.writes.append(("del", k.path, name))
        try:
            del self.store[(k.path, name)]
        except KeyError:
            raise FileNotFoundError(name)


@pytest.fixture
def reg(monkeypatch):
    """Every winreg call startup_manager makes goes to a dict; INSTALLED and PORTABLE both exist."""
    fake = FakeReg()
    for n in ("OpenKey", "CloseKey", "QueryValueEx", "SetValueEx", "DeleteValue"):
        monkeypatch.setattr(sm.winreg, n, getattr(fake, n))
    monkeypatch.setattr(sm.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sm.sys, "executable", INSTALLED)
    fake.existing = {INSTALLED.lower(), PORTABLE.lower()}
    monkeypatch.setattr(sm, "_exe_exists", lambda p: str(p).lower() in fake.existing)
    return fake


def _value(reg):
    entry = reg.store.get((RUN, NAME))
    return entry[0] if entry else None


def test_legacy_value_is_rewritten_once_when_enabled(reg):
    reg.store[(RUN, NAME)] = (LEGACY, 1)
    m = sm.StartupManager()
    m.synchronize_startup_task(True)
    assert _value(reg) == NEW
    n = len(reg.writes)
    m.synchronize_startup_task(True)
    assert len(reg.writes) == n                          # idempotent
    assert m.is_startup_enabled() is True


def test_a_task_manager_disable_is_never_touched(reg):
    reg.store[(RUN, NAME)] = (LEGACY, 1)
    reg.store[(APPROVED, NAME)] = (bytes([3] + [0] * 11), 3)
    sm.StartupManager().synchronize_startup_task(True)
    assert reg.store[(APPROVED, NAME)][0][0] == 3
    assert not any(w[1] == APPROVED for w in reg.writes)


def test_no_value_and_off_writes_nothing(reg):
    sm.StartupManager().synchronize_startup_task(False)
    assert reg.writes == []


@pytest.mark.parametrize("value", [LEGACY, NEW, f'{INSTALLED} --autostart', f'"{INSTALLED.upper()}"'])
def test_off_removes_a_value_for_this_exe_in_any_format(reg, value):
    reg.store[(RUN, NAME)] = (value, 1)
    sm.StartupManager().synchronize_startup_task(False)
    assert _value(reg) is None


def test_off_leaves_another_exes_value(reg):
    reg.store[(RUN, NAME)] = (f'"{PORTABLE}" --autostart', 1)
    sm.StartupManager().synchronize_startup_task(False)
    assert _value(reg) == f'"{PORTABLE}" --autostart' and reg.writes == []


def test_a_portable_copy_never_takes_over_an_installed_copys_value(reg, monkeypatch, caplog):
    monkeypatch.setattr(sm.sys, "executable", PORTABLE)
    reg.store[(RUN, NAME)] = (NEW, 1)
    with caplog.at_level(logging.WARNING):
        sm.StartupManager().synchronize_startup_task(True)
    assert _value(reg) == NEW and reg.writes == []
    assert "another NetSpeedTray" in caplog.text


def test_an_installed_copy_never_takes_over_a_portable_value_either(reg):
    reg.store[(RUN, NAME)] = (f'"{PORTABLE}"', 1)
    sm.StartupManager().synchronize_startup_task(True)
    assert _value(reg) == f'"{PORTABLE}"' and reg.writes == []


def test_a_value_for_an_exe_that_is_gone_is_taken_over(reg, monkeypatch):
    """The app moved (reinstalled elsewhere, the portable folder renamed): the old target is gone."""
    gone = r"D:\Old\NetSpeedTray\NetSpeedTray.exe"
    reg.store[(RUN, NAME)] = (f'"{gone}"', 1)
    sm.StartupManager().synchronize_startup_task(True)
    assert _value(reg) == NEW


def test_the_settings_toggle_may_take_over_and_says_so(reg, monkeypatch, caplog):
    monkeypatch.setattr(sm, "_clear_startup_approved", lambda name: None)
    monkeypatch.setattr(sm.sys, "executable", PORTABLE)
    reg.store[(RUN, NAME)] = (NEW, 1)
    with caplog.at_level(logging.INFO):
        assert sm.StartupManager().toggle_startup(True) is True
    assert _value(reg) == f'"{PORTABLE}" --autostart'
    assert "takes over" in caplog.text


@pytest.mark.parametrize("value,exe", [
    (NEW, INSTALLED), (LEGACY, INSTALLED), (f"{INSTALLED} --autostart", INSTALLED),
    (f"{INSTALLED}", INSTALLED), ('"C:\\a b\\x.exe" --autostart --other', "C:\\a b\\x.exe"),
])
def test_the_exe_a_value_names(value, exe):
    assert sm.run_value_exe(value) == exe
