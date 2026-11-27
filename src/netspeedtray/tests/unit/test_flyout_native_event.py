"""Flyout.nativeEvent: the crash the 2.2.2 live VM session found (2026-11-18, F1).

The first-run tip and the "what's new" card killed the app the moment they showed on a real desktop:
an access violation in PyQt's QtCore.pyd, then 0xc000041d. The cause was the Flyout's nativeEvent
override returning ``super().nativeEvent(...)``: under PyQt 6.10 the base call returns ``(False,
None)``, and handing that ``None`` back as the native result access-violates in the binding during
the window's creation (the first WM_WINDOWPOSCHANGING). Every Flyout was affected - the tip, what's
new, the update offer, data-cap alerts - because the override sits on the class. The widget's own
nativeEvent learned the same lesson in v2.0 (``test_native_event_resume.py``).

The offscreen suite never delivers native events, so two layers here:
- the return contract, called directly with a synthesised MSG (any platform);
- a REAL native window in a subprocess on Qt's "windows" platform (the native lane): the cards are
  built and shown exactly as startup_notices / the update offer do, then real messages are sent to
  them. On the broken code the probe dies with 0xC0000005 before printing anything.
"""
import ctypes
import json
import os
import subprocess
import sys
import types
from ctypes import wintypes
from pathlib import Path

import pytest

from netspeedtray.views import flyout as flyout_mod
from netspeedtray.views.flyout import Flyout

SRC_DIR = Path(__file__).resolve().parents[3]


def _msg(message: int, wparam: int = 0, lparam: int = 0, hwnd: int = 0):
    m = wintypes.MSG()
    m.hWnd = hwnd
    m.message = message
    m.wParam = wparam
    m.lParam = lparam
    return m, ctypes.addressof(m)


def _fake(own_hwnd: int = 0x1234):
    fake = types.SimpleNamespace(_prev_foreground=0)
    # Calling winId() from inside nativeEvent re-enters native window creation: it must not be used.
    fake.winId = lambda: (_ for _ in ()).throw(AssertionError("winId() called from nativeEvent"))
    fake.internalWinId = lambda: own_hwnd
    return fake


def test_native_event_returns_the_unhandled_tuple_never_super(monkeypatch):
    """(False, 0) for every message - the base implementation is never called (it returns
    (False, None), which crashes PyQt)."""
    monkeypatch.setattr(flyout_mod.QWidget, "nativeEvent",
                        lambda *a: (_ for _ in ()).throw(AssertionError("super().nativeEvent called")))
    fake = _fake()
    for message in (0x0046, 0x0001, 0x0200, flyout_mod._WM_ACTIVATE):
        _ref, addr = _msg(message, hwnd=0x1234)
        result = Flyout.nativeEvent(fake, b"windows_generic_MSG", addr)
        assert result == (False, 0)
        assert isinstance(result[1], int)


def test_native_event_remembers_the_previous_foreground_window():
    fake = _fake(own_hwnd=0x1234)
    _ref, addr = _msg(flyout_mod._WM_ACTIVATE, wparam=1, lparam=0x9999, hwnd=0x1234)
    assert Flyout.nativeEvent(fake, b"windows_generic_MSG", addr) == (False, 0)
    assert fake._prev_foreground == 0x9999


def test_native_event_ignores_deactivation_and_its_own_window():
    fake = _fake(own_hwnd=0x1234)
    _ref, addr = _msg(flyout_mod._WM_ACTIVATE, wparam=0, lparam=0x9999, hwnd=0x1234)   # WA_INACTIVE
    Flyout.nativeEvent(fake, b"windows_generic_MSG", addr)
    _ref2, addr2 = _msg(flyout_mod._WM_ACTIVATE, wparam=1, lparam=0x1234, hwnd=0x1234)
    Flyout.nativeEvent(fake, b"windows_generic_MSG", addr2)
    assert fake._prev_foreground == 0


def test_native_event_foreign_type_and_null_message_are_safe():
    fake = _fake()
    assert Flyout.nativeEvent(fake, b"something_else", 0) == (False, 0)
    assert Flyout.nativeEvent(fake, b"windows_generic_MSG", None) == (False, 0)
    assert Flyout.nativeEvent(fake, b"windows_generic_MSG", 0) == (False, 0)


_PROBE = r"""
import ctypes, json, sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect
app = QApplication(sys.argv)
from netspeedtray.views.flyout import Flyout

u = ctypes.windll.user32
u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]

def pump():
    for _ in range(20):
        app.processEvents()
        ctypes.windll.kernel32.Sleep(5)

screen = app.primaryScreen()
avail = screen.availableGeometry()
widget = QRect(avail.right() - 200, avail.bottom() - 60, 150, 40)
out = {}
cards = {
    # The first-run tip / what's new (startup_notices._build): focusable, two buttons, no timeout.
    "tip": dict(action_text="Open Monitor", secondary_text="Got it", close_text="Close",
                auto_dismiss_ms=0, focusable=True),
    # The update offer / data-cap alerts: the plain no-focus card with an X.
    "offer": dict(action_text="Update", close_text="Close", auto_dismiss_ms=0),
}
for name, kw in cards.items():
    card = Flyout("Title", "A message body long enough to wrap once or twice.", **kw)
    card.show_for(widget, avail, edge=None, screen=screen, tail=(name == "tip"), activate=False)
    pump()
    hwnd = int(card.winId())
    g = card.geometry()
    lp = ((g.height() // 2) << 16) | (g.width() // 2)
    u.SendMessageW(hwnd, 0x0200, 0, lp)          # WM_MOUSEMOVE
    u.SendMessageW(hwnd, 0x0084, 0, 0)           # WM_NCHITTEST
    u.SendMessageW(hwnd, 0x0020, hwnd, 1)        # WM_SETCURSOR
    u.SendMessageW(hwnd, 0x0006, 1, 0x7777)      # WM_ACTIVATE (WA_ACTIVE, from a fake previous window)
    u.PostMessageW(hwnd, 0x0201, 1, lp)          # WM_LBUTTONDOWN on the body (not a button)
    u.PostMessageW(hwnd, 0x0202, 0, lp)          # WM_LBUTTONUP
    pump()
    out[name] = {"visible": card.isVisible(), "prev": card._prev_foreground}
    card._prev_foreground = 0                    # never hand a fake hwnd the foreground on close
    card.close()
    pump()
print("RESULT" + json.dumps(out), flush=True)
"""


@pytest.mark.native_desktop
@pytest.mark.skipif(sys.platform != "win32", reason="needs real Win32 windows")
def test_cards_survive_real_native_events(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "QT_QPA_FONTDIR")}
    env.update({"QT_QPA_PLATFORM": "windows", "APPDATA": str(tmp_path), "LOCALAPPDATA": str(tmp_path),
                "PYTHONPATH": str(SRC_DIR), "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run([sys.executable, "-X", "faulthandler", "-c", _PROBE], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, (f"probe died ({proc.returncode & 0xFFFFFFFF:#x}):\n{proc.stdout[-2000:]}\n"
                   f"{proc.stderr[-4000:]}")
    r = json.loads(lines[-1][len("RESULT"):])
    assert r["tip"]["visible"] and r["offer"]["visible"], r
    assert r["tip"]["prev"] == 0x7777 and r["offer"]["prev"] == 0x7777, r
