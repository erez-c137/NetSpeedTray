"""
#456 (2.2.1): the opt-in 0.5 s update rate. 1.2.4 had it, 1.2.6 dropped it; 1 s stays the default.

The preset ladder, the migration and the slider are pinned in test_update_rate_presets.py (its PRESETS
now starts at 0.5). This file pins the places a sub-second rate reaches beyond the ladder:

* every locale labels the new slider position, with its own decimal separator;
* the display spike filter keeps a 20 s baseline at 0.5 s (40 polls), not a 10 s one;
* a 0.5 s poll is not discarded by the controller's half-interval gate and yields a real rate;
* history stays one net_raw row per interface per second (two 0.5 s intervals merge) and a 0.5 s
  poll is not a gap;
* hardware coverage at 0.5 s reads ~100 %, not 50 % (hw_raw keeps one row per metric per second);
* the in-memory graph series (mini graph, Monitor session view) hold one point per wall-clock
  second at 0.5 s - the average of that second's samples - so the graph window, the 5000-point cap
  and the points per window are the same as at 1 s, while the widget's numbers update every 0.5 s.
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode
from netspeedtray.core.controller import StatsController
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db import writer_v8 as W
from netspeedtray.core.db.history_reader import V8HistoryReader
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.unit.v8_twin_fixture import fixed_now

SRC = Path(__file__).resolve().parents[2]          # src/netspeedtray
LOCALES = SRC / "constants" / "locales"
NOW = int(fixed_now().timestamp())


def test_half_second_is_an_opt_in_preset_and_one_second_stays_the_default():
    assert UpdateMode.PRESETS[0] == UpdateMode.REALTIME == 0.5
    assert constants.config.defaults.DEFAULT_UPDATE_RATE == 1.0
    assert constants.config.defaults.DEFAULT_CONFIG["update_rate"] == 1.0
    # The monitor thread's own floor (set_interval / MINIMUM_INTERVAL_MS) is below the new preset.
    assert constants.timers.MINIMUM_INTERVAL_MS / 1000.0 < UpdateMode.REALTIME


# --- the slider label, in every locale --------------------------------------------------------

@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_every_locale_labels_the_half_second_position(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    label = data.get("UPDATE_MODE_REALTIME_LABEL")
    assert label, f"{path.name} has no UPDATE_MODE_REALTIME_LABEL"
    # "0.5" written the locale's way: "0,5" where the decimal separator is a comma.
    sep = data["DECIMAL_SEPARATOR"]
    assert sep in label, f"{path.name}: {label!r} does not use the locale decimal separator {sep!r}"
    other = "," if sep == "." else "."
    assert f"0{other}5" not in label


def test_slider_fallback_label_keeps_the_fraction():
    """Without a translation the label is built from the preset: 0.5 s must not print as "0s"."""
    from netspeedtray.views.settings.pages.general import GeneralPage
    page = SimpleNamespace(i18n=SimpleNamespace())
    assert GeneralPage._format_update_rate_label(page, 0) == "0.5s"
    assert GeneralPage._format_update_rate_label(page, 4) == "10s"


# --- the spike filter's baseline is a time window at sub-second rates -------------------------

@pytest.mark.parametrize("rate, polls", [(0.5, 40), (1.0, 20), (2.0, 20), (5.0, 20), (10.0, 20)])
def test_spike_baseline_spans_twenty_seconds_at_half_a_second(rate, polls):
    assert StatsController._spike_baseline_len(rate) == polls


class _NetIO:
    def __init__(self, sent, recv):
        self.bytes_sent, self.bytes_recv = sent, recv


def _controller(rate):
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    cfg.update({"update_rate": rate, "interface_mode": "all_physical"})
    c = StatsController(config=cfg, widget_state=MagicMock(spec=WidgetState))
    c.set_view(MagicMock())
    c.primary_interface = None
    return c


def _poll(c, t, sent, recv):
    with patch("time.monotonic", return_value=t):
        c._handle_network_counters({"Ethernet": _NetIO(sent, recv)})


def test_half_second_polls_produce_rates_and_a_forty_poll_baseline():
    c = _controller(0.5)
    c.last_check_time = 1000.0
    c.last_interface_counters = {"Ethernet": _NetIO(0, 0)}
    _poll(c, 1000.5, 500, 5_000)
    # 500 B up / 5000 B down over 0.5 s: 1000 / 10000 B/s - a real rate, not a dropped poll.
    assert c.live_rates[2] == (1000, 10000)
    assert c.recent_speeds["Ethernet"].maxlen == 40


def test_a_rate_change_resizes_the_baseline_and_keeps_the_newest_samples():
    c = _controller(1.0)
    c.last_check_time = 1000.0
    c.last_interface_counters = {"Ethernet": _NetIO(0, 0)}
    t, sent = 1000.0, 0
    for _ in range(25):
        t, sent = t + 1.0, sent + 1_000
        _poll(c, t, sent, sent)
    assert c.recent_speeds["Ethernet"].maxlen == 20 and len(c.recent_speeds["Ethernet"]) == 20
    c.config["update_rate"] = 0.5
    t, sent = t + 0.5, sent + 500
    _poll(c, t, sent, sent)
    dq = c.recent_speeds["Ethernet"]
    assert dq.maxlen == 40 and len(dq) == 21          # the 20 kept, plus this poll
    c.config["update_rate"] = 2.0
    t, sent = t + 2.0, sent + 2_000
    _poll(c, t, sent, sent)
    assert c.recent_speeds["Ethernet"].maxlen == 20   # back to the polls-based window


# --- history: one row per second, no gap flag ------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "speed_history.db")
    S.create_v8_schema(c)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    yield c
    c.close()


def test_half_second_samples_merge_into_one_raw_row_per_second_and_are_not_gaps(conn):
    w = W.WriterV8(conn, version="test", poll_ms=500)
    up = down = 0
    for i in range(20):                               # 10 s at 0.5 s: two intervals end in each second
        ts = NOW + 0.25 + i * 0.5
        w.add_net_sample(W.NetSample(ts, 500, {"Wi-Fi 3": (100 + i, 1000 + i)}))
        up, down = up + 100 + i, down + 1000 + i
    assert w.flush(NOW + 11)
    rows = conn.execute("SELECT ts, up_bytes, down_bytes, dt_ms, flags FROM net_raw ORDER BY ts").fetchall()
    assert len(rows) == 10                            # one per second, not one per poll
    assert all(r[3] == 1000 and r[4] & S.FLAG_GAP == 0 for r in rows)
    assert (sum(r[1] for r in rows), sum(r[2] for r in rows)) == (up, down)   # nothing dropped
    assert W.gap_threshold_ms(500) == constants.network.speed.RATE_VALIDITY_MIN_SECONDS * 1000


def test_hardware_coverage_at_half_a_second_is_not_halved(conn, tmp_path):
    """Two CPU samples per second at 0.5 s; hw_raw keeps the first of each second. The Stats sheet /
    export coverage must still read ~100 % for a fully sampled window, not 50 %."""
    w = W.WriterV8(conn, version="test", poll_ms=500)
    start = NOW - 120
    for i in range(240):                              # 120 s at 0.5 s
        w.add_hw_sample(start + i * 0.5, "cpu", 10.0 + (i % 7))
    assert w.flush(NOW)
    assert conn.execute("SELECT COUNT(*) FROM hw_raw").fetchone()[0] == 120
    db = tmp_path / "speed_history.db"
    rc = sqlite3.connect(db)
    try:
        r = V8HistoryReader(lambda: rc, lambda: db, logging.getLogger("t"), clock=lambda: NOW)
        s = r.summarize_hardware("cpu", datetime.fromtimestamp(start), datetime.fromtimestamp(NOW), 0.5)
        assert s.count == 120
        assert s.coverage_pct >= 99.0, s.coverage_pct
        # A slower preset is unchanged: at 2 s, 120 one-second rows over 120 s is still capped at 100 %.
        assert r.summarize_hardware("cpu", datetime.fromtimestamp(start), datetime.fromtimestamp(NOW),
                                    2.0).coverage_pct == 100.0
    finally:
        rc.close()


# --- the mini graph: one point per second at 0.5 s -------------------------------------------

class _Series:
    """WidgetState's graph-series feeders on a plain object (no DB worker, no QObject)."""
    _append_graph_point = getattr(WidgetState, "_append_graph_point", None)   # None on pre-#456 code
    add_speed_data = WidgetState.add_speed_data
    add_hardware_stat = WidgetState.add_hardware_stat

    def __init__(self, rate, maxlen=5000):
        from collections import deque
        self.config = {"update_rate": rate}
        self.aggregated_history = deque(maxlen=maxlen)
        self.cpu_history, self.gpu_history, self.ram_history = (deque(maxlen=maxlen) for _ in range(3))


