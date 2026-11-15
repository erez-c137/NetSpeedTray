"""Lazy loading of pywin32's COM client (``win32com.client``) and the ``wmi`` module.

Neither is imported at startup any more (memory plan item 2.5: ~3 MB USS together). They load on
the first call that needs them, on the thread that makes it - which must have initialised COM
(``pythoncom.CoInitialize()``) first, as every caller in the app does. Who loads them, and when:

* ``win32com.client``: the StatsMonitorThread, only when its temperature / power search reaches
  the LHM 0.9.4 / OpenHardwareMonitor WMI namespace (the LHM web server found nothing) or the
  legacy ACPI ``MSAcpi_ThermalZoneTemperature`` fallback - so only with CPU/GPU temperatures or
  power switched on. ``wmi`` imports it too.
* ``wmi``: the battery reads (a machine that reports a battery, with the hover's battery rows or
  the power tiles on), the Check connection sheet's adapter details, and the default-gateway
  lookup only when the IP Helper route table could not be read at all.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
from types import ModuleType
from typing import Optional

logger = logging.getLogger("NetSpeedTray.COM")

_lock = threading.RLock()
_client_unavailable = False


def _redirect_gen_path() -> None:
    """A frozen install under read-only Program Files makes win32com's default gen_py cache dir
    (inside the bundle, ``_internal\\win32com\\gen_py``) unwritable. Importing win32com.client then
    crashes while (re)building its COM cache there - and it raises FileNotFoundError, NOT
    ImportError, so an ImportError-only guard let it through and the app failed to start (#210).
    Point the generated-code cache at a writable per-user temp dir BEFORE importing
    win32com.client. (We only ever use late-bound GetObject("winmgmts:...") for WMI, which doesn't
    need this cache at all; the redirect just keeps the import from crashing.)"""
    import win32com
    try:
        gen_dir = os.path.join(tempfile.gettempdir(), "netspeedtray_win32com_gen")
        os.makedirs(gen_dir, exist_ok=True)
        win32com.__gen_path__ = gen_dir
    except OSError:
        pass


def win32com_client() -> Optional[ModuleType]:
    """``win32com.client``, imported on first use; None when it cannot load (then WMI hardware
    sources are simply unavailable - never a crash). A failure is remembered for the process, as
    the old module-level import did."""
    global _client_unavailable
    mod = sys.modules.get("win32com.client")
    # Only a FINISHED import: a module sits in sys.modules from the moment its body starts running,
    # so while another worker is still importing it (the Check connection's details thread in
    # import_wmi, say) this fast path would hand the stats thread a half-built module without
    # GetObject. Such a caller takes the import below, which waits for the import to finish.
    if mod is not None and not getattr(getattr(mod, "__spec__", None), "_initializing", False):
        return mod
    with _lock:
        if _client_unavailable:
            return None
        try:
            _redirect_gen_path()
            import importlib
            return importlib.import_module("win32com.client")
        except (ImportError, OSError) as e:
            _client_unavailable = True
            logger.debug("win32com.client unavailable: %s", e)
            return None


def import_wmi() -> ModuleType:
    """The ``wmi`` module, imported on first use on the CALLING thread. Raises ImportError when it
    is unavailable (callers already treat that as "no WMI").

    ``import wmi`` itself makes a COM object (``wmi.obj = GetObject("winmgmts:")``, only read for
    a few typelib constants at import and by ``connect_server`` - remote logins, which this app
    never uses) that would live as long as the module. Through rc.6 it was imported on the GUI
    thread at startup (``preload_wmi``), because whichever thread imports wmi first owns that
    object: a worker that later calls CoUninitialize (the stats thread, the latency probe, a Check
    connection's details thread) left it to be released at interpreter exit, after its apartment
    was gone, and pywin32 printed "Win32 exception occurred releasing IUnknown". Instead of pinning
    the import to the GUI thread, the first import now releases that object right away, on the
    importing thread, while its apartment is alive - so wmi owns no COM object at all and any
    thread may import it first (pinned by test_lazy_com.py, which reproduces the exit-time error
    without the release)."""
    with _lock:
        fresh = "wmi" not in sys.modules
        if fresh and win32com_client() is None:   # also redirects gen_py before wmi imports it
            raise ImportError("win32com.client unavailable")
        import wmi
        if fresh and getattr(wmi, "obj", None) is not None:
            wmi.obj = None   # released here, in this thread's apartment (see the docstring)
        return wmi
