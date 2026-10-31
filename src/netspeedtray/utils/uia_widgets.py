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


# --- Side-taskbar layout: the app-button span + the Widgets button (one FindAll pass) -------------
_IEL_CURRENT_AUTOMATION_ID = 29
_IEL_CURRENT_CLASS_NAME = 30

# What counts as "the apps": every pinned/running app button plus the shell buttons that sit in the
# same group (Start, Search, Task View). Matched on STABLE class names / AutomationIds, never on Name
# (localized, and it carries window titles). Probed on Win11 26200 (2026-10-21): StartButton,
# SearchButton, then one Taskbar.TaskListButtonAutomationPeer per app; tray icons are SystemTray.*,
# and our own widget shows up as a descendant too (it is an owned window of the taskbar).
_APP_BUTTON_CLASSES = frozenset({"Taskbar.TaskListButtonAutomationPeer"})
_APP_GROUP_IDS = frozenset({"StartButton", "SearchButton", "TaskViewButton"})
_WIDGETS_ID = "WidgetsButton"
# Every OTHER button on the bar counts as occupied too (rc.1 live test B4, 2026-10-22: the "..." app
# overflow button and any shell button a future build adds were invisible to the placement, so the
# widget could be parked on them). Recognised generically - "Button" in the class name or the
# AutomationId - except the tray's own icons (SystemTray.*, they ARE the tray end) and our own Qt
# windows (AutomationId "QApplication.<...>"), which a walk from the taskbar root also reaches.
_TRAY_CLASS_PREFIX = "SystemTray."
_QT_ID_PREFIX = "QApplication."


def _is_other_bar_button(cls: str, aid: str) -> bool:
    cls, aid = cls or "", aid or ""
    if cls.startswith(_TRAY_CLASS_PREFIX) or aid.startswith(_QT_ID_PREFIX) or aid == "SystemTrayIcon":
        return False
    return "button" in cls.lower() or "button" in aid.lower()


def taskbar_layout_from_elements(elements) -> Tuple[Optional[Tuple[int, int, int, int]],
                                                    Optional[Tuple[int, int, int, int]]]:
    """Pure: ``(apps_rect, widgets_rect)`` from ``(class_name, automation_id, (l, t, r, b))`` triples.

    apps_rect is the union of the app group's buttons PLUS every other non-tray button on the bar (the
    "..." overflow button, Copilot, ...); widgets_rect the WidgetsButton's own box, kept apart because
    where it sits (above the apps, or beside the tray with "Top"/left alignment) decides the free
    spans. Empty / zero-size elements (collapsed or off-screen buttons) are ignored. Either may be
    None."""
    apps = None
    widgets = None
    for cls, aid, rect in elements:
        try:
            left, top, right, bottom = (int(v) for v in rect)
        except Exception:
            continue
        if right <= left or bottom <= top:
            continue
        if aid == _WIDGETS_ID:
            if widgets is None:
                widgets = (left, top, right, bottom)
        elif cls in _APP_BUTTON_CLASSES or aid in _APP_GROUP_IDS or _is_other_bar_button(cls, aid):
            if apps is None:
                apps = (left, top, right, bottom)
            else:
                apps = (min(apps[0], left), min(apps[1], top), max(apps[2], right), max(apps[3], bottom))
    return apps, widgets


# Two buttons belong to one contiguous run when the gap between them is at most this (physical px).
# Win11's taskbar buttons sit edge to edge (44 px slots at 96 DPI, gap 0); the "..." overflow button
# follows the last app directly. A button further away (a shell button a future build parks by the
# tray, Widgets on the far side) is not part of the run.
APP_GROUP_MAX_GAP_PX = 8


def app_group_from_elements(elements, vertical: bool,
                            max_gap: int = APP_GROUP_MAX_GAP_PX) -> Optional[Tuple[int, int, int, int]]:
    """Pure: the CONTIGUOUS run of buttons that starts at Start - Start/Search/Task View, the app
    buttons, and any other non-tray button touching the run (the "..." overflow) - as one
    ``(l, t, r, b)`` rect in physical px, or None when there is no StartButton.

    Unlike ``taskbar_layout_from_elements``' union, a button separated from the run by more than
    ``max_gap`` along the bar is left out, so an unknown button far from the apps can't make the whole
    bar look full. ``vertical`` picks the bar's axis (y for a left/right bar, x for top/bottom). The
    run is extended from Start in both directions (a right-to-left layout puts Start at the far end)."""
    buttons = []
    start = None
    for cls, aid, rect in elements:
        try:
            left, top, right, bottom = (int(v) for v in rect)
        except Exception:
            continue
        if right <= left or bottom <= top or aid == _WIDGETS_ID:
            continue
        if not (cls in _APP_BUTTON_CLASSES or aid in _APP_GROUP_IDS or _is_other_bar_button(cls, aid)):
            continue
        r = (left, top, right, bottom)
        lo, hi = (top, bottom) if vertical else (left, right)
        buttons.append((lo, hi, r))
        if aid == "StartButton" and start is None:
            start = (lo, hi, r)
    if start is None:
        return None
    lo, hi = start[0], start[1]
    group = start[2]
    remaining = [b for b in buttons if b is not start]
    grew = True
    while grew:
        grew = False
        for b in list(remaining):
            if b[0] <= hi + max_gap and b[1] >= lo - max_gap:
                lo, hi = min(lo, b[0]), max(hi, b[1])
                r = b[2]
                group = (min(group[0], r[0]), min(group[1], r[1]), max(group[2], r[2]), max(group[3], r[3]))
                remaining.remove(b)
                grew = True
    return group


