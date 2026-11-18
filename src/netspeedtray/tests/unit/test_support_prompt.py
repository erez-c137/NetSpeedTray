"""
2.2.2 D8: the one-time "rate / star / support" card in the Monitor - who sees it, when, and only once.

The decision (core/support_prompt) is tested on plain config dicts with an explicit clock; the moment
and the card (views/monitor/support_card) on a stand-in main widget with a real card (offscreen). The
registry (policy, Store-install record), the URL opener and the Support dialog are faked - a test never
opens a browser, never reads the machine's registry and never touches the network.
"""
import json
import os
import re
import socket
import types
import urllib.request
import winreg
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import support_prompt as logic
from netspeedtray.views.monitor import support_card as ui

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG
T0 = datetime(2026, 12, 1, 9, 0, 0, tzinfo=timezone.utc)
SEEN_MS = 300          # the "seen" wait in tests (the real one is SUPPORT_PROMPT_SEEN_MS = 4.5 s)


def _cfg(days_ago=None, **over):
    cfg = dict(DEFAULTS)
    if days_ago is not None:
        cfg["first_seen_at"] = logic.format_ts(T0 - timedelta(days=days_ago))
    cfg.update(over)
    return cfg


def _ago(days):
    return logic.format_ts(datetime.now(timezone.utc) - timedelta(days=days))


# --- the decision ----------------------------------------------------------------------------------

def test_defaults_and_constants():
    assert DEFAULTS["first_seen_at"] is None and DEFAULTS["support_prompt_shown"] is False
    assert constants.app.SUPPORT_PROMPT_AFTER_DAYS == 30
    assert 4000 <= constants.app.SUPPORT_PROMPT_SEEN_MS <= 5000
    assert constants.app.SUPPORT_PROMPT_COLLAPSE_MS == 150
    assert logic.clock_floor() == datetime(2026, 11, 1, tzinfo=timezone.utc)
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


def test_garbage_clock_restarts_now_but_never_moves_backwards_or_forwards():
    assert logic.first_seen_updates(_cfg(first_seen_at="yesterday"), T0) == {"first_seen_at": logic.format_ts(T0)}
    future = _cfg(first_seen_at=logic.format_ts(T0 + timedelta(days=3)))
    assert logic.first_seen_updates(future, T0) == {}       # never moved to an earlier "now"
    assert logic.due(future, prompts_allowed=True, now=T0 + timedelta(days=32)) is False
    assert logic.due(future, prompts_allowed=True, now=T0 + timedelta(days=33)) is True


def test_a_backward_clock_never_collapses_the_30_days():
    """Stored 2026-11-18; one launch with the clock at 2000-01-01 (below the floor: nothing written);
    the clock is fixed: still counted from 2026-11-18."""
    cfg = _cfg(first_seen_at="2026-11-18T10:00:00Z")
    y2k = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert logic.first_seen_updates(cfg, y2k) == {}
    assert logic.due(cfg, prompts_allowed=True, now=y2k) is False
    fixed = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    cfg.update(logic.first_seen_updates(cfg, fixed))
    assert cfg["first_seen_at"] == "2026-11-18T10:00:00Z"
    assert logic.due(cfg, prompts_allowed=True, now=fixed) is False
    # A stored date that is itself below the floor (hand-edited, or an old build) is never "due"
    # while the clock is also wrong, however long ago it looks.
    old = _cfg(first_seen_at="1999-11-01T00:00:00Z")
    assert logic.due(old, prompts_allowed=True, now=y2k) is False
    # A fresh install whose very first launch has a dead-battery clock writes nothing...
    fresh = _cfg()
    assert logic.first_seen_updates(fresh, y2k) == {}
    # ...so the clock starts on the first sane launch.
    assert logic.first_seen_updates(fresh, fixed) == {"first_seen_at": logic.format_ts(fixed)}


def test_upgrader_counts_from_the_first_222_run_never_from_history():
    cfg = dict(DEFAULTS, first_run_ever=False, first_run_v2_seen=True)
    cfg.pop("first_seen_at")
    cfg.pop("support_prompt_shown")
    cfg.update(logic.first_seen_updates(cfg, T0))
    assert logic.due(cfg, prompts_allowed=True, now=T0) is False
    assert logic.due(cfg, prompts_allowed=True, now=T0 + timedelta(days=29)) is False
    assert logic.due(cfg, prompts_allowed=True, now=T0 + timedelta(days=30)) is True


