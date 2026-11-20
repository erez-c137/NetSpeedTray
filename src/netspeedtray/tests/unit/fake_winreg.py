"""
An in-memory stand-in for the winreg calls core/startup_manager makes, so no test touches the real
registry - not the HKCU Run key (where the owner's real NetSpeedTray value lives), not
Explorer\\StartupApproved\\Run, not AppCompatFlags\\Layers.

Values live in ``store`` keyed by ``(key path, value name)`` -> ``(data, type)``; the hive is ignored
(HKCU and HKLM share one store). Every write is recorded in ``writes``. ``install(monkeypatch)``
patches the winreg module itself, so every winreg call in the process during the test goes here.
"""

_PATCHED = ("OpenKey", "CreateKey", "CloseKey", "QueryValueEx", "SetValueEx", "DeleteValue")


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

    def CreateKey(self, hive, path):
        return FakeKey(path)

    def CloseKey(self, k):
        pass

    def QueryValueEx(self, k, name):
        try:
            return self.store[(k.path, name)]
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, k, name, _reserved, kind, value):
        self.writes.append(("set", k.path, name, value))
        self.store[(k.path, name)] = (value, kind)

    def DeleteValue(self, k, name):
        self.writes.append(("del", k.path, name))
        try:
            del self.store[(k.path, name)]
        except KeyError:
            raise FileNotFoundError(name)

    def get(self, path, name):
        entry = self.store.get((path, name))
        return entry[0] if entry else None


def install(monkeypatch):
    """Route every winreg call to a fresh FakeReg for this test; returns it."""
    import winreg
    reg = FakeReg()
    for name in _PATCHED:
        monkeypatch.setattr(winreg, name, getattr(reg, name))
    return reg
