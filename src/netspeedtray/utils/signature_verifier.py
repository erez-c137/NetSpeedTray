"""
Authenticode signature verification for the auto-updater.

A downloaded installer must pass BOTH gates before it is ever executed:

  1. WinVerifyTrust - the OS confirms the Authenticode signature is valid, the file
     is not tampered, and the cert chains to a trusted root (revocation checked).
  2. Publisher pin - the signing cert's Subject CN and Issuer CN match the pinned
     SignPath Foundation / GlobalSign values.

Fail-closed: ANY error, missing signature, or mismatch yields ``trusted=False``.

Note on the pin: SignPath Foundation's free OSS program signs every project with one
shared certificate, so the Subject is "SignPath Foundation" - NOT "NetSpeedTray". The
NetSpeedTray-specific binding therefore comes from downloading only over TLS from our
own GitHub releases; this pin proves the binary went through SignPath's signing (an
attacker who swapped a GitHub release could not re-sign it without SignPath).
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("NetSpeedTray.SignatureVerifier")

# --- Pinned publisher --------------------------------------------------------
# Full DNs (reference; only the CNs are matched):
#   Subject: CN=SignPath Foundation, O=SignPath Foundation, L=Lewes, S=Delaware, C=US
#   Issuer : CN=GlobalSign GCC R45 CodeSigning CA 2020, O=GlobalSign nv-sa, C=BE
# Update ONLY if SignPath changes its signing identity or CA on renewal. The
# thumbprint rotates yearly and is deliberately NOT pinned (it would brick the
# updater after every renewal); these CNs are stable across renewals.
PINNED_SUBJECT_CN: str = "SignPath Foundation"
PINNED_ISSUER_CN: str = "GlobalSign GCC R45 CodeSigning CA 2020"


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of verifying a file. ``trusted`` is the only thing callers should gate on."""
    trusted: bool
    reason: str
    subject_cn: Optional[str] = None
    issuer_cn: Optional[str] = None
    status_code: Optional[int] = None


# --- WinVerifyTrust ctypes ---------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _wintrust_action_generic_verify_v2() -> "_GUID":
    # {00AAC56B-CD44-11D0-8CC2-00C04FC295EE}
    return _GUID(0x00AAC56B, 0xCD44, 0x11D0,
                 (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.c_void_p),  # union member; we point it at a _WINTRUST_FILE_INFO
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


_WTD_UI_NONE = 2
_WTD_REVOKE_WHOLECHAIN = 1
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2


def _win_verify_trust(path: str) -> int:
    """Run WinVerifyTrust on `path`. Returns the HRESULT (0 == trusted)."""
    wintrust = ctypes.windll.wintrust
    wintrust.WinVerifyTrust.restype = wintypes.LONG
    wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p]

    file_info = _WINTRUST_FILE_INFO(
        cbStruct=ctypes.sizeof(_WINTRUST_FILE_INFO),
        pcwszFilePath=path, hFile=None, pgKnownSubject=None,
    )
    data = _WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(_WINTRUST_DATA)
    data.dwUIChoice = _WTD_UI_NONE
    data.fdwRevocationChecks = _WTD_REVOKE_WHOLECHAIN
    data.dwUnionChoice = _WTD_CHOICE_FILE
    data.dwStateAction = _WTD_STATEACTION_VERIFY
    data.pFile = ctypes.cast(ctypes.byref(file_info), ctypes.c_void_p)

    action = _wintrust_action_generic_verify_v2()
    hr = wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))

    # Always release the state data, regardless of the verify result.
    data.dwStateAction = _WTD_STATEACTION_CLOSE
    wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
    return int(hr)


# --- Signer certificate CN extraction (crypt32) ------------------------------
_CERT_QUERY_OBJECT_FILE = 0x00000001
_CERT_QUERY_CONTENT_FLAG_ALL = 0x00003FFE
_CERT_QUERY_FORMAT_FLAG_ALL = 0x0000000E
_CMSG_SIGNER_CERT_INFO_PARAM = 7
_X509_ASN_ENCODING = 0x00000001
_PKCS_7_ASN_ENCODING = 0x00010000
_CERT_NAME_SIMPLE_DISPLAY_TYPE = 4
_CERT_NAME_ISSUER_FLAG = 0x1


def _name_string(crypt32, cert_ctx, issuer: bool) -> Optional[str]:
    flags = _CERT_NAME_ISSUER_FLAG if issuer else 0
    cch = crypt32.CertGetNameStringW(cert_ctx, _CERT_NAME_SIMPLE_DISPLAY_TYPE, flags, None, None, 0)
    if cch <= 1:  # 1 == just the null terminator
        return None
    buf = ctypes.create_unicode_buffer(cch)
    crypt32.CertGetNameStringW(cert_ctx, _CERT_NAME_SIMPLE_DISPLAY_TYPE, flags, None, buf, cch)
    return buf.value or None


