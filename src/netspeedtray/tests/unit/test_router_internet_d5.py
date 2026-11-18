"""
2.2.2 D5 - "Router" vs "Internet", said honestly, and the internet check in one click.

With the internet check off (the default) the Overview's health line came from ROUTER pings but said
"Connection: steady", so an ISP outage with Wi-Fi up read green. It now names the hop it measured,
offers the internet check beside it ("Also check your internet") with a TeachingTip-style confirm that
says what leaves the PC, and the Check connection sheet uses the same confirm (inline) instead of a
Settings trip.
"""
import json
import re
from datetime import datetime, timedelta
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
LRE, PDF = "‪", "‬"
LAT = constants.network.latency


def _cfg(**over):
    c = dict(DEFAULTS)
    c.update(over)
    return c


def _mw(config, turn_on=True):
    """A main widget: save_config_keys / config_controller.update_config write the shared dict (as the
    real ones do); turn_on_internet_check flips the internet check through it."""
    m = MagicMock()
    m.config = config
    m.system_power = None
    m.latency_gw = 4.0
    m.latency_anchor = None
    m.latency_link_lost = False
    m.config_controller.update_config.side_effect = lambda u, **k: config.update(u)
    m.save_config_keys.side_effect = lambda u: config.update(u)
    if turn_on:
        m.turn_on_internet_check.side_effect = lambda: m.save_config_keys({"latency_public_enabled": True})
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
        if t._internet_tip is not None:
            t._internet_tip.close_for_action()
        t.teardown()
        t.deleteLater()


def _events(count=0, hop="gateway", last=None, rows=100, first=None, start=None):
    now = datetime.now()
    start = start or now - timedelta(hours=24)
    return {"count": count, "last_start": last, "total_down_seconds": 0.0, "runs": [], "hop": hop,
            "rows": rows, "first": first or start, "window_start": start}


def _plain(html):
    return re.sub(r"<[^>]+>", "", html)


def _opened(ov):
    """Open the tip from the link (as a click would) and return it."""
    ov._internet_link.click()
    return ov._internet_tip


# --- host -> provider, the body ---------------------------------------------------------------------

@pytest.mark.parametrize("host, provider", [
    ("1.1.1.1", "Cloudflare"), (" 1.0.0.1 ", "Cloudflare"), ("2606:4700:4700:0:0:0:0:1111", "Cloudflare"),
    ("[2606:4700:4700::1001]", "Cloudflare"), ("One.One.One.One.", "Cloudflare"),
    ("8.8.8.8", "Google"), ("dns.google", "Google"), ("9.9.9.9", "Quad9"), ("208.67.222.222", "OpenDNS"),
    ("192.0.2.7", None), ("example.net", None), ("", None), (None, None),
])
def test_known_test_servers_are_named(host, provider):
    assert IO.provider_for_host(host) == provider


def test_host_labels_round_trip():
    assert IO.host_label("1.1.1.1") == "1.1.1.1 (Cloudflare)"
    assert IO.host_from_label("1.1.1.1 (Cloudflare)") == "1.1.1.1"
    assert IO.host_from_label("8.8.8.8 (Cloudflare)") == "8.8.8.8 (Cloudflare)"   # not ours: as typed
    assert IO.host_from_label(" ping.example.net ") == "ping.example.net"
    assert IO.include_once_target("1.1.1.1") == "1.1.1.1, Cloudflare"


def test_no_control_characters_in_a_left_to_right_ui():
    """FSI/PDI isolates made Qt map every installed font (852 files) in every language."""
    assert IO.offer_target("1.1.1.1") == "1.1.1.1 (Cloudflare)"
    body = IO.confirm_body(EN, "1.1.1.1")
    assert not any(ch in body for ch in "⁦⁧⁨⁩‪‫‬")


def test_the_target_is_embedded_ltr_in_hebrew():
    he = I18nStrings("he_IL")
    body = IO.confirm_body(he, "1.1.1.1")
    assert f"{LRE}1.1.1.1 (Cloudflare){PDF}" in body
    assert not any(ch in body for ch in "⁦⁧⁨⁩")   # never the isolates


