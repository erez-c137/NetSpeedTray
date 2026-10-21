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
                                          5. rename <install> -> <install>.previous-<ts>
                                          6. copy   <staged>  -> <install>, verify the copy
                                          7. record the outcome in the marker, launch
                                             <install>/NetSpeedTray.exe, exit
    new version (v N+1, in <install>)
    ---------------------------------
    9. once it has been up a few seconds - i.e. it STARTED - the update janitor waits for the
       applier to exit, then removes the .previous- folder, the staged copy and its downloaded zip
       (with retries for AV/indexer locks; anything still locked is retried on later launches,
       up to ``_MAX_LAUNCH_ATTEMPTS``, then given up on - never deleted any other way).

Nothing is kept as a rollback once the new version has started. Until then the .previous- folder IS the
rollback: a new version that crashes on start leaves the previous one intact beside it (#260).

Since 2.2 the staged copy lives in an app-owned folder (``staging_root()``, under %LOCALAPPDATA%),
not the user's Downloads: Downloads is only used for the guided copy-it-yourself fallback, where the
user has to find the folder. What may be removed is RECORDED: the staged marker (shape shared with
older builds) and ``update_backups.json`` (backups and failed swaps; older builds never read it).
The only unrecorded folders ever removed are the ``<install>.old-<unix time>`` leftovers of older
appliers, under stricter checks (``sweep_old_backups``). Every deletion goes through
``_portable_tree_refusal`` - a folder is removed only if it looks exactly like a portable
NetSpeedTray (its EXE + portable.marker, nothing else at the top level, no junction, symlink or
hard link anywhere inside) - and re-checks it under a handle that pins it. A user's own file in it,
an unexpected layout or a link is a refusal, logged once, and the folder is left alone; a refused
backup stays recorded. An install folder holding anything of the user's is never swapped at all:
``validate`` sends that update down the guided copy instead.

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
import re
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
# A recorded leftover still locked after this many launches is given up on: untracked (with one
# log line), never deleted any other way (review-2 L3).
_MAX_LAUNCH_ATTEMPTS = 10
# Backup names carry int(time.time()). Anything outside [2023-01-01, now + 1 day] is not one of ours.
_PLAUSIBLE_BACKUP_EPOCH = 1672531200

# The old process is asked to quit before we are launched; this is the ceiling on how long we wait
# for it to actually go. Beyond that something is wrong, and we refuse rather than force anything.
_EXIT_TIMEOUT_SEC = 30.0
# Windows - and AV, and Explorer - can hold a directory handle for a moment after a process exits,
# so the rename is retried rather than treated as fatal on the first failure.
_SWAP_RETRY_SEC = 8.0
_SWAP_RETRY_STEP = 0.25

# The previous install is renamed to `<install>.previous-<ts>`. Builds up to 2.2.0-beta.3 named it
# `<install>.old-<ts>` and, at every start, rmtree'd ANY `<install>.old-*` sibling with no check of
# what is inside - so a backup holding a user's file would be wiped by an older build run from the
# same folder (a downgrade). They never match the new name. Their `.old-` leftovers are still swept
# here, under the same guards (#260 review).
_BACKUP_SUFFIX = ".previous-"
_LEGACY_BACKUP_SUFFIX = ".old-"


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
    try:
        known = set(_PORTABLE_TOP_LEVEL) | {n.lower() for n in os.listdir(staged_dir)}
        extra = sorted(n for n in os.listdir(install_dir) if n.lower() not in known)
    except OSError as e:
        return "cannot list the install directory: %s" % e
    if extra:
        # The swap renames the WHOLE folder aside; a user's own file in it would silently end up
        # in the backup. The guided copy (over the folder, in place) keeps it where it is.
        return "install directory holds items that are not part of NetSpeedTray: %s" % (
            ", ".join(extra[:5]))
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


class _RestoreFailed(RuntimeError):
    """The swap failed AND the original install could not be put back. ``backup`` is now the only
    complete copy of the user's app; it is recorded so that nothing ever removes it."""

    def __init__(self, backup: str, message: str) -> None:
        super().__init__(message)
        self.backup = backup


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
    succeed and the user is left exactly as they started. Only a destination THIS call created is
    ever cleared: the install path is re-created with ``os.mkdir`` (which fails if anything else
    put a folder there after the rename), and if that fails nothing is touched (review-2 L1). If
    the original cannot be put back, ``_RestoreFailed`` names the backup - the only good copy.
    """
    backup = _backup_path(install_dir)
    _rename_with_retry(install_dir, backup)
    logger.info("Moved the existing install aside.")
    try:
        os.mkdir(install_dir)               # ours from here on; fails if something else made it
    except OSError as e:
        logger.critical("Something re-created %s during the swap; leaving it alone. The original "
                        "install is at: %s", install_dir, backup)
        raise _RestoreFailed(backup, "install path re-created during the swap: %s" % e)
    try:
        shutil.copytree(staged_dir, install_dir, dirs_exist_ok=True)
        problem = _copy_mismatch(staged_dir, install_dir)
        if problem:
            raise RuntimeError("the copied install does not match the staged copy: %s" % problem)
    except Exception as e:
        logger.error("Copying the staged copy failed; restoring the original install.", exc_info=True)
        # Clear the partial destination FIRST or the rename back has nowhere to land. It is the
        # folder os.mkdir created above - nothing else's.
        shutil.rmtree(install_dir, ignore_errors=True)
        try:
            os.rename(backup, install_dir)
            logger.info("Original install restored.")
        except Exception:
            # Say exactly where their app is rather than pretend this is recoverable from here.
            logger.critical("Could not restore the original install. It is at: %s", backup)
            raise _RestoreFailed(backup, "copy failed (%s) and the restore failed" % e) from e
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

def _reparse_is_link(attrs: int, tag: int) -> bool:
    """A reparse point that NAMES another location (junction, symlink, mount point), or one whose
    tag we cannot read. Cloud-file placeholders are reparse points without the name-surrogate bit."""
    if not attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return False
    return not tag or bool(tag & _REPARSE_NAME_SURROGATE)


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
    return _reparse_is_link(getattr(st, "st_file_attributes", 0), getattr(st, "st_reparse_tag", 0))


def _entry_is_link(entry: "os.DirEntry") -> bool:
    """``_is_link_like`` from the directory listing itself - no second lookup by path that a swap
    could slip between."""
    try:
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            return True
        st = entry.stat(follow_symlinks=False)
        return _reparse_is_link(getattr(st, "st_file_attributes", 0),
                                getattr(st, "st_reparse_tag", 0))
    except OSError:
        return True


def _has_other_links(path: str) -> bool:
    """True if this file has more than one name (a hard link): the data, and its attributes, are
    shared with a name outside our tree, so we never chmod it and never treat it as ours."""
    try:
        return os.lstat(path).st_nlink > 1
    except OSError:
        return True


class _HeldDir:
    """A directory handle that pins ``path`` while we work inside it (#260 review M1).

    Opened with FILE_FLAG_OPEN_REPARSE_POINT, so a junction or symlink is opened AS ITSELF and seen
    for what it is rather than resolved; and without FILE_SHARE_DELETE, so nobody can rename or
    delete the directory - and so swap a junction into its place - until we close it. ``ok`` is
    False when the path is not a plain directory. Off Windows it degrades to a path check.
    """
    _GENERIC_FLAGS = 0x02000000 | 0x00200000       # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
    _ACCESS = 0x0001 | 0x0080                      # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
    _SHARE = 0x1 | 0x2                             # READ | WRITE - deliberately not DELETE

    def __init__(self, path: str) -> None:
        self.handle = None
        self.ok = False
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:                          # pragma: no cover - non-Windows
            self.ok = os.path.isdir(path) and not _is_link_like(path)
            return
        if os.name != "nt":                        # pragma: no cover
            self.ok = os.path.isdir(path) and not _is_link_like(path)
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32
        h = k32.CreateFileW(path, self._ACCESS, self._SHARE, None, 3, self._GENERIC_FLAGS, None)
        if not h or h == wintypes.HANDLE(-1).value:
            return
        self.handle = h

        class _TagInfo(ctypes.Structure):          # FILE_ATTRIBUTE_TAG_INFO
            _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]
        info = _TagInfo()
        if k32.GetFileInformationByHandleEx(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            is_dir = bool(info.FileAttributes & 0x10)
            self.ok = is_dir and not _reparse_is_link(info.FileAttributes, info.ReparseTag)
        if not self.ok:
            self.close()                           # a file or a link: nothing to hold

    def close(self) -> None:
        if self.handle:
            try:
                self._k32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


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
    - ``require_markers``: ``portable.marker`` present, and the EXE too unless the marker is all
      that is left (our own delete removes the EXE before the marker - review L1). Exempt only for
      an unrecorded ``<install>.old-<digits>`` sibling, which an older applier half-deleted;
    - no junction, symlink or hard-linked file anywhere inside.
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
        lower = {n.lower() for n in names}
        needed = [PORTABLE_MARKER] if lower == {PORTABLE_MARKER} else [APP_EXE, PORTABLE_MARKER]
        for required in needed:
            p = os.path.join(path, required)
            if not os.path.isfile(p) or _is_link_like(p):
                return "it has no %s" % required
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            if _is_link_like(p):
                return "it contains a junction or symlink: %s" % p
        for n in filenames:
            p = os.path.join(dirpath, n)
            if _has_other_links(p):
                return "it contains a hard-linked file: %s" % p
    return None


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        if _has_other_links(path):
            raise                                   # its attributes are shared: never chmod it
        os.chmod(path, stat.S_IWRITE)               # a read-only file; nothing else is retried
        os.unlink(path)


def _rmdir(path: str) -> None:
    try:
        os.rmdir(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)               # a read-only folder (review L2)
        os.rmdir(path)


def _remove_portable_tree(path: str, *, allowed_extra: Iterable[str] = (),
                          require_markers: bool = True) -> bool:
    """Delete a folder that passed ``_portable_tree_refusal``. True when it is gone.

    Every directory is pinned with ``_HeldDir`` before it is listed - the root included - so it is
    checked as a plain directory by handle and cannot be swapped for a junction while we work in it
    (review M1); a link met on the way stops the delete. Children first; then the EXE, and
    ``portable.marker`` LAST, only once everything else is gone - so a lock part-way leaves the
    folder recognisably ours for the retry.
    """
    failures: List[str] = []
    identifying = frozenset({APP_EXE.lower(), PORTABLE_MARKER})

    def _clear(d: str, held: "_HeldDir", skip: frozenset = frozenset()) -> None:
        if not held.ok:
            failures.append(d)
            return
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
                if _entry_is_link(entry):
                    failures.append(p)              # refuse: never follow, never unlink a link
                elif entry.is_dir(follow_symlinks=False):
                    sub = _HeldDir(p)
                    try:
                        _clear(p, sub)
                    finally:
                        sub.close()
                    if sub.ok:
                        _rmdir(p)
                elif _has_other_links(p):
                    failures.append(p)
                else:
                    _unlink(p)
            except OSError:
                failures.append(p)

    allowed_extra = list(allowed_extra)
    root = _HeldDir(path)
    try:
        if not root.ok:
            logger.info("Not removing %s: it is not a plain directory.", path)
            return False
        # Re-check now that the folder is pinned: nothing can be swapped in from here on, so this
        # is the verdict that counts (review-2 L4).
        refusal = _portable_tree_refusal(path, allowed_extra=allowed_extra,
                                         require_markers=require_markers)
        if refusal:
            logger.info("Not removing %s: %s", path, refusal)
            return False
        _clear(path, root, identifying)
        if failures:
            logger.info("Could not remove everything in %s yet (%d item(s) in use or refused); "
                        "will retry.", path, len(failures))
            return False
        try:
            for name in (APP_EXE, PORTABLE_MARKER):     # the marker last: it is what marks it ours
                p = os.path.join(path, name)
                if os.path.lexists(p):
                    _unlink(p)
        except OSError as e:
            logger.info("Could not finish removing %s yet: %s; will retry.", path, e)
            return False
    finally:
        root.close()
    try:
        _rmdir(path)
    except OSError as e:
        logger.info("Could not finish removing %s yet: %s; will retry.", path, e)
        return False
    return not os.path.lexists(path)


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return ""


def _same_path(a: str, b: str) -> bool:
    """Same directory? By file identity where both exist (8.3 names, case, a subst drive), by
    normalised path otherwise."""
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return _norm(a) == _norm(b)


def _running_dir() -> str:
    """The folder of the running executable, in its ORIGINAL case (review M3)."""
    try:
        return os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        return ""


def _install_is_complete(install_dir: str) -> bool:
    """The running install is a whole NetSpeedTray: a real executable (PE header) and
    ``portable.marker``, with the frozen stdlib in ``_internal``. If not, a swap went wrong and a
    sibling backup may be the only complete copy (reviews L4, review-2 M3)."""
    try:
        with open(os.path.join(install_dir, APP_EXE), "rb") as fh:
            if fh.read(2) != b"MZ":
                return False
    except OSError:
        return False
    return (os.path.isfile(os.path.join(install_dir, PORTABLE_MARKER))
            and os.path.isfile(os.path.join(install_dir, "_internal", "base_library.zip")))


def _ascii_ieq(a: str, b: str) -> bool:
    """Equal, ignoring case for ASCII letters ONLY: str.lower() folds e.g. the Kelvin sign onto
    'k', which the filesystem does not - a name must not match by Unicode case folding."""
    return len(a) == len(b) and all(
        x == y or (x.isascii() and y.isascii() and x.lower() == y.lower()) for x, y in zip(a, b))


def _plausible_backup_stamp(rest: str) -> bool:
    """Exactly ten ASCII digits (str.isdigit also accepts Arabic-Indic and superscript digits),
    forming a unix time between 2023 and tomorrow - what int(time.time()) produced."""
    if not re.fullmatch(r"[0-9]{10}", rest):
        return False
    return _PLAUSIBLE_BACKUP_EPOCH <= int(rest) <= time.time() + 86400


def _is_backup_name(path: str, install_dir: str,
                    suffixes: Tuple[str, ...] = (_BACKUP_SUFFIX, _LEGACY_BACKUP_SUFFIX)) -> bool:
    """``path`` is exactly ``<install><suffix><unix time>``, a sibling of ``install_dir``."""
    install_abs = os.path.abspath(install_dir)
    if not _same_path(os.path.dirname(os.path.abspath(path)), os.path.dirname(install_abs)):
        return False
    name = os.path.basename(os.path.abspath(path))
    for suffix in suffixes:
        prefix = os.path.basename(install_abs) + suffix
        if (len(name) > len(prefix) and _ascii_ieq(name[:len(prefix)], prefix)
                and _plausible_backup_stamp(name[len(prefix):])):
            return True
    return False


def _has_pyinstaller_fingerprint(path: str) -> bool:
    """A (possibly half-deleted) NetSpeedTray tree still shows what built it inside ``_internal``:
    the frozen stdlib (base_library.zip), the Python DLL, or PyQt6's Qt*.pyd - which is exactly
    what an AV-held 2.1.7 leftover was found to keep. A user's own ``_internal`` does not."""
    internal = os.path.join(path, "_internal")
    if not os.path.isdir(internal) or _is_link_like(internal):
        return False
    names = [n.lower() for n in _top_level_names(internal)]
    if "base_library.zip" in names:
        return True
    if any(n.startswith("python3") and n.endswith(".dll") for n in names):
        return True
    qt = os.path.join(internal, "PyQt6")
    return (os.path.isdir(qt) and not _is_link_like(qt)
            and any(n.lower().startswith("qt") and n.lower().endswith(".pyd")
                    for n in _top_level_names(qt)))


def sweep_old_backups(install_dir: str, keep_newer_than_sec: float = 60.0) -> int:
    """Remove ``<install>.old-<unix time>`` folders that an OLDER applier (2.1.x - 2.2 betas) left
    behind: it deleted them once, errors ignored, so what is left is often PARTIAL - no EXE, just a
    locked file or two - and is accepted as a SUBSET of the portable layout (review M3), provided it
    still carries the PyInstaller fingerprint in ``_internal`` (review-2 M1).

    Only the legacy ``.old-`` name, with exactly ten ASCII digits forming a plausible unix time.
    This version's own ``.previous-`` backups are never swept blind - each is recorded and removed
    through its record. Never while an update failed recently or a restore failed (the backup may
    be the only complete copy), never while the running install itself is incomplete (review L4),
    never a junction, never a folder with anything that is not ours in it.
    """
    removed = 0
    try:
        install_abs = os.path.abspath(install_dir)
        if not _install_is_complete(install_abs):
            logger.info("The running install looks incomplete; leaving any previous version alone.")
            return 0
        if _recent_failure(install_abs):
            logger.info("An update failed recently; leaving any previous version alone.")
            return 0
        parent = os.path.dirname(install_abs)
        current_names = _top_level_names(install_abs)
        now = time.time()
        protected = _restore_failed_backups()
        for name in os.listdir(parent):
            path = os.path.join(parent, name)
            if not _is_backup_name(path, install_abs, suffixes=(_LEGACY_BACKUP_SUFFIX,)):
                continue
            if _norm(path) in protected:
                continue
            if _same_path(path, _running_dir()) or _is_link_like(path) or not os.path.isdir(path):
                continue
            try:
                if now - os.path.getmtime(path) < keep_newer_than_sec:
                    continue
            except OSError:
                continue
            refusal = _portable_tree_refusal(path, allowed_extra=current_names,
                                             require_markers=False)
            if not refusal and _top_level_names(path) and not _has_pyinstaller_fingerprint(path):
                refusal = "nothing in it shows it is a NetSpeedTray build"
            if refusal:
                logger.info("Leaving the old version at %s in place: %s", path, refusal)
                continue
            if _remove_portable_tree(path, allowed_extra=current_names, require_markers=False):
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
        _note_applier_outcome(staged_dir, applied=False, install_dir=install_dir)
        return 2

    reason = validate(install_dir, staged_dir)
    if reason:
        logger.warning("Refusing to apply the update: %s", reason)
        _note_applier_outcome(staged_dir, applied=False, install_dir=install_dir)
        return 3

    try:
        backup = swap(install_dir, staged_dir)
    except _RestoreFailed as e:
        logger.error("Update swap failed and the original could not be restored: %s", e,
                     exc_info=True)
        # Recorded with no expiry: the backup is the only complete copy and is never removed.
        _note_applier_outcome(staged_dir, applied=False, install_dir=install_dir,
                              restore_failed_backup=e.backup)
        return 4
    except Exception as e:
        logger.error("Update swap failed: %s", e, exc_info=True)
        # The staged copy (and its zip) are KEPT for a retry or a support bundle, and aged out later.
        _note_applier_outcome(staged_dir, applied=False, install_dir=install_dir)
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


# Backups and failed swaps are recorded in a SEPARATE file (review M2). Builds up to 2.2.0-beta.3
# read the staged marker and delete any `NetSpeedTray-*` folder in it with an EXE after ten minutes,
# whatever else the entry says - so a backup recorded there could be deleted by an older build
# (the installed copy, or after a downgrade) with a user's file inside it. They never read this.
_BACKUPS_RECORD_NAME = "update_backups.json"


def _backups_path() -> str:
    """Next to the staged marker (so whatever relocates one relocates both)."""
    return os.path.join(os.path.dirname(_marker_path()), _BACKUPS_RECORD_NAME)


def staging_root() -> str:
    """The app-owned folder a verified portable update is staged in (since 2.2; #260).

    %LOCALAPPDATA%, not %APPDATA%: ~100 MB of binaries must never roam with a profile, and keeping
    it out of the config folder means no cleanup here can ever be near config or history.
    """
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "NetSpeedTray", "updates")


# FOLDERID_Downloads (as update_installer; duplicated to keep this module free of Qt imports).
_FOLDERID_DOWNLOADS = (0x374DE290, 0x123F, 0x4565, (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))


def _downloads_dirs() -> List[str]:
    """Where any build ever staged a portable update: Windows' Downloads folder, and the
    ``~/Downloads`` / home fallbacks update_installer._downloads_dir() used."""
    out: List[str] = []
    try:
        import ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                        ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]
        a, b, c, d = _FOLDERID_DOWNLOADS
        g = GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                                      ctypes.byref(p)) == 0:
            try:
                if p.value:
                    out.append(p.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(p)
    except Exception:
        pass
    home = os.path.expanduser("~")
    out += [os.path.join(home, "Downloads"), home]
    return out


def _allowed_staged_parents() -> List[str]:
    """A recorded staged folder is only ever deleted from directly inside one of these (review
    L3): a tampered marker naming some other portable install must not delete it."""
    return [staging_root()] + _downloads_dirs()


# Marker read-modify-writes from this process (the janitor thread vs. a new update recording its
# staged folder on the UI thread) are serialized; the applier is a separate process that only
# writes while the app is not running.
_marker_lock = threading.RLock()


def _read_list_checked(path: str) -> Tuple[List, bool]:
    """(items, readable). A missing file is ([], True); one that exists but cannot be read or
    parsed as a JSON list is ([], False) - the caller decides what that must mean."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return [], True
    except Exception:
        return [], False
    return (data, True) if isinstance(data, list) else ([], False)


def _read_list(path: str) -> List:
    """A JSON list of records, or [] when missing/unreadable."""
    return _read_list_checked(path)[0]


def _read_staged_paths() -> List[dict]:
    """The staged-marker entries, or []. Each is a dict with at least ``path`` and ``validated``.

    The SHAPE IS SHARED with older builds, which also read this file: ``{"path", "validated"}``,
    and the extra keys this version adds are ones they ignore harmlessly - ``applied`` /
    ``applier_pid`` (set by the applier after a verified swap: delete it now), ``failed_at`` (a
    refused/failed swap: keep it ``_FAILED_KEEP_SEC``), ``package`` (its downloaded zip, deleted
    with it). Bare-string entries from older markers are accepted as unvalidated. A missing or
    unreadable marker reads as empty - the sweep then removes nothing (fail-safe).
    ``validated`` (legacy) means an earlier sweep half-deleted this folder (review C3)."""
    entries: List[dict] = []
    for item in _read_list(_marker_path()):
        if isinstance(item, str) and item:
            entries.append({"path": item, "validated": False})
        elif isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]:
            if item.get("kind") not in (None, "staged"):
                continue                            # not a staged entry: never ours to act on
            entry = dict(item)
            entry["validated"] = bool(item.get("validated"))
            entries.append(entry)
    return entries


def _read_backup_records() -> List[dict]:
    """``{"kind": "backup", "path", "install", "applier_pid"}`` and
    ``{"kind": "failure", "install", "failed_at"[, "restore_failed", "backup"]}`` records."""
    out = []
    for item in _read_list(_backups_path()):
        if isinstance(item, dict) and item.get("kind") in ("backup", "failure") \
                and isinstance(item.get("install"), str):
            if item["kind"] == "backup" and not isinstance(item.get("path"), str):
                continue
            out.append(dict(item))
    return out


def _backups_record_unreadable() -> bool:
    """update_backups.json exists but cannot be read. Its failure records may be in there, so this
    is treated as a recent failure: no backup is removed until it is readable again (review-2 L2)."""
    return not _read_list_checked(_backups_path())[1]


def _restore_failed_backups() -> set:
    """Normalised paths of backups a failed restore left as the ONLY complete copy. Never removed,
    by anything, ever (review-2 M3)."""
    return {_norm(r["backup"]) for r in _read_backup_records()
            if r.get("kind") == "failure" and r.get("restore_failed")
            and isinstance(r.get("backup"), str)}


def _write_list(path: str, entries: List[dict]) -> None:
    """Persist ``entries`` atomically (a unique temp file beside it, then os.replace - two
    processes never share a temp name, review-2 L2), or remove the file when there are none."""
    if not entries:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    import tempfile
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _write_entries(entries: List[dict]) -> None:
    _write_list(_marker_path(), entries)


def _entry_key(entry: dict) -> Tuple[str, str]:
    return (entry.get("kind") or "staged", _norm(entry.get("path") or entry.get("install") or ""))


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
                          install_dir: Optional[str] = None,
                          restore_failed_backup: Optional[str] = None) -> None:
    """The applier's half of the record: mark the staged entry applied (or failed) in the staged
    marker, and record the backup the swap made - or the failure - in the backups record, so the
    relaunched version knows exactly what it may remove, and what it must not.

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
            _write_entries(entries)
            if install_dir:
                if _backups_record_unreadable():
                    # Keep the unreadable file for a support bundle, and stand in for whatever
                    # failure records it held with a fresh one (review-2 L2).
                    aside = _backups_path() + ".unreadable-%d" % int(now)
                    try:
                        os.replace(_backups_path(), aside)
                    except OSError:
                        pass
                    records = [{"kind": "failure", "install": os.path.abspath(install_dir),
                                "failed_at": now, "reason": "previous record unreadable"}]
                else:
                    records = _read_backup_records()
                if applied and backup:
                    records.append({"kind": "backup", "path": os.path.abspath(backup),
                                    "install": os.path.abspath(install_dir),
                                    "applier_pid": os.getpid()})
                elif not applied:
                    rec = {"kind": "failure", "install": os.path.abspath(install_dir),
                           "staged": staged_abs, "failed_at": now}
                    if restore_failed_backup:
                        # No expiry: this backup is the only complete copy of the user's app.
                        rec.update(restore_failed=True,
                                   backup=os.path.abspath(restore_failed_backup))
                    records.append(rec)
                _write_list(_backups_path(), records)
    except Exception:
        logger.warning("Could not record the update outcome; leftovers may need a later sweep.",
                       exc_info=True)


def _recent_failure(install_dir: str) -> bool:
    """An update failed within ``_FAILED_KEEP_SEC`` (or its restore failed, ever) for this install
    (backups record), or any ``failed_at`` in the staged marker, or the backups record is
    unreadable. While true, no legacy ``.old-`` folder is removed (review L4, review-2 L2/M3)."""
    if _backups_record_unreadable():
        return True
    now = time.time()

    def _recent(ts) -> bool:
        try:
            return now - float(ts) < _FAILED_KEEP_SEC
        except (TypeError, ValueError):
            return True                             # unreadable timestamp: assume recent

    for r in _read_backup_records():
        if r.get("kind") == "failure" and _same_path(r["install"], install_dir) \
                and (r.get("restore_failed") or _recent(r.get("failed_at"))):
            return True
    return any(e.get("failed_at") is not None and _recent(e.get("failed_at"))
               for e in _read_staged_paths())


def _remove_package(entry: dict) -> None:
    """Delete the staged copy's downloaded zip - only a regular ``.zip`` inside our staging root."""
    pkg = entry.get("package")
    if not isinstance(pkg, str) or not pkg:
        return
    try:
        if (pkg.lower().endswith(".zip") and _is_within(pkg, staging_root())
                and os.path.isfile(pkg) and not _is_link_like(pkg) and not _has_other_links(pkg)):
            _unlink(pkg)
            logger.info("Removed the downloaded update package: %s", pkg)
    except OSError:
        logger.info("Could not remove %s yet; it is aged out later.", pkg)


# Outcomes of handling one record in a sweep pass.
_REMOVED, _KEEP, _RETRY, _DROP = "removed", "keep", "retry", "drop"


def _failed_recently(entry: dict, now: float) -> Optional[bool]:
    """None if the entry is not a failed one; else whether it is still within the keep window."""
    if entry.get("failed_at") is None:
        return None
    try:
        return now - float(entry["failed_at"]) < _FAILED_KEEP_SEC
    except (TypeError, ValueError):
        return True


def _sweep_staged_entry(entry: dict, here: str, now: float) -> str:
    path = entry["path"]
    if _same_path(path, here):
        # Never delete the ground we stand on. This is the staged copy itself running (the
        # --apply-update handoff); the relaunched install is the one that sweeps it.
        logger.info("Recorded staged folder is the running copy; deferring: %s", path)
        return _KEEP
    failed = _failed_recently(entry, now)
    if failed:
        return _KEEP                                # kept for a retry, a bundle, and review L4
    if not os.path.isdir(path):
        if entry.get("applied"):
            _remove_package(entry)
        return _DROP                                # already gone - drop the stale entry quietly
    if not os.path.basename(path).startswith("NetSpeedTray-"):
        # A corrupted marker must never become a way to delete an arbitrary folder.
        logger.warning("Recorded staged folder does not look like ours; leaving it alone: %s", path)
        return _DROP                                # refuse AND stop tracking it
    if not any(_same_path(os.path.dirname(os.path.abspath(path)), p)
               for p in _allowed_staged_parents()):
        logger.warning("Recorded staged folder is not in a staging location; leaving it alone: %s",
                       path)
        return _DROP
    if failed is False:
        logger.info("Ageing out the staged copy of an update that failed to apply: %s", path)
    elif not entry.get("applied") and not entry["validated"]:
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
    extra = _top_level_names(here)
    refusal = _portable_tree_refusal(path, allowed_extra=extra,
                                     require_markers=not entry["validated"])
    if refusal:
        logger.warning("Recorded staged folder %s is left alone: %s", path, refusal)
        return _DROP
    if _remove_portable_tree(path, allowed_extra=extra, require_markers=not entry["validated"]):
        logger.info("Removed staged update folder: %s", path)
        _remove_package(entry)
        return _REMOVED
    return _RETRY


def _refused_once(rec: dict, reason: str) -> str:
    """A backup the janitor will not remove: logged ONCE, kept recorded (never dropped - review-2
    M2), re-checked silently on later launches in case the user tidied it up."""
    if rec.get("refused") != reason:
        logger.warning("The previous version at %s is left in place: %s", rec.get("path"), reason)
        rec["refused"] = reason
    return _KEEP


def _sweep_backup_record(rec: dict, here: str, now: float) -> str:
    if rec.get("kind") == "failure":
        if rec.get("restore_failed"):
            return _KEEP                            # no expiry (review-2 M3)
        return _KEEP if _failed_recently(rec, now) else _DROP
    path, install = rec["path"], rec["install"]
    if not _same_path(install, here) or _same_path(path, here):
        # Only the new version, running from the very install this backup was taken from, may
        # remove it. The installed copy (which shares this record) or a rollback run from the
        # backup itself leaves it alone.
        return _KEEP
    if not os.path.isdir(path) and not os.path.lexists(path):
        return _DROP
    if _norm(path) in _restore_failed_backups():
        return _refused_once(rec, "a failed restore left it as the only complete copy")
    if not _is_backup_name(path, install, suffixes=(_BACKUP_SUFFIX,)):
        return _refused_once(rec, "its name is not this install's own backup name")
    if not _install_is_complete(here):
        logger.info("The running install looks incomplete; keeping the previous version: %s", path)
        return _KEEP
    extra = _top_level_names(here)
    refusal = _portable_tree_refusal(path, allowed_extra=extra)
    if refusal:
        return _refused_once(rec, refusal)
    rec.pop("refused", None)
    if _remove_portable_tree(path, allowed_extra=extra):
        logger.info("Removed the previous version: %s", path)
        return _REMOVED
    return _RETRY


def _sweep_once(count_attempt: bool = False) -> Tuple[int, int]:
    """One pass over both records. Returns (removed, still locked).

    ``count_attempt`` (the janitor's first pass of a launch) counts one more launch on every entry
    still locked, and gives up on it - untracked with one log line, nothing deleted - after
    ``_MAX_LAUNCH_ATTEMPTS`` (review-2 L3). The records are written back with this pass's changes
    merged onto what is on disk now, so an entry recorded meanwhile is never lost.
    """
    if not os.path.isfile(_marker_path()) and not os.path.isfile(_backups_path()):
        return 0, 0
    with _marker_lock:
        staged = _read_staged_paths()
        backups_readable = not _backups_record_unreadable()
        backups = _read_backup_records() if backups_readable else []
    here = _running_dir()
    now = time.time()
    removed = retry = 0
    done: dict = {}                                 # key -> (outcome, possibly updated entry)
    for kind, items in (("staged", staged), ("record", backups)):
        for entry in items:
            try:
                result = (_sweep_staged_entry(entry, here, now) if kind == "staged"
                          else _sweep_backup_record(entry, here, now))
            except Exception:
                logger.debug("Sweep of %s failed.", entry.get("path"), exc_info=True)
                result = _RETRY
            if result == _RETRY and count_attempt:
                entry["attempts"] = int(entry.get("attempts") or 0) + 1
                if entry["attempts"] >= _MAX_LAUNCH_ATTEMPTS:
                    logger.warning("Giving up on removing %s after %d launches; it is left in "
                                   "place.", entry.get("path"), entry["attempts"])
                    result = _DROP
            done[(kind,) + _entry_key(entry)] = (result, entry)
            removed += result == _REMOVED
            retry += result == _RETRY
    try:
        with _marker_lock:
            if os.path.isfile(_marker_path()):
                _write_entries(_merge_staged(_read_list(_marker_path()), done))
            if backups_readable and not _backups_record_unreadable():
                merged = []
                for r in _read_backup_records():
                    key = ("record",) + _entry_key(r)
                    if key in done:
                        result, updated = done.pop(key)
                        if result in (_KEEP, _RETRY):
                            merged.append(updated)
                    else:
                        merged.append(r)            # recorded after this pass read the file
                _write_list(_backups_path(), merged)
    except Exception:
        logger.debug("Could not write the update records back.", exc_info=True)
    return removed, retry


def _merge_staged(raw: list, done: dict) -> list:
    """The staged marker as currently on disk, with this pass's outcome applied: removed/dropped
    entries out, kept ones in their updated form. Items this version does not understand (another
    build's) are kept verbatim."""
    out = []
    for item in raw:
        if isinstance(item, str) and item:
            key = ("staged", "staged", _norm(item))
        elif isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"] \
                and item.get("kind") in (None, "staged"):
            key = ("staged", "staged", _norm(item["path"]))
        else:
            out.append(item)
            continue
        if key not in done:
            out.append(item)
            continue
        result, updated = done[key]
        if result in (_KEEP, _RETRY):
            out.append(updated)
    return out


def sweep_staged_leftovers() -> int:
    """One pass of the record-driven cleanup. Returns the number of folders removed.

    Only paths recorded by an update are ever candidates - with no record present the sweep
    removes NOTHING, so a folder that merely looks like a staged copy (a user's own rollback
    backup, for instance) is never collateral. Every removal is logged with its full path.
    Entries whose folder is already gone are dropped; an entry the sweep refuses is dropped
    without deleting; one still in use or locked is kept and retried.
    """
    return _sweep_once()[0]


def age_out_staging_root(max_age_sec: float = _FAILED_KEEP_SEC) -> int:
    """Remove anything in ``staging_root()`` that no record references and that is older than
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
                    or _same_path(path, here) or _is_link_like(path)):
                continue
            try:
                if now - os.path.getmtime(path) < max_age_sec:
                    continue
            except OSError:
                continue
            if os.path.isfile(path) and name.lower().endswith(".zip"):
                if _has_other_links(path):
                    continue
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
                elif _remove_portable_tree(path, allowed_extra=_top_level_names(here)):
                    removed += 1
                    logger.info("Aged out an old staged update: %s", path)
        try:
            os.rmdir(root)                          # only succeeds when empty
        except OSError:
            pass
    except Exception:
        logger.debug("Staging-root age-out failed.", exc_info=True)
    return removed


def run_update_janitor(retry_delays: Iterable[float] = _JANITOR_RETRY_DELAYS,
                       sleep=time.sleep) -> int:
    """The post-update cleanup, run by the NEW version once it has started (#260).

    Waits for the applier recorded in the records to exit (it runs from the staged folder), removes
    what the update left - the staged copy, its zip, the previous version - retrying while AV or the
    indexer still holds a just-closed file, then sweeps unrecorded ``.old-`` folders from older
    appliers and ages out the staging root. Anything still locked stays recorded for the next
    launch. Never raises; returns the number of items removed.
    """
    removed = 0
    try:
        with _marker_lock:
            pids = {e.get("applier_pid") for e in _read_staged_paths()}
            pids |= {r.get("applier_pid") for r in _read_backup_records()}
        gone = set()
        for pid in pids:
            if isinstance(pid, int) and pid > 0 and pid != os.getpid():
                if _wait_for_exit(pid, _APPLIER_EXIT_WAIT_SEC):
                    gone.add(pid)
        if gone:
            _forget_applier_pids(gone)              # never wait on a recycled PID again (L3)
        done, locked = _sweep_once(count_attempt=True)
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


def _forget_applier_pids(pids: set) -> None:
    """Drop ``applier_pid`` from every record whose applier has been seen to exit."""
    try:
        with _marker_lock:
            for path, readable in ((_marker_path(), True),
                                   (_backups_path(), not _backups_record_unreadable())):
                if not readable or not os.path.isfile(path):
                    continue
                items = _read_list(path)
                changed = False
                for it in items:
                    if isinstance(it, dict) and it.get("applier_pid") in pids:
                        it.pop("applier_pid")
                        changed = True
                if changed:
                    _write_list(path, items)
    except Exception:
        logger.debug("Could not clear applier pids.", exc_info=True)


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
