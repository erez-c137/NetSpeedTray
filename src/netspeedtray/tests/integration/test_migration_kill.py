"""
The v8 migration, tested by killing it (release-plan Gate 2; implementation step 5).

Real processes, a real populated v7 database with a HOT WAL, and ``TerminateProcess`` - no mocks.
For each of the migrator's kill points (``migrate_v8.KILLPOINTS``), plus five ``Popen.kill`` runs
at random delays, the migrator is killed and then relaunched WITHOUT the hooks, and:

1. after a recovery-only relaunch the live file is either v7 holding every manifest row, or v8
   with every manifest row mapped (read through the compatibility views);
2. after a full relaunch it is v8 with every row mapped, and nothing of the migration is left over;
3. the newest ``.bak.v7_*`` - restored per DATABASE.md "Restoring a backup" while the live v8 file
   has HOT ``-wal`` / ``-shm`` sidecars, which the restore must delete - matches the manifest
   exactly, the per-second raw tier included.

``quick_check`` is never the assertion: an empty file passes it. Every check compares rows.
"""
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

if sys.platform != "win32":
    pytest.skip("the kill test drives Windows TerminateProcess", allow_module_level=True)

from netspeedtray.core.db import migrate_v8 as M
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.tests.integration import v7_fixture as F

SRC = str(Path(__file__).resolve().parents[3])
DB = "speed_history.db"
KILLED = 137

_BOOT = ("import sys; sys.path.insert(0, {src!r}); "
         "from netspeedtray.core.db.migrate_v8 import main; sys.exit(main(sys.argv[1:]))")
_FIX = ("import sys; sys.path.insert(0, {src!r}); "
        "from netspeedtray.tests.integration import v7_fixture as F; F.{fn}(sys.argv[1])")


def _env(killpoint=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("NST_")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    if killpoint:
        env["NST_TEST_HOOKS"] = "1"
        env["NST_KILLPOINT"] = killpoint
    return env


def _migrator(directory, *extra, killpoint=None, timeout=120):
    # -I: isolated (no PYTHONPATH / user site / cwd on sys.path) - the source path is inserted
    # explicitly, the same way a frozen build would find its own package.
    return subprocess.run([sys.executable, "-I", "-c", _BOOT.format(src=SRC), str(directory), *extra],
                          env=_env(killpoint), capture_output=True, text=True, timeout=timeout)


def _fixture_child(fn, arg):
    p = subprocess.run([sys.executable, "-I", "-c", _FIX.format(src=SRC, fn=fn), str(arg)],
                       env=_env(), capture_output=True, text=True, timeout=120)
    assert p.returncode == KILLED, f"fixture child did not self-terminate: {p.returncode}\n{p.stderr}"


# --------------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------------

# NST_KILLTEST_SOURCE=<path to a v7 .db> runs the whole matrix on THAT data instead of the
# synthetic fixture (used once, by hand, on a read-only copy of the owner's database). The source
# is only ever opened read-only.
REAL_SOURCE = os.environ.get("NST_KILLTEST_SOURCE")


@pytest.fixture(scope="module")
def manifest():
    return F.manifest_from_db(REAL_SOURCE) if REAL_SOURCE else F.manifest()


@pytest.fixture(scope="module")
def hot_template(tmp_path_factory, manifest):
    """Built ONCE: the v7 file + hot -wal + -shm exactly as the self-terminated child left them.
    A terminated process's files are static, so a file copy reproduces the state faithfully."""
    d = tmp_path_factory.mktemp("v7_hot_template")
    if REAL_SOURCE:
        _fixture_child("hot_copy_main", f"{REAL_SOURCE}|{d}")
    else:
        _fixture_child("child_main", d)
    wal = d / (DB + "-wal")
    assert wal.exists() and wal.stat().st_size > 0, "the fixture must leave a HOT WAL"
    # ...and the WAL must matter: the main file alone does not hold the manifest.
    probe = d.parent / (d.name + "_mainonly")
    probe.mkdir()
    shutil.copy2(d / DB, probe / DB)
    c = sqlite3.connect(probe / DB)
    try:
        n = c.execute("SELECT COUNT(*) FROM hardware_stats_raw").fetchone()[0]
    except sqlite3.DatabaseError:
        n = 0
    finally:
        c.close()
    assert n < len(manifest["hardware_stats_raw"]), "rows must live in the WAL, not the main file"
    return d


@pytest.fixture
def hot_db(hot_template, tmp_path):
    d = tmp_path / "appdata"
    d.mkdir()
    for f in hot_template.iterdir():
        shutil.copy2(f, d / f.name)
    assert (d / (DB + "-wal")).stat().st_size > 0
    return d


# --------------------------------------------------------------------------------------------
# assertions (always on a SNAPSHOT copy, so opening a file never changes the state under test)
# --------------------------------------------------------------------------------------------

def _snapshot(directory: Path, tmp_path: Path, tag: str) -> Path:
    snap = tmp_path / f"snap_{tag}"
    if snap.exists():
        shutil.rmtree(snap)
    snap.mkdir()
    for f in directory.iterdir():
        if f.name.startswith(DB):
            shutil.copy2(f, snap / f.name)
    return snap


def _rows(c, table, cols="*"):
    return sorted(tuple(r) for r in c.execute(f"SELECT {cols} FROM {table}"))


def _version(c):
    return c.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0]


