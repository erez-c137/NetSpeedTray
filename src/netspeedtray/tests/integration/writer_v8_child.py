"""
Fixture + child entry points for ``test_writer_v8_kill.py``. Qt-free on purpose: the child runs with
``python -I`` and must import nothing but the writer.

``build(dir, now)`` (in the test process) writes a v8 file through the writer itself and returns the
manifest: exact byte totals and hardware sums. ``main(argv)`` is the killed child:

* ``maintain <dir> <now>`` - one ``run_maintenance`` pass (the kill point comes from the environment);
* ``flush <dir> <now>`` - start a session, flush once cleanly, then arm ``writer_flush_before_commit``
  and flush again (so the kill lands on the SECOND flush, after a committed heartbeat).
"""
import os
import random
import sqlite3
import sys
from pathlib import Path
from typing import Dict

from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db import writer_v8 as W

DB = "speed_history.db"


def _open(d: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(Path(d) / DB), timeout=10)
    c.execute("PRAGMA journal_mode = WAL")   # as the DatabaseWorker opens it
    return c


def build(d: Path, now: int, legacy_dest: bool = False) -> Dict[str, float]:
    """``legacy_dest``: also plant migrated-style (NULL-bytes) rows in the DESTINATION tiers, at buckets
    the roll-ups will land on - the merge path - and count their legacy estimates in the manifest."""
    c = sqlite3.connect(str(Path(d) / DB))
    S.create_v8_schema(c)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    rng = random.Random(6)
    w = W.WriterV8(c, version="kill-fixture", poll_ms=1000)
    w.start_session(now - 36 * 86400)
    up = down = 0
    hw = {"cpu": 0.0, "latency_anchor_timeout": 0.0}
    loss = (0.0, 0.25, 0.5, 1.0)
    for start, secs, step in ((now - 35 * 86400, 3 * 3600, 1), (now - 2 * 86400, 2 * 3600, 2),
                              (now - 3600, 3000, 5)):
        for i in range(0, secs, step):
            t = start + i
            deltas = {}
            for nm in ("Wi-Fi 3", "Ethernet"):
                u, dn = rng.randrange(0, 200_000), rng.randrange(0, 900_000)
                deltas[nm] = (u, dn)
                up, down = up + u, down + dn
            w.add_net_sample(W.NetSample(t, step * 1000, deltas))
            v = float(rng.randrange(0, 100))
            w.add_hw_sample(t, "cpu", v)
            hw["cpu"] += v
            lv = loss[i % 4]
            w.add_hw_sample(t, "latency_anchor_timeout", lv)
            hw["latency_anchor_timeout"] += lv
        assert w.flush(start + secs)
    if legacy_dest:
        iid = c.execute("SELECT iface_id FROM interfaces WHERE name = 'Wi-Fi 3'").fetchone()[0]
        cpu = c.execute("SELECT metric_id FROM metrics WHERE name = 'cpu' AND entity = ''").fetchone()[0]
        rows = []
        for start in (now - 35 * 86400, now - 2 * 86400):
            for j in range(10):          # minute buckets the raw rows also fall in
                rows.append((start // 60 * 60 + 60 * j, 500.0 + j, 20 + j))
        for ts, avg, n in rows:
            c.execute("INSERT INTO net_minute (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", (ts, iid, avg, avg, avg, avg, n))
            up, down = up + avg * min(n, 60), down + avg * min(n, 60)
        h = (now - 35 * 86400) // 3600 * 3600 + 3600   # an hour the 35-day-old minutes roll into
        c.execute("INSERT INTO net_hour (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                  "VALUES (?, ?, 50.0, 50.0, 50.0, 50.0, 100)", (h, iid))
        up, down = up + 50.0 * 100, down + 50.0 * 100
        for j in range(5):               # hardware minutes the raw cpu rows also fall in
            ts = (now - 2 * 86400) // 60 * 60 + 60 * j
            c.execute("INSERT INTO hw_minute (ts, metric_id, avg_value, max_value, sample_count) "
                      "VALUES (?, ?, 10.0, 10.0, 5)", (ts, cpu))
            hw["cpu"] += 50.0
        c.commit()
    c.close()
    return {"up": up, "down": down, **hw}


def main(argv) -> int:
    mode, d, now = argv[0], Path(argv[1]), int(argv[2])
    c = _open(d)
    w = W.WriterV8(c, version="kill-child", poll_ms=1000)
    if mode == "maintain":
        w.run_maintenance(now)
    elif mode == "flush":
        w.start_session(now - 100)
        w.add_net_sample(W.NetSample(now - 95, 1000, {"Wi-Fi 3": (111, 222)}))
        assert w.flush(now - 90)
        os.environ["NST_KILLPOINT"] = "writer_flush_before_commit"   # armed only now
        w.add_net_sample(W.NetSample(now - 85, 1000, {"Wi-Fi 3": (333, 444)}))
        w.add_hw_sample(now - 85, "cpu", 42.0)
        w.flush(now - 80)
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
