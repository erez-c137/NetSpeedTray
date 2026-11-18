"""
2.2.2 D5 - "Router" vs "Internet", said honestly, and the internet check in one click.

With the internet check off (the default) the Overview's health line came from ROUTER pings but said
"Connection: steady", so an ISP outage with Wi-Fi up read green. It now names the hop it measured,
offers the internet check beside it ("Also watch the internet") with an inline confirm that says
what leaves the PC, and the Check connection sheet uses the same confirm instead of a Settings trip.
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor import internet_offer as IO

EN = I18nStrings("en_US")
DEFAULTS = constants.config.defaults.DEFAULT_CONFIG
PKG = Path(__file__).resolve().parents[2]          # src/netspeedtray
REPO = PKG.parents[1]
FSI, PDI = "⁨", "⁩"


def _cfg(**over):
    c = dict(DEFAULTS)
    c.update(over)
    return c


def _mw(config, turn_on=True):
    """A main widget: config_controller.update_config writes the shared dict (as the real one does);
    turn_on_internet_check flips the internet check through it."""
    m = MagicMock()
    m.config = config
    m.system_power = None
    m.latency_gw = 4.0
    m.latency_anchor = None
    m.latency_link_lost = False
    m.config_controller.update_config.side_effect = lambda u, **k: config.update(u)
    if turn_on:
        m.turn_on_internet_check.side_effect = (
            lambda: m.config_controller.update_config({"latency_public_enabled": True}))
    else:
        del m.turn_on_internet_check
    m.internet_offer_snoozed = False
    return m


@pytest.fixture
def tab(q_app):
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    made = []

    def make(cfg=None, mw=None):
        cfg = cfg if cfg is not None else _cfg()
        mw = mw if mw is not None else _mw(cfg)
        t = OverviewTab(mw, cfg, EN)
        made.append(t)
        return t, mw, cfg
    yield make
    for t in made:
        t.teardown()
        t.deleteLater()


def _events(count=0, hop="gateway", last=None):
    return {"count": count, "last_start": last, "total_down_seconds": 0.0, "runs": [], "hop": hop}


def _plain(html):
    import re
    return re.sub(r"<[^>]+>", "", html)


# --- host -> provider ---------------------------------------------------------------------------------

@pytest.mark.parametrize("host, provider", [
    ("1.1.1.1", "Cloudflare"), (" 1.0.0.1 ", "Cloudflare"), ("2606:4700:4700:0:0:0:0:1111", "Cloudflare"),
    ("[2606:4700:4700::1001]", "Cloudflare"), ("One.One.One.One.", "Cloudflare"),
    ("8.8.8.8", "Google"), ("dns.google", "Google"), ("9.9.9.9", "Quad9"), ("208.67.222.222", "OpenDNS"),
    ("192.0.2.7", None), ("example.net", None), ("", None), (None, None),
])
def test_known_test_servers_are_named(host, provider):
    assert IO.provider_for_host(host) == provider


def test_the_confirm_names_the_server_and_its_operator():
    assert IO.offer_target("1.1.1.1") == f"{FSI}1.1.1.1 (Cloudflare){PDI}"
    assert IO.offer_target("ping.example.net") == f"{FSI}ping.example.net{PDI}"   # unknown: just the host
    line = IO.confirm_text(EN, "1.1.1.1")
    lat = constants.network.latency
    assert "1.1.1.1 (Cloudflare)" in line and "IP address" in line and "Nothing else leaves your PC" in line
    assert f"{lat.BURST_SIZE} tiny pings every {lat.OUTAGE_CYCLE_SEC:g} seconds" in line


def test_the_target_stays_one_ltr_run_in_hebrew():
    he = I18nStrings("he_IL")
    line = IO.confirm_text(he, "1.1.1.1")
    assert f"{FSI}1.1.1.1 (Cloudflare){PDI}" in line


# --- labels by mode -----------------------------------------------------------------------------------

def test_router_only_data_says_router_never_connection(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    html, hop = ov._connection_health(mw)
    assert _plain(html) == "Router: steady" and hop == "gateway"
    from datetime import datetime, timedelta
    ov._latency_events = _events(3, last=datetime.now() - timedelta(hours=2))
    assert _plain(ov._connection_health_html(mw)).startswith("Router: 3 drops")
    assert "Connection" not in ov._connection_health_html(mw)


def test_internet_side_data_says_internet(tab):
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = 18.0
    ov._latency_events = _events(0, hop="anchor")
    html, hop = ov._connection_health(mw)
    assert _plain(html) == "Internet: steady" and hop == "anchor"


def test_internet_steady_waits_for_the_internets_own_reply(tab):
    """Just turned on: the router answers, the internet ping has not yet - no "Internet: steady"."""
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = None
    ov._latency_events = _events(0, hop="anchor")
    assert ov._connection_health_html(mw) == ""


def test_drops_read_for_the_other_hop_are_never_shown(tab, monkeypatch):
    """The internet check was switched since the read: the router's drops must not read "Internet"."""
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = 18.0
    asked = []
    monkeypatch.setattr(ov, "_reload_window", lambda force=False: asked.append(force))
    ov._latency_events = _events(5, hop="gateway")
    assert ov._connection_health_html(mw) == ""
    QApplication.processEvents()
    assert asked == [True]                       # one forced re-read for the right hop
    ov._connection_health_html(mw)
    QApplication.processEvents()
    assert asked == [True]                       # ...asked once, not on every 1 Hz render