def test_config_schema_accepts_and_rejects():
    schema = constants.config.defaults.VALIDATION_SCHEMA
    assert re.fullmatch(schema["first_seen_at"]["regex"], logic.format_ts(T0))
    assert not re.fullmatch(schema["first_seen_at"]["regex"], "2026-12-01 09:00")
    assert schema["support_prompt_shown"]["type"] is bool


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


# --- the Store-install record (Rate only where the Store accepts it) ---------------------------------

@pytest.fixture
def fake_registry(monkeypatch):
    class _Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def install(hklm=None, hkcu=None):
        hives = {winreg.HKEY_LOCAL_MACHINE: hklm, winreg.HKEY_CURRENT_USER: hkcu}

        def open_key(hive, path, reserved=0, access=winreg.KEY_READ):
            assert access == winreg.KEY_READ
            if path != constants.app.INSTALL_SOURCE_REGISTRY_KEY or hives.get(hive) is None:
                raise FileNotFoundError(2, "no key")
            return _Key(hives[hive])

        def query(key, name):
            if name not in key.values:
                raise FileNotFoundError(2, "no value")
            return key.values[name]
        monkeypatch.setattr(winreg, "OpenKey", open_key)
        monkeypatch.setattr(winreg, "QueryValueEx", query)
    return install


def test_installed_from_store_reads_setups_record(fake_registry):
    fake_registry()
    assert logic.installed_from_store() is False
    fake_registry(hklm={"InstalledFrom": ("Store", winreg.REG_SZ)})
    assert logic.installed_from_store() is True
    fake_registry(hkcu={"InstalledFrom": ("store", winreg.REG_SZ)})
    assert logic.installed_from_store() is True
    fake_registry(hklm={"InstalledFrom": ("Setup", winreg.REG_SZ)})
    assert logic.installed_from_store() is False
    fake_registry(hklm={"InstalledFrom": (1, winreg.REG_DWORD)})
    assert logic.installed_from_store() is False


def test_setup_records_a_store_install_only_with_the_store_switch():
    """setup.iss writes the same key / value the app reads, only under /STORE, and removes it on
    uninstall. The Store payload's installerParameters are NOT changed here (owner, Partner Center)."""
    root = Path(__file__).resolve().parents[4]
    iss = (root / "build" / "setup.iss").read_text(encoding="utf-8")
    line = next(l for l in iss.splitlines() if "InstalledFrom" in l and l.startswith("Root:"))
    assert 'Root: HKLM; Subkey: "Software\\{#MyAppName}"' in line
    assert 'ValueName: "InstalledFrom"; ValueData: "Store"' in line
    assert "Check: IsStoreInstall" in line and "uninsdeletevalue" in line
    assert constants.app.INSTALL_SOURCE_REGISTRY_KEY.lower() == "software\\netspeedtray"
    assert re.search(r"function IsStoreInstall\(\): Boolean;.*CompareText\(ParamStr\(I\), '/STORE'\) = 0",
                     iss, re.S)
    payload = (root / ".github" / "workflows" / "store-submit.yml").read_text(encoding="utf-8")
    params = re.search(r'"installerParameters": "([^"]*)"', payload).group(1)
    assert len(params) <= 40
    # With /NORESTART traded for /STORE the payload would still fit the 40-character cap.
    assert len("/VERYSILENT /SUPPRESSMSGBOXES /STORE") <= 40


# --- the moment ------------------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch):
    """No policy, signed in long ago, not a Store install; no real browser, no network, no real
    Support dialog; a short 'seen' wait."""
    state = types.SimpleNamespace(policy_allows=True, since_sign_in=3600.0, opened=[], store_protocol=True,
                                  open_result=True, dialogs=[], net=[], store_install=False)
    monkeypatch.setattr(ui, "_policy_allows", lambda: state.policy_allows)
    monkeypatch.setattr(ui, "_seconds_since_sign_in", lambda: state.since_sign_in)
    monkeypatch.setattr(logic, "store_protocol_registered", lambda: state.store_protocol)
    monkeypatch.setattr(logic, "installed_from_store", lambda: state.store_install)
    monkeypatch.setattr(constants.app, "SUPPORT_PROMPT_SEEN_MS", SEEN_MS, raising=False)

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
    w.settings_dialog = None
    w._update_flyout = w._notice_flyout = w._usage_alert_flyout = w._running_panel = None
    w.windowIcon = lambda: QIcon()
    return w


