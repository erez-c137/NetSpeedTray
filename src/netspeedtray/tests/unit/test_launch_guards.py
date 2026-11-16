"""The updater's TOCTOU launch guards (update_installer._acquire/_release_launch_guards).

Hardening added after the re-audit: reparse points (symlinks/junctions) at the download path
or its parent directory are refused outright - a handle opened WITHOUT FILE_FLAG_OPEN_REPARSE
_POINT follows a planted link and pins the TARGET, leaving the link itself free to be deleted
and re-pointed after verification (which would elevate a never-verified binary via the path
string). Junctions are directory links, so the refusal is tested through the parent directory;
the file-path branch shares the same check.

The guards are also only as good as their lifetime: Windows refuses modification of the pinned
file/directory WHILE the handles are open (tested directly below), so every path out of an
update - elevated launch (success, declined UAC, shell failure) and cleanup - must drop them,
and _cleanup_file must drop them BEFORE the rmtree or the delete silently fails.
"""
import os
import shutil
import _winapi

import pytest

from netspeedtray.core import update_installer as ui


@pytest.fixture(scope="module", autouse=True)
def _no_leftover_guards():
    yield
    ui._release_launch_guards()


def test_guards_accept_a_plain_file_and_release_cleanly(tmp_path):
    dest = tmp_path / "NetSpeedTray-Setup.exe"
    dest.write_bytes(b"MZ fake installer")
    assert ui._acquire_launch_guards(str(dest)) is True
    assert ui._LAUNCH_GUARDS is not None and len(ui._LAUNCH_GUARDS) == 2
    ui._release_launch_guards()
    assert ui._LAUNCH_GUARDS is None
    dest.unlink()   # the pins are gone: the file is deletable again (what _cleanup_file needs)


