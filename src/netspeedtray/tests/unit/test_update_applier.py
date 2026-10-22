"""
The hands-off portable update: it replaces the user's application folder, so the bar is that a
failure must never leave them worse off than not updating at all.

These tests run against **real directories**, not mocks. The whole feature is filesystem semantics -
what renames, what moves, what survives a failure - and a mocked `os.rename` would prove nothing
about the thing that can actually hurt someone.

Two properties matter more than the happy path:

1. **Every doubtful case is a refusal, not a repair.** `validate()` returning a reason means the
   guided "copy it yourself" flow still happens, which is the pre-existing behavior and is safe.
2. **A failed swap restores the original.** The install folder is renamed aside before anything is
   written, so a failure is reversible. Deleting first would not be.

The copy-vs-move distinction below is not stylistic: the applier runs *from* the staged folder, and
Windows will not move a directory containing a running executable. A live test against real binaries
found that; these tests, with their dummy text files, had not.
"""

import json
import logging
import os
import shutil
import time
from pathlib import Path

import pytest

from netspeedtray.core import update_applier as ua


def _make_install(root: Path, name: str = "NetSpeedTray", *, with_exe: bool = True) -> Path:
    d = root / name
    (d / "_internal").mkdir(parents=True, exist_ok=True)
    if with_exe:
        (d / ua.APP_EXE).write_text("old", encoding="utf-8")
    (d / "_internal" / "data.bin").write_text("payload", encoding="utf-8")
    (d / ua.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")   # as the release ZIP ships
    return d


def _make_staged(root: Path, name: str = "staged", *, with_exe: bool = True) -> Path:
    d = root / name
    (d / "_internal").mkdir(parents=True, exist_ok=True)
    if with_exe:
        (d / ua.APP_EXE).write_text("new", encoding="utf-8")
    # A nested payload, so a test can tell a whole-tree copy from just the EXE landing.
    (d / "_internal" / "data.bin").write_text("new-payload", encoding="utf-8")
    (d / ua.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")
    return d


# --------------------------------------------------------------------------- validate

def test_validate_accepts_a_normal_pair(tmp_path):
    assert ua.validate(str(_make_install(tmp_path)), str(_make_staged(tmp_path))) is None


def test_validate_refuses_a_directory_that_is_not_ours(tmp_path):
    """The flag must never become a way to move an arbitrary folder off someone's disk."""
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "taxes.pdf").write_text("important", encoding="utf-8")
    reason = ua.validate(str(victim), str(_make_staged(tmp_path)))
    assert reason and ua.APP_EXE in reason
    assert (victim / "taxes.pdf").exists()


def test_validate_refuses_when_staged_is_inside_the_install(tmp_path):
    """Renaming the install would drag the staged copy - and the running process - with it."""
    install = _make_install(tmp_path)
    nested = _make_staged(install, "staged_inside")
    reason = ua.validate(str(install), str(nested))
    assert reason and "inside" in reason


def test_validate_refuses_identical_paths(tmp_path):
    install = _make_install(tmp_path)
    assert ua.validate(str(install), str(install)) is not None


def test_validate_refuses_a_staged_copy_with_no_exe(tmp_path):
    staged = _make_staged(tmp_path, with_exe=False)
    reason = ua.validate(str(_make_install(tmp_path)), str(staged))
    assert reason and ua.APP_EXE in reason


def test_validate_refuses_missing_directories(tmp_path):
    assert ua.validate(str(tmp_path / "nope"), str(_make_staged(tmp_path))) is not None
    assert ua.validate(str(_make_install(tmp_path)), str(tmp_path / "nope")) is not None
    assert ua.validate("", "") is not None


# --------------------------------------------------------------------------- swap

def test_swap_replaces_the_install_and_keeps_a_backup(tmp_path):
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)

    backup = ua.swap(str(install), str(staged))

    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "new", "install was not replaced"
    assert (install / "_internal" / "data.bin").read_text(encoding="utf-8") == "new-payload", (
        "the nested tree was not copied - only the EXE landed")
    assert staged.exists(), "staged must survive - the applier runs from it (see the copy-vs-move note)"
    assert Path(backup).is_dir() and (Path(backup) / ua.APP_EXE).read_text(encoding="utf-8") == "old"


