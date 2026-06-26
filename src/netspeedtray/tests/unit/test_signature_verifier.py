"""
Unit tests for the auto-update signature verifier.

The Win32 layer (_win_verify_trust + _signer_cns) is monkeypatched so the verify_file
decision logic is tested deterministically on any runner. One ground-truth test runs
against the real signed installer when it's present locally (skipped in CI).
"""
import os

import pytest

from netspeedtray.utils import signature_verifier as sv

_REAL_INSTALLER = os.path.expanduser("~/Downloads/NetSpeedTray-1.3.3-x64-Setup.exe")


@pytest.fixture
def fake_file(tmp_path):
    p = tmp_path / "installer.exe"
    p.write_bytes(b"not a real exe")
    return str(p)


def test_trusted_when_valid_and_pin_matches(monkeypatch, fake_file):
    monkeypatch.setattr(sv, "_win_verify_trust", lambda path: 0)
    monkeypatch.setattr(sv, "_signer_cns", lambda path: (sv.PINNED_SUBJECT_CN, sv.PINNED_ISSUER_CN))
    r = sv.verify_file(fake_file)
    assert r.trusted is True
    assert r.reason == "trusted"
    assert r.subject_cn == sv.PINNED_SUBJECT_CN
    assert r.issuer_cn == sv.PINNED_ISSUER_CN


def test_rejects_when_winverifytrust_fails(monkeypatch, fake_file):
    monkeypatch.setattr(sv, "_win_verify_trust", lambda path: 0x800B0100)  # NO_SIGNATURE
    # Even with matching CNs, a failed WinVerifyTrust must reject (gate order matters).
    monkeypatch.setattr(sv, "_signer_cns", lambda path: (sv.PINNED_SUBJECT_CN, sv.PINNED_ISSUER_CN))
    r = sv.verify_file(fake_file)
    assert r.trusted is False
    assert "WinVerifyTrust failed" in r.reason
    assert r.status_code == 0x800B0100


def test_rejects_when_signer_unreadable(monkeypatch, fake_file):
    monkeypatch.setattr(sv, "_win_verify_trust", lambda path: 0)
    monkeypatch.setattr(sv, "_signer_cns", lambda path: (None, None))
    r = sv.verify_file(fake_file)
    assert r.trusted is False
    assert "could not read" in r.reason


def test_rejects_on_subject_mismatch(monkeypatch, fake_file):
    monkeypatch.setattr(sv, "_win_verify_trust", lambda path: 0)
    monkeypatch.setattr(sv, "_signer_cns", lambda path: ("Evil Corp", sv.PINNED_ISSUER_CN))
    r = sv.verify_file(fake_file)
    assert r.trusted is False
    assert "subject CN mismatch" in r.reason


def test_rejects_on_issuer_mismatch(monkeypatch, fake_file):
    monkeypatch.setattr(sv, "_win_verify_trust", lambda path: 0)
    monkeypatch.setattr(sv, "_signer_cns", lambda path: (sv.PINNED_SUBJECT_CN, "Evil CA"))
    r = sv.verify_file(fake_file)
    assert r.trusted is False
    assert "issuer CN mismatch" in r.reason


def test_rejects_missing_file():
    r = sv.verify_file("nope/does/not/exist.exe")
    assert r.trusted is False
    assert "file not found" in r.reason


def test_fail_closed_on_exception(monkeypatch, fake_file):
    def boom(path):
        raise OSError("ctypes blew up")
    monkeypatch.setattr(sv, "_win_verify_trust", boom)
    r = sv.verify_file(fake_file)
    assert r.trusted is False
    assert "verification error" in r.reason


@pytest.mark.skipif(not os.path.isfile(_REAL_INSTALLER), reason="signed installer not present locally")
def test_real_signed_installer_is_trusted():
    """Ground truth: the actual SignPath-signed installer must pass both gates."""
    r = sv.verify_file(_REAL_INSTALLER)
    assert r.trusted is True, r.reason
    assert r.subject_cn == sv.PINNED_SUBJECT_CN
    assert r.issuer_cn == sv.PINNED_ISSUER_CN
