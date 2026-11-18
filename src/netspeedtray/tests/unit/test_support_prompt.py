"""
2.2.2 D8: the one-time "rate / star / support" card in the Monitor - who sees it, when, and only once.

The decision (core/support_prompt) is tested on plain config dicts with an explicit clock; the moment
and the card (views/monitor/support_card) on a stand-in main widget with a real card (offscreen). The
registry policy, the URL opener and the Support dialog are faked - a test never opens a browser, never
reads the machine's policy key and never touches the network.
"""
import os
import re
import socket
import types
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import support_prompt as logic
from netspeedtray.views.monitor import support_card as ui

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG
T0 = datetime(2026, 12, 1, 9, 0, 0, tzinfo=timezone.utc)
ACTIONS = (ui.ACTION_RATE, ui.ACTION_STAR, ui.ACTION_SUPPORT, ui.ACTION_NO_THANKS)


def _cfg(days_ago=None, **over):
    cfg = dict(DEFAULTS)
    if days_ago is not None:
        cfg["first_seen_at"] = logic.format_ts(T0 - timedelta(days=days_ago))
    cfg.update(over)
    return cfg


# --- the decision ----------------------------------------------------------------------------------

def test_defaults_and_constants():
    assert DEFAULTS["first_seen_at"] is None and DEFAULTS["support_prompt_shown"] is False
    assert constants.app.SUPPORT_PROMPT_AFTER_DAYS == 30
    assert constants.app.SUPPORT_PROMPT_SEEN_MS == 3000
    assert logic.store_review_url() == "ms-windows-store://review/?ProductId=XP9MHWQZJPLQM8"
    assert logic.store_web_url() == "https://apps.microsoft.com/detail/XP9MHWQZJPLQM8"
    assert logic.github_repo_url() == "https://github.com/erez-c137/NetSpeedTray"


def test_29_days_nothing_30_days_due():
    assert logic.due(_cfg(29), prompts_allowed=True, now=T0) is False
    assert logic.due(_cfg(29.99), prompts_allowed=True, now=T0) is False
    assert logic.due(_cfg(30), prompts_allowed=True, now=T0) is True
    assert logic.due(_cfg(400), prompts_allowed=True, now=T0) is True


def test_never_twice_and_never_with_the_policy():
    assert logic.due(_cfg(31, support_prompt_shown=True), prompts_allowed=True, now=T0) is False
    assert logic.due(_cfg(31), prompts_allowed=False, now=T0) is False
    assert logic.shown_updates() == {"support_prompt_shown": True}


def test_no_clock_no_card_and_the_clock_starts_once():
    cfg = _cfg()
    assert cfg["first_seen_at"] is None
    assert logic.due(cfg, prompts_allowed=True, now=T0) is False
    upd = logic.first_seen_updates(cfg, T0)
    assert upd == {"first_seen_at": "2026-12-01T09:00:00Z"}
    cfg.update(upd)
    assert logic.first_seen_updates(cfg, T0 + timedelta(days=5)) == {}     # never restarted


def test_garbage_or_far_future_clock_restarts_now():
    assert logic.first_seen_updates(_cfg(first_seen_at="yesterday"), T0) == {"first_seen_at": logic.format_ts(T0)}
    future = _cfg(first_seen_at=logic.format_ts(T0 + timedelta(days=3)))
    assert logic.first_seen_updates(future, T0) == {"first_seen_at": logic.format_ts(T0)}
    soon = _cfg(first_seen_at=logic.format_ts(T0 + timedelta(hours=2)))   # a clock a bit ahead: kept
    assert logic.first_seen_updates(soon, T0) == {}


def test_upgrader_counts_from_the_first_222_run_never_from_history():
    """A config written by 2.2.1 (years of history, no first_seen_at) gets nothing on upgrade day:
    the clock starts on its first 2.2.2 run."""
    cfg = dict(DEFAULTS, first_run_ever=False, first_run_v2_seen=True)
    cfg.pop("first_seen_at")
    cfg.pop("support_prompt_shown")
    upgrade_day = T0
    cfg.update(logic.first_seen_updates(cfg, upgrade_day))
    assert logic.due(cfg, prompts_allowed=True, now=upgrade_day) is False
    assert logic.due(cfg, prompts_allowed=True, now=upgrade_day + timedelta(days=29)) is False
    assert logic.due(cfg, prompts_allowed=True, now=upgrade_day + timedelta(days=30)) is True


def test_config_schema_accepts_and_rejects():
    from netspeedtray.utils.config import ConfigManager
    schema = constants.config.defaults.VALIDATION_SCHEMA
    assert re.fullmatch(schema["first_seen_at"]["regex"], logic.format_ts(T0))
    assert not re.fullmatch(schema["first_seen_at"]["regex"], "2026-12-01 09:00")
    assert schema["support_prompt_shown"]["type"] is bool
    assert ConfigManager is not None