def test_a_failed_copy_restores_the_original_install(tmp_path, monkeypatch):
    """The property that matters: a failure leaves the user exactly as they started."""
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)

    def boom(*a, **k):
        raise OSError("simulated: disk full midway through the copy")

    monkeypatch.setattr(ua.shutil, "copytree", boom)

    with pytest.raises(OSError):
        ua.swap(str(install), str(staged))

    assert install.is_dir(), "the install directory was not restored"
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "old"
    assert (install / "_internal" / "data.bin").exists(), "install contents were lost"
    assert staged.is_dir(), "the staged copy should still be there for the guided fallback"


def test_a_partially_written_destination_still_restores(tmp_path, monkeypatch):
    """The failure a live test found, which mocked filesystems had hidden.

    The first implementation used `shutil.move`. Against real binaries it failed with WinError 5 -
    the applier runs FROM the staged folder and Windows will not move a directory containing a
    running executable - and because `move` degrades to copy-then-delete it had already created a
    *partial* destination. The restore then failed too, because `os.rename` had nowhere to land, and
    the user was left with a half-copied app plus their real one under a `.old-` name.

    So: a copy that dies midway must still leave the original install exactly as it was.
    """
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)

    real_copytree = ua.shutil.copytree

    def half_copy(src, dst, *a, **k):
        os.makedirs(dst, exist_ok=True)
        with open(os.path.join(dst, "partial.tmp"), "w", encoding="utf-8") as fh:
            fh.write("half a copy")
        raise OSError("simulated: died midway through the copy")

    monkeypatch.setattr(ua.shutil, "copytree", half_copy)

    with pytest.raises(OSError):
        ua.swap(str(install), str(staged))

    assert install.is_dir(), "the install directory was not restored"
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "old"
    assert (install / "_internal" / "data.bin").exists()
    assert not (install / "partial.tmp").exists(), "the partial copy was left behind"
    assert not list(tmp_path.glob("*" + ua._BACKUP_SUFFIX + "*")), "the backup should be gone"
    ua.shutil.copytree = real_copytree


def test_the_staged_copy_survives_the_swap(tmp_path, monkeypatch):
    """It must be copied, not moved: the applier is running from it and cannot delete itself."""
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)
    ua.swap(str(install), str(staged))
    assert staged.is_dir(), "staged was moved - the applier cannot move the folder it runs from"
    assert (staged / ua.APP_EXE).exists()


# ------------------------------------------------------------- staged-leftover sweep (marker-based)
#
# v2.1.5 item 3. The old sweep deleted ANY `NetSpeedTray-*` folder containing the app EXE - which is
# exactly what a tester's rollback copy looks like. The beta cycle is precisely when people keep a
# "NetSpeedTray-2.1.5-backup" folder beside the install, and the app was deleting it, silently.
# Now the hands-off update RECORDS the exact folder it staged, and the sweep removes only recorded
# paths: no marker, no sweep, and every removal is logged with its full path.


@pytest.fixture
def marker(tmp_path, monkeypatch):
    """Point the staged-update marker at a scratch file - never the user's real %APPDATA%."""
    path = tmp_path / "appdata" / ua._STAGED_MARKER_NAME
    monkeypatch.setattr(ua, "_marker_path", lambda: str(path))
    return path