_MAN = {}   # the active manifest, set by the autouse fixture below


@pytest.fixture(autouse=True)
def _active_manifest(manifest):
    _MAN["m"] = manifest


def _assert_v7_complete(c):
    for table, want in _MAN["m"].items():
        got = _rows(c, table)
        assert got == want, f"v7 {table}: {len(got)} rows, manifest has {len(want)}"


def _assert_v8_complete(c):
    if REAL_SOURCE:
        exp = F.expected_v8_view_rows(_MAN["m"], None)
        folded = F.folded_minute_keys(_MAN["m"])
    else:
        exp = F.expected_v8_view_rows()
        folded = F.folded_minute_keys()
        assert F.merged_minute_keys(), "the fixture must exercise the minute-tier merge"
    for table, want in exp.items():
        got = _rows(c, table)
        if table == "metadata":
            got_wm = {k: v for k, v in got if k in S.WATERMARK_KEYS}
            assert set(got_wm) == set(S.WATERMARK_KEYS), "the tier watermarks must be stamped"
            if REAL_SOURCE:          # values are checked by verify() against the source
                got = [r for r in got if r[0] not in S.WATERMARK_KEYS]
        assert len(got) == len(want), f"v8 {table}: {len(got)} rows, expected {len(want)}"
        if table != F.SPEED_MIN:
            assert got == want, f"v8 {table} differs from the manifest"
            continue
        for g, w in zip(got, want):
            if (w[0], w[1]) in folded:          # AVG / sample-weighted merge: summation order
                assert g[:2] == w[:2] and g[4:] == w[4:]
                assert g[2] == pytest.approx(w[2], rel=1e-12) and g[3] == pytest.approx(w[3], rel=1e-12)
            else:
                assert g == w
    # Pre-v8 rows carry no invented bytes or coverage.
    for t in ("net_minute", "net_hour"):
        assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE up_bytes IS NOT NULL OR down_bytes IS NOT NULL "
                         "OR covered_ms IS NOT NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM net_raw").fetchone()[0] == 0
    assert c.execute("SELECT name FROM sqlite_master WHERE name='bandwidth_history'").fetchone() is None
    assert c.execute("PRAGMA auto_vacuum").fetchone()[0] == 2


def _live_state(directory: Path, tmp_path: Path, tag: str) -> str:
    """'v7' or 'v8' - and fails unless that version holds every manifest row."""
    snap = _snapshot(directory, tmp_path, tag)
    assert (snap / DB).exists(), f"[{tag}] no live database after relaunch: {sorted(os.listdir(directory))}"
    c = sqlite3.connect(snap / DB)
    try:
        v = _version(c)
        if v == "7":
            _assert_v7_complete(c)
        elif v == "8":
            _assert_v8_complete(c)
        else:
            pytest.fail(f"[{tag}] live db_version is {v!r}")
        return "v" + v
    finally:
        c.close()


def _newest_v7_backup(directory: Path) -> Path:
    baks = sorted(directory.glob(DB + ".bak.v7_*"), key=lambda p: (p.stat().st_mtime, p.name))
    assert baks, f"no .bak.v7_* in {sorted(os.listdir(directory))}"
    return baks[-1]


def _restore_per_database_md(directory: Path) -> None:
    """DATABASE.md, "Restoring a backup", with the app closed: 1. delete speed_history.db, -wal AND
    -shm; 2. COPY the .bak (not rename) to speed_history.db."""
    bak = _newest_v7_backup(directory)
    for name in (DB, DB + "-wal", DB + "-shm"):
        (directory / name).unlink(missing_ok=True)
    shutil.copy2(bak, directory / DB)


def _make_live_hot(directory: Path) -> None:
    _fixture_child("make_hot_v8_wal_main", directory / DB)
    assert (directory / (DB + "-wal")).stat().st_size > 0 and (directory / (DB + "-shm")).exists(), \
        "the restore must be exercised with hot sidecars beside the live file"


def _assert_full_cycle(directory: Path, tmp_path: Path, tag: str) -> str:
    # 1. recovery only
    p = _migrator(directory, "--recover-only")
    assert p.returncode == 0, f"[{tag}] recovery failed: {p.stdout}\n{p.stderr}"
    state = _live_state(directory, tmp_path, tag + "_recovered")
    # 2. a normal launch: migrates if it is still v7
    p = _migrator(directory)
    assert p.returncode == 0, f"[{tag}] relaunch failed: {p.stdout}\n{p.stderr}"
    assert _live_state(directory, tmp_path, tag + "_relaunched") == "v8"
    left = sorted(f.name for f in directory.iterdir()
                  if f.name.endswith(M.TMP_SUFFIX) or M.TMP_SUFFIX + "-" in f.name or f.name.startswith(M.JOURNAL_NAME))
    assert left == [], f"[{tag}] migration left {left}"
    # 3. restore the v7 backup over a HOT v8 live file
    _make_live_hot(directory)
    _restore_per_database_md(directory)
    c = sqlite3.connect(directory / DB)
    try:
        assert _version(c) == "7"
        _assert_v7_complete(c)
    finally:
        c.close()
    return state