def _host(qtbot):
    win = QWidget()
    qtbot.addWidget(win)
    lay = QVBoxLayout(win)
    tab = QWidget()
    tab.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    lay.addWidget(tab)              # the tab header
    page = QWidget()
    page.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # Qt's own fallback would land here, not on the tab
    lay.addWidget(page)             # the stack
    win.tab, win.page = tab, page
    return win, lay


def _attach(qtbot, w, world, now=None):
    win, lay = _host(qtbot)
    prompt = ui.SupportPrompt(win, w, I18nStrings("en_US"), opener=world.opener, focus_target=lambda: win.tab)
    card = prompt.attach(lay, 1, now=now if now is not None else datetime.now(timezone.utc))
    return win, lay, prompt, card


def _show_active(qtbot, win):
    win.show()
    win.activateWindow()
    qtbot.waitUntil(win.isActiveWindow, timeout=1000)


def test_29_days_monitor_open_shows_nothing(qtbot, world):
    w = _main(_cfg(first_seen_at=_ago(29)))
    assert _attach(qtbot, w, world)[3] is None


def test_30_days_monitor_open_shows_the_card_once(qtbot, world):
    w = _main(_cfg(first_seen_at=_ago(30)))
    win, lay, prompt, card = _attach(qtbot, w, world)
    assert isinstance(card, ui.SupportCard)
    assert lay.indexOf(card.parentWidget()) == 1               # under the tabs, above the pages
    _show_active(qtbot, win)
    qtbot.waitUntil(lambda: w.config.get("support_prompt_shown") is True, timeout=SEEN_MS + 2000)
    assert {"support_prompt_shown": True} in w.saved
    assert card.isVisible()                                    # stays until answered or the Monitor closes
    assert _attach(qtbot, w, world)[3] is None                 # the next Monitor open: never again
    assert world.opened == [] and world.net == []