def test_a_testers_backup_folder_survives_repeated_sweeps(tmp_path, monkeypatch, marker):
    """THE pinned scenario: a rollback copy beside the install AND one in Downloads both survive
    the sweep across repeated launches. This is the tester's only escape route from a bad beta."""
    beside = tmp_path / "PortableApps"
    downloads = tmp_path / "Downloads"
    install = _make_install(beside, "NetSpeedTray")
    backup_beside = _make_install(beside, "NetSpeedTray-2.1.5-backup")
    backup_downloads = _make_install(downloads, "NetSpeedTray-2.1.5-backup")
    monkeypatch.setattr(ua.sys, "executable", str(install / ua.APP_EXE))

    for _ in range(3):  # three consecutive launches, per the readiness checklist
        assert ua.sweep_staged_leftovers() == 0, "no marker present: the sweep must remove NOTHING"

    assert backup_beside.is_dir() and (backup_beside / ua.APP_EXE).exists()
    assert backup_downloads.is_dir() and (backup_downloads / ua.APP_EXE).exists()
    assert install.is_dir()


def test_a_recorded_staged_folder_is_removed_and_logged(tmp_path, monkeypatch, marker, caplog):
    """The genuine leftover - the folder the applier ran from and could not delete - IS removed,
    the removal is logged at INFO with the full path, and a lookalike beside it is spared."""
    downloads = tmp_path / "Downloads"
    staged = _make_install(downloads, "NetSpeedTray-2.1.6")
    lookalike = _make_install(downloads, "NetSpeedTray-2.1.5-backup")
    install = _make_install(tmp_path, "NetSpeedTray")
    monkeypatch.setattr(ua.sys, "executable", str(install / ua.APP_EXE))

    ua.record_staged_path(str(staged))
    _backdate(staged)   # old enough that no handoff could still be using it (see review C1)
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.UpdateApplier"):
        removed = ua.sweep_staged_leftovers()

    assert removed == 1
    assert not staged.exists(), "the recorded staged folder should be swept"
    assert lookalike.is_dir(), "only RECORDED paths may be removed"
    assert any(str(staged) in rec.getMessage()
               for rec in caplog.records if rec.levelno == logging.INFO), (
        "every removal must be logged with the full path")
    assert not marker.exists(), "a fully processed marker must clean itself up"
    assert ua.sweep_staged_leftovers() == 0, "a second sweep has nothing left to do"


def test_a_stale_marker_entry_cleans_itself_without_crashing(tmp_path, monkeypatch, marker):
    """A marker pointing at a since-deleted path is dropped quietly - no crash, no residue."""
    install = _make_install(tmp_path)
    monkeypatch.setattr(ua.sys, "executable", str(install / ua.APP_EXE))
    ua.record_staged_path(str(tmp_path / "NetSpeedTray-long-gone"))

    assert ua.sweep_staged_leftovers() == 0
    assert not marker.exists(), "the stale marker must clean itself up"


def test_a_corrupt_marker_removes_nothing_and_cleans_itself(tmp_path, monkeypatch, marker):
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{this is not json", encoding="utf-8")
    backup = _make_install(tmp_path, "NetSpeedTray-2.1.5-backup")
    install = _make_install(tmp_path, "NetSpeedTray")
    monkeypatch.setattr(ua.sys, "executable", str(install / ua.APP_EXE))

    assert ua.sweep_staged_leftovers() == 0
    assert backup.is_dir(), "an unreadable marker must not degrade to deleting lookalikes"
    assert not marker.exists()


def test_the_running_copy_is_never_removed_even_if_recorded(tmp_path, monkeypatch, marker):
    """Defense in depth: an entry naming the folder we run from is deferred, never deleted.
    (The --apply-update process runs from the staged folder; the entry must survive for the
    relaunched install to act on.)"""
    staged = _make_install(tmp_path, "NetSpeedTray-2.1.6")
    monkeypatch.setattr(ua.sys, "executable", str(staged / ua.APP_EXE))
    ua.record_staged_path(str(staged))

    assert ua.sweep_staged_leftovers() == 0
    assert staged.is_dir(), "never delete the ground we stand on"
    assert os.path.abspath(str(staged)) in [
        e["path"] for e in json.loads(marker.read_text(encoding="utf-8"))], (
        "the entry must be kept for the relaunched copy to sweep")


def test_a_recorded_path_that_is_not_ours_is_refused_and_untracked(tmp_path, monkeypatch, marker):
    """A corrupted/tampered marker must not become a way to delete an arbitrary folder."""
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "taxes.pdf").write_text("important", encoding="utf-8")
    install = _make_install(tmp_path)
    monkeypatch.setattr(ua.sys, "executable", str(install / ua.APP_EXE))
    ua.record_staged_path(str(victim))

    assert ua.sweep_staged_leftovers() == 0
    assert (victim / "taxes.pdf").exists(), "a folder that does not look like ours is never removed"
    assert not marker.exists(), "the refused entry is dropped rather than retried forever"