def test_mb_per_day_is_computed_from_the_probe():
    per_echo = 20 + 8 + len(LAT.ECHO_PAYLOAD)
    exact = LAT.BURST_SIZE * 2 * per_echo * 86400 / LAT.OUTAGE_CYCLE_SEC / 1e6
    assert IO.mb_per_day("1.1.1.1") == round(exact) == 9


def test_the_body_says_what_leaves_the_pc():
    body = IO.confirm_body(EN, "1.1.1.1")
    assert body == ("Pings 1.1.1.1 (Cloudflare) 4 times every 3 seconds, about 9 MB a day. Cloudflare sees "
                    "your IP address, as any website does. Nothing else leaves your PC.")
    assert "192.0.2.7 sees your IP address" in IO.confirm_body(EN, "192.0.2.7")


def test_a_host_name_says_it_is_looked_up():
    """A typed NAME is resolved every cycle: "Nothing else leaves your PC" would be false without it."""
    body = IO.confirm_body(EN, "ping.example.net")
    assert "looks up its address each time" in body and "your DNS server sees the name" in body


def test_german_says_it_plainly():
    assert "Cloudflare sieht dabei deine IP-Adresse." in IO.confirm_body(I18nStrings("de_DE"), "1.1.1.1")


# --- labels by mode -----------------------------------------------------------------------------------

