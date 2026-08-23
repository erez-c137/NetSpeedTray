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
> reading alongside the running app is safe. Writing to it underneath the app is not.

This file is the source of truth for the schema. If you change the schema, change this file in the
same commit.

---

## The shape of it: three tiers that age

Every second, NetSpeedTray records how many bytes crossed each network adapter. Keeping that forever
would mean ~31 million rows per adapter per year, so older data is rolled up:

| Tier | Resolution | Holds | Then |
|---|---|---|---|
| `speed_history_raw` | per second | the last **24 hours** | averaged into per-minute rows, originals deleted |
| `speed_history_minute` | per minute | the last **30 days** | averaged into per-hour rows, originals deleted |
| `speed_history_hour` | per hour | until your retention setting | deleted |

Retention is **Settings → Advanced → Keep data**, default 365 days. "Keep everything" sets it to
36,500 days, which is a hundred years and means in practice *never delete*.

Hardware statistics use the identical three-tier model in `hardware_stats_raw` / `_minute` / `_hour`.

**The consequence that matters when querying:** a window longer than 24 hours spans more than one
tier, and each tier stores different columns. A query that reads only one table silently misses data.

---

## Tables

### `speed_history_raw` — per-second, last 24h

```sql
CREATE TABLE speed_history_raw (
    timestamp          INTEGER NOT NULL,   -- Unix epoch seconds
    interface_name     TEXT    NOT NULL,   -- e.g. 'Wi-Fi 3', 'Ethernet', 'Tailscale'
    upload_bytes_sec   REAL    NOT NULL,   -- BYTES per second, not bits
    download_bytes_sec REAL    NOT NULL,
    PRIMARY KEY (timestamp, interface_name)
);
```

### `speed_history_minute` / `speed_history_hour` — the rollups

```sql
CREATE TABLE speed_history_minute (      -- and _hour, identically
    timestamp      INTEGER NOT NULL,     -- start of the bucket
    interface_name TEXT    NOT NULL,
    upload_avg     REAL    NOT NULL,     -- mean bytes/sec across the bucket
    download_avg   REAL    NOT NULL,
    upload_max     REAL    NOT NULL,     -- peak bytes/sec seen in the bucket
    download_max   REAL    NOT NULL,
    sample_count   INTEGER NOT NULL DEFAULT 1,   -- how many samples went in
    PRIMARY KEY (timestamp, interface_name)
);
```

`sample_count` is how you tell a full bucket from a partial one — 60 samples in a minute bucket means
NetSpeedTray was running the whole minute; 12 means it was not.

### `hardware_stats_raw` / `_minute` / `_hour`

A generic key/value time series. One row per metric per timestamp.

```sql
CREATE TABLE hardware_stats_raw (
    timestamp INTEGER NOT NULL,
    stat_type TEXT    NOT NULL,
    value     REAL    NOT NULL,
    PRIMARY KEY (timestamp, stat_type)
);
-- _minute and _hour replace `value` with avg_value, max_value, sample_count
```

`stat_type` values currently recorded, and their units:

| `stat_type` | Unit | Notes |
|---|---|---|
| `cpu`, `gpu`, `ram` | percent | 0–100 |
| `cpu_temp` | °C | needs LibreHardwareMonitor on most systems |
| `cpu_power`, `gpu_power`, `total_power` | watts | |
| `latency_gw` | milliseconds | ping to your router — your LAN |
| `latency_anchor` | milliseconds | ping to a public anchor — true internet latency |
| `latency_gw_timeout` | 0 or 1 | 1 = that probe timed out. **Averaged in the rollups, so in `_minute`/`_hour` this is a packet-loss *rate* between 0 and 1.** |

Because this table is generic, **adding a new metric needs no schema change** — a new `stat_type`
flows through aggregation and retention automatically.

### `usage_counter` — the data-cap odometer

One row, always `id = 1`. Separate from the history tables because a data cap must survive retention
pruning.

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

### `metadata` — bookkeeping

`key`/`value` text pairs: `db_version`, `created_at`, `current_retention_days`,
`last_maintenance_at`, `last_vacuum_at`, and the pending-retention keys used to schedule a prune.

### `bandwidth_history` — **not used**

```sql
CREATE TABLE bandwidth_history (
    interface_name       TEXT PRIMARY KEY,
    total_upload_bytes   REAL NOT NULL DEFAULT 0,
    total_download_bytes REAL NOT NULL DEFAULT 0
);
```

Created and indexed by every install, and **never written to**. Do not build on it without wiring it
up first. It is documented here so nobody mistakes it for a working lifetime odometer — that is
`usage_counter`.

---

## Things that will bite you

**Speeds are bytes per second, not bits.** Multiply by 8 for Mbps: `bytes_sec * 8 / 1e6`.

**There is no "All interfaces" row.** Every row is one real adapter. To aggregate, `SUM` across
interfaces — and note that includes virtual adapters (WSL, Hyper-V, VPN clients like Tailscale). A
typical machine has one adapter carrying ~98% of traffic and several carrying almost none.

