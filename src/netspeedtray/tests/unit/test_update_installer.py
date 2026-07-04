"""
Unit tests for the secure-update downloader (the pure, security-relevant logic).
The Qt thread/dialog orchestration in SecureUpdater is glue over download_to +
verify_file, both covered on their own.
"""
import io
import sys
import zipfile

import pytest

from netspeedtray import constants
from netspeedtray.core import update_installer as ui
from netspeedtray.utils.helpers import is_portable_install


class _FakeResp:
    def __init__(self, data: bytes, content_length=None):
        self._buf = io.BytesIO(data)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, n): return self._buf.read(n)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_download_to_writes_file_and_reaches_100(tmp_path, monkeypatch):
    data = b"x" * (200 * 1024)  # multiple 64K chunks
    dest = tmp_path / "out.bin"
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda req, timeout=30: _FakeResp(data, len(data)))
    pcts = []
    ui.download_to("https://example/x", str(dest), progress_cb=pcts.append)
    assert dest.read_bytes() == data
    assert pcts and pcts[-1] == 100


def test_download_to_progress_minus_one_when_size_unknown(tmp_path, monkeypatch):
    data = b"y" * 2048
    dest = tmp_path / "out.bin"
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda req, timeout=30: _FakeResp(data, None))
    pcts = []
    ui.download_to("https://e/x", str(dest), progress_cb=pcts.append)
    assert dest.read_bytes() == data
    assert all(p == -1 for p in pcts)


def test_download_to_raises_on_cancel(tmp_path, monkeypatch):
    data = b"z" * (200 * 1024)
    dest = tmp_path / "out.bin"
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda req, timeout=30: _FakeResp(data, len(data)))
    with pytest.raises(RuntimeError):
        ui.download_to("https://e/x", str(dest), is_cancelled=lambda: True)


def test_download_to_enforces_size_cap(tmp_path, monkeypatch):
    # Pretend the stream is bigger than the ceiling: it must abort, not fill the disk.
    monkeypatch.setattr(ui, "_MAX_BYTES", 100 * 1024)
    data = b"q" * (300 * 1024)
    dest = tmp_path / "out.bin"
    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda req, timeout=30: _FakeResp(data, len(data)))
    with pytest.raises(RuntimeError):
        ui.download_to("https://e/x", str(dest))


# --- #195 portable guided update: pure logic -------------------------------------

def test_safe_extract_writes_members(tmp_path):
    z = tmp_path / "portable.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("NetSpeedTray/NetSpeedTray.exe", b"exe-bytes")
        zf.writestr("NetSpeedTray/_internal/x.dll", b"dll-bytes")
    dest = tmp_path / "out"
    ui._safe_extract(str(z), str(dest))
    assert (dest / "NetSpeedTray" / "NetSpeedTray.exe").read_bytes() == b"exe-bytes"
    assert (dest / "NetSpeedTray" / "_internal" / "x.dll").read_bytes() == b"dll-bytes"


def test_safe_extract_rejects_zip_slip(tmp_path):
    # A malicious archive that tries to escape the extraction dir must be refused, and nothing
    # may be written outside dest (the download host is untrusted until the inner EXE verifies).
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escaped.txt", b"pwned")
    dest = tmp_path / "out"
    with pytest.raises(RuntimeError):
        ui._safe_extract(str(z), str(dest))
    assert not (tmp_path / "escaped.txt").exists()


def test_locate_portable_exe_finds_nested_exe(tmp_path):
    appdir = tmp_path / "NetSpeedTray"
    appdir.mkdir()
    exe = appdir / "NetSpeedTray.exe"
    exe.write_bytes(b"")
    assert ui._locate_portable_exe(str(tmp_path)) == str(exe)


def test_locate_portable_exe_raises_when_absent(tmp_path):
    (tmp_path / "readme.txt").write_text("no exe here")
    with pytest.raises(RuntimeError):
        ui._locate_portable_exe(str(tmp_path))


def test_downloads_dir_prefers_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.os.path, "expanduser", lambda p: str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    assert ui._downloads_dir() == str(tmp_path / "Downloads")


def test_downloads_dir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.os.path, "expanduser", lambda p: str(tmp_path))  # no Downloads subdir
    assert ui._downloads_dir() == str(tmp_path)


def test_is_portable_install_true_with_marker(tmp_path, monkeypatch):
    (tmp_path / "NetSpeedTray.exe").write_bytes(b"")
    (tmp_path / constants.app.PORTABLE_MARKER_FILENAME).write_text("portable")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "NetSpeedTray.exe"))
    assert is_portable_install() is True


def test_is_portable_install_false_without_marker(tmp_path, monkeypatch):
    (tmp_path / "NetSpeedTray.exe").write_bytes(b"")  # installed layout: no marker
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "NetSpeedTray.exe"))
    assert is_portable_install() is False


def test_is_portable_install_false_when_not_frozen(tmp_path, monkeypatch):
    (tmp_path / constants.app.PORTABLE_MARKER_FILENAME).write_text("portable")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "NetSpeedTray.exe"))
    assert is_portable_install() is False


# --- #195 whole-ZIP checksum verification (the portable trust anchor) ------------

def test_sha256_file_matches_hashlib(tmp_path):
    import hashlib
    p = tmp_path / "portable.zip"
    data = b"NetSpeedTray portable payload " * 1000
    p.write_bytes(data)
    assert ui._sha256_file(str(p)) == hashlib.sha256(data).hexdigest()


def test_expected_hash_for_matches_case_insensitively():
    # Published format is "<UPPERHASH> <filename>" (PowerShell Get-FileHash).
    text = f"{'A' * 64} NetSpeedTray-Portable-2.1.0.zip\n{'B' * 64} NetSpeedTray-2.1.0-x64-Setup.exe\n"
    assert ui._expected_hash_for(text, "netspeedtray-portable-2.1.0.zip") == "a" * 64
    assert ui._expected_hash_for(text, "NetSpeedTray-2.1.0-x64-Setup.exe") == "b" * 64


def test_expected_hash_for_absent_returns_none():
    text = f"{'C' * 64} some-other-file.zip\n"
    assert ui._expected_hash_for(text, "NetSpeedTray-Portable-2.1.0.zip") is None


def test_expected_hash_for_rejects_malformed_hash():
    text = "not-a-real-sha NetSpeedTray-Portable-2.1.0.zip\n"
    assert ui._expected_hash_for(text, "NetSpeedTray-Portable-2.1.0.zip") is None


def test_unique_dir_returns_same_when_absent(tmp_path):
    target = tmp_path / "NetSpeedTray-2.1.0"
    assert ui._unique_dir(str(target)) == str(target)


def test_unique_dir_clears_removable_stale(tmp_path):
    target = tmp_path / "NetSpeedTray-2.1.0"
    target.mkdir()
    (target / "old.txt").write_text("stale from a prior run")
    assert ui._unique_dir(str(target)) == str(target)
    assert not target.exists()


def test_unique_dir_suffixes_when_stale_cannot_be_removed(tmp_path, monkeypatch):
    # A locked file leaves the stale dir present after rmtree -> we must NOT move into it (nesting bug).
    target = tmp_path / "NetSpeedTray-2.1.0"
    target.mkdir()
    monkeypatch.setattr(ui.shutil, "rmtree", lambda *a, **k: None)  # simulate a surviving locked dir
    assert ui._unique_dir(str(target)) == str(tmp_path / "NetSpeedTray-2.1.0-2")
