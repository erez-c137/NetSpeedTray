"""
Unit tests for the secure-update downloader (the pure, security-relevant logic).
The Qt thread/dialog orchestration in SecureUpdater is glue over download_to +
verify_file, both covered on their own.
"""
import io
import sys
import urllib.request
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
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30, **kw: _FakeResp(data, len(data)))
    pcts = []
    ui.download_to("https://example/x", str(dest), progress_cb=pcts.append)
    assert dest.read_bytes() == data
    assert pcts and pcts[-1] == 100


def test_download_to_progress_minus_one_when_size_unknown(tmp_path, monkeypatch):
    data = b"y" * 2048
    dest = tmp_path / "out.bin"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30, **kw: _FakeResp(data, None))
    pcts = []
    ui.download_to("https://e/x", str(dest), progress_cb=pcts.append)
    assert dest.read_bytes() == data
    assert all(p == -1 for p in pcts)


def test_download_to_raises_on_cancel(tmp_path, monkeypatch):
    data = b"z" * (200 * 1024)
    dest = tmp_path / "out.bin"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30, **kw: _FakeResp(data, len(data)))
    with pytest.raises(RuntimeError):
        ui.download_to("https://e/x", str(dest), is_cancelled=lambda: True)


def test_download_to_enforces_size_cap(tmp_path, monkeypatch):
    # Pretend the stream is bigger than the ceiling: it must abort, not fill the disk.
    monkeypatch.setattr(ui, "_MAX_BYTES", 100 * 1024)
    data = b"q" * (300 * 1024)
    dest = tmp_path / "out.bin"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30, **kw: _FakeResp(data, len(data)))
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


def test_downloads_dir_trusts_windows_over_the_guess(tmp_path, monkeypatch):
    """Windows' own answer wins - it is the only one that survives a relocated Downloads folder.

    People move Downloads off the system drive routinely (right-click -> Properties -> Location).
    The `~/Downloads` guess then misses, and we would stage the update somewhere the user has no
    reason to look - which, for a folder we then ask them to go and copy, is the same as losing it.
    """
    real = tmp_path / "D_drive_downloads"
    real.mkdir()
    (tmp_path / "Downloads").mkdir()          # the guess exists too, and must NOT win
    monkeypatch.setattr(ui, "_known_downloads_dir", lambda: str(real))
    monkeypatch.setattr(ui.os.path, "expanduser", lambda p: str(tmp_path))
    assert ui._downloads_dir() == str(real)


