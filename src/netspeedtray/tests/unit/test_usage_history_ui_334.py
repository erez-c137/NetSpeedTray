"""
#334 UI: the Usage history sheet (Overview usage card -> "Usage by day"), the card's link, the
WidgetState wiring (same adapters as the card's Today), and Settings > Advanced > Back up history.
"""
import csv
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.history_reader import DayVolume, V8HistoryReader
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.views.monitor.usage_history_sheet import (
    UsageHistorySheet, VIEW_DAYS, VIEW_MONTHS, VIEW_YEARS)

TODAY = date.today()


def _days():
    """60 days ending today; days 50-59 back are pre-2.2 (estimated)."""
    out = []
    for i in range(60):
        d = TODAY - timedelta(days=i)
        out.append(DayVolume(d, 0, 1e9, 5e9, i >= 50))
    return list(reversed(out))


class _WS:
    def __init__(self, days):
        self._days = days
        self.calls = 0

    def get_metered_daily_volume(self, start, end):
        self.calls += 1
        return self._days


def _sheet(qt_owned, days=None, config=None, ceiling=None, lang="en_US"):
    ws = _WS(_days() if days is None else days)
    s = qt_owned(UsageHistorySheet(ws, config or {}, I18nStrings(lang),
                                   period_ceiling=(lambda: ceiling) if ceiling else None))
    return s, ws


def _cells(s, row):
    return [s._table.item(row, c).text() for c in range(4)]


def test_loads_on_a_reader_thread_and_lists_days_newest_first(qt_owned, qtbot):
    s, ws = _sheet(qt_owned)
    qtbot.waitUntil(s.is_loaded, timeout=5000)
    assert ws.calls == 1
    assert s._table.rowCount() == 60
    first = _cells(s, 0)
    assert TODAY.strftime("%m/%d/%Y").lstrip("0").replace("/0", "/") in first[0]   # en_US M/d/yyyy
    assert first[1:] == ["5.00 GB", "1.00 GB", "6.00 GB"]
    assert _cells(s, 59)[1] == "~5.00 GB"                       # the oldest day is estimated
    assert not s._estimated_note.isHidden() and not s._footnote.isHidden()
    assert s._export_btn.isEnabled()


def test_months_and_years_sum_the_days(qt_owned):
    s, _ws = _sheet(qt_owned)
    s.set_days(_days())
    s._seg.setValue(VIEW_YEARS)
    s._on_view_changed(VIEW_YEARS)
    total_down = sum(float(_cells(s, r)[1].strip("~").split()[0]) for r in range(s._table.rowCount()))
    assert total_down == pytest.approx(300.0)                   # 60 days x 5 GB
    s._on_view_changed(VIEW_MONTHS)
    months = [_cells(s, r) for r in range(s._table.rowCount())]
    assert 2 <= len(months) <= 3
    assert months[0][0] == f"{TODAY.strftime('%B')} {TODAY.year}"
    assert s._period_note.isHidden()                            # calendar months: no billing-period line


def test_months_follow_the_billing_period_with_a_reset_day(qt_owned):
    cfg = {"data_cap_enabled": True, "data_cap_gb": 100, "data_cap_reset_day": 15}
    s, _ws = _sheet(qt_owned, config=cfg)
    s.set_days(_days())
    s._on_view_changed(VIEW_MONTHS)
    label = _cells(s, 0)[0]
    assert " – " in label                                       # "M/15/yyyy – M/14/yyyy"
    assert not s._period_note.isHidden() and "day 15" in s._period_note.text()
    newest = s._rows()[-1]
    assert newest.start.day == 15 and newest.start <= TODAY <= newest.end


def test_today_row_takes_the_cards_cap_clamp(qt_owned):
    s, _ws = _sheet(qt_owned, ceiling=(TODAY.replace(day=1), (0.5e9, 2e9)))
    s.set_days(_days())
    assert _cells(s, 0)[1:] == ["2.00 GB", "500.00 MB", "2.50 GB"]
    assert _cells(s, 1)[1] == "5.00 GB"                         # only today is clamped


def test_empty_and_unreadable_states(qt_owned):
    s, _ws = _sheet(qt_owned, days=[])
    s.set_days([])
    assert s._table.isHidden() and not s._export_btn.isEnabled()
    assert s._status.text() == I18nStrings("en_US").USAGE_HISTORY_EMPTY
    s.set_days(None)
    assert s._status.text() == I18nStrings("en_US").USAGE_HISTORY_UNAVAILABLE


def test_export_writes_the_shown_view_in_bytes(qt_owned, tmp_path, monkeypatch):
    s, _ws = _sheet(qt_owned)
    s.set_days(_days())
    s._on_view_changed(VIEW_MONTHS)
    out = tmp_path / "u"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    s._export()
    rows = list(csv.reader((tmp_path / "u.csv").open(encoding="utf-8")))
    assert rows[0] == ["date", "download_bytes", "upload_bytes", "total_bytes", "estimated"]
    assert sum(int(r[1]) for r in rows[1:]) == 300 * 10 ** 9
    assert rows[-1][0] == f"{TODAY.year:04d}-{TODAY.month:02d}"     # oldest first in the file
    assert {r[4] for r in rows[1:]} <= {"true", "false"}


