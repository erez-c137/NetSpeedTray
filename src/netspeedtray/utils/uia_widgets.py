"""
In-process UI Automation probe for the Win11 taskbar's Widgets/weather button (#200), via raw ctypes COM.

This replaces a hidden ``powershell.exe`` that the 1 Hz state watcher spawned every 30 s for the
whole session (2.2.0-beta.1 lifecycle review). It computes exactly what that script computed:

* ``ElementFromHandle(Shell_TrayWnd)`` -> ``FindFirst(Descendants, AutomationId == "WidgetsButton")``
* the button's bounding rect, with its x-extent narrowed to the union of its visible descendants
  (``Width > 0`` and inside ``[button.left, button.right + 2]``), falling back to the button's own
  extent when that union is empty - i.e. the weather content, not the button's padding.

No pywin32 COM (IUIAutomation is not IDispatch) and no comtypes (it generates wrapper modules at
runtime, which a frozen build cannot do). The vtable slots below are fixed by the published
``UIAutomationClient.h`` ABI. Must run on a background thread: UIA calls are cross-process into
explorer.exe. Never raises - any failure is "no Widgets rect".
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Optional, Tuple

logger = logging.getLogger("NetSpeedTray.UiaWidgets")

_COINIT_MULTITHREADED = 0x0
_CLSCTX_INPROC_SERVER = 0x1
_RPC_E_CHANGED_MODE = -2147417850   # 0x80010106: this thread already has an STA - still usable
_UIA_AUTOMATION_ID_PROPERTY = 30011
_TREE_SCOPE_DESCENDANTS = 4
_VT_BSTR = 8

# Vtable indices (IUnknown = 0..2).
_RELEASE = 2
_IUIA_ELEMENT_FROM_HANDLE = 6
_IUIA_CREATE_TRUE_CONDITION = 21
_IUIA_CREATE_PROPERTY_CONDITION = 23
_IEL_FIND_FIRST = 5
_IEL_FIND_ALL = 6
_IEL_BOUNDING_RECT = 43
_IARR_LENGTH = 3
_IARR_GET = 4


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        g = cls()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g


class _VARIANT(ctypes.Structure):
    # 24 bytes on x64: vt + 3 reserved WORDs + a 16-byte union (we only ever store a BSTR pointer).
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("ptr", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


_CLSID_CUIAutomation = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"
_IID_IUIAutomation = "{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}"


def _method(ptr: int, index: int, *argtypes):
    """The ``index``-th vtable slot of COM object ``ptr`` as a callable (this pointer first)."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def _release(ptr: Optional[int]) -> None:
    if ptr:
        try:
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[_RELEASE])(ptr)
        except Exception:
            pass


def _rect(element: int) -> wintypes.RECT:
    r = wintypes.RECT()
    _method(element, _IEL_BOUNDING_RECT, ctypes.POINTER(wintypes.RECT))(element, ctypes.byref(r))
    return r


def query_widgets_rect(taskbar_hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """The Widgets button's visible content rect in physical px ``(left, top, right, bottom)``, or None.

    ``taskbar_hwnd`` is the Shell_TrayWnd root (the button lives under the taskbar frame, not
    TrayNotifyWnd). Blocking (tens of ms, cross-process); call from a background thread."""
    ole32 = ctypes.oledll.ole32
    oleaut32 = ctypes.windll.oleaut32
    oleaut32.SysAllocString.restype = ctypes.c_void_p
    oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
    oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]

    initialized = False
    try:
        try:
            ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
            initialized = True
        except OSError as e:
            if getattr(e, "winerror", None) != _RPC_E_CHANGED_MODE:
                raise
        return _probe(ole32, oleaut32, int(taskbar_hwnd))
    except Exception as e:  # noqa: BLE001 - fail-safe: any problem means "no Widgets rect"
        logger.debug("Widgets UIA probe failed (treating as no Widgets button): %s", e)
        return None
    finally:
        if initialized:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


def _probe(ole32, oleaut32, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    uia = ctypes.c_void_p()
    root = ctypes.c_void_p()
    cond = ctypes.c_void_p()
    true_cond = ctypes.c_void_p()
    button = ctypes.c_void_p()
    kids = ctypes.c_void_p()
    bstr = None
    try:
        clsid, iid = _GUID.parse(_CLSID_CUIAutomation), _GUID.parse(_IID_IUIAutomation)
        ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER, ctypes.byref(iid),
                               ctypes.byref(uia))
        _method(uia.value, _IUIA_ELEMENT_FROM_HANDLE, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p))(
            uia.value, hwnd, ctypes.byref(root))
        if not root.value:
            return None

        bstr = oleaut32.SysAllocString("WidgetsButton")
        var = _VARIANT(vt=_VT_BSTR, ptr=bstr)
        _method(uia.value, _IUIA_CREATE_PROPERTY_CONDITION, ctypes.c_int, _VARIANT,
                ctypes.POINTER(ctypes.c_void_p))(uia.value, _UIA_AUTOMATION_ID_PROPERTY, var,
                                                 ctypes.byref(cond))
        _method(root.value, _IEL_FIND_FIRST, ctypes.c_int, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p))(root.value, _TREE_SCOPE_DESCENDANTS, cond.value,
                                                 ctypes.byref(button))
        if not button.value:
            return None

        b = _rect(button.value)
        cl, cr = b.right, b.left      # inverted on purpose: any qualifying child widens it
        _method(uia.value, _IUIA_CREATE_TRUE_CONDITION, ctypes.POINTER(ctypes.c_void_p))(
            uia.value, ctypes.byref(true_cond))
        _method(button.value, _IEL_FIND_ALL, ctypes.c_int, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p))(button.value, _TREE_SCOPE_DESCENDANTS,
                                                 true_cond.value, ctypes.byref(kids))
        if kids.value:
            n = ctypes.c_int(0)
            _method(kids.value, _IARR_LENGTH, ctypes.POINTER(ctypes.c_int))(kids.value, ctypes.byref(n))
            get = _method(kids.value, _IARR_GET, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
            for i in range(n.value):
                kid = ctypes.c_void_p()
                try:
                    get(kids.value, i, ctypes.byref(kid))
                    if not kid.value:
                        continue
                    k = _rect(kid.value)
                    if (k.right - k.left) > 0 and k.left >= b.left and k.right <= (b.right + 2):
                        cl = min(cl, k.left)
                        cr = max(cr, k.right)
                finally:
                    _release(kid.value)
        if cr <= cl:
            cl, cr = b.left, b.right
        if cr > cl and b.bottom > b.top:
            return (int(cl), int(b.top), int(cr), int(b.bottom))
        return None
    finally:
        for p in (kids, button, true_cond, cond, root, uia):
            _release(p.value)
        if bstr:
            oleaut32.SysFreeString(bstr)
