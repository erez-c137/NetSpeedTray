"""
Applies a staged portable update - run from *inside* the staged copy.

A program cannot replace the folder it is running from, which is why the portable build has always
ended in "here is the new version, copy it over yourself" (#195). This closes that gap without a
helper script.

The trick is that we already download and SHA-256-verify a **complete, working copy of the app**, so
the new copy applies the update itself:

    existing app (v N)                    staged copy (v N+1, verified)
    ------------------                    -----------------------------
    1. download + verify + extract
    2. launch staged NetSpeedTray.exe
       --apply-update <install> --wait-pid <own pid>
    3. quit  ---------------------------> 4. wait for <pid> to exit
                                          5. rename <install> -> <install>.old-<ts>
                                          6. copy   <staged>  -> <install>
                                          7. launch <install>/NetSpeedTray.exe
                                          8. delete the .old- folder, exit
                                             (the staged folder is swept on the next launch -
                                              this process is running from it)

**Why not a helper script.** A .bat is fragile with non-ASCII paths - a real user population here,
since the bundle on #260 carries an Arabic interface name - PowerShell needs -ExecutionPolicy Bypass,
and "a script that terminates a process then overwrites binaries" is a textbook malware heuristic.
This project already had to clear a Webroot false positive, and the rest of the update path is built
on WinVerifyTrust plus a SignPath certificate pin, fail-closed. Doing the replacement from our own
signed executable keeps that posture intact.

**Why rename-aside-then-copy, and never delete-first.** At no point may the user be left with
nothing. The old install is renamed (reversible) before anything is written; deleting it first would
mean a failure halfway leaves them with no app at all.

It is a *copy* rather than a move because this process runs from the staged folder, and Windows will
not let a directory containing a running executable be moved or deleted. An earlier version used
`shutil.move` and failed with `WinError 5` on our own EXE - and since `move` degrades to
copy-then-delete, it had already created a partial destination, which then broke the restore too.
A live test against real binaries caught that; the unit tests, which used dummy text files, did not.

Anything unexpected is a **refusal, not a repair**: return non-zero and leave both folders intact.
The staged copy is still on disk, so the guided "copy it yourself" path remains available.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional

logger = logging.getLogger("NetSpeedTray.UpdateApplier")

APP_EXE = "NetSpeedTray.exe"

# The old process is asked to quit before we are launched; this is the ceiling on how long we wait
# for it to actually go. Beyond that something is wrong, and we refuse rather than force anything.
_EXIT_TIMEOUT_SEC = 30.0
# Windows - and AV, and Explorer - can hold a directory handle for a moment after a process exits,
# so the rename is retried rather than treated as fatal on the first failure.
_SWAP_RETRY_SEC = 8.0
_SWAP_RETRY_STEP = 0.25

_BACKUP_SUFFIX = ".old-"


def _wait_for_exit(pid: int, timeout: float = _EXIT_TIMEOUT_SEC) -> bool:
    """Block until `pid` exits. True if it is gone, False on timeout.

    Uses a process *handle* rather than polling by PID: a handle refers to one specific process, so a
    recycled PID cannot make us believe the old app is still running - or, worse, mistake some
    unrelated new process for it.
    """
    try:
        import win32api
        import win32con
        import win32event
    except Exception:                                    # pragma: no cover - non-Windows dev box
        logger.warning("pywin32 unavailable; falling back to a PID poll.")
        return _wait_for_exit_poll(pid, timeout)

    try:
        handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
    except Exception:
        logger.info("Process %s already gone.", pid)
        return True                                       # cannot open it => it already exited
    try:
        result = win32event.WaitForSingleObject(handle, int(timeout * 1000))
        gone = result == win32event.WAIT_OBJECT_0
        logger.info("Waited for process %s to exit: %s", pid, "exited" if gone else "TIMED OUT")
        return gone
    finally:
        try:
            win32api.CloseHandle(handle)
        except Exception:
            pass


def _wait_for_exit_poll(pid: int, timeout: float) -> bool:
    """Handle-free fallback, used only when pywin32 is missing."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _is_dir_writable(path: str) -> bool:
    """True if we can create and remove a file in `path`.

    Probed directly rather than inferred from os.access, which lies on Windows.
    """
    probe = os.path.join(path, ".nst-write-probe")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except Exception:
        return False


def _is_within(child: str, parent: str) -> bool:
    """True if `child` is `parent`, or lives underneath it."""
    try:
        c = os.path.normcase(os.path.abspath(child))
        p = os.path.normcase(os.path.abspath(parent))
        return c == p or c.startswith(p + os.sep)
    except Exception:
        return False


