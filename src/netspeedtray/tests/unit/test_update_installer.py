"""
Unit tests for the secure-update downloader (the pure, security-relevant logic).
The Qt thread/dialog orchestration in SecureUpdater is glue over download_to +
verify_file, both covered on their own.
"""
import io

import pytest

from netspeedtray.core import update_installer as ui


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
