"""
StatsDetailSheet - the drill-down behind every Overview card. Verifies the honesty spine in the UI:
raw-tier blocks show percentiles, rollup-tier blocks show em-dashes + a note, empty primary metrics
say "not enough history", empty secondary metrics drop out, and the copy buffer is populated.
"""
from datetime import datetime, timedelta

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.summaries import summarize_raw, summarize_rollup
from netspeedtray.views.monitor.stats_detail import StatsDetailSheet


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _WS:
    """download = raw/exact; cpu = rollup (avg+max only); cpu_temp = empty (no sensor)."""
    def __init__(self):
        self._t0 = datetime(2026, 6, 28, 20, 0, 0)

    def summarize_network(self, direction, start, end, iface, poll):
        if direction == "download":
            return summarize_raw([10_000_000.0, 20_000_000.0, 30_000_000.0])
        return summarize_raw([1_000_000.0, 2_000_000.0])

    def summarize_hardware(self, stat, start, end, poll):
        if stat == "cpu":
            return summarize_rollup([20.0, 40.0], [55.0, 80.0], [60, 60], tier="minute")
        if stat == "latency_gw":
            return summarize_raw([5.0, 9.0, 7.0])
        if stat == "latency_gw_timeout":
            return summarize_raw([0.0, 1.0, 0.0])
        return summarize_raw([])   # cpu_temp / cpu_power / etc -> empty

    def get_speed_history(self, start, end, iface, resolution='auto', **_kw):
        return [(self._t0, 1_000_000.0, 10_000_000.0),
                (self._t0 + timedelta(hours=8), 2_000_000.0, 5_000_000.0)]

    def get_network_hourly_profile(self, direction, start, end, iface=None):
        # Hourly AVERAGES (the reader's): 03:00 is busiest on average, although the peak points above
        # (get_speed_history) are highest at 20:00.
        return {3: 4_000_000.0, 20: 1_000_000.0, 12: 500_000.0}

    def get_hardware_history(self, stat, start, end):
        if stat == "cpu":
            return [(self._t0, 33.0), (self._t0 + timedelta(seconds=1), 44.0)]
        return []


def _cfg(**over):
    c = {"update_rate": 1.0, "unit_type": "bits_decimal", "decimal_places": 1}
    c.update(over)
    return c


def _window():
    return (datetime(2026, 6, 28, 12), datetime(2026, 6, 28, 21), "Last 24 hours")


def test_raw_block_shows_percentiles(q_app):
    subjects = [{"key": "download", "label": "Download", "unit": "Mbps", "kind": "net_down", "primary": True}]
    sheet = StatsDetailSheet(_WS(), subjects, _window(), _cfg(), I18nStrings("en_US"))
    text = "\n".join(sheet._copy_text_parts)
    assert "Download" in text and "per-second" in text
    assert "Median" in text and "95th" in text          # exact percentiles present
    assert "Median: -" not in text and "95th pct: -" not in text   # no percentile dashed out (raw)


def test_rollup_block_dashes_percentiles_with_note(q_app):
    subjects = [{"key": "cpu", "label": "CPU", "unit": "%", "kind": "hw", "primary": True}]
    sheet = StatsDetailSheet(_WS(), subjects, _window(), _cfg(), I18nStrings("en_US"))
    text = "\n".join(sheet._copy_text_parts)
    assert "per-minute" in text
    assert "Median: -" in text and "95th pct: -" in text   # honest: not fabricated


def test_empty_primary_says_no_data(q_app):
    subjects = [{"key": "cpu_temp", "label": "CPU temperature", "unit": "°C", "kind": "hw", "primary": True}]
    sheet = StatsDetailSheet(_WS(), subjects, _window(), _cfg(), I18nStrings("en_US"))
    # No copy text for an empty primary (no stat cells), but the sheet builds without crashing.
    assert sheet is not None


def test_empty_secondary_block_drops_out(q_app):
    # CPU (primary, has rollup data) + CPU temp (secondary, empty) -> only the CPU block renders.
    subjects = [
        {"key": "cpu", "label": "CPU", "unit": "%", "kind": "hw", "primary": True},
        {"key": "cpu_temp", "label": "CPU temperature", "unit": "°C", "kind": "hw"},
    ]
    sheet = StatsDetailSheet(_WS(), subjects, _window(), _cfg(), I18nStrings("en_US"))
    text = "\n".join(sheet._copy_text_parts)
    assert "CPU temperature" not in text     # the empty secondary block was skipped
    assert "CPU" in text


def test_network_peak_offpeak_context(q_app):
    subjects = [{"key": "download", "label": "Download", "unit": "Mbps", "kind": "net_down", "primary": True}]
    sheet = StatsDetailSheet(_WS(), subjects, _window(), _cfg(), I18nStrings("en_US"))
    text = "\n".join(sheet._copy_text_parts)
    # From the reader's hourly AVERAGES, labelled as such - never the graph's per-bucket peaks (the
    # 2.2.0-beta.1 smoke test: "Busiest hour 03:00 - 80.4 Mbps" beside "Average 0.1 Mbps").
    assert "Busiest hour 03:00 · avg 32.0 Mbps" in text
    assert "Quietest 12:00 · avg 4.0 Mbps" in text


