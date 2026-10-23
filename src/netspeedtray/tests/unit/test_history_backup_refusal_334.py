"""
#334 review (PR #439): "Back up history" refuses the live file's name and its siblings - proven on an
UNLOCKED layout, so Windows' share lock on an open database cannot be what makes the test pass.

``test_usage_history_334.test_backup_refuses_the_live_name`` runs with the writer's connection still
open: for ``speed_history.db`` / ``-wal`` the final ``os.replace`` would fail on the lock anyway, so that
test also passes with ``_refused_target`` gutted. Here every connection is closed first (the files can be
renamed and deleted), and the test checks that nothing in the folder changes - names and bytes.

The second group spells the SAME folder another way (``\\\\?\\`` prefix, 8.3 short name, a directory
junction). ``os.path.abspath`` + ``normcase`` sees different strings, so a refusal that compares text
lets them through.
"""
import ctypes
import hashlib
import os
import sys
from pathlib import Path

import pytest

from netspeedtray.core.db import history_backup as B
from netspeedtray.tests.unit import v8_twin_fixture as F

NOW = F.fixed_now()

SIBLINGS = ["speed_history.db", "speed_history.db-wal", "speed_history.db-shm", "speed_history.db-journal",
            "SPEED_HISTORY.DB", "speed_history.db.bak.v7_20261001-120000"]


def _snapshot(folder: Path):
    return sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in folder.iterdir() if p.is_file())


@pytest.fixture()
def unlocked(tmp_path):
    """A v8 history plus a pre-v8 backup beside it, every connection closed: nothing holds a lock."""
    d = tmp_path / "NetSpeedTray"
    d.mkdir()
    p = d / "speed_history.db"
    F.write_v8(p, F.make_history(NOW)).close()
    (d / "speed_history.db.bak.v7_20261001-120000").write_bytes(b"pre-v8 backup bytes")
    os.rename(p, p.with_name("probe.tmp"))        # proof that nothing holds the file open
    os.rename(p.with_name("probe.tmp"), p)
    return p


@pytest.mark.parametrize("name", SIBLINGS)
def test_refused_on_an_unlocked_layout(unlocked, name):
    p = unlocked
    before = _snapshot(p.parent)
    with pytest.raises(B.BackupError):
        B.backup_history(p, p.parent / name)
    assert _snapshot(p.parent) == before


def _short_path(path: Path) -> str:
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    return buf.value if n else ""


def _aliases(folder: Path):
    out = [("extended", "\\\\?\\" + str(folder))]
    short = _short_path(folder)
    if short and os.path.normcase(short) != os.path.normcase(str(folder)):
        out.append(("short", short))
    return out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path aliases")
@pytest.mark.parametrize("kind", ["extended", "short", "junction"])
def test_refused_through_another_spelling_of_the_folder(unlocked, tmp_path, kind):
    p = unlocked
    if kind == "junction":
        import _winapi
        j = tmp_path / "alias"
        _winapi.CreateJunction(str(p.parent), str(j))
        folder = str(j)
    else:
        found = dict(_aliases(p.parent))
        if kind not in found:
            pytest.skip(f"no {kind} name for this folder (8.3 names off on this volume?)")
        folder = found[kind]
    before = _snapshot(p.parent)
    with pytest.raises(B.BackupError):
        B.backup_history(p, Path(folder) / "speed_history.db.bak.v7_20261001-120000")
    assert _snapshot(p.parent) == before