def test_record_staged_path_appends_and_dedupes(tmp_path, marker):
    first = tmp_path / "NetSpeedTray-2.1.6"
    second = tmp_path / "NetSpeedTray-2.1.7"
    ua.record_staged_path(str(first))
    ua.record_staged_path(str(first))     # recording twice must not duplicate
    ua.record_staged_path(str(second))
    assert json.loads(marker.read_text(encoding="utf-8")) == [
        {"path": os.path.abspath(str(first)), "validated": False},
        {"path": os.path.abspath(str(second)), "validated": False}]


def test_backup_paths_do_not_collide(tmp_path):
    a = ua._backup_path(str(tmp_path / "App"))
    assert a.startswith(str(tmp_path / "App") + ua._BACKUP_SUFFIX)
    assert a != str(tmp_path / "App")


# --------------------------------------------------------------------------- sweep

def test_sweep_removes_old_backups_but_spares_recent_ones(tmp_path):
    install = _make_install(tmp_path)
    (install / ua.APP_EXE).write_bytes(b"MZ")                      # a whole, real-looking install
    (install / "_internal" / "base_library.zip").write_bytes(b"PK")
    stale = tmp_path / ("NetSpeedTray" + ua._LEGACY_BACKUP_SUFFIX + "1790000001")
    stale.mkdir()
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    fresh = tmp_path / ("NetSpeedTray" + ua._LEGACY_BACKUP_SUFFIX + "1790000002")
    fresh.mkdir()

    removed = ua.sweep_old_backups(str(install))

    assert removed == 1
    assert not stale.exists(), "an hour-old backup should be swept"
    assert fresh.exists(), "a backup from seconds ago may belong to an update still in flight"
    assert install.is_dir(), "the sweep must never touch the install itself"


def test_sweep_ignores_unrelated_folders(tmp_path):
    install = _make_install(tmp_path)
    bystander = tmp_path / "NetSpeedTray-notes"
    bystander.mkdir()
    os.utime(bystander, (time.time() - 3600, time.time() - 3600))
    ua.sweep_old_backups(str(install))
    assert bystander.exists()


# --------------------------------------------------------------------------- CLI

def test_cli_returns_none_without_the_flag():
    """The normal launch must be entirely unaffected."""
    assert ua.run_apply_update_cli([]) is None
    assert ua.run_apply_update_cli(["--export-csv", "--period", "24h"]) is None


def test_cli_refuses_and_reports_when_validation_fails(tmp_path):
    """A refusal is a non-zero exit, not a crash and not a partial swap."""
    victim = tmp_path / "Documents"
    victim.mkdir()
    staged = _make_staged(tmp_path)
    code = ua.run_apply_update_cli(
        ["--apply-update", str(victim), "--staged-dir", str(staged)])
    assert code == 3
    assert victim.is_dir()