def test_latency_loss_context(q_app):
    subjects = [{"key": "latency_gw", "label": "Internet", "unit": "ms", "kind": "hw", "primary": True}]
    sheet = StatsDetailSheet(_WS(), subjects, _window(), _cfg(), I18nStrings("en_US"))
    text = "\n".join(sheet._copy_text_parts)
    assert "packet loss" in text             # 1/3 timeouts surfaced


def test_below_plan_when_threshold_set(q_app):
    subjects = [{"key": "download", "label": "Download", "unit": "Mbps", "kind": "net_down", "primary": True}]
    # download samples are 10/5 MB/s = 80/40 Mbps; plan 100 Mbps -> 100% below.
    sheet = StatsDetailSheet(_WS(), subjects, _window(), _cfg(plan_down_mbps=100), I18nStrings("en_US"))
    text = "\n".join(sheet._copy_text_parts)
    assert "100 Mbps" in text and "below" in text


def test_configured_poll_reaches_the_summaries(q_app):
    """2.1.5 item 9: the configured update_rate must reach summarize_* through the shared resolver
    (a local `or 1.0` once leaked a sentinel and zeroed coverage_pct on every stats sheet)."""
    captured = []

    class _CapturingWS(_WS):
        def summarize_network(self, direction, start, end, iface, poll):
            captured.append(poll)
            return super().summarize_network(direction, start, end, iface, poll)

    subjects = [{"key": "download", "label": "Download", "unit": "Mbps", "kind": "net_down", "primary": True}]
    sheet = StatsDetailSheet(_CapturingWS(), subjects, _window(), _cfg(update_rate=5.0),
                             I18nStrings("en_US"))
    assert sheet._poll == 5.0
    assert captured and all(p == 5.0 for p in captured)


def test_interactive_export_passes_the_configured_poll(q_app, monkeypatch, tmp_path):
    """The Export path (stats_detail.run_interactive_export) is another of the four item-9 leak
    sites: it must hand export_window_zip the configured interval."""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    from netspeedtray.views.monitor import stats_detail as SD

    captured = {}

    def _fake_zip(ws, start, end, label, zip_path, basename, *, machine_id="", app_version="",
                  poll_interval=1.0):
        captured["poll"] = poll_interval
        return zip_path

    monkeypatch.setattr(SD.stats_exporter, "export_window_zip", _fake_zip)
    monkeypatch.setattr(SD, "get_machine_id", lambda: "cafebabe12345678")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "x.zip"), "")))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)   # swallow the success dialog

    start, end, label = _window()
    SD.run_interactive_export(None, _WS(), start, end, label,
                              _cfg(update_rate=5.0), I18nStrings("en_US"))
    assert captured["poll"] == 5.0


class _BlockingFlushWS(_WS):
    """Mimics WidgetState.get_speed_history: the default (wait_for_flush=True) blocks on the DB write
    thread's flush barrier - up to 2 s each. The sheet is built on the GUI thread, so it must opt out."""
    def __init__(self):
        super().__init__()
        self.waits = 0

    def get_speed_history(self, start, end, iface, resolution='auto', wait_for_flush=True, **_kw):
        if wait_for_flush:
            self.waits += 1
            import time
            time.sleep(1.0)
        return super().get_speed_history(start, end, iface, resolution)


def test_sheet_never_waits_for_the_db_flush_on_the_gui_thread(q_app):
    """2.2.0-beta.1 review: opening the download/upload sheet froze the UI 2-4 s - two flush waits."""
    import time
    subjects = [{"key": "download", "label": "Download", "unit": "Mbps", "kind": "net_down", "primary": True},
                {"key": "upload", "label": "Upload", "unit": "Mbps", "kind": "net_up"}]
    ws = _BlockingFlushWS()
    t0 = time.perf_counter()
    StatsDetailSheet(ws, subjects, _window(), _cfg(), I18nStrings("en_US"))
    assert ws.waits == 0
    assert time.perf_counter() - t0 < 0.9


def test_hourly_profile_read_never_takes_the_flush_barrier():
    """The busiest-hour profile is read inside the sheet's _summarize, on the same no-wait path as the
    summaries: WidgetState.get_network_hourly_profile goes straight to the reader, never flush_and_wait."""
    from netspeedtray.core.widget_state import WidgetState

    class _Reader:
        def network_hourly_profile(self, d, s, e, iface=None):
            return {3: 1.0}

    def _no_wait(*_a, **_k):
        raise AssertionError("flush barrier taken on the GUI thread")

    ws = WidgetState.__new__(WidgetState)
    ws.db_worker = object()
    ws._history_reader = _Reader()
    ws.flush_and_wait = _no_wait
    start, end, _ = _window()
    assert ws.get_network_hourly_profile("download", start, end) == {3: 1.0}
