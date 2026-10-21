"""
#260: after a portable update, the downloaded update stayed in Downloads and the previous version
stayed beside the install.

Two causes, both pinned here:

1. **The staged copy was deferred on exactly the launch that should remove it.** The relaunched
   version swept it seconds after the swap, but the sweep defers any recorded folder younger than
   ten minutes (review C1), so it stayed until some later restart - for a tray app, often weeks.
   Now the applier marks the entry *applied* after a verified swap and the new version removes it
   (and the zip kept beside it) once it has started, after waiting for the applier to exit.
2. **The previous version was deleted once, best-effort, with every error ignored**, right after the
   old process exited - when AV or the indexer may still hold a just-closed file. Nothing logged
   it, and the startup sweep skipped anything younger than a minute. Now the new version removes it
   with retries, after it has started (the backup is the rollback until then).

And because this deletes folders on a user's disk, most of this file is the dangerous cases: user
files in the install, junctions, unexpected layouts, the wrong running copy, locked files. Real
directories throughout - the bug class here is filesystem semantics, which mocks cannot show.
"""

import _winapi
import json
import os
import time
import zipfile
from pathlib import Path

import pytest

from netspeedtray.core import update_applier as ua


# ----------------------------------------------------------------------------------- helpers

def _portable(root: Path, name: str, exe_text: str = "old") -> Path:
    """A folder laid out exactly like the released portable ZIP's NetSpeedTray folder."""
    d = root / name
    (d / "_internal" / "PyQt6").mkdir(parents=True, exist_ok=True)
    (d / ua.APP_EXE).write_text(exe_text, encoding="utf-8")
    (d / ua.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")
    (d / "_internal" / "python313.dll").write_bytes(b"dll")
    (d / "_internal" / "PyQt6" / "QtCore.pyd").write_bytes(b"pyd")
    return d


def _backdate(path: Path, age_sec: float = 24 * 3600.0) -> None:
    old = time.time() - age_sec
    os.utime(path, (old, old))


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / "appdata" / ua._STAGED_MARKER_NAME
    monkeypatch.setattr(ua, "_marker_path", lambda: str(path))
    return path


@pytest.fixture
def root(tmp_path, monkeypatch):
    """The app-owned staging root, redirected to scratch."""
    r = tmp_path / "localappdata" / "NetSpeedTray" / "updates"
    r.mkdir(parents=True)
    monkeypatch.setattr(ua, "staging_root", lambda: str(r))
    return r


@pytest.fixture
def no_wait(monkeypatch):
    """The applier pid in the marker is this test process's own child-less pid; never block."""
    waited = []
    monkeypatch.setattr(ua, "_wait_for_exit", lambda pid, timeout=None: waited.append(pid) or True)
    return waited


@pytest.fixture
def no_relaunch(monkeypatch):
    launched = []
    monkeypatch.setattr(ua, "relaunch", lambda exe: launched.append(exe) or True)
    return launched


def _run_as(monkeypatch, folder: Path) -> None:
    """Pretend the running executable is ``folder``'s EXE."""
    monkeypatch.setattr(ua.sys, "executable", str(folder / ua.APP_EXE))


def _apply(install: Path, staged: Path) -> int:
    return ua.apply_update(str(install), wait_pid=None, staged_dir=str(staged))


def _backups(parent: Path):
    return sorted(parent.glob("*" + ua._BACKUP_SUFFIX + "*"))


# ------------------------------------------------------------------- the two reported leftovers

def test_the_staged_copy_goes_on_the_first_start_after_a_verified_swap(
        tmp_path, monkeypatch, marker, no_relaunch):
    """Leftover 1, reproduced with the API the startup ran at 2.2.0-beta.3: record (old app) ->
    apply (staged copy) -> the relaunched version sweeps seconds later. It used to defer the fresh
    folder for ten minutes, i.e. until a restart that may never come."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    ua.record_staged_path(str(staged))                       # the old version, committing

    assert _apply(install, staged) == 0                      # the staged copy, swapping
    _run_as(monkeypatch, install)                            # the relaunched new version
    assert ua.sweep_staged_leftovers() >= 1                  # (the previous version goes too)
    assert not staged.exists(), "the applied staged copy must not wait for a later restart"


def test_the_previous_version_is_kept_until_the_new_one_has_started(
        tmp_path, marker, no_relaunch):
    """The applier no longer deletes the old version: until the new one starts, it is the only
    rollback. (It used to be rmtree'd, errors ignored, the instant the old process exited.)"""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")

    assert _apply(install, staged) == 0

    (backup,) = _backups(apps)
    assert (backup / ua.APP_EXE).read_text(encoding="utf-8") == "old"
    entries = json.loads(marker.read_text(encoding="utf-8"))
    assert {"kind": "backup", "path": str(backup), "install": str(install)}.items() <= next(
        e for e in entries if e.get("kind") == "backup").items()


def test_the_janitor_removes_every_leftover_of_a_successful_update(
        tmp_path, monkeypatch, marker, root, no_wait, no_relaunch):
    """The whole flow, end to end: staged in the app-owned root with its zip beside it, applied,
    then the new version's janitor leaves nothing behind but the updated install."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(root, "NetSpeedTray-9.9.9", exe_text="new")
    package = root / "NetSpeedTray-9.9.9.zip"
    package.write_bytes(b"PK")
    ua.record_staged_path(str(staged), package=str(package))

    assert _apply(install, staged) == 0
    entries = json.loads(marker.read_text(encoding="utf-8"))
    for e in entries:                                         # the applier was another process
        e["applier_pid"] = 424242
    marker.write_text(json.dumps(entries), encoding="utf-8")
    _run_as(monkeypatch, install)
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)

    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "new"
    assert not staged.exists() and not package.exists()
    assert _backups(apps) == [], "the previous version must be gone once the new one started"
    assert sorted(p.name for p in apps.iterdir()) == ["NetSpeedTray"]
    assert not marker.exists()
    assert not root.exists(), "an emptied staging root is removed too"
    assert no_wait == [424242], "the janitor must wait for the applier (it runs from the staged copy)"


# --------------------------------------------------------------------------- dangerous cases

def test_user_files_in_the_install_are_never_deleted_with_the_previous_version(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    """Someone keeps notes, or a second tool, inside the portable folder. The swap renames that
    folder aside with their file in it; deleting the backup would silently destroy the file."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    (install / "my-notes.txt").write_text("mine", encoding="utf-8")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")

    assert _apply(install, staged) == 0
    _run_as(monkeypatch, install)
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    ua.sweep_old_backups(str(install), keep_newer_than_sec=0)   # the legacy sweep, too

    (backup,) = _backups(apps)
    assert (backup / "my-notes.txt").read_text(encoding="utf-8") == "mine"
    assert (backup / ua.APP_EXE).exists(), "a refused folder is left whole, not half-deleted"


def test_a_junction_inside_the_previous_version_is_never_followed(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "taxes.pdf").write_text("important", encoding="utf-8")
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    _winapi.CreateJunction(str(victim), str(install / "_internal" / "linked"))
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")

    assert _apply(install, staged) == 0
    _run_as(monkeypatch, install)
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    ua.sweep_old_backups(str(install), keep_newer_than_sec=0)

    assert (victim / "taxes.pdf").read_text(encoding="utf-8") == "important"
    (backup,) = _backups(apps)
    assert (backup / ua.APP_EXE).exists(), "a tree containing a link is refused outright"


def test_a_junction_named_like_a_backup_is_never_followed(tmp_path, monkeypatch):
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "taxes.pdf").write_text("important", encoding="utf-8")
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    # A folder that looks exactly like ours, reached through a junction with a backup's name.
    lookalike = _portable(tmp_path / "Elsewhere", "Real")
    _winapi.CreateJunction(str(lookalike), str(apps / ("NetSpeedTray" + ua._BACKUP_SUFFIX + "123")))
    _winapi.CreateJunction(str(victim), str(apps / ("NetSpeedTray" + ua._BACKUP_SUFFIX + "456")))
    _run_as(monkeypatch, install)

    assert ua.sweep_old_backups(str(install), keep_newer_than_sec=0) == 0
    assert (victim / "taxes.pdf").exists()
    assert (lookalike / ua.APP_EXE).exists()


