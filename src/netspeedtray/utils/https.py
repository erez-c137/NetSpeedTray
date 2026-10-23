"""
HTTPS for the update checker and the updater, verified against the Windows certificate store the way
Windows itself verifies.

Why not plain ``urllib``: Python's ``ssl`` loads the roots that are already IN the Windows store and
hands them to OpenSSL. Windows does not ship every root it trusts - it fetches a missing one from
Windows Update the first time CryptoAPI builds a chain that needs it. OpenSSL never asks, so on a
fresh install (no browser or other SChannel client has fetched GitHub's root yet) every update check
failed with ``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`` until something
else happened to pull the root in (rc.2 VM session; 2.1.7 had it too).

``truststore`` (MIT, pure Python, ctypes) verifies the server's chain with ``CertGetCertificateChain``
and the SSL policy - including the host name - so the root is fetched on demand exactly as Edge
would. Verification is never weakened: if truststore cannot be imported (a broken bundle), the
stdlib's default verifying context is used, which is the pre-fix behaviour, never an unverified one.
The updater's own trust gates (WinVerifyTrust + the SignPath publisher pin, checksums.txt) are
unchanged and still decide what runs.
"""
from __future__ import annotations

import logging
import ssl
import urllib.request
from typing import Optional

logger = logging.getLogger("NetSpeedTray.HTTPS")

_FALLBACK_LOGGED = False


def https_context() -> ssl.SSLContext:
    """A client context that verifies the certificate chain and host name. Prefers the OS verifier
    (truststore); falls back to ``ssl.create_default_context()`` - also verifying - if it is absent."""
    global _FALLBACK_LOGGED
    try:
        import truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception as e:  # ImportError in a broken bundle, or an OS without the API
        if not _FALLBACK_LOGGED:
            _FALLBACK_LOGGED = True
            logger.warning("OS certificate verifier unavailable (%s); using Python's default "
                           "verification", e)
        ctx = ssl.create_default_context()
    # Both kinds default to these; checked so a future change can never ship an unverified context.
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        raise ssl.SSLError("refusing an HTTPS context that does not verify the server")
    return ctx


def urlopen(req: "urllib.request.Request | str", timeout: float,
            context: Optional[ssl.SSLContext] = None):
    """``urllib.request.urlopen`` with :func:`https_context` (redirects keep the same context)."""
    return urllib.request.urlopen(req, timeout=timeout,
                                  context=context if context is not None else https_context())
