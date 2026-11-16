"""The updater's TOCTOU launch guards (update_installer._acquire/_release_launch_guards).

Hardening added after the re-audit: reparse points (symlinks/junctions) at the download path
or its parent directory are refused outright - a handle opened WITHOUT FILE_FLAG_OPEN_REPARSE
_POINT follows a planted link and pins the TARGET, leaving the link itself free to be deleted
and re-pointed after verification (which would elevate a never-verified binary via the path
string). Junctions are directory links, so the refusal is tested through the parent directory;
the file-path branch shares the same check.
"""
import os
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
