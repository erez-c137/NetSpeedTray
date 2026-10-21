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
                                          6. copy   <staged>  -> <install>, verify the copy
                                          7. record the outcome in the marker, launch
                                             <install>/NetSpeedTray.exe, exit
    new version (v N+1, in <install>)
    ---------------------------------
    9. once it has been up a few seconds - i.e. it STARTED - the update janitor waits for the
       applier to exit, then removes the .old- folder, the staged copy and its downloaded zip
       (with retries for AV/indexer locks; anything still locked is retried next launch).

Nothing is kept as a rollback once the new version has started. Until then the .old- folder IS the
rollback: a new version that crashes on start leaves the previous one intact beside it (#260).

Since 2.2 the staged copy lives in an app-owned folder (``staging_root()``, under %LOCALAPPDATA%),
not the user's Downloads: Downloads is only used for the guided copy-it-yourself fallback, where the
user has to find the folder. Every deletion goes through ``_portable_tree_refusal`` - a folder is
removed only if it looks exactly like a portable NetSpeedTray (its EXE + portable.marker, nothing
else at the top level, no junction or symlink anywhere inside). A user's own file in it, an
unexpected layout or a link is a refusal, logged, and the folder is left alone.

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
import stat
import subprocess
import sys
import threading
import time
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger("NetSpeedTray.UpdateApplier")

APP_EXE = "NetSpeedTray.exe"
# Shipped next to the EXE in the portable ZIP only (build.bat; constants.app.PORTABLE_MARKER_FILENAME
# - duplicated here because this module stays stdlib-only). Required before any folder is deleted.
PORTABLE_MARKER = "portable.marker"
# Everything a portable NetSpeedTray folder has at its top level (the released 2.1.7 ZIP: exactly
# these three). Anything else at the top level is someone else's, so the folder is not deleted.
_PORTABLE_TOP_LEVEL = frozenset({APP_EXE.lower(), "_internal", PORTABLE_MARKER})

# IO_REPARSE_TAG name-surrogate bit: set for junctions, symlinks and mount points - reparse points
# that NAME another location. Cloud-file placeholders (OneDrive) are reparse points without it.
_REPARSE_NAME_SURROGATE = 0x20000000

# A staged copy whose swap FAILED is kept this long, for a retry or a support bundle, then aged out.
_FAILED_KEEP_SEC = 7 * 24 * 3600.0
# The janitor waits at most this long for the applier (still running from the staged folder) to exit.
_APPLIER_EXIT_WAIT_SEC = 60.0
# Seconds between janitor passes while something is still locked (AV scanning a just-closed EXE,
# the indexer). ~77 s in all; whatever is still locked after that is retried on the next launch.
_JANITOR_RETRY_DELAYS = (2.0, 5.0, 10.0, 20.0, 40.0)

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
    purpose - this process cannot delete the ground it stands on - and the new version's update
    janitor removes it once it has started (``run_update_janitor``).

    If the copy fails the partial destination is cleared first, so the rename back can actually
    succeed and the user is left exactly as they started.
    """
    backup = _backup_path(install_dir)
    _rename_with_retry(install_dir, backup)
    logger.info("Moved the existing install aside.")
    try:
        shutil.copytree(staged_dir, install_dir)
        problem = _copy_mismatch(staged_dir, install_dir)
        if problem:
            raise RuntimeError("the copied install does not match the staged copy: %s" % problem)
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


def _copy_mismatch(staged_dir: str, install_dir: str) -> Optional[str]:
    """None when ``install_dir`` holds what ``staged_dir`` holds at the top level and the EXE is the
    same size; else what differs. A cheap check that the swap really produced the new version
    before anything is marked applied - and so before the old copy becomes deletable."""
    try:
        want = sorted(n.lower() for n in os.listdir(staged_dir))
        got = sorted(n.lower() for n in os.listdir(install_dir))
        if want != got:
            return "top-level entries differ"
        if (os.path.getsize(os.path.join(staged_dir, APP_EXE))
                != os.path.getsize(os.path.join(install_dir, APP_EXE))):
            return "%s size differs" % APP_EXE
    except OSError as e:
        return str(e)
    return None


# --- guarded deletion ---------------------------------------------------------------------------
# The only way this module deletes a folder. Two halves: a refusal check that the folder is a
# portable NetSpeedTray and nothing else, and a delete that never follows a link and removes the
# two identifying files LAST - so a delete interrupted by a lock leaves a folder that is still
# recognisably ours, and the next attempt still passes the check.

def _is_link_like(path: str) -> bool:
    """True for a symlink, junction or mount point - anything that names ANOTHER location.

    Never followed, never descended into. Unreadable counts as a link (fail-safe). Cloud-file
    placeholders are reparse points too, but not name surrogates, so a portable copy kept in a
    OneDrive folder can still be cleaned up.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    try:
        if os.path.isjunction(path):                 # Python 3.12+
            return True
    except AttributeError:                           # pragma: no cover - older Python
        pass
    if getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        tag = getattr(st, "st_reparse_tag", 0)
        return not tag or bool(tag & _REPARSE_NAME_SURROGATE)
    return False


def _top_level_names(path: Optional[str]) -> List[str]:
    try:
        return os.listdir(path) if path else []
    except OSError:
        return []


def _portable_tree_refusal(path: str, *, allowed_extra: Iterable[str] = (),
                           require_markers: bool = True) -> Optional[str]:
    """None if ``path`` is a portable NetSpeedTray folder we may delete; otherwise why not.

    - an absolute path to a real directory, not a junction/symlink;
    - at its top level, nothing but the portable layout (EXE, ``_internal``, ``portable.marker``)
      plus ``allowed_extra`` - the top-level names of the CURRENT install, so a future build that
      adds a file still matches its own previous copy. A user's own file there is a refusal;
    - the EXE and ``portable.marker`` present as regular files (``require_markers``; only a legacy
      entry our own earlier sweep half-deleted is exempt);
    - no junction or symlink anywhere inside.
    An empty directory is always deletable: removing it removes nothing else.
    """
    if not path or not os.path.isabs(path):
        return "not an absolute path"
    if _is_link_like(path):
        return "it is a junction or symlink"
    if not os.path.isdir(path):
        return "not a directory"
    try:
        names = os.listdir(path)
    except OSError as e:
        return "cannot list it: %s" % e
    if not names:
        return None
    allowed = set(_PORTABLE_TOP_LEVEL) | {n.lower() for n in allowed_extra}
    unexpected = sorted(n for n in names if n.lower() not in allowed)
    if unexpected:
        return "it holds items that are not part of NetSpeedTray: %s" % ", ".join(unexpected[:5])
    if require_markers:
        for required in (APP_EXE, PORTABLE_MARKER):
            p = os.path.join(path, required)
            if not os.path.isfile(p) or _is_link_like(p):
                return "it has no %s" % required
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for n in dirnames + filenames:
            if _is_link_like(os.path.join(dirpath, n)):
                return "it contains a junction or symlink: %s" % os.path.join(dirpath, n)
    return None


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)               # a read-only file; nothing else is retried
        os.unlink(path)


