"""
The pre-migration backup must actually contain the user's data.

`test_db_migration.test_backup_database_success` asserts a `.bak` file exists and
that its name contains a version. It never opens it. That is the same blind spot
that let `WinError 5` through on the portable updater despite 16 green tests: the
assertion checks the artifact was produced, not that it works.

It matters because the old implementation used `shutil.copy2`, which copies only
the main `.db` file. In WAL mode, everything committed since the last checkpoint
lives in the `-wal` sidecar, so a copy taken with a hot WAL silently loses it -
and an empty-but-valid database still passes `PRAGMA integrity_check`.

That is not a corner case, it is the default upgrade path: `build/setup.iss` runs
`taskkill /F /IM NetSpeedTray.exe /T` before installing, which is
`TerminateProcess` - no commit, no checkpoint, guaranteed hot WAL. So the one
moment the backup matters is the one moment it was guaranteed to be incomplete.

These tests open the copy and compare it to the source.
"""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.abspath("src"))
from netspeedtray.core.widget_state import DatabaseWorker


@pytest.fixture
def worker(tmp_path):
    w = DatabaseWorker(tmp_path / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    yield w
    if w.conn:
        w.conn.close()


def _seed(worker, rows=5000):
    """Commit rows without checkpointing, leaving content in the WAL."""
    worker.conn.execute("PRAGMA wal_autocheckpoint=0")
    worker.conn.executemany(
        "INSERT OR IGNORE INTO speed_history_raw "
        "(timestamp, interface_name, upload_bytes_sec, download_bytes_sec) VALUES (?,?,?,?)",
        [(1787000000 + i, "Wi-Fi", float(i), float(i * 2)) for i in range(rows)],
    )
    worker.conn.commit()
    return rows


def test_backup_captures_data_still_in_the_wal(worker, tmp_path):
    """The regression: a hot WAL is the normal state during an upgrade."""
    rows = _seed(worker)
    wal = Path(str(worker.db_path) + "-wal")
    assert wal.exists() and wal.stat().st_size > 0, "test needs a hot WAL to be meaningful"

    assert worker._backup_database() is True

    backup = next(tmp_path.glob("*.bak.*"))
    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as check:
        assert check.execute("SELECT COUNT(*) FROM speed_history_raw").fetchone()[0] == rows


def test_the_old_copy_based_backup_would_have_lost_it(worker, tmp_path):
    """Pins WHY this changed, so nobody 'simplifies' it back to a file copy."""
    rows = _seed(worker)
    naive = tmp_path / "naive_copy.db"
    shutil.copy2(worker.db_path, naive)

    with sqlite3.connect(f"file:{naive}?mode=ro", uri=True) as check:
        try:
            recovered = check.execute("SELECT COUNT(*) FROM speed_history_raw").fetchone()[0]
        except sqlite3.DatabaseError:
            recovered = 0                     # the table itself never made it
    assert recovered < rows, (
        "a plain file copy is expected to miss WAL content; if this ever passes, "
        "the journal mode or checkpoint behaviour changed - re-check the backup design"
    )


def test_verification_rejects_a_backup_missing_rows(worker, tmp_path):
    """The exact WAL-blind failure mode: structurally valid, content missing."""
    _seed(worker, rows=200)
    assert worker._backup_database() is True
    backup = next(tmp_path.glob("*.bak.*"))

    # Make the copy look like one taken without WAL content: valid, but short.
    victim = sqlite3.connect(backup)
    victim.execute("DELETE FROM speed_history_raw WHERE timestamp > 1787000100")
    victim.commit()
    assert victim.execute("PRAGMA quick_check").fetchone()[0] == "ok"   # still 'valid'
    victim.close()

    assert worker._verify_backup(backup, worker._DB_VERSION) is False


def test_verification_rejects_a_version_mismatch(worker, tmp_path):
    _seed(worker, rows=50)
    assert worker._backup_database() is True
    backup = next(tmp_path.glob("*.bak.*"))
    assert worker._verify_backup(backup, worker._DB_VERSION + 99) is False


def test_failed_verification_leaves_no_file_behind(worker, monkeypatch, tmp_path):
    """A backup that silently isn't one is worse than no backup."""
    _seed(worker, rows=100)
    monkeypatch.setattr(worker, "_verify_backup", lambda *a, **k: False)

    assert worker._backup_database() is False, "an unverified backup must not report success"
    assert list(tmp_path.glob("*.bak.*")) == [], "a failed backup must not leave a file behind"


def test_repeated_backups_within_one_second_do_not_collide(worker, tmp_path):
    """VACUUM INTO refuses an existing target; init retries are 0.1s apart."""
    _seed(worker, rows=50)
    assert worker._backup_database() is True
    assert worker._backup_database() is True
    assert len(list(tmp_path.glob("*.bak.*"))) == 2


def test_old_backups_are_pruned(worker, tmp_path):
    """Nothing used to delete these, at any size, ever."""
    _seed(worker, rows=50)
    for _ in range(5):
        assert worker._backup_database() is True
    assert len(list(tmp_path.glob("*.bak.*"))) == worker._BACKUP_RETENTION