@pytest.mark.parametrize("lang", ["de_DE", "he_IL", "ja_JP", "hu_HU"])
def test_localized_labels(qt_owned, lang):
    s, _ws = _sheet(qt_owned, lang=lang)
    s.set_days(_days())
    s._on_view_changed(VIEW_MONTHS)
    label = _cells(s, 0)[0]
    assert str(TODAY.year) in label and "{" not in label
    if lang == "de_DE":
        assert _cells(s, 0)[1].endswith("GB") and "," in _cells(s, 0)[1]    # 5,00 GB


def test_usage_card_link_emits(q_app, qt_owned):
    from netspeedtray.views.monitor.overview.tiles import UsageTile
    t = qt_owned(UsageTile(I18nStrings("en_US")))
    got = []
    t.usage_history_requested.connect(lambda: got.append(1))
    t._history_link.linkActivated.emit("#")
    assert got == [1]
    t.set((1.0, 2.0), (3.0, 4.0), (5.0, 10.0, 50.0))            # cap set: hint hidden, link stays
    assert t._cap_hint.isHidden() and not t._history_link.isHidden()


def test_widget_state_daily_volume_uses_the_cards_scope(tmp_path):
    """Through WidgetState: the sheet's today row == get_metered_volume_for_period(midnight, now)."""
    from netspeedtray.core.db import writer_v8 as W
    p = tmp_path / "h.db"
    c = sqlite3.connect(p)
    S8.create_v8_schema(c)
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    now = int(time.time())
    w = W.WriterV8(c, version="t", poll_ms=1000, is_physical=lambda n: n == "Ethernet")
    for i in range(0, 3 * 3600, 60):
        w.add_net_sample(W.NetSample(ts=now - 3 * 3600 + i, dt_ms=1000, byte_deltas={
            "Ethernet": (100, 1_000), "ProtonVPN": (100, 1_000)}))
    w.flush(now)
    c.close()
    reader = V8HistoryReader(lambda: sqlite3.connect(p), lambda: p, clock=lambda: now)
    for mode in ("auto", "all_virtual", "selected"):
        cfg = {"interface_mode": mode, "selected_interfaces": ["ProtonVPN"], "excluded_interfaces": []}
        ws = SimpleNamespace(db_worker=object(), history_reader=reader, config=cfg)
        now_dt = datetime.fromtimestamp(now)
        midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        card = WidgetState.get_metered_volume_for_period(ws, midnight, now_dt)
        days = WidgetState.get_metered_daily_volume(ws, None, now_dt)
        today = [d for d in days if d.day == now_dt.date()][0]
        assert (today.up_bytes, today.down_bytes) == (card.up_bytes, card.down_bytes), mode
    assert WidgetState.get_metered_daily_volume(SimpleNamespace(db_worker=None), None, None) is None


# ------------------------------------------------------------------ Settings > Advanced > Back up

def _make_db(path: Path) -> None:
    c = sqlite3.connect(path)
    S8.create_v8_schema(c)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    c.close()


def test_advanced_backup_runs_off_the_gui_thread_and_reports(qt_owned, qtbot, tmp_path, monkeypatch):
    from netspeedtray.views.settings.pages.advanced import AdvancedPage
    src = tmp_path / "speed_history.db"
    _make_db(src)
    page = qt_owned(AdvancedPage(I18nStrings("en_US"), lambda: None, history_path_callback=lambda: src))
    page.show()                                                # Settings on screen: message boxes
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(("warn", a[2])))
    dest = tmp_path / "out" / "copy.db"
    dest.parent.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest)[:-3], ""))   # no .db
    assert page.backup_history_btn.isEnabled()
    page._on_backup_clicked()
    assert not page.backup_history_btn.isEnabled()            # busy while the thread runs
    qtbot.waitUntil(lambda: bool(shown), timeout=10000)
    assert page.backup_history_btn.isEnabled() and page._backup_busy.isHidden()
    assert shown[0].startswith(I18nStrings("en_US").BACKUP_HISTORY_DONE) and "copy.db" in shown[0]
    chk = sqlite3.connect(dest)
    assert chk.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0] == "8"
    chk.close()
    # Refused target (the live name): a warning, nothing written.
    shown.clear()
    page.start_backup(src, src.parent / "speed_history.db-wal")
    qtbot.waitUntil(lambda: bool(shown), timeout=10000)
    en = I18nStrings("en_US")
    assert shown[0][0] == "warn" and shown[0][1] == f"{en.BACKUP_HISTORY_FAILED}\n{en.BACKUP_HISTORY_ERR_REFUSED}"


def test_advanced_backup_without_a_history_file(qt_owned, tmp_path, monkeypatch):
    from netspeedtray.views.settings.pages.advanced import AdvancedPage
    page = qt_owned(AdvancedPage(I18nStrings("en_US"), lambda: None,
                                 history_path_callback=lambda: tmp_path / "missing.db"))
    page.show()
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: pytest.fail("no file: no save dialog"))
    page._on_backup_clicked()
    assert shown and I18nStrings("en_US").BACKUP_HISTORY_NO_FILE in shown[0]
    no_cb = qt_owned(AdvancedPage(I18nStrings("en_US"), lambda: None))
    assert not no_cb.backup_history_btn.isEnabled()