def _remove_portable_tree(path: str) -> bool:
    """Delete a folder that passed ``_portable_tree_refusal``. True when it is gone.

    Children first, the EXE and ``portable.marker`` last and only once everything else is gone, so a
    lock part-way leaves the identifying files in place for the retry. A link met on the way is
    never followed and stops the delete.
    """
    failures: List[str] = []
    identifying = {APP_EXE.lower(), PORTABLE_MARKER}

    def _clear(d: str, skip: frozenset = frozenset()) -> None:
        try:
            entries = list(os.scandir(d))
        except OSError:
            failures.append(d)
            return
        for entry in entries:
            if entry.name.lower() in skip:
                continue
            p = entry.path
            try:
                if _is_link_like(p):
                    failures.append(p)              # refuse: never follow, never unlink a link
                elif entry.is_dir(follow_symlinks=False):
                    _clear(p)
                    os.rmdir(p)
                else:
                    _unlink(p)
            except OSError:
                failures.append(p)

    _clear(path, frozenset(identifying))
    if failures:
        logger.info("Could not remove everything in %s yet (%d item(s) in use); will retry.",
                    path, len(failures))
        return False
    try:
        for name in (PORTABLE_MARKER, APP_EXE):     # the EXE last: it is what makes the folder ours
            p = os.path.join(path, name)
            if os.path.lexists(p):
                _unlink(p)
        os.rmdir(path)
    except OSError as e:
        logger.info("Could not finish removing %s yet: %s; will retry.", path, e)
        return False
    return not os.path.lexists(path)


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return ""


