"""
AppBarList + AppActivityFeed — the Monitor Network tab's per-app connection list. Verifies the bar
list builds/reuses/removes rows in place, summarises honestly, handles empty + RDP-unavailable
states, and that the feed degrades to an 'unavailable' signal under RDP without spawning a thread.
"""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.network.app_list import AppBarList, AppRow, _ActivityBar
from netspeedtray.views.monitor.network.app_feed import AppActivityFeed


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _row(name, conn, est=0, hosts=0):
    return {"identity_key": name.casefold(), "display_name": name,
            "conn_count": conn, "established_count": est, "host_count": hosts}


def _payload(rows, **kw):
    p = {"rows": rows, "app_count": len(rows),
         "active_app_count": sum(1 for r in rows if r.get("established_count", 0) > 0),
         "total_conn_count": sum(r.get("conn_count", 0) for r in rows),
         "updated_at": "12:00:00", "access_limited": False}
    p.update(kw)
    return p


def test_rows_built_and_summarised(q_app):
    lst = AppBarList(I18nStrings("en_US"))
    lst.set_payload(_payload([_row("chrome.exe", 18, est=5, hosts=7), _row("svchost", 2, est=0)]))
    assert set(lst._rows.keys()) == {"chrome.exe", "svchost"}
    assert "2" in lst._summary.text()  # 2 apps


def test_rows_updated_in_place_and_pruned(q_app):
    lst = AppBarList(I18nStrings("en_US"))
    lst.set_payload(_payload([_row("a", 4, est=1), _row("b", 2)]))
    row_a = lst._rows["a"]
    lst.set_payload(_payload([_row("a", 9, est=2)]))   # b gone, a updated
    assert lst._rows["a"] is row_a                      # reused, not recreated
    assert "b" not in lst._rows
    assert row_a._count.text() == "9"


def test_empty_and_rdp_states(q_app):
    lst = AppBarList(I18nStrings("en_US"))
    lst.set_payload(_payload([]))
    assert lst._summary.text() == getattr(I18nStrings("en_US"), "NO_APP_DATA_MESSAGE", "No application activity.")
    lst.set_unavailable("rdp")
    assert "Remote Desktop" in lst._summary.text() or "RDP" in lst._summary.text()


def test_activity_bar_paints(q_app):
    bar = _ActivityBar()
    bar.resize(120, 8)
    bar.set_value(0.6, active=True)
    bar.repaint()       # must not raise
    bar.set_value(0.0, active=False)
    bar.repaint()


def test_feed_rdp_degrades_without_thread(q_app, monkeypatch):
    import netspeedtray.utils.rdp_utils as rdp
    monkeypatch.setattr(rdp, "is_rdp_session", lambda: True)
    seen = []
    feed = AppActivityFeed()
    feed.unavailable.connect(seen.append)
    feed.start()
    assert seen == ["rdp"]
    assert feed._worker is None and feed._thread is None
    feed.teardown()     # idempotent / safe even though nothing started


def test_feed_teardown_before_start_is_safe(q_app):
    AppActivityFeed().teardown()   # must not raise
