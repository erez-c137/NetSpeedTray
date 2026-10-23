"""
HTTPS trust (rc.2 VM finding): on a fresh Windows install every update check failed with
CERTIFICATE_VERIFY_FAILED, because Python's OpenSSL only sees the roots already in the Windows store
and never triggers Windows' on-demand root fetch. Every HTTPS call now verifies through truststore
(Windows' chain engine), and nothing may ever weaken verification.
"""
import ast
import builtins
import pathlib
import re
import ssl
import sys
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.utils import https

SRC = pathlib.Path(__file__).resolve().parents[2]          # src/netspeedtray
REPO = SRC.parents[1]


def test_context_is_the_os_verifier_and_verifies():
    truststore = pytest.importorskip("truststore")
    ctx = https.https_context()
    assert isinstance(ctx, truststore.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_missing_truststore_falls_back_to_a_verifying_context_never_an_unverified_one(monkeypatch):
    real_import = builtins.__import__

    def no_truststore(name, *a, **kw):
        if name == "truststore":
            raise ImportError("not bundled")
        return real_import(name, *a, **kw)

    monkeypatch.delitem(sys.modules, "truststore", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_truststore)
    ctx = https.https_context()
    assert type(ctx) is ssl.SSLContext
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_a_non_verifying_context_is_refused(monkeypatch):
    weak = ssl.create_default_context()
    weak.check_hostname = False
    weak.verify_mode = ssl.CERT_NONE
    fake = MagicMock()
    fake.SSLContext.return_value = weak
    monkeypatch.setitem(sys.modules, "truststore", fake)
    with pytest.raises(ssl.SSLError):
        https.https_context()


def test_urlopen_always_passes_a_verifying_context():
    with patch("urllib.request.urlopen") as uo:
        https.urlopen("https://example.invalid/", timeout=7)
    _, kw = uo.call_args
    assert kw["timeout"] == 7
    ctx = kw["context"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_the_update_check_goes_through_the_os_verifier():
    """The call the VM caught failing: the GitHub API request must carry the truststore context."""
    truststore = pytest.importorskip("truststore")
    from netspeedtray.core import update_checker as uc
    resp = MagicMock()
    resp.__enter__.return_value.read.return_value = b'{"tag_name": "v9.9.9"}'
    with patch("urllib.request.urlopen", return_value=resp) as uo:
        w = uc._CheckWorker()
        w.run()
    assert isinstance(uo.call_args.kwargs.get("context"), truststore.SSLContext)


def test_update_downloads_go_through_the_os_verifier(tmp_path):
    truststore = pytest.importorskip("truststore")
    from netspeedtray.core import update_installer as ui
    resp = MagicMock()
    resp.__enter__.return_value.headers = {}
    resp.__enter__.return_value.read.side_effect = [b"x", b"", b"HASH a.exe"]
    with patch("urllib.request.urlopen", return_value=resp) as uo:
        ui.download_to("https://example.invalid/a.exe", str(tmp_path / "a.exe"))
        ui._fetch_checksums("https://example.invalid/checksums.txt")
    assert len(uo.call_args_list) == 2
    for call in uo.call_args_list:
        assert isinstance(call.kwargs.get("context"), truststore.SSLContext)


def test_no_https_call_bypasses_the_helper():
    """Any new urlopen / HTTPSConnection / ssl context outside utils/https.py would quietly
    reintroduce the fresh-install failure. Source scan of the shipped package (tests excluded)."""
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("tests/") or rel == "utils/https.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            via_helper = (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                          and node.value.id == "https")
            if isinstance(node, ast.Attribute) and not via_helper and node.attr in (
                    "urlopen", "HTTPSConnection", "create_default_context", "_create_unverified_context"):
                offenders.append(f"{rel}:{node.lineno} {node.attr}")
            if isinstance(node, ast.ImportFrom) and node.module in ("urllib.request", "ssl", "http.client"):
                for alias in node.names:
                    if alias.name in ("urlopen", "HTTPSConnection", "create_default_context"):
                        offenders.append(f"{rel}:{node.lineno} from-import {alias.name}")
    assert not offenders, offenders


def test_truststore_is_pinned_and_bundled():
    for req in ("requirements.txt", "dev-requirements.txt"):
        text = (REPO / req).read_text(encoding="utf-8")
        assert re.search(r"^truststore==\d", text, re.M), req
    assert re.search(r"^truststore\b", (REPO / "requirements.in").read_text(encoding="utf-8"), re.M)
    spec = (REPO / "build" / "netspeedtray.spec").read_text(encoding="utf-8")
    assert "'truststore'," in spec                       # hidden import
    assert "'truststore' is missing from the bundle" in spec   # build-time guard