def _running_dir() -> str:
    try:
        return _norm(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        return ""


def sweep_old_backups(install_dir: str, keep_newer_than_sec: float = 60.0) -> int:
    """Remove ``<install>.old-<ts>`` folders that a swap left behind - the ones the marker does not
    track: those an older applier (2.1.x - 2.2 betas) failed to delete straight after its swap.

    The age guard keeps this from deleting a backup belonging to an update still in flight. Each
    one must pass ``_portable_tree_refusal`` - a user's own file inside it, an odd layout or a
    junction leaves it in place, logged.
    """
    removed = 0
    try:
        install_abs = os.path.abspath(install_dir)
        parent = os.path.dirname(install_abs)
        base = os.path.basename(install_abs) + _BACKUP_SUFFIX
        current_names = _top_level_names(install_abs)
        here = _running_dir()
        now = time.time()
        for name in os.listdir(parent):
            if not name.startswith(base) or not name[len(base):].isdigit():
                continue
            path = os.path.join(parent, name)
            if _norm(path) == here or _is_link_like(path) or not os.path.isdir(path):
                continue
            try:
                if now - os.path.getmtime(path) < keep_newer_than_sec:
                    continue
            except OSError:
                continue
            refusal = _portable_tree_refusal(path, allowed_extra=current_names)
            if refusal:
                logger.info("Leaving the old version at %s in place: %s", path, refusal)
                continue
            if _remove_portable_tree(path):
                removed += 1
                logger.info("Removed the previous version left beside the install: %s", path)
    except Exception:
        logger.debug("Old-backup sweep failed.", exc_info=True)
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
        _note_applier_outcome(staged_dir, applied=False)
        return 2

    reason = validate(install_dir, staged_dir)
    if reason:
        logger.warning("Refusing to apply the update: %s", reason)
        _note_applier_outcome(staged_dir, applied=False)
        return 3

    try:
        backup = swap(install_dir, staged_dir)
    except Exception as e:
        logger.error("Update swap failed: %s", e, exc_info=True)
        # The staged copy (and its zip) are KEPT for a retry or a support bundle, and aged out later.
        _note_applier_outcome(staged_dir, applied=False)
        return 4

    # Neither the backup nor `staged_dir` is removed here. The backup is the rollback until the
    # new version has actually started; `staged_dir` is the ground this process stands on. The
    # relaunched version's update janitor removes both (#260) - recorded first, so it knows to.
    _note_applier_outcome(staged_dir, applied=True, backup=backup, install_dir=install_dir)
    relaunch(os.path.join(install_dir, APP_EXE))
    logger.info("Update applied successfully. The new version removes the previous one (%s) and "
                "the staged copy (%s) once it has started.", backup, staged_dir)
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


def staging_root() -> str:
    """The app-owned folder a verified portable update is staged in (since 2.2; #260).

    %LOCALAPPDATA%, not %APPDATA%: ~100 MB of binaries must never roam with a profile, and keeping
    it out of the config folder means no cleanup here can ever be near config or history.
    """
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "NetSpeedTray", "updates")


# Marker read-modify-writes from this process (the janitor thread vs. a new update recording its
# staged folder on the UI thread) are serialized; the applier is a separate process that only
# writes while the app is not running.
_marker_lock = threading.RLock()


def _read_staged_paths() -> List[dict]:
    """The recorded entries, or []. Each is a dict with at least ``path`` and ``validated``.

    Bare-string entries from older markers are accepted as unvalidated staged entries. A missing or
    unreadable marker reads as empty - the sweep then removes nothing (fail-safe) and cleans the
    file up. Keys this version does not know are preserved.

    Entry kinds (``kind``, default ``"staged"``):
    - ``staged``: a verified copy the hands-off flow staged. ``applied`` is set by the applier after
      a verified swap (delete it now); ``failed_at`` after a refused/failed one (keep it
      ``_FAILED_KEEP_SEC``); ``package`` is its downloaded zip, deleted with it.
    - ``backup``: the previous install, renamed aside by the swap, with the ``install`` it came from.
    ``validated`` (legacy, 2.1.5-2.2 betas) means an earlier sweep passed this folder through its
    guard and half-deleted it, so its EXE may already be gone (review C3)."""
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
                    entry = dict(item)
                    entry["validated"] = bool(item.get("validated"))
                    entries.append(entry)
    except Exception:
        pass
    return entries