def validate(install_dir: str, staged_dir: str) -> Optional[str]:
    """Return a refusal reason, or None when the swap is safe to attempt.

    Every one of these is a refusal rather than something to work around: the staged folder stays on
    disk either way, so the user can always fall back to copying it by hand.
    """
    if not install_dir or not staged_dir:
        return "missing install or staged directory"
    if not os.path.isdir(install_dir):
        return "install directory does not exist: %s" % install_dir
    if not os.path.isdir(staged_dir):
        return "staged directory does not exist: %s" % staged_dir
    if not os.path.isfile(os.path.join(install_dir, APP_EXE)):
        # Refuse to rename a directory that is not obviously ours. This flag must never become a way
        # to move an arbitrary folder.
        return "install directory has no %s; refusing to touch it" % APP_EXE
    if not os.path.isfile(os.path.join(staged_dir, APP_EXE)):
        return "staged directory has no %s" % APP_EXE
    if os.path.normcase(os.path.abspath(staged_dir)) == os.path.normcase(os.path.abspath(install_dir)):
        return "staged and install directories are the same"
    if _is_within(staged_dir, install_dir):
        # Renaming the install would drag the staged copy - and this running process - along with it.
        return "staged copy lives inside the install directory"
    parent = os.path.dirname(os.path.abspath(install_dir)) or install_dir
    if not _is_dir_writable(parent):
        # The install directory is renamed, which is a write against its PARENT, not itself.
        return "cannot write beside the install directory: %s" % parent
    return None


def _rename_with_retry(src: str, dst: str, timeout: float = _SWAP_RETRY_SEC) -> None:
    """os.rename with a short retry, for handles that outlive the process that held them."""
    deadline = time.monotonic() + timeout
    last: Optional[BaseException] = None
    while True:
        try:
            os.rename(src, dst)
            return
        except OSError as e:
            last = e
            if time.monotonic() >= deadline:
                break
            time.sleep(_SWAP_RETRY_STEP)
    raise RuntimeError("could not rename %s -> %s within %.0fs: %s" % (src, dst, timeout, last))


def _backup_path(install_dir: str) -> str:
    return "%s%s%d" % (install_dir.rstrip(os.sep), _BACKUP_SUFFIX, int(time.time()))


def swap(install_dir: str, staged_dir: str) -> str:
    """Rename the install aside, then COPY the staged tree into its place. Returns the backup path.

    **Copy, not move, and the distinction is the whole point.** This process is running *from*
    `staged_dir`, and Windows will not let a directory containing a running executable be moved or
    deleted. An earlier version used `shutil.move` here; against real binaries it failed with
    `WinError 5` on our own EXE, and because `move` falls back to copy-then-delete it had already
    created a *partial* destination - which then made the restore fail too, leaving the user with a
    half-copied app. A live test caught that; the unit tests, which used dummy text files, did not.

    Reading our own EXE while it runs is fine, so copying works. The staged folder is left behind on
    purpose - this process cannot delete the ground it stands on - and is swept on the next launch.

    If the copy fails the partial destination is cleared first, so the rename back can actually
    succeed and the user is left exactly as they started.
    """
    backup = _backup_path(install_dir)
    _rename_with_retry(install_dir, backup)
    logger.info("Moved the existing install aside.")
    try:
        shutil.copytree(staged_dir, install_dir)
    except Exception:
        logger.error("Copying the staged copy failed; restoring the original install.", exc_info=True)
        # Clear the partial destination FIRST or the rename back has nowhere to land.
        shutil.rmtree(install_dir, ignore_errors=True)
        try:
            os.rename(backup, install_dir)
            logger.info("Original install restored.")
        except Exception:
            # Say exactly where their app is rather than pretend this is recoverable from here.
            logger.critical("Could not restore the original install. It is at: %s", backup)
        raise
    logger.info("Staged copy is now the install.")
    return backup


def cleanup_backup(backup: str) -> None:
    """Best effort. A leftover folder is untidy; a failed update is not."""
    try:
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        logger.info("Could not remove %s; it will be swept on a later launch.", backup)


def sweep_old_backups(install_dir: str, keep_newer_than_sec: float = 60.0) -> int:
    """Remove `<install>.old-*` folders left behind when a cleanup could not finish.

    The age guard keeps this from deleting a backup belonging to an update still in flight.
    """
    removed = 0
    try:
        parent = os.path.dirname(os.path.abspath(install_dir))
        base = os.path.basename(os.path.abspath(install_dir)) + _BACKUP_SUFFIX
        now = time.time()
        for name in os.listdir(parent):
            if not name.startswith(base):
                continue
            path = os.path.join(parent, name)
            if not os.path.isdir(path):
                continue
            try:
                if now - os.path.getmtime(path) < keep_newer_than_sec:
                    continue
            except OSError:
                continue
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.isdir(path):
                removed += 1
    except Exception:
        pass
    return removed