def _feed(series, start, step, n, up=lambda i: 1000.0 * i, cpu=lambda i: float(i % 100)):
    for i in range(n):
        ts = datetime.fromtimestamp(start + i * step)
        series.add_speed_data({}, now=ts, aggregated_up=up(i), aggregated_down=2 * up(i))
        series.add_hardware_stat("cpu", cpu(i), now=ts, persist=False)


def _span_s(series):
    return (series[-1].timestamp - series[0].timestamp).total_seconds()


def test_graph_points_per_window_are_the_same_at_half_a_second_as_at_one():
    from netspeedtray.utils.widget_renderer import RenderConfig
    for minutes in (3, 30, 120):
        cfg = {**constants.config.defaults.DEFAULT_CONFIG, "history_minutes": minutes}
        half = RenderConfig.from_dict({**cfg, "update_rate": 0.5}).max_samples
        one = RenderConfig.from_dict({**cfg, "update_rate": 1.0}).max_samples
        assert half == one == minutes * 60
    fake = SimpleNamespace(config={"update_rate": 0.5}, logger=logging.getLogger("t"))
    half_buf = WidgetState._get_graph_buffer_points(fake)
    fake.config["update_rate"] = 1.0
    assert half_buf == WidgetState._get_graph_buffer_points(fake) == 5000   # same cap, never larger


