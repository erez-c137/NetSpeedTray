"""
#334 review (PR #439) fixes:

* M4 - only a refused statement falls back to the backup API; an interrupt, a full disk or a bad target
  is final (no second full copy).
* Gap 2 - ``cancel_all`` (the app's quit) interrupts a running copy, which deletes its temp file and its
  ``-journal``; the backup thread is joined; new backups are refused; stale ``<name>.partial-*`` files
  beside the target are swept first; two backups to the same name are refused.
* M5 - every failure carries a code the UI translates; the raw detail is not shown.
* M3 - with a cap set, the current month / period row and its year never read above the odometer's
  period count (the usage card's figure); the footnote is honest per interface mode.
* Gap 1 - a backup that finishes while Settings is closed (hidden) reports through the widget's
  non-focus flyout, never an application-modal box with no visible parent.
* Low - a 2.1 file waiting for its upgrade says the table comes after the upgrade.
"""
import logging
import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMessageBox, QWidget

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import usage_history as U
from netspeedtray.core.db import history_backup as B
from netspeedtray.core.db.history_reader import DayVolume
from netspeedtray.tests.unit import v8_twin_fixture as F

NOW = F.fixed_now()
TODAY = date.today()
EN = I18nStrings("en_US")


@pytest.fixture(autouse=True)
def _fresh_backup_state(monkeypatch):
    """cancel_all latches the module for the rest of the process (the app is quitting): undo per test."""
    monkeypatch.setattr(B, "_cancelled", False)
    monkeypatch.setattr(B, "_targets", set())
    monkeypatch.setattr(B, "_conns", set())


@pytest.fixture()
def live(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    p = d / "speed_history.db"
    F.write_v8(p, F.make_history(NOW)).close()
    return p


def _leftovers(folder: Path, name: str):
    return sorted(p.name for p in folder.iterdir() if p.name.startswith(name) and p.name != name)


def _slow_ro(monkeypatch, on_first=None, sleep_s=0.0):
    """Give the backup's read-only connection a progress handler (slows the copy / runs ``on_first``)."""
    real = B._connect
    fired = []

    def connect(target, **kw):
        conn = real(target, **kw)
        if kw.get("uri") and "speed_history" in str(target):
            def handler():
                if on_first is not None and not fired:
                    fired.append(1)
                    on_first()
                if sleep_s:
                    time.sleep(sleep_s)
                return 0
            conn.set_progress_handler(handler, 200)
        return conn
    monkeypatch.setattr(B, "_connect", connect)
    return fired


# ------------------------------------------------------------------ M4: narrow fallback

@pytest.mark.parametrize("msg, code", [("database or disk is full", B.ERR_IO), ("unable to open database file", B.ERR_IO)])
def test_other_errors_do_not_fall_back(live, tmp_path, monkeypatch, msg, code):
    real = B._connect
    opened = []

    class _Fail:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, *a):
            if sql.startswith("VACUUM INTO"):
                raise sqlite3.OperationalError(msg)
            return self._c.execute(sql, *a)

        def __getattr__(self, n):
            return getattr(self._c, n)

    def connect(target, **kw):
        opened.append(str(target))
        c = real(target, **kw)
        return _Fail(c) if kw.get("uri") and "speed_history" in str(target) else c
    monkeypatch.setattr(B, "_connect", connect)
    dest = tmp_path / "x.db"
    with pytest.raises(B.BackupError) as ei:
        B.backup_history(live, dest)
    assert ei.value.code == code
    assert not any(".partial-" in o for o in opened)          # the backup API never opened the temp file
    assert not dest.exists() and _leftovers(tmp_path, "x.db") == []


@pytest.mark.parametrize("code, refused", [(8, True), (8 | (5 << 8), True), (23, True), (9, False), (13, False),
                                           (14, False)])
def test_only_readonly_or_auth_counts_as_refused(code, refused):
    e = sqlite3.OperationalError("x")
    e.sqlite_errorcode = code
    assert B._statement_refused(e) is refused


# ------------------------------------------------------------------ Gap 2: cancel, sweep, busy