def relaunch(exe: str) -> bool:
    """Start the updated app detached, so this process can exit immediately."""
    try:
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True)
        logger.info("Relaunched the updated app.")
        return True
    except Exception:
        # The update itself already succeeded - never undo a good update because the restart failed.
        logger.error("Update applied, but the relaunch failed. The user can start it manually.",
                     exc_info=True)
        return False


def apply_update(install_dir: str, wait_pid: Optional[int], staged_dir: Optional[str] = None) -> int:
    """The whole flow. Returns a process exit code (0 = applied)."""
    staged_dir = staged_dir or os.path.dirname(os.path.abspath(sys.executable))
    logger.info("Apply-update requested: install=%s staged=%s pid=%s",
                install_dir, staged_dir, wait_pid)

    if wait_pid and not _wait_for_exit(wait_pid):
        logger.warning("The previous instance did not exit; refusing to swap.")
        return 2

    reason = validate(install_dir, staged_dir)
    if reason:
        logger.warning("Refusing to apply the update: %s", reason)
        return 3

    try:
        backup = swap(install_dir, staged_dir)
    except Exception as e:
        logger.error("Update swap failed: %s", e, exc_info=True)
        return 4

    relaunch(os.path.join(install_dir, APP_EXE))
    cleanup_backup(backup)
    # `staged_dir` is deliberately NOT removed: we are running from it. The relaunched copy sweeps
    # it via sweep_staged_leftovers(), and it is harmless until then.
    logger.info("Update applied successfully. Staged copy at %s will be swept on next launch.",
                staged_dir)
    return 0


# --- the staged-update marker ------------------------------------------------------------------
# The hands-off flow records the EXACT folder it staged, and the sweep on the next launch removes
# only what was recorded. Anything that merely looks like a staged copy - a tester's own
# "NetSpeedTray-2.1.5-backup" rollback folder beside the install, say - is never touched: keeping
# a copy of the previous version next to the install is the only sane rollback plan for a portable
# user, and an earlier sweep was deleting exactly that. No marker, no sweep. The marker lives in
# the app data dir because the install folder itself is renamed aside and replaced during the
# swap - a sentinel inside it would ride away with the `.old-` backup and be lost.
_STAGED_MARKER_NAME = "staged_update.json"

# A recorded folder younger than this is deferred, never deleted: the marker is written the
# moment the handoff COMMITS, and the applier then runs FROM that folder for up to ~30 s of
# pid-wait plus the whole folder swap. A duplicate launch in that window (the user restarting
# the app they just watched vanish) must not gut it (review C1). Generous on purpose - a
# deferred folder is only untidy for one more launch; a gutted one becomes a broken install
# with its backup deleted.
_STAGED_SWEEP_GRACE_SEC = 600.0