@pytest.mark.parametrize("lang", ["en_US", "de_DE", "he_IL"])
def test_the_line_uses_the_sheets_router_and_internet_words(q_app, lang):
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    i18n = I18nStrings(lang)
    for public, hop, word in ((False, "gateway", i18n.CHECK_CONN_ROUTER), (True, "anchor", i18n.LATENCY_LABEL)):
        cfg = _cfg(latency_public_enabled=public)
        mw = _mw(cfg)
        mw.latency_anchor = 18.0
        ov = OverviewTab(mw, cfg, i18n)
        try:
            ov._latency_events = _events(0, hop=hop)
            assert _plain(ov._connection_health_html(mw)).startswith(f"{word}: ")
        finally:
            ov.teardown()


def test_no_locale_keeps_a_connection_label_for_the_health_line():
    for f in (PKG / "constants" / "locales").glob("*.json"):
        assert "CONN_HEALTH_LABEL" not in json.loads(f.read_text(encoding="utf-8")), f.name


def test_the_health_line_explains_a_drop(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = ov._context_r_lbl.toolTip()
    assert "router" in tip and "6 seconds" in tip and "estimated" in tip
    cfg["latency_public_enabled"] = True
    mw.latency_anchor = 18.0
    ov._latency_events = _events(0, hop="anchor")
    ov._render_health_strip(mw)
    assert "internet test server" in ov._context_r_lbl.toolTip()


def test_window_reads_tag_their_drops_with_the_hop():
    from netspeedtray.views.monitor.overview.tab import _read_db
    from datetime import datetime, timedelta
    ws = MagicMock()
    ws.get_overview_window.return_value = ({}, {}, True)
    ws.get_loss_history.return_value = {}
    end = datetime.now()
    for public, hop in ((False, "gateway"), (True, "anchor")):
        events = _read_db(ws, end - timedelta(hours=1), end, False, 1.0, True, None, public)[2]
        assert events["hop"] == hop


# --- the offer beside the router line -----------------------------------------------------------------

def test_the_offer_sits_beside_a_router_line_only(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    link = ov._internet_link
    assert not link.isHidden()
    assert link.button().accessibleName() == EN.INTERNET_WATCH_OFFER
    ov._latency_events = None                    # nothing claimed yet: no offer either
    ov._render_health_strip(mw)
    assert link.isHidden()
    mw.system_power = 30.0                       # the slot shows System power: no router line
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    assert link.isHidden()


def test_the_offer_is_not_shown_with_the_internet_check_on(tab):
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = 18.0
    ov._latency_events = _events(0, hop="anchor")
    ov._render_health_strip(mw)
    assert ov._internet_link.isHidden()


def test_the_link_opens_an_inline_confirm_and_changes_nothing(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    ov._internet_link.click()
    confirm = ov._internet_confirm
    assert not confirm.isHidden() and ov._internet_link.isHidden()
    assert "1.1.1.1 (Cloudflare)" in confirm.text() and "IP address" in confirm.text()
    assert cfg["latency_public_enabled"] is False
    mw.turn_on_internet_check.assert_not_called()
    ov._render_health_strip(mw)                  # the 1 Hz render keeps it open
    assert not confirm.isHidden()


def test_turn_on_turns_the_internet_check_on(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    ov._internet_link.click()
    ov._internet_confirm.turn_on_button().click()
    mw.turn_on_internet_check.assert_called_once()
    assert cfg["latency_public_enabled"] is True
    assert ov._internet_confirm.is_done()
    assert ov._internet_confirm.text() == EN.INTERNET_CONFIRM_DONE
    assert ov._internet_link.isHidden()


def test_not_now_hides_the_offer_for_the_session_and_counts(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    ov._internet_link.click()
    ov._internet_confirm.not_now_button().click()
    assert ov._internet_confirm.isHidden() and ov._internet_link.isHidden()
    assert cfg["latency_public_enabled"] is False
    mw.turn_on_internet_check.assert_not_called()
    assert cfg["internet_offer_declines"] == 1
    mw.config_controller.update_config.assert_called_with({"internet_offer_declines": 1},
                                                          apply_and_repaint=False)
    ov._render_health_strip(mw)
    assert ov._internet_link.isHidden()
    again, _, _ = tab(cfg, mw)                   # the Monitor reopened, same session
    again._latency_events = _events(0)
    again._render_health_strip(mw)
    assert again._internet_link.isHidden()


def test_two_not_nows_stop_the_offer_for_good(tab):
    cfg = _cfg(internet_offer_declines=constants.network.latency.INTERNET_OFFER_MAX_DECLINES)
    ov, mw, _ = tab(cfg)                         # a new session: not snoozed, but declined twice
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    assert ov._internet_link.isHidden()
    cfg["internet_offer_declines"] = 1           # one Not now (an earlier session): offered again
    ov._render_health_strip(mw)
    assert not ov._internet_link.isHidden()


def test_keyboard_activation_moves_focus_into_the_confirm(tab):
    ov, mw, cfg = tab()
    ov.resize(900, 600)
    ov.show()
    QTest.qWaitForWindowExposed(ov)
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    btn = ov._internet_link.button()
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(btn, Qt.Key.Key_Return)
    assert ov._internet_confirm.turn_on_button().hasFocus()
    QTest.keyClick(ov._internet_confirm.not_now_button(), Qt.Key.Key_Space)   # Space works too
    assert ov._internet_confirm.isHidden() and cfg["latency_public_enabled"] is False
    ov.hide()


def test_settings_turned_on_meanwhile_closes_an_open_confirm(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    ov._internet_link.click()
    cfg["latency_public_enabled"] = True         # Settings, while the confirm was open
    ov._render_health_strip(mw)
    assert ov._internet_confirm.isHidden()


# --- the main widget's Turn on --------------------------------------------------------------------------

def test_turn_on_saves_applies_live_and_syncs_an_open_settings_dialog():
    from netspeedtray.core.config_controller import ConfigController
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    cfg = _cfg()
    manager = MagicMock()
    fake = SimpleNamespace(config=cfg, renderer=None, update=lambda: None, logger=MagicMock(),
                           apply_latency_probe=MagicMock())
    fake.config_controller = ConfigController(fake, manager)
    section = MagicMock()
    fake.settings_dialog = SimpleNamespace(config=dict(cfg), original_config=dict(cfg),
                                           interfaces_page=SimpleNamespace(connection=section))
    NetworkSpeedWidget.turn_on_internet_check(fake)
    assert cfg["latency_public_enabled"] is True                       # the probe reads this dict live
    assert manager.save.call_args[0][0]["latency_public_enabled"] is True   # persisted
    fake.apply_latency_probe.assert_called_once()
    assert fake.settings_dialog.config["latency_public_enabled"] is True
    assert fake.settings_dialog.original_config["latency_public_enabled"] is True   # Cancel keeps it
    section.set_public_enabled.assert_called_once_with(True)


def test_settings_section_shows_the_change_without_a_settings_edit(q_app):
    from netspeedtray.views.settings.pages.connection_section import ConnectionSettings
    changes = []
    sec = ConnectionSettings(lambda: changes.append(1), EN)
    sec.load_settings(_cfg())
    changes.clear()
    sec.set_public_enabled(True)
    assert sec.get_settings()["latency_public_enabled"] is True
    assert changes == []
    sec.deleteLater()


def test_settings_says_what_is_sent_to_whom_and_how_often(q_app):
    lat = constants.network.latency
    sub = EN.CONN_PUBLIC_SUB.format(count=lat.BURST_SIZE, seconds=f"{lat.OUTAGE_CYCLE_SEC:g}")
    assert "4 tiny pings every 3 seconds" in sub and "internet test server" in sub and "IP address" in sub
    assert EN.CONN_PUBLIC_LABEL == EN.INTERNET_WATCH_OFFER
    en = json.loads((PKG / "constants" / "locales" / "en_US.json").read_text(encoding="utf-8"))
    assert not [k for k, v in en.items() if "true internet" in str(v).lower()]


# --- Check connection: the same inline confirm --------------------------------------------------------

def _sheet(qt_owned, monkeypatch, cfg, offer):
    from netspeedtray.core import latency_probe as LP
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    monkeypatch.setattr(LP, "network_details", lambda: None)
    return qt_owned(ConnectionCheckSheet(cfg, EN, gateway_ip="192.0.2.1", autostart=False,
                                         offer_settings_link=True, internet_offer=offer))


def test_check_connection_asks_inline_and_keeps_its_figures(q_app, qt_owned, monkeypatch):
    cfg = _cfg()
    mw = _mw(cfg)
    sheet = _sheet(qt_owned, monkeypatch, cfg, IO.InternetOffer(mw, cfg))
    sheet.show()
    hint = sheet._hint
    assert hint.button().accessibleName() == EN.INTERNET_WATCH_ALWAYS and not hint.isHidden()
    hint.click()
    assert sheet.isVisible() and sheet.settings_request is None      # no trip to Settings
    assert not sheet._internet_confirm.isHidden()
    assert cfg["latency_public_enabled"] is False
    sheet._internet_confirm.turn_on_button().click()
    assert cfg["latency_public_enabled"] is True and sheet.isVisible()
    assert sheet._tick.isHidden() and sheet.include_internet()        # the next Check again includes it
    assert sheet._internet_confirm.is_done()
    sheet.hide()


def test_check_connection_not_now(q_app, qt_owned, monkeypatch):
    cfg = _cfg()
    mw = _mw(cfg)
    sheet = _sheet(qt_owned, monkeypatch, cfg, IO.InternetOffer(mw, cfg))
    sheet._hint.click()
    sheet._internet_confirm.not_now_button().click()
    assert sheet._internet_confirm.isHidden() and sheet._hint.isHidden()
    assert cfg["latency_public_enabled"] is False and cfg["internet_offer_declines"] == 1
    assert mw.internet_offer_snoozed is True
    again = _sheet(qt_owned, monkeypatch, cfg, IO.InternetOffer(mw, cfg))
    assert again._hint.isHidden()                                     # declined: no Settings link either


def test_check_connection_with_the_probe_off_keeps_the_settings_trip(q_app, qt_owned, monkeypatch):
    """The internet ping rides on the latency probe, whose switch is in Settings."""
    cfg = _cfg(latency_enabled=False)
    sheet = _sheet(qt_owned, monkeypatch, cfg, IO.InternetOffer(_mw(cfg), cfg))
    assert sheet._hint.button().accessibleName() == EN.NOTICE_TURN_ON_IN_SETTINGS
    assert not sheet._hint.isHidden()


def test_open_connection_check_hands_the_sheet_the_offer(q_app, monkeypatch):
    from netspeedtray.views.monitor import connection_check as view
    seen = {}

    class _Sheet:
        def __init__(self, *a, **kw):
            seen.update(kw)
            self.settings_request = None

        def exec(self):
            return 0
    monkeypatch.setattr(view, "ConnectionCheckSheet", _Sheet)
    cfg = _cfg()
    mw = _mw(cfg)
    mw.latency_probe = None
    view.open_connection_check(mw, cfg, EN)
    assert isinstance(seen["internet_offer"], IO.InternetOffer)
    seen.clear()
    view.open_connection_check(SimpleNamespace(latency_probe=None), cfg, EN)
    assert seen["internet_offer"] is None


# --- appearance ----------------------------------------------------------------------------------------

def test_high_contrast_uses_the_themes_colours(q_app, monkeypatch):
    from netspeedtray.utils import taskbar_appearance as ta
    hc = {"text": "#FFFFFF", "background": "#000000", "link": "#FFFF00", "focus": "#1AEBFF"}
    monkeypatch.setattr(ta, "read_high_contrast_link_colors", lambda: dict(hc))
    c = IO.InternetCheckConfirm(EN, "1.1.1.1")
    try:
        assert c.colors()["hc"] is True
        assert "#000000" in c.styleSheet() and "#FFFFFF" in c.styleSheet()
    finally:
        c.deleteLater()


# --- privacy.md -----------------------------------------------------------------------------------------

def test_privacy_md_describes_the_internet_check():
    text = (REPO / "privacy.md").read_text(encoding="utf-8")
    for needle in ("Also watch the internet", "off by default", "Cloudflare", "every 3 seconds",
                   "Turn on", "Not now", "public IP address", "nothing else leaves your PC"):
        assert needle in text, needle
    assert "true internet latency" not in text