def test_cli_applies_a_valid_update(tmp_path, monkeypatch, marker):
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)
    launched = {}
    monkeypatch.setattr(ua, "relaunch", lambda exe: launched.setdefault("exe", exe) or True)

    code = ua.run_apply_update_cli(
        ["--apply-update", str(install), "--staged-dir", str(staged)])

    assert code == 0
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "new"
    assert launched["exe"] == os.path.join(str(install), ua.APP_EXE)
    backups = list(tmp_path.glob("*" + ua._BACKUP_SUFFIX + "*"))
    assert len(backups) == 1, "the previous version is the rollback until the new one has started"
    records = marker.parent / ua._BACKUPS_RECORD_NAME
    assert any(e.get("kind") == "backup" for e in json.loads(records.read_text(encoding="utf-8"))), (
        "the backup must be recorded for the new version's janitor")


def test_apply_refuses_if_the_old_process_never_exits(tmp_path, monkeypatch):
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)
    monkeypatch.setattr(ua, "_wait_for_exit", lambda pid, timeout=None: False)

    code = ua.apply_update(str(install), wait_pid=4321, staged_dir=str(staged))

    assert code == 2
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "old", "swapped despite the timeout"


def test_waiting_on_a_dead_pid_returns_immediately():
    """A PID that cannot be opened has already exited - that is success, not an error."""
    assert ua._wait_for_exit(0x7FFFFFFE, timeout=1.0) is True


# --- adversarial-review regressions (C1, C3): the sweep vs the update handoff -------------------

def _backdate(path, age_sec=24 * 3600.0):
    """Make a folder look old enough that no update handoff could still be using it."""
    old = time.time() - age_sec
    os.utime(path, (old, old))


def test_a_fresh_staged_folder_is_never_swept_mid_handoff(tmp_path, monkeypatch, marker):
    """Review C1: a duplicate launch during the handoff window must not gut the staged folder
    the applier is still running from. A recorded folder with a fresh mtime is deferred."""
    downloads = tmp_path / "Downloads"
    staged = _make_install(downloads, "NetSpeedTray-2.1.6")
    payload = staged / "_internal" / "matplotlib.pyd"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"x")
    install = _make_install(tmp_path, "NetSpeedTray")
    monkeypatch.setattr(ua.sys, "executable", str(install / ua.APP_EXE))

    ua.record_staged_path(str(staged))          # mtime is NOW: a handoff may be in flight

    assert ua.sweep_staged_leftovers() == 0
    assert payload.exists() and (staged / ua.APP_EXE).exists(), (
        "a freshly staged folder must survive INTACT - the applier may be running from it")
    assert marker.exists(), "the deferred entry must stay tracked for a later sweep"

    _backdate(staged)                           # much later: the handoff is long over
    assert ua.sweep_staged_leftovers() == 1
    assert not staged.exists()


def test_a_partially_swept_folder_is_retried_until_gone(tmp_path, monkeypatch, marker):
    """Review C3: a folder our own sweep half-deleted (a transient AV/indexer lock) must be
    retried on later launches - never untracked as not-ours because WE removed its exe."""
    downloads = tmp_path / "Downloads"
    staged = _make_install(downloads, "NetSpeedTray-2.1.6")
    blocker = staged / "_internal" / "locked.dat"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_bytes(b"x")
    install = _make_install(tmp_path, "NetSpeedTray")
    monkeypatch.setattr(ua.sys, "executable", str(install / ua.APP_EXE))
    ua.record_staged_path(str(staged))
    _backdate(staged)

    with open(blocker, "rb"):                   # Windows: an open handle blocks deletion
        assert ua.sweep_staged_leftovers() == 0
        assert staged.is_dir(), "the locked file keeps the folder alive"
        assert marker.exists(), "the half-deleted folder must stay tracked"

    assert ua.sweep_staged_leftovers() == 1, "retried and removed once the lock is gone"
    assert not staged.exists()
    assert not marker.exists()


# ------------------------------------------------- a swap that does not happen restarts the app (RC)
#
# The previous instance QUITS to hand the applier the swap. A refusal (exit 3) or a swap that failed
# and put the original back (exit 4) used to exit without starting anything: the app just vanished,
# and an older build (2.1.7, whose weaker pre-check hands off what this applier refuses) re-offered
# the update daily - vanishing daily.

