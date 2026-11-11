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
* the mini-graph buffer keeps its memory cap (it covers less time at 0.5 s, it never grows).
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


# --- the mini graph -------------------------------------------------------------------------

def test_graph_buffer_keeps_its_memory_cap_at_half_a_second():
    fake = SimpleNamespace(config={"update_rate": 0.5}, logger=logging.getLogger("t"))
    assert WidgetState._get_graph_buffer_points(fake) == 5000           # capped, never larger
    fake.config["update_rate"] = 1.0
    assert WidgetState._get_graph_buffer_points(fake) == 5000


def test_graph_window_counts_half_second_samples():
    from netspeedtray.utils.widget_renderer import RenderConfig
    rc = RenderConfig.from_dict({**constants.config.defaults.DEFAULT_CONFIG,
                                 "update_rate": 0.5, "history_minutes": 3})
    assert rc.max_samples == 360                      # 3 min of 0.5 s samples
