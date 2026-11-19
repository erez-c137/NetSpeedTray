"""
2.2.2: the IT policy ``DisableTips`` also hides the two unprompted suggestions 2.2.2 added (owner
decision 2026-11-19) - besides the first-run tip, "what's new" and the rate / star / support card:

- D5: the Monitor's "Also check your internet" offer (the Overview's link + TeachingTip, and the Check
  connection sheet's inline "Check your internet all the time" / its Settings trip).
- D6: the temperature setup that opens on its own when temperatures are on but no sensor reads.

The facts stay ("Router: steady", the sheet's one-off "Include the internet this time" tick), and the
features stay reachable in Settings (Settings > Network's internet check, Settings > Hardware's
temperature setup link). The policy is re-read at show time, like the support card.
"""
from pathlib import Path

import pytest

from netspeedtray.core import startup_notices
from netspeedtray.views.monitor import internet_offer as IO

from netspeedtray.tests.unit.test_router_internet_d5 import EN, _cfg, _events, _mw, _sheet, tab  # noqa: F401
from netspeedtray.tests.unit.test_lhm_setup_d6 import _FakeSetup, _nag_host, nag_world  # noqa: F401


@pytest.fixture
def policy(monkeypatch):
    state = type("P", (), {"on": True})()
    monkeypatch.setattr(startup_notices, "tips_disabled_by_policy", lambda: state.on)
    return state


# --- D5: the internet offer ------------------------------------------------------------------------------

def test_the_offer_is_not_available_under_the_policy(policy):
    cfg = _cfg()
    offer = IO.InternetOffer(_mw(cfg), cfg)
    assert offer.available() is False
    policy.on = False                                   # re-read on every ask: lifted, it is back
    assert offer.available() is True


def test_the_overview_shows_no_offer_but_keeps_the_router_line(tab, policy):
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    assert ov._internet_link.isHidden()
    assert "Router" in ov._context_r_lbl.text()          # the fact stays
    ov._open_internet_tip()                              # nor can a stale link open the tip
    assert ov._internet_tip is None
    policy.on = False                                    # lifted while the Monitor is open
    ov._render_health_strip(mw)
    assert not ov._internet_link.isHidden()


def test_a_policy_pushed_while_the_tip_is_open_closes_it(tab, policy):
    policy.on = False
    ov, mw, cfg = tab()
    ov._latency_events = _events(0)
    ov._render_health_strip(mw)
    ov._internet_link.click()
    assert ov._internet_tip is not None and ov._internet_tip.isVisible()
    policy.on = True
    ov._render_health_strip(mw)
    assert not ov._internet_tip.isVisible() and ov._internet_link.isHidden()
    assert cfg["latency_public_enabled"] is False and cfg.get("internet_offer_declines", 0) == 0


def test_the_sheet_has_no_inline_offer_but_keeps_the_one_off_tick(q_app, qt_owned, monkeypatch, policy):
    cfg = _cfg()
    sheet = _sheet(qt_owned, monkeypatch, cfg, IO.InternetOffer(_mw(cfg), cfg))
    assert sheet._hint.isHidden()
    assert not sheet._tick.isHidden()                   # the one-off check the user opened the sheet for


def test_the_sheet_has_no_settings_trip_with_the_probe_off(q_app, qt_owned, monkeypatch, policy):
    cfg = _cfg(latency_enabled=False)
    sheet = _sheet(qt_owned, monkeypatch, cfg, IO.InternetOffer(_mw(cfg), cfg))
    assert sheet._hint.isHidden()
    sheet = _sheet(qt_owned, monkeypatch, _cfg(), None)  # no main widget to turn it on: the Settings trip
    assert sheet._hint.isHidden()


# --- D6: the automatic temperature setup -------------------------------------------------------------------

def test_the_automatic_temperature_setup_never_shows_under_the_policy(q_app, nag_world, policy):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    from netspeedtray.views.widget import startup_notices as sn
    host = _nag_host()
    NetworkSpeedWidget._on_lhm_not_detected(host)
    assert _FakeSetup.made == []
    assert host.config.get("temp_onboarding_dismissed", False) is False   # not used up: lifted, still owed
    assert sn.moment_taken(host) is None
    policy.on = False
    NetworkSpeedWidget._on_lhm_not_detected(host)
    assert len(_FakeSetup.made) == 1


def test_a_policy_pushed_while_the_setup_waits_drops_it(q_app, nag_world, policy, monkeypatch):
    """Blocked by a gate it retries later; the retry re-reads the policy (and stops retrying)."""
    from PyQt6.QtCore import QTimer
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    retries = []
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, fn: retries.append(fn))
    policy.on = False
    host = _nag_host(_update_offer_blocked=lambda: True)
    host._on_lhm_not_detected = lambda: NetworkSpeedWidget._on_lhm_not_detected(host)
    NetworkSpeedWidget._on_lhm_not_detected(host)
    assert len(retries) == 1
    policy.on = True
    host._update_offer_blocked = lambda: False
    NetworkSpeedWidget._on_lhm_not_detected(host)          # the retry
    assert _FakeSetup.made == [] and len(retries) == 1


# --- docs ---------------------------------------------------------------------------------------------------

def test_deploying_md_lists_what_disabletips_covers():
    text = (Path(startup_notices.__file__).parents[3] / "DEPLOYING.md").read_text(encoding="utf-8")
    assert "Not covered by `DisableTips`" not in text
    section = text[text.index("DisableTips"):]
    for phrase in ("Also check your internet", "temperature setup"):
        assert phrase in section, phrase
