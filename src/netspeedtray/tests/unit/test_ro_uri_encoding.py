"""
Read-only SQLite opens must survive any legal Windows path.

``sqlite3.connect(f"file:{path}?mode=ro", uri=True)`` hands SQLite an UNENCODED URI. A ``#`` in the
path (a username like ``Bob#2`` puts one in ``%APPDATA%``) starts the URI fragment: the path is cut
there, ``?mode=ro`` is lost with it, and SQLite opens - and CREATES - the truncated path read-write.
``_verify_backup`` then checks an empty stray file instead of the backup, fails, and every
migration on that profile aborts forever. ``%`` and ``?`` break the same way.
"""
import os
import sqlite3
import sys

import pytest

sys.path.append(os.path.abspath("src"))
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri

AWKWARD = ("Bob#2", "pct%41x", "space and#hash", "dollar$sign", "café")


@pytest.mark.parametrize("name", AWKWARD)
def test_verify_backup_works_under_an_awkward_profile_path(tmp_path, name):
    d = tmp_path / name / "NetSpeedTray"
    d.mkdir(parents=True)
    w = DatabaseWorker(d / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    try:
        assert w._backup_database() is True, f"backup did not verify under {name!r}"
        assert len(list(d.glob("*.bak.*"))) == 1
    finally:
        w.conn.close()
    # nothing was created outside the profile directory (the truncated-URI stray file)
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]
    assert sorted(p.name for p in (tmp_path / name).iterdir()) == ["NetSpeedTray"]


@pytest.mark.parametrize("name", AWKWARD + ("plain",))
def test_ro_uri_opens_the_right_file_read_only(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    db = d / "x.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (v)")
    c.execute("INSERT INTO t VALUES (42)")
    c.commit()
    c.close()
    ro = sqlite3.connect(sqlite_ro_uri(db), uri=True)
    try:
        assert ro.execute("SELECT v FROM t").fetchone() == (42,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro.execute("INSERT INTO t VALUES (1)")
    finally:
        ro.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_ro_uri_never_creates_a_missing_file(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(sqlite_ro_uri(tmp_path / "a#b" / "missing.db"), uri=True).execute("SELECT 1")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows-only")
def test_ro_uri_handles_a_unc_path(tmp_path):
    db = tmp_path / "u#1" / "x.db"
    db.parent.mkdir()
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (v)")
    c.commit()
    c.close()
    unc = "\\\\localhost\\" + str(db)[0] + "$" + str(db)[2:]
    if not os.path.exists(unc):
        pytest.skip("administrative share not reachable")
    ro = sqlite3.connect(sqlite_ro_uri(unc), uri=True)
    try:
        assert ro.execute("SELECT COUNT(*) FROM t").fetchone() == (0,)
    finally:
        ro.close()


def test_no_shipped_code_builds_an_unencoded_file_uri():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    offenders = []
    for dirpath, _dirs, files in os.walk(root):
        if "tests" in dirpath.split(os.sep):
            continue
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                src = open(p, encoding="utf-8", errors="ignore").read()
                for needle in ('f"file:', "f'file:", '"file:" +', "'file:' +"):
                    if needle in src and not p.endswith("sqlite_uri.py"):
                        offenders.append((os.path.relpath(p, root), needle))
    assert offenders == []