def _write_entries(entries: List[dict]) -> None:
    """Persist ``entries`` (atomically), or remove the marker when there are none."""
    marker = _marker_path()
    if not entries:
        try:
            os.remove(marker)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    tmp = marker + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entries, fh)
    os.replace(tmp, marker)


def _entry_key(entry: dict) -> Tuple[str, str]:
    return (entry.get("kind") or "staged", _norm(entry["path"]))


def record_staged_path(path: str, package: Optional[str] = None) -> None:
    """Record a staged-update folder so the post-update janitor may remove it.

    Called by the hands-off flow at the moment it commits to the handoff - never earlier, because
    an uncommitted staging falls back to the guided copy, which points the user at that very
    folder. ``package`` is the downloaded zip kept beside it, removed together with it.
    Best-effort: a failure here means the folder survives the sweep (untidy), never that
    something is deleted that should not be.
    """
    try:
        target = os.path.abspath(path)
        with _marker_lock:
            entries = _read_staged_paths()
            if ("staged", _norm(target)) not in {_entry_key(e) for e in entries}:
                entry = {"path": target, "validated": False}
                if package:
                    entry["package"] = os.path.abspath(package)
                entries.append(entry)
            _write_entries(entries)
        logger.info("Recorded staged folder for the post-update sweep: %s", target)
    except Exception:
        logger.warning("Could not record the staged folder %s; it will survive the sweep.",
                       path, exc_info=True)


def _note_applier_outcome(staged_dir: str, *, applied: bool, backup: Optional[str] = None,
                          install_dir: Optional[str] = None) -> None:
    """The applier's half of the record: mark the staged entry applied (or failed) and record the
    backup the swap made, so the relaunched version knows exactly what it may remove.

    A staged folder the old version did not record (pre-2.1.5 handoff) is only added when it lies
    inside our own ``staging_root()`` - a folder elsewhere that nobody recorded is never adopted.
    Best-effort: if this fails the folders stay on disk (untidy), never the reverse.
    """
    try:
        staged_abs = os.path.abspath(staged_dir)
        now = time.time()
        with _marker_lock:
            entries = _read_staged_paths()
            found = False
            for e in entries:
                if _entry_key(e) == ("staged", _norm(staged_abs)):
                    found = True
                    if applied:
                        e["applied"] = True
                        e["applier_pid"] = os.getpid()
                        e.pop("failed_at", None)
                    else:
                        e["failed_at"] = now
            if not found and _is_within(staged_abs, staging_root()):
                e = {"path": staged_abs, "validated": False}
                pkg = staged_abs + ".zip"
                if os.path.isfile(pkg):
                    e["package"] = pkg
                if applied:
                    e.update(applied=True, applier_pid=os.getpid())
                else:
                    e["failed_at"] = now
                entries.append(e)
            if applied and backup and install_dir:
                entries.append({"kind": "backup", "path": os.path.abspath(backup),
                                "install": os.path.abspath(install_dir),
                                "applier_pid": os.getpid(), "validated": False})
            _write_entries(entries)
    except Exception:
        logger.warning("Could not record the update outcome; leftovers may need a later sweep.",
                       exc_info=True)


def _remove_package(entry: dict) -> None:
    """Delete the staged copy's downloaded zip - only a regular ``.zip`` inside our staging root."""
    pkg = entry.get("package")
    if not isinstance(pkg, str) or not pkg:
        return
    try:
        if (pkg.lower().endswith(".zip") and _is_within(pkg, staging_root())
                and os.path.isfile(pkg) and not _is_link_like(pkg)):
            _unlink(pkg)
            logger.info("Removed the downloaded update package: %s", pkg)
    except OSError:
        logger.info("Could not remove %s yet; it is aged out later.", pkg)


# Outcomes of handling one marker entry in a sweep pass.
_REMOVED, _KEEP, _RETRY, _DROP = "removed", "keep", "retry", "drop"


