"""Per-user secret storage with Windows DPAPI (the LibreHardwareMonitor web-server password, G2).

``protect`` turns a secret into a base64 string that is safe to keep in config.json: a
CryptProtectData blob bound to the current Windows user (and to this app's entropy). Only the same
user on the same machine can turn it back with ``unprotect`` - a config copied to another PC or
another account decrypts to nothing, and the user types the password again. Any program running as
the same user CAN decrypt it (DPAPI's user scope); the app entropy only stops a casual decrypt.

Neither function ever logs, raises or returns the secret in an error: callers get ``""`` / ``None``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
from typing import Optional

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.SecretStore")

# CRYPTPROTECT_UI_FORBIDDEN: never show a DPAPI prompt (there is none for a plain user-scope blob,
# but a background poll must not be able to pop one either).
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

# Fingerprints (never the values) of saved blobs already reported as unreadable: the page re-reads the
# blob on every show and the monitor thread on every settings change, and one WARNING is enough.
_warned_unreadable: set = set()


def _warn_unreadable_once(stored: str, why: str) -> None:
    fingerprint = hashlib.sha256(stored.encode("utf-8", "replace")).hexdigest()
    if fingerprint in _warned_unreadable:
        return
    _warned_unreadable.add(fingerprint)
    logger.warning("The saved LibreHardwareMonitor password cannot be read for this Windows user (%s). "
                   "It is kept until a new one is entered in Settings > Hardware.", why)


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
        _warn_unreadable_once(stored, "not a DPAPI value")
        return None
    crypt = _win32crypt()
    if crypt is None:
        _warn_unreadable_once(stored, "DPAPI unavailable")
        return None
    try:
        _description, data = crypt.CryptUnprotectData(blob, entropy, None, None,
                                                      _CRYPTPROTECT_UI_FORBIDDEN)
        return bytes(data).decode("utf-8")
    except Exception as e:
        _warn_unreadable_once(stored, type(e).__name__)
        return None