def test_cancel_interrupts_the_copy_and_cleans_up(live, tmp_path, monkeypatch):
    calls, copying = [], []
    real = B._connect

    def connect(target, **kw):
        conn = real(target, **kw)
        if kw.get("uri") and "speed_history" in str(target):
            def handler():
                if copying:                       # only inside VACUUM INTO, not the earlier reads
                    calls.append(1)
                    if len(calls) == 1:
                        B.cancel_all(0)
                return 0
            conn.set_progress_handler(handler, 50)
        return conn
    monkeypatch.setattr(B, "_connect", connect)
    dest = tmp_path / "c.db"
    with pytest.raises(B.BackupError) as ei:
        B.backup_history(live, dest, progress=lambda s: copying.append(s) if s == B.STEP_COPY else None)
    assert ei.value.code == B.ERR_CANCELLED
    # Interrupted mid-statement, not run to the end and discarded: a whole copy calls the handler
    # thousands of times (measured); an interrupted one stops at the next check.
    assert len(calls) <= 3, len(calls)
    assert not dest.exists() and _leftovers(tmp_path, "c.db") == []    # no .partial-<pid>, no -journal
    with pytest.raises(B.BackupError) as again:                       # quitting: no new backup starts
        B.backup_history(live, tmp_path / "d.db")
    assert again.value.code == B.ERR_CANCELLED


def test_cancel_all_joins_a_running_backup_thread(live, tmp_path, monkeypatch):
    started = threading.Event()
    _slow_ro(monkeypatch, on_first=started.set, sleep_s=0.002)
    out = {}

    def work():
        try:
            B.backup_history(live, tmp_path / "t.db")
            out["code"] = "ok"
        except B.BackupError as e:
            out["code"] = e.code

    t = B.run_in_thread(work)
    assert started.wait(10)
    t0 = time.monotonic()
    assert B.cancel_all(5.0) is True
    assert time.monotonic() - t0 < 5.0 and not t.is_alive()
    assert out["code"] == B.ERR_CANCELLED
    assert _leftovers(tmp_path, "t.db") == [] and not (tmp_path / "t.db").exists()


def test_a_failed_copy_removes_its_journal_too(live, tmp_path, monkeypatch):
    """The temp copy's rollback journal (``<tmp>-journal``, what a quit mid-copy left behind) goes with
    it on every failure path."""
    def verify(tmp, *a):
        Path(str(tmp) + "-journal").write_bytes(b"hot journal")
        raise B.BackupError(B.ERR_VERIFY, "simulated")
    monkeypatch.setattr(B, "_verify", verify)
    with pytest.raises(B.BackupError):
        B.backup_history(live, tmp_path / "j.db")
    assert _leftovers(tmp_path, "j.db") == []


def test_unresolvable_target_is_refused(live, tmp_path, monkeypatch):
    def boom(p):
        raise OSError("cannot resolve")
    monkeypatch.setattr(B.os.path, "realpath", boom)
    assert B._refused_target(live, tmp_path / "anything.db") is True


def test_stale_partials_are_swept(live, tmp_path):
    dest = tmp_path / "s.db"
    for n in ("s.db.partial-4242", "s.db.partial-4242-journal", "S.DB.partial-7"):
        (tmp_path / n).write_bytes(b"junk")
    (tmp_path / "s.db.notpartial").write_bytes(b"keep")
    B.backup_history(live, dest)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app", "s.db", "s.db.notpartial"]


def test_two_backups_to_one_name_are_refused(live, tmp_path):
    import os
    dest = tmp_path / "b.db"
    B._targets.add(os.path.normcase(os.path.realpath(dest)))
    with pytest.raises(B.BackupError) as ei:
        B.backup_history(live, dest)
    assert ei.value.code == B.ERR_BUSY and not dest.exists()
    B._targets.clear()
    B.backup_history(live, dest)                               # and the guard is per run
    assert dest.exists() and not B._targets


# ------------------------------------------------------------------ M5: codes

def test_error_codes(live, tmp_path, monkeypatch):
    with pytest.raises(B.BackupError) as e1:
        B.backup_history(tmp_path / "none.db", tmp_path / "o.db")
    assert e1.value.code == B.ERR_NO_FILE
    with pytest.raises(B.BackupError) as e2:
        B.backup_history(live, live.parent / "speed_history.db.bak.v7_x")
    assert e2.value.code == B.ERR_REFUSED
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t(x)").connection.close()
    with pytest.raises(B.BackupError) as e3:
        B.backup_history(other, tmp_path / "o.db")
    assert e3.value.code == B.ERR_READ
    monkeypatch.setattr(B, "_verify", lambda *a: (_ for _ in ()).throw(sqlite3.DatabaseError("malformed")))
    with pytest.raises(B.BackupError) as e4:
        B.backup_history(live, tmp_path / "o.db")
    assert e4.value.code == B.ERR_VERIFY and _leftovers(tmp_path, "o.db") == []