def test_router_only_data_says_router_never_connection(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    html, hop = ov._connection_health(mw)
    assert _plain(html) == "Router: steady" and hop == "gateway"
    ov._latency_events = _events(3, last=datetime.now() - timedelta(hours=2))
    assert _plain(ov._connection_health_html(mw)).startswith("Router: 3 drops")
    assert "Connection" not in ov._connection_health_html(mw)


def test_the_hop_name_is_primary_text_and_only_the_verdict_is_coloured(tab):
    from netspeedtray.utils import styles as su
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    html = ov._connection_health_html(mw)
    primary = su.semantic_colors()["text_primary"]
    assert html.startswith(f"<span style='color:{primary};'>Router:</span>")
    assert f"<span style='color:{LAT.VERDICT_COLORS['good']};'>steady</span>" in html


def test_internet_side_data_says_internet(tab):
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = 18.0
    ov._latency_events = _events(0, hop="anchor")
    html, hop = ov._connection_health(mw)
    assert _plain(html) == "Internet: steady" and hop == "anchor"


def test_internet_says_checking_until_its_own_reply(tab):
    """Just turned on: the router answers, the internet ping has not yet - "checking…", never "steady"."""
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = None
    ov._latency_events = _events(0, hop="anchor")
    assert _plain(ov._connection_health_html(mw)) == "Internet: checking…"
    mw.latency_anchor = 18.0
    ov._latency_events = _events(0, hop="anchor", rows=0)       # a reply, but no rows in the window yet
    assert _plain(ov._connection_health_html(mw)) == "Internet: checking…"


def test_internet_steady_speaks_only_for_what_it_covered(tab):
    """Turned on an hour ago, a 30-day window: "steady since HH:MM", not a month of "steady"."""
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = 18.0
    now = datetime.now()
    first = now - timedelta(minutes=50)
    ov._latency_events = _events(0, hop="anchor", rows=1000, first=first, start=now - timedelta(days=30))
    assert _plain(ov._connection_health_html(mw)) == f"Internet: steady since {first.strftime('%H:%M')}"
    ov._latency_events = _events(0, hop="anchor", rows=1000, start=now - timedelta(days=30),
                                 first=now - timedelta(days=30) + timedelta(minutes=2))
    assert _plain(ov._connection_health_html(mw)) == "Internet: steady"   # covers the window


def test_the_router_line_has_no_since(tab):
    ov, mw, cfg = tab()
    now = datetime.now()
    ov._latency_events = _events(0, first=now - timedelta(minutes=5), start=now - timedelta(days=30))
    assert _plain(ov._connection_health_html(mw)) == "Router: steady"


def test_drops_read_for_the_other_hop_are_never_shown(tab, monkeypatch):
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


def test_a_read_made_while_latency_was_off_claims_nothing(tab, monkeypatch):
    """Latency switched back on: the old read's empty drops must not flash "Router: steady"."""
    from netspeedtray.views.monitor.overview.tab import HOP_OFF, _read_db
    ws = MagicMock()
    ws.get_overview_window.return_value = ({}, {}, True)
    end = datetime.now()
    events = _read_db(ws, end - timedelta(hours=1), end, False, 1.0, False, None, False)[2]
    assert events == {"hop": HOP_OFF}
    ov, mw, cfg = tab()
    monkeypatch.setattr(ov, "_reload_window", lambda force=False: None)
    ov._latency_events = events
    assert ov._connection_health_html(mw) == ""


def test_window_reads_tag_their_drops_with_the_hop_and_coverage():
    from netspeedtray.core.db.history_reader import LossRow
    from netspeedtray.views.monitor.overview.tab import _read_db
    end = datetime.now()
    start = end - timedelta(hours=1)
    first = end - timedelta(minutes=10)
    ws = MagicMock()
    ws.get_overview_window.return_value = ({}, {}, True)
    for public, hop, stat in ((False, "gateway", LAT.STAT_GW_TIMEOUT), (True, "anchor", LAT.STAT_ANCHOR_TIMEOUT)):
        ws.get_loss_history.return_value = {stat: [LossRow(first + timedelta(seconds=3 * i), 0.0, None, 1)
                                                    for i in range(3)]}
        events = _read_db(ws, start, end, False, 1.0, True, None, public)[2]
        assert events["hop"] == hop and events["rows"] == 3
        assert events["first"] == first and events["window_start"] == start


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


def test_the_health_line_explains_itself(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = ov._context_r_lbl.toolTip()
    assert tip.startswith("Checks your router only, not your internet provider.") and "6 seconds" in tip
    assert ov._context_r_lbl.accessibleDescription() == tip
    assert ov._context_r_lbl.accessibleName() == "Router: steady"
    cfg["latency_public_enabled"] = True
    mw.latency_anchor = 18.0
    ov._latency_events = _events(0, hop="anchor")
    ov._render_health_strip(mw)
    assert "internet test server" in ov._context_r_lbl.toolTip()
    mw.latency_anchor = None                      # checking…: what it is AND where to turn it off (U7)
    ov._render_health_strip(mw)
    tip = ov._context_r_lbl.toolTip()
    assert "internet test server" in tip and tip.endswith(EN.INTERNET_CHECK_OFF_HINT)


# --- System power never pushes the health line out ----------------------------------------------------

def test_system_power_moves_left_when_latency_is_on(tab):
    ov, mw, cfg = tab()
    mw.system_power = 30.0
    mw.session_start_time = datetime.now() - timedelta(hours=1)
    ov._session_volume = None
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    assert _plain(ov._context_r_lbl.text()) == "Router: steady"
    assert not ov._internet_link.isHidden()
    assert "System 30 W" in ov._session_text(mw)


def test_system_power_keeps_the_right_slot_with_latency_off(tab):
    ov, mw, cfg = tab(_cfg(latency_enabled=False))
    mw.system_power = 30.0
    mw.session_start_time = datetime.now() - timedelta(hours=1)
    ov._session_volume = None
    assert ov._context_right_text(mw) == "System 30 W"
    assert "System" not in ov._session_text(mw)


# --- the offer beside the router line -----------------------------------------------------------------

def test_the_offer_sits_beside_a_router_line_only(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    link = ov._internet_link
    assert not link.isHidden()
    assert link.button().accessibleName() == EN.INTERNET_CHECK_OFFER == "Also check your internet"
    ov._latency_events = None                    # nothing claimed yet: no offer either
    ov._render_health_strip(mw)
    assert link.isHidden()


def test_the_strip_height_is_pinned(tab):
    ov, mw, cfg = tab()
    h = ov._strip_box.height() or ov._strip_box.minimumHeight()
    assert ov._strip_box.minimumHeight() == ov._strip_box.maximumHeight() == h
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    assert ov._strip_box.minimumHeight() == ov._strip_box.maximumHeight() == h
    assert h >= ov._internet_link.sizeHint().height()


def test_the_offer_is_not_shown_with_the_internet_check_on(tab):
    ov, mw, cfg = tab(_cfg(latency_public_enabled=True))
    mw.latency_anchor = 18.0
    ov._latency_events = _events(0, hop="anchor")
    ov._render_health_strip(mw)
    assert ov._internet_link.isHidden()


def test_no_tip_is_built_until_the_link_is_clicked(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    assert ov._internet_tip is None


def test_the_link_opens_a_teaching_tip_and_changes_nothing(tab):
    ov, mw, cfg = tab()
    ov.resize(900, 600)
    ov.show()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = _opened(ov)
    assert tip.isVisible() and tip.isWindow()
    assert tip.windowFlags() & Qt.WindowType.Popup
    assert tip.confirm.title() == "Also check your internet?"
    assert "1.1.1.1 (Cloudflare)" in tip.confirm.text() and "IP address" in tip.confirm.text()
    assert tip.width() == constants.styles.INTERNET_TIP_WIDTH + 2 * constants.styles.INTERNET_TIP_MARGIN
    assert not ov._internet_link.isHidden()      # the anchor stays; nothing in the page moved
    assert cfg["latency_public_enabled"] is False
    mw.turn_on_internet_check.assert_not_called()
    ov.hide()


def test_the_tips_beak_points_at_the_link(tab):
    ov, mw, cfg = tab()
    ov.resize(900, 600)
    ov.show()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = _opened(ov)
    link = ov._internet_link.button()
    centre = link.mapToGlobal(link.rect().center()).x()
    assert abs(tip.x() + tip._beak_x - centre) <= 1
    below = tip.beak_side() == "top"
    assert (tip.y() > link.mapToGlobal(link.rect().topLeft()).y()) is below
    ov.hide()


def test_esc_or_a_click_outside_is_not_a_decline(tab):
    ov, mw, cfg = tab()
    ov.resize(900, 600)
    ov.show()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = _opened(ov)
    QTest.keyClick(tip, Qt.Key.Key_Escape)
    assert not tip.isVisible()
    assert cfg.get("internet_offer_declines", 0) == 0 and mw.internet_offer_snoozed is False
    ov._render_health_strip(mw)
    assert not ov._internet_link.isHidden()
    tip = _opened(ov)
    tip.close()                                   # what a click outside a popup does
    assert cfg.get("internet_offer_declines", 0) == 0
    ov.hide()


def test_turn_on_turns_the_internet_check_on_and_says_checking(tab, monkeypatch):
    ov, mw, cfg = tab()
    ov.resize(900, 600)
    ov.show()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    reloads = []
    real = ov._reload_window
    monkeypatch.setattr(ov, "_reload_window", lambda force=False: (reloads.append(force), real(force)))
    tip = _opened(ov)
    tip.confirm.turn_on_button().click()
    mw.turn_on_internet_check.assert_called_once()
    assert cfg["latency_public_enabled"] is True
    assert not tip.isVisible()
    QApplication.processEvents()
    assert reloads.count(True) == 1               # one forced read, not two
    assert _plain(ov._context_r_lbl.text()) == "Internet: checking…"
    assert ov._context_r_lbl.toolTip().endswith("\nTurn it off any time in Settings.")   # + what it is (U7)
    QApplication.processEvents()
    assert QApplication.focusWidget() is ov._context_r_lbl       # Narrator reads the new status
    assert ov._internet_link.isHidden()
    ov.hide()


def test_not_now_hides_the_offer_for_the_session_and_counts(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = _opened(ov)
    assert tip.confirm.not_now_button().text() == "Not now"
    tip.confirm.not_now_button().click()
    assert not tip.isVisible() and ov._internet_link.isHidden()
    assert cfg["latency_public_enabled"] is False
    mw.turn_on_internet_check.assert_not_called()
    assert cfg["internet_offer_declines"] == 1
    mw.save_config_keys.assert_called_with({"internet_offer_declines": 1})
    again, _, _ = tab(cfg, mw)                   # the Monitor reopened, same session
    again._latency_events = _events(0)
    again._render_health_strip(mw)
    assert again._internet_link.isHidden()


def test_the_last_offer_says_no_thanks(tab):
    ov, mw, cfg = tab(_cfg(internet_offer_declines=LAT.INTERNET_OFFER_MAX_DECLINES - 1))
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = _opened(ov)
    assert tip.confirm.not_now_button().text() == EN.INTERNET_CONFIRM_NO_THANKS == "No thanks"


def test_two_not_nows_stop_the_offer_for_good(tab):
    cfg = _cfg(internet_offer_declines=LAT.INTERNET_OFFER_MAX_DECLINES)
    ov, mw, _ = tab(cfg)
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    assert ov._internet_link.isHidden()
    cfg["internet_offer_declines"] = 1
    ov._render_health_strip(mw)
    assert not ov._internet_link.isHidden()


def test_keyboard_opens_the_tip_on_turn_on_and_esc_returns_to_the_link(tab):
    ov, mw, cfg = tab()
    ov.resize(900, 600)
    ov.show()
    QTest.qWaitForWindowExposed(ov)
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    btn = ov._internet_link.button()
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(btn, Qt.Key.Key_Return)
    tip = ov._internet_tip
    assert tip.confirm.turn_on_button().hasFocus()
    QTest.keyClick(tip, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert not tip.isVisible() and btn.hasFocus()
    assert cfg["latency_public_enabled"] is False
    ov.hide()


def test_settings_turned_on_meanwhile_closes_an_open_tip(tab):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = _opened(ov)
    cfg["latency_public_enabled"] = True
    ov._render_health_strip(mw)
    assert not tip.isVisible()
    assert cfg.get("internet_offer_declines", 0) == 0


# --- the main widget's saves ----------------------------------------------------------------------------

def _fake_widget(cfg, dialog=None):
    from netspeedtray.core.config_controller import ConfigController
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    manager = MagicMock()
    fake = SimpleNamespace(config=cfg, renderer=None, update=lambda: None, logger=MagicMock(),
                           apply_latency_probe=MagicMock(), settings_dialog=dialog)
    fake.config_controller = ConfigController(fake, manager)
    fake.save_config_keys = lambda u: NetworkSpeedWidget.save_config_keys(fake, u)
    return fake, manager


def test_turn_on_saves_and_applies_live():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    cfg = _cfg()
    fake, manager = _fake_widget(cfg)
    NetworkSpeedWidget.turn_on_internet_check(fake)
    assert cfg["latency_public_enabled"] is True                       # the probe reads this dict live
    assert manager.save.call_args[0][0]["latency_public_enabled"] is True
    fake.apply_latency_probe.assert_called_once()


def test_turn_on_never_saves_settings_unsaved_preview_edits():
    """Settings open with an unsaved preview (font size 20 -> live config): the file gets the dialog's
    baseline + the new key, never the preview."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    saved = _cfg(font_size=9)
    live = dict(saved, font_size=20)
    section = MagicMock()
    dialog = SimpleNamespace(config=dict(live), original_config=dict(saved), isVisible=lambda: True,
                             _built_pages={"interfaces": SimpleNamespace(connection=section)})
    fake, manager = _fake_widget(live, dialog)
    NetworkSpeedWidget.turn_on_internet_check(fake)
    written = manager.save.call_args[0][0]
    assert written["font_size"] == 9 and written["latency_public_enabled"] is True
    assert live["latency_public_enabled"] is True and live["font_size"] == 20     # the preview stays on screen
    assert dialog.original_config["latency_public_enabled"] is True              # Cancel keeps it on
    assert dialog.config["latency_public_enabled"] is True                       # Save keeps it on
    section.set_public_enabled.assert_called_once_with(True)


def test_turn_on_does_not_build_the_lazy_network_page():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    cfg = _cfg()

    class Dialog:
        config, original_config, _built_pages = dict(cfg), dict(cfg), {}

        def isVisible(self):
            return True

        @property
        def interfaces_page(self):
            raise AssertionError("built the Network page")
    fake, _ = _fake_widget(cfg, Dialog())
    NetworkSpeedWidget.turn_on_internet_check(fake)
    assert fake.settings_dialog.original_config["latency_public_enabled"] is True


def test_not_now_survives_settings_cancel_and_save():
    """decline() goes through save_config_keys: both dialog snapshots take the count, so neither
    Settings' Cancel (original_config) nor its Save (config) brings the offer back."""
    saved = _cfg()
    live = dict(saved, font_size=20)
    dialog = SimpleNamespace(config=dict(live), original_config=dict(saved), _built_pages={},
                             isVisible=lambda: True)
    fake, manager = _fake_widget(live, dialog)
    offer = IO.InternetOffer(fake, live)
    offer.decline()
    assert live["internet_offer_declines"] == 1
    assert dialog.config["internet_offer_declines"] == 1
    assert dialog.original_config["internet_offer_declines"] == 1
    written = manager.save.call_args[0][0]
    assert written["internet_offer_declines"] == 1 and written["font_size"] == saved["font_size"]


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


def test_settings_uses_the_same_body_and_labels_the_servers(q_app):
    from netspeedtray.views.settings.pages.connection_section import ConnectionSettings
    sec = ConnectionSettings(lambda: None, EN)
    sec.load_settings(_cfg())
    assert sec._public_card._desc_label.text() == IO.confirm_body(EN, "1.1.1.1")
    items = [sec._host.itemText(i) for i in range(sec._host.count())]
    assert items == ["1.1.1.1 (Cloudflare)", "8.8.8.8 (Google)", "9.9.9.9 (Quad9)"]
    assert sec._host.currentText() == "1.1.1.1 (Cloudflare)"
    assert sec.get_settings()["latency_public_host"] == "1.1.1.1"            # saved as the address
    sec._host.setCurrentIndex(1)
    assert sec.get_settings()["latency_public_host"] == "8.8.8.8"
    assert "Google sees your IP address" in sec._public_card._desc_label.text()
    sec._host.setEditText("ping.example.net")
    assert sec.get_settings()["latency_public_host"] == "ping.example.net"
    assert "your DNS server sees the name" in sec._public_card._desc_label.text()
    sec.load_settings(_cfg(latency_public_host="9.9.9.9"))
    assert sec._host.currentText() == "9.9.9.9 (Quad9)"
    sec.deleteLater()


def test_no_true_internet_latency_and_no_watch_wording():
    en = json.loads((PKG / "constants" / "locales" / "en_US.json").read_text(encoding="utf-8"))
    assert not [k for k, v in en.items() if "true internet" in str(v).lower()]
    assert not [k for k, v in en.items() if re.search(r"\bwatch", str(v), re.I) and "INTERNET" in k]
    assert EN.CONN_PUBLIC_LABEL == EN.INTERNET_CHECK_OFFER == "Also check your internet"
    assert EN.INTERNET_CHECK_ALWAYS == "Check your internet all the time"


# --- Check connection: the same confirm, inline --------------------------------------------------------

def _sheet(qt_owned, monkeypatch, cfg, offer):
    from netspeedtray.core import latency_probe as LP
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    monkeypatch.setattr(LP, "network_details", lambda: None)
    return qt_owned(ConnectionCheckSheet(cfg, EN, gateway_ip="192.0.2.1", autostart=False,
                                         offer_settings_link=True, internet_offer=offer))


def test_the_sheets_tick_names_the_server(q_app, qt_owned, monkeypatch):
    sheet = _sheet(qt_owned, monkeypatch, _cfg(), None)
    assert sheet._tick.text() == "Include the internet this time (pings 1.1.1.1, Cloudflare)"


def test_check_connection_asks_inline_and_keeps_its_figures(q_app, qt_owned, monkeypatch):
    from netspeedtray.utils import styles as su
    cfg = _cfg()
    mw = _mw(cfg)
    sheet = _sheet(qt_owned, monkeypatch, cfg, IO.InternetOffer(mw, cfg))
    sheet.show()
    hint = sheet._hint
    assert hint.button().accessibleName() == EN.INTERNET_CHECK_ALWAYS and not hint.isHidden()
    assert sheet._internet_confirm.text() == ""                      # built empty: text on open
    hint.click()
    assert sheet.isVisible() and sheet.settings_request is None      # no trip to Settings
    confirm = sheet._internet_confirm
    assert not confirm.isHidden() and confirm.title() == "Also check your internet?"
    assert sheet._run_btn.styleSheet() == su.button_style(accent=False)   # one accent per surface
    assert cfg["latency_public_enabled"] is False
    confirm.turn_on_button().click()
    assert cfg["latency_public_enabled"] is True and sheet.isVisible()
    assert sheet._tick.isHidden() and sheet.include_internet()        # the next Check again includes it
    assert confirm.is_done() and confirm.text() == "The internet check is on. Turn it off any time in Settings."
    assert sheet._run_btn.styleSheet() == su.button_style(accent=True)
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

def test_high_contrast_uses_the_themes_colours_and_equal_buttons(q_app, monkeypatch):
    from netspeedtray.utils import taskbar_appearance as ta
    hc = {"text": "#FFFFFF", "background": "#000000", "link": "#FFFF00", "focus": "#1AEBFF"}
    monkeypatch.setattr(ta, "read_high_contrast_link_colors", lambda: dict(hc))
    c = IO.InternetCheckConfirm(EN, framed=True)
    try:
        c.set_host("1.1.1.1")
        c.resize(420, 160)
        c.show()
        QApplication.processEvents()
        assert c.colors()["hc"] is True
        assert "#000000" in c.styleSheet() and "#FFFFFF" in c.styleSheet()
        assert c.turn_on_button().height() == c.not_now_button().height()
        assert c.turn_on_button().height() >= constants.styles.INTERNET_CONFIRM_BUTTON_HEIGHT
    finally:
        c.hide()
        c.deleteLater()


# --- docs ------------------------------------------------------------------------------------------------

def test_privacy_md_describes_the_internet_check():
    text = (REPO / "privacy.md").read_text(encoding="utf-8")
    for needle in ("Also check your internet", "off by default", "Cloudflare", "every 3 seconds",
                   "about 9 MB a day", "Turn on", "Not now", "No thanks", "public IP address",
                   "nothing else leaves your PC", "Include the internet this time"):
        assert needle in text, needle
    assert "true internet latency" not in text and "Also watch the internet" not in text


def test_readme_and_database_docs_match_d5():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "**Router: Good / OK / Slow**" in readme and "Internet: Good / OK / Slow" not in readme
    db = (REPO / "DATABASE.md").read_text(encoding="utf-8")
    assert "Also measure true internet latency" not in db and "Also check your internet" in db


def test_not_now_moves_focus_to_the_health_line(tab):
    """The link is gone after Not now (offer snoozed): focus lands on the health line, never nowhere."""
    ov, mw, cfg = tab()
    ov.resize(900, 600)
    ov.show()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    tip = _opened(ov)
    tip.confirm.not_now_button().click()
    QApplication.processEvents()
    assert QApplication.focusWidget() is ov._context_r_lbl
    ov.hide()
