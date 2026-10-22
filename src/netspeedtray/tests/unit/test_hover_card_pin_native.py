"""The pinned hover card takes clicks - on a REAL native window (rc.1 live test B5, 2026-10-22).

The rest of the suite runs on Qt's offscreen platform and mocks the Win32 style calls, which is how
#406 shipped a pinned card that ignored every click: Qt drops all mouse events addressed to a window
created with ``Qt.WindowTransparentForInput``, even after the native WS_EX_TRANSPARENT style is
cleared. This test builds the real ``UsageFlyout`` in a subprocess on Qt's native "windows" platform,
with real window flags, and checks:

- hover: Qt's flag is absent, the native window is WS_EX_TRANSPARENT | LAYERED | NOACTIVATE and the
  system hit test (``WindowFromPoint``) looks straight through it;
- pinned: the hit test finds the card, a left click delivered through the real Win32 message queue
  reaches Qt and fires ``dismiss_requested``, and WS_EX_NOACTIVATE stays (the card never takes focus);
- unpinned again: click-through is back.

No pointer is moved and no input is synthesised system-wide: the click is posted to the card's own
window, and the hit test is a query. With the old ``WindowTransparentForInput`` flag the posted click
is swallowed (verified: the test fails on #406's code).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[3]

_PROBE = r"""
import ctypes, ctypes.wintypes as wt, json, sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QRect
app = QApplication(sys.argv)
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.usage_flyout import UsageFlyout

u = ctypes.windll.user32
u.WindowFromPoint.argtypes = [wt.POINT]; u.WindowFromPoint.restype = wt.HWND
u.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]; u.GetAncestor.restype = wt.HWND
u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]; u.GetWindowLongW.restype = ctypes.c_long
u.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
u.GetForegroundWindow.restype = wt.HWND
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

def cls(h):
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(h, b, 256)
    return b.value

def pump():
    for _ in range(40):
        app.processEvents()
        ctypes.windll.kernel32.Sleep(5)

from PyQt6.QtWidgets import QWidget
# Our own window right under the card: a hit that goes THROUGH the card lands here ("through"). A spot
# some other always-on-top / shell window covers (Start, a flyout, a toast - in a z-band above ours)
# hits neither ("occluded"): then the hit test says nothing about the card and is not asserted.
behind = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
behind.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

EN = I18nStrings("en_US")
card = UsageFlyout(EN, (1e6, 2e6), (3e6, 4e6), hint="hint", pin_hint=EN.HOVER_CARD_PIN_HINT)
if "--legacy" in sys.argv:
    card.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
fired = []
card.dismiss_requested.connect(lambda: fired.append(1))
screen = app.primaryScreen()
avail = screen.availableGeometry()

def free_spot():
    # A 460x360 spot nothing else covers (our behind window hit-tests as itself), else the centre.
    for fx, fy in ((0.25, 0.2), (0.6, 0.2), (0.25, 0.55), (0.6, 0.55), (0.05, 0.05), (0.4, 0.4)):
        r = QRect(avail.x() + int(avail.width() * fx), avail.y() + int(avail.height() * fy), 460, 360)
        behind.setGeometry(r)
        behind.show()
        pump()
        dpr = behind.devicePixelRatioF()
        ok = all((u.GetAncestor(u.WindowFromPoint(wt.POINT(int(x * dpr), int(y * dpr))), 2) or 0)
                 == int(behind.winId())
                 for x, y in ((r.center().x(), r.center().y()), (r.x() + 30, r.y() + 30),
                              (r.right() - 30, r.bottom() - 30)))
        if ok:
            return r
    return QRect(avail.center().x() - 230, avail.center().y() - 180, 460, 360)

spot = free_spot()
widget = QRect(spot.center().x() - 42, spot.bottom() - 60, 85, 48)   # the card opens above it
card.show_for(widget, avail, edge=None, screen=screen)
pump()
hwnd = int(card.winId())
card.raise_()
pump()
behind_hwnd = int(behind.winId())

def state():
    ex = u.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
    g = card.frameGeometry()
    dpr = card.devicePixelRatioF()
    cx, cy = int(g.width() / 2 * dpr), int(g.height() / 2 * dpr)
    pt = wt.POINT(int(g.x() * dpr) + cx, int(g.y() * dpr) + cy)
    hit = u.GetAncestor(u.WindowFromPoint(pt), 2) or 0          # GA_ROOT
    rel = "card" if hit == hwnd else ("through" if hit == behind_hwnd else "occluded")
    fg = u.GetForegroundWindow() or 0
    fired.clear()
    lp = (cy << 16) | cx
    u.PostMessageW(hwnd, 0x0201, 1, lp)                          # WM_LBUTTONDOWN, MK_LBUTTON
    u.PostMessageW(hwnd, 0x0202, 0, lp)                          # WM_LBUTTONUP
    pump()
    return {"transparent": bool(ex & 0x20), "layered": bool(ex & 0x80000),
            "noactivate": bool(ex & 0x08000000),
            "qt_tfi": bool(card.windowFlags() & Qt.WindowType.WindowTransparentForInput),
            "hit": rel, "hit_class": cls(hit) if hit else "", "clicked": bool(fired),
            "foreground_is_card": (u.GetForegroundWindow() or 0) == hwnd,
            "foreground_unchanged": (u.GetForegroundWindow() or 0) == fg,
            "visible": card.isVisible()}

out = {"hover": state()}
card.set_pinned(True)
pump()
out["pinned"] = state()
card.set_pinned(False)
pump()
out["unpinned"] = state()
card.hide()
print("RESULT" + json.dumps(out), flush=True)
"""


def _run_probe(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "QT_QPA_FONTDIR")}
    env.update({"QT_QPA_PLATFORM": "windows", "APPDATA": str(tmp_path), "LOCALAPPDATA": str(tmp_path),
                "PYTHONPATH": str(SRC_DIR), "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run([sys.executable, "-c", _PROBE, *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=120)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, f"probe failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    return json.loads(lines[-1][len("RESULT"):])


@pytest.mark.skipif(sys.platform != "win32", reason="needs real Win32 windows")
def test_pinned_card_takes_a_real_click_and_never_takes_focus(tmp_path):
    r = _run_probe(tmp_path)
    hover, pinned, unpinned = r["hover"], r["pinned"], r["unpinned"]

    assert hover["transparent"] and hover["layered"] and hover["noactivate"], hover
    # The hit test is a real desktop query; a spot another app's always-on-top window covers is
    # "occluded" and proves nothing either way (the posted click below still does).
    assert hover["hit"] in ("through", "occluded"), hover  # hover: the pointer falls through
    assert not hover["clicked"], hover               # ...and a stray message does not dismiss it

    assert not pinned["transparent"] and pinned["layered"] and pinned["noactivate"], pinned
    assert pinned["hit"] in ("card", "occluded"), pinned  # the click lands on the card, not behind
    assert pinned["clicked"], pinned                 # B5: Qt delivered it -> dismiss_requested
    assert not pinned["foreground_is_card"] and pinned["foreground_unchanged"], pinned
    assert pinned["visible"], pinned                 # pinning never re-created / hid the window

    assert not hover["qt_tfi"]                       # Qt's flag is what made the pinned card deaf
    assert unpinned["transparent"] and unpinned["hit"] != "card" and not unpinned["clicked"], unpinned