def test_reset_all_keeps_both_keys():
    from netspeedtray.views.settings.dialog import SettingsDialog
    assert "first_seen_at" in SettingsDialog.RESET_ALL_KEEPS
    assert "support_prompt_shown" in SettingsDialog.RESET_ALL_KEEPS


def test_disable_tips_policy_is_the_switch(monkeypatch):
    from netspeedtray.core import startup_notices
    monkeypatch.setattr(startup_notices, "tips_disabled_by_policy", lambda: True)
    assert ui._policy_allows() is False
    monkeypatch.setattr(startup_notices, "tips_disabled_by_policy", lambda: False)
    assert ui._policy_allows() is True


# --- the moment ------------------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch):
    """No policy, signed in long ago; no real browser, no network, no real Support dialog."""
    state = types.SimpleNamespace(policy_allows=True, since_sign_in=3600.0, opened=[], store_protocol=True,
                                  open_result=True, dialogs=[], net=[])
    monkeypatch.setattr(ui, "_policy_allows", lambda: state.policy_allows)
    monkeypatch.setattr(ui, "_seconds_since_sign_in", lambda: state.since_sign_in)
    monkeypatch.setattr(logic, "store_protocol_registered", lambda: state.store_protocol)

    def opener(url):
        state.opened.append(url)
        return state.open_result
    state.opener = opener
    from netspeedtray.utils import helpers
    monkeypatch.setattr(helpers, "open_external", opener)

    class _Dialog:
        def __init__(self, i18n, parent=None, app_icon=None):
            state.dialogs.append(parent)

        def exec(self):
            return 0
    import netspeedtray.views.support_dialog as sd
    monkeypatch.setattr(sd, "SupportDialog", _Dialog)

    def no_net(*a, **k):
        state.net.append(a)
        raise AssertionError("the support card must never touch the network")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(urllib.request, "urlopen", no_net)
    return state


def _main(cfg, *, uptime_s=3600.0, fullscreen=False):
    w = types.SimpleNamespace()
    w.config = cfg
    w.saved = []
    w.update_config = lambda updates, save_to_disk=True: w.saved.append(dict(updates))
    w.session_start_time = datetime.now() - timedelta(seconds=uptime_s)
    w.system_event_handler = types.SimpleNamespace(is_fullscreen_active=lambda: fullscreen)
    w._fs_latch = None
    w._startup_notices = None
    w._update_flyout = w._notice_flyout = w._usage_alert_flyout = w._running_panel = None
    w.windowIcon = lambda: QIcon()
    return w


def _host(qtbot):
    win = QWidget()
    qtbot.addWidget(win)
    lay = QVBoxLayout(win)
    lay.addWidget(QWidget())        # the tab header
    lay.addWidget(QWidget())        # the stack
    return win, lay


def _attach(qtbot, w, world, now=None):
    win, lay = _host(qtbot)
    prompt = ui.SupportPrompt(win, w, I18nStrings("en_US"), opener=world.opener)
    card = prompt.attach(lay, 1, now=now if now is not None else datetime.now(timezone.utc))
    return win, lay, prompt, card


def test_29_days_monitor_open_shows_nothing(qtbot, world):
    w = _main(_cfg(29, first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=29))))
    assert _attach(qtbot, w, world)[3] is None


def test_30_days_monitor_open_shows_the_card_once(qtbot, world):
    w = _main(_cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=30))))
    win, lay, prompt, card = _attach(qtbot, w, world)
    assert isinstance(card, ui.SupportCard)
    assert lay.indexOf(card.parentWidget()) == 1               # under the tabs, above the pages
    win.show()
    qtbot.waitUntil(lambda: w.config.get("support_prompt_shown") is True,
                    timeout=constants.app.SUPPORT_PROMPT_SEEN_MS + 2000)
    assert {"support_prompt_shown": True} in w.saved
    assert card.isVisible()                                    # stays until answered or the Monitor closes
    # The next Monitor open: never again.
    assert _attach(qtbot, w, world)[3] is None
    assert world.opened == [] and world.net == []


def test_closed_before_it_was_really_seen_is_owed_again(qtbot, world):
    w = _main(_cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=31))))
    win, _lay, _p, card = _attach(qtbot, w, world)
    win.show()
    qtbot.wait(200)
    win.close()                                                # well under 3 s
    qtbot.wait(constants.app.SUPPORT_PROMPT_SEEN_MS + 300)
    assert w.config.get("support_prompt_shown") is False
    assert _attach(qtbot, w, world)[3] is not None             # the next open still owes it