# --------------------------------------------------------------------------------------------
# the matrix
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", M.KILLPOINTS)
def test_kill_at_every_point_then_relaunch(point, hot_db, tmp_path):
    extra = ("--simulate-first-persist",) if point == "first_v8_persist" else ()
    p = _migrator(hot_db, *extra, killpoint=point)
    assert p.returncode == KILLED, (
        f"the kill at {point!r} never fired (rc={p.returncode}) - the matrix would be testing "
        f"nothing\n{p.stdout}\n{p.stderr}")
    state = _assert_full_cycle(hot_db, tmp_path, point)
    # Before the journal exists the v7 file is untouched, so recovery must leave v7; from the
    # first rename on, the swap is completed.
    before_swap = M.KILLPOINTS.index(point) < M.KILLPOINTS.index("after_journal_pre_rename")
    assert state == ("v7" if before_swap else "v8"), f"{point}: recovered to {state}"


@pytest.mark.parametrize("seed", range(5))
def test_kill_at_a_random_moment_then_relaunch(seed, hot_db, hot_template, tmp_path):
    # Calibrated on identical copies: `start` = interpreter + imports + recover() (a --recover-only
    # run), `full` = a whole unkilled migration. Delays land between the two, i.e. inside migrate().
    cal = tmp_path / "calibrate"
    shutil.copytree(hot_db, cal)
    t = time.perf_counter()
    assert _migrator(cal, "--recover-only").returncode == 0
    start = time.perf_counter() - t
    shutil.rmtree(cal)
    shutil.copytree(hot_db, cal)
    t = time.perf_counter()
    assert _migrator(cal).returncode == 0
    full = time.perf_counter() - t
    rng = random.Random(1000 + seed)
    delay = rng.uniform(start, max(start, 0.8 * full))
    for attempt in range(4):
        proc = subprocess.Popen([sys.executable, "-I", "-c", _BOOT.format(src=SRC), str(hot_db)],
                                env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(delay)
        proc.kill()                   # Popen.kill on Windows IS TerminateProcess(handle, 1)
        rc = proc.wait(timeout=60)
        if rc == 1:
            break
        # The child finished before the kill landed - this run tested nothing. Start over from the
        # hot template with a shorter delay; never count it as a pass.
        assert rc == 0, f"migrator failed on its own (rc={rc})"
        shutil.rmtree(hot_db)
        hot_db.mkdir()
        for f in hot_template.iterdir():
            shutil.copy2(f, hot_db / f.name)
        delay = start + (delay - start) * 0.5
    else:
        pytest.fail(f"seed {seed}: the kill never landed before the migrator finished "
                    f"(start={start:.2f}s full={full:.2f}s last delay={delay:.2f}s)")
    _assert_full_cycle(hot_db, tmp_path, f"random{seed}")


def test_naive_restore_with_hot_sidecars_is_wrong(hot_db, tmp_path):
    """Negative control for the restore assertion above: copying the .bak over the live file while
    the v8 -wal stays put does NOT give back the v7 data - so the sidecars in that test were hot
    and the delete step is what made the restore correct."""
    assert _migrator(hot_db).returncode == 0
    _make_live_hot(hot_db)
    shutil.copy2(_newest_v7_backup(hot_db), hot_db / DB)        # step 1 skipped
    c = sqlite3.connect(hot_db / DB)
    try:
        try:
            _assert_v7_complete(c)
        except (AssertionError, sqlite3.DatabaseError):
            return
        pytest.fail("a restore that leaves the v8 -wal behind came back whole; the hot-sidecar "
                    "restore test is not testing what it claims")
    finally:
        c.close()


def test_backup_is_the_one_a_rolled_back_build_names(hot_db):
    """Step 0 interplay: 2.1.x's _find_newest_backup (versions <= 7 only) names the migrator's
    backup, and version-aware pruning keeps it when newer backups pile up."""
    assert _migrator(hot_db).returncode == 0
    bak = _newest_v7_backup(hot_db)
    from netspeedtray.core.database import DatabaseWorker
    w = DatabaseWorker(hot_db / DB)
    assert w._find_newest_backup() == bak
    for i in range(3):
        newer = hot_db / f"{DB}.bak.v8_20991231_00000{i}"
        newer.write_bytes(b"x")
        os.utime(newer, (time.time() + 10 + i, time.time() + 10 + i))
    w._prune_old_backups()
    assert bak.exists(), "pruning deleted the only v7 backup"