def _marker_path() -> str:
    """Where the staged-update marker lives. Mirrors helpers.get_app_data_path() without importing
    it - this module stays stdlib-only so the applier can run headless from the staged copy."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "NetSpeedTray", _STAGED_MARKER_NAME)


def _read_staged_paths() -> List[dict]:
    """The recorded staged entries as ``{"path": str, "validated": bool}``, or []. Bare-string
    entries from older markers are accepted as unvalidated. A missing or unreadable marker reads
    as empty - the sweep then removes nothing (fail-safe) and cleans the file up.

    ``validated`` means a prior sweep already passed this folder through the looks-like-ours
    guard and partially deleted it (a transient lock kept the folder alive). Such an entry must
    be retried even though our own sweep removed its exe - otherwise the remnant leaks in
    Downloads forever (review C3)."""
    entries: List[dict] = []
    try:
        with open(_marker_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str) and item:
                    entries.append({"path": item, "validated": False})
                elif (isinstance(item, dict) and isinstance(item.get("path"), str)
                        and item["path"]):
                    entries.append({"path": item["path"],
                                    "validated": bool(item.get("validated"))})
    except Exception:
        pass
    return entries


def record_staged_path(path: str) -> None:
    """Record a staged-update folder so ``sweep_staged_leftovers`` may remove it on a later launch.

    Called by the hands-off flow at the moment it commits to the handoff - never earlier, because
    an uncommitted staging falls back to the guided copy, which points the user at that very
    folder. Best-effort: a failure here means the folder survives the sweep (untidy), never that
    something is deleted that should not be.
    """
    try:
        target = os.path.abspath(path)
        marker = _marker_path()
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        entries = _read_staged_paths()
        if target not in (e["path"] for e in entries):
            entries.append({"path": target, "validated": False})
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        logger.info("Recorded staged folder for the post-update sweep: %s", target)
    except Exception:
        logger.warning("Could not record the staged folder %s; it will survive the sweep.",
                       path, exc_info=True)


def sweep_staged_leftovers() -> int:
    """Remove staged-update folders a hands-off update recorded but could not delete from inside
    itself. Returns the number removed.

    Only paths recorded via ``record_staged_path`` are ever candidates - with no marker present
    the sweep removes NOTHING, so a folder that merely looks like a staged copy (a user's own
    rollback backup, for instance) is never collateral. Every removal is logged with its full
    path. Entries whose folder is already gone are dropped; an entry the sweep refuses (the
    running copy, or a folder that does not look like ours) is dropped without deleting or kept
    as appropriate; a folder still locked is retried on the next launch.
    """
    marker = _marker_path()
    if not os.path.isfile(marker):
        return 0
    recorded = _read_staged_paths()
    try:
        here = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        here = ""
    removed = 0
    remaining: List[dict] = []
    for entry in recorded:
        path = entry["path"]
        try:
            if os.path.normcase(os.path.abspath(path)) == here:
                # Never delete the ground we stand on. Keep the entry rather than drop it: this
                # is the staged copy itself running (the --apply-update handoff); the relaunched
                # install is the one that sweeps it, on the next normal startup.
                logger.info("Recorded staged folder is the running copy; deferring: %s", path)
                remaining.append(entry)
                continue
            if not os.path.isdir(path):
                continue                        # already gone - drop the stale entry quietly
            if not os.path.basename(path).startswith("NetSpeedTray-"):
                # A corrupted marker must never become a way to delete an arbitrary folder.
                logger.warning("Recorded staged folder does not look like ours; "
                               "leaving it alone: %s", path)
                continue                        # refuse AND stop tracking it
            if not entry["validated"] and not os.path.isfile(os.path.join(path, APP_EXE)):
                # Same corrupted-marker guard - but a `validated` entry skips it: OUR earlier
                # sweep already deleted the exe (partial rmtree around a transient lock), and
                # re-refusing it here would untrack the remnant forever (review C3).
                logger.warning("Recorded staged folder does not look like ours; "
                               "leaving it alone: %s", path)
                continue                        # refuse AND stop tracking it
            if not entry["validated"]:
                try:
                    age = time.time() - os.path.getmtime(path)
                except OSError:
                    age = 0.0
                if age < _STAGED_SWEEP_GRACE_SEC:
                    # A fresh mtime may mean the handoff is IN FLIGHT and the applier is running
                    # from this folder - a duplicate launch must not gut it (review C1).
                    # Deferred, never lost: the next launch re-sweeps it.
                    logger.info("Recorded staged folder is recent; deferring in case an update "
                                "is still in flight: %s", path)
                    remaining.append(entry)
                    continue
            shutil.rmtree(path, ignore_errors=True)
            if os.path.isdir(path):
                logger.info("Staged folder still in use; will retry next launch: %s", path)
                remaining.append({"path": path, "validated": True})
            else:
                removed += 1
                logger.info("Removed staged update folder: %s", path)
        except Exception:
            remaining.append(entry)
    try:
        if remaining:
            with open(marker, "w", encoding="utf-8") as fh:
                json.dump(remaining, fh)
        else:
            os.remove(marker)
    except Exception:
        pass
    return removed


def run_apply_update_cli(argv: Optional[List[str]] = None) -> Optional[int]:
    """Handle `--apply-update <dir> [--wait-pid N]`, or return None to let the GUI start.

    Mirrors `export_cli.run_export_cli`: it runs before any QApplication exists, so the applier is a
    short headless process rather than a second copy of the app coming up.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--apply-update" not in args:
        return None

    parser = argparse.ArgumentParser(prog="NetSpeedTray", add_help=False)
    parser.add_argument("--apply-update", dest="install_dir", default="")
    parser.add_argument("--wait-pid", dest="wait_pid", type=int, default=0)
    parser.add_argument("--staged-dir", dest="staged_dir", default="")
    try:
        ns, _unknown = parser.parse_known_args(args)
    except SystemExit:
        logger.error("Malformed --apply-update arguments: %r", args)
        return 1

    return apply_update(ns.install_dir, ns.wait_pid or None, ns.staged_dir or None)