def _bstr(element: int, index: int) -> str:
    out = ctypes.c_void_p()
    _method(element, index, ctypes.POINTER(ctypes.c_void_p))(element, ctypes.byref(out))
    if not out.value:
        return ""
    try:
        return ctypes.wstring_at(out.value)
    finally:
        ctypes.windll.oleaut32.SysFreeString(ctypes.c_void_p(out.value))


# The taskbar's XAML island host. Walking from it (not from Shell_TrayWnd) keeps the search inside
# the taskbar's own buttons: our widget is an OWNED window of the taskbar, so a walk from the root
# also descends into it - asking a Qt window for its UIA tree switches Qt accessibility on in our
# own process for the rest of the session, and adds every widget element to the walk.
_XAML_HOST_CLASS = "Windows.UI.Composition.DesktopWindowContentBridge"


def _layout_root_hwnds(taskbar_hwnd: int):
    """The HWNDs to search from, best first: the XAML island host, then the taskbar root."""
    try:
        import win32gui
        host = win32gui.FindWindowEx(int(taskbar_hwnd), 0, _XAML_HOST_CLASS, None)
    except Exception:
        host = 0
    return [h for h in (host, int(taskbar_hwnd)) if h]


def query_taskbar_layout(taskbar_hwnd: int):
    """``(apps_rect, widgets_rect)`` in physical px for the taskbar ``taskbar_hwnd`` (Shell_TrayWnd),
    or None when UIA is unavailable. Blocking (cross-process); background thread only. Never raises.
    Searches the XAML island first; the whole taskbar only if that finds no app buttons."""
    full = query_taskbar_layout_full(taskbar_hwnd)
    return None if full is None else (full[0], full[1])


def overflow_button_from_elements(elements, vertical: bool) -> Optional[Tuple[int, int, int, int]]:
    """Pure: the app group's "..." overflow button (physical px), or None.

    It is the only way to reach the apps Win11 hides when the bar is full, so the widget must never
    cover it (rc.2 live test L2). Its class / AutomationId have not been probed live, so: an element
    whose class or id says "overflow"; else the non-app button (not Start/Search/Task View/an app,
    not Widgets, not the tray) that ENDS the contiguous Start-led run on the tray side."""
    for cls, aid, rect in elements:
        if "overflow" in (cls or "").lower() or "overflow" in (aid or "").lower():
            r = _int_rect(rect)
            if r is not None and _is_other_bar_button(cls, aid) and aid != _WIDGETS_ID:
                return r
    group = app_group_from_elements(elements, vertical)
    if group is None:
        return None
    hi = group[3] if vertical else group[2]
    best = None
    for cls, aid, rect in elements:
        r = _int_rect(rect)
        if r is None or aid == _WIDGETS_ID or cls in _APP_BUTTON_CLASSES or aid in _APP_GROUP_IDS:
            continue
        if not _is_other_bar_button(cls, aid):
            continue
        end = r[3] if vertical else r[2]
        inside = (r[1] >= group[1] and r[3] <= group[3]) if vertical else (r[0] >= group[0] and r[2] <= group[2])
        if inside and end == hi:
            best = r
    return best


def _int_rect(rect) -> Optional[Tuple[int, int, int, int]]:
    try:
        left, top, right, bottom = (int(v) for v in rect)
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def tray_extent_from_elements(elements) -> Optional[Tuple[int, int, int, int]]:
    """Pure: the union of the bar's tray buttons (class ``SystemTray.*``) in physical px, or None.

    On a Win11 SECONDARY taskbar (Shell_SecondaryTrayWnd) this is the only way to find its clock:
    there is no TrayNotifyWnd child, but the clock is a ``SystemTray.OmniButton`` in the XAML island
    (VM probe, Win11 26300, 2026-10-23: right side bar 0..1080, clock y 1036..1072). On the main bar
    it is the tray (chevron .. Show desktop), the same span TrayNotifyWnd reports."""
    tray = None
    for cls, _aid, rect in elements:
        if not (cls or "").startswith(_TRAY_CLASS_PREFIX):
            continue
        r = _int_rect(rect)
        if r is None:
            continue
        tray = r if tray is None else (min(tray[0], r[0]), min(tray[1], r[1]),
                                       max(tray[2], r[2]), max(tray[3], r[3]))
    return tray