**There is no `min` column.** Only `avg` and `max` survive rollup, so percentiles cannot be computed
honestly beyond the 24-hour raw tier. NetSpeedTray refuses to guess them rather than showing a
fabricated p95 — if you see percentiles marked unavailable in the Statistics sheet, this is why.

**`avg` and `max` answer different questions.** The graph plots `max` at minute and hour resolution,
so a long-range line is a *peak envelope*, not a rate trace. Totals use `avg`. The two cannot be
reconciled by eye, and that is expected.

**Compute volume from `avg × bucket_seconds`, not `sample_count × poll_interval`.** The poll rate is
user-configurable; multiplying by it retroactively rescales history if the user ever changed it. Use
60 for minute rows and 3600 for hour rows.

**Timestamps are Unix epoch seconds**, interpreted in local time by the app
(`datetime.fromtimestamp`). They are bucket *start* times in the rollup tables.

---

## Recipes

Read-only, safe to run while the app is open.

**Daily volume per interface, last 30 days**

```sql
SELECT date(timestamp, 'unixepoch', 'localtime') AS day,
       interface_name,
       ROUND(SUM(download_avg) * 60 / 1e9, 2) AS down_gb,
       ROUND(SUM(upload_avg)   * 60 / 1e9, 2) AS up_gb
FROM speed_history_minute
GROUP BY day, interface_name
ORDER BY day DESC, down_gb DESC;
```

**Which adapters actually carry traffic**

```sql
SELECT interface_name,
       ROUND(SUM(download_avg) * 60 / 1e9, 3) AS down_gb,
       ROUND(SUM(upload_avg)   * 60 / 1e9, 3) AS up_gb
FROM speed_history_minute
GROUP BY interface_name
ORDER BY down_gb DESC;
```

**Your busiest hour**

```sql
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS hour,
       ROUND(MAX(download_max) * 8 / 1e6, 1) AS peak_down_mbps
FROM speed_history_hour
GROUP BY timestamp
ORDER BY peak_down_mbps DESC
LIMIT 10;
```

**Internet latency and packet loss over the last week**

```sql
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS hour,
       ROUND(MAX(CASE WHEN stat_type='latency_anchor' THEN avg_value END), 1) AS latency_ms,
       ROUND(MAX(CASE WHEN stat_type='latency_gw_timeout' THEN avg_value END) * 100, 1) AS loss_pct
FROM hardware_stats_hour
WHERE timestamp > strftime('%s','now','-7 days')
GROUP BY timestamp
HAVING latency_ms IS NOT NULL
ORDER BY hour;
```

Empty result? The hourly tier only fills once data is older than 30 days, so on a young install
widen the window or query `hardware_stats_minute` instead.

**Percentiles — but only over the last 24 hours**, where per-second data still exists:

```sql
SELECT ROUND(download_bytes_sec * 8 / 1e6, 1) AS mbps
FROM speed_history_raw
WHERE interface_name = 'Wi-Fi 3'
ORDER BY download_bytes_sec
LIMIT 1 OFFSET (SELECT COUNT(*) * 95 / 100 FROM speed_history_raw
                WHERE interface_name = 'Wi-Fi 3');
```

Beyond 24 hours this is not possible from the stored columns, by design — see *no `min` column*
above.

---

## Schema versioning

`metadata.db_version` tracks the schema; the current version is **7**. On launch, a database at an
older version is backed up and migrated forward one step at a time (v2→v3→…→v7). Migrations are
additive — no version has ever dropped a column.

| Version | Added |
|---|---|
| 3 | covering indexes, `metadata` table |
| 4 | `sample_count` on the rollup tables |
| 5 | hardware statistics tables |
| 6 | `hardware_stats_hour` |
| 7 | `usage_counter` (the data-cap odometer) |

**If you add a table or column:** bump `_DB_VERSION` in `core/database.py`, add a `_migrate_vN_to_vN+1`
method, and update this file. Old databases must keep working — a migration that assumes a column
exists will break every existing install.

---

## Maintenance

A background pass runs periodically and does three things: roll each tier up into the next, delete
data past your retention setting, and truncate the write-ahead log.

It also runs `VACUUM` to reclaim space, at most once a day and only when there is real slack to
reclaim. Rolling data up deletes rows, and deleted rows leave free pages behind; without a periodic
VACUUM the file keeps its high-water mark forever.

If you ever want to reclaim space by hand — with NetSpeedTray closed:

```sql
VACUUM;
```

---

## What is deliberately *not* stored

NetSpeedTray does not record what it cannot measure honestly:

- **Per-application bytes.** Windows does not attribute network bytes per process without a kernel
  driver. The Monitor shows per-app *connections*, which is real, rather than per-app speeds, which
  would be a guess.
- **Which traffic was LAN and which was internet.** Adapter byte counters carry no addresses, ports
  or routes — the information simply is not there to filter on.
- **Anything that leaves your machine.** No telemetry, no accounts, no uploads. The latency probe
  pings your own gateway by default; pinging a public host is opt-in and you name the host.