@pytest.fixture
def restart_spy(monkeypatch, tmp_path):
    launched, told = [], []
    monkeypatch.setattr(ua, "relaunch", lambda exe: launched.append(exe) or True)
    monkeypatch.setattr(ua, "_notify_not_applied", raising=False, value=
                        lambda reason, staged, install: told.append((reason, staged, install)))
    monkeypatch.setattr(ua, "_marker_path", lambda: str(tmp_path / "appdata" / "m.json"))
    return launched, told


def test_a_refused_swap_restarts_the_existing_app_and_says_why(tmp_path, restart_spy):
    launched, told = restart_spy
    install = _make_install(tmp_path)
    (install / "my-notes.txt").write_text("mine", encoding="utf-8")
    staged = _make_staged(tmp_path)

    assert ua.apply_update(str(install), None, str(staged)) == 3
    assert launched == [os.path.join(str(install), ua.APP_EXE)], "the app must not just vanish"
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "old"
    (reason, told_staged, told_install), = told
    assert "my-notes.txt" in reason and told_staged == str(staged) and told_install == str(install)


def test_a_failed_but_restored_swap_restarts_the_existing_app(tmp_path, monkeypatch, restart_spy):
    launched, told = restart_spy
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)

    def boom(*a, **k):
        raise OSError("simulated: disk full")
    monkeypatch.setattr(ua.shutil, "copytree", boom)

    assert ua.apply_update(str(install), None, str(staged)) == 4
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "old", "restored"
    assert launched == [os.path.join(str(install), ua.APP_EXE)]
    assert len(told) == 1 and "disk full" in told[0][0]


def test_a_failed_restore_launches_nothing(tmp_path, monkeypatch, restart_spy):
    """No complete app in the install folder: nothing there may be started."""
    launched, told = restart_spy
    install = _make_install(tmp_path)
    staged = _make_staged(tmp_path)
    monkeypatch.setattr(ua, "swap", lambda i, s: (_ for _ in ()).throw(
        ua._RestoreFailed(str(tmp_path / "bk"), "simulated")))
    assert ua.apply_update(str(install), None, str(staged)) == 4
    assert launched == []


def test_a_refusal_for_a_folder_that_is_not_ours_launches_nothing(tmp_path, restart_spy):
    launched, told = restart_spy
    victim = tmp_path / "Documents"
    victim.mkdir()
    assert ua.apply_update(str(victim), None, str(_make_staged(tmp_path))) == 3
    assert launched == [], "never start an EXE from a folder validate() did not recognise"


def test_the_notice_is_never_shown_outside_the_frozen_app(monkeypatch):
    """Tests and dev runs must never block on a message box."""
    import netspeedtray.core.update_notice as un
    monkeypatch.delattr(ua.sys, "frozen", raising=False)
    monkeypatch.setattr(un, "show_update_not_applied",
                        lambda *a: pytest.fail("shown outside the frozen app"))
    ua._notify_not_applied("r", "s", "i")


def test_the_notice_text_is_localized_and_names_the_folders():
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.core.update_notice import build_update_not_applied
    de = I18nStrings("de_DE")
    title, text = build_update_not_applied(de, "because", r"C:\staged", r"D:\app", "9.9.9")
    assert title == de.UPDATE_PORTABLE_READY_TITLE
    assert "because" in text and r"C:\staged" in text and r"D:\app" in text and "9.9.9" in text
    assert de.UPDATE_PORTABLE_READY_MESSAGE.split("{")[0].strip() in text


def test_a_broken_translation_still_shows_the_message():
    from types import SimpleNamespace
    from netspeedtray.core.update_notice import build_update_not_applied
    bad = SimpleNamespace(UPDATE_NOT_APPLIED_MESSAGE="{oops}", UPDATE_PORTABLE_READY_MESSAGE="{x}",
                          UPDATE_PORTABLE_READY_TITLE="T")
    title, text = build_update_not_applied(bad, "why", "S", "I", "1")
    assert title == "T" and "why" in text and "S" in text and "I" in text