def test_downloads_dir_prefers_downloads(tmp_path, monkeypatch):
    """When Windows cannot answer, fall back to the guess."""
    monkeypatch.setattr(ui, "_known_downloads_dir", lambda: None)
    monkeypatch.setattr(ui.os.path, "expanduser", lambda p: str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    assert ui._downloads_dir() == str(tmp_path / "Downloads")


def test_downloads_dir_falls_back_to_home(tmp_path, monkeypatch):
    """Last resort: somewhere that definitely exists."""
    monkeypatch.setattr(ui, "_known_downloads_dir", lambda: None)
    monkeypatch.setattr(ui.os.path, "expanduser", lambda p: str(tmp_path))  # no Downloads subdir
    assert ui._downloads_dir() == str(tmp_path)


def test_known_downloads_dir_never_raises():
    """It is best-effort by contract - a COM failure must degrade to the guess, not crash."""
    result = ui._known_downloads_dir()
    assert result is None or ui.os.path.isdir(result)


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


def test_unique_dir_never_deletes_an_existing_folder(tmp_path):
    """#260 review: in Downloads an existing NetSpeedTray-<ver> may be the user's own folder (a copy
    they unzipped by hand, a rollback). It is never cleared to make room - a suffix is used."""
    target = tmp_path / "NetSpeedTray-2.1.0"
    target.mkdir()
    (target / "mine.txt").write_text("the user's")
    assert ui._unique_dir(str(target)) == str(tmp_path / "NetSpeedTray-2.1.0-2")
    assert (target / "mine.txt").read_text() == "the user's"


def test_unique_dir_suffixes_when_stale_cannot_be_removed(tmp_path, monkeypatch):
    # A locked file leaves the stale dir present after rmtree -> we must NOT move into it (nesting bug).
    target = tmp_path / "NetSpeedTray-2.1.0"
    target.mkdir()
    monkeypatch.setattr(ui.shutil, "rmtree", lambda *a, **k: None)  # simulate a surviving locked dir
    assert ui._unique_dir(str(target)) == str(tmp_path / "NetSpeedTray-2.1.0-2")


# --------------------------------------------------------------------------- #260: visible dialogs

def test_info_box_is_raised_and_activated(q_app, monkeypatch):
    """#260: a dialog the user never sees is indistinguishable from nothing happening.

    The update dialogs are parented to the widget, which is frameless, always-on-top and - since
    2.0 - docked into the taskbar's own Z-order. A plain QMessageBox parented to that can end up
    behind the shell, the same class of problem as #200. This one matters more than most, because
    telling the user where their update went IS the portable flow.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QMessageBox

    calls = {"raise_": 0, "activate": 0, "exec": 0, "on_top": None}
    monkeypatch.setattr(QMessageBox, "raise_", lambda self: calls.__setitem__("raise_", calls["raise_"] + 1))
    monkeypatch.setattr(QMessageBox, "activateWindow",
                        lambda self: calls.__setitem__("activate", calls["activate"] + 1))
    monkeypatch.setattr(QMessageBox, "show", lambda self: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: (
        calls.__setitem__("on_top", bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)),
        calls.__setitem__("exec", calls["exec"] + 1))[1])

    ui._info_box(None, "Update ready", "the new version is in your Downloads folder")

    assert calls["exec"] == 1, "the dialog was never shown"
    assert calls["raise_"] == 1, "the dialog was not raised above the shell"
    assert calls["activate"] == 1, "the dialog was not activated"
    assert calls["on_top"] is True, "the dialog did not carry WindowStaysOnTopHint"


def test_info_box_never_raises(q_app, monkeypatch):
    """Best-effort by contract: a dialog failure must not take the update flow down with it."""
    from PyQt6.QtWidgets import QMessageBox

    def boom(self):
        raise RuntimeError("simulated: no display")

    monkeypatch.setattr(QMessageBox, "show", boom)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    ui._info_box(None, "t", "m")   # must not propagate


# --- v2.1.5 item 3: the hands-off handoff records exactly what it staged ----------
# The startup sweep removes ONLY recorded paths, so if the handoff does not record
# the staged folder it can never be cleaned - and if it recorded it at the wrong
# moment, the guided-copy folder the user is told to use would be deleted.

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from netspeedtray.core import update_applier as _ua


def _make_app_dir(root, name):
    d = root / name
    (d / "_internal").mkdir(parents=True)
    (d / "NetSpeedTray.exe").write_text("x", encoding="utf-8")
    return d


def _updater(q_app):
    return ui.SecureUpdater(None, "", "https://example/release", SimpleNamespace(),
                            portable=True, portable_url="https://example/portable.zip",
                            latest_version="9.9.9")


def test_try_hands_off_records_the_staged_path(q_app, tmp_path, monkeypatch):
    marker = tmp_path / "appdata" / _ua._STAGED_MARKER_NAME
    monkeypatch.setattr(_ua, "_marker_path", lambda: str(marker))
    install = _make_app_dir(tmp_path, "NetSpeedTray")
    ready = _make_app_dir(tmp_path, "NetSpeedTray-9.9.9")
    monkeypatch.setattr(ui.subprocess, "Popen", MagicMock())

    handed_off = _updater(q_app)._try_hands_off(str(ready), str(install))

    assert handed_off is True
    assert json.loads(marker.read_text(encoding="utf-8")) == [
        {"path": os.path.abspath(str(ready)), "validated": False}], (
        "the committed handoff must record the exact staged path for the post-update sweep")


def test_try_hands_off_launch_failure_records_nothing(q_app, tmp_path, monkeypatch):
    """A failed handoff falls back to the guided copy, which points the user at the staged
    folder - so it must NOT be marked for sweeping."""
    marker = tmp_path / "appdata" / _ua._STAGED_MARKER_NAME
    monkeypatch.setattr(_ua, "_marker_path", lambda: str(marker))
    install = _make_app_dir(tmp_path, "NetSpeedTray")
    ready = _make_app_dir(tmp_path, "NetSpeedTray-9.9.9")
    monkeypatch.setattr(ui.subprocess, "Popen",
                        MagicMock(side_effect=OSError("simulated: AV blocked the launch")))

    handed_off = _updater(q_app)._try_hands_off(str(ready), str(install))

    assert handed_off is False
    assert not marker.exists(), "an uncommitted handoff must record nothing"


def test_try_hands_off_validation_refusal_records_nothing(q_app, tmp_path, monkeypatch):
    marker = tmp_path / "appdata" / _ua._STAGED_MARKER_NAME
    monkeypatch.setattr(_ua, "_marker_path", lambda: str(marker))
    ready = _make_app_dir(tmp_path, "NetSpeedTray-9.9.9")
    popen = MagicMock()
    monkeypatch.setattr(ui.subprocess, "Popen", popen)

    # No install dir at all -> validate() refuses -> guided copy path.
    handed_off = _updater(q_app)._try_hands_off(str(ready), str(tmp_path / "missing"))

    assert handed_off is False
    popen.assert_not_called()
    assert not marker.exists()


# ------------------------------------------------- every worker signal reaches the updater (U8 review)

@pytest.mark.parametrize("portable", [True, False])
def test_start_connects_every_worker_signal(q_app, tmp_path, monkeypatch, portable):
    """A worker signal nobody listens to is a silent dead end: an `untrusted` that is emitted but not
    connected leaves the progress dialog up forever and never tells the user (a mutation deleting
    that connect kept every other updater test green). So every pyqtSignal the worker declares must
    have a receiver once start() has wired it - including any signal added later."""
    from PyQt6.QtCore import pyqtSignal

    class _NoStartThread(ui.QThread):
        def start(self, *a, **k):   # wire everything, run nothing
            pass

    monkeypatch.setattr(ui, "QThread", _NoStartThread)
    monkeypatch.setattr(ui, "sweep_stale_update_dirs", lambda: None)
    monkeypatch.setattr(ui.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    monkeypatch.setattr(ui, "_downloads_dir", lambda: str(tmp_path / "Downloads"))
    up = ui.SecureUpdater(None, "https://example/Setup.exe", "https://example/release", SimpleNamespace(),
                          portable=portable, portable_url="https://example/d/NetSpeedTray-Portable.zip",
                          latest_version="9.9.9")
    try:
        up.start()
        worker = up._worker
        assert worker is not None
        names = [n for n, v in vars(ui._DownloadWorker).items() if isinstance(v, pyqtSignal)]
        assert "untrusted" in names and "failed" in names
        unconnected = [n for n in names if worker.receivers(getattr(worker, n)) < 1]
        assert unconnected == [], f"worker signals with no receiver: {unconnected}"
    finally:
        up._close_progress()
        up._thread = None
        up._worker = None


# ---------------------------------- review round 2: the installer worker's guarded re-verification
# The installer branch of _DownloadWorker.run() must pin the download FIRST, tie the pinned file to
# the release via checksums.txt, and refuse closed on every mismatch - each refusal drops the pins
# and routes to `failed` (the browser fallback). Removing any of these steps used to leave every
# other updater test green (review round 2), so each one is pinned directly here.

import hashlib

_INSTALLER_URL = "https://github.com/x/y/releases/download/v2.2.0/NetSpeedTray-2.2.0-x64-Setup.exe"
_CHECKSUMS_URL = "https://github.com/x/y/releases/download/v2.2.0/checksums.txt"
_SETUP_NAME = "NetSpeedTray-2.2.0-x64-Setup.exe"
_FAKE_INSTALLER = b"MZ fake installer"


@pytest.fixture(autouse=True)
def _drop_any_guards_left_behind():
    """The worker's success path leaves the pins held ON PURPOSE (launch_installer releases them
    once the elevated shell has started); drop whatever a test here left behind so handles and pins
    never leak into a later test."""
    yield
    ui._release_launch_guards()


def _installer_worker(tmp_path, monkeypatch, *, checksums_text=None, fetch_error=False,
                      acquire_result=None):
    """An installer-mode worker harness: the download writes a real dummy file, the checksums fetch
    and the Authenticode gate are faked (recorded), but the launch guards and the SHA-256 run for
    real. Returns (worker, caps) with the captured signals and calls."""
    from netspeedtray.utils import signature_verifier

    dest = tmp_path / _SETUP_NAME

    def fake_download(url, d, progress_cb=None, is_cancelled=None):
        with open(d, "wb") as f:
            f.write(_FAKE_INSTALLER)
    monkeypatch.setattr(ui, "download_to", fake_download)

    if fetch_error:
        def broken_fetch(url):
            raise RuntimeError("simulated: checksums.txt unreachable")
        monkeypatch.setattr(ui, "_fetch_checksums", broken_fetch)
    else:
        monkeypatch.setattr(ui, "_fetch_checksums", lambda url: checksums_text)

    verify_calls = []

    def recording_verify(path):
        verify_calls.append(path)
        return signature_verifier.VerifyResult(True, "", subject_cn="X")
    monkeypatch.setattr(ui, "verify_file", recording_verify)

    acquire_calls = []
    real_acquire = ui._acquire_launch_guards

    def recording_acquire(path):
        acquire_calls.append(path)
        return real_acquire(path) if acquire_result is None else acquire_result
    monkeypatch.setattr(ui, "_acquire_launch_guards", recording_acquire)

    w = ui._DownloadWorker(_INSTALLER_URL, str(dest), portable=False,
                           checksums_url=_CHECKSUMS_URL, expected_name=_SETUP_NAME)
    failed, verified = [], []
    w.failed.connect(failed.append)
    w.verified.connect(lambda path, trusted, reason: verified.append((path, trusted, reason)))
    return w, {"failed": failed, "verified": verified, "verify_calls": verify_calls,
               "acquire_calls": acquire_calls, "dest": dest}


def _match_line() -> str:
    """A checksums.txt line that really matches the fake download (uppercase, as published)."""
    return f"{hashlib.sha256(_FAKE_INSTALLER).hexdigest().upper()} {_SETUP_NAME}\n"


def test_installer_worker_checksum_mismatch_fails_and_drops_the_guards(tmp_path, monkeypatch):
    w, caps = _installer_worker(tmp_path, monkeypatch,
                                checksums_text=f"{'F' * 64} {_SETUP_NAME}\n")

    w.run()

    assert len(caps["failed"]) == 1 and "checksum mismatch" in caps["failed"][0]
    assert caps["verified"] == [] and caps["verify_calls"] == [], "a tampered download never reaches the gate"
    assert caps["acquire_calls"] == [str(caps["dest"])], "the pin was taken before the check"
    assert ui._LAUNCH_GUARDS is None, "a refusal must not carry the pins with it"


def test_installer_worker_missing_checksum_entry_fails_and_drops_the_guards(tmp_path, monkeypatch):
    """Fail closed: a checksums.txt that does not list THIS artifact is a refusal, not a pass."""
    w, caps = _installer_worker(tmp_path, monkeypatch,
                                checksums_text=f"{'A' * 64} some-other-asset.exe\n")

    w.run()

    assert len(caps["failed"]) == 1 and "no published checksum" in caps["failed"][0]
    assert caps["verified"] == [] and caps["verify_calls"] == []
    assert ui._LAUNCH_GUARDS is None


def test_installer_worker_checksum_fetch_error_fails_and_drops_the_guards(tmp_path, monkeypatch):
    w, caps = _installer_worker(tmp_path, monkeypatch, fetch_error=True)

    w.run()

    assert len(caps["failed"]) == 1 and "checksums.txt unreachable" in caps["failed"][0]
    assert caps["verified"] == [] and caps["verify_calls"] == []
    assert ui._LAUNCH_GUARDS is None


def test_installer_worker_checksum_match_reaches_verify_file(tmp_path, monkeypatch):
    """The happy path: the pinned file's SHA-256 matches the release, and the Authenticode gate
    actually runs - `verified` carries its verdict while the pin holds (releasing it is
    launch_installer's job, after the elevated shell has started)."""
    w, caps = _installer_worker(tmp_path, monkeypatch, checksums_text=_match_line())

    w.run()

    assert caps["failed"] == []
    assert caps["verify_calls"] == [str(caps["dest"])], "the signature gate ran on the pinned file"
    assert caps["verified"] == [(str(caps["dest"]), True, "")]
    assert caps["acquire_calls"] == [str(caps["dest"])]
    if os.name == "nt":
        assert ui._LAUNCH_GUARDS is not None, "the pins hold until the elevated launch"
    ui._release_launch_guards()


@pytest.mark.parametrize("missing", ["url", "name"])
def test_installer_worker_without_a_checksums_reference_fails_closed(
        tmp_path, monkeypatch, missing):
    """Review round 2: the installer checksum preconditions must not silently skip the check when
    either reference is empty - like the portable path, a missing checksums reference is a refusal,
    so the check cannot turn itself off."""
    w, caps = _installer_worker(tmp_path, monkeypatch, checksums_text=_match_line())
    if missing == "url":
        w._checksums_url = ""    # as if start() had failed to derive it
    else:
        w._expected_name = ""

    w.run()

    assert len(caps["failed"]) == 1 and "missing checksums reference" in caps["failed"][0]
    assert caps["verified"] == [] and caps["verify_calls"] == []
    assert ui._LAUNCH_GUARDS is None


def test_installer_worker_failed_guard_acquire_fails_closed(tmp_path, monkeypatch):
    """The pin itself could not be taken: nothing is verified and nothing is launched - the update
    is refused before anything can ride the elevation prompt."""
    w, caps = _installer_worker(tmp_path, monkeypatch, checksums_text=_match_line(),
                                acquire_result=False)

    w.run()

    assert len(caps["failed"]) == 1 and "could not pin" in caps["failed"][0]
    assert caps["verified"] == [] and caps["verify_calls"] == [], "nothing runs unpinned"
    assert ui._LAUNCH_GUARDS is None


def test_start_passes_the_installer_checksums_url_to_the_worker(q_app, tmp_path, monkeypatch):
    """start() derives checksums.txt from the installer asset's own release-download folder and
    hands it (with the artifact's basename) to the worker - both feed the guarded re-verification,
    so a regression here would silently strip the check for every installer update."""
    class _NoStartThread(ui.QThread):
        def start(self, *a, **k):   # wire everything, run nothing
            pass

    captured = {}

    class _RecordingWorker(ui._DownloadWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.update(kwargs)

        def run(self):   # never invoked: the thread never starts; belt and braces
            raise AssertionError("the worker must not run in this test")

    monkeypatch.setattr(ui, "QThread", _NoStartThread)
    monkeypatch.setattr(ui, "_DownloadWorker", _RecordingWorker)
    monkeypatch.setattr(ui, "sweep_stale_update_dirs", lambda: None)
    monkeypatch.setattr(ui.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))

    up = ui.SecureUpdater(None, _INSTALLER_URL, "https://example/release", SimpleNamespace(),
                          portable=False, latest_version="2.2.0")
    try:
        up.start()
        assert captured["checksums_url"] == _CHECKSUMS_URL
        assert captured["expected_name"] == _SETUP_NAME
        assert captured["portable"] is False
    finally:
        up._close_progress()
        up._thread = None
        up._worker = None