def test_every_code_has_a_translated_reason():
    from netspeedtray.views.settings.pages.advanced import AdvancedPage
    codes = {B.ERR_REFUSED, B.ERR_NO_FILE, B.ERR_READ, B.ERR_VERIFY, B.ERR_IO, B.ERR_BUSY}
    assert set(AdvancedPage._BACKUP_REASON_KEYS) == codes
    for lang in I18nStrings.LANGUAGE_MAP:
        i18n = I18nStrings(lang)
        for key in AdvancedPage._BACKUP_REASON_KEYS.values():
            assert getattr(i18n, key) and "WinError" not in getattr(i18n, key)


# ------------------------------------------------------------------ M3: the current period at the card's figure

def _rows(first: date, n: int, up=1.0, down=10.0):
    return U.day_rows([DayVolume(first + timedelta(days=i), 0, up, down, False) for i in range(n)])


def test_current_period_row_and_year_capped_at_the_odometer():
    today = date(2026, 10, 21)
    days = _rows(date(2026, 9, 1), 51)                        # 1 Sep -> 21 Oct, 10 down / day
    months = U.month_rows(days)
    out = U.clamp_current_period(months, days, today, date(2026, 10, 1), (5.0, 150.0))
    oct_, sep = out[-1], out[-2]
    assert (oct_.up_bytes, oct_.down_bytes) == (5.0, 150.0)    # history 21 up / 210 down -> the odometer
    assert sep == months[-2]
    years = U.clamp_current_period(U.year_rows(days), days, today, date(2026, 10, 1), (5.0, 150.0))
    assert (years[0].up_bytes, years[0].down_bytes) == (51.0 - 16.0, 510.0 - 60.0)
    # Below the odometer (closed-app traffic): left alone.
    assert U.clamp_current_period(months, days, today, date(2026, 10, 1), (1e9, 1e9)) == months


def test_billing_period_row_capped_only_when_it_is_the_odometers_period():
    today = date(2026, 10, 21)
    days = _rows(date(2026, 9, 1), 51)
    periods = U.month_rows(days, reset_day=15)
    out = U.clamp_current_period(periods, days, today, date(2026, 10, 15), (0.0, 20.0))
    assert out[-1].start == date(2026, 10, 15) and out[-1].down_bytes == 20.0
    stale = U.clamp_current_period(periods, days, today, date(2026, 10, 1), (0.0, 20.0))
    assert stale[-1] == periods[-1]                            # odometer period != this row: untouched


class _WS:
    def __init__(self, days, available=True):
        self._days, self._available = days, available

    def usage_history_available(self):
        return self._available

    def get_metered_daily_volume(self, s, e):
        return self._days


def _days_to_today(n=40):
    return [DayVolume(TODAY - timedelta(days=i), 0, 1e9, 10e9, False) for i in reversed(range(n))]


def test_sheet_caps_month_and_year_with_a_cap(qt_owned):
    from netspeedtray.views.monitor.usage_history_sheet import UsageHistorySheet, VIEW_MONTHS, VIEW_YEARS
    start = TODAY.replace(day=1)
    s = qt_owned(UsageHistorySheet(_WS([]), {"interface_mode": "auto"}, EN,
                                   period_ceiling=lambda: (start, (2e9, 5e9))))
    s.set_days(_days_to_today())
    s._on_view_changed(VIEW_MONTHS)
    cur = [r for r in s._rows() if r.start <= TODAY <= r.end][0]
    assert cur.down_bytes == 5e9 and cur.up_bytes <= 2e9
    s._on_view_changed(VIEW_YEARS)
    hist_period = sum(10e9 for i in range(40) if TODAY - timedelta(days=i) >= start)
    year = [r for r in s._rows() if r.start <= TODAY <= r.end][0]
    assert year.down_bytes == pytest.approx(sum(10e9 for i in range(40) if (TODAY - timedelta(days=i)).year == TODAY.year)
                                            - (hist_period - 5e9))
    assert not s._cap_note.isHidden()


@pytest.mark.parametrize("mode, key", [("auto", "USAGE_HISTORY_FOOTNOTE_AUTO"), ("all_physical", "USAGE_HISTORY_FOOTNOTE"),
                                        ("selected", "USAGE_HISTORY_FOOTNOTE")])