def test_closed_before_it_was_really_seen_is_owed_again(qtbot, world):
    w = _main(_cfg(first_seen_at=_ago(31)))
    win, _lay, _p, card = _attach(qtbot, w, world)
    _show_active(qtbot, win)
    qtbot.wait(SEEN_MS // 3)
    win.close()
    assert not card._seen_timer.isActive()                     # hideEvent stopped it
    qtbot.wait(SEEN_MS + 200)
    assert w.config.get("support_prompt_shown") is False
    assert _attach(qtbot, w, world)[3] is not None             # the next open still owes it


def test_hidden_card_stops_counting(qtbot, world):
    w = _main(_cfg(first_seen_at=_ago(31)))
    win, _lay, _p, card = _attach(qtbot, w, world)
    _show_active(qtbot, win)
    assert card._seen_timer.isActive()
    card.hide()
    assert not card._seen_timer.isActive()


def test_seen_counts_only_while_the_monitor_is_active_and_not_minimized(qtbot, world):
    w = _main(_cfg(first_seen_at=_ago(31)))
    win, _lay, _p, card = _attach(qtbot, w, world)
    win.show()
    other = QWidget()
    qtbot.addWidget(other)
    other.show()
    other.activateWindow()                                     # the Monitor behind another window
    qtbot.waitUntil(other.isActiveWindow, timeout=1000)
    qtbot.wait(SEEN_MS + 200)
    assert w.config["support_prompt_shown"] is False
    assert not card._seen_timer.isActive()
    # Activated, then minimized before the wait is up: not seen, and the timer stops.
    win.activateWindow()
    qtbot.waitUntil(lambda: card._seen_timer.isActive(), timeout=1000)
    win.showMinimized()
    qtbot.waitUntil(lambda: not card._seen_timer.isActive(), timeout=1000)
    qtbot.wait(SEEN_MS + 200)
    assert w.config["support_prompt_shown"] is False
    # Restored and active again: re-armed from zero, then seen.
    win.showNormal()
    win.activateWindow()
    qtbot.waitUntil(lambda: w.config["support_prompt_shown"] is True, timeout=SEEN_MS + 2000)


@pytest.mark.parametrize("store_install", [False, True])
def test_rate_only_on_a_store_install(qtbot, world, store_install):
    world.store_install = store_install
    w = _main(_cfg(first_seen_at=_ago(40)))
    win, _lay, _p, card = _attach(qtbot, w, world)          # keep the window alive
    assert (ui.ACTION_RATE in card.buttons) is store_install
    assert ui.ACTION_STAR in card.buttons
    if store_install:
        assert "a rating or a star" in card.accessibleDescription()
    else:
        assert "a star helps others find it" in card.accessibleDescription()
        assert "rating" not in card.accessibleDescription()


@pytest.mark.parametrize("action", [ui.ACTION_RATE, ui.ACTION_STAR, ui.ACTION_SUPPORT, ui.ACTION_DISMISS])
def test_any_action_marks_it_done_forever(qtbot, world, action, monkeypatch):
    monkeypatch.setattr(ui.su, "prefers_reduced_motion", lambda: False)
    world.store_install = True
    w = _main(_cfg(first_seen_at=_ago(45)))
    win, lay, prompt, card = _attach(qtbot, w, world)
    _show_active(qtbot, win)
    target = {ui.ACTION_RATE: card.buttons[ui.ACTION_RATE], ui.ACTION_STAR: card.buttons[ui.ACTION_STAR],
              ui.ACTION_SUPPORT: card.support_link.button(), ui.ACTION_DISMISS: card.close_button}[action]
    trailing = QWidget(win)        # next in the focus chain after the card: Qt's own fallback lands here
    trailing.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    lay.addWidget(trailing)
    target.setFocus(Qt.FocusReason.TabFocusReason)               # a keyboard user on that action
    qtbot.waitUntil(target.hasFocus, timeout=1000)
    target.click()
    assert w.config["support_prompt_shown"] is True
    assert {"support_prompt_shown": True} in w.saved
    assert prompt._anim is not None                            # collapses (Reduce motion off)
    qtbot.waitUntil(lambda: lay.count() == 3, timeout=2000)    # the card (and its host) is gone
    assert win.tab.hasFocus()                                  # the keyboard went to the active tab
    if action == ui.ACTION_SUPPORT:
        qtbot.waitUntil(lambda: world.dialogs == [win], timeout=1000)   # the app's own dialog, over the Monitor
    else:
        qtbot.wait(50)
        assert world.dialogs == []
    expected = {ui.ACTION_RATE: ["ms-windows-store://review/?ProductId=XP9MHWQZJPLQM8"],
                ui.ACTION_STAR: ["https://github.com/erez-c137/NetSpeedTray"]}.get(action, [])
    assert world.opened == expected
    assert all(set(s) <= {"support_prompt_shown", "first_seen_at"} for s in w.saved)   # never WHICH action
    assert world.net == []
    assert _attach(qtbot, w, world)[3] is None


def test_reduce_motion_removes_the_card_at_once(qtbot, world, monkeypatch):
    monkeypatch.setattr(ui.su, "prefers_reduced_motion", lambda: True)
    w = _main(_cfg(first_seen_at=_ago(45)))
    win, lay, prompt, card = _attach(qtbot, w, world)
    _show_active(qtbot, win)
    host = prompt.host
    card.close_button.click()
    assert prompt._anim is None and not host.isVisible()
    qtbot.waitUntil(lambda: lay.count() == 2, timeout=1000)


def test_rate_falls_back_to_the_store_web_page(world):
    for protocol, result in ((False, True), (True, False)):
        world.opened.clear()
        world.store_protocol, world.open_result = protocol, result
        ui.open_store_review(world.opener)
        if protocol:
            assert world.opened == [logic.store_review_url(), logic.store_web_url()]
        else:
            assert world.opened == [logic.store_web_url()]


def test_links_go_through_the_apps_url_opener(qtbot, world):
    w = _main(_cfg(first_seen_at=_ago(30)))
    win, lay = _host(qtbot)
    prompt = ui.SupportPrompt(win, w, I18nStrings("en_US"))
    card = prompt.attach(lay, 1)
    card.buttons[ui.ACTION_STAR].click()
    assert world.opened == [logic.github_repo_url()]


def test_policy_off_never(qtbot, world):
    world.policy_allows = False
    w = _main(_cfg(first_seen_at=_ago(90)))
    assert _attach(qtbot, w, world)[3] is None
    assert w.config["support_prompt_shown"] is False           # not used up: simply never shown


class _Modal(QWidget):
    pass


@pytest.mark.parametrize("why", ["uptime", "sign_in", "fullscreen", "fs_latch", "modal", "settings",
                                 "_update_flyout", "_notice_flyout", "_usage_alert_flyout",
                                 "_running_panel", "tip"])
def test_never_on_launch_fullscreen_or_beside_another_card(qtbot, world, why):
    cfg = _cfg(first_seen_at=_ago(60))
    w = _main(cfg, uptime_s=30.0 if why == "uptime" else 3600.0, fullscreen=(why == "fullscreen"))

    def shown():
        x = QWidget()
        qtbot.addWidget(x)
        x.show()
        return x
    if why == "sign_in":
        world.since_sign_in = 20.0
    elif why == "fs_latch":
        w._fs_latch = types.SimpleNamespace(covered=True)
    elif why == "modal":
        m = QWidget()
        qtbot.addWidget(m)
        m.setWindowModality(Qt.WindowModality.ApplicationModal)
        m.show()
        qtbot.waitUntil(lambda: __import__("PyQt6.QtWidgets", fromlist=["QApplication"])
                        .QApplication.activeModalWidget() is not None, timeout=1000)
    elif why == "settings":
        w.settings_dialog = shown()
    elif why.startswith("_"):
        setattr(w, why, shown())
    elif why == "tip":
        tip = shown()
        w._startup_notices = types.SimpleNamespace(_live_card=lambda: tip)
    assert ui.blocked_reason(w) is not None
    assert _attach(qtbot, w, world)[3] is None
    assert cfg["support_prompt_shown"] is False                # a "not this time", never a "never"


def test_nothing_blocks_a_plain_open(qtbot, world):
    assert ui.blocked_reason(_main(_cfg(first_seen_at=_ago(60)))) is None


def test_saves_through_save_config_keys_so_settings_previews_never_reach_disk(qtbot, world):
    """With Settings open (its unsaved preview edits are in the live config), the card's state goes
    through the widget's save_config_keys: the file gets Settings' baseline + this key, never the
    preview."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    baseline = _cfg(first_seen_at=_ago(40), font_size=9)
    live = dict(baseline, font_size=20)                       # the user is previewing a bigger font
    written = []
    w = types.SimpleNamespace(config=live, logger=MagicMock())
    w.settings_dialog = types.SimpleNamespace(original_config=baseline, config=dict(live),
                                              unowned_app_state_keys=lambda: ("first_seen_at",))
    w.config_controller = types.SimpleNamespace(
        config_manager=types.SimpleNamespace(save=lambda cfg: written.append(dict(cfg))),
        update_config=MagicMock(side_effect=AssertionError("must not save the live (preview) config")))
    w.save_config_keys = lambda updates: NetworkSpeedWidget.save_config_keys(w, updates)
    ui.save_config(w, {"support_prompt_shown": True})
    assert written and written[-1]["support_prompt_shown"] is True
    assert written[-1]["font_size"] == 9                      # the preview never reached the file
    assert live["support_prompt_shown"] is True and baseline["support_prompt_shown"] is True


def test_launch_starts_the_clock_and_never_shows_the_card(monkeypatch, world):
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
    old = _main(_cfg(first_seen_at=_ago(99), first_run_ever=False, first_run_v2_seen=True))
    old.logger = MagicMock()
    widget_main.NetworkSpeedWidget._maybe_show_welcome(old)
    widget_main.NetworkSpeedWidget._maybe_show_welcome(fake)
    assert fake.config["first_seen_at"] == first               # never restarted
    assert built == []


def test_only_the_monitor_builds_the_card():
    root = Path(__file__).resolve().parents[2]
    users = sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in root.rglob("*.py")
                   if "tests" not in p.parts and "SupportPrompt(" in p.read_text(encoding="utf-8"))
    assert users == ["views/monitor/window.py"]


# --- the card --------------------------------------------------------------------------------------

def _card(qtbot, lang="en_US", width=900, show_rate=True):
    card = ui.SupportCard(I18nStrings(lang), show_rate=show_rate)
    qtbot.addWidget(card)
    card.resize(width, 120)
    card.show()
    return card


def test_card_text_actions_and_accessibility(qtbot):
    card = _card(qtbot)
    assert card.accessibleName() == "A month with NetSpeedTray"
    assert card.accessibleDescription() == ("It's free and always will be. If it's earned its spot on your "
                                            "taskbar, a rating or a star helps others find it.")
    assert [b.text() for b in card.buttons.values()] == ["Rate it in the Store", "Star on GitHub"]
    assert card.support_link.action_text() == "Support the project"
    assert card.close_button.accessibleName() == "Dismiss"
    # Equal weight: one style for both buttons, no accent fill.
    assert len({b.styleSheet() for b in card.buttons.values()}) == 1
    assert "accent']" not in "".join(b.styleSheet() for b in card.buttons.values())
    assert all(b.focusPolicy() & Qt.FocusPolicy.TabFocus for b in card.buttons.values())
    assert chr(ui._GLYPH_STAR) == ""


def test_card_enter_presses_the_focused_button(qtbot):
    card = _card(qtbot)
    got = []
    card.answered.connect(got.append)
    card.close_button.setFocus()
    qtbot.keyClick(card, Qt.Key.Key_Return)
    assert got == [ui.ACTION_DISMISS]


def test_card_fits_one_row_in_german_at_the_monitors_minimum(qtbot, world):
    """German, a Store install (both buttons + the link), in a real Monitor at its 660 px minimum:
    the actions stay on one row."""
    from netspeedtray.views.monitor.window import MonitorWindow
    world.store_install = True
    cfg = _cfg(first_seen_at=_ago(30))
    mw = MagicMock()
    mw.config = cfg
    mw.session_start_time = datetime.now() - timedelta(hours=2)
    mw.system_event_handler.is_fullscreen_active.return_value = False
    mw._fs_latch = None
    mw._startup_notices = None
    mw.settings_dialog = None
    mw._update_flyout = mw._notice_flyout = mw._usage_alert_flyout = mw._running_panel = None
    del mw.save_config_keys
    win = MonitorWindow(mw, cfg, I18nStrings("de_DE"))
    qtbot.addWidget(win)
    win.resize(660, 600)
    win.show()
    card = win._support_prompt.card
    qtbot.waitUntil(lambda: card.width() > 0, timeout=1000)
    qtbot.wait(50)
    if card.font().family().startswith("Segoe"):              # real Windows fonts loaded
        assert card.actions_on_one_row(), (card.width(), card.actions_width())
    for b in card.buttons.values():                           # nothing clipped, either way
        assert b.width() >= b.fontMetrics().horizontalAdvance(b.text())


def test_monitor_window_carries_the_card_under_the_tabs(qtbot, world):
    from netspeedtray.views.monitor.window import MonitorWindow
    mw = MagicMock()
    mw.session_start_time = datetime.now() - timedelta(hours=2)
    mw.system_event_handler.is_fullscreen_active.return_value = False
    mw._fs_latch = None
    mw._startup_notices = None
    mw.settings_dialog = None
    mw._update_flyout = mw._notice_flyout = mw._usage_alert_flyout = mw._running_panel = None
    del mw.save_config_keys
    plain_cfg = _cfg(first_seen_at=_ago(29))
    mw.config = plain_cfg
    plain = MonitorWindow(mw, plain_cfg, I18nStrings("en_US"))
    qtbot.addWidget(plain)
    assert plain._support_prompt.card is None                  # 29 days: an ordinary Monitor
    cfg = _cfg(first_seen_at=_ago(30))
    mw.config = cfg
    win = MonitorWindow(mw, cfg, I18nStrings("en_US"))
    qtbot.addWidget(win)
    card = win._support_prompt.card
    assert isinstance(card, ui.SupportCard)
    root = win.layout()
    assert root.indexOf(card.parentWidget()) == root.indexOf(win._stack) - 1
    assert win.minimumSizeHint().width() <= plain.minimumSizeHint().width()
    assert win._tab_bar.current_button() is not None


def test_every_locale_has_the_card_strings():
    keys = ["SUPPORT_PROMPT_TITLE", "SUPPORT_PROMPT_BODY", "SUPPORT_PROMPT_BODY_STAR_ONLY",
            "SUPPORT_PROMPT_RATE_BUTTON", "SUPPORT_PROMPT_STAR_BUTTON", "SUPPORT_PROMPT_SUPPORT_LINK",
            "SUPPORT_PROMPT_DISMISS"]
    folder = Path(constants.__file__).resolve().parent / "locales"
    en = json.loads((folder / "en_US.json").read_text(encoding="utf-8"))
    for f in folder.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for k in keys:
            assert data.get(k), f"{f.name} lacks {k}"
            if f.name != "en_US.json":
                assert len(data[k]) <= max(2 * len(en[k]), 24), f"{f.name} {k} too long"
        assert "NetSpeedTray" in data["SUPPORT_PROMPT_TITLE"]
        for gone in ("SUPPORT_PROMPT_NO_THANKS_BUTTON", "SUPPORT_PROMPT_SUPPORT_BUTTON"):
            assert gone not in data