def _sweep_staged_entry(entry: dict, here: str, now: float) -> str:
    path = entry["path"]
    if _norm(path) == here:
        # Never delete the ground we stand on. This is the staged copy itself running (the
        # --apply-update handoff); the relaunched install is the one that sweeps it.
        logger.info("Recorded staged folder is the running copy; deferring: %s", path)
        return _KEEP
    if not os.path.isdir(path):
        if entry.get("applied"):
            _remove_package(entry)
        return _DROP                            # already gone - drop the stale entry quietly
    if not os.path.basename(path).startswith("NetSpeedTray-"):
        # A corrupted marker must never become a way to delete an arbitrary folder.
        logger.warning("Recorded staged folder does not look like ours; leaving it alone: %s", path)
        return _DROP                            # refuse AND stop tracking it
    if entry.get("applied"):
        pass                                    # swapped in and verified: remove it now
    elif entry.get("failed_at") is not None:
        try:
            failed_at = float(entry["failed_at"])
        except (TypeError, ValueError):
            failed_at = 0.0
        if now - failed_at < _FAILED_KEEP_SEC:
            return _KEEP                        # kept for a retry or a support bundle
        logger.info("Ageing out the staged copy of an update that failed to apply: %s", path)
    elif not entry["validated"]:
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            age = 0.0
        if age < _STAGED_SWEEP_GRACE_SEC:
            # A fresh mtime may mean the handoff is IN FLIGHT and the applier is running from this
            # folder - a duplicate launch must not gut it (review C1). Deferred, never lost.
            logger.info("Recorded staged folder is recent; deferring in case an update is still "
                        "in flight: %s", path)
            return _KEEP
    refusal = _portable_tree_refusal(path, allowed_extra=_top_level_names(here),
                                     require_markers=not entry["validated"])
    if refusal:
        logger.warning("Recorded staged folder %s is left alone: %s", path, refusal)
        return _DROP
    if _remove_portable_tree(path):
        logger.info("Removed staged update folder: %s", path)
        _remove_package(entry)
        return _REMOVED
    return _RETRY


def _sweep_backup_entry(entry: dict, here: str) -> str:
    path, install = entry["path"], entry.get("install") or ""
    if not install or _norm(install) != here or _norm(path) == here:
        # Only the new version, running from the very install this backup was taken from, may
        # remove it. The installed copy (which shares this marker) or a rollback run from the
        # backup itself leaves it alone.
        return _KEEP
    if not os.path.isdir(path):
        return _DROP
    install_abs = os.path.abspath(install)
    name = os.path.basename(os.path.abspath(path))
    prefix = os.path.basename(install_abs) + _BACKUP_SUFFIX
    if (_norm(os.path.dirname(os.path.abspath(path))) != _norm(os.path.dirname(install_abs))
            or not name.startswith(prefix) or not name[len(prefix):].isdigit()):
        logger.warning("Recorded previous version does not look like ours; leaving it alone: %s",
                       path)
        return _DROP
    refusal = _portable_tree_refusal(path, allowed_extra=_top_level_names(install_abs))
    if refusal:
        logger.warning("The previous version at %s is left in place: %s", path, refusal)
        return _DROP
    if _remove_portable_tree(path):
        logger.info("Removed the previous version: %s", path)
        return _REMOVED
    return _RETRY


def _sweep_once() -> Tuple[int, int]:
    """One pass over the marker. Returns (removed, still locked)."""
    if not os.path.isfile(_marker_path()):
        return 0, 0
    with _marker_lock:
        recorded = _read_staged_paths()
    here = _running_dir()
    now = time.time()
    removed = retry = 0
    outcome = {}
    for entry in recorded:
        try:
            if (entry.get("kind") or "staged") == "backup":
                result = _sweep_backup_entry(entry, here)
            elif entry.get("kind") in (None, "staged"):
                result = _sweep_staged_entry(entry, here, now)
            else:
                result = _KEEP                  # a kind from a newer version: not ours to judge
        except Exception:
            logger.debug("Sweep of %s failed.", entry.get("path"), exc_info=True)
            result = _RETRY
        outcome[_entry_key(entry)] = result
        removed += result == _REMOVED
        retry += result == _RETRY
    # Re-read before writing: a new update may have recorded a folder meanwhile (UI thread).
    try:
        with _marker_lock:
            fresh = _read_staged_paths()
            _write_entries([e for e in fresh
                            if outcome.get(_entry_key(e), _KEEP) in (_KEEP, _RETRY)])
    except Exception:
        pass
    return removed, retry


