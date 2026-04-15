"""
RDP (Remote Desktop Protocol) session detection for NetSpeedTray.

Used to skip hardware monitoring and app activity features that are
unreliable or unavailable inside virtual/remote desktop environments.
"""

from __future__ import annotations
import ctypes
import logging

logger = logging.getLogger(__name__)


def is_rdp_session() -> bool:
    """Returns True if running inside an RDP (Remote Desktop) session."""
    try:
        SM_REMOTESESSION = 0x1000
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION))
    except Exception:
        logger.debug("Could not determine RDP session state via GetSystemMetrics")
        return False