@pytest.mark.parametrize("action", ACTIONS)
def test_any_button_marks_it_done_forever(qtbot, world, action):
    w = _main(_cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=45))))
    win, lay, _p, card = _attach(qtbot, w, world)
    win.show()
    card.buttons[action].click()
    assert w.config["support_prompt_shown"] is True
    assert {"support_prompt_shown": True} in w.saved
    qtbot.waitUntil(lambda: lay.count() == 2, timeout=1000)   # the card (and its host) is gone
    if action == ui.ACTION_SUPPORT:
        qtbot.waitUntil(lambda: world.dialogs == [win], timeout=1000)   # the app's own dialog, over the Monitor
    else:
        qtbot.wait(50)
        assert world.dialogs == []
    expected = {ui.ACTION_RATE: ["ms-windows-store://review/?ProductId=XP9MHWQZJPLQM8"],
                ui.ACTION_STAR: ["https://github.com/erez-c137/NetSpeedTray"]}.get(action, [])
    assert world.opened == expected
    # Zero telemetry: the only thing saved is "done" - never which button.
    assert all(set(s) <= {"support_prompt_shown", "first_seen_at"} for s in w.saved)
    assert world.net == []
    assert _attach(qtbot, w, world)[3] is None


def test_rate_falls_back_to_the_store_web_page(qtbot, world):
    for protocol, result in ((False, True), (True, False)):
        world.opened.clear()
        world.store_protocol, world.open_result = protocol, result
        ui.open_store_review(world.opener)
        if protocol:
            assert world.opened == [logic.store_review_url(), logic.store_web_url()]
        else:
            assert world.opened == [logic.store_web_url()]


def test_links_go_through_the_apps_url_opener(qtbot, world):
    """Without an injected opener the card uses helpers.open_external (here: the fake)."""
    w = _main(_cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=30))))
    win, lay = _host(qtbot)
    prompt = ui.SupportPrompt(win, w, I18nStrings("en_US"))
    card = prompt.attach(lay, 1)
    card.buttons[ui.ACTION_STAR].click()
    assert world.opened == [logic.github_repo_url()]


def test_policy_off_never(qtbot, world):
    world.policy_allows = False
    w = _main(_cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=90))))
    assert _attach(qtbot, w, world)[3] is None
    assert w.config["support_prompt_shown"] is False           # not used up: simply never shown


@pytest.mark.parametrize("why", ["uptime", "sign_in", "fullscreen", "other_card", "tip"])
def test_never_on_launch_fullscreen_or_beside_another_card(qtbot, world, why):
    cfg = _cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=60)))
    w = _main(cfg, uptime_s=30.0 if why == "uptime" else 3600.0, fullscreen=(why == "fullscreen"))
    if why == "sign_in":
        world.since_sign_in = 20.0
    if why == "other_card":
        other = QWidget()
        qtbot.addWidget(other)
        other.show()
        w._update_flyout = other
    if why == "tip":
        tip = QWidget()
        qtbot.addWidget(tip)
        tip.show()
        w._startup_notices = types.SimpleNamespace(_live_card=lambda: tip)
    assert _attach(qtbot, w, world)[3] is None
    assert cfg["support_prompt_shown"] is False                # a "not this time", never a "never"


def test_launch_starts_the_clock_and_never_shows_the_card(monkeypatch, world):
    """The startup path (_maybe_show_welcome) writes first_seen_at once and builds no card - even
    for a config whose clock ran out long ago."""
    from netspeedtray.views.widget import main as widget_main
    import netspeedtray.views.widget.startup_notices as sn
    built = []
    monkeypatch.setattr(ui, "SupportCard", lambda *a, **k: built.append(a))
    monkeypatch.setattr(sn, "StartupNotices", MagicMock())
    fake = _main(dict(DEFAULTS, first_run_ever=False, first_run_v2_seen=True))
    fake.logger = MagicMock()
    widget_main.NetworkSpeedWidget._maybe_show_welcome(fake)
    assert logic.parse_ts(fake.config["first_seen_at"]) is not None
    first = fake.config["first_seen_at"]
    old = _main(_cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=99)),
                     first_run_ever=False, first_run_v2_seen=True))
    old.logger = MagicMock()
    widget_main.NetworkSpeedWidget._maybe_show_welcome(old)
    widget_main.NetworkSpeedWidget._maybe_show_welcome(fake)
    assert fake.config["first_seen_at"] == first               # never restarted
    assert built == []


def test_only_the_monitor_builds_the_card():
    """Never on launch, never a flyout: SupportPrompt is built in exactly one place."""
    root = Path(__file__).resolve().parents[2]
    users = sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in root.rglob("*.py")
                   if "tests" not in p.parts and "SupportPrompt(" in p.read_text(encoding="utf-8"))
    assert users == ["views/monitor/window.py"]