def test_a_recorded_backup_that_is_a_junction_is_refused(tmp_path, monkeypatch, marker, no_wait):
    """A tampered marker naming a junction must not delete what it points at."""
    victim = _portable(tmp_path / "Elsewhere", "Victim")
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    link = apps / ("NetSpeedTray" + ua._BACKUP_SUFFIX + "123")
    _winapi.CreateJunction(str(victim), str(link))
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps([{"kind": "backup", "path": str(link), "install": str(install),
                                   "validated": False}]), encoding="utf-8")
    _run_as(monkeypatch, install)

    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert (victim / ua.APP_EXE).exists() and (victim / "_internal" / "python313.dll").exists()


def test_a_recorded_backup_outside_the_install_parent_is_refused(
        tmp_path, monkeypatch, marker, no_wait):
    """The backup must be the install's own ``<name>.old-<digits>`` sibling - nothing else."""
    elsewhere = _portable(tmp_path / "Other", "NetSpeedTray" + ua._BACKUP_SUFFIX + "123")
    oddname = _portable(tmp_path / "Apps", "NetSpeedTray" + ua._BACKUP_SUFFIX + "mine")
    install = _portable(tmp_path / "Apps", "NetSpeedTray")
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps([
        {"kind": "backup", "path": str(elsewhere), "install": str(install), "validated": False},
        {"kind": "backup", "path": str(oddname), "install": str(install), "validated": False},
    ]), encoding="utf-8")
    _run_as(monkeypatch, install)

    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert (elsewhere / ua.APP_EXE).exists() and (oddname / ua.APP_EXE).exists()