# ------------------------------------------------- Explorer's folder metadata is carried, not refused

@pytest.mark.parametrize("name", ["desktop.ini", "Thumbs.db", "DESKTOP.INI"])
def test_explorer_metadata_does_not_block_the_swap(tmp_path, name):
    install = _make_install(tmp_path)
    (install / name).write_text("[.ShellClassInfo]", encoding="utf-8")
    assert ua.validate(str(install), str(_make_staged(tmp_path))) is None


def test_a_users_own_file_beside_explorer_metadata_is_still_refused(tmp_path):
    install = _make_install(tmp_path)
    (install / "desktop.ini").write_text("x", encoding="utf-8")
    (install / "notes.txt").write_text("mine", encoding="utf-8")
    reason = ua.validate(str(install), str(_make_staged(tmp_path)))
    assert reason and "notes.txt" in reason and "desktop.ini" not in reason


def test_the_swap_carries_explorer_metadata_and_the_backup_stays_removable(
        tmp_path, monkeypatch, restart_spy):
    launched, told = restart_spy
    install = _make_install(tmp_path)
    ini = install / "desktop.ini"
    ini.write_text("[.ShellClassInfo]\nIconResource=x.ico,0\n", encoding="utf-8")
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(ini), 0x2 | 0x4)       # hidden | system
        ctypes.windll.kernel32.SetFileAttributesW(str(install), 0x10 | 0x1)   # dir | read-only
    staged = _make_staged(tmp_path)

    assert ua.apply_update(str(install), None, str(staged)) == 0
    assert told == []
    assert (install / ua.APP_EXE).read_text(encoding="utf-8") == "new"
    assert ini.read_text(encoding="utf-8").startswith("[.ShellClassInfo]"), "customisation lost"
    if os.name == "nt":
        st = os.stat(install)
        assert st.st_file_attributes & 0x1, "the folder's read-only flag (Explorer reads desktop.ini " \
                                            "only then) was not carried"
        assert os.stat(ini).st_file_attributes & 0x2, "desktop.ini lost its hidden attribute"
    (backup,) = tmp_path.glob("*" + ua._BACKUP_SUFFIX + "*")
    assert ua._portable_tree_refusal(str(backup), allowed_extra=os.listdir(install)) is None, (
        "the backup must still be removable by the janitor")
    if os.name == "nt":                              # let tmp_path clean up
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(install), 0x10)


def test_a_refused_staged_copy_leaves_the_marker_older_sweeps_read(tmp_path, restart_spy):
    """The app restarted after a refusal is often an OLDER build, whose sweep deletes every folder
    in staged_update.json ten minutes after staging - the folder the notice sends the user to."""
    launched, told = restart_spy
    install = _make_install(tmp_path)
    (install / "notes.txt").write_text("mine", encoding="utf-8")
    staged = _make_staged(tmp_path, "NetSpeedTray-9.9.9")
    other = str(tmp_path / "NetSpeedTray-other")
    ua.record_staged_path(str(staged))
    marker = Path(ua._marker_path())
    entries = json.loads(marker.read_text(encoding="utf-8"))
    entries.append(other)                                       # an older build's bare entry
    marker.write_text(json.dumps(entries), encoding="utf-8")

    assert ua.apply_update(str(install), None, str(staged)) == 3
    left = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else []
    paths = [e if isinstance(e, str) else e.get("path") for e in left]
    assert str(staged) not in paths, "an older build's sweep would delete the folder we point at"
    assert other in paths, "other entries are kept verbatim"
    failures = json.loads((marker.parent / ua._BACKUPS_RECORD_NAME).read_text(encoding="utf-8"))
    assert any(r.get("kind") == "failure" and r.get("staged") == str(staged) for r in failures)
    assert staged.is_dir() and told and told[0][1] == str(staged)