# The Widgets button's VISIBLE content: its weather icon and text. Leaf XAML classes only - its
# NamedContainerAutomationPeer spans nearly the whole button box (VM, Win11 26300: box 1606..1758,
# container 1614..1750, "Sunny" ending at 1682), so it says nothing about where the text ends.
_WIDGETS_CONTENT_CLASSES = ("TextBlock", "Image", "Microsoft.UI.Xaml.Controls.AnimatedVisualPlayer")


def widgets_content_from_elements(elements) -> Optional[Tuple[int, int, int, int]]:
    """Pure: the union (physical px) of the icon / text elements inside the WidgetsButton's box - where
    its weather text really ends - or None when there is no button or nothing visible in it."""
    box = None
    for _cls, aid, rect in elements:
        if aid == _WIDGETS_ID:
            box = _int_rect(rect)
            break
    if box is None:
        return None
    content = None
    for cls, _aid, rect in elements:
        if cls not in _WIDGETS_CONTENT_CLASSES:
            continue
        r = _int_rect(rect)
        if r is None or r[0] < box[0] or r[2] > box[2] or r[1] < box[1] or r[3] > box[3]:
            continue
        content = r if content is None else (min(content[0], r[0]), min(content[1], r[1]),
                                             max(content[2], r[2]), max(content[3], r[3]))
    return content


def _layout_from_elements_ext(elements, vertical: Optional[bool]):
    """``_layout_from_elements_full`` plus the Widgets button's content rect (one walk, no new probe)."""
    return _layout_from_elements_full(elements, vertical) + (widgets_content_from_elements(elements),)


def _layout_from_elements_full(elements, vertical: Optional[bool]):
    apps, widgets = taskbar_layout_from_elements(elements)
    if vertical is None:   # infer the axis from the union: a left/right bar's run is taller than wide
        vertical = apps is not None and (apps[3] - apps[1]) > (apps[2] - apps[0])
    return (apps, widgets, app_group_from_elements(elements, vertical),
            overflow_button_from_elements(elements, vertical), tray_extent_from_elements(elements))


def query_taskbar_layout_full(taskbar_hwnd: int, vertical: Optional[bool] = None):
    """``(apps_rect, widgets_rect, app_group_rect, overflow_rect, tray_rect)`` in physical px (see
    ``app_group_from_elements`` / ``overflow_button_from_elements`` / ``tray_extent_from_elements``),
    or None when UIA is unavailable. Same cost and rules as ``query_taskbar_layout`` - ONE FindAll."""
    ole32 = ctypes.oledll.ole32
    initialized = False
    try:
        try:
            ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
            initialized = True
        except OSError as e:
            if getattr(e, "winerror", None) != _RPC_E_CHANGED_MODE:
                raise
        result = None
        for root in _layout_root_hwnds(taskbar_hwnd):
            elements = _probe_layout(ole32, root)
            result = None if elements is None else _layout_from_elements_ext(elements, vertical)
            if result is not None and result[0] is not None:
                return result
        return result
    except Exception as e:  # noqa: BLE001 - fail-safe
        logger.debug("Taskbar layout UIA probe failed: %s", e)
        return None
    finally:
        if initialized:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


def _probe_layout(ole32, hwnd: int):
    uia = ctypes.c_void_p()
    root = ctypes.c_void_p()
    true_cond = ctypes.c_void_p()
    kids = ctypes.c_void_p()
    try:
        clsid, iid = _GUID.parse(_CLSID_CUIAutomation), _GUID.parse(_IID_IUIAutomation)
        ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER, ctypes.byref(iid),
                               ctypes.byref(uia))
        _method(uia.value, _IUIA_ELEMENT_FROM_HANDLE, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p))(
            uia.value, hwnd, ctypes.byref(root))
        if not root.value:
            return None
        _method(uia.value, _IUIA_CREATE_TRUE_CONDITION, ctypes.POINTER(ctypes.c_void_p))(
            uia.value, ctypes.byref(true_cond))
        _method(root.value, _IEL_FIND_ALL, ctypes.c_int, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p))(root.value, _TREE_SCOPE_DESCENDANTS,
                                                 true_cond.value, ctypes.byref(kids))
        if not kids.value:
            return None
        n = ctypes.c_int(0)
        _method(kids.value, _IARR_LENGTH, ctypes.POINTER(ctypes.c_int))(kids.value, ctypes.byref(n))
        get = _method(kids.value, _IARR_GET, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        elements = []
        for i in range(n.value):
            kid = ctypes.c_void_p()
            try:
                get(kids.value, i, ctypes.byref(kid))
                if not kid.value:
                    continue
                r = _rect(kid.value)
                elements.append((_bstr(kid.value, _IEL_CURRENT_CLASS_NAME),
                                 _bstr(kid.value, _IEL_CURRENT_AUTOMATION_ID),
                                 (r.left, r.top, r.right, r.bottom)))
            except Exception:
                continue
            finally:
                _release(kid.value)
        return elements
    finally:
        for p in (kids, true_cond, root, uia):
            _release(p.value)
