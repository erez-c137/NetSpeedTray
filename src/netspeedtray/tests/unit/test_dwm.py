"""
Unit tests for the DWM Win11 chrome helper.

The actual ctypes -> dwmapi call (`_set_attr`) and the OS build probe (`_build`) are
monkeypatched, so these assert the dispatch logic (which attributes get set, with what
values, and when they're skipped) deterministically on any runner.
"""
from netspeedtray.utils import dwm


def _record(monkeypatch, build=22621):
    """Patch _build + _set_attr; return the list that captures (attr, value) calls."""
    calls = []
    monkeypatch.setattr(dwm, "_build", lambda: build)
    monkeypatch.setattr(dwm, "_set_attr", lambda hwnd, attr, value: (calls.append((attr, value)), True)[1])
    return calls


def test_apply_chrome_none_or_zero_hwnd_is_noop(monkeypatch):
    calls = _record(monkeypatch)
    dwm.apply_win11_chrome(None, dark=True)
    dwm.apply_win11_chrome(0, dark=False)
    assert calls == []


def test_apply_chrome_non_int_hwnd_is_swallowed(monkeypatch):
    _record(monkeypatch)
    # int("nope") raises -> must be swallowed, not propagate.
    dwm.apply_win11_chrome("nope", dark=True)


def test_apply_chrome_sets_dark_and_corners_no_mica(monkeypatch):
    calls = _record(monkeypatch, build=22621)
    dwm.apply_win11_chrome(123, dark=True, mica=False)
    attrs = [a for a, _ in calls]
    assert dwm._DWMWA_USE_IMMERSIVE_DARK_MODE in attrs
    assert dwm._DWMWA_WINDOW_CORNER_PREFERENCE in attrs
    assert dwm._DWMWA_SYSTEMBACKDROP_TYPE not in attrs  # mica off by default


def test_apply_chrome_enables_mica_when_requested(monkeypatch):
    calls = _record(monkeypatch, build=22621)
    dwm.apply_win11_chrome(123, dark=True, mica=True)
    attrs = [a for a, _ in calls]
    assert dwm._DWMWA_SYSTEMBACKDROP_TYPE in attrs


def test_dark_titlebar_value_follows_flag(monkeypatch):
    calls = _record(monkeypatch)
    dwm.set_dark_titlebar(123, True)
    assert calls[-1] == (dwm._DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    dwm.set_dark_titlebar(123, False)
    assert calls[-1] == (dwm._DWMWA_USE_IMMERSIVE_DARK_MODE, 0)


def test_dark_titlebar_falls_back_to_legacy_attr(monkeypatch):
    # Primary attr id fails; the 20H1 legacy id (19) must be tried next.
    monkeypatch.setattr(dwm, "_set_attr",
                        lambda hwnd, attr, value: attr == dwm._DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY)
    assert dwm.set_dark_titlebar(123, True) is True


def test_rounded_corners_skipped_on_win10(monkeypatch):
    called = []
    monkeypatch.setattr(dwm, "_build", lambda: 19045)  # Win10 22H2
    monkeypatch.setattr(dwm, "_set_attr", lambda *a: called.append(a) or True)
    assert dwm.set_rounded_corners(123) is False
    assert called == []  # never even attempts the call


def test_supports_mica_threshold(monkeypatch):
    monkeypatch.setattr(dwm, "_build", lambda: 22000)
    assert dwm.supports_mica() is False
    monkeypatch.setattr(dwm, "_build", lambda: 22621)
    assert dwm.supports_mica() is True


def test_set_mica_false_on_unsupported_build(monkeypatch):
    called = []
    monkeypatch.setattr(dwm, "_build", lambda: 22000)
    monkeypatch.setattr(dwm, "_set_attr", lambda *a: called.append(a) or True)
    assert dwm.set_mica(123, True) is False
    assert called == []