def test_a_recorded_folder_with_an_unexpected_layout_is_left_alone(
        tmp_path, monkeypatch, marker, no_relaunch):
    install = _portable(tmp_path / "Apps", "NetSpeedTray")
    no_marker = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.8")
    (no_marker / ua.PORTABLE_MARKER).unlink()                  # an installer copy, not portable
    extra = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9")
    (extra / "Documents").mkdir()
    (extra / "Documents" / "cv.docx").write_text("mine", encoding="utf-8")
    for d in (no_marker, extra):
        ua.record_staged_path(str(d))
        _backdate(d)
    _run_as(monkeypatch, install)

    assert ua.sweep_staged_leftovers() == 0
    assert (no_marker / ua.APP_EXE).exists()
    assert (extra / "Documents" / "cv.docx").exists()
    assert not marker.exists(), "refused entries are untracked, not retried forever"


def test_the_installed_copy_leaves_a_portable_backup_alone(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    """The installer build shares %APPDATA% - and so this marker - with a portable copy. Only the
    new version running from the very install the backup came from may remove it."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    assert _apply(install, staged) == 0

    _run_as(monkeypatch, _portable(tmp_path / "Program Files", "NetSpeedTray"))
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    (backup,) = _backups(apps)
    assert (backup / ua.APP_EXE).exists()
    assert any(e.get("kind") == "backup" for e in json.loads(marker.read_text(encoding="utf-8")))

    _run_as(monkeypatch, install)                             # later, the portable copy starts
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert _backups(apps) == []


def test_a_rollback_run_from_the_backup_never_deletes_itself(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    assert _apply(install, staged) == 0
    (backup,) = _backups(apps)

    _run_as(monkeypatch, backup)                              # the user went back to the old one
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert (backup / ua.APP_EXE).exists()


def test_a_locked_file_is_retried_and_the_folder_stays_recognisable_meanwhile(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    """AV holding a just-closed DLL. The first pass cannot finish; the identifying files must
    survive it (so the retry still passes the guard), and the retry finishes the job."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    assert _apply(install, staged) == 0
    (backup,) = _backups(apps)
    held = open(backup / "_internal" / "python313.dll", "rb")
    _run_as(monkeypatch, install)
    seen = []

    def _sleep(_s):
        seen.append((backup / ua.APP_EXE).exists() and (backup / ua.PORTABLE_MARKER).exists())
        held.close()                                          # the scanner lets go

    try:
        ua.run_update_janitor(retry_delays=(0.0, 0.0), sleep=_sleep)
    finally:
        held.close()
    assert seen == [True], "a lock part-way must leave the EXE and portable.marker in place"
    assert not backup.exists()
    assert not marker.exists()


def test_still_locked_after_every_retry_is_kept_for_the_next_launch(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    assert _apply(install, staged) == 0
    (backup,) = _backups(apps)
    _run_as(monkeypatch, install)

    with open(backup / "_internal" / "python313.dll", "rb"):
        ua.run_update_janitor(retry_delays=(0.0,), sleep=lambda s: None)
    assert backup.exists()
    assert any(e.get("kind") == "backup" for e in json.loads(marker.read_text(encoding="utf-8")))

    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)   # the next launch
    assert not backup.exists()


# --------------------------------------------------------------------- failed swap: keep, age out

def test_a_failed_swap_keeps_the_staged_copy_and_zip_then_ages_them_out(
        tmp_path, monkeypatch, marker, root, no_wait, no_relaunch):
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    staged = _portable(root, "NetSpeedTray-9.9.9", exe_text="new")
    package = root / "NetSpeedTray-9.9.9.zip"
    package.write_bytes(b"PK")
    ua.record_staged_path(str(staged), package=str(package))

    def _boom(src, dst, *a, **k):
        raise OSError("simulated: disk full")
    monkeypatch.setattr(ua.shutil, "copytree", _boom)
    assert _apply(install, staged) == 4
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "old", "restored"

    _backdate(staged)                                         # well past the in-flight grace
    _run_as(monkeypatch, install)
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert staged.exists() and package.exists(), "kept for a retry or a support bundle"

    entries = json.loads(marker.read_text(encoding="utf-8"))
    for e in entries:
        e["failed_at"] = time.time() - 8 * 24 * 3600
    marker.write_text(json.dumps(entries), encoding="utf-8")
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert not staged.exists() and not package.exists()


def test_the_staging_root_ages_out_only_old_unreferenced_items(
        tmp_path, monkeypatch, marker, root):
    install = _portable(tmp_path / "Apps", "NetSpeedTray")
    _run_as(monkeypatch, install)
    old_dir = _portable(root, "NetSpeedTray-1.0.0")
    old_zip = root / "NetSpeedTray-1.0.0.zip"
    old_zip.write_bytes(b"PK")
    fresh_dir = _portable(root, "NetSpeedTray-9.9.9")
    stranger = root / "notes.txt"
    stranger.write_text("not ours", encoding="utf-8")
    tracked = _portable(root, "NetSpeedTray-5.0.0")
    ua.record_staged_path(str(tracked))
    for p in (old_dir, old_zip, stranger, tracked):
        _backdate(p, 30 * 24 * 3600.0)

    assert ua.age_out_staging_root() == 2
    assert not old_dir.exists() and not old_zip.exists()
    assert fresh_dir.exists() and stranger.exists() and tracked.exists()


# ------------------------------------------------------------- where the update is staged (#260)

def _release_zip(tmp_path: Path) -> Path:
    src = tmp_path / "build"
    _portable(src, "NetSpeedTray", exe_text="new")
    z = tmp_path / "dl" / "NetSpeedTray-Portable.zip"
    z.parent.mkdir()
    with zipfile.ZipFile(z, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())
    return z


def _worker(tmp_path, z, *, app_dir, staging_target):
    from netspeedtray.core import update_installer as ui
    return ui._DownloadWorker("https://example/p.zip", str(z), portable=True,
                              extract_dir=str(tmp_path / "dl" / "extracted"),
                              checksums_url="https://example/checksums.txt",
                              expected_name="NetSpeedTray-Portable-9.9.9.zip",
                              ready_target=str(tmp_path / "Downloads" / "NetSpeedTray-9.9.9"),
                              staging_target=staging_target, app_dir=app_dir)


@pytest.fixture
def checksums(monkeypatch):
    from netspeedtray.core import update_installer as ui
    def _for(z):
        line = f"{ui._sha256_file(str(z)).upper()} NetSpeedTray-Portable-9.9.9.zip"
        monkeypatch.setattr(ui, "_fetch_checksums", lambda url: line)
    return _for


def test_a_hands_off_update_is_staged_in_the_app_folder_not_downloads(
        tmp_path, root, checksums):
    install = _portable(tmp_path / "Apps", "NetSpeedTray")
    (tmp_path / "Downloads").mkdir()
    z = _release_zip(tmp_path)
    checksums(z)
    w = _worker(tmp_path, z, app_dir=str(install),
                staging_target=str(root / "NetSpeedTray-9.9.9"))
    got = []
    w.staged.connect(got.append)

    w._run_portable()

    assert got == [str(root / "NetSpeedTray-9.9.9")]
    assert (root / "NetSpeedTray-9.9.9" / ua.APP_EXE).read_text(encoding="utf-8") == "new"
    assert (root / "NetSpeedTray-9.9.9.zip").is_file(), "the zip is kept until the swap is verified"
    assert list((tmp_path / "Downloads").iterdir()) == [], "nothing is put in the user's Downloads"


def test_the_guided_fallback_still_stages_in_downloads(tmp_path, root, checksums):
    """No hands-off possible (here: no install to swap) -> the user copies it by hand, so it
    goes where they will find it, and no zip is kept."""
    (tmp_path / "Downloads").mkdir()
    z = _release_zip(tmp_path)
    checksums(z)
    w = _worker(tmp_path, z, app_dir=str(tmp_path / "missing"),
                staging_target=str(root / "NetSpeedTray-9.9.9"))
    got = []
    w.staged.connect(got.append)

    w._run_portable()

    assert got == [str(tmp_path / "Downloads" / "NetSpeedTray-9.9.9")]
    assert list(root.iterdir()) == []


def test_a_portable_copy_inside_the_staging_root_is_not_staged_beside_itself(
        tmp_path, root, checksums):
    install = _portable(root, "Portable")                     # unzipped into our own data folder
    (tmp_path / "Downloads").mkdir()
    z = _release_zip(tmp_path)
    checksums(z)
    w = _worker(tmp_path, z, app_dir=str(install), staging_target=str(root / "NetSpeedTray-9.9.9"))
    got = []
    w.staged.connect(got.append)
    w._run_portable()
    assert got == [str(tmp_path / "Downloads" / "NetSpeedTray-9.9.9")]
    assert install.exists()


def test_is_link_like_detects_a_junction_but_not_a_plain_folder(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "j"
    _winapi.CreateJunction(str(target), str(link))
    assert ua._is_link_like(str(link)) is True
    assert ua._is_link_like(str(target)) is False
    assert ua._is_link_like(str(tmp_path / "does-not-exist")) is True, "unreadable is unsafe"
