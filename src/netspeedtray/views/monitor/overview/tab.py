"""
OverviewTab - the Monitor's control center: an at-a-glance "everything that's going on" screen.

By contract this tab NEVER imports matplotlib (it's the default tab, so a glance-only session stays
at the idle-RAM baseline). It leads with a NetworkHero (co-equal download + upload over a dual
sparkline), then a row of hardware tiles (CPU / GPU / RAM / VRAM - utilization, temperature, memory),
then a Today/This-month data-usage card. The Monitor forces hardware collection while it's open, so
the hardware tiles show real data even when the taskbar widget has hardware monitoring off.

The tiles refresh on a 1 Hz timer that only runs while the tab is actually visible. All data is read
defensively from the main widget and its WidgetState, so a missing field or a teardown race degrades
to a dash (or a hidden tile), never a crash.

The window's history reads (sparklines, avg/peak, drops) run on the tab's own long-lived reader
thread - the Monitor graph worker's bind_reader_thread/stop_reader_thread lifecycle - because on a big
database they froze the UI for up to ~1.3 s every few seconds. Reloads are coalesced (one in flight,
at most one queued behind it), and a result for an old range, a hidden tab or a closed Monitor is
dropped. The tab asks every 6 s; the reader re-reads the window part only when the window's own
cadence is due (6 s / 30 s / 90 s by length) and the database changed, and reads the session totals
every time (_ReadCache, _reload_interval_ms).
"""
from __future__ import annotations

import logging
import math
import re
import time
from collections import deque
from functools import partial
from typing import Any, Deque, Dict, List, Optional

from datetime import date, datetime, timedelta

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QToolButton, QScrollArea, QFrame,
)

from netspeedtray import constants
from netspeedtray.core.db.history_reader import overview_bin_seconds
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_speed, format_data_size, format_duration_short
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds
from netspeedtray.utils.widget_paint import WidgetMetrics   # reuse its Mbps→bytes/sec converter (DRY)
# NOTE: `summaries` is numpy-free since #488 (the widget's hover card reads it too), so importing it here
# would cost little; it stays imported lazily inside _read_window, the one place it is used.
from netspeedtray.views.monitor.overview.tiles import StatTile, UsageTile, NetworkHero, dynamic_range
from netspeedtray.views.monitor.overview.busiest_apps import BusiestAppsCard
from netspeedtray.views.monitor.timeline_selector import TimelineSelector
from netspeedtray.views.monitor.graph_host import bind_reader_thread, stop_reader_thread
# stats_detail (the detail sheet + its exporter) is only needed when the user opens the detail sheet or
# exports, so it is imported LAZILY in those handlers - keeping the Overview's import path light.

# Per-resource accent as (dark, light) pairs. Network up/down get a distinct, harmonious pair; CPU/GPU
# echo the graph's line hues; RAM/VRAM get their own calm colors. The light variant keeps the thin
# trend line legible on the near-white light card.
_ACCENTS = {
    # Download green / upload blue - the SAME codes as the standalone graph (color.DOWNLOAD/UPLOAD_LINE_COLOR)
    # so the hero and the Network-tab graph read identically.
    "down": ("#42B883", "#42B883"),
    "up":   ("#4287F5", "#4287F5"),
    "cpu":  ("#00BCD4", "#0097A7"),
    "gpu":  ("#FF9800", "#F57C00"),
    "ram":  ("#4CAF50", "#388E3C"),
    "vram": ("#9C27B0", "#7B1FA2"),
}

_REFRESH_MS = 1000        # live current-value tick
# The DB window reload (sparklines + avg/peak) runs on the reader thread, off the 1 Hz path, at a
# cadence set by the window's length (_reload_interval_ms; constants.timers.OVERVIEW_*).
_NARROW_BP = 760          # below this content width: reflow to the compact layout (2×2 tiles, stacked cards)
# latency_probe.latency_verdict code -> (i18n key, English fallback)
def _plain_text(html: str) -> str:
    """Rich text -> its words (an accessible name)."""
    return re.sub(r"<[^>]+>", "", str(html or "")).replace("&nbsp;", " ").strip()


# A window read made while latency monitoring was off: its (empty) drops are about no hop at all.
HOP_OFF = "off"
_VERDICT_KEYS = {"good": ("LATENCY_GOOD", "Good"), "ok": ("LATENCY_OK", "OK"), "slow": ("LATENCY_SLOW", "Slow")}


def _resolve_window(ws, key: str, now: datetime, session_start: Optional[datetime], earliest_fn=None):
    """(start, end, is_session) for period ``key`` at ``now``. All / System uptime need the earliest
    stored sample - a DB read (plus a batch flush), so the Overview's reloads resolve it on the
    reader thread (through ``earliest_fn``: its cache, no flush); Export and the Stats-detail sheet
    resolve it on demand."""
    hp = constants.data.history_period
    earliest = None
    if key in ("TIMELINE_ALL", "TIMELINE_SYSTEM_UPTIME") and ws is not None:
        try:
            earliest = earliest_fn() if earliest_fn is not None else ws.get_earliest_data_timestamp()
        except Exception:
            earliest = None
    # "System uptime" starts at the real boot (M3). It used to pass None here, which fell through to
    # the "last 24 h" fallback below - and the Stats sheet and Export inherit this window.
    boot = _boot_time() if key == "TIMELINE_SYSTEM_UPTIME" else None
    start = hp.get_start_time(key, now, session_start, boot, earliest)
    if start is None:
        start = now - timedelta(hours=24)
    return start, now, (key == "TIMELINE_SESSION")


def drop_time_label(last: datetime, now: datetime, language: Optional[str]) -> str:
    """The "last" drop's time: HH:MM today, else the locale's numeric short date without the year
    plus HH:MM ("10/11 16:48", "11.10 16:48") - an old drop must not read as this morning's. The
    locale gives the day/month ORDER only: digits stay Western in every language, as every clock
    and value does (helpers.fixed_digits) - formatted under QLocale "C", never "১১/১০"."""
    hm = last.strftime("%H:%M")
    if last.date() == now.date():
        return hm
    fallback = constants.network.latency.OUTAGE_LAST_DATE_FALLBACK_FORMAT
    try:
        from PyQt6.QtCore import QDate, QLocale
        fmt = QLocale(str(language or "en_US")).dateFormat(QLocale.FormatType.ShortFormat)
        fmt = re.sub(r"^y+[^dMy]*|[^dMy]*y+", "", fmt).strip() or fallback
        if "MMM" in fmt:   # a month NAME would be in C (English) - keep it numeric
            fmt = fmt.replace("MMMM", "M").replace("MMM", "M")
        day = QLocale.c().toString(QDate(last.year, last.month, last.day), fmt)
    except Exception:
        day = f"{last.month}/{last.day}"
    return f"{day} {hm}"


def drop_color(last: Optional[datetime], now: datetime) -> str:
    """The drop count's colour: red while the last drop is at most OUTAGE_RECENT_SEC old (24 h,
    elapsed time - not "today"), amber when older or unknown. Red means recent, not "many"."""
    lat = constants.network.latency
    if last is not None and (now - last) <= timedelta(seconds=lat.OUTAGE_RECENT_SEC):
        return lat.VERDICT_COLORS["slow"]
    return lat.VERDICT_COLORS["ok"]


def _boot_time() -> Optional[datetime]:
    """When this PC last started (psutil; no DB, no matplotlib), or None if unreadable."""
    try:
        import psutil
        return datetime.fromtimestamp(psutil.boot_time())
    except Exception:
        return None


def saved_period_index(config: Dict[str, Any]) -> int:
    """The Monitor period to open on. A CHOSEN period is honoured as stored - including Session,
    whose index is 0. Never chosen (a fresh install stores 0 too) opens on 24 h, as it always has.
    The old ``int(x or 2)`` could not tell the two apart and turned a chosen Session into 24 h (M2)."""
    raw = config.get("history_period_slider_value", 2)
    try:
        if config.get("history_period_chosen", False):
            return int(raw if raw is not None else 2)
        return int(raw or 2)
    except (TypeError, ValueError):
        return 2


def _cap_coverage(ws, cap_start: Optional[datetime]) -> Optional[float]:
    """The fraction of the data cap's period (``cap_start`` to now) NetSpeedTray was running, from
    the recorded sessions (F19) - or None when that is not known for ALL of it: no cap, a reader with
    no session record (v7), a period that began before the first recorded session (history from
    before 2.2, or after "Delete all history"), or a failed read. Never a guess."""
    if cap_start is None:
        return None
    try:
        cov = ws.history_reader.coverage(cap_start, datetime.now())
    except Exception:
        return None
    if cov is None or cov.unknown_seconds > 0 or cov.end_ts <= cov.start_ts:
        return None
    running = cov.running_seconds
    # The live session ends at its last heartbeat: the few seconds since are this run, not a gap.
    tail = cov.end_ts - (cov.sessions[-1][1] if cov.sessions else cov.start_ts)
    if cov.sessions and 0 < tail <= constants.network.usage.COVERAGE_HEARTBEAT_SLACK_SEC:
        running += tail
    return min(1.0, running / float(cov.end_ts - cov.start_ts))


def _reload_interval_ms(key: str, now: Optional[datetime] = None,
                        session_start: Optional[datetime] = None) -> int:
    """How often the Overview re-reads window ``key``'s figures from the DB, by the window's length:
    up to 4 h every 6 s, up to 48 h every 30 s, longer every 90 s (constants.timers.OVERVIEW_*). The
    Session's length is the session's. A full read costs 0.1-0.4 s of CPU on a big database; one
    sparkline point of 24 h is ~13 minutes."""
    t = constants.timers
    now = now or datetime.now()
    if key == "TIMELINE_SESSION":
        span = max(0.0, (now - session_start).total_seconds()) if session_start is not None else 0.0
    elif key == "TIMELINE_ALL":
        span = math.inf
    elif key == "TIMELINE_SYSTEM_UPTIME":
        boot = _boot_time()
        span = (now - boot).total_seconds() if boot is not None else math.inf
    else:
        span = constants.data.history_period.CUTOFF_DAYS.get(key, 1) * 86400
    if span <= t.OVERVIEW_SHORT_WINDOW_SECONDS:
        return t.OVERVIEW_SHORT_RELOAD_MS
    if span <= t.OVERVIEW_DAY_WINDOW_SECONDS:
        return t.OVERVIEW_DAY_RELOAD_MS
    return t.OVERVIEW_LONG_RELOAD_MS