def sweep_staged_leftovers() -> int:
    """One pass of the marker-driven cleanup. Returns the number of folders removed.

    Only paths recorded in the marker are ever candidates - with no marker present the sweep
    removes NOTHING, so a folder that merely looks like a staged copy (a user's own rollback
    backup, for instance) is never collateral. Every removal is logged with its full path.
    Entries whose folder is already gone are dropped; an entry the sweep refuses is dropped
    without deleting; one still in use or locked is kept and retried.
    """
    return _sweep_once()[0]


def age_out_staging_root(max_age_sec: float = _FAILED_KEEP_SEC) -> int:
    """Remove anything in ``staging_root()`` that no marker entry references and that is older than
    ``max_age_sec``: a staging an app crash never recorded, a kept package outliving its folder.
    Only ``NetSpeedTray-*`` names; folders must pass ``_portable_tree_refusal``."""
    root = staging_root()
    removed = 0
    try:
        if not os.path.isdir(root) or _is_link_like(root):
            return 0
        with _marker_lock:
            entries = _read_staged_paths()
        referenced = {_norm(e["path"]) for e in entries}
        referenced |= {_norm(e["package"]) for e in entries if isinstance(e.get("package"), str)}
        here = _running_dir()
        now = time.time()
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if (not name.startswith("NetSpeedTray-") or _norm(path) in referenced
                    or _norm(path) == here or _is_link_like(path)):
                continue
            try:
                if now - os.path.getmtime(path) < max_age_sec:
                    continue
            except OSError:
                continue
            if os.path.isfile(path) and name.lower().endswith(".zip"):
                try:
                    _unlink(path)
                    removed += 1
                    logger.info("Aged out an old update package: %s", path)
                except OSError:
                    pass
            elif os.path.isdir(path):
                refusal = _portable_tree_refusal(path, allowed_extra=_top_level_names(here))
                if refusal:
                    logger.info("Leaving %s: %s", path, refusal)
                elif _remove_portable_tree(path):
                    removed += 1
                    logger.info("Aged out an old staged update: %s", path)
        try:
            os.rmdir(root)                      # only succeeds when empty
        except OSError:
            pass
    except Exception:
        logger.debug("Staging-root age-out failed.", exc_info=True)
    return removed


def run_update_janitor(retry_delays: Iterable[float] = _JANITOR_RETRY_DELAYS,
                       sleep=time.sleep) -> int:
    """The post-update cleanup, run by the NEW version once it has started (#260).

    Waits for the applier recorded in the marker to exit (it runs from the staged folder), removes
    what the update left - the staged copy, its zip, the previous version - retrying while AV or the
    indexer still holds a just-closed file, then sweeps unrecorded ``.old-`` folders from older
    appliers and ages out the staging root. Anything still locked stays recorded for the next
    launch. Never raises; returns the number of items removed.
    """
    removed = 0
    try:
        with _marker_lock:
            entries = _read_staged_paths()
        pids = {e.get("applier_pid") for e in entries}
        for pid in pids:
            if isinstance(pid, int) and pid > 0 and pid != os.getpid():
                _wait_for_exit(pid, _APPLIER_EXIT_WAIT_SEC)
        done, locked = _sweep_once()
        removed += done
        for delay in retry_delays:
            if not locked:
                break
            sleep(delay)
            done, locked = _sweep_once()
            removed += done
        if locked:
            logger.info("%d update leftover(s) still in use; retrying on the next launch.", locked)
        here = _running_dir()
        if here:
            removed += sweep_old_backups(here)
        removed += age_out_staging_root()
    except Exception:
        logger.warning("Post-update cleanup failed.", exc_info=True)
    return removed


def start_update_janitor() -> Optional[threading.Thread]:
    """Run ``run_update_janitor`` on a daemon thread. Call it once the app has started - from
    inside the single-instance mutex (review C1) - never from the --apply-update process."""
    try:
        t = threading.Thread(target=run_update_janitor, name="NST-UpdateJanitor", daemon=True)
        t.start()
        return t
    except Exception:
        logger.warning("Could not start the post-update cleanup.", exc_info=True)
        return None


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