def test_half_second_samples_become_one_graph_point_per_second():
    s = _Series(0.5)
    _feed(s, NOW + 0.25, 0.5, 600)                    # 300 s of 0.5 s samples, two per second
    assert len(s.aggregated_history) == len(s.cpu_history) == 300
    # Each point is the plain average of its second's two samples (rates and percentages alike).
    p = s.aggregated_history[10]                      # samples 20 and 21
    assert (p.upload, p.download) == (pytest.approx(20_500.0), pytest.approx(41_000.0))
    assert s.cpu_history[10].value == pytest.approx(20.5)
    # Stamped with the newest sample of its second.
    assert p.timestamp == datetime.fromtimestamp(NOW + 0.25 + 21 * 0.5)
    # The same number of points as 1 s sampling over the same time: the 5 min window is 5 min.
    one = _Series(1.0)
    _feed(one, NOW + 0.25, 1.0, 300)
    assert len(one.aggregated_history) == 300
    assert _span_s(s.aggregated_history) == pytest.approx(_span_s(one.aggregated_history), abs=1.0)


def test_full_buffer_spans_the_same_time_at_half_a_second_as_at_one():
    """The 5000-point buffer: ~83 min of history at 1 s, and the SAME at 0.5 s (was ~42 min)."""
    half, one = _Series(0.5, maxlen=5000), _Series(1.0, maxlen=5000)
    _feed(half, NOW + 0.25, 0.5, 12_000)              # 6000 s at 0.5 s
    _feed(one, NOW + 0.25, 1.0, 6_000)                # 6000 s at 1 s
    assert len(half.aggregated_history) == len(one.aggregated_history) == 5000
    assert _span_s(half.aggregated_history) == pytest.approx(_span_s(one.aggregated_history), abs=1.0)
    assert _span_s(half.cpu_history) >= 4998


def test_the_open_second_is_shown_and_then_finalised():
    s = _Series(0.5)
    t = NOW + 0.1
    s.add_speed_data({}, now=datetime.fromtimestamp(t), aggregated_up=100.0, aggregated_down=10.0)
    assert [(p.upload, p.download) for p in s.aggregated_history] == [(100.0, 10.0)]   # shown at once
    s.add_speed_data({}, now=datetime.fromtimestamp(t + 0.5), aggregated_up=300.0, aggregated_down=30.0)
    assert [(p.upload, p.download) for p in s.aggregated_history] == [(200.0, 20.0)]   # replaced: average
    s.add_speed_data({}, now=datetime.fromtimestamp(t + 1.0), aggregated_up=50.0, aggregated_down=5.0)
    assert [(p.upload, p.download) for p in s.aggregated_history] == [(200.0, 20.0), (50.0, 5.0)]


@pytest.mark.parametrize("rate", [1.0, 2.0, 10.0])
def test_one_second_and_slower_keep_every_sample(rate):
    """Unchanged at 1 s and slower: two samples in one wall-clock second (a stall, then a catch-up)
    stay two points - merging is only the 0.5 s preset's."""
    s = _Series(rate)
    s.add_speed_data({}, now=datetime.fromtimestamp(NOW + 0.1), aggregated_up=1.0, aggregated_down=1.0)
    s.add_speed_data({}, now=datetime.fromtimestamp(NOW + 0.9), aggregated_up=3.0, aggregated_down=3.0)
    assert [p.upload for p in s.aggregated_history] == [1.0, 3.0]