def test_footnote_is_honest_per_mode(qt_owned, mode, key):
    from netspeedtray.views.monitor.usage_history_sheet import UsageHistorySheet
    s = qt_owned(UsageHistorySheet(_WS([]), {"interface_mode": mode}, EN))
    assert s._footnote.text() == getattr(EN, key)
    s.set_days(_days_to_today(3))
    assert s._cap_note.isHidden()                              # no cap: no cap line


def test_pending_upgrade_says_so(qt_owned, qtbot):
    from netspeedtray.views.monitor.usage_history_sheet import UsageHistorySheet
    s = qt_owned(UsageHistorySheet(_WS(None, available=False), {}, EN))
    qtbot.waitUntil(s.is_loaded, timeout=5000)
    assert s._status.text() == EN.USAGE_HISTORY_PENDING_UPGRADE


def test_widget_state_availability_follows_the_backend():
    from netspeedtray.core.widget_state import WidgetState
    from netspeedtray.core.db.history_reader import V7HistoryReader, V8HistoryReader
    v7 = SimpleNamespace(history_reader=V7HistoryReader(lambda: None, lambda: None))
    v8 = SimpleNamespace(history_reader=V8HistoryReader(lambda: None, lambda: None))
    assert WidgetState.usage_history_available(v7) is False
    assert WidgetState.usage_history_available(v8) is True


# ------------------------------------------------------------------ Gap 1: Settings closed

def _page(qt_owned, src, notes):
    from netspeedtray.views.settings.pages.advanced import AdvancedPage
    host = qt_owned(QWidget())                                 # stands in for the Settings window
    page = AdvancedPage(EN, lambda: None, history_path_callback=lambda: src,
                        notify_callback=lambda *a: notes.append(a))
    page.setParent(host)
    return host, page


def test_finished_while_settings_hidden_uses_the_flyout(qt_owned, qtbot, live, tmp_path, monkeypatch):
    boxes = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: boxes.append(self.text()) or 0)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: boxes.append(a[2]))
    notes = []
    host, page = _page(qt_owned, live, notes)
    host.show()
    page.start_backup(live, tmp_path / "h.db")
    host.hide()                                                # Settings closed mid-copy (hidden, cached)
    qtbot.waitUntil(lambda: bool(notes), timeout=10000)
    assert boxes == []
    title, body, path = notes[0]
    assert title == EN.BACKUP_HISTORY_LABEL and body.startswith(EN.BACKUP_HISTORY_DONE)
    assert path == str(tmp_path / "h.db")
    notes.clear()
    page.start_backup(live, live.parent / "speed_history.db-shm")
    qtbot.waitUntil(lambda: bool(notes), timeout=10000)
    assert notes[0] == (EN.BACKUP_HISTORY_LABEL, f"{EN.BACKUP_HISTORY_FAILED}\n{EN.BACKUP_HISTORY_ERR_REFUSED}", None)
    assert boxes == []


def test_cancelled_backup_reports_nothing(qt_owned, live):
    notes = []
    host, page = _page(qt_owned, live, notes)
    page._on_backup_finished(None, B.ERR_CANCELLED)
    assert notes == [] and page.backup_history_btn.isEnabled()


def test_widget_notice_has_open_folder(monkeypatch):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    calls = []
    fake = SimpleNamespace(i18n=EN, _show_notice_flyout=lambda *a, **k: calls.append((a, k)))
    NetworkSpeedWidget.show_backup_notice(fake, "T", "B", r"C:\x\h.db")
    NetworkSpeedWidget.show_backup_notice(fake, "T", "B", None)
    assert calls[0][1]["action_text"] == EN.STATS_DETAIL_OPEN_FOLDER and calls[0][1]["on_action"] is not None
    assert calls[1][1]["action_text"] is None and calls[1][1]["on_action"] is None


def test_quit_cancels_running_backups(monkeypatch):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    from netspeedtray import constants
    seen = []
    monkeypatch.setattr(B, "cancel_all", lambda wait_s: seen.append(wait_s) or True)
    fake = SimpleNamespace(logger=logging.getLogger("test.cleanup"))
    NetworkSpeedWidget.cleanup(fake, save_config=False)        # every other step fails harmlessly
    assert seen == [constants.timeouts.HISTORY_BACKUP_CANCEL_WAIT_MS / 1000.0]
