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
    (d / ua.APP_EXE).write_bytes(b"MZ" + exe_text.encode())     # a PE header, like the real one
    (d / "_internal" / "base_library.zip").write_bytes(b"PK")    # the frozen stdlib
    (d / ua.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")
    (d / "_internal" / "python313.dll").write_bytes(b"dll")
    (d / "_internal" / "PyQt6" / "QtCore.pyd").write_bytes(b"pyd")
    return d


def _exe(d: Path) -> str:
    """The text a test put in ``d``'s EXE (after the PE header)."""
    return (d / ua.APP_EXE).read_bytes()[2:].decode()


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


def _records(marker: Path) -> Path:
    """The backups/failures record - kept apart from the staged marker older builds read (M2)."""
    return marker.parent / ua._BACKUPS_RECORD_NAME


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
    ua.record_staged_path(str(staged))

    assert _apply(install, staged) == 0

    (backup,) = _backups(apps)
    assert _exe(backup) == "old"
    entries = json.loads(_records(marker).read_text(encoding="utf-8"))
    assert {"kind": "backup", "path": str(backup), "install": str(install)}.items() <= next(
        e for e in entries if e.get("kind") == "backup").items()
    assert all("kind" not in e for e in json.loads(marker.read_text(encoding="utf-8"))), (
        "the staged marker keeps the shape older builds understand - no backup records in it")


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
    for f in (marker, _records(marker)):                      # the applier was another process
        entries = json.loads(f.read_text(encoding="utf-8"))
        for e in entries:
            e["applier_pid"] = 424242
        f.write_text(json.dumps(entries), encoding="utf-8")
    _run_as(monkeypatch, install)
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)

    assert _exe(install) == "new"
    assert not staged.exists() and not package.exists()
    assert _backups(apps) == [], "the previous version must be gone once the new one started"
    assert sorted(p.name for p in apps.iterdir()) == ["NetSpeedTray"]
    assert not marker.exists()
    assert not root.exists(), "an emptied staging root is removed too"
    assert no_wait == [424242], "the janitor must wait for the applier (it runs from the staged copy)"


# --------------------------------------------------------------------------- dangerous cases

