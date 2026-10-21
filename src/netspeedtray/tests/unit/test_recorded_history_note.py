"""
D5 labelling beyond the hover card: wherever a total is shown that is partly estimated from speeds
recorded before 2.2, the "From recorded history" note goes with it - the Monitor's Network-tab period
totals (graph worker -> GraphHost.network_totals_ready -> NetworkHeader) and the Overview session strip.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.db.history_reader import VolumeResult
from netspeedtray.views.graph.request import DataRequest
from netspeedtray.views.graph.worker import GraphDataWorker

T0 = datetime(2026, 9, 1)


def _db_request(seq=7):
    return DataRequest(start_time=T0, end_time=T0 + timedelta(days=30), interface_name=None,
                       is_session_view=False, sequence_id=seq, stat_type="network")


def _ws(reconstructed):
    ws = MagicMock()
    ws.get_speed_history.return_value = [(T0 + timedelta(hours=i), 1.0, 2.0) for i in range(5)]
    ws.get_history_volume_for_period.return_value = VolumeResult(3e9, 9e9, 1e9, 2e9, reconstructed)
    return ws


@pytest.mark.parametrize("reconstructed", [True, False])
def test_worker_reports_whether_its_totals_are_estimated(q_app, reconstructed):
    w = GraphDataWorker(_ws(reconstructed))
    events = []
    w.totals_provenance.connect(lambda seq, recon: events.append(("prov", seq, recon)))
    w.data_ready.connect(lambda data, up, down, seq: events.append(("data", seq, up, down)))
    w.process_data(_db_request())
    assert events == [("prov", 7, reconstructed), ("data", 7, 3e9, 9e9)]


def test_worker_live_session_totals_are_never_estimated(q_app):
    ws = MagicMock()
    ws.get_aggregated_speed_history.return_value = [
        SimpleNamespace(timestamp=T0 + timedelta(seconds=i), upload=1.0, download=2.0) for i in range(5)]
    w = GraphDataWorker(ws)
    prov = []
    w.totals_provenance.connect(lambda seq, recon: prov.append(recon))
    w.process_data(DataRequest(start_time=T0, end_time=T0 + timedelta(minutes=5), interface_name=None,
                               is_session_view=True, sequence_id=1, stat_type="network"))
    assert prov == [False]
    ws.get_history_volume_for_period.assert_not_called()


def test_graph_host_pairs_the_flag_with_its_reply(q_app):
    from netspeedtray.views.monitor.graph_host import GraphHost
    host = SimpleNamespace(_totals_reconstructed={})
    GraphHost._on_totals_provenance(host, 3, True)
    GraphHost._on_totals_provenance(host, 40, False)        # a much later reply: old ones are dropped
    assert host._totals_reconstructed == {40: False}


def test_network_header_shows_the_note_only_for_estimated_totals(q_app):
    from netspeedtray.views.monitor.network.header import NetworkHeader
    i18n = I18nStrings("en_US")
    hdr = NetworkHeader(i18n)
    hdr.set_totals(3e9, 9e9, "TIMELINE_MONTH", reconstructed=True)
    assert not hdr._history_note.isHidden()
    assert hdr._history_note.full_text() == i18n.USAGE_RECORDED_HISTORY_NOTE
    assert hdr._history_note.toolTip() == i18n.USAGE_RECORDED_HISTORY_NOTE
    hdr.set_totals(3e9, 9e9, "TIMELINE_24_HOURS", reconstructed=False)
    assert hdr._history_note.isHidden()
    hdr.set_totals(3e9, 9e9, "TIMELINE_24_HOURS")          # old callers: no note
    assert hdr._history_note.isHidden()


def test_header_note_never_outgrows_the_band(q_app):
    """The band is a fixed 56 px: the note is cut to MONITOR_HISTORY_NOTE_MAX_LINES with an ellipsis."""
    from netspeedtray import constants
    from netspeedtray.views.monitor.network.header import _ClampedNote
    full = I18nStrings("de_DE").USAGE_RECORDED_HISTORY_NOTE
    note = _ClampedNote(full, constants.layout.MONITOR_HISTORY_NOTE_MAX_LINES)
    note.resize(160, 56)
    note._clamp(160)
    lines = note.heightForWidth(160) / note.fontMetrics().lineSpacing()
    assert lines <= constants.layout.MONITOR_HISTORY_NOTE_MAX_LINES + 0.5, (lines, note.text())
    assert note.text().endswith("…") and note.toolTip() == full
    note._clamp(4000)                                       # room again: the whole note comes back
    assert note.text() == full


def test_network_tab_forwards_the_flag(q_app):
    from netspeedtray.views.monitor.network.tab import NetworkTab
    tab = SimpleNamespace(_header=MagicMock())
    NetworkTab._on_totals(tab, 1.0, 2.0, "TIMELINE_WEEK", True)
    tab._header.set_totals.assert_called_once_with(1.0, 2.0, "TIMELINE_WEEK", reconstructed=True)


@pytest.mark.parametrize("reconstructed", [True, False])
def test_overview_session_strip_labels_estimated_totals(q_app, reconstructed):
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    i18n = I18nStrings("en_US")
    ov = OverviewTab(_MW(), _cfg(), i18n)
    # The volume arrives with the reader thread's window load (the 1 Hz render never reads the DB).
    ov._session_volume = VolumeResult(1e9, 2e9, 0.0, 0.0, reconstructed)
    mw = SimpleNamespace(session_start_time=datetime.now() - timedelta(minutes=5))
    text = ov._session_text(mw)
    assert ("2.0 GB" in text) or ("2,0 GB" in text), text
    assert (i18n.USAGE_RECORDED_HISTORY_NOTE in text) is reconstructed, text