# --- the card --------------------------------------------------------------------------------------

def _card(qtbot, lang="en_US", width=900):
    card = ui.SupportCard(I18nStrings(lang))
    qtbot.addWidget(card)
    card.resize(width, 120)
    card.show()
    return card


def test_card_text_buttons_and_accessibility(qtbot):
    card = _card(qtbot)
    assert card.accessibleName() == "Enjoying NetSpeedTray?"
    assert card.accessibleDescription() == "It's free and always will be."
    assert [b.text() for b in card.buttons.values()] == [
        "Rate it on the Microsoft Store", "Star it on GitHub", "Support it", "No thanks"]
    # Equal weight: one style for all four, "No thanks" included (no accent fill).
    sheets = {b.styleSheet() for b in card.buttons.values()}
    assert len(sheets) == 1
    from PyQt6.QtCore import Qt
    assert all(b.focusPolicy() & Qt.FocusPolicy.TabFocus for b in card.buttons.values())


def test_card_enter_presses_the_focused_button(qtbot):
    from PyQt6.QtCore import Qt
    card = _card(qtbot)
    got = []
    card.answered.connect(got.append)
    btn = card.buttons[ui.ACTION_NO_THANKS]
    btn.setFocus()
    qtbot.keyClick(card, Qt.Key.Key_Return)
    assert got == [ui.ACTION_NO_THANKS]


def test_card_reflows_for_long_words(qtbot):
    """German: four in a row on a wide Monitor, a 2 x 2 grid (or a column) on a narrow one - and the
    card never makes the Monitor wider than its own minimum."""
    host = QWidget()
    qtbot.addWidget(host)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    card = ui.SupportCard(I18nStrings("de_DE"))
    lay.addWidget(card)
    lay.addStretch(1)
    widths = [card._button_width(b) for b in card.buttons.values()]   # font-dependent: measure, don't guess
    host.resize(sum(widths) + 3 * ui._GAP + 160, 300)
    host.show()
    qtbot.waitUntil(lambda: card._columns == 4, timeout=1000)
    host.resize(max(widths) + 140, 300)
    qtbot.waitUntil(lambda: card._columns == 1, timeout=1000)
    host.resize(2 * max(widths) + ui._GAP + 140, 300)
    qtbot.waitUntil(lambda: card._columns == 2, timeout=1000)
    assert card.minimumSizeHint().width() <= 300
    for b in card.buttons.values():                              # nothing clipped
        assert b.width() >= b.fontMetrics().horizontalAdvance(b.text())


def test_monitor_window_carries_the_card_under_the_tabs(qtbot, world):
    from netspeedtray.views.monitor.window import MonitorWindow
    cfg = _cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=30)))
    mw = MagicMock()
    mw.config = cfg
    mw.session_start_time = datetime.now() - timedelta(hours=2)
    mw.system_event_handler.is_fullscreen_active.return_value = False
    mw._fs_latch = None
    mw._startup_notices = None
    mw._update_flyout = mw._notice_flyout = mw._usage_alert_flyout = mw._running_panel = None
    plain_cfg = _cfg(first_seen_at=logic.format_ts(datetime.now(timezone.utc) - timedelta(days=29)))
    mw.config = plain_cfg
    plain = MonitorWindow(mw, plain_cfg, I18nStrings("en_US"))
    qtbot.addWidget(plain)
    assert plain._support_prompt.card is None                    # 29 days: an ordinary Monitor
    mw.config = cfg
    win = MonitorWindow(mw, cfg, I18nStrings("en_US"))
    qtbot.addWidget(win)
    card = win._support_prompt.card
    assert isinstance(card, ui.SupportCard)
    root = win.layout()
    assert root.indexOf(card.parentWidget()) == root.indexOf(win._stack) - 1
    # The card never makes the Monitor wider than it already is.
    assert win.minimumSizeHint().width() <= plain.minimumSizeHint().width()


def test_every_locale_has_the_card_strings():
    keys = ["SUPPORT_PROMPT_TITLE", "SUPPORT_PROMPT_BODY", "SUPPORT_PROMPT_RATE_BUTTON",
            "SUPPORT_PROMPT_STAR_BUTTON", "SUPPORT_PROMPT_SUPPORT_BUTTON", "SUPPORT_PROMPT_NO_THANKS_BUTTON"]
    import json
    folder = Path(constants.__file__).resolve().parent / "locales"
    en = json.loads((folder / "en_US.json").read_text(encoding="utf-8"))
    for f in folder.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for k in keys:
            assert data.get(k), f"{f.name} lacks {k}"
            if f.name != "en_US.json":
                assert len(data[k]) <= max(2 * len(en[k]), 24), f"{f.name} {k} too long for a button"
