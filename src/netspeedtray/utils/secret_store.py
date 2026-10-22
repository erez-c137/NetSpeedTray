"""Per-user secret storage with Windows DPAPI (the LibreHardwareMonitor web-server password, G2).

``protect`` turns a secret into a base64 string that is safe to keep in config.json: a
CryptProtectData blob bound to the current Windows user (and to this app's entropy). Only the same
user on the same machine can turn it back with ``unprotect`` - a config copied to another PC or
another account decrypts to nothing, and the user simply types the password again.

Neither function ever logs, raises or returns the secret in an error: callers get ``""`` / ``None``.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Optional

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.SecretStore")

# CRYPTPROTECT_UI_FORBIDDEN: never show a DPAPI prompt (there is none for a plain user-scope blob,
# but a background poll must not be able to pop one either).
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _win32crypt():
    try:
        import win32crypt  # pywin32; imported lazily so a non-Windows test run can mock it
        return win32crypt
    except ImportError:
        return None


def protect(secret: str, entropy: bytes = constants.lhm.DPAPI_ENTROPY) -> str:
    """The DPAPI-protected, base64-encoded form of ``secret``; ``""`` for an empty secret or when
    DPAPI is unavailable (the password is then not stored at all - never in plain text)."""
    if not secret:
        return ""
    crypt = _win32crypt()
    if crypt is None:
        logger.warning("Windows DPAPI is unavailable; the password was not saved.")
        return ""
    try:
        blob = crypt.CryptProtectData(secret.encode("utf-8"), None, entropy, None, None,
                                      _CRYPTPROTECT_UI_FORBIDDEN)
    except Exception as e:  # pywintypes.error; the message never contains the secret
        logger.warning("Could not protect the password with DPAPI (%s); it was not saved.",
                       type(e).__name__)
        return ""
    return base64.b64encode(bytes(blob)).decode("ascii")


def unprotect(stored: str, entropy: bytes = constants.lhm.DPAPI_ENTROPY) -> Optional[str]:
    """The secret behind a ``protect`` result, or ``None`` when there is none or it cannot be
    decrypted (another user / machine, a hand-edited value, DPAPI unavailable)."""
    if not stored or not isinstance(stored, str):
        return None
    try:
        blob = base64.b64decode(stored.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        logger.info("The saved LibreHardwareMonitor password is not a DPAPI value; ignoring it.")
        return None
    crypt = _win32crypt()
    if crypt is None:
        return None
    try:
        _description, data = crypt.CryptUnprotectData(blob, entropy, None, None,
                                                      _CRYPTPROTECT_UI_FORBIDDEN)
        return bytes(data).decode("utf-8")
    except Exception as e:
        logger.info("The saved LibreHardwareMonitor password cannot be decrypted for this Windows "
                    "user (%s); enter it again in Settings > Hardware.", type(e).__name__)
        return None