def _signer_cns(path: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (subject_cn, issuer_cn) of the file's signing certificate, or (None, None)."""
    crypt32 = ctypes.windll.crypt32
    # Explicit signatures so 64-bit pointers aren't truncated to int.
    crypt32.CryptQueryObject.restype = wintypes.BOOL
    crypt32.CryptQueryObject.argtypes = [
        wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    crypt32.CryptMsgGetParam.restype = wintypes.BOOL
    crypt32.CryptMsgGetParam.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
    ]
    crypt32.CertGetSubjectCertificateFromStore.restype = ctypes.c_void_p
    crypt32.CertGetSubjectCertificateFromStore.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD
    crypt32.CertGetNameStringW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD,
    ]
    crypt32.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    crypt32.CryptMsgClose.argtypes = [ctypes.c_void_p]

    h_store = ctypes.c_void_p()
    h_msg = ctypes.c_void_p()
    ok = crypt32.CryptQueryObject(
        _CERT_QUERY_OBJECT_FILE,
        ctypes.cast(ctypes.c_wchar_p(path), ctypes.c_void_p),
        _CERT_QUERY_CONTENT_FLAG_ALL,
        _CERT_QUERY_FORMAT_FLAG_ALL,
        0, None, None, None,
        ctypes.byref(h_store), ctypes.byref(h_msg), None,
    )
    if not ok or not h_msg:
        return None, None
    try:
        cb = wintypes.DWORD(0)
        if not crypt32.CryptMsgGetParam(h_msg, _CMSG_SIGNER_CERT_INFO_PARAM, 0, None, ctypes.byref(cb)):
            return None, None
        buf = (ctypes.c_byte * cb.value)()
        if not crypt32.CryptMsgGetParam(h_msg, _CMSG_SIGNER_CERT_INFO_PARAM, 0,
                                        ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(cb)):
            return None, None
        cert_ctx = crypt32.CertGetSubjectCertificateFromStore(
            h_store, _X509_ASN_ENCODING | _PKCS_7_ASN_ENCODING, ctypes.cast(buf, ctypes.c_void_p),
        )
        if not cert_ctx:
            return None, None
        try:
            return _name_string(crypt32, cert_ctx, issuer=False), _name_string(crypt32, cert_ctx, issuer=True)
        finally:
            crypt32.CertFreeCertificateContext(cert_ctx)
    finally:
        if h_store:
            crypt32.CertCloseStore(h_store, 0)
        if h_msg:
            crypt32.CryptMsgClose(h_msg)


# --- Public API --------------------------------------------------------------
def verify_file(path: str,
                expected_subject_cn: str = PINNED_SUBJECT_CN,
                expected_issuer_cn: str = PINNED_ISSUER_CN) -> VerifyResult:
    """
    Verify `path` is Authenticode-signed, valid/untampered/trusted-chain (WinVerifyTrust),
    AND signed by the pinned publisher (Subject CN + Issuer CN). Fail-closed: any error or
    mismatch returns ``trusted=False``. NEVER execute a file unless this returns ``trusted``.
    """
    try:
        if not path or not os.path.isfile(path):
            return VerifyResult(False, "file not found")

        hr = _win_verify_trust(path)
        if hr != 0:
            # Common: 0x800B0100 NO_SIGNATURE, 0x80096010 BAD_DIGEST (tampered),
            # 0x800B0109 UNTRUSTEDROOT, 0x800B010C revoked.
            return VerifyResult(False, f"WinVerifyTrust failed (0x{hr & 0xFFFFFFFF:08X})", status_code=hr)

        subject_cn, issuer_cn = _signer_cns(path)
        if not subject_cn or not issuer_cn:
            return VerifyResult(False, "could not read signer certificate", status_code=hr)

        if subject_cn != expected_subject_cn:
            return VerifyResult(False, f"subject CN mismatch: {subject_cn!r}",
                                subject_cn=subject_cn, issuer_cn=issuer_cn, status_code=hr)
        if issuer_cn != expected_issuer_cn:
            return VerifyResult(False, f"issuer CN mismatch: {issuer_cn!r}",
                                subject_cn=subject_cn, issuer_cn=issuer_cn, status_code=hr)

        return VerifyResult(True, "trusted", subject_cn=subject_cn, issuer_cn=issuer_cn, status_code=hr)
    except Exception as e:  # fail-closed on any unexpected error
        logger.error("Signature verification raised: %s", e, exc_info=True)
        return VerifyResult(False, f"verification error: {e}")
