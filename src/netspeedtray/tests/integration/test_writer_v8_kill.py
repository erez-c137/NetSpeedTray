"""
The v8 writer, tested by killing it (implementation step 6; the step-5 pattern).

A real v8 file in WAL mode, a child process running the writer, and ``TerminateProcess`` at each of
``writer_v8.KILLPOINTS`` - between a roll-up's row move and its watermark write, between the
watermark write and COMMIT, and inside the persist tick. After each kill:

1. read with the watermark-precedence rule, the file still holds EXACTLY the fixture's bytes and
   hardware sums - nothing lost, nothing counted twice;
2. the killed move left no trace: its watermark is not written and its source tier is whole;
3. a relaunch without hooks completes the pass, and then a plain SUM over every tier agrees with the
   precedence read (moved, not copied) and every watermark is written.

The persist-tick kill proves the crash semantics end to end: the committed heartbeat survives, the
uncommitted rows and heartbeat do not, and the session stays open (ended NULL, clean_exit 0).
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

from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db import writer_v8 as W
from netspeedtray.tests.integration import writer_v8_child as C

SRC = str(Path(__file__).resolve().parents[3])
DB = C.DB
KILLED = 137
NOW = int(time.time())

_BOOT = ("import sys; sys.path.insert(0, {src!r}); "
         "from netspeedtray.tests.integration.writer_v8_child import main; sys.exit(main(sys.argv[1:]))")


def _env(killpoint=None, hooks=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("NST_")}
    if killpoint or hooks:
        env["NST_TEST_HOOKS"] = "1"
    if killpoint:
        env["NST_KILLPOINT"] = killpoint
    return env


def _child(mode, directory, killpoint=None, hooks=False):
    return subprocess.run([sys.executable, "-I", "-c", _BOOT.format(src=SRC), mode, str(directory), str(NOW)],
                          env=_env(killpoint, hooks), capture_output=True, text=True, timeout=180)


@pytest.fixture(scope="module", params=["empty_dest", "legacy_dest"])
def template(request, tmp_path_factory):
    """``legacy_dest``: the destination tiers already hold migrated (NULL-bytes) rows at the buckets the
    roll-ups land on, so every kill also lands inside the MERGE path."""
    d = tmp_path_factory.mktemp(f"writer_v8_template_{request.param}")
    manifest = C.build(d, NOW, legacy_dest=request.param == "legacy_dest")
    return d, manifest


@pytest.fixture
def db(template, tmp_path):
    src, manifest = template
    d = tmp_path / "appdata"
    d.mkdir()
    for f in src.iterdir():
        shutil.copy2(f, d / f.name)
    return d, manifest


def _snapshot(d: Path, tmp: Path, tag: str) -> sqlite3.Connection:
    """Open a COPY (with whatever -wal the killed child left), so checking never changes the state."""
    s = tmp / f"snap_{tag}"
    s.mkdir()
    for f in d.iterdir():
        # A just-killed process's byte-range locks can outlive Popen.wait() by a few ms (WinError 33).
        for attempt in range(50):
            try:
                shutil.copy2(f, s / f.name)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.1)
    return sqlite3.connect(s / DB)


def _wm(c, key):
    r = c.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return None if r is None else int(r[0])


def _bounds(c, k_raw, k_min):
    big = 1 << 62
    wr, wm = _wm(c, k_raw), _wm(c, k_min)
    return ((-big if wr is None else wr, big), (-big if wm is None else wm, big if wr is None else wr),
            (-big, min(big if wr is None else wr, big if wm is None else wm)))


# The reader's volume rule: exact bytes, else the legacy estimate avg x MIN(sample_count, bucket).
_BUCKET = {S.NET_RAW: 1, S.NET_MINUTE: 60, S.NET_HOUR: 3600}
_EST = "COALESCE(SUM(COALESCE({d}_bytes, {d}_avg * MIN(sample_count, BUCKET_{t}))), 0)"


def _est(t, d):
    if t == S.NET_RAW:
        return f"COALESCE(SUM({d}_bytes), 0)"
    return _EST.replace("BUCKET_{t}", str(_BUCKET[t])).format(d=d)


def _net_volume(c):
    (r_lo, r_hi), (m_lo, m_hi), (h_lo, h_hi) = _bounds(c, S.WATERMARK_RAW_KEY, S.WATERMARK_MINUTE_KEY)
    tot = [0, 0]
    for t, lo, hi in ((S.NET_RAW, r_lo, r_hi), (S.NET_MINUTE, m_lo, m_hi), (S.NET_HOUR, h_lo, h_hi)):
        u, d = c.execute(f"SELECT {_est(t, 'up')}, {_est(t, 'down')} FROM {t} "
                         "WHERE ts >= ? AND ts < ?", (lo, hi)).fetchone()
        tot[0] += u
        tot[1] += d
    return tuple(tot)


def _hw_sum(c, name):
    (r_lo, r_hi), (m_lo, m_hi), (h_lo, h_hi) = _bounds(c, S.HW_WATERMARK_RAW_KEY, S.HW_WATERMARK_MINUTE_KEY)
    mid = c.execute("SELECT metric_id FROM metrics WHERE name = ? AND entity = ''", (name,)).fetchone()[0]
    tot = c.execute(f"SELECT COALESCE(SUM(value), 0) FROM {S.HW_RAW} WHERE metric_id = ? AND ts >= ? AND ts < ?",
                    (mid, r_lo, r_hi)).fetchone()[0]
    for t, lo, hi in ((S.HW_MINUTE, m_lo, m_hi), (S.HW_HOUR, h_lo, h_hi)):
        tot += c.execute(f"SELECT COALESCE(SUM(avg_value * sample_count), 0) FROM {t} "
                         "WHERE metric_id = ? AND ts >= ? AND ts < ?", (mid, lo, hi)).fetchone()[0]
    return tot


def _plain_net_sum(c):
    return tuple(sum(c.execute(f"SELECT {_est(t, d)} FROM {t}").fetchone()[0]
                     for t in (S.NET_RAW, S.NET_MINUTE, S.NET_HOUR)) for d in ("up", "down"))


def _assert_complete(c, manifest):
    assert _net_volume(c) == pytest.approx((manifest["up"], manifest["down"]), rel=1e-12)
    for name in ("cpu", "latency_anchor_timeout"):
        assert _hw_sum(c, name) == pytest.approx(manifest[name], rel=1e-12, abs=1e-9)


# move -> (destination tier, its watermark key). The fixture never ran maintenance, so every
# destination starts empty and no watermark exists.
_MOVES = {"net_raw": (S.NET_MINUTE, S.WATERMARK_RAW_KEY), "net_minute": (S.NET_HOUR, S.WATERMARK_MINUTE_KEY),
          "hw_raw": (S.HW_MINUTE, S.HW_WATERMARK_RAW_KEY), "hw_minute": (S.HW_HOUR, S.HW_WATERMARK_MINUTE_KEY)}
ROLLUP_POINTS = [p for p in W.KILLPOINTS if p.startswith("rollup_")]


def test_every_rollup_killpoint_is_exercised():
    assert sorted(ROLLUP_POINTS) == sorted(f"rollup_{m}_{s}" for m in _MOVES for s in ("moved", "before_commit"))


@pytest.mark.parametrize("point", ROLLUP_POINTS)
def test_kill_mid_rollup_then_relaunch(point, db, tmp_path):
    d, manifest = db
    move = point[len("rollup_"):].rsplit("_", 1)[0].replace("_before", "")
    dest, key = _MOVES[move]
    pre = _snapshot(d, tmp_path, "pre")
    pre_dest = sorted(pre.execute(f"SELECT * FROM {dest}").fetchall())
    assert _wm(pre, key) is None
    pre.close()

    p = _child("maintain", d, killpoint=point)
    assert p.returncode == KILLED, f"{point}: child exited {p.returncode}\n{p.stderr}"
    assert (d / (DB + "-wal")).exists()

    c = _snapshot(d, tmp_path, "killed")
    _assert_complete(c, manifest)
    # The killed move is all-or-nothing: no watermark, its destination tier exactly as before (empty,
    # or the planted legacy rows un-merged).
    assert _wm(c, key) is None
    assert sorted(c.execute(f"SELECT * FROM {dest}").fetchall()) == pre_dest
    c.close()

    p = _child("maintain", d)
    assert p.returncode == 0, p.stderr
    c = _snapshot(d, tmp_path, "relaunched")
    _assert_complete(c, manifest)
    assert _plain_net_sum(c) == pytest.approx((manifest["up"], manifest["down"]), rel=1e-12)
    assert all(_wm(c, k) is not None for k in S.WATERMARK_KEYS)
    for t, k in ((S.NET_RAW, S.WATERMARK_RAW_KEY), (S.NET_MINUTE, S.WATERMARK_MINUTE_KEY),
                 (S.HW_RAW, S.HW_WATERMARK_RAW_KEY), (S.HW_MINUTE, S.HW_WATERMARK_MINUTE_KEY)):
        assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE ts < ?", (_wm(c, k),)).fetchone()[0] == 0, t
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()


@pytest.mark.parametrize("seed", range(5))
def test_kill_maintenance_at_a_random_moment(seed, db, tmp_path):
    d, manifest = db
    rng = random.Random(seed)
    proc = subprocess.Popen([sys.executable, "-I", "-c", _BOOT.format(src=SRC), "maintain", str(d), str(NOW)],
                            env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(rng.uniform(0.08, 0.4))   # child start ~0.2 s, the pass ~0.15 s
    proc.kill()                       # Popen.kill on Windows IS TerminateProcess(handle, 1)
    proc.wait(timeout=60)
    c = _snapshot(d, tmp_path, "killed")
    _assert_complete(c, manifest)
    c.close()
    assert _child("maintain", d).returncode == 0
    c = _snapshot(d, tmp_path, "relaunched")
    _assert_complete(c, manifest)
    assert _plain_net_sum(c) == pytest.approx((manifest["up"], manifest["down"]), rel=1e-12)
    c.close()


def test_kill_inside_the_persist_tick_keeps_the_last_committed_heartbeat(db, tmp_path):
    d, manifest = db
    p = _child("flush", d, hooks=True)
    assert p.returncode == KILLED, f"child exited {p.returncode}\n{p.stderr}"
    assert (d / (DB + "-wal")).stat().st_size > 0, "the kill must leave a HOT WAL"
    c = _snapshot(d, tmp_path, "killed")
    s = c.execute("SELECT started_ts, last_seen_ts, ended_ts, clean_exit FROM app_sessions "
                  "WHERE version = 'kill-child'").fetchall()
    assert s == [(NOW - 100, NOW - 90, None, 0)]          # bounded by the committed heartbeat
    assert c.execute("SELECT up_bytes FROM net_raw WHERE ts IN (?, ?) ORDER BY ts",
                     (NOW - 95, NOW - 85)).fetchall() == [(111,)]
    assert c.execute("SELECT COUNT(*) FROM hw_raw WHERE ts = ?", (NOW - 85,)).fetchone()[0] == 0
    c.close()