def test_guards_refuse_a_junctioned_parent_directory(tmp_path):
    """A junction at the download's parent means the private temp dir was tampered with: refuse
    closed (the update falls back to the browser) instead of pinning through the link."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "NetSpeedTray-Setup.exe").write_bytes(b"MZ fake installer")
    link = tmp_path / "NetSpeedTray-update-planted"
    _winapi.CreateJunction(str(real), str(link))
    try:
        assert ui._acquire_launch_guards(str(link / "NetSpeedTray-Setup.exe")) is False
        assert ui._LAUNCH_GUARDS is None
    finally:
        os.rmdir(link)   # remove the junction itself, not its target


@pytest.mark.skipif(os.name != "nt",
                    reason="sharing violations are Windows handle semantics; the guard is a no-op elsewhere")
def test_guard_blocks_write_delete_and_rename_while_held(tmp_path):
    """The whole point of the guards: while they hold, Windows itself refuses every modification
    of the pinned file and its directory - a swap in the verify->launch window cannot open the
    file for write, delete it, or rename its folder away. Prove each refusal with raw kernel32
    (same prototypes as the module under test), then prove the SAME operations succeed once the
    pins are dropped, so any failure here is the guard and nothing else."""
    import ctypes
    import ctypes.wintypes as wintypes
    sub = tmp_path / "sub"
    sub.mkdir()
    dest = sub / "NetSpeedTray-Setup.exe"
    dest.write_bytes(b"MZ fake installer")

    assert ui._acquire_launch_guards(str(dest)) is True
    assert ui._LAUNCH_GUARDS is not None and len(ui._LAUNCH_GUARDS) == 2

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Prototypes as in _acquire_launch_guards: handles are c_void_p (never truncated to c_int),
    # strings wide, results typed - otherwise the INVALID_HANDLE_VALUE compare is meaningless.
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    GENERIC_WRITE, OPEN_EXISTING = 0x40000000, 3
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    # Open-for-write: the guard's read-only handle shares READ only, so a writer is refused...
    assert k32.CreateFileW(str(dest), GENERIC_WRITE, 0, None, OPEN_EXISTING, 0,
                           None) == INVALID_HANDLE_VALUE
    # ...and so is delete (rename-over and in-place rewrite both need write/delete access)...
    with pytest.raises(OSError):
        os.remove(str(dest))
    # ...and the directory cannot be renamed out from under the launch path.
    with pytest.raises(OSError):
        os.rename(str(sub), str(tmp_path / "sub-renamed"))

    ui._release_launch_guards()
    assert ui._LAUNCH_GUARDS is None
    # Same operations, pins dropped: the write-open now succeeds and the file is deletable -
    # this is what lets _cleanup_file remove the download once the update is done.
    h = k32.CreateFileW(str(dest), GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
    assert h is not None and h != INVALID_HANDLE_VALUE
    k32.CloseHandle(h)
    os.remove(str(dest))


def test_launch_installer_releases_guards_on_success(tmp_path, monkeypatch):
    """The happy path: ShellExecuteEx gives us the shell's process handle, launch_installer
    waits on it for real (a bogus handle just yields WAIT_FAILED, which it logs and moves on
    from) and drops the pins in its finally - whatever the wait said."""
    dest = tmp_path / "NetSpeedTray-Setup.exe"
    dest.write_bytes(b"MZ fake installer")
    assert ui._acquire_launch_guards(str(dest)) is True
    if os.name == "nt":   # real handles exist only there; elsewhere the guard trivially holds
        assert ui._LAUNCH_GUARDS is not None

    monkeypatch.setattr(ui, "_shell_execute_runas", lambda *a, **k: 0xDEADBEEF)
    ui.launch_installer(str(dest))   # must not raise, whatever the wait made of the handle

    assert ui._LAUNCH_GUARDS is None
    dest.unlink()   # the pins are gone: the file is deletable (proves the handles were closed)


def test_launch_installer_releases_guards_when_shell_gives_no_handle(tmp_path, monkeypatch):
    """ShellExecuteEx may legitimately hand back no process handle (hProcess is best-effort);
    launch_installer skips the wait but must still drop the pins in its finally."""
    dest = tmp_path / "NetSpeedTray-Setup.exe"
    dest.write_bytes(b"MZ fake installer")
    assert ui._acquire_launch_guards(str(dest)) is True

    monkeypatch.setattr(ui, "_shell_execute_runas", lambda *a, **k: None)
    ui.launch_installer(str(dest))   # must not raise

    assert ui._LAUNCH_GUARDS is None
    dest.unlink()


def test_launch_installer_releases_guards_when_uac_declined(tmp_path, monkeypatch):
    """A declined UAC prompt raises UpdateElevationDeclined for the UI to catch - the update is
    over, so the exception must not carry the pins with it: the finally releases them first."""
    dest = tmp_path / "NetSpeedTray-Setup.exe"
    dest.write_bytes(b"MZ fake installer")
    assert ui._acquire_launch_guards(str(dest)) is True

    def declined(*a, **k):
        raise ui.UpdateElevationDeclined("the UAC prompt was declined")
    monkeypatch.setattr(ui, "_shell_execute_runas", declined)
    with pytest.raises(ui.UpdateElevationDeclined):
        ui.launch_installer(str(dest))

    assert ui._LAUNCH_GUARDS is None
    dest.unlink()


def test_launch_installer_releases_guards_on_shell_error(tmp_path, monkeypatch):
    """Same contract for a hard shell failure (ShellExecuteEx erroring for any other reason):
    the OSError propagates, the pins do not outlive it."""
    dest = tmp_path / "NetSpeedTray-Setup.exe"
    dest.write_bytes(b"MZ fake installer")
    assert ui._acquire_launch_guards(str(dest)) is True

    def boom(*a, **k):
        raise OSError(5, "access is denied")
    monkeypatch.setattr(ui, "_shell_execute_runas", boom)
    with pytest.raises(OSError):
        ui.launch_installer(str(dest))

    assert ui._LAUNCH_GUARDS is None
    dest.unlink()


def test_cleanup_file_releases_guards_before_rmtree(tmp_path, monkeypatch):
    """_cleanup_file's own comment says why the order matters: while a guard is held the pinned
    file cannot be deleted, so an rmtree that ran first would silently fail (ignore_errors=True)
    and leave the download behind. Record both steps to prove the release is strictly first,
    with real guards seeded so the release call does real work too."""
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "NetSpeedTray-Setup.exe").write_bytes(b"MZ fake installer")

    assert ui._acquire_launch_guards(str(dl / "NetSpeedTray-Setup.exe")) is True

    updater = ui.SecureUpdater.__new__(ui.SecureUpdater)   # skip __init__: it needs a QWidget parent
    updater._tmpdir = str(dl)
    updater._dest = str(dl / "NetSpeedTray-Setup.exe")

    events = []
    real_release = ui._release_launch_guards

    def recording_release():
        events.append("release")
        real_release()   # the seeded handles get closed for real, not just counted

    real_rmtree = shutil.rmtree

    def recording_rmtree(path, *a, **k):
        events.append("rmtree")
        real_rmtree(path, *a, **k)   # the module imports shutil inside the method: same module

    monkeypatch.setattr(ui, "_release_launch_guards", recording_release)
    monkeypatch.setattr(shutil, "rmtree", recording_rmtree)

    updater._cleanup_file()

    assert events == ["release", "rmtree"]   # the release is strictly before the rmtree
    assert ui._LAUNCH_GUARDS is None
    assert not dl.exists()   # the pinned file was actually removable once the pins dropped