def _bin_points(points, start_ts: int, end_ts: int, b: int, peak: bool) -> List[Optional[float]]:
    """(epoch ts, value) pairs as one value per ``b``-second bin, from the bin holding ``start_ts`` to
    the one holding ``end_ts`` - each bin its peak (``peak``) or mean, None where there is no value.
    The bins are ``ts // b``, exactly the v8 SQL read's (overview_bin_seconds), so a reload a few
    seconds later lands every sample in the same bin and gaps stay at their place in time."""
    first = int(start_ts) // b
    n = max(1, int(end_ts) // b - first + 1)
    vals: List[Optional[float]] = [None] * n
    counts = [0] * n
    for ts, v in points:
        if v is None:
            continue
        i = int(ts) // b - first
        if not 0 <= i < n:
            continue
        v = float(v)
        if vals[i] is None:
            vals[i], counts[i] = v, 1
        elif peak:
            vals[i] = max(vals[i], v)
        else:
            vals[i] += v
            counts[i] += 1
    if not peak:
        vals = [v / c if v is not None else None for v, c in zip(vals, counts)]
    return vals


def _session_series(samples: Dict[str, list], start_ts: int, end_ts: int) -> Dict[str, list]:
    """The Session window's sparklines from WidgetState's in-memory samples (``agg``: AggregatedSpeedData,
    ``cpu`` / ``gpu`` / ``ram``: HardwareStatSnapshot - immutable, list-copied on the GUI thread),
    binned like the DB read. A metric with no samples at all stays an empty series."""
    b = overview_bin_seconds(start_ts, end_ts)
    agg = samples.get("agg") or []
    out = {"down": _bin_points(((a.timestamp.timestamp(), a.download) for a in agg), start_ts, end_ts, b, True),
           "up": _bin_points(((a.timestamp.timestamp(), a.upload) for a in agg), start_ts, end_ts, b, True)}
    for k in ("cpu", "gpu", "ram"):
        items = samples.get(k) or []
        out[k] = (_bin_points(((s.timestamp.timestamp(), s.value) for s in items), start_ts, end_ts, b, False)
                  if items else [])
    return out


class _ReadCache:
    """The Overview's memory between reloads. Lives on, and is only touched by, the reader thread (one
    load runs at a time); the GUI thread only bumps the ``epoch`` it passes with each request.

    * The earliest stored sample (All / System uptime): read once per epoch, without the batch flush
      ``get_earliest_data_timestamp`` does for Export - that flush was a DB write every reload.
    * The last successful window read: reused while it is younger than the window's reload interval,
      and past it while the database is unchanged (its ``data_version`` token) - up to
      OVERVIEW_REUSE_MAX_AGE_MS. A failed read is never kept.

    A new epoch (range change, Overview shown, history deleted) drops both."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self.epoch: Optional[int] = None
        self._reset()

    def _reset(self) -> None:
        self._sig = self._token = self._db = self._earliest = None
        self._at = 0.0

    def begin(self, epoch: int) -> None:
        if epoch != self.epoch:
            self.epoch = epoch
            self._reset()

    def now(self) -> float:
        return self._clock()

    def earliest(self, ws) -> Optional[datetime]:
        if self._earliest is None:   # an empty database stays unknown: ask again next time (cheap)
            self._earliest = ws.get_earliest_data_timestamp(flush=False)
        return self._earliest

    def reuse(self, sig, token, interval_ms: float = 0.0, force: bool = False):
        if force or self._db is None or sig != self._sig:
            return None
        t = constants.timers
        age_ms = (self._clock() - self._at) * 1000.0
        if age_ms < interval_ms - t.OVERVIEW_REUSE_SLACK_MS:
            return self._db          # the window's cadence: not due yet
        if token is not None and token == self._token and age_ms < t.OVERVIEW_REUSE_MAX_AGE_MS:
            return self._db          # due, but nothing was written since
        return None

    def store(self, sig, token, db, at: Optional[float] = None) -> None:
        self._sig, self._token, self._db = sig, token, db
        self._at = self._clock() if at is None else at


def _data_version(ws):
    fn = getattr(ws, "get_history_data_version", None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def _session_volume(ws, session_start: Optional[datetime]):
    """The session's ↓/↑ totals (the strip) - read on EVERY request (a few ms), never from the cached
    window read, so they keep counting while a long window's figures wait for their interval."""
    if session_start is None:
        return None
    try:
        return ws.get_history_volume_for_period(session_start, datetime.now())
    except Exception:
        return None   # the strip just shows the uptime, as when this read failed on the GUI thread


def _read_window(ws, key: str, now: datetime, session_start: Optional[datetime],
                 series: Optional[Dict[str, list]], poll: float, latency_enabled: bool,
                 cap_start: Optional[datetime] = None, public_enabled: bool = False,
                 cache: Optional[_ReadCache] = None, epoch: int = 0, interval_ms: float = 0.0,
                 force: bool = False):
    """The window's reads - (series, summaries, drop events, session volume, cap coverage, fresh,
    read_at). Runs on the reader thread. ``series`` is the Session's in-memory samples (``agg`` /
    ``cpu`` / ``gpu`` / ``ram`` lists, copied on the GUI thread, where they are appended); None means
    read the sparklines from the DB. The session volume (the strip's ↓/↑ totals, independent of the
    range) rides along, read every time, so the 1 Hz render never reads.

    Sparklines span the whole window, one value per bin and None where nothing was recorded;
    ``summaries`` holds the download / upload count, avg and peak - the hero's figures. With a
    ``cache`` (the tab's), the window part is reused until ``interval_ms`` has passed and the database
    changed (``force`` re-reads regardless). ``fresh`` says whether it was read now, at ``read_at``
    (the cache's monotonic clock)."""
    earliest_fn = None
    if cache is not None:
        cache.begin(epoch)
        earliest_fn = partial(cache.earliest, ws)
    start, end, _is_session = _resolve_window(ws, key, now, session_start, earliest_fn)
    sig = (key, session_start, cap_start, poll, bool(latency_enabled), bool(public_enabled), series is None)
    # The token is taken BEFORE the reads: a write landing during them changes it for the next reload.
    token = _data_version(ws) if cache is not None else None
    db = cache.reuse(sig, token, interval_ms, force) if cache is not None else None
    fresh = db is None
    read_at = cache.now() if cache is not None else time.monotonic()
    if db is None:
        db_series, summ, events, coverage, ok = _read_db(ws, start, end, series is None, poll,
                                                         latency_enabled, cap_start, public_enabled)
        db = (db_series, summ, events, coverage)
        if cache is not None and ok:   # a failed read is shown once, never kept as current
            cache.store(sig, token, db, read_at)
    db_series, summ, events, coverage = db
    if series is not None:
        db_series = _session_series(series, int(start.timestamp()), int(end.timestamp()))
    return db_series, summ, events, _session_volume(ws, session_start), coverage, fresh, read_at


def _read_db(ws, start: datetime, end: datetime, want_series: bool, poll: float, latency_enabled: bool,
             cap_start: Optional[datetime], public_enabled: bool):
    """_read_window's database part: (series or None, summaries, drop events, cap coverage, ok)."""
    st, et = int(start.timestamp()), int(end.timestamp())
    b = overview_bin_seconds(st, et)
    lean_fn = getattr(ws, "get_overview_window", None)
    lean = lean_fn(start, end, b, want_series) if lean_fn is not None else None
    if lean is not None:
        series, summ, ok = lean
    else:
        # No lean read (a v7 file while the upgrade is deferred): the general-purpose reads, binned
        # the same way. Their failures are swallowed inside (as before), so they count as read.
        ok = True
        series = None
        if want_series:
            # wait_for_flush=False: never hold a refresh on the DB-worker drain for a last second
            # that's invisible at multi-hour resolution.
            net = ws.get_speed_history(start, end, None, resolution='auto', wait_for_flush=False)

            def epoch_of(t):
                return t.timestamp() if isinstance(t, datetime) else float(t)

            series = {"down": _bin_points(((epoch_of(r[0]), r[2]) for r in net), st, et, b, True),
                      "up": _bin_points(((epoch_of(r[0]), r[1]) for r in net), st, et, b, True)}
            for stat in ("cpu", "gpu", "ram"):
                pts = ws.get_hardware_history(stat, start, end)
                series[stat] = (_bin_points(((epoch_of(t), v) for t, v in pts), st, et, b, False)
                                if pts else [])
        # Only download / upload: the hardware tiles show live values, never a window avg/peak.
        summ = {
            "down": ws.summarize_network("download", start, end, None, poll),
            "up": ws.summarize_network("upload", start, end, None, poll),
        }
    # Connection-drop events over the window (from the persisted loss series), so a latency blip you
    # missed live is still counted. ONE hop decides: the internet anchor when the internet ping is on
    # (if it answered, the connection was up - and a real router failure loses it too, so nothing
    # true is lost), else the router. The Stats sheet's block for the same hop reads the same.
    # Every result names the hop it is about - the strip says "Router: ..." / "Internet: ..." (D5) and
    # never shows a result read for another hop (the internet check just switched) - or HOP_OFF: read
    # while latency was off, so it says nothing about either hop.
    events: Dict[str, Any] = {"hop": HOP_OFF}
    if latency_enabled:
        from netspeedtray.utils import summaries as S   # lazy (only this reader uses it)
        from netspeedtray.core.latency_probe import HOP_ANCHOR, HOP_GATEWAY
        lat = constants.network.latency
        stat = lat.STAT_ANCHOR_TIMEOUT if public_enabled else lat.STAT_GW_TIMEOUT
        rows = ws.get_loss_history([stat], start, end).get(stat, [])
        events = S.monitor_outages(rows)
        events["hop"] = HOP_ANCHOR if public_enabled else HOP_GATEWAY
        # How much of the window this hop covers: "Internet: steady" right after Turn on must not
        # speak for a month the internet ping never saw (D5 review). Its own read: loss_history holds
        # only the BAD rows and their neighbours on v8, none for a clean hop (live test F2 - the line
        # said "checking…" for good while every reply came back).
        coverage_fn = getattr(ws, "get_metric_coverage", None)
        if callable(coverage_fn):
            n, first = coverage_fn(stat, start, end)
            events["rows"], events["first"] = int(n or 0), first
        else:
            events["rows"] = len(rows)
            events["first"] = min((r.ts for r in rows if getattr(r, "ts", None) is not None), default=None)
        events["window_start"] = start
    return series, summ, events, _cap_coverage(ws, cap_start), ok


def _raise_last(series: list, live: float) -> list:
    """``series`` with its last bin (the one holding "now") raised to ``live`` when that is higher."""
    if not series or not live or live <= 0:
        return list(series)
    out = list(series)
    if out[-1] is None or out[-1] < live:
        out[-1] = float(live)
    return out


class _WindowLoader(QObject):
    """Lives on the Overview's reader thread and runs one window read per request."""

    loaded = pyqtSignal(int, object)   # (load sequence, _read_window result - or the exception)

    @pyqtSlot(int, object)
    def run(self, seq: int, fn) -> None:
        try:
            result = fn()
        except Exception as e:   # handed back so the GUI thread logs it (rate-limited) as before
            result = e
        self.loaded.emit(seq, result)


class OverviewTab(QWidget):
    """Overview tab content - the control center. Matplotlib-free by contract."""

    stat_type = "overview"

    _load_requested = pyqtSignal(int, object)   # -> _WindowLoader.run on the reader thread

    def __init__(self, main_widget, config: Dict[str, Any], i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._main_widget = main_widget
        self._config = config
        self._i18n = i18n
        self.logger = logging.getLogger("NetSpeedTray.OverviewTab")
        self._tiles: Dict[str, StatTile] = {}
        # RAM/VRAM have no WidgetState history deque, so the tab keeps its own rolling buffer.
        self._ram_series: Deque[float] = deque(maxlen=120)
        self._vram_series: Deque[float] = deque(maxlen=120)

        dark = su.is_dark_mode()

        def accent(key: str) -> str:
            pair = _ACCENTS.get(key)
            return (pair[0] if dark else pair[1]) if pair else su.semantic_colors()["accent"]

        # The global timeline scopes every card to real DB history; default to the saved period (24h
        # if unset), so the control center opens on history, not "since the page opened".
        self._period_index = saved_period_index(config)
        self._series: Dict[str, Any] = {}     # latest per-metric sparkline series for the active window
        # Latest per-metric WindowSummary / drop events for the active window; None = not read yet
        # (the hero says "Reading history…" and the strip claims nothing, rather than showing zeros).
        self._win_summ: Optional[Dict[str, Any]] = None
        # The session's ↓/↑ volume (VolumeResult) from the reader's last load; None = not read yet.
        self._session_volume = None
        # The window reads run on a reader thread (started on the first reload, stopped in teardown).
        # _load_seq stamps each request; bumping it (range change, hide, close) makes the result in
        # flight stale. One request at a time: a reload asked for meanwhile only sets _load_dirty.
        self._reader_thread: Optional[QThread] = None
        self._reader: Optional[_WindowLoader] = None
        self._reader_ident: Optional[int] = None
        self._load_seq = 0
        self._load_busy = False
        self._load_dirty = False
        self._closed = False
        # Reader-thread memory between reloads (earliest sample, last read + its data_version); keyed
        # by _load_seq, so a range change, a hide/show or a history delete starts it afresh.
        self._read_cache = _ReadCache()
        self._force_reload = False      # the next request re-reads the window whatever its cadence
        self._last_request_at = -1e9    # monotonic time of the last request (activation re-asks)
        # Live ↓/↑ samples (monotonic t, down, up) not yet surely in a window read: the card's peak and
        # the sparkline's last point are never below what the user just saw live (_render).
        self._live_peaks: Deque[tuple] = deque(maxlen=600)
        self._last_link_lost: Optional[bool] = None
        self._filtered_window = None
        # "Delete all history" (Settings) empties what the cards show: re-read at once, whatever the
        # cadence, instead of showing the deleted figures for up to two minutes.
        self._history_deleted_sig = None
        try:
            sig = getattr(getattr(main_widget, "widget_state", None), "history_deleted", None)
            if sig is not None:
                sig.connect(self._on_history_deleted)
                self._history_deleted_sig = sig
        except Exception:
            pass

        # The tab itself holds ONLY a scroll area; all cards live in an inner content widget. When the
        # window is shorter than the content needs, it scrolls instead of squeezing widgets past their
        # minimums (which is what made the hero's avg/peak line ride up over the sparkline). Horizontal
        # scrolling is off - width is handled by the responsive reflow (resizeEvent) instead.
        self._narrow = True   # provisional; resolved by the first resizeEvent
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        # A Tab stop (arrow keys scroll the page): named, or Narrator says just "group" (VM round 2).
        self._scroll.setAccessibleName(self._tr("MONITOR_TAB_OVERVIEW", "Overview"))
        content = QWidget()
        content.setStyleSheet("background: transparent;")

        c = su.semantic_colors()
        _m = constants.layout.MONITOR_BODY_MARGIN

        # --- Header band: a discoverable Export action (left) + the timeline dropdown (right). It lives
        # OUTSIDE the scroll area as a fixed-height command band - same height + right edge as the
        # Network/Hardware bands - so it never scrolls away and the timeline lines up across every tab.
        band = QWidget()
        band.setFixedHeight(constants.layout.MONITOR_HEADER_BAND_HEIGHT)
        head = QHBoxLayout(band)
        head.setContentsMargins(_m, 0, _m, 0)
        self._export_btn = QToolButton()
        self._export_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._export_btn.setIcon(su.fluent_icon(0xE896, 16, c['text_secondary']))   # Fluent "Download"
        self._export_btn.setText(self._tr('OVERVIEW_EXPORT', 'Export…'))
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setToolTip(self._tr("OVERVIEW_EXPORT_TIP",
                                              "Export this period's stats (summary + raw CSV)"))
        r = tokens.RADIUS_CONTROL
        self._export_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {c['text_secondary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: {r}px; padding: 4px 12px; }}"
            f" QToolButton:hover {{ color: {c['text_primary']}; border-color: {c['accent']}; }}")
        self._export_btn.clicked.connect(self._export_window)
        head.addWidget(self._export_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self._timeline = TimelineSelector(i18n, current_index=self._period_index)
        self._timeline.period_changed.connect(self._on_period_changed)
        head.addWidget(self._timeline, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(band)
        outer.addWidget(self._scroll)

        root = QVBoxLayout(content)
        root.setContentsMargins(_m, 4, _m, _m)
        root.setSpacing(12)

        # --- Network hero (the headline) ---
        self._hero = NetworkHero(i18n, accent("down"), accent("up"))
        self._hero.clicked.connect(lambda: self._open_detail("network"))
        self._hero.check_connection_requested.connect(self._open_connection_check)
        root.addWidget(self._hero)

        # --- Thin context strip: session uptime + totals (left), and on the right the window's
        # connection health (drops over the window, named for the hop measured - D5) so a latency blip
        # you missed live is still visible. True System power, when the platform exposes it, joins the
        # left part then (the right slot when latency monitoring is off). The row's height is pinned:
        # the offer link coming and going never moves the cards below. ---
        strip_box = QWidget()
        strip_box.setObjectName("overviewStrip")
        strip = QHBoxLayout(strip_box)
        strip.setContentsMargins(4, 0, 4, 0)
        self._session_lbl = QLabel("")
        self._session_lbl.setFont(su.font(tokens.TYPE_CAPTION))
        self._session_lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._context_r_lbl = QLabel("")
        self._context_r_lbl.setObjectName("overviewHealth")
        self._context_r_lbl.setFont(su.font(tokens.TYPE_CAPTION))
        self._context_r_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._context_r_lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        # Tab-reachable: after Turn on / Not now focus lands here, so Narrator reads the new status.
        # Only while it says something (_render_health_strip): an empty line is no Tab stop - Narrator
        # would read an unnamed "group"/"text" (VM round 2).
        self._context_r_lbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        strip.addWidget(self._session_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        strip.addStretch(1)
        strip.addWidget(self._context_r_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        # D5: beside a router-only health line, the one-click offer of the internet check. Its confirm
        # is a TeachingTip-style popup anchored to the link (InternetOfferTip, built on first open).
        # Nothing is turned on without its Turn on.
        from netspeedtray.utils.components import NoticeLink
        from netspeedtray.views.monitor.internet_offer import offer_for
        self._internet_offer = offer_for(main_widget, config)
        self._internet_link = NoticeLink("", self._tr("INTERNET_CHECK_OFFER", "Also check your internet"),
                                         self._open_internet_tip)
        self._internet_link.setObjectName("internetCheckOffer")
        self._internet_link.setVisible(False)
        strip.addWidget(self._internet_link, 0, Qt.AlignmentFlag.AlignVCenter)
        strip_box.setFixedHeight(max(tokens.NOTICE_LINK_MIN_HEIGHT + 2 * tokens.FOCUS_RING_WIDTH,
                                     self._context_r_lbl.sizeHint().height()))
        self._strip_box = strip_box
        root.addWidget(strip_box)
        self._internet_tip = None
        self._health_hop_reload = False   # one forced re-read when the drops were read for the other hop
        self._latency_events: Optional[Dict[str, Any]] = None   # outage_summary; None = not read yet

        # --- Hardware tiles: CPU / GPU / RAM / VRAM. Always built (the Monitor forces collection);
        # VRAM hides itself when there's no dedicated-VRAM reading (integrated GPUs, etc). Laid out in a
        # GRID so they can reflow 1×4 (wide) → 2×2 (narrow) without ever crushing or overlapping. ---
        self._hw_grid = QGridLayout()
        self._hw_grid.setContentsMargins(0, 0, 0, 0)
        self._hw_grid.setHorizontalSpacing(12)
        self._hw_grid.setVerticalSpacing(12)
        self._tile_order = ["cpu", "gpu", "ram", "vram"]
        for key, label in (("cpu", self._tr("ORDER_TYPE_CPU", "CPU")),
                           ("gpu", self._tr("ORDER_TYPE_GPU", "GPU")),
                           ("ram", self._tr("MONITOR_TILE_RAM", "RAM")),
                           ("vram", self._tr("MONITOR_TILE_VRAM", "VRAM"))):
            # Parent to `content` up front: the tiles are stored now but only added to the grid later
            # in _layout_tiles, while _set_visible_tiles() calls setVisible(True) on them BEFORE that.
            # Parentless, that setVisible would map each tile as its own top-level window for a frame
            # (the "small window flashes before the Monitor" bug). As children of the still-hidden
            # `content`, setVisible can't map them until the Monitor itself is shown.
            t = StatTile(label, accent(key), content)
            t.clicked.connect(lambda k=key: self._open_detail(k))   # a click opens the stat sheet
            self._tiles[key] = t
        root.addLayout(self._hw_grid)

        # --- Bottom row: Data-usage card + Most-connections card. Grid so it reflows side-by-side (wide) →
        # stacked (narrow). ---
        self._bottom_grid = QGridLayout()
        self._bottom_grid.setContentsMargins(0, 0, 0, 0)
        self._bottom_grid.setHorizontalSpacing(12)
        self._bottom_grid.setVerticalSpacing(12)
        self._usage = UsageTile(i18n)
        self._usage.set_cap_requested.connect(self._open_settings)
        self._usage.usage_history_requested.connect(self._open_usage_history)
        self._busiest = BusiestAppsCard(i18n)
        self._busiest.go_to_network.connect(self._goto_network)
        root.addLayout(self._bottom_grid)
        root.addStretch(1)

        # Seed the compact layout (all tiles; _render narrows the set once it knows what has data).
        self._visible_keys = list(self._tile_order)
        self._tile_sig = None
        self._set_visible_tiles(self._tile_order)
        self._place_bottom(stacked=True)
        self._scroll.setWidget(content)

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        # Separate, slower timer reloads the window's DB series + summaries (sparklines + avg/peak), so
        # the 1 Hz current-value tick never touches the DB. It asks every 6 s; the reader re-reads the
        # window part only when the window's own cadence (_reload_interval_ms) is due and the database
        # changed, and the session's ↓/↑ totals every time.
        self._hist_timer = QTimer(self)
        self._hist_timer.setInterval(constants.timers.OVERVIEW_SHORT_RELOAD_MS)
        self._hist_timer.timeout.connect(self._reload_window)
        # One re-read shortly after the connection drops or comes back (the drop count must not say
        # "steady" for a minute and a half): OVERVIEW_LINK_CHANGE_RELOAD_MS, after its batch commits.
        self._link_timer = QTimer(self)
        self._link_timer.setSingleShot(True)
        self._link_timer.setInterval(constants.timers.OVERVIEW_LINK_CHANGE_RELOAD_MS)
        self._link_timer.timeout.connect(lambda: self._reload_window(force=True))

    # --------------------------------------------------------------- responsive layout
    def _set_visible_tiles(self, visible_keys) -> None:
        """Show exactly `visible_keys` (in order) and reflow them to FILL the row - so e.g. CPU/GPU/RAM
        with no VRAM tile grow to thirds instead of leaving a hole where the 4th tile would be."""
        for k in self._tile_order:
            self._tiles[k].setVisible(k in visible_keys)
        self._visible_keys = list(visible_keys)
        sig = (tuple(visible_keys), self._narrow)
        if sig != self._tile_sig:
            self._tile_sig = sig
            self._layout_tiles(visible_keys)

    def _layout_tiles(self, visible_keys) -> None:
        """Place the visible tiles in a grid: up to 4 columns when wide, 2 when narrow, but never more
        than there are tiles - and the last tile spans any leftover columns so a row is always full."""
        while self._hw_grid.count():
            self._hw_grid.takeAt(0)          # detach (widgets stay parented); we re-add below
        for col in range(4):
            self._hw_grid.setColumnStretch(col, 0)
        if not visible_keys:
            return
        max_cols = 2 if self._narrow else 4
        cols = max(1, min(len(visible_keys), max_cols))
        last = len(visible_keys) - 1
        for i, key in enumerate(visible_keys):
            r, c = divmod(i, cols)
            span = (cols - c) if i == last else 1   # fill the rest of the final row
            self._hw_grid.addWidget(self._tiles[key], r, c, 1, span)
        for c in range(cols):
            self._hw_grid.setColumnStretch(c, 1)

    def _place_bottom(self, stacked: bool) -> None:
        """Data-usage + Most-connections cards side by side (wide) or stacked (narrow)."""
        self._bottom_grid.addWidget(self._usage, 0, 0)
        if stacked:
            self._bottom_grid.addWidget(self._busiest, 1, 0)
            self._bottom_grid.setColumnStretch(0, 1)
            self._bottom_grid.setColumnStretch(1, 0)
        else:
            self._bottom_grid.addWidget(self._busiest, 0, 1)
            self._bottom_grid.setColumnStretch(0, 1)
            self._bottom_grid.setColumnStretch(1, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # Reflow only when crossing the breakpoint (cheap; never every pixel). Use the tab's own width -
        # it's already updated when resizeEvent fires, whereas the scroll viewport lags a layout pass.
        narrow = self.width() < _NARROW_BP
        if narrow != self._narrow:
            self._narrow = narrow
            self._set_visible_tiles(self._visible_keys)   # re-flow current tiles for the new width
            self._place_bottom(stacked=narrow)

    # --------------------------------------------------------------- timeline / window
    def _on_period_changed(self, index: int) -> None:
        self._period_index = int(index)
        # Persist the choice (shared with the graph's slider) without a repaint storm.
        try:
            self._main_widget.config_controller.update_config(
                {"history_period_slider_value": self._period_index, "history_period_chosen": True},
                apply_and_repaint=False)
        except Exception:
            pass
        self._invalidate_window()
        self._render()          # the placeholder now, not the old range's figures under the new label
        self._reload_window()

    def _invalidate_window(self) -> None:
        """The cached window figures no longer match the selected range: drop them (back to the
        placeholder) and make any read in flight stale."""
        self._load_seq += 1
        self._series = {}
        self._win_summ = None
        self._latency_events = None

    def _period_key(self) -> str:
        return constants.data.history_period.PERIOD_MAP.get(self._period_index, "TIMELINE_24_HOURS")

    def _window(self):
        """(start, end, is_session) for the active period - resolved here, on the calling thread."""
        return _resolve_window(getattr(self._main_widget, "widget_state", None), self._period_key(),
                               datetime.now(), getattr(self._main_widget, "session_start_time", None))

    # ----------------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        # Re-sync the period from config: another tab (Network/Hardware) may have changed it while we
        # were hidden. Without this the dropdown still showed the old window AND the sparklines/avg/peak
        # computed for the stale range (mirrors HardwareTab.showEvent).
        try:
            idx = saved_period_index(self._config)
            if idx != self._period_index:
                self._period_index = idx
                self._timeline.set_period_index(idx, emit=False)
                self._invalidate_window()
        except Exception:
            pass
        self._watch_window()
        self._render()          # live values now; the window's figures follow from the reader thread
        self._reload_window()
        self._timer.start()
        self._hist_timer.start()
        self._busiest.start()   # start the per-app connection sampler (idles again on hide)

    def _watch_window(self) -> None:
        """Watch the Monitor window for activation (eventFilter); a restore is showEvent's."""
        win = self.window()
        if win is self._filtered_window or win is None:
            return
        if self._filtered_window is not None:
            try:
                self._filtered_window.removeEventFilter(self)
            except RuntimeError:
                pass
        win.installEventFilter(self)
        self._filtered_window = win

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        """Coming back to the Monitor (activated) re-asks at once when the last request is older than
        OVERVIEW_ACTIVATE_RELOAD_MS. A restore from minimized is showEvent's: Qt hands the window's
        WindowStateChange to this filter BEFORE the restore's spontaneous show, so asking here too
        cost a second read (#594 review)."""
        try:
            if obj is self._filtered_window and event.type() == QEvent.Type.WindowActivate:
                if (self.isVisible() and not obj.isMinimized()
                        and (time.monotonic() - self._last_request_at) * 1000.0
                        >= constants.timers.OVERVIEW_ACTIVATE_RELOAD_MS):
                    self._reload_window()
        except RuntimeError:
            pass
        return False

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        self._hist_timer.stop()
        if self._link_timer.isActive():
            # A link change's re-read is still pending: the next show's read must be that forced one.
            self._force_reload = True
        self._link_timer.stop()
        self._busiest.stop()
        # A read still in flight lands while hidden: drop it (showEvent reloads anyway).
        self._load_seq += 1
        self._load_dirty = False
        super().hideEvent(event)

    def teardown(self) -> None:
        """Called by MonitorWindow on close."""
        for t in (self._timer, self._hist_timer, self._link_timer):
            try:
                t.stop()
            except Exception:
                pass
        if self._filtered_window is not None:
            try:
                self._filtered_window.removeEventFilter(self)
            except RuntimeError:
                pass
            self._filtered_window = None
        try:
            self._busiest.teardown()
        except Exception:
            pass
        self._closed = True
        self._load_seq += 1     # anything still in flight is now stale
        self._load_dirty = False
        if self._history_deleted_sig is not None:
            try:
                self._history_deleted_sig.disconnect(self._on_history_deleted)
            except (TypeError, RuntimeError):
                pass
            self._history_deleted_sig = None
        self._stop_reader()

    def _on_history_deleted(self, _status: str = "") -> None:
        """History was deleted (or the delete failed): what the cards show may be gone - re-read now.
        Bumping the sequence drops a read already in flight (it saw the old rows) and the reader's
        cache; a reload asked for while one runs reruns after it (_load_dirty)."""
        if self._closed:
            return
        self._load_seq += 1
        self._reload_window()

    # ----------------------------------------------------------------- reader thread
    def _ensure_reader(self) -> None:
        if self._reader_thread is not None:
            return
        self._reader_thread = QThread()
        self._reader = _WindowLoader()
        self._reader.moveToThread(self._reader_thread)
        self._reader.loaded.connect(self._on_window_loaded)    # queued: lands on the GUI thread
        self._load_requested.connect(self._reader.run)         # queued: runs on the reader thread
        # The reader thread's read connection is opened, and closed, on that thread.
        bind_reader_thread(self._reader_thread, getattr(self._main_widget, "widget_state", None),
                           self._set_reader_ident)
        self._reader_thread.start()

    def _set_reader_ident(self, ident: int) -> None:
        self._reader_ident = ident   # runs on the reader thread (started, DirectConnection)

    def _stop_reader(self) -> None:
        """Stop + release the reader thread (bounded; see stop_reader_thread). Idempotent. The Monitor
        is closing, so a read in flight is unwanted: interrupt its query first instead of giving it
        GRAPH_THREAD_QUIT_WAIT_MS to finish (the Stats-detail sheet's close does the same)."""
        thread, reader = self._reader_thread, self._reader
        self._reader_thread = self._reader = None
        if thread is None:
            return
        try:
            reader.loaded.disconnect(self._on_window_loaded)
            self._load_requested.disconnect(reader.run)
        except (TypeError, RuntimeError):
            pass
        ws = getattr(self._main_widget, "widget_state", None)
        if self._load_busy:
            interrupt = getattr(ws, "interrupt_reads", None)
            if interrupt is not None and self._reader_ident is not None:
                try:
                    interrupt(thread_id=self._reader_ident)
                except Exception:
                    pass
        self._load_busy = False
        if stop_reader_thread(thread, reader, ws, self._reader_ident, self.logger):
            reader.deleteLater()
            thread.deleteLater()

    # ----------------------------------------------------------------- refresh

    def _reload_window(self, force: bool = False) -> None:
        """Load the selected window's series (sparklines) + honest summaries (avg/peak) from the DB -
        or the live in-memory deques for the Session window - then repaint. Runs every few seconds and
        on a period change, NOT on the 1 Hz tick, so the DB read never touches the current-value path.

        The DB reads run on the reader thread (_read_window); _on_window_loaded stores and paints the
        result. While one is in flight, a further reload is coalesced into one rerun after it."""
        if force:
            self._force_reload = True
        if not self.isVisible() or self._closed:
            return
        try:
            win = self.window()
            if win is not None and win is not self and win.isMinimized():
                return      # paused while minimized; the restore re-asks (showEvent)
        except RuntimeError:
            return
        ws = getattr(self._main_widget, "widget_state", None)
        if ws is None:
            if self._win_summ is None:      # nothing will ever load: no "Reading history…" forever
                self._win_summ, self._latency_events = {}, {"hop": HOP_OFF}
            self._render()
            return
        if self._load_busy:
            self._load_dirty = True
            return
        # The range is resolved on the reader thread (All / System uptime read the earliest sample);
        # only the period, the clock and the session start are taken here.
        key, now = self._period_key(), datetime.now()
        session_start = getattr(self._main_widget, "session_start_time", None)
        interval = _reload_interval_ms(key, now, session_start)
        # Seconds per sample for coverage / volume math: the shared resolver, never a local `or 1.0`.
        poll = resolve_poll_interval_seconds(self._config.get("update_rate", 1.0))
        series = None
        if key == "TIMELINE_SESSION":
            try:   # the in-memory deques are appended on this thread: copy them here, not off-thread
                agg = ws.get_aggregated_speed_history()
                # The buffer holds the last few thousand samples (~83 min at 1 s). A longer session is
                # read from the DB like any window, so the sparkline spans what avg/peak cover.
                slack = constants.timers.OVERVIEW_SESSION_BUFFER_SLACK_SECONDS
                if session_start is None or (agg and (agg[0].timestamp - session_start).total_seconds() <= slack):
                    series = {"agg": agg, "cpu": ws.get_cpu_history(), "gpu": ws.get_gpu_history(),
                              "ram": ws.get_ram_history()}
            except Exception as e:
                self._log_reload_error(e)
                if self._win_summ is None:  # no load follows: no "Reading history…" forever
                    self._win_summ, self._latency_events = {}, {"hop": HOP_OFF}
                self._render()
                return
        self._ensure_reader()
        self._load_busy = True
        self._last_request_at = time.monotonic()
        force, self._force_reload = self._force_reload, False
        self._load_requested.emit(self._load_seq, partial(
            _read_window, ws, key, now, session_start, series, poll,
            bool(self._config.get("latency_enabled", True)), self._cap_period_start(ws),
            bool(self._config.get("latency_public_enabled", False)),
            cache=self._read_cache, epoch=self._load_seq, interval_ms=interval, force=force))

    def _cap_period_start(self, ws) -> Optional[datetime]:
        """The data cap's current period start (the odometer owns it), or None with no cap set."""
        try:
            if not self._config.get("data_cap_enabled") or float(self._config.get("data_cap_gb", 0) or 0) <= 0:
                return None
            return datetime.strptime(str(ws.get_usage_period_key()), "%Y-%m-%d")
        except Exception:
            return None

    @pyqtSlot(int, object)
    def _on_window_loaded(self, seq: int, result) -> None:
        self._load_busy = False
        if self._closed or sip.isdeleted(self):
            return
        # Stale: the range changed, or the tab was hidden, after this read began - drop it.
        if seq == self._load_seq:
            if isinstance(result, Exception):
                self._log_reload_error(result)
                if self._win_summ is None:
                    self._win_summ = {}     # stop saying "Reading history…"; the strip stays quiet
            else:
                self._series, self._win_summ, self._latency_events, self._session_volume = result[:4]
                self._cap_coverage = result[4] if len(result) > 4 else None
                if len(result) > 6 and result[5]:
                    # A fresh window read holds every live sample older than the persist lag: those
                    # no longer need holding up the peak.
                    hold = constants.timers.OVERVIEW_LIVE_PEAK_HOLD_MS / 1000.0
                    while self._live_peaks and self._live_peaks[0][0] < result[6] - hold:
                        self._live_peaks.popleft()
            self._render()
        if self._load_dirty:
            self._load_dirty = False
            self._reload_window()

    def _log_reload_error(self, e: BaseException) -> None:
        # Log the FIRST failure loudly (with traceback) so a broken Overview leaves a diagnostic in
        # the support bundle; rate-limit the rest to DEBUG so the few-second timer doesn't spam.
        if not getattr(self, "_reload_err_logged", False):
            self._reload_err_logged = True
            self.logger.warning("Overview window reload failed (further at DEBUG): %s", e,
                                exc_info=(type(e), e, e.__traceback__))
        else:
            self.logger.debug("Overview window reload skipped: %s", e)

    def _tick(self) -> None:
        # hideEvent stops the timer when we leave Overview or the window minimizes; this guard is
        # belt-and-suspenders (a child's isVisible() stays True while the top-level is minimized).
        if not self.isVisible():
            return
        win = self.window()
        if win is not None and win.isMinimized():
            return
        self._render()

    def _render(self) -> None:
        """Paint the cards: live CURRENT values (1 Hz) over the cached window series + avg/peak."""
        mw = self._main_widget
        ser = self._series
        summ = self._win_summ
        try:
            # --- Network hero: live current ↓/↑ over the window's dual sparkline; sub = window avg+peak.
            # download_speed/upload_speed are in Mbps; _fmt_speed/format_speed (and the byte-rate series)
            # are in bytes/sec. Reuse the widget's OWN Mbps→bytes/sec converter (WidgetMetrics.net_bytes)
            # rather than re-deriving it - passing raw Mbps collapsed the headline number to ~0.0 (most
            # visibly in "always Mbps" mode) while the sparkline, built from byte rates, looked fine.
            up, down = WidgetMetrics(
                upload_mbps=float(getattr(mw, "upload_speed", 0.0) or 0.0),
                download_mbps=float(getattr(mw, "download_speed", 0.0) or 0.0)).net_bytes()
            # The window's figures come from the last DB read (every 6 s to 90 s); what the user saw
            # live since then is part of the window too. Peak, scale and the last sparkline point
            # never read below it - until a read that includes those seconds replaces them.
            self._note_live(down, up)
            live_down = max((p[1] for p in self._live_peaks), default=0.0)
            live_up = max((p[2] for p in self._live_peaks), default=0.0)
            down_series = _raise_last(ser.get("down", []), live_down)
            up_series = _raise_last(ser.get("up", []), live_up)
            peak_v = max([v for v in down_series + up_series if v is not None], default=0.0)
            sd, su_ = (summ.get("down"), summ.get("up")) if summ is not None else (None, None)
            sub = ""
            if summ is None:   # the window's figures are still being read: say so, never a fake 0
                sub = self._tr("STATS_DETAIL_LOADING", "Reading history…")
            elif sd is not None and su_ is not None and sd.count:
                sub = (f"{self._tr('STAT_AVG_SHORT', 'avg')}  ↓ {self._fmt_speed(sd.avg or 0)}"
                       f"  ↑ {self._fmt_speed(su_.avg or 0)}      "
                       f"{self._tr('GRAPH_PEAK_SHORT', 'Peak')}  ↓ {self._fmt_speed(max(sd.max or 0, live_down))}"
                       f"  ↑ {self._fmt_speed(max(su_.max or 0, live_up))}")
            self._hero.set(self._fmt_speed(down), self._fmt_speed(up), down_series, up_series, sub,
                           scale_label=self._fmt_speed(peak_v), soft_last=True)
            self._hero.set_latency(self._latency_html(mw))

            # A discrete GPU has dedicated VRAM; an integrated one shares system RAM -> "iGPU".
            vu, vt = getattr(mw, "vram_used", None), getattr(mw, "vram_total", None)
            vram_shared = bool(getattr(mw, "vram_is_shared", False))
            integrated = (vu is None and vt is None) or vram_shared

            # Each utilization sparkline auto-zooms to its own active band (dynamic_range), so a
            # low-but-varying metric (e.g. CPU mostly 5-20%) reads in detail instead of as a flat
            # squiggle against a fixed 0-100% - with a minimum span so a steady metric isn't blown up.
            cpu_series = ser.get("cpu", [])
            cpu_lo, cpu_hi = dynamic_range(cpu_series)
            self._tiles["cpu"].set(f"{float(getattr(mw, 'cpu_usage', 0.0) or 0.0):.0f}%",
                                   cpu_series, vmax=cpu_hi, vmin=cpu_lo, soft_last=True,
                                   sub_text=self._hw_sub(getattr(mw, "cpu_temp", None),
                                                         getattr(mw, "cpu_power", None)))

            gpu_present = bool(getattr(mw, "gpu_present", True))
            if gpu_present:
                gpu_word = self._tr("ORDER_TYPE_GPU", "GPU")
                self._tiles["gpu"].set_label(("i" + gpu_word) if integrated else gpu_word)
                gpu_series = ser.get("gpu", [])
                gpu_lo, gpu_hi = dynamic_range(gpu_series)
                self._tiles["gpu"].set(f"{float(getattr(mw, 'gpu_usage', 0.0) or 0.0):.0f}%",
                                       gpu_series, vmax=gpu_hi, vmin=gpu_lo, soft_last=True,
                                       sub_text=self._hw_sub(getattr(mw, "gpu_temp", None),
                                                             getattr(mw, "gpu_power", None)))

            ru, rt = getattr(mw, "ram_used", None), getattr(mw, "ram_total", None)
            ram_series = ser.get("ram", [])
            ram_lo, ram_hi = dynamic_range(ram_series)
            self._tiles["ram"].set(f"{self._pct(ru, rt):.0f}%", ram_series, vmax=ram_hi, vmin=ram_lo, soft_last=True,
                                   sub_text=self._mem_sub(ru, rt))

            # VRAM is session-only for now (not persisted) - keeps its own rolling buffer. There's no
            # dedicated VRAM reading when it's None OR ~0 (an iGPU reports 0 dedicated VRAM).
            # An integrated GPU's shared-memory reading (vram_shared) is real however small.
            vram_reading = vu is not None and (vram_shared or float(vu) >= 0.05)
            self._tiles["vram"].set_label(self._tr("VRAM_SHARED_LABEL", "VRAM (shared)") if vram_shared
                                          else self._tr("MONITOR_TILE_VRAM", "VRAM"))
            if vram_shared != getattr(self, "_vram_series_shared", vram_shared):
                self._vram_series.clear()   # never mix shared-budget and dedicated percentages
            self._vram_series_shared = vram_shared
            if vram_reading:
                vpct = self._pct(vu, vt)
                self._vram_series.append(vpct)
                if vt:
                    vlo, vhi = dynamic_range(list(self._vram_series))
                    self._tiles["vram"].set(f"{vpct:.0f}%", list(self._vram_series),
                                            vmax=vhi, vmin=vlo, sub_text=self._mem_sub(vu, vt))
                else:
                    self._tiles["vram"].set(f"{float(vu):.1f} GB", [float(vu)],
                                            vmax=None, sub_text=self._mem_sub(vu, vt))

            # Show only the tiles that actually have data, reflowed to fill the width (no empty hole
            # where a missing GPU/VRAM tile would sit). Order preserved: CPU · GPU · RAM · VRAM.
            visible = [k for k in self._tile_order
                       if k in ("cpu", "ram")
                       or (k == "gpu" and gpu_present)
                       or (k == "vram" and vram_reading)]
            self._set_visible_tiles(visible)

            today, month = (mw._hover_usage_totals() if hasattr(mw, "_hover_usage_totals")
                            else ((0.0, 0.0), (0.0, 0.0)))
            cap = mw._hover_cap_info() if hasattr(mw, "_hover_cap_info") else None
            note = (self._tr("USAGE_RECORDED_HISTORY_NOTE", "")
                    if getattr(mw, "_hover_usage_reconstructed", False) else None)
            self._usage.set(today, month, cap, history_note=note or None,
                            period_start=getattr(mw, "_hover_usage_period_start", None),
                            coverage=getattr(self, "_cap_coverage", None))

            su.set_ui_text(self._session_lbl, self._session_text(mw))   # arrows not in every UI face
            self._render_health_strip(mw)
        except Exception as e:
            if not getattr(self, "_render_err_logged", False):
                self._render_err_logged = True
                self.logger.warning("Overview render failed (further at DEBUG): %s", e, exc_info=True)
            else:
                self.logger.debug("Overview render skipped: %s", e)

    def _note_live(self, down: float, up: float) -> None:
        """Record this tick's live rates (for the peak hold) and watch the link: a drop or a recovery
        schedules one window re-read OVERVIEW_LINK_CHANGE_RELOAD_MS later."""
        if down > 0 or up > 0:
            self._live_peaks.append((time.monotonic(), float(down), float(up)))
        lost = getattr(self._main_widget, "latency_link_lost", False) is True
        if self._last_link_lost is not None and lost != self._last_link_lost and not self._closed:
            self._link_timer.start()
        self._last_link_lost = lost

    def _session_text(self, mw) -> str:
        start = getattr(mw, "session_start_time", None)
        if start is None:
            return ""
        secs = max(0.0, (datetime.now() - start).total_seconds())
        parts = [f"{self._tr('STAT_SESSION_LABEL', 'Session')} {format_duration_short(secs, self._i18n)}"]
        # The volume comes from the reader thread's last window load (refreshed every few seconds),
        # never a DB read here: this runs on every 1 Hz render.
        v = self._session_volume
        if v is not None:
            try:
                dv, du = format_data_size(v.down_bytes, self._i18n, precision=1)
                uv, uu = format_data_size(v.up_bytes, self._i18n, precision=1)
                parts.append(f"↓ {self._num(dv)} {du}  ↑ {self._num(uv)} {uu}")
                if v.reconstructed:
                    # D5 - a session normally postdates the 2.2 upgrade (exact bytes), but a session
                    # whose upgrade is deferred reads the old file: say the total is estimated.
                    note = self._tr("USAGE_RECORDED_HISTORY_NOTE", "")
                    if note:
                        parts.append(note)
            except Exception:
                pass
        power = self._system_power_text(mw)
        if power and self._config.get("latency_enabled", True):
            parts.append(power)   # the right slot is the health line's: System power stays visible here
        return "    ·    ".join(parts)

    def _render_health_strip(self, mw) -> None:
        """The strip's right slot (health line, its tooltip / accessible description) and the internet
        offer beside it."""
        right, hop = self._context_right(mw)
        self._context_r_lbl.setText(right)
        tip = self._health_tooltip(hop) if right else ""
        self._context_r_lbl.setToolTip(tip)
        self._context_r_lbl.setAccessibleName(_plain_text(right))
        self._context_r_lbl.setAccessibleDescription(tip)
        self._context_r_lbl.setFocusPolicy(Qt.FocusPolicy.TabFocus if right else Qt.FocusPolicy.NoFocus)
        self._sync_internet_offer(hop, bool(right))

    def _system_power_text(self, mw) -> str:
        sysp = getattr(mw, "system_power", None)
        try:
            if sysp is not None and float(sysp) >= 0.5:
                return f"{self._tr('STAT_SYS_POWER_TRUE', 'System')} {float(sysp):.0f} W"
        except (TypeError, ValueError):
            pass
        return ""

    def _context_right_text(self, mw) -> str:
        """The strip's right slot: the window's connection health (drop count + last drop, named for
        the hop measured), so a latency blip you missed live is still on screen. With latency
        monitoring off (no health line) it shows true System power instead, when the platform exposes
        it (RAPL PSYS / battery discharge); with it on, System power sits in the left part
        (_session_text), never pushing the health line or its offer out (D5 review I)."""
        return self._context_right(mw)[0]

    def _context_right(self, mw):
        """(_context_right_text, the hop its health line is about - None when it shows something else)."""
        html, hop = self._connection_health(mw)
        if html:
            return html, hop
        if not self._config.get("latency_enabled", True):
            power = self._system_power_text(mw)
            if power:
                return power, None
        return "", None

    def _connection_health_html(self, mw) -> str:
        return self._connection_health(mw)[0]

    def _connection_health(self, mw):
        """Window connection-health: (html, hop) - drop count + last-drop time, and the hop it measured.
        Empty when latency monitoring is off or nothing is known yet.

        Named for what it measured (2.2.2 D5, "honesty beats green"): with the internet check off the
        drops are the ROUTER's ("Router: steady" - an ISP outage with Wi-Fi up is not seen, so it must
        not read "Connection: steady"); with it on, only internet-side drops count (#587), so
        "Internet: ...". There is no combined verdict here, so the line never says "Connection". The
        hop's name is drawn in the primary text colour, only the verdict in its colour. The internet
        line speaks only for what the internet ping covered: "checking…" until its first reply,
        "steady since HH:MM" when it started inside the window."""
        if not self._config.get("latency_enabled", True):
            return "", None
        ev = self._latency_events
        if ev is None:
            return "", None   # the window's drops are still being read: claim neither "steady" nor a count
        from netspeedtray.core.latency_probe import HOP_ANCHOR, headline_hop
        hop = headline_hop(bool(self._config.get("latency_public_enabled", False)))
        if ev.get("hop", hop) != hop:
            # Read for the other hop (the internet check was just switched) or while latency was off:
            # those drops are not this hop's. Say nothing until the re-read (asked for once) has them -
            # except the internet's "checking…", which is true from the first moment.
            if not self._health_hop_reload:
                self._health_hop_reload = True
                QTimer.singleShot(0, self._reload_for_hop)   # bound: dropped if the tab is gone
            if hop == HOP_ANCHOR and getattr(mw, "latency_anchor", None) is None:
                return self._checking_html(hop), hop
            return "", None
        self._health_hop_reload = False
        count = int(ev.get("count", 0) or 0)
        gw = getattr(mw, "latency_gw", None)
        anchor = getattr(mw, "latency_anchor", None)
        label = self._latency_label(hop)
        c = su.semantic_colors()
        colors = constants.network.latency.VERDICT_COLORS
        name = f"<span style='color:{c['text_primary']};'>{label}:</span> "
        if count == 0:
            if hop == HOP_ANCHOR:
                # The internet's "steady" needs the internet ping's own reply (never the router's) and
                # rows of its own in this window.
                if anchor is None or "rows" in ev and not ev.get("rows"):
                    return self._checking_html(hop), hop
            elif gw is None and anchor is None:
                return "", None   # only claim "no drops" once there is latency history
            word = self._tr("CONN_HEALTH_STEADY", "steady")
            since = self._covered_since(ev, hop)
            if since:
                word = self._tr("CONN_HEALTH_STEADY_SINCE", "steady since {time}").format(time=since)
            return name + f"<span style='color:{colors['good']};'>{word}</span>", hop
        last = ev.get("last_start")
        now = datetime.now()
        # Red means RECENT (the last drop within 24 h); an older one is a caution, whatever the count.
        dated = hasattr(last, "strftime")
        color = drop_color(last if dated else None, now)
        lt = drop_time_label(last, now, getattr(self._i18n, "language", None)) if dated else ""
        drops = self._tr("CONN_HEALTH_DROPS", "drops")
        out = name + f"<span style='color:{color};'>{count} {drops}</span>"
        if lt:
            out += f"  <span style='color:{c['text_secondary']};'>· {self._tr('STATS_DETAIL_LAST', 'last')} {lt}</span>"
        return out, hop

    def _checking_html(self, hop) -> str:
        sub = su.semantic_colors()["text_secondary"]
        return (f"<span style='color:{sub};'>{self._latency_label(hop)}: "
                f"{self._tr('CONN_HEALTH_CHECKING', 'checking…')}</span>")

    def _covered_since(self, ev, hop) -> str:
        """The internet line's "since" time when its first row is well inside the window (it was turned
        on part-way through) - empty when it covers the window, and always for the router."""
        from netspeedtray.core.latency_probe import HOP_ANCHOR
        first, start = ev.get("first"), ev.get("window_start")
        if hop != HOP_ANCHOR or not hasattr(first, "strftime") or not hasattr(start, "strftime"):
            return ""
        if (first - start).total_seconds() <= constants.network.latency.HEALTH_PARTIAL_SLACK_SEC:
            return ""
        return drop_time_label(first, datetime.now(), getattr(self._i18n, "language", None))

    def _health_tooltip(self, hop) -> str:
        """What the line measures and what a drop is (D5) - also its accessible description. While the
        internet line is still "checking…", where to turn it off."""
        from netspeedtray.core.latency_probe import HOP_ANCHOR, HOP_GATEWAY
        lat = constants.network.latency
        seconds = f"{lat.OUTAGE_MIN_CYCLES * lat.OUTAGE_CYCLE_SEC:g}"
        if hop == HOP_ANCHOR:
            text = self._tr("CONN_HEALTH_INTERNET_TOOLTIP",
                            "Checks the path to the internet test server, past your router and your internet "
                            "provider. A drop is counted when it doesn't answer for {seconds} seconds or more. "
                            "Older history is per minute, so short drops there are estimated.").format(
                seconds=seconds)
            if self._context_r_lbl.text() and self._tr("CONN_HEALTH_CHECKING", "checking…") in self._context_r_lbl.text():
                # Just turned on: what it checks, AND where to turn it off (combined review U7).
                text += "\n" + self._tr("INTERNET_CHECK_OFF_HINT", "Turn it off any time in Settings.")
            return text
        if hop == HOP_GATEWAY:
            return self._tr("CONN_HEALTH_ROUTER_TOOLTIP",
                            "Checks your router only, not your internet provider. A drop is counted when the "
                            "router doesn't answer for {seconds} seconds or more. Older history is per minute, "
                            "so short drops there are estimated.").format(seconds=seconds)
        return ""

    def _sync_internet_offer(self, hop, shown: bool) -> None:
        """The "Also check your internet" link sits beside a ROUTER health line only, while the offer
        stands (internet check off, not declined). An open tip closes once it no longer applies
        (turned on in Settings meanwhile)."""
        from netspeedtray.core.latency_probe import HOP_GATEWAY
        offer = self._internet_offer
        available = offer is not None and offer.available()
        tip = self._internet_tip
        if tip is not None and tip.isVisible() and not available:
            tip.close_for_action()
        self._internet_link.setVisible(bool(available and shown and hop == HOP_GATEWAY))

    # --- the internet offer's TeachingTip ------------------------------------------------------------
    def _open_internet_tip(self) -> None:
        offer = self._internet_offer
        if offer is None or not offer.available():
            return
        keyboard = self._internet_link.button().hasFocus()   # the link takes Tab focus only
        tip = self._internet_tip
        if tip is None:
            from netspeedtray.views.monitor.internet_offer import InternetOfferTip
            tip = InternetOfferTip(self._i18n, self)
            tip.confirm.turn_on_clicked.connect(self._internet_turn_on)
            tip.confirm.not_now_clicked.connect(self._internet_not_now)
            tip.dismissed.connect(self._internet_tip_dismissed)
            self._internet_tip = tip
        tip.confirm.set_host(offer.host())               # lazily: only an opened tip lays out its text
        tip.confirm.set_ends_offer(offer.ends_offer())
        tip.show_for(self._internet_link.button(), focus=keyboard)

    def _internet_turn_on(self) -> None:
        offer = self._internet_offer
        if self._internet_tip is not None:
            self._internet_tip.close_for_action()
        if offer is None or not offer.turn_on():
            self._focus_link_or_status()
            return
        # One forced re-read for the new hop: flagged first, so the render's own mismatch check does
        # not ask for a second one.
        self._health_hop_reload = True
        self._reload_window(force=True)
        self._refresh_strip()
        self._focus_status()

    def _internet_not_now(self) -> None:
        if self._internet_tip is not None:
            self._internet_tip.close_for_action()
        if self._internet_offer is not None:
            self._internet_offer.decline()
        self._refresh_strip()
        self._focus_link_or_status()

    def _refresh_strip(self) -> None:
        """Repaint the health line and the offer now (the rest follows on the next 1 Hz render)."""
        try:
            self._render_health_strip(self._main_widget)
        except Exception as e:
            self.logger.debug("Overview strip refresh skipped: %s", e)

    def _internet_tip_dismissed(self) -> None:
        """Esc / a click outside: closed, not declined - focus back on the link."""
        self._focus_link_or_status()

    def _focus_status(self) -> None:
        """Focus the health line - Narrator reads its new text ("Internet: checking…"). Deferred: the
        closing popup hands focus back to the window first."""
        QTimer.singleShot(0, self._do_focus_status)

    def _do_focus_status(self) -> None:
        if not self._closed and not sip.isdeleted(self):
            self.activateWindow()
            self._context_r_lbl.setFocus(Qt.FocusReason.OtherFocusReason)

    def _focus_link_or_status(self) -> None:
        QTimer.singleShot(0, self._do_focus_link_or_status)

    def _do_focus_link_or_status(self) -> None:
        if self._closed or sip.isdeleted(self):
            return
        if not self._internet_link.isHidden():
            self.activateWindow()
            self._internet_link.button().setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._do_focus_status()

    def _reload_for_hop(self) -> None:
        self._reload_window(force=True)

    def _export_window(self) -> None:
        """The header Export action - write the stats export (a single .zip) for the active timeline window."""
        ws = getattr(self._main_widget, "widget_state", None)
        if ws is None:
            return
        from netspeedtray import __version__
        from netspeedtray.views.monitor.stats_detail import run_interactive_export   # lazy (only on export)
        start, end, _is_session = self._window()
        label = self._timeline.current_label() if hasattr(self._timeline, "current_label") \
            else self._period_key()
        run_interactive_export(self.window(), ws, start, end, label, self._config, self._i18n, __version__)

    def _goto_network(self) -> None:
        win = self.window()
        if win is not None and hasattr(win, "select_tab"):
            try:
                win.select_tab("network")
            except Exception:
                pass

    def _open_settings(self) -> None:
        """Open Settings on the Network page, where the data-cap controls live - NOT General (index 0),
        which is where a bare show_settings() lands and where the headline data-cap feature looked broken."""
        try:
            self._main_widget.open_data_usage_settings()
        except Exception as e:
            self.logger.debug("Could not open data-usage settings from usage card: %s", e)

    def _num(self, value: float) -> str:
        s = f"{value:.1f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _latency_html(self, mw) -> str:
        """Latency pill: a color-coded plain word (Good/OK/Slow - the panel insisted avg-ms is a bad
        headline) with the ms + loss% as quiet subtext. Both follow the widget's HEADLINE hop: the
        internet anchor when the user opted into the public probe, the gateway otherwise
        (`latency_loss` is already that hop's loss; latency_probe.headline_hop is the rule).
        The ms is the hop's most recent successful RTT within its live window, so one lost ping does
        not flip the pill to "Slow"; the loss is None (no subtext) until the window is long enough.
        A lost connection (the latest sample's ``latency_link_lost``) reads "No connection" in the Slow
        colour at once - never the window's last good ms, which outlived a pulled cable by ~2 min."""
        label = self._latency_label(getattr(mw, "latency_headline_hop", None))
        if getattr(mw, "latency_link_lost", False) is True:
            color = constants.network.latency.VERDICT_COLORS["slow"]
            word = self._tr("LATENCY_NO_CONNECTION", "No connection")
            return f"<span style='color:{color};'>{label}: {word}</span>"
        gw = getattr(mw, "latency_gw", None)
        anchor = getattr(mw, "latency_anchor", None)
        raw_loss = getattr(mw, "latency_loss", None)
        loss_known = raw_loss is not None
        loss = float(raw_loss or 0.0)
        if getattr(mw, "latency_headline_hop", None) in ("anchor", "gateway"):
            # Never the router's ms under "Internet": this is the headline hop's own last good RTT.
            ms = getattr(mw, "latency_headline_ms", None)
        else:
            ms = anchor if anchor is not None else gw   # no hop reported (no sample yet / old caller)
        if ms is None and loss <= 0:
            return ""   # no probe data yet
        from netspeedtray.core.latency_probe import latency_verdict   # the rule the check reuses
        verdict = latency_verdict(ms, loss)
        word = self._tr(_VERDICT_KEYS[verdict][0], _VERDICT_KEYS[verdict][1])
        color = constants.network.latency.VERDICT_COLORS[verdict]
        sub = su.semantic_colors()["text_secondary"]
        out = f"<span style='color:{color};'>{label}: {word}</span>"
        detail = []
        if ms is not None:
            detail.append(f"{ms:.0f} ms")
        if loss_known and loss > 0:
            # Bursts resolve 0.6% steps: below 1% keep the decimal, or a "Good" verdict would read
            # "1% loss" - the very figure that makes it "OK".
            pct = self._num(loss) if loss < 1 else f"{loss:.0f}"   # _num: the locale's separator
            detail.append(self._tr("MONITOR_LATENCY_LOSS_FMT", "{pct}% loss").format(pct=pct))
        if detail:
            out += f"  <span style='color:{sub};'>· {' · '.join(detail)}</span>"
        return out

    def _latency_label(self, hop=None) -> str:
        """The name of the hop a latency figure measures, in the Check-connection sheet's words:
        "Router" for the gateway (the default - public probe off), "Internet" for the public anchor.
        ``hop`` is the widget's reported headline hop; None falls back to the configured rule."""
        from netspeedtray.core.latency_probe import HOP_ANCHOR, HOP_GATEWAY, headline_hop
        if hop not in (HOP_ANCHOR, HOP_GATEWAY):
            hop = headline_hop(bool(self._config.get("latency_public_enabled", False)))
        if hop == HOP_ANCHOR:
            return self._tr("LATENCY_LABEL", "Internet")
        return self._tr("CHECK_CONN_ROUTER", "Router")

    def _latency_detail_key(self) -> str:
        """The latency series the Router / Internet block describes - the HEADLINE hop, like the pill: the
        public anchor when the user opted into it, the gateway otherwise (latency_probe.headline_hop)."""
        from netspeedtray.core.latency_probe import HOP_ANCHOR, headline_hop
        hop = headline_hop(bool(self._config.get("latency_public_enabled", False)))
        return "latency_anchor" if hop == HOP_ANCHOR else "latency_gw"

    def _detail_subjects(self, metric: str):
        """The subject list a card opens its Stats-detail sheet with. Hardware cards lead with their
        utilization and add temperature/power when a sensor reported them; network leads with both
        directions plus the headline hop's latency. (Empty secondary blocks drop out inside the sheet.)"""
        cpu = self._tr("ORDER_TYPE_CPU", "CPU")
        gpu = self._tr("ORDER_TYPE_GPU", "GPU")
        if metric == "network":
            return [
                {"key": "download", "label": self._tr("DOWNLOAD_LABEL", "Download"),
                 "unit": "Mbps", "kind": "net_down", "primary": True},
                {"key": "upload", "label": self._tr("UPLOAD_LABEL", "Upload"),
                 "unit": "Mbps", "kind": "net_up", "primary": True},
                {"key": self._latency_detail_key(), "label": self._latency_label(),
                 "unit": "ms", "kind": "hw"},
            ]
        if metric == "cpu":
            return [
                {"key": "cpu", "label": cpu, "unit": "%", "kind": "hw", "primary": True},
                {"key": "cpu_temp", "label": f"{cpu} {self._tr('STAT_TEMP', 'temperature')}",
                 "unit": "°C", "kind": "hw"},
                {"key": "cpu_power", "label": f"{cpu} {self._tr('STAT_POWER', 'power')}",
                 "unit": "W", "kind": "hw"},
            ]
        if metric == "gpu":
            return [
                {"key": "gpu", "label": gpu, "unit": "%", "kind": "hw", "primary": True},
                {"key": "gpu_temp", "label": f"{gpu} {self._tr('STAT_TEMP', 'temperature')}",
                 "unit": "°C", "kind": "hw"},
                {"key": "gpu_power", "label": f"{gpu} {self._tr('STAT_POWER', 'power')}",
                 "unit": "W", "kind": "hw"},
            ]
        if metric == "ram":
            return [{"key": "ram", "label": self._tr("MONITOR_TILE_RAM", "RAM"),
                     "unit": "%", "kind": "hw", "primary": True}]
        if metric == "vram":
            shared = bool(getattr(self._main_widget, "vram_is_shared", False))
            return [{"key": "vram", "label": (self._tr("VRAM_SHARED_LABEL", "VRAM (shared)") if shared
                                              else self._tr("MONITOR_TILE_VRAM", "VRAM")),
                     "unit": "%", "kind": "hw", "primary": True}]
        return []

    def _open_detail(self, metric: str) -> None:
        """Open the Stats-detail sheet for the clicked card, scoped to the active timeline window."""
        ws = getattr(self._main_widget, "widget_state", None)
        if ws is None:
            return
        subjects = self._detail_subjects(metric)
        if not subjects:
            return
        from netspeedtray import __version__
        from netspeedtray.views.monitor.stats_detail import StatsDetailSheet   # lazy (only on open)
        start, end, _is_session = self._window()
        label = self._timeline.current_label() if hasattr(self._timeline, "current_label") \
            else self._period_key()
        sheet = None
        try:
            sheet = StatsDetailSheet(ws, subjects, (start, end, label), self._config, self._i18n,
                                     app_version=__version__, parent=self.window())
            sheet.exec()
        except Exception as e:
            self.logger.error("Could not open stats detail: %s", e, exc_info=True)
        finally:
            # Parented to the Monitor window, so without this every opened sheet stayed alive as its
            # child until the Monitor closed. exec() has stopped the sheet's reader thread by now.
            # Quitting the app with the sheet open closes (and deletes) the Monitor - and the sheet with
            # it - while exec() still runs; deleteLater() on the dead wrapper then raised out of this
            # click handler and the app logged it as CRITICAL on the way out.
            if sheet is not None and not sip.isdeleted(sheet):
                sheet.deleteLater()

    def _open_usage_history(self) -> None:
        """The usage card's "Usage by day" link - the per-day / month / year table (#334)."""
        mw = self._main_widget
        ws = getattr(mw, "widget_state", None)
        if ws is None:
            return
        from netspeedtray.views.monitor.usage_history_sheet import UsageHistorySheet

        def period_ceiling():
            # The usage card's own rule with a cap set: its period row IS the odometer, and Today never
            # reads above it. (period start, (up, down)), else None.
            cap_set = getattr(mw, "_usage_cap_set", None)
            if cap_set is None or not cap_set():
                return None
            return date.fromisoformat(ws.get_usage_period_key()), tuple(ws.get_usage_this_period())

        sheet = None
        try:
            sheet = UsageHistorySheet(ws, getattr(mw, "config", None) or self._config, self._i18n,
                                      period_ceiling=period_ceiling,
                                      parent=self.window())
            sheet.exec()
        except Exception as e:
            self.logger.error("Could not open usage history: %s", e, exc_info=True)
        finally:
            if sheet is not None and not sip.isdeleted(sheet):   # as _open_detail
                sheet.deleteLater()

    def _open_connection_check(self) -> None:
        """The hero's "Check connection" action - the one-shot report sheet (2.2 D3)."""
        from netspeedtray.views.monitor.connection_check import open_connection_check
        open_connection_check(self._main_widget, self._config, self._i18n, parent=self.window())

    def _hw_sub(self, temp: Optional[float], power: Optional[float]) -> str:
        """CPU/GPU sub-line: temperature and (when collected) power, e.g. "62°C  ·  35 W"."""
        parts = []
        if temp is not None and float(temp) >= 1:
            from netspeedtray.utils.helpers import format_temperature, temperature_unit
            parts.append(format_temperature(float(temp), temperature_unit(self._config)))
        # >= 0.5 W, not > 0: a flaky iGPU power reading of 0.3 W rounds to "0 W" and then vanishes,
        # which would make the sub-line flicker - only show power that actually displays as >= 1 W.
        if power is not None and float(power) >= 0.5:
            parts.append(f"{float(power):.0f} W")
        return "  ·  ".join(parts)

    def _mem_sub(self, used: Optional[float], total: Optional[float]) -> str:
        if used is None:
            return ""
        if total:
            return f"{float(used):.1f} / {float(total):.1f} GB"
        return f"{float(used):.1f} GB"

    @staticmethod
    def _pct(used: Optional[float], total: Optional[float]) -> float:
        if used and total and total > 0:
            return max(0.0, min(100.0, (used / total) * 100.0))
        return 0.0

    def _fmt_speed(self, bps: float) -> str:
        cfg = self._config
        return format_speed(
            bps, self._i18n,
            force_mega_unit=(cfg.get("speed_display_mode") == "always_mbps"),
            decimal_places=int(cfg.get("decimal_places", 1)),
            unit_type=cfg.get("unit_type", "bits_decimal"),
            short_labels=cfg.get("short_unit_labels", False),
        )

    def _tr(self, key: str, default: str) -> str:
        from netspeedtray.utils.helpers import tr
        return tr(self._i18n, key, default)
