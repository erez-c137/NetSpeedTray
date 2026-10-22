"""
The one message the headless ``--apply-update`` process ever shows: "the update was not applied".

The applier runs before any QApplication exists, so this is a plain Win32 MessageBoxW. It is
localized from the user's saved language (read straight from the config file - ConfigManager would
validate and may write it, and nothing in an update path may write the user's config) and reuses the
guided-copy message the in-app updater already shows, so translators have one new line to do.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional, Tuple

logger = logging.getLogger("NetSpeedTray.UpdateNotice")

_MB_OK = 0x0
_MB_ICONINFORMATION = 0x40
_MB_SETFOREGROUND = 0x10000
_MB_TOPMOST = 0x40000
_MB_RTLREADING = 0x2000
_MB_RIGHT = 0x80000

_FALLBACK_TITLE = "Update ready to install"
_FALLBACK_LEAD = ("NetSpeedTray could not replace its folder by itself, so you are still on the "
                  "version you had.\nReason: {reason}")
_FALLBACK_READY = ("NetSpeedTray {version} is ready in the folder that just opened:\n{ready}\n\n"
                   "To finish updating: close NetSpeedTray, then copy everything from that folder "
                   "into your current folder:\n{app_dir}\n(replacing the old files). Your settings "
                   "are kept.")


def _saved_language() -> Optional[str]:
    """The ``language`` the user chose, or None (auto-detect). Read-only."""
    try:
        from netspeedtray import constants
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, constants.app.APP_NAME, constants.config.defaults.CONFIG_FILENAME)
        with open(path, "r", encoding="utf-8-sig") as fh:   # -sig: a Notepad BOM, as in config.load()
            value = json.load(fh).get("language")
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


def _app_version() -> str:
    try:
        from netspeedtray import constants
        return constants.app.VERSION
    except Exception:
        return ""


def build_update_not_applied(i18n, reason: str, staged_dir: str, install_dir: str,
                             version: str) -> Tuple[str, str]:
    """(title, text). ``i18n`` may be None (English)."""
    def tr(key: str, fallback: str) -> str:
        try:
            value = getattr(i18n, key) if i18n is not None else fallback
            return value if isinstance(value, str) and value else fallback
        except Exception:
            return fallback

    def fmt(template: str, fallback: str, **kw) -> str:
        try:
            return template.format(**kw)
        except (KeyError, IndexError, ValueError):
            return fallback.format(**kw)            # a broken translation must not hide the message

    lead_t = tr("UPDATE_NOT_APPLIED_MESSAGE", _FALLBACK_LEAD)
    ready_t = tr("UPDATE_PORTABLE_READY_MESSAGE", _FALLBACK_READY)
    lead = fmt(lead_t, _FALLBACK_LEAD, reason=reason)
    ready = fmt(ready_t, _FALLBACK_READY, version=version, ready=staged_dir, app_dir=install_dir)
    return tr("UPDATE_PORTABLE_READY_TITLE", _FALLBACK_TITLE), lead + "\n\n" + ready


def show_update_not_applied(reason: str, staged_dir: str, install_dir: str) -> None:
    """Open the staged folder (the message calls it "the folder that just opened") and show the
    message. Blocks until the user dismisses it; the app has already been restarted by then."""
    i18n = None
    try:
        from netspeedtray.constants.i18n import I18nStrings
        i18n = I18nStrings(_saved_language())
    except Exception:
        logger.debug("i18n unavailable for the update notice; using English.", exc_info=True)
    title, text = build_update_not_applied(i18n, reason, staged_dir, install_dir, _app_version())
    try:
        os.startfile(staged_dir)                      # type: ignore[attr-defined]
    except Exception:
        logger.debug("Could not open the staged folder.", exc_info=True)
    if sys.platform != "win32":                       # pragma: no cover
        return
    import ctypes
    flags = _MB_OK | _MB_ICONINFORMATION | _MB_SETFOREGROUND | _MB_TOPMOST
    try:
        if i18n is not None and getattr(i18n, "is_rtl", False):
            flags |= _MB_RTLREADING | _MB_RIGHT
    except Exception:
        pass
    ctypes.windll.user32.MessageBoxW(None, text, title, flags)
