# The NetSpeedTray database

Your history lives in one SQLite file. It is yours — nothing is uploaded, and this document tells you
exactly what is in it so you can query it yourself.

```
%APPDATA%\NetSpeedTray\speed_history.db
```

Paste that into Explorer's address bar to find it. It opens in any SQLite tool
([DB Browser for SQLite](https://sqlitebrowser.org/) is the usual choice), or from Python with the
standard library and no dependencies.

> **Read it while NetSpeedTray is running, don't write to it.** The database is in WAL mode, so
> reading alongside the running app is safe. Writing to it underneath the app is not. From Python,
> open it read-only: `sqlite3.connect("file:speed_history.db?mode=ro", uri=True)`.

This file is the source of truth for the schema. If you change the schema, change this file in the
same commit. This describes **schema v8** (NetSpeedTray 2.2 and later). The design record is
`core/db/schema_v8.py`.

---

## What changed in v8 (2.2)

- **Bytes, not rates.** Every poll stores the exact number of bytes each adapter moved, and the
  interval it moved them over (`net_raw.dt_ms`). Volume is a `SUM`, exact, and the poll-rate setting
  can no longer skew it. Rates are derived: `bytes * 1000 / dt_ms`.
- **Adapter and metric names live once**, in `interfaces` and `metrics`; the history tables hold
  small integer ids. Your 2.1 history was rebuilt into this shape on the first 2.2 start.
- **When the app was running is recorded** (`app_sessions`), instead of being guessed from traffic.
- **Tiers never overlap**, and the boundary between them is written down (watermarks, below).
- **The old table names still work** as read-only views (`speed_history_raw` and friends), so a
  2.1.x NetSpeedTray, or a query you wrote for 2.1, can still read the history.

---

## The shape of it: three tiers that age

| Tier | Network | Hardware | Resolution | Holds | Then |
|---|---|---|---|---|---|
| raw | `net_raw` | `hw_raw` | each poll | the last **24 hours** | rolled into per-minute rows, originals deleted |
| minute | `net_minute` | `hw_minute` | per minute | the last **30 days** | rolled into per-hour rows, originals deleted |
| hour | `net_hour` | `hw_hour` | per hour | until your retention setting | deleted |

Retention is **Settings → Advanced → Keep data**, default 365 days. "Keep everything" sets it to
36,500 days, which is a hundred years and means in practice *never delete*. A *reduced* retention
takes effect 48 hours after you set it (so an accidental change can be undone); an increase applies at
once.

### Precedence and watermarks — the rule every query must follow

The tiers are **query-precedence layers, not additive**: exactly one tier answers each moment. Which
one is decided by four watermarks in `metadata` (integer Unix seconds as text):

| key | meaning |
|---|---|
| `tier_watermark_raw` | network: every `net_raw` row older than this has been rolled into `net_minute` |
| `tier_watermark_minute` | network: every `net_minute` row older than this has been rolled into `net_hour` |
| `hw_tier_watermark_raw` / `hw_tier_watermark_minute` | the same for the `hw_*` family |

With `W_r` = the raw watermark and `W_m` = the minute watermark of a family:

- `raw` answers **`ts >= W_r`**
- `minute` answers **`W_m <= ts < W_r`**
- `hour` answers **`ts < W_m`**

A watermark is written in the same transaction as the roll-up that moves the rows, so a reader that
sees the new rows sees the new watermark. If one is ever missing, the app falls back to "the finer
tier's oldest row inside its retention window" for that boundary only.

**The consequence that matters when querying:** a window longer than 24 hours spans more than one
tier. Read each tier only inside its own range, then add the parts. Summing every tier without the
watermarks can count the same minute twice — the first v8 design did exactly that and measured
**2.00×** the true data-cap total.

---

## Tables

### `interfaces` and `metrics` — the names

```sql
CREATE TABLE interfaces (
    iface_id    INTEGER PRIMARY KEY,
    stable_key  TEXT    NOT NULL UNIQUE,   -- today: the adapter's friendly name
    name        TEXT    NOT NULL,          -- display name, e.g. 'Wi-Fi 3', 'Ethernet', 'Tailscale'
    is_physical INTEGER NOT NULL DEFAULT 1,-- 0 = virtual (VPN, WSL, Hyper-V, loopback)
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    alias_of    INTEGER                    -- reserved; always NULL in 2.2
);
CREATE TABLE metrics (
    metric_id INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,               -- 'cpu', 'latency_anchor', 'errin', 'link_speed', ...
    entity    TEXT NOT NULL DEFAULT '',    -- '' = the one/aggregate series; 'iface:<iface_id>' = per adapter
    unit      TEXT NOT NULL DEFAULT '',
    UNIQUE (name, entity)
);
```

### `net_raw` — every poll, last 24 h

```sql
CREATE TABLE net_raw (
    ts         INTEGER NOT NULL,   -- END of the measured interval, Unix epoch seconds
    iface_id   INTEGER NOT NULL,
    up_bytes   INTEGER NOT NULL,   -- exact bytes sent in the interval
    down_bytes INTEGER NOT NULL,   -- exact bytes received
    dt_ms      INTEGER NOT NULL CHECK (dt_ms > 0),   -- how long the interval was
    flags      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts, iface_id)
) WITHOUT ROWID;
```

The bytes are the same exact counter deltas the data-usage odometer counts: before the widget's
display smoothing, a counter that went backwards counts 0, and one interval is capped at 100 Gbps so
a driver glitch cannot add phantom gigabytes. A poll in which an adapter moved nothing is not written
(the session record says the app was running). `flags`: `1` the counter went backwards in that
interval, `2` a gap (the interval was far longer than the poll rate - standby, a stall; the bytes are
real, a graph should not draw them as one spike). `4` and `8` are reserved (meant for "resumed
from sleep" and "first sample of a session"); nothing sets them yet, so a reader must not rely on them.

### `net_minute` / `net_hour` — the rollups

```sql
CREATE TABLE net_minute (            -- and net_hour, identically
    ts           INTEGER NOT NULL,   -- start of the bucket
    iface_id     INTEGER NOT NULL,
    up_bytes     INTEGER,            -- exact bytes in the bucket; NULL = recorded before 2.2
    down_bytes   INTEGER,
    up_avg       REAL NOT NULL,      -- time-weighted mean rate, bytes/s
    down_avg     REAL NOT NULL,
    up_max       REAL NOT NULL,      -- peak rate seen in the bucket, bytes/s
    down_max     REAL NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 1,  -- SECONDS the row stands for (see below)
    covered_ms   INTEGER,            -- ms the app was running in the bucket; NULL = unknown (pre-2.2)
    flags        INTEGER NOT NULL DEFAULT 0,  -- the OR of the rows it was built from
    PRIMARY KEY (ts, iface_id)
) WITHOUT ROWID;
```

**Units.** On the network tiers `sample_count` is the number of **seconds** the row stands for: the
measured time of the raw rows it absorbed, capped at the bucket (60 / 3600). For a row from before
2.2 it is the old per-second sample count, which is the same unit. `*_avg` is a rate over those
seconds.

**Volume of a rollup row:** `up_bytes` / `down_bytes` when they are not NULL (exact). When they are
NULL the row was recorded before 2.2, when only rates were stored; the app then estimates
`avg × MIN(sample_count, bucket_seconds)` and labels the result *recorded history* rather than an
exact count. The data-usage odometer (`usage_counter`) stays the authority for "how much did I use".

### `hw_raw` / `hw_minute` / `hw_hour` — hardware, latency, link health

```sql
CREATE TABLE hw_raw (
    ts        INTEGER NOT NULL,
    metric_id INTEGER NOT NULL,
    value     REAL    NOT NULL,
    PRIMARY KEY (ts, metric_id)
) WITHOUT ROWID;
-- hw_minute / hw_hour: avg_value, min_value (NULL before 2.2), max_value, sample_count
-- (here sample_count IS a sample count, not seconds)
```

Metrics recorded, by `metrics.name` (`entity = ''` unless noted):

| name | Unit | Notes |
|---|---|---|
| `cpu`, `gpu`, `ram` | percent | 0–100 |
| `cpu_temp`, `gpu_temp` | °C | `cpu_temp` needs LibreHardwareMonitor on most systems |
| `cpu_power`, `gpu_power`, `total_power`, `system_power` | watts | |
| `latency_gw` | milliseconds | ping to your router — your LAN. Median of the cycle's burst of 4 pings |
| `latency_anchor` | milliseconds | ping to a public anchor — true internet latency. Median of its burst of 4 |
| `latency_gw_timeout` | 0 to 1 | The share of that cycle's ping burst that was lost: 0, 0.25, 0.5, 0.75 or 1. Rows from before 2.2 are 0 or 1 for a single ping - the same meaning, so both shapes mix without conversion. **Averaged in the rollups, so in `hw_minute`/`hw_hour` this is a packet-loss *rate* between 0 and 1.** |
| `latency_anchor_timeout` | 0 to 1 | The same for the public anchor - *internet* loss. Recorded only while the public probe is on. When the router loses its whole burst, the anchor is not pinged that cycle and is recorded as fully lost, so internet loss is always at least router loss. |
| `errin`, `errout`, `dropin`, `dropout` | count | entity `iface:<id>`: packets the adapter reported as errored / dropped **in that interval** (only non-zero intervals are written; absent while the app ran means 0) |
| `link_speed` | Mbps | entity `iface:<id>`: negotiated link speed, on first sight and on change (at most once a minute) |
| `link_duplex` | 0/1/2 | entity `iface:<id>`: unknown / half / full, like `link_speed` |

The probe runs every 3 seconds: a burst of 4 pings, 100 ms apart, to your router - about 1.3 packets
a second of ~80 bytes - and the same to the public anchor only while that probe is on.

Because this table is generic, **adding a new metric needs no schema change**.

The hardware rows are written only for metrics you monitor in Settings → Hardware, and only while
**Settings → Advanced → Record hardware history** is on. The open Monitor window reads every metric
live, but does not record the ones you are not monitoring. The `latency_*` rows follow the Connection
settings instead.

### `app_sessions` and `link_events`

```sql
CREATE TABLE app_sessions (
    session_id   INTEGER PRIMARY KEY,
    started_ts   INTEGER NOT NULL,
    last_seen_ts INTEGER NOT NULL,   -- heartbeat, updated every 10 s
    ended_ts     INTEGER,            -- NULL while running, or after a crash / forced kill
    version      TEXT    NOT NULL,   -- the NetSpeedTray version that ran
    poll_ms      INTEGER NOT NULL,
    clean_exit   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE link_events (
    iface_id INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    event    INTEGER NOT NULL,       -- 0 = link went down, 1 = up
    PRIMARY KEY (iface_id, ts)
) WITHOUT ROWID;
```

A session runs from `started_ts` to `ended_ts`, or to its last heartbeat when it never ended
cleanly. Time outside every session, after the first one, is time NetSpeedTray **was not running** -
the reason a day can show less traffic than your router saw. Nothing before 2.2 has a session.

### `usage_counter` — the data-cap odometer

One row, always `id = 1`, unchanged since v7. Separate from the history tables because a data cap must
survive retention pruning.

```sql
CREATE TABLE usage_counter (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    cumulative_up   REAL NOT NULL DEFAULT 0,   -- lifetime bytes, monotonic
    cumulative_down REAL NOT NULL DEFAULT 0,
    anchor_up       REAL NOT NULL DEFAULT 0,   -- cumulative at the start of this billing period
    anchor_down     REAL NOT NULL DEFAULT 0,
    period_key      TEXT NOT NULL DEFAULT '',  -- the billing period this anchor belongs to
    updated_ts      INTEGER NOT NULL DEFAULT 0
);
```

Usage this period is `cumulative - anchor`. The anchor only ever advances on a genuine **forward**
period rollover, so a clock change or DST shift cannot wipe your running total.

The odometer counts every poll's exact counter delta, including across long gaps such as standby
(only the speed display re-primes), capped per direction at 100 Gbps x the time elapsed so one
counter glitch cannot add phantom gigabytes. Each write also stores, in `metadata`, the network
counters that total was counted up to (`usage_counter_snapshot`, below); a later launch **in the
same boot** adds what moved while the app was closed. After a reboot, or if an adapter's counters
went backwards, nothing is added; in auto mode, nothing is added either when the primary interface
is not the one the snapshot was metered on.

Bytes are charged to the period in which their measurement window **began**: a standby gap or a
closed-app window that spans the reset day counts toward the period it started in (the lifetime
`cumulative` and the `anchor` both advance), never all toward the new one.

### `metadata` — bookkeeping

`key`/`value` text pairs: `db_version` (8), `created_at`, the four tier watermarks above,
`current_retention_days`, `last_maintenance_at`, the pending-retention keys used to schedule a prune,
and `pending_history_compaction` while a "Delete all history" still owes a WAL checkpoint (`1`) or a
checkpoint plus removing a backup that was locked (`2` - only a `2` ever deletes a backup at startup;
2.1.x writes `1`, and the upgrade carries it over). `migrated_from_v7_at` / `migrated_from_v7_backup`
record a completed v7 -> v8 upgrade and the backup it left; `backup_clock_continuous_since` arms
backup age-out (see [Restoring a backup](#restoring-a-backup)). `usage_counter_snapshot` holds the NIC counters,
by adapter name, that `usage_counter`'s total was counted up to (same-boot launch reconciliation).
Three keys guard the live history against a wrong wall clock (maintenance rolls up and prunes by
the clock): `clock_trusted_ts` is the time of the last maintenance pass on a clock found continuous
with the one before it; `clock_continuous_since` is when the current run of such passes began
(the prune waits 7 days after it); `clock_candidate` (`<first>,<last>` epoch seconds) is a clock
that did not follow on - a jump, a large correction or a long time off - while passes confirm it:
nothing is rolled up or pruned on it until it has held for 6 hours. Absent keys are normal on a
file this build has not maintained yet.
The first stamp a file gets (every file from a 2.2 beta, every fresh v7 -> v8 upgrade) starts
`clock_continuous_since`, so its first retention prune runs 7 days later; roll-ups are not delayed.
`usage_rekey_memory` (JSON) remembers, for the data-usage counter, the periods it left on a
rollover or a reset-day change and their anchors, so a reset day changed back (or a clock
correction) restores the exact count; it is written with `usage_counter` and deleted with it.
After "Delete all history" with the usage kept, a reset-day change to a later start can
over-count by the traffic between the old start and the delete until the next rollover:
history no longer covers it, and the counter keeps no per-day record.
Every key a 2.1 database had is carried over unchanged by the upgrade.

`usage_counter_snapshot` is JSON, written in the same transaction as the `usage_counter` row:
`{"v": 1, "boot_time": <psutil.boot_time()>, "ts": <epoch s the counters were read>,
"counters": {"<adapter name>": [bytes_sent, bytes_recv], ...}, "primary": "<adapter name>"}`
(`primary` only in auto mode). Two boot times within 30 s are the same boot session. If counters were
read but no snapshot could be written (boot time unreadable), the key is deleted in the same
transaction, so a stale snapshot can never be re-added against a newer total.

### The v7-named views — read-only compatibility

`speed_history_raw`, `speed_history_minute`, `speed_history_hour`, `hardware_stats_raw`,
`hardware_stats_minute` and `hardware_stats_hour` are **views** over the v8 tables with the 2.1
column names (`timestamp`, `interface_name`, `upload_bytes_sec`, `upload_avg`, `stat_type`, ...).
They exist so a rolled-back 2.1.x, and queries written for 2.1, keep reading. They cannot be written
to, they show only `entity = ''` metrics, and they do **not** apply the watermarks - for exact volume
use the v8 tables. `bandwidth_history` (created by every 2.1 install, never written) is gone.

---

## Things that will bite you

**Bytes per second, not bits.** Multiply by 8 for Mbps: `bytes_per_sec * 8 / 1e6`.

**There is no "All interfaces" row.** Every row is one real adapter. To aggregate, `SUM` across
interfaces — and note that includes virtual adapters (WSL, Hyper-V, VPN clients like Tailscale);
`interfaces.is_physical = 0` marks them. A typical machine has one adapter carrying ~98% of traffic.

**Percentiles only within 24 hours.** The rollups keep avg / max (and, since 2.2, min for hardware),
so percentiles cannot be computed honestly beyond the raw tier. NetSpeedTray refuses to guess them -
if you see percentiles marked unavailable in the Statistics sheet, this is why.

**`avg` and `max` answer different questions.** The graph plots `max` at minute and hour resolution,
so a long-range line is a *peak envelope*, not a rate trace. Totals use bytes.

**Timestamps are Unix epoch seconds**, interpreted in local time by the app
(`datetime.fromtimestamp`). In `net_raw` they are the END of the measured interval; in the rollups the
bucket START.

**The live "Session" graph and the stored history can differ by one burst.** The Monitor's Session
view for *all* interfaces draws the in-memory series the widget shows - smoothed for display (an
isolated poll above 5× the recent baseline is drawn at 2×) and aggregated by your interface mode.
Scope the Session view to *one* adapter, or pick any longer range, and it reads this file, which stores
the measured bytes. They agree everywhere except on such a smoothed second.

---

## Recipes

Read-only, safe to run while the app is open. The multi-tier recipes read the watermarks themselves;
a missing one counts as 0 (a new install before its first maintenance pass keeps everything in the
finest tier).

**Exact daily volume per adapter, last 30 days** (minute + raw tiers, each in its own range)

```sql
WITH wr AS (SELECT COALESCE((SELECT CAST(value AS INTEGER) FROM metadata WHERE key = 'tier_watermark_raw'), 0) AS v),
     wm AS (SELECT COALESCE((SELECT CAST(value AS INTEGER) FROM metadata WHERE key = 'tier_watermark_minute'), 0) AS v),
     parts AS (
       SELECT ts, iface_id, up_bytes, down_bytes FROM net_raw, wr WHERE ts >= wr.v
       UNION ALL
       SELECT ts, iface_id,
              COALESCE(up_bytes,   up_avg   * MIN(sample_count, 60)),   -- estimate for pre-2.2 rows
              COALESCE(down_bytes, down_avg * MIN(sample_count, 60))
       FROM net_minute, wr, wm WHERE ts >= wm.v AND ts < wr.v)
SELECT date(p.ts, 'unixepoch', 'localtime') AS day, i.name AS adapter,
       ROUND(SUM(p.down_bytes) / 1e9, 2) AS down_gb, ROUND(SUM(p.up_bytes) / 1e9, 2) AS up_gb
FROM parts p LEFT JOIN interfaces i ON i.iface_id = p.iface_id
WHERE p.ts >= strftime('%s', 'now', '-30 days')
GROUP BY day, adapter
ORDER BY day DESC, down_gb DESC;
```

**When was NetSpeedTray not running, this week**

```sql
SELECT datetime(started_ts, 'unixepoch', 'localtime') AS started,
       datetime(COALESCE(ended_ts, last_seen_ts), 'unixepoch', 'localtime') AS ended,
       CASE WHEN clean_exit THEN 'closed' ELSE 'crashed / killed / still running' END AS how,
       version
FROM app_sessions
WHERE COALESCE(ended_ts, last_seen_ts) > strftime('%s', 'now', '-7 days')
ORDER BY started_ts;
```

**Your busiest hours** (most data moved, all tiers, every adapter)

```sql
WITH wr AS (SELECT COALESCE((SELECT CAST(value AS INTEGER) FROM metadata WHERE key = 'tier_watermark_raw'), 0) AS v),
     wm AS (SELECT COALESCE((SELECT CAST(value AS INTEGER) FROM metadata WHERE key = 'tier_watermark_minute'), 0) AS v),
     parts AS (
       SELECT ts, up_bytes, down_bytes, down_bytes * 1000.0 / dt_ms AS down_max FROM net_raw, wr WHERE ts >= wr.v
       UNION ALL
       SELECT ts, COALESCE(up_bytes,   up_avg   * MIN(sample_count, 60)),
                  COALESCE(down_bytes, down_avg * MIN(sample_count, 60)), down_max
       FROM net_minute, wr, wm WHERE ts >= wm.v AND ts < wr.v
       UNION ALL
       SELECT ts, COALESCE(up_bytes,   up_avg   * MIN(sample_count, 3600)),
                  COALESCE(down_bytes, down_avg * MIN(sample_count, 3600)), down_max
       FROM net_hour, wm WHERE ts < wm.v)
SELECT datetime(ts / 3600 * 3600, 'unixepoch', 'localtime') AS hour,
       ROUND(SUM(down_bytes + up_bytes) / 1e9, 2) AS total_gb,
       ROUND(SUM(down_bytes) / 1e9, 2) AS down_gb,
       ROUND(MAX(down_max) * 8 / 1e6, 1) AS peak_down_mbps   -- one adapter's fastest moment
FROM parts
GROUP BY ts / 3600
ORDER BY total_gb DESC
LIMIT 10;
```

"Busiest" here means volume. Sort by `peak_down_mbps` instead for the hours with the fastest burst;
the two lists rarely match (a ten-second speed test peaks high but moves little).

**Internet latency and packet loss over the last week**

```sql
WITH m AS (SELECT metric_id, name FROM metrics
           WHERE entity = '' AND name IN ('latency_anchor', 'latency_gw_timeout', 'latency_anchor_timeout')),
     w AS (SELECT COALESCE((SELECT CAST(value AS INTEGER) FROM metadata WHERE key = 'hw_tier_watermark_raw'), 0) AS v),
     samples AS (                        -- the last week spans two tiers: raw (24 h) + minute
       SELECT h.ts, m.name, h.value AS v, 1 AS n FROM hw_raw h JOIN m USING (metric_id), w WHERE h.ts >= w.v
       UNION ALL
       SELECT h.ts, m.name, h.avg_value, h.sample_count FROM hw_minute h JOIN m USING (metric_id), w
       WHERE h.ts < w.v)
SELECT datetime(ts / 3600 * 3600, 'unixepoch', 'localtime') AS hour,
       ROUND(SUM(CASE WHEN name='latency_anchor' THEN v * n END)
           / SUM(CASE WHEN name='latency_anchor' THEN n END), 1) AS internet_ms,
       ROUND(100.0 * SUM(CASE WHEN name='latency_gw_timeout' THEN v * n END)
           / SUM(CASE WHEN name='latency_gw_timeout' THEN n END), 1) AS router_loss_pct,
       ROUND(100.0 * SUM(CASE WHEN name='latency_anchor_timeout' THEN v * n END)
           / SUM(CASE WHEN name='latency_anchor_timeout' THEN n END), 1) AS internet_loss_pct
FROM samples
WHERE ts > strftime('%s','now','-7 days')
GROUP BY ts / 3600
ORDER BY ts / 3600;
```

Each minute row is weighted by its `sample_count`, so a minute the app was only partly running
counts for what it measured. The loss columns average per probe *cycle*, so they read the same across
the 2.2 change from one ping per cycle (0/1 rows) to a burst of four (quarter steps).
`internet_ms` and `internet_loss_pct` stay empty unless **Also measure true internet latency** is on.
Read the two loss columns together: router loss is your Wi-Fi or LAN; internet loss *above* router
loss is beyond your router (ISP or path).

**Adapter errors and drops, last 30 days** (a failing cable or driver shows up here)

```sql
SELECT i.name AS adapter, m.name AS counter, SUM(t.v) AS packets
FROM (SELECT metric_id, value AS v, ts FROM hw_raw
      UNION ALL SELECT metric_id, avg_value * sample_count, ts FROM hw_minute) t
JOIN metrics m ON m.metric_id = t.metric_id
LEFT JOIN interfaces i ON 'iface:' || i.iface_id = m.entity
WHERE m.name IN ('errin', 'errout', 'dropin', 'dropout') AND t.ts > strftime('%s','now','-30 days')
GROUP BY adapter, counter ORDER BY packets DESC;
```

**Percentiles — but only over the last 24 hours**, where per-poll data still exists:

```sql
WITH r AS (SELECT n.down_bytes * 8.0 / n.dt_ms / 1000.0 AS mbps
           FROM net_raw n JOIN interfaces i ON i.iface_id = n.iface_id
           WHERE i.name = 'Wi-Fi 3')
SELECT ROUND(mbps, 1) FROM r ORDER BY mbps
LIMIT 1 OFFSET (SELECT COUNT(*) * 95 / 100 FROM r);
```

---

## Schema versioning

`metadata.db_version` tracks the schema; the current version is **8**.

| Version | Added |
|---|---|
| 3 | covering indexes, `metadata` table |
| 4 | `sample_count` on the rollup tables |
| 5 | hardware statistics tables |
| 6 | `hardware_stats_hour` |
| 7 | `usage_counter` (the data-cap odometer) |
| 8 | the rebuild above (2.2): bytes at capture, dimension tables, `app_sessions`, `link_events`, watermarked tiers, v7-named views |

**The upgrade to v8 is a rebuild, not an in-place change.** On the first 2.2 start, the v8 file is
built next to the old one as `speed_history.db.v8tmp`, checked row-for-row against it, and only then
swapped in by two renames; the 2.1 file is kept, unchanged, as the backup (below). A file older than
v7 is first brought to v7 in place, with a verified backup, as 2.1 did. If the upgrade cannot finish
- not enough free disk (it needs 1.5× the file), another program holding the file, a check that does
not match - nothing is changed: NetSpeedTray shows your history read-only, records nothing, says so,
and tries again at the next start. A kill at any moment (a crash, Task Manager, or the installer
force-closing an app that didn't close when asked) is finished or undone at the next start from `migrate_v8.journal`. What the upgrade carries over and
what it cannot:

- the last 24 hours of per-second rows become per-minute rows (2.1 never stored bytes or intervals,
  so there was nothing exact to keep at second resolution; nothing is invented);
- every pre-2.2 rollup keeps its rates; its bytes stay empty (NULL) and are estimated when needed;
- hardware, latency and loss rows, `usage_counter` and every `metadata` key are copied unchanged.

**If you add a table or column:** bump `_DB_VERSION` in `core/database.py`, write the migration, and
update this file. Old databases must keep working — a migration that assumes a column exists will
break every existing install.

---

## Restoring a backup

The upgrade to v8 keeps your 2.1 database, unchanged, next to the new one:

```
%APPDATA%\NetSpeedTray\speed_history.db.bak.v7_20261020_141503
```

`v7` is the schema version the backup holds; the rest is when the upgrade ran. It is the very file
2.1 was writing, renamed - not a copy - so it cannot be an incomplete one. Older migrations (v2-v6 to
v7) write a verified copy with the same naming. NetSpeedTray keeps the newest two backups plus the
newest one of every schema version, so the v7 backup is never pushed out by later ones.

A backup holds history too, so it follows the same rules as the live file:

- **Only the app's own names.** Matched exactly (ignoring case, as NTFS does), each with an optional `-wal` /
  `-shm` / `-journal` suffix: `speed_history.db.bak.v<N>_<YYYYMMDD>_<HHMMSS>[_<n>]`, the legacy bare
  `speed_history.db.bak`, and `speed_history.db.stray_<YYYYMMDD>_<HHMMSS>[_<n>]`. Nothing else in the
  folder is ever deleted or written, and a directory never is. The same matcher decides what the
  older-version upgrades prune and which file the downgrade notice names as the one to restore.
- **It ages out with "Keep history for".** The hourly maintenance deletes a backup once it is older
  than the retention period, but never sooner than 30 days after it was made (`BACKUP_MIN_KEEP_DAYS`),
  so a rollback to 2.1.x always has a month. "Keep everything" keeps backups forever. Its age comes
  from the LATER of the date in its name and the file's modified time, and a file dated in the future
  is kept. Three guards, because a wrong deletion cannot be undone:
  - **The 30 days are counted in time the app observed.** Each maintenance pass credits at most 2 h
    (`BACKUP_STEADY_CREDIT_MAX_SEC`) of steady time to every backup present (`backup_seen_s:<name>` in
    `metadata`); app-off time and clock jumps add nothing. A backup goes only when BOTH its dates say
    it is older than the retention AND it has been observed for 30 days - so a clock corrected forward,
    or a jump small enough to pass for app-off time, can never shorten the floor.
  - **The clock must look right.** Age-out only runs once every maintenance pass for the last 7 days
    followed the previous one within 14 days, in order (`backup_clock_continuous_since` in
    `metadata`; `BACKUP_CLOCK_*`). Within a session the wall clock is also checked against the
    monotonic clock between passes: a disagreement over 5 minutes (or 1%) is a jump too. A clock set to 2030 would otherwise make a day-old backup look
    years old - on the same pass that prunes the live rows, leaving no copy at all.
  - **A pre-v8 file needs a finished upgrade.** A `.bak.v<N>` with N < 8, the bare `.bak` and a
    stray are only aged out when the live file records a completed v7 -> v8 migration
    (`migrated_from_v7_at`, written on success; files migrated by 2.2.0-beta.1 do not have it, so
    their backup is kept until "Delete all history"). A fresh v8 file next to a v7 backup - an upgrade
    whose journal was deleted by hand - means that backup is the only full history.
- **"Delete all history" deletes every backup**, `-wal`/`-shm` sidecars included (each file is
  overwritten with zeros first), so after a delete there is nothing to restore. The same goes for
  `speed_history.db.stray_<date>` files - whatever an interrupted upgrade's recovery found under the
  live name and moved aside rather than delete (it can be a whole 2.1-recorded history file); they
  also age out by the rule above. A symbolic link, junction or hard-linked file with one of these
  names is only unlinked - whatever it points at is never written; if such a link is read-only it
  cannot be unlinked without changing the file it shares, so it is left in place for good
  (`backups_unremovable` in `metadata`, never retried) and the result says so. A read-only file that
  is not linked is made writable first. A backup another program holds open is retried with backoff (5 s doubling to 1 h, at most 8
  times a session, logged once as a warning) without being re-zeroed, while the WAL checkpoint is
  retried on its own; whatever is still left goes at the next start. Nothing is removed while
  `migrate_v8.journal` exists - an unfinished upgrade still needs its backup; the result says it is
  kept until the upgrade has finished, and it is removed at a later start. A failure while handing
  the free pages back AFTER the delete has committed is still a done delete (retried while idle).

You need it if you go back to NetSpeedTray 2.1.x after 2.2 upgraded your database. (Going back
without restoring is safe too - see the next section - it just doesn't record.) **With NetSpeedTray
fully closed**, in `%APPDATA%\NetSpeedTray`:

1. Delete **all three** of these files — not just the first one:
   - `speed_history.db`
   - `speed_history.db-wal`
   - `speed_history.db-shm`
2. Copy the `.bak.v7_...` file and rename the copy to `speed_history.db`. Use the file whose name
   ends in the date and time, never one ending in `-wal` or `-shm`: opening a backup in any SQLite
   tool leaves those beside it, and NetSpeedTray 2.1.5-2.1.7 can name one of them in its log by
   mistake.
3. Start NetSpeedTray 2.1.x.

**Step 1 is not optional, and the `-wal`/`-shm` files are the dangerous part.** They are the
write-ahead log of the database you are replacing. Leave them behind and SQLite silently replays
them into the restored file on the next launch — the result *passes* an integrity check but has the
wrong schema inside, and the history in it is unrecoverable. Don't be reassured by their absence
either: a clean shutdown removes them, but a force-killed app leaves them hot. The installer asks
the app to close cleanly and only force-closes it if that fails - but installing 2.1.x over 2.2
always force-closes it, because 2.1.x installers can't send that request (and upgrading *from*
2.1.7 force-closes 2.1.7 once, for the same reason).

Copying in step 2, rather than renaming the `.bak` itself, keeps the backup intact in case anything
goes wrong. The restored file holds your history **up to the moment of the upgrade**; anything 2.2
recorded since is only in the v8 file.

### Going back to 2.1.x without restoring

A 2.1.5-2.1.7 NetSpeedTray that finds a v8 file shows a "History recording is paused" notice, keeps
**showing** your history through the v7-named views, and writes nothing. 2.1.4 refuses to write too,
but says so only in its log.

**Restore the backup BEFORE running 2.1.3 or older.** Those versions have no downgrade guard: on a v8
file they make a full copy of the database (`.bak.v8_*`) on every launch and never delete one, log an
error every 10 seconds (they try to write into the read-only views), and their data-usage counter
writes go into the v8 file.

**A 2.1.x launch can change the v8 file's bytes without writing to it.** If 2.2 was killed rather
than closed (Task Manager, a crash, or an installer force-closing it - which a 2.1.x installer
always does), its last writes are still in
`speed_history.db-wal`. The first 2.1.x to open the file checkpoints those frames - 2.2's own data -
into `speed_history.db`. The file's hash and timestamp change, but it is still v8, passes
`PRAGMA integrity_check`, and holds nothing written by 2.1.x. Harmless; it only matters if you are
comparing hashes to prove the file was untouched.

Two things look different in 2.1.4-2.1.7, and neither is damage:

- **The last 24 hours before the upgrade show empty in 2.1.x's per-second views.** The upgrade folded
  them into minute rows; 2.1.x reads its last day from the per-second tier.
- **2.1.x's history totals read high - often by a lot.** On a real 80 MB database, a rolled-back
  2.1.7 showed 30-day totals **+47% upload / +26% download** above what 2.2 shows for the same rows;
  per adapter it ranged from about +29% (the main Wi-Fi) to **~20×** (sparse virtual adapters). Why:
  2.1.x turns each minute row into bytes as `average rate × 60 s`, but a raw row is only stored for a
  second the adapter actually moved data, so the average is over the *active* seconds and a minute
  that was busy for 5 seconds is counted as busy for 60. The quieter and burstier the adapter, the
  bigger the error. This is how 2.1.x has always counted - on its own v7 file too - so restoring the
  backup does **not** remove it. The upgrade's only contribution is the last day before it, folded
  from per-second rows into minute rows that 2.1.x then reads the same way.
  **The data-usage odometer (`usage_counter`, the data-cap figure) is unaffected**: it is a running
  byte counter, not rebuilt from history, and reads the same in both versions.

**Expect 2.2's history totals to be LOWER than 2.1.x showed for the same period.** Nothing was lost:
2.1.x over-counted. 2.2 counts exact bytes for every row it records (`up_bytes` / `down_bytes`), and
for rows recorded before 2.2 it estimates `average × MIN(sample_count, 60)` - the seconds the row
really stands for - instead of a flat 60, and labels the result *recorded history*.

---

## Maintenance

A background pass runs at start and then hourly: it rolls each tier into the next (raw older than
24 h into minutes, minutes older than 30 days into hours), writes the watermarks in the same
transaction, deletes rows past your retention setting (hour tiers, link events, sessions), and hands
up to ~8 MB of free pages back to Windows (`PRAGMA incremental_vacuum` - the file is
`auto_vacuum = INCREMENTAL`, so there is no full-file `VACUUM` any more and no daily rewrite). It then
truncates the write-ahead log.

**Settings → Advanced → Delete all history** empties every history table - all network and hardware
tiers, latency included, the session and link records, and the adapter / metric names (adapter names
are identifying) - with `secure_delete` on, so the freed pages are overwritten with zeros, gives the
free space back, and runs `wal_checkpoint(TRUNCATE)` so the old pages are gone from
`speed_history.db-wal` too. If a reader is mid-query (the Monitor's graphs) the checkpoint reports
busy; the app then says the file is still being cleaned up and retries every few seconds while idle
until it completes. Every backup and moved-aside file - `speed_history.db.bak.v<N>_<YYYYMMDD>_<HHMMSS>[_<n>]`,
the bare `speed_history.db.bak` and `speed_history.db.stray_<YYYYMMDD>_<HHMMSS>[_<n>]`, each with its
`-wal`/`-shm`/`-journal` - is then removed (see
[Restoring a backup](#restoring-a-backup)). `metadata` is kept, and so is `usage_counter` unless you
tick "Also reset this period's data usage" in the confirmation - which, in the same transaction,
also deletes `usage_counter_snapshot`, so the next launch reconciles nothing from before the reset.
The app resets its in-memory counter (and re-arms the data-cap alerts) only once the delete has
reported success; a failed delete leaves the counter exactly as it was. Recording carries on into
the emptied tables.

---

## What is deliberately *not* stored

NetSpeedTray does not record what it cannot measure honestly:

- **Per-application bytes.** Windows does not attribute network bytes per process without a kernel
  driver. The Monitor shows per-app *connections*, which is real, rather than per-app speeds, which
  would be a guess.
- **Which traffic was LAN and which was internet.** Adapter byte counters carry no addresses, ports
  or routes — the information simply is not there to filter on.
- **Wi-Fi network names, MAC addresses, IP addresses.** Adapter friendly names are the only
  identifying text in the file (in `interfaces`, and in `usage_counter_snapshot`), and "Delete all
  history" removes them - the snapshot's copy only when you also reset the data-usage counter.
- **Anything that leaves your machine.** No telemetry, no accounts, no uploads. The latency probe
  pings your own gateway by default (about 1.3 small packets a second); pinging a public host is
  opt-in and you name the host.