def test_user_files_in_the_install_are_never_swapped_away(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    """Someone keeps notes, or a second tool, inside the portable folder. The swap renames the
    WHOLE folder aside, so their file would silently move into the backup (review-2 M2). The
    hands-off swap is refused and the update goes down the guided copy instead."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    (install / "my-notes.txt").write_text("mine", encoding="utf-8")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")

    assert ua.validate(str(install), str(staged)) is not None
    assert _apply(install, staged) == 3
    assert (install / "my-notes.txt").read_text(encoding="utf-8") == "mine"
    assert _exe(install) == "old" and _backups(apps) == []


def test_a_refused_backup_record_is_kept_and_logged_once(
        tmp_path, monkeypatch, marker, no_wait, caplog):
    """A recorded backup holding something that is not ours is never deleted - and its record is
    never dropped either (review-2 M2): it stays, the refusal is logged once, not every launch."""
    import logging
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    bk = _portable(apps, "NetSpeedTray" + ua._BACKUP_SUFFIX + "1790000000")
    (bk / "my-notes.txt").write_text("mine", encoding="utf-8")
    _records(marker).parent.mkdir(parents=True)
    _records(marker).write_text(json.dumps([{"kind": "backup", "path": str(bk),
                                             "install": str(install)}]), encoding="utf-8")
    _run_as(monkeypatch, install)

    with caplog.at_level(logging.WARNING, logger="NetSpeedTray.UpdateApplier"):
        for _ in range(3):
            ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert (bk / "my-notes.txt").exists()
    recs = json.loads(_records(marker).read_text(encoding="utf-8"))
    assert [r["path"] for r in recs] == [str(bk)], "the refused record must be kept"
    assert sum("left in place" in r.getMessage() for r in caplog.records) == 1


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
    _winapi.CreateJunction(str(lookalike), str(apps / ("NetSpeedTray" + ua._BACKUP_SUFFIX + "1790000123")))
    _winapi.CreateJunction(str(victim), str(apps / ("NetSpeedTray" + ua._BACKUP_SUFFIX + "1790000456")))
    _run_as(monkeypatch, install)

    assert ua.sweep_old_backups(str(install), keep_newer_than_sec=0) == 0
    assert (victim / "taxes.pdf").exists()
    assert (lookalike / ua.APP_EXE).exists()


def test_a_recorded_backup_that_is_a_junction_is_refused(tmp_path, monkeypatch, marker, no_wait):
    """A tampered marker naming a junction must not delete what it points at."""
    victim = _portable(tmp_path / "Elsewhere", "Victim")
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    link = apps / ("NetSpeedTray" + ua._BACKUP_SUFFIX + "1790000123")
    _winapi.CreateJunction(str(victim), str(link))
    marker.parent.mkdir(parents=True)
    _records(marker).write_text(json.dumps([{"kind": "backup", "path": str(link), "install": str(install),
                                   "validated": False}]), encoding="utf-8")
    _run_as(monkeypatch, install)

    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert (victim / ua.APP_EXE).exists() and (victim / "_internal" / "python313.dll").exists()


def test_a_recorded_backup_outside_the_install_parent_is_refused(
        tmp_path, monkeypatch, marker, no_wait):
    """The backup must be the install's own ``<name>.old-<digits>`` sibling - nothing else."""
    elsewhere = _portable(tmp_path / "Other", "NetSpeedTray" + ua._BACKUP_SUFFIX + "1790000123")
    oddname = _portable(tmp_path / "Apps", "NetSpeedTray" + ua._BACKUP_SUFFIX + "mine")
    install = _portable(tmp_path / "Apps", "NetSpeedTray")
    marker.parent.mkdir(parents=True)
    _records(marker).write_text(json.dumps([
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
    assert any(e.get("kind") == "backup"
               for e in json.loads(_records(marker).read_text(encoding="utf-8")))

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
    assert any(e.get("kind") == "backup"
               for e in json.loads(_records(marker).read_text(encoding="utf-8")))

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
    assert _exe(install) == "old", "restored"

    _backdate(staged)                                         # well past the in-flight grace
    _run_as(monkeypatch, install)
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert staged.exists() and package.exists(), "kept for a retry or a support bundle"

    assert any(r.get("kind") == "failure"
               for r in json.loads(_records(marker).read_text(encoding="utf-8")))
    for f in (marker, _records(marker)):
        entries = json.loads(f.read_text(encoding="utf-8"))
        for e in entries:
            e["failed_at"] = time.time() - 8 * 24 * 3600
        f.write_text(json.dumps(entries), encoding="utf-8")
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert not staged.exists() and not package.exists()
    assert not marker.exists() and not _records(marker).exists(), "aged-out records go too"


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
    assert _exe(root / "NetSpeedTray-9.9.9") == "new"
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


# ------------------------------------------------------------- adversarial review of #260 (M/L)

def test_m1_a_folder_swapped_for_a_junction_after_the_check_is_never_followed(tmp_path):
    """The guard passes, then the folder is replaced by a junction to the user's documents before
    the delete runs. The delete pins every directory by handle and sees the link for what it is."""
    import shutil
    victim = tmp_path / "UserDocs"
    victim.mkdir()
    for n in ("thesis.docx", "photos.zip"):
        (victim / n).write_text("precious", encoding="utf-8")
    staged = _portable(tmp_path, "NetSpeedTray-9.9.9")
    assert ua._portable_tree_refusal(str(staged)) is None
    shutil.rmtree(staged)
    _winapi.CreateJunction(str(victim), str(staged))

    assert ua._remove_portable_tree(str(staged)) is False
    assert sorted(p.name for p in victim.iterdir()) == ["photos.zip", "thesis.docx"]


def test_m1_a_subfolder_swapped_for_a_junction_is_never_followed(tmp_path):
    import shutil
    victim = tmp_path / "UserDocs"
    victim.mkdir()
    (victim / "thesis.docx").write_text("precious", encoding="utf-8")
    d = _portable(tmp_path, "NetSpeedTray-9.9.9")
    assert ua._portable_tree_refusal(str(d)) is None
    shutil.rmtree(d / "_internal" / "PyQt6")
    _winapi.CreateJunction(str(victim), str(d / "_internal" / "PyQt6"))

    assert ua._remove_portable_tree(str(d)) is False
    assert (victim / "thesis.docx").read_text(encoding="utf-8") == "precious"


def test_m2_backups_are_recorded_where_older_builds_never_look(tmp_path, marker, no_relaunch):
    """Builds up to 2.2.0-beta.3 delete any recorded `NetSpeedTray-*` folder with an EXE from the
    staged marker after ten minutes, and rmtree every `<install>.old-*` sibling at startup. The
    backup of an install named NetSpeedTray-Portable (holding a user's file) must be in neither."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray-Portable")
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    ua.record_staged_path(str(staged))

    assert _apply(install, staged) == 0

    (backup,) = _backups(apps)
    legacy = json.loads(marker.read_text(encoding="utf-8"))
    assert [os.path.normcase(e["path"]) for e in legacy] == [os.path.normcase(str(staged))]
    assert all("kind" not in e for e in legacy)
    assert not backup.name.lower().startswith("netspeedtray-portable.old-")


def test_m3_a_partial_legacy_backup_is_found_and_removed(tmp_path, monkeypatch, marker, no_wait):
    """2.1.7's cleanup_backup rmtree'd with errors ignored: an AV-held file leaves just
    `_internal/PyQt6/QtCore.pyd`. The janitor (passing the running folder in its ORIGINAL case)
    must find it, and accept that subset."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    bk = apps / "NetSpeedTray.old-1700000000"
    (bk / "_internal" / "PyQt6").mkdir(parents=True)
    (bk / "_internal" / "PyQt6" / "QtCore.pyd").write_bytes(b"pyd")
    _backdate(bk)
    _run_as(monkeypatch, install)

    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert not bk.exists()


def test_l1_an_av_held_exe_leaves_a_folder_the_retry_still_recognises(tmp_path):
    d = _portable(tmp_path, "NetSpeedTray.old-1700000000")
    with open(d / ua.APP_EXE, "rb"):
        assert ua._remove_portable_tree(str(d)) is False
    assert ua._portable_tree_refusal(str(d)) is None
    assert ua._remove_portable_tree(str(d)) is True


def test_l1_a_folder_with_only_the_marker_left_is_still_ours(tmp_path):
    d = tmp_path / "NetSpeedTray.old-1700000000"
    d.mkdir()
    (d / ua.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")
    assert ua._portable_tree_refusal(str(d)) is None


def test_l2_a_read_only_subfolder_is_removed_in_one_pass(tmp_path):
    import subprocess
    d = _portable(tmp_path, "NetSpeedTray.old-1700000000")
    sub = d / "_internal" / "PyQt6"
    subprocess.run(["attrib", "+R", str(sub)], check=True)
    try:
        assert ua._remove_portable_tree(str(d)) is True
    finally:
        if sub.exists():
            subprocess.run(["attrib", "-R", str(sub)])
    assert not d.exists()


def test_l3_a_tampered_applied_entry_cannot_delete_another_install(tmp_path, monkeypatch, marker):
    other = _portable(tmp_path / "D_Tools", "NetSpeedTray-Portable")
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps([{"path": str(other), "applied": True}]), encoding="utf-8")
    _run_as(monkeypatch, _portable(tmp_path / "elsewhere", "NetSpeedTray"))

    assert ua.sweep_staged_leftovers() == 0
    assert (other / ua.APP_EXE).exists()


def test_l4_after_a_failed_restore_the_only_complete_copy_survives(
        tmp_path, monkeypatch, marker, no_wait):
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")                       # partial NEW copy
    (install / "_internal" / "PyQt6" / "QtCore.pyd").unlink()
    bk = _portable(apps, "NetSpeedTray.old-1792590000")             # the only complete copy
    _backdate(bk, 5 * 86400)                                         # a rename keeps the old mtime
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps([{"path": str(tmp_path / "NetSpeedTray-9.9.9"),
                                   "failed_at": time.time()}]), encoding="utf-8")
    _run_as(monkeypatch, install)

    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert ua.sweep_old_backups(str(install)) == 0                  # and called directly, after
    assert (bk / "_internal" / "PyQt6" / "QtCore.pyd").exists()


def test_l4_an_incomplete_running_install_never_removes_a_previous_version(tmp_path, monkeypatch):
    apps = tmp_path / "Apps"
    install = _portable(apps, "NetSpeedTray")
    (install / ua.PORTABLE_MARKER).unlink()
    bk = _portable(apps, "NetSpeedTray.old-1792590000")
    _backdate(bk)
    _run_as(monkeypatch, install)
    assert ua.sweep_old_backups(str(install), keep_newer_than_sec=0) == 0
    assert bk.exists()


def test_a_hard_linked_read_only_file_is_never_made_writable(tmp_path):
    import stat as _stat
    d = _portable(tmp_path, "NetSpeedTray.old-1")
    outside = tmp_path / "user.txt"
    outside.write_text("keep", encoding="utf-8")
    os.chmod(outside, _stat.S_IREAD)
    os.link(outside, d / "_internal" / "hl.txt")
    try:
        assert ua._portable_tree_refusal(str(d)) is not None
        assert ua._remove_portable_tree(str(d)) is False
        assert outside.read_text(encoding="utf-8") == "keep"
        assert not os.access(outside, os.W_OK), "chmod on a hard link changes the user's file"
    finally:
        os.chmod(outside, _stat.S_IWRITE)


def _secure_updater():
    from types import SimpleNamespace
    from netspeedtray.core import update_installer as ui
    return ui.SecureUpdater(None, "", "https://example/release", SimpleNamespace(), portable=True,
                            portable_url="https://example/p.zip", latest_version="9.9.9")


def test_l5_a_failed_handoff_moves_the_copy_to_downloads(q_app, tmp_path, monkeypatch, root):
    """Popen blocked (AppLocker/AV): the guided message must not send the user into AppData, to a
    folder that is aged out in a week."""
    from netspeedtray.core import update_installer as ui
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(ui, "_downloads_dir", lambda: str(downloads))
    ready = _portable(root, "NetSpeedTray-9.9.9", exe_text="new")
    (root / "NetSpeedTray-9.9.9.zip").write_bytes(b"PK")

    moved = _secure_updater()._move_to_downloads(str(ready))

    assert moved == str(downloads / "NetSpeedTray-9.9.9")
    assert (downloads / "NetSpeedTray-9.9.9" / ua.APP_EXE).exists()
    assert not ready.exists() and not (root / "NetSpeedTray-9.9.9.zip").exists()


def test_l5_cancel_never_deletes_a_zip_beside_a_downloads_folder(q_app, tmp_path):
    downloads = tmp_path / "Downloads"
    ready = _portable(downloads, "NetSpeedTray-9.9.9", exe_text="new")
    theirs = downloads / "NetSpeedTray-9.9.9.zip"
    theirs.write_bytes(b"the user's own download")
    up = _secure_updater()
    up._user_cancelled = True

    up._on_staged(str(ready))

    assert theirs.exists()


def test_the_test_session_never_points_at_the_real_profile():
    """The conftest floor: nothing in the suite can write the maintainer's %APPDATA%."""
    from netspeedtray.utils.helpers import get_app_data_path
    real = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    assert not os.path.normcase(str(get_app_data_path())).startswith(os.path.normcase(real))


# ------------------------------------------------------------- second adversarial review (#260)

_STAMP = "1790000000"                                  # a plausible int(time.time()) (2026-09)


def _whole_install(apps: Path) -> Path:
    return _portable(apps, "NetSpeedTray")


def test_r2_m1_a_users_old_folder_beside_the_install_survives_every_launch(tmp_path, monkeypatch):
    """S1: `Tools.old-<stamp>` next to a portable named `Tools`, holding only `_internal\\photos`."""
    install = _portable(tmp_path, "Tools")
    user = tmp_path / ("Tools.old-" + _STAMP)
    (user / "_internal" / "photos").mkdir(parents=True)
    (user / "_internal" / "photos" / "kid.jpg").write_text("precious", encoding="utf-8")
    _backdate(user)
    _run_as(monkeypatch, install)
    assert ua.sweep_old_backups(str(install), keep_newer_than_sec=0) == 0
    assert (user / "_internal" / "photos" / "kid.jpg").exists()


def test_r2_m1_a_users_rollback_copy_with_a_short_suffix_survives(tmp_path, monkeypatch):
    """S2: the user's own complete copy, `NetSpeedTray.old-2` - not a timestamp."""
    install = _whole_install(tmp_path)
    mine = _portable(tmp_path, "NetSpeedTray.old-2", exe_text="my rollback")
    _backdate(mine)
    _run_as(monkeypatch, install)
    assert ua.sweep_old_backups(str(install), keep_newer_than_sec=0) == 0
    assert _exe(mine) == "my rollback"


def test_r2_m1_this_versions_backups_are_never_swept_unrecorded(tmp_path, monkeypatch):
    install = _whole_install(tmp_path)
    prev = _portable(tmp_path, "NetSpeedTray" + ua._BACKUP_SUFFIX + _STAMP)
    _backdate(prev)
    _run_as(monkeypatch, install)
    assert ua.sweep_old_backups(str(install), keep_newer_than_sec=0) == 0
    assert prev.exists(), "a .previous- backup is only ever removed through its record"


@pytest.mark.parametrize("name", [
    "Kit.old-" + _STAMP,                        # KELVIN SIGN, which str.lower() folds to 'k'
    "Kit.old-١٢٣٤٥٦٧٨٩٠",   # Arabic-Indic
    "Kit.old-¹²³" + "1234567",        # superscripts
    "Kit.old-17900000",                              # too short
    "Kit.old-9999999999",                            # in the future
    "Kit.old-1000000000",                            # 2001: before any portable updater
])
def test_r2_m1_only_a_plausible_ascii_timestamp_names_a_backup(tmp_path, name):
    install = _portable(tmp_path, "Kit")
    assert not ua._is_backup_name(str(tmp_path / name), str(install))
    assert ua._is_backup_name(str(tmp_path / ("kit.OLD-" + _STAMP)), str(install))


def test_r2_m1_a_real_partial_leftover_is_still_removed(tmp_path, monkeypatch):
    """What 2.1.7 really leaves - a locked Qt module - still carries the build's fingerprint."""
    install = _whole_install(tmp_path)
    bk = tmp_path / ("NetSpeedTray.old-" + _STAMP)
    (bk / "_internal" / "PyQt6").mkdir(parents=True)
    (bk / "_internal" / "PyQt6" / "QtCore.pyd").write_bytes(b"pyd")
    _backdate(bk)
    _run_as(monkeypatch, install)
    assert ua.sweep_old_backups(str(install), keep_newer_than_sec=0) == 1
    assert not bk.exists()


def test_r2_l1_a_folder_recreated_at_the_install_path_is_never_wiped(
        tmp_path, monkeypatch, marker, root, no_relaunch):
    """S7: something re-creates the install path between the rename and the copy."""
    install = _portable(tmp_path / "Apps", "P")
    staged = _portable(root, "NetSpeedTray-4.0.0", exe_text="new")
    orig = ua._rename_with_retry

    def _rename_then_recreate(src, dst, timeout=0):
        orig(src, dst, 0)
        os.makedirs(src)
        (Path(src) / "SOMEONE_ELSES.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ua, "_rename_with_retry", _rename_then_recreate)

    assert _apply(install, staged) == 4
    assert (install / "SOMEONE_ELSES.txt").exists()
    (backup,) = _backups(tmp_path / "Apps")
    assert _exe(backup) == "old"
    recs = json.loads(_records(marker).read_text(encoding="utf-8"))
    assert any(r.get("restore_failed") and r.get("backup") == str(backup) for r in recs)


def test_r2_m3_a_failed_restore_records_the_only_good_copy_forever(
        tmp_path, monkeypatch, marker, root, no_relaunch, no_wait):
    """S8: copy fails, then the restore fails too. The backup is named in a no-expiry record and
    nothing removes it - not after 8 days, not even with a whole-looking install in place."""
    apps = tmp_path / "Apps"
    install = _portable(apps, "Q")
    staged = _portable(root, "NetSpeedTray-9.9.9", exe_text="new")

    def _boom(src, dst, *a, **k):
        _portable(Path(dst).parent, Path(dst).name, exe_text="partial")   # a partial copy...
        raise OSError("simulated: disk full")
    monkeypatch.setattr(ua.shutil, "copytree", _boom)
    real_rename = os.rename

    def _no_restore(a, b):
        if Path(a).name.startswith("Q" + ua._BACKUP_SUFFIX):
            raise OSError("simulated: restore blocked")
        return real_rename(a, b)
    monkeypatch.setattr(ua.os, "rename", _no_restore)
    monkeypatch.setattr(ua.shutil, "rmtree", lambda *a, **k: None)          # ...that stays put

    assert _apply(install, staged) == 4
    monkeypatch.undo()
    monkeypatch.setattr(ua, "_marker_path", lambda: str(marker))
    monkeypatch.setattr(ua, "_wait_for_exit", lambda pid, timeout=None: True)
    (backup,) = _backups(apps)
    recs = json.loads(_records(marker).read_text(encoding="utf-8"))
    for r in recs:
        r["failed_at"] = time.time() - 8 * 86400
    recs.append({"kind": "backup", "path": str(backup), "install": str(install)})   # tampered
    _records(marker).write_text(json.dumps(recs), encoding="utf-8")
    _run_as(monkeypatch, install)

    for _ in range(2):
        ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert _exe(backup) == "old", "the only complete copy must never be removed"


def test_r2_m3_an_install_without_its_frozen_stdlib_is_not_complete(tmp_path):
    install = _portable(tmp_path, "NetSpeedTray")
    assert ua._install_is_complete(str(install))
    (install / "_internal" / "base_library.zip").unlink()
    assert not ua._install_is_complete(str(install))
    (install / "_internal" / "base_library.zip").write_bytes(b"PK")
    (install / ua.APP_EXE).write_text("not a program", encoding="utf-8")
    assert not ua._install_is_complete(str(install))


def test_r2_l2_an_unreadable_backups_record_protects_every_backup(tmp_path, monkeypatch, marker):
    """S10: a truncated update_backups.json may have held a failure record."""
    install = _whole_install(tmp_path)
    bk = _portable(tmp_path, "NetSpeedTray.old-" + _STAMP)
    _backdate(bk)
    _records(marker).parent.mkdir(parents=True)
    _records(marker).write_text('[{"kind": "failure", "install": "x", "failed_at": 1',
                                encoding="utf-8")
    _run_as(monkeypatch, install)
    ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert bk.exists()
    assert _records(marker).read_text(encoding="utf-8").startswith('[{"kind"'), "left as found"


def test_r2_l2_records_are_written_through_a_unique_temp_file(tmp_path, marker):
    """Two processes never share a temp name: a stale `<record>.tmp` (another writer's, or a
    leftover) does not stop the write."""
    (marker.parent).mkdir(parents=True)
    (marker.parent / (ua._BACKUPS_RECORD_NAME + ".tmp")).mkdir()          # blocks a fixed name
    ua._write_list(str(_records(marker)), [{"kind": "failure", "install": "x", "failed_at": 1}])
    assert json.loads(_records(marker).read_text(encoding="utf-8"))[0]["install"] == "x"
    assert sorted(p.name for p in marker.parent.iterdir()) == [
        ua._BACKUPS_RECORD_NAME, ua._BACKUPS_RECORD_NAME + ".tmp"]


def test_r2_l3_a_permanently_locked_leftover_is_given_up_on_not_retried_forever(
        tmp_path, monkeypatch, marker, no_wait, no_relaunch):
    apps = tmp_path / "Apps"
    install = _whole_install(apps)
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    assert _apply(install, staged) == 0
    (backup,) = _backups(apps)
    _run_as(monkeypatch, install)
    with open(backup / "_internal" / "python313.dll", "rb"):
        for _ in range(ua._MAX_LAUNCH_ATTEMPTS):
            ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert backup.exists(), "giving up never means deleting some other way"
    assert not _records(marker).exists() or not any(
        r.get("kind") == "backup" for r in json.loads(_records(marker).read_text("utf-8")))


def test_r2_l3_the_applier_pid_is_forgotten_once_seen_exited(
        tmp_path, monkeypatch, marker, no_relaunch):
    apps = tmp_path / "Apps"
    install = _whole_install(apps)
    staged = _portable(tmp_path / "Downloads", "NetSpeedTray-9.9.9", exe_text="new")
    ua.record_staged_path(str(staged))
    assert _apply(install, staged) == 0
    for f in (marker, _records(marker)):
        items = json.loads(f.read_text(encoding="utf-8"))
        for it in items:
            it["applier_pid"] = 424242
        f.write_text(json.dumps(items), encoding="utf-8")
    waited = []
    monkeypatch.setattr(ua, "_wait_for_exit", lambda pid, timeout=None: waited.append(pid) or True)
    _run_as(monkeypatch, install)
    with open(_backups(apps)[0] / "_internal" / "python313.dll", "rb"):   # keep a record alive
        ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
        ua.run_update_janitor(retry_delays=(), sleep=lambda s: None)
    assert waited == [424242], "a recycled PID must not be waited on every launch"


def test_r2_l4_the_verdict_is_taken_again_once_the_folder_is_pinned(tmp_path, monkeypatch):
    """Something drops a user file into the folder after the first check: the re-check under the
    pinning handle refuses, and nothing is deleted."""
    d = _portable(tmp_path, "NetSpeedTray.old-" + _STAMP)
    assert ua._portable_tree_refusal(str(d)) is None
    real = ua._HeldDir

    class _LateWriter(real):
        def __init__(self, path):
            if os.path.normcase(path) == os.path.normcase(str(d)):
                (d / "late-user-file.txt").write_text("mine", encoding="utf-8")
            super().__init__(path)
    monkeypatch.setattr(ua, "_HeldDir", _LateWriter)

    assert ua._remove_portable_tree(str(d)) is False
    assert (d / "late-user-file.txt").exists() and (d / "_internal" / "python313.dll").exists()


def test_r2_l5_a_cross_drive_move_that_copied_fully_points_at_the_copy(
        q_app, tmp_path, monkeypatch, root):
    from netspeedtray.core import update_installer as ui
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(ui, "_downloads_dir", lambda: str(downloads))
    ready = _portable(root, "NetSpeedTray-9.9.9", exe_text="new")
    import shutil as _sh

    def _copy_then_fail(src, dst):
        _sh.copytree(src, dst)
        raise PermissionError("simulated: AV holds a file in the source")
    monkeypatch.setattr(ui.shutil, "move", _copy_then_fail)

    assert _secure_updater()._move_to_downloads(str(ready)) == str(downloads / "NetSpeedTray-9.9.9")
