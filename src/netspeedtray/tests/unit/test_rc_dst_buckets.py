"""
History hour / day buckets across DST changes (RC review item 1).

The bucket SQL runs in SQLite with its 'localtime' - the process's C runtime time zone - so each case
runs in a subprocess with the TZ environment variable set. That makes the test meaningful on a UTC CI
machine too: "EST5EDT" carries the C runtime's built-in US rules (spring forward 2026-03-08, fall back
2026-11-01), "IST-5:30" is a half-hour zone, and "" keeps the machine's own zone (Jerusalem on the
owner's PC: 2026-03-27 / 2026-10-25).

Checked minute by minute through each transition: a bucket never starts after its row and the row is
inside it (0 <= ts - bucket < its length), hour buckets start on the local hour, day buckets on local
midnight, and the repeated fall-back hour stays two separate buckets.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[3])

_PROBE = textwrap.dedent(r"""
    import sqlite3, sys, time
    from datetime import datetime, timedelta
    sys.path.insert(0, sys.argv[1])
    from netspeedtray.core.db.history_reader import _bin_start, _local_bin_sql
    con = sqlite3.connect(":memory:")
    hour_sql, day_sql = _local_bin_sql("t.x", 3600), _local_bin_sql("t.x", 86400)
    def bucket(sql, ts):
        v = con.execute(f"SELECT {sql} FROM (SELECT ? AS x) t", (ts,)).fetchone()[0]
        return _bin_start(v, 86400 if sql is day_sql else 3600)
    # Every UTC-offset change in 2026 for this process's zone, found by scanning hourly.
    start = int(datetime(2026, 1, 1).timestamp())
    changes, prev = [], None
    for ts in range(start, start + 366 * 86400, 3600):
        off = time.localtime(ts).tm_gmtoff
        if prev is not None and off != prev:
            changes.append(ts)
        prev = off
    bad, checked, repeated = [], 0, set()
    windows = [(c - 36 * 3600, c + 36 * 3600) for c in changes] or [(start, start + 3 * 86400)]
    for a, b in windows:
        for ts in range(a, b, 60):
            local = datetime.fromtimestamp(ts)
            h, d = bucket(hour_sql, ts), bucket(day_sql, ts)
            checked += 1
            if not (0 <= ts - h < 3600) or datetime.fromtimestamp(h).replace(minute=0, second=0) != datetime.fromtimestamp(h):
                bad.append(("hour", local.isoformat(), ts - h))
            if not (0 <= ts - d < 25 * 3600) or datetime.fromtimestamp(d).time().isoformat() != "00:00:00" \
                    or datetime.fromtimestamp(d).date() != local.date():
                bad.append(("day", local.isoformat(), ts - d))
            if local.hour == 1 and (ts - h) < 3600:
                repeated.add(h)
    print(len(changes), checked, len(bad), len(repeated), repr(bad[:5]))
""")


@pytest.mark.parametrize("tz", ["EST5EDT", "IST-5:30", ""])
def test_hour_and_day_buckets_hold_across_dst(tmp_path, tz):
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    env = dict(os.environ)
    if tz:
        env["TZ"] = tz
    else:
        env.pop("TZ", None)
    out = subprocess.run([sys.executable, str(probe), SRC], env=env, capture_output=True, text=True,
                         timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    n_changes, checked, n_bad, n_repeat_hours, sample = out.stdout.split(" ", 4)
    assert int(checked) > 1000
    assert int(n_bad) == 0, f"TZ={tz!r}: {n_bad} rows outside their bucket: {sample}"
    if tz == "EST5EDT":
        assert int(n_changes) == 2   # the zone really changes offset twice in 2026 under this C runtime
        # Each 01:xx local hour in the windows is its own bucket - including BOTH 01:00 hours of the
        # fall-back night: 3 days x 2 transitions of 01:xx hours + 1 repeated.
        assert int(n_repeat_hours) >= 7


# The day-bucket SQL as #409 shipped it (two local-midnight conversions + up to three 'localtime' calls
# per row inside a CASE). Kept verbatim as the reference the one-call form must reproduce exactly.
def _legacy_day_sql(col: str) -> str:
    local = f"CAST(strftime('%s', {col}, 'unixepoch', 'localtime') AS INTEGER)"
    own = f"({col} - ({local} % 86400))"
    midnight = (f"CAST(strftime('%s', CAST({local} / 86400 AS INTEGER) * 86400, "
                f"'unixepoch', 'utc') AS INTEGER)")
    return f"(CASE WHEN {midnight} <= {col} THEN {midnight} ELSE {own} END)"


_SWEEP = textwrap.dedent(r"""
    import sqlite3, sys, time
    from datetime import datetime
    sys.path.insert(0, sys.argv[1])
    from netspeedtray.core.db.history_reader import _bin_start, _local_bin_sql
    legacy = sys.argv[2]
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (x INTEGER)")
    start = int(datetime(2026, 1, 1).timestamp()) - 86400
    end = int(datetime(2027, 1, 1).timestamp()) + 86400
    con.executemany("INSERT INTO t VALUES (?)", ((ts,) for ts in range(start, end, 600)))
    rows = con.execute(f"SELECT x, {legacy}, {_local_bin_sql('t.x', 86400)} FROM t ORDER BY x").fetchall()
    cache, diff = {}, []
    for x, old, day in rows:
        if day not in cache:
            cache[day] = _bin_start(day, 86400)
        if cache[day] != old:
            diff.append((datetime.fromtimestamp(x).isoformat(), old, cache[day]))
    # The grouped form the readers run: same buckets, same order, same members.
    g_old = con.execute(f"SELECT {legacy} AS b, COUNT(*), MIN(x), MAX(x) FROM t GROUP BY b ORDER BY b").fetchall()
    g_new = [(_bin_start(b, 86400), n, lo, hi) for b, n, lo, hi in con.execute(
        f"SELECT {_local_bin_sql('t.x', 86400)} AS b, COUNT(*), MIN(x), MAX(x) FROM t GROUP BY b ORDER BY b")]
    offsets = len({time.localtime(x).tm_gmtoff for x, _, _ in rows})
    print(len(rows), len(diff), int(g_old == g_new), len(g_new), offsets, repr(diff[:3]))
""")


@pytest.mark.parametrize("tz", ["", "EST5EDT", "IST-5:30", "PST8PDT"])
def test_day_buckets_match_the_legacy_form_row_for_row(tmp_path, tz):
    """The one-'localtime'-per-row day buckets (a local day number in SQL, its midnight computed once
    per output row in Python) give exactly the bucket the #409 CASE form gave: every 10 minutes of
    2026 (+ a day each side), row by row and as grouped buckets. "" is the machine's own zone -
    Jerusalem on the owner's PC."""
    probe = tmp_path / "sweep.py"
    probe.write_text(_SWEEP, encoding="utf-8")
    env = dict(os.environ)
    if tz:
        env["TZ"] = tz
    else:
        env.pop("TZ", None)
    out = subprocess.run([sys.executable, str(probe), SRC, _legacy_day_sql("t.x")], env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    n_rows, n_diff, grouped_same, n_days, n_offsets, sample = out.stdout.split(" ", 5)
    assert int(n_rows) > 52000
    assert int(n_diff) == 0, f"TZ={tz!r}: {n_diff} rows differ (local, legacy, new): {sample}"
    assert grouped_same == "1"
    assert 366 <= int(n_days) <= 368
    if tz in ("EST5EDT", "PST8PDT"):
        assert int(n_offsets) == 2   # the sweep really crosses both DST changes
