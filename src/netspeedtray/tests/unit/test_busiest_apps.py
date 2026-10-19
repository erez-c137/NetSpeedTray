"""
BusiestAppsCard - the Overview's "Most connections" tile. Verifies the honest ranking (active-first, then by
live connection count), the hard cap of 5 rows, the empty/unavailable states, and that a row/card click
asks to navigate to the Network tab. The psutil sampler itself isn't started here (no real network).
"""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.overview.busiest_apps import BusiestAppsCard, _MAX_ROWS


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _row(name, conn, est=0, hosts=0):
    return {"identity_key": name, "display_name": name, "conn_count": conn,
            "established_count": est, "host_count": hosts}


def test_ranks_active_first_then_by_count(q_app):
    card = BusiestAppsCard(I18nStrings("en_US"))
    payload = {"rows": [
        _row("idle_big", 99),                 # many conns but inactive
        _row("chrome", 12, est=4, hosts=8),   # active
        _row("svchost", 40, est=1),           # active, most conns among active
    ]}
    card._on_payload(payload)
    visible = [r for r in card._rows if r.isVisibleTo(card)]
    assert visible[0]._key == "svchost"       # active + highest count
    assert visible[1]._key == "chrome"        # active, fewer
    assert visible[2]._key == "idle_big"      # inactive sinks below active apps


def test_caps_at_five_rows(q_app):
    card = BusiestAppsCard(I18nStrings("en_US"))
    card._on_payload({"rows": [_row(f"app{i}", 50 - i, est=1) for i in range(12)]})
    assert sum(1 for r in card._rows if r.isVisibleTo(card)) == _MAX_ROWS == 5


def test_empty_payload_shows_status(q_app):
    card = BusiestAppsCard(I18nStrings("en_US"))
    card._on_payload({"rows": []})
    assert card._status.isVisibleTo(card)
    assert not any(r.isVisibleTo(card) for r in card._rows)


def test_click_navigates_to_network(q_app):
    card = BusiestAppsCard(I18nStrings("en_US"))
    fired = []
    card.go_to_network.connect(lambda: fired.append(True))
    card._on_payload({"rows": [_row("chrome", 10, est=2)]})
    card._rows[0].clicked.emit("chrome")      # a row click
    assert fired == [True]


def test_unavailable_rdp(q_app):
    card = BusiestAppsCard(I18nStrings("en_US"))
    card._on_unavailable("rdp")
    assert card._status.isVisibleTo(card)
    assert not any(r.isVisibleTo(card) for r in card._rows)


# --- the title says what it ranks (2.2 scope C) ---------------------------------------------------
# The card ranks by live connection COUNT. Windows can't attribute bytes to a process without
# admin/ETW, so a bandwidth title ("Top talkers") promised something the card can't show.

def test_title_says_connections_not_bandwidth(q_app):
    i18n = I18nStrings("en_US")
    card = BusiestAppsCard(i18n)
    assert card._title.text() == i18n.MONITOR_MOST_CONNECTIONS == "Most connections"
    assert card.accessibleName() == i18n.MONITOR_MOST_CONNECTIONS


def test_every_locale_renamed_the_card():
    import json
    from pathlib import Path
    import netspeedtray.constants as c
    files = sorted((Path(c.__file__).parent / "locales").glob("*.json"))
    assert len(files) == 16
    old = {"Top talkers", "Top-Verbraucher", "Más activas", "Applications les plus actives",
           "צרכנים מובילים", "Legnagyobb adatforgalmú alkalmazások", "App più attive", "通信量の多いアプリ",
           "주요 사용 앱", "Meest actief", "Najaktywniejsze aplikacje", "Самые активные",
           "Najaktivnejše aplikacije", "En çok veri kullananlar", "流量排行", "主要流量來源"}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        assert "MONITOR_TOP_TALKERS" not in d, f.name
        assert d.get("MONITOR_MOST_CONNECTIONS") and d["MONITOR_MOST_CONNECTIONS"] not in old, f.name
