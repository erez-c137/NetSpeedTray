"""
2.2.2 D1 (first-run tip) and D2 (one-time "what's new"): who sees what, when, and only once.

The decision (core/startup_notices.plan_startup_notice) is tested on plain config dicts; the timing
and the card (views/widget/startup_notices.StartupNotices) on a stand-in widget with a real Flyout
(offscreen). The registry policy is faked - a test never touches the machine's policy key.
"""
import types
import winreg
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect, Qt

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import startup_notices as logic
from netspeedtray.views.widget import startup_notices as ui

cfg_defaults = constants.config.defaults

TIP, WN = logic.NOTICE_TIP, logic.NOTICE_WHATS_NEW
DEFAULTS = cfg_defaults.DEFAULT_CONFIG


def _upgrader_221() -> dict:
    """A config written by 2.2.1: past its first run, no last_seen_version key at all."""
    cfg = dict(DEFAULTS, first_run_ever=False, first_run_v2_seen=True)
    cfg.pop("last_seen_version", None)
    return cfg


def _has(keys=("WHATS_NEW_2_2_2",)):
    return lambda key: key in keys


def _plan(cfg, version, *, keys=("WHATS_NEW_2_2_2",), allowed=True):
    return logic.plan_startup_notice(cfg, version, has_string=_has(keys), prompts_allowed=allowed)


def _launch(cfg, version, **kw):
    """One launch, decision only: apply the startup updates and (if a notice shows) its seen-mark."""
    plan = _plan(cfg, version, **kw)
    cfg.update(plan.updates)
    if plan.notice:
        cfg.update(logic.shown_updates(plan.notice, cfg, version))
    return plan.notice


# --- the decision --------------------------------------------------------------------------------

def test_fresh_install_gets_the_tip_once_then_never():
    cfg = dict(DEFAULTS)
    assert _launch(cfg, "2.2.2") == TIP
    assert cfg["first_run_tip_shown"] is True and cfg["first_run_ever"] is False
    assert cfg["last_seen_version"] == "2.2.2"
    for _ in range(3):
        assert _launch(cfg, "2.2.2") is None


def test_fresh_install_never_gets_whats_new():
    cfg = dict(DEFAULTS)
    plan = _plan(cfg, "2.2.2")
    assert plan.notice == TIP
    assert plan.updates["last_seen_version"] == "2.2.2"   # recorded, so the next launch owes nothing
    assert "first_run_ever" not in plan.updates          # stays True until the tip really shows


def test_upgrade_from_221_shows_whats_new_once():
    cfg = _upgrader_221()
    assert _launch(cfg, "2.2.2") == WN
    assert cfg["last_seen_version"] == "2.2.2"
    assert cfg.get("first_run_tip_shown", False) is False   # an upgrader never gets the first-run tip
    assert _launch(cfg, "2.2.2") is None                    # relaunch: never again


def test_whats_new_is_not_marked_until_shown():
    plan = _plan(_upgrader_221(), "2.2.2")
    assert plan.notice == WN and plan.updates == {}       # still owed if this launch never shows it


def test_downgrade_shows_nothing_and_keeps_the_higher_mark():
    cfg = dict(_upgrader_221(), last_seen_version="2.2.3")
    plan = _plan(cfg, "2.2.2", keys=("WHATS_NEW_2_2_2", "WHATS_NEW_2_2_3"))
    assert plan.notice is None and plan.updates == {}
    # ...and going back up to 2.2.3 does not re-show 2.2.3's notice.
    assert _plan(cfg, "2.2.3", keys=("WHATS_NEW_2_2_3",)).notice is None


def test_prerelease_testers_see_it_once_on_the_final():
    cfg = _upgrader_221()
    assert _launch(cfg, "2.2.2-beta.1") is None
    assert cfg["last_seen_version"] == "2.2.2-beta.1"
    assert _launch(cfg, "2.2.2-beta.2") is None
    assert _launch(cfg, "2.2.2-rc.1") is None
    assert _launch(cfg, "2.2.2") == WN                     # the final, once
    assert _launch(cfg, "2.2.2") is None


def test_a_fresh_beta_install_gets_the_tip_then_whats_new_on_the_final():
    cfg = dict(DEFAULTS)
    assert _launch(cfg, "2.2.2-beta.1") == TIP
    assert _launch(cfg, "2.2.2") == WN
    assert _launch(cfg, "2.2.2") is None


def test_a_release_without_a_whats_new_string_shows_nothing():
    cfg = dict(_upgrader_221(), last_seen_version="2.2.2")
    plan = _plan(cfg, "2.2.3")                             # only WHATS_NEW_2_2_2 exists
    assert plan.notice is None
    assert plan.updates == {"last_seen_version": "2.2.3"}


def test_policy_off_shows_nothing_and_uses_both_up():
    fresh = dict(DEFAULTS)
    plan = _plan(fresh, "2.2.2", allowed=False)
    assert plan.notice is None
    assert plan.updates["first_run_ever"] is False         # lifting the policy later: no stale welcome
    up = _upgrader_221()
    plan = _plan(up, "2.2.2", allowed=False)
    assert plan.notice is None and plan.updates == {"last_seen_version": "2.2.2"}


def test_a_1x_upgrade_gets_the_20_welcome_not_whats_new():
    cfg = dict(_upgrader_221(), first_run_v2_seen=False)
    assert _plan(cfg, "2.2.2").notice is None


def test_never_both_tip_owed_across_an_upgrade_stays_the_tip():
    cfg = dict(DEFAULTS)
    cfg.update(_plan(cfg, "2.2.2").updates)                # tip owed, never shown (deferred all session)
    plan = _plan(cfg, "2.2.3", keys=("WHATS_NEW_2_2_3",))
    assert plan.notice == TIP
    assert plan.updates["last_seen_version"] == "2.2.3"


def test_reset_config_behaviour():
    from netspeedtray.views.settings.dialog import SettingsDialog
    # Settings > Reset all keeps both marks: no tip, no what's-new replay.
    assert "first_run_tip_shown" in SettingsDialog.RESET_ALL_KEEPS
    assert "last_seen_version" in SettingsDialog.RESET_ALL_KEEPS
    # A deleted config (all defaults) is a fresh install: the tip once more, never what's-new.
    assert _plan(dict(DEFAULTS), "2.2.2").notice == TIP


def test_version_helpers():
    assert logic.whats_new_key("2.2.2") == "WHATS_NEW_2_2_2"
    assert logic.whats_new_key("v2.2.2-beta.3") == "WHATS_NEW_2_2_2"
    assert logic.whats_new_key("nonsense") is None
    assert logic.is_prerelease("2.2.2-rc.1") and not logic.is_prerelease("2.2.2")
    assert logic.release_page_url("2.2.2") == "https://github.com/erez-c137/NetSpeedTray/releases/tag/v2.2.2"
    assert logic.later_version("2.2.2-beta.1", "2.2.2") == "2.2.2"
    assert logic.later_version("2.2.3", "2.2.2") == "2.2.3"
    assert logic.later_version(None, "2.2.2") == "2.2.2"


def test_config_schema_validates_the_new_keys(tmp_path):
    schema = cfg_defaults.VALIDATION_SCHEMA
    assert schema["first_run_tip_shown"]["default"] is False
    assert schema["last_seen_version"]["default"] is None
    from netspeedtray.utils.config import ConfigManager
    cm = ConfigManager(tmp_path / "NetSpeedTray_Config.json")
    cfg = cm.load()
    assert cfg["last_seen_version"] is None and cfg["first_run_tip_shown"] is False
    cfg["last_seen_version"] = "2.2.2-beta.1"
    cfg["first_run_tip_shown"] = True
    cm.save(cfg)
    again = ConfigManager(tmp_path / "NetSpeedTray_Config.json").load()
    assert again["last_seen_version"] == "2.2.2-beta.1" and again["first_run_tip_shown"] is True
    mangled = ConfigManager(tmp_path / "NetSpeedTray_Config.json")
    assert mangled._validate_value("last_seen_version", "2.2.2; <b>x</b>",
                                   schema["last_seen_version"]) is None


def test_every_whats_new_string_is_one_short_sentence_per_release():
    import json
    from pathlib import Path
    en = json.loads((Path(__file__).resolve().parents[2] / "constants" / "locales" / "en_US.json")
                    .read_text(encoding="utf-8"))
    prefix = logic.WHATS_NEW_KEY_PREFIX
    keys = [k for k in en if k.startswith(prefix) and k[len(prefix):][:1].isdigit()]
    assert "WHATS_NEW_2_2_2" in keys
    for k in keys:
        assert logic.whats_new_key(k[len(prefix):].replace("_", ".")) == k
        assert len(en[k]) <= 160, f"{k} is a card, not release notes - keep it to one short sentence"


# --- the policy key ---------------------------------------------------------------------------------

@pytest.fixture
def fake_policy(monkeypatch):
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
            if path != constants.app.POLICY_REGISTRY_KEY or hives.get(hive) is None:
                raise FileNotFoundError(2, "no key")
            return _Key(hives[hive])

        def query(key, name):
            if name not in key.values:
                raise FileNotFoundError(2, "no value")
            return key.values[name]

        monkeypatch.setattr(winreg, "OpenKey", open_key)
        monkeypatch.setattr(winreg, "QueryValueEx", query)
    return install


def test_disable_tips_policy(fake_policy):
    assert constants.app.POLICY_DISABLE_TIPS_VALUE == "DisableTips"
    fake_policy()
    assert logic.tips_disabled_by_policy() is False
    fake_policy(hklm={"DisableTips": (1, winreg.REG_DWORD)})
    assert logic.tips_disabled_by_policy() is True
    fake_policy(hkcu={"DisableTips": (1, winreg.REG_DWORD)})
    assert logic.tips_disabled_by_policy() is True
    fake_policy(hklm={"DisableTips": ("1", winreg.REG_SZ)})   # wrong type: ignored (logged)
    assert logic.tips_disabled_by_policy() is False
    # The two policies are independent.
    fake_policy(hklm={"DisableUpdateCheck": (1, winreg.REG_DWORD)})
    assert logic.tips_disabled_by_policy() is False
    from netspeedtray.core.update_checker import update_check_disabled_by_policy
    assert update_check_disabled_by_policy() is True


# --- on screen -------------------------------------------------------------------------------------

@pytest.fixture
def screen(monkeypatch):
    """The widget's world: no policy, signed in long ago, nobody else's card up."""
    state = types.SimpleNamespace(policy=False, since_sign_in=3600.0)
    monkeypatch.setattr(logic, "tips_disabled_by_policy", lambda: state.policy)
    monkeypatch.setattr(ui, "_seconds_since_sign_in", lambda: state.since_sign_in)
    return state


def _widget(cfg, *, visible=True, blocked=False, language="en_US"):
    w = types.SimpleNamespace()
    w.config = cfg
    w.saved = []
    w.update_config = lambda updates, save_to_disk=True: w.saved.append(dict(updates))
    w.visible = visible
    w.isVisible = lambda: w.visible
    w.blocked = blocked
    w._update_offer_blocked = lambda: w.blocked
    w._app_is_quitting = lambda: False
    w.yielding = False
    w.position_manager = types.SimpleNamespace(is_yielding_to_apps=lambda: w.yielding)
    w.frameGeometry = lambda: QRect(1500, 1000, 120, 40)
    w.i18n = I18nStrings(language)
    w.open_monitor_window = MagicMock()
    return w


def _shown(notices):
    card = notices._live_card()
    return card is not None and card.isVisible()


def _close(notices):
    card = notices._live_card()
    if card is not None:
        card._dismiss()                                       # as the X / Esc does


def test_tip_shows_after_the_delay_once_then_never(qtbot, screen):
    w = _widget(dict(DEFAULTS))
    n = ui.StartupNotices(w, version="2.2.2")
    assert n.start() == TIP
    assert n._timer.isActive() and n._timer.interval() == constants.app.STARTUP_NOTICE_DELAY_MS
    assert w.config["first_run_ever"] is True               # not used up before it shows
    n.try_show()
    assert _shown(n)
    card = n._live_card()
    assert card.accessibleName() == w.i18n.FIRST_RUN_TIP_TITLE
    assert w.config["first_run_tip_shown"] is True and w.config["first_run_ever"] is False
    assert w.config["last_seen_version"] == "2.2.2"
    _close(n)
    n2 = ui.StartupNotices(w, version="2.2.2")              # next launch
    assert n2.start() is None
    n2.try_show()
    assert n2._live_card() is None


@pytest.mark.parametrize("why", ["hidden", "fullscreen", "yielding", "sign_in", "other_card", "settings"])
def test_a_blocked_moment_defers_the_notice_never_loses_it(qtbot, screen, why):
    w = _widget(dict(DEFAULTS))
    other = None
    if why == "hidden":
        w.visible = False
    elif why == "fullscreen":
        w.blocked = True
    elif why == "yielding":
        w.yielding = True
    elif why == "sign_in":
        screen.since_sign_in = 30.0
    elif why == "settings":
        w.settings_dialog = types.SimpleNamespace(isVisible=lambda: True)
    else:
        from netspeedtray.views.flyout import Flyout
        other = Flyout("Update", "x", auto_dismiss_ms=0)
        qtbot.addWidget(other)
        other.show()
        w._update_flyout = other
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n._timer.stop()
    n.try_show()
    assert n._live_card() is None
    if why == "hidden":
        assert not n._timer.isActive()                         # no 30 s polling: on_widget_shown re-arms
    else:
        assert n._timer.isActive() and n._timer.interval() == constants.app.STARTUP_NOTICE_RETRY_MS
    assert w.config.get("first_run_tip_shown") is False      # still owed
    # The moment passes: it shows on the retry.
    w.visible, w.blocked, w.yielding, screen.since_sign_in = True, False, False, 3600.0
    w.settings_dialog = None
    if other is not None:
        other.hide()
    n.on_widget_shown()
    assert n._timer.interval() == constants.app.STARTUP_NOTICE_DELAY_MS
    n.try_show()
    assert _shown(n)
    _close(n)


def test_owed_tip_survives_a_restart_when_never_shown(qtbot, screen):
    w = _widget(dict(DEFAULTS), visible=False)
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()                                              # hidden all session; app exits
    w2 = _widget(w.config)
    n2 = ui.StartupNotices(w2, version="2.2.2")
    assert n2.start() == TIP
    n2.try_show()
    assert _shown(n2)
    _close(n2)


def test_upgrade_shows_whats_new_once_and_links_this_release(qtbot, screen):
    w = _widget(_upgrader_221())
    n = ui.StartupNotices(w, version="2.2.2")
    assert n.start() == WN
    n.try_show()
    assert _shown(n)
    card = n._live_card()
    assert card.accessibleName() == w.i18n.WHATS_NEW_TITLE.format(version="2.2.2")
    assert card.accessibleDescription() == w.i18n.WHATS_NEW_2_2_2
    assert w.config["last_seen_version"] == "2.2.2"
    with patch("netspeedtray.utils.helpers.open_external") as opened:
        card._action_btn.click()
    opened.assert_called_once_with("https://github.com/erez-c137/NetSpeedTray/releases/tag/v2.2.2")
    assert n._live_card() is None
    assert ui.StartupNotices(w, version="2.2.2").start() is None


def test_fresh_install_shows_no_whats_new_card(qtbot, screen):
    w = _widget(dict(DEFAULTS))
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()
    assert n._live_card().accessibleName() == w.i18n.FIRST_RUN_TIP_TITLE
    _close(n)


def test_downgrade_shows_no_card(qtbot, screen):
    w = _widget(dict(_upgrader_221(), last_seen_version="2.2.3"))
    n = ui.StartupNotices(w, version="2.2.2")
    assert n.start() is None
    n.try_show()
    assert n._live_card() is None and w.saved == []


def test_tip_open_monitor_and_got_it(qtbot, screen):
    w = _widget(dict(DEFAULTS))
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()
    n._live_card()._action_btn.click()
    qtbot.waitUntil(lambda: w.open_monitor_window.called, timeout=1000)
    assert n._live_card() is None
    w2 = _widget(dict(DEFAULTS))
    n2 = ui.StartupNotices(w2, version="2.2.2")
    n2.start()
    n2.try_show()
    n2._live_card()._secondary_btn.click()
    assert n2._live_card() is None and not w2.open_monitor_window.called


def test_policy_pushed_while_waiting_drops_it(qtbot, screen):
    w = _widget(dict(DEFAULTS), visible=False)
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()
    screen.policy = True
    w.visible = True
    n.try_show()
    assert n._live_card() is None and n._pending is None


def test_policy_pushed_while_the_card_hides_with_the_widget_drops_it(qtbot, screen):
    w = _widget(dict(DEFAULTS))
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()
    n.on_widget_hidden()
    screen.policy = True
    n.on_widget_shown()
    n.try_show()
    assert n._live_card() is None
    n._timer.stop()                                           # (the manual try_show stood in for it)
    n.on_widget_shown()
    assert not n._timer.isActive()                            # nothing left to show


def test_policy_off_at_start_shows_nothing(qtbot, screen):
    screen.policy = True
    w = _widget(dict(DEFAULTS))
    n = ui.StartupNotices(w, version="2.2.2")
    assert n.start() is None
    assert w.config["first_run_ever"] is False
    w2 = _widget(_upgrader_221())
    assert ui.StartupNotices(w2, version="2.2.2").start() is None
    assert w2.config["last_seen_version"] == "2.2.2"


def test_card_hides_with_the_widget_and_comes_back(qtbot, screen):
    w = _widget(dict(DEFAULTS))
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()
    n.on_widget_hidden()                                      # a fullscreen app
    assert n._live_card() is not None and not n._live_card().isVisible()
    w.visible = True
    n.on_widget_shown()
    assert n._timer.isActive()
    n.try_show()
    assert _shown(n)
    assert sum(1 for s in w.saved if s.get("first_run_tip_shown")) == 1   # still one showing, saved once
    _close(n)


def test_widget_wires_the_notices_and_the_1x_welcome_records_the_version(monkeypatch):
    """main._maybe_show_welcome: fresh / 2.x configs go to StartupNotices; the 2.0 welcome dialog's
    launch (a 1.x upgrade) records the version so the next launch doesn't follow it with what's-new."""
    from netspeedtray.views.widget import main as main_mod
    W = main_mod.NetworkSpeedWidget
    started = []

    class _Fake:
        def __init__(self, widget):
            started.append(widget)

        def start(self):
            return None

    monkeypatch.setattr(ui, "StartupNotices", _Fake)
    stub = types.SimpleNamespace(config=dict(DEFAULTS), logger=MagicMock(), update_config=MagicMock())
    W._maybe_show_welcome(stub)
    assert started == [stub]
    one_x = types.SimpleNamespace(config=dict(_upgrader_221(), first_run_v2_seen=False), logger=MagicMock(),
                                  update_config=MagicMock(), i18n=I18nStrings("en_US"))
    with patch("netspeedtray.views.welcome_dialog.WelcomeDialog") as dlg:
        dlg.return_value.action = None
        W._maybe_show_welcome(one_x)
    assert len(started) == 1
    # 2.2.2 D8: the same launch also starts the support card's clock (first_seen_at), first.
    calls = [c.args[0] for c in one_x.update_config.call_args_list]
    assert len(calls) == 2 and set(calls[0]) == {"first_seen_at"}
    assert calls[1] == {"first_run_v2_seen": True, "last_seen_version": constants.app.VERSION}


# --- the card -------------------------------------------------------------------------------------

def test_flyout_secondary_button_and_keyboard(qtbot):
    from netspeedtray.views.flyout import Flyout
    f = Flyout("T", "M", action_text="Go", secondary_text="Got it", close_text="Close",
               auto_dismiss_ms=0, focusable=True)
    qtbot.addWidget(f)
    assert not (f.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
    assert f._close_btn is None                               # "Got it" is the way out: no X
    assert f.accessibleName() == "T" and f.accessibleDescription() == "M"
    fired = []
    f.secondary_clicked.connect(lambda: fired.append("secondary"))
    with qtbot.waitSignal(f.dismissed, timeout=500):
        f._secondary_btn.click()
    assert fired == ["secondary"]

    g = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0, focusable=True)
    qtbot.addWidget(g)
    g.show()
    with qtbot.waitSignal(g.dismissed, timeout=500):
        qtbot.keyClick(g, Qt.Key.Key_Escape)


def test_flyout_default_stays_no_focus(qtbot):
    from netspeedtray.views.flyout import Flyout
    f = Flyout("T", "M", action_text="Go", auto_dismiss_ms=0)
    qtbot.addWidget(f)
    assert f.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert f._action_btn.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_flyout_high_contrast_uses_system_colors(qtbot, monkeypatch):
    from netspeedtray.utils import taskbar_appearance
    from netspeedtray.views.flyout import Flyout
    monkeypatch.setattr(taskbar_appearance, "read_high_contrast_ui_colors",
                        lambda: {"text": "#FFFF00", "background": "#000000",
                                 "highlight": "#1AEBFF", "highlight_text": "#000000"})
    f = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0, focusable=True)
    qtbot.addWidget(f)
    assert f._colors["card_bg"] == "#000000" and f._colors["card_stroke"] == "#FFFF00"
    # The primary is filled with Highlight (as the internet confirm's, combined review U7), its ring the
    # window text; the secondary is plain text-on-Window with a Highlight ring.
    assert "background-color: #1AEBFF" in f._action_btn.styleSheet()
    assert "border-color: #FFFF00" in f._action_btn.styleSheet()
    sec = f._secondary_btn.styleSheet()
    assert "background-color: #000000" in sec and "border: 2px solid #1AEBFF" in sec


def test_flyout_buttons_mirror_right_to_left(qtbot):
    from PyQt6.QtCore import QPoint
    from netspeedtray.views.flyout import Flyout
    ltr = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0)
    qtbot.addWidget(ltr)
    ltr.show_at(QPoint(1500, 1000))
    assert ltr._action_btn.x() < ltr._secondary_btn.x()
    rtl = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0)
    rtl.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    qtbot.addWidget(rtl)
    rtl.show_at(QPoint(1500, 1000))
    assert rtl._action_btn.x() > rtl._secondary_btn.x()


# --- review round 1 (#592): code majors ---------------------------------------------------------

class _SettingsMain:
    """The parts of the widget SettingsDialog touches (ConfigController's merge semantics)."""

    def __init__(self, cfg):
        self.config = dict(cfg)
        self.config_manager = MagicMock()
        self.calls = []
        self.metrics_endpoint = None

    def handle_settings_changed(self, updated, save_to_disk=True):
        self.calls.append((dict(updated), save_to_disk))
        self.config.update(updated)

    def toggle_startup(self, on):
        pass

    def update(self):
        pass

    def width(self):
        return 200


@pytest.mark.parametrize("close", ["save", "cancel", "preview"])
def test_open_settings_never_undoes_a_notice_shown_meanwhile(qtbot, monkeypatch, close):
    """M1: Settings' snapshot is from when it opened. A card shown while it is open must stay shown
    through a preview, Save and Cancel."""
    from PyQt6.QtWidgets import QMessageBox
    from netspeedtray.views.settings.dialog import SettingsDialog
    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    cfg = dict(DEFAULTS)                                        # a fresh install, tip still owed
    main = _SettingsMain(cfg)
    dlg = SettingsDialog(main_widget=main, config=cfg, version="2.2.2", i18n=I18nStrings("en_US"),
                         available_interfaces=["Ethernet"], is_startup_enabled=False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.settings_changed.connect(lambda c: main.handle_settings_changed(c, save_to_disk=False))
    qtbot.addWidget(dlg)
    dlg.show()
    # The tip shows while Settings is open.
    main.config.update(first_run_ever=False, first_run_tip_shown=True, last_seen_version="2.2.2")
    if close == "preview":
        dlg._update_timer.stop()
        dlg._emit_settings_changed_throttled()
    elif close == "save":
        dlg._save_and_close()
    else:
        dlg.reject()
    assert main.calls, "the dialog wrote nothing - the test proves nothing"
    assert main.config["first_run_tip_shown"] is True
    assert main.config["first_run_ever"] is False
    assert main.config["last_seen_version"] == "2.2.2"
    for sent, _ in main.calls:
        assert "first_run_tip_shown" not in sent and "last_seen_version" not in sent


def test_settings_still_sends_the_keys_its_pages_own(qtbot):
    from netspeedtray.views.settings.dialog import SettingsDialog
    main = _SettingsMain(DEFAULTS)
    dlg = SettingsDialog(main_widget=main, config=dict(DEFAULTS), version="2.2.2",
                         i18n=I18nStrings("en_US"), available_interfaces=["Ethernet"],
                         is_startup_enabled=False)
    qtbot.addWidget(dlg)
    dlg._build_all_pages()
    out = dlg._for_widget(dlg.get_settings())
    assert "start_with_windows" in out                          # a RESET_ALL_KEEPS key a page shows
    assert "font_size" in out
    assert not ({"first_run_tip_shown", "last_seen_version", "update_snooze", "position_x"} & set(out))


def _visible_tip(cfg=None, **kw):
    w = _widget(dict(DEFAULTS) if cfg is None else cfg, **kw)
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n._timer.stop()
    n.try_show()
    assert _shown(n)
    return w, n


def test_fullscreen_with_keep_visible_hides_the_card_and_it_comes_back(qtbot, screen):
    """M2: "Keep visible in fullscreen" keeps the widget over a game - never the card."""
    w, n = _visible_tip()
    n.on_fullscreen()
    assert not n._live_card().isVisible()
    assert n._timer.isActive() and n._timer.interval() == constants.app.STARTUP_NOTICE_RETRY_MS
    w.blocked = True                                          # still fullscreen at the retry
    n.try_show()
    assert not n._live_card().isVisible()
    w.blocked = False
    n.try_show()
    assert _shown(n)
    assert sum(1 for s in w.saved if s.get("first_run_tip_shown")) == 1
    _close(n)


def test_widget_fullscreen_hook_reaches_the_notices_even_with_keep_visible():
    from netspeedtray.views.widget import main as main_mod
    notices = MagicMock()
    stub = types.SimpleNamespace(config={"keep_visible_fullscreen": True}, dismiss_hover_card=MagicMock(),
                                 _startup_notices=notices, position_manager=None)
    main_mod.NetworkSpeedWidget._on_immediate_hide_requested(stub)
    notices.on_fullscreen.assert_called_once_with()


def test_another_card_wins_and_the_tip_comes_back_after_it(qtbot, screen):
    """M3: an update offer / notice / data-cap alert never stacks on the tip."""
    from netspeedtray.views.flyout import Flyout
    w, n = _visible_tip()
    other = Flyout("Update available", "x", auto_dismiss_ms=0)
    other.show()
    w._update_flyout = other
    n.yield_to(other)
    assert not n._live_card().isVisible() and not n._timer.isActive()
    other._dismiss()
    assert n._timer.isActive() and n._timer.interval() == constants.app.STARTUP_NOTICE_DELAY_MS
    n.try_show()
    assert _shown(n)
    _close(n)


def test_another_card_before_the_tip_shows_delays_it(qtbot, screen):
    from netspeedtray.views.flyout import Flyout
    w = _widget(dict(DEFAULTS))
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    other = Flyout("Notice", "x", auto_dismiss_ms=0)
    other.show()
    w._notice_flyout = other
    n.yield_to(other)
    assert not n._timer.isActive()
    other._dismiss()
    assert n._timer.isActive()
    n.try_show()
    assert _shown(n)
    _close(n)


@pytest.mark.parametrize("method, attr", [("_show_notice_flyout", "_notice_flyout"),
                                          ("_show_usage_alert", "_usage_alert_flyout")])
def test_widget_cards_make_the_tip_yield(qtbot, method, attr):
    from netspeedtray.views.widget import main as main_mod
    W = main_mod.NetworkSpeedWidget
    notices = MagicMock()
    stub = types.SimpleNamespace(_startup_notices=notices, logger=MagicMock(),
                                 frameGeometry=lambda: QRect(1500, 1000, 120, 40),
                                 _NOTICE_FLYOUT_DISMISS_MS=W._NOTICE_FLYOUT_DISMISS_MS)
    setattr(stub, attr, None)
    getattr(W, method)(stub, "T", "B")
    card = getattr(stub, attr)
    notices.yield_to.assert_called_once_with(card)
    card._dismiss()


# --- review round 1: code minors ----------------------------------------------------------------

def test_card_follows_the_widget_when_it_moves(qtbot, screen):
    w, n = _visible_tip()
    before = n._live_card().pos()
    w.frameGeometry = lambda: QRect(300, 1000, 120, 40)
    n.on_widget_moved()
    assert n._live_card().pos() != before
    _close(n)


def test_restore_foreground_only_while_the_card_holds_it():
    from netspeedtray.views import flyout as fmod
    calls = []
    u = types.SimpleNamespace(GetForegroundWindow=lambda: 100, IsWindow=lambda h: True,
                              IsWindowVisible=lambda h: True,
                              SetForegroundWindow=lambda h: calls.append(h) or 1)
    assert fmod.restore_foreground(100, 200, user32=u) is True and calls == [200]
    u.GetForegroundWindow = lambda: 300                        # the user moved on: leave it alone
    assert fmod.restore_foreground(100, 200, user32=u) is False and calls == [200]
    u.GetForegroundWindow = lambda: 100
    u.IsWindow = lambda h: False                               # the previous window is gone
    assert fmod.restore_foreground(100, 200, user32=u) is False
    assert fmod.restore_foreground(100, 0, user32=u) is False


def test_closing_the_card_hands_the_foreground_back(qtbot, monkeypatch):
    from netspeedtray.views import flyout as fmod
    seen = []
    monkeypatch.setattr(fmod, "restore_foreground",
                        lambda own, prev, user32=None: seen.append((own, prev)))
    f = fmod.Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0, focusable=True)
    f.show()
    f._prev_foreground = 4242                                  # as WM_ACTIVATE would record it
    f._secondary_btn.click()
    assert seen and seen[0][1] == 4242


def test_alt_f4_or_any_close_emits_dismissed_once(qtbot):
    from netspeedtray.views.flyout import Flyout
    f = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0, focusable=True)
    f.show()
    fired = []
    f.dismissed.connect(lambda: fired.append(1))
    f.close()                                                  # Alt+F4 / a system close
    assert fired == [1]
    g = Flyout("T", "M", action_text="Go", auto_dismiss_ms=0)
    g.show()
    fired2 = []
    g.dismissed.connect(lambda: fired2.append(1))
    g._dismiss()
    assert fired2 == [1]


def test_four_part_versions():
    assert logic.is_prerelease("2.2.2.0") is False
    assert logic.release_core("2.2.2.0") == "2.2.2"
    assert logic.whats_new_key("2.2.2.0") == "WHATS_NEW_2_2_2"
    assert logic.release_core("2.2.2.1001") is None            # a build quad, not a release
    assert logic.is_prerelease("2.2.2.1001") is False
    cfg = dict(_upgrader_221(), last_seen_version="2.2.2")
    assert _plan(cfg, "2.2.2.0").notice is None                # the same release
    assert logic.later_version("2.2.2.0", "2.2.3") == "2.2.3"


def test_every_locale_keeps_whats_new_short():
    import json
    from pathlib import Path
    loc_dir = Path(__file__).resolve().parents[2] / "constants" / "locales"
    en = json.loads((loc_dir / "en_US.json").read_text(encoding="utf-8"))
    prefix = logic.WHATS_NEW_KEY_PREFIX
    keys = [k for k in en if k.startswith(prefix) and k[len(prefix):][:1].isdigit()]
    assert keys
    for f in loc_dir.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for k in keys:
            cap = min(240, int(1.5 * len(en[k])))
            assert len(data[k]) <= cap, f"{f.stem} {k}: {len(data[k])} > {cap}"


def test_real_save_path_is_bookkeeping_only(qtbot, screen):
    """The notices save through ConfigController.update_config(..., apply_and_repaint=False)."""
    w = _widget(dict(DEFAULTS))
    w.config_controller = MagicMock()
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()
    calls = w.config_controller.update_config.call_args_list
    assert calls and all(c.kwargs == {"apply_and_repaint": False} for c in calls)
    assert {"first_run_tip_shown": True, "first_run_ever": False} in [c.args[0] for c in calls]
    assert w.saved == []                                       # never the repainting path
    _close(n)


def _bare_widget():
    """A NetworkSpeedWidget whose __init__ never ran - just a QWidget with its event handlers."""
    from PyQt6.QtWidgets import QWidget
    from netspeedtray.views.widget import main as main_mod
    W = main_mod.NetworkSpeedWidget
    obj = W.__new__(W)
    QWidget.__init__(obj)
    obj.logger = MagicMock()
    obj._startup_notices = MagicMock()
    return obj


def test_widget_show_hide_and_move_events_reach_the_notices(qtbot):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QHideEvent, QMoveEvent, QShowEvent
    w = _bare_widget()
    w._arm_shutdown_channel = lambda create=True: None
    w._reanchor_hover_card = lambda: None
    w.showEvent(QShowEvent())
    w._startup_notices.on_widget_shown.assert_called_once_with()
    w.hideEvent(QHideEvent())
    w._startup_notices.on_widget_hidden.assert_called_once_with()
    w.moveEvent(QMoveEvent(QPoint(1, 1), QPoint(0, 0)))
    w._startup_notices.on_widget_moved.assert_called_once_with()


# --- review round 1: UI/UX ----------------------------------------------------------------------

def test_focus_ring_is_for_the_keyboard_only(qtbot):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QFocusEvent
    from PyQt6.QtWidgets import QApplication
    from netspeedtray.views.flyout import Flyout
    f = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0, focusable=True)
    btn = f._action_btn
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason))
    assert not btn.property("kbfocus")
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
    assert btn.property("kbfocus") is True
    QApplication.sendEvent(btn, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
    assert not btn.property("kbfocus")
    # The resting border is as wide as the ring: focus changes a colour, never the text's room.
    assert "border: 2px solid transparent" in btn.styleSheet()
    assert 'kbfocus="true"' in btn.styleSheet()
    assert btn.minimumHeight() >= 32
    f.close()


def test_a_taught_gesture_answers_the_tip(qtbot, screen):
    w, n = _visible_tip()
    assert n.tip_visible()
    n.on_gesture()
    assert n._live_card() is None
    assert ui.StartupNotices(w, version="2.2.2").start() is None   # answered: never again


def test_a_gesture_leaves_whats_new_alone(qtbot, screen):
    w, n = _visible_tip(cfg=_upgrader_221())
    assert not n.tip_visible()
    n.on_gesture()
    assert _shown(n)
    _close(n)


def test_widget_gesture_hooks():
    from netspeedtray.views.widget import main as main_mod
    from netspeedtray.core.tray_manager import TrayIconManager as TrayManager
    W = main_mod.NetworkSpeedWidget
    notices = MagicMock()
    stub = types.SimpleNamespace(_startup_notices=notices, input_handler=None, logger=MagicMock())
    W.mouseDoubleClickEvent(stub, None)
    assert notices.on_gesture.call_count == 1
    tray = MagicMock()
    tray.widget = stub
    TrayManager.show_context_menu(tray)
    assert notices.on_gesture.call_count == 2


@pytest.mark.parametrize("reader", [False, True])
def test_a_running_screen_reader_gets_an_activated_card(qtbot, screen, monkeypatch, reader):
    monkeypatch.setattr(ui, "_screen_reader_running", lambda: reader)
    w, n = _visible_tip()
    card = n._live_card()
    assert card.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is (not reader)
    _close(n)


def test_screen_reader_flag_is_read_from_spi(monkeypatch):
    import ctypes
    from netspeedtray.views import flyout as fmod
    seen = []

    def spi(action, param, pv, flags):
        seen.append(action)
        pv._obj.value = 1
        return 1
    monkeypatch.setattr(ctypes.windll.user32, "SystemParametersInfoW", spi)
    assert fmod.screen_reader_running() is True
    assert seen == [0x0046]                                    # SPI_GETSCREENREADER


def _spi(value):
    def spi(action, param, pv, flags):
        pv._obj.value = value
        return 1
    return spi


@pytest.mark.parametrize("name", ["Narrator.exe", "nvda.exe", "NVDA.EXE", "jfw.exe"])
def test_a_running_screen_reader_process_counts_when_spi_says_no(monkeypatch, name):
    """The VM (Windows 11 26300): Narrator running and SPI_GETSCREENREADER still 0 - the tip never
    came up activated for it (live test F5). A screen reader's own process counts too."""
    import ctypes
    import types
    import psutil
    from netspeedtray.views import flyout as fmod
    monkeypatch.setattr(ctypes.windll.user32, "SystemParametersInfoW", _spi(0))
    procs = [types.SimpleNamespace(info={"name": n}) for n in ("explorer.exe", None, name)]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert fmod.screen_reader_running() is True


def test_no_screen_reader_when_neither_spi_nor_a_process_says_so(monkeypatch):
    import ctypes
    import types
    import psutil
    from netspeedtray.views import flyout as fmod
    monkeypatch.setattr(ctypes.windll.user32, "SystemParametersInfoW", _spi(0))
    procs = [types.SimpleNamespace(info={"name": n}) for n in ("explorer.exe", "narratorx.exe", "Code.exe")]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert fmod.screen_reader_running() is False


def test_the_process_scan_is_skipped_when_spi_already_says_yes(monkeypatch):
    import ctypes
    import psutil
    from netspeedtray.views import flyout as fmod
    monkeypatch.setattr(ctypes.windll.user32, "SystemParametersInfoW", _spi(1))
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: (_ for _ in ()).throw(AssertionError("scanned")))
    assert fmod.screen_reader_running() is True


def test_a_failing_process_scan_reads_as_no_screen_reader(monkeypatch):
    import ctypes
    import psutil
    from netspeedtray.views import flyout as fmod
    monkeypatch.setattr(ctypes.windll.user32, "SystemParametersInfoW", _spi(0))
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: (_ for _ in ()).throw(OSError("denied")))
    assert fmod.screen_reader_running() is False


_AVAIL = {"BOTTOM": QRect(0, 0, 1920, 1032), "TOP": QRect(0, 48, 1920, 1032),
          "LEFT": QRect(72, 0, 1848, 1080), "RIGHT": QRect(0, 0, 1848, 1080)}


@pytest.mark.parametrize("edge_name, geo, side", [
    ("BOTTOM", QRect(1600, 1032, 90, 48), "bottom"),
    ("TOP", QRect(1600, 0, 90, 48), "top"),
    ("LEFT", QRect(0, 900, 72, 44), "left"),
    ("RIGHT", QRect(1848, 900, 72, 44), "right"),
])
def test_tip_is_placed_like_the_hover_card_with_a_tail_at_the_widget(qtbot, edge_name, geo, side):
    from netspeedtray.constants import TaskbarEdge
    from netspeedtray.views.flyout import Flyout, _MARGIN
    avail = _AVAIL[edge_name]
    f = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0, focusable=True)
    f.show_for(geo, avail, edge=getattr(TaskbarEdge, edge_name), tail=True)
    r = f.frameGeometry()
    assert not r.intersects(geo)                               # never over the widget
    assert avail.contains(r.adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN))   # body in the work area
    got_side, at = f.tail()
    assert got_side == side
    target = geo.center().x() - r.x() if side in ("top", "bottom") else geo.center().y() - r.y()
    assert abs(at - target) <= 1                               # pointing at the widget's centre
    f.close()


def test_tail_is_clamped_not_the_card_at_a_screen_corner(qtbot):
    from netspeedtray.constants import TaskbarEdge
    from netspeedtray.constants.styles import styles as tokens
    from netspeedtray.views.flyout import Flyout, _MARGIN
    geo = QRect(1890, 1032, 30, 48)                            # the widget in the very corner
    f = Flyout("T", "M", action_text="Go", secondary_text="Got it", auto_dismiss_ms=0, focusable=True)
    f.show_for(geo, QRect(0, 0, 1920, 1032), edge=TaskbarEdge.BOTTOM, tail=True)
    side, at = f.tail()
    r = f.frameGeometry()
    assert side == "bottom"
    assert r.right() <= 1919                                   # the card stays on screen...
    assert at == r.width() - _MARGIN - tokens.RADIUS_CARD - 6  # ...the tail stops at the corner
    f.close()


def test_whats_new_has_no_tail_and_calm_buttons(qtbot, screen):
    w, n = _visible_tip(cfg=_upgrader_221())
    card = n._live_card()
    assert card.tail() is None
    assert card._secondary_btn.text() == w.i18n.WELCOME_2_0_GOT_IT_BUTTON
    assert card._close_btn is None
    assert card._colors["accent"] not in card._action_btn.styleSheet()   # neutral, not a call to action
    _close(n)


def test_tip_has_a_tail(qtbot, screen):
    w, n = _visible_tip()
    assert n._live_card().tail() is not None
    _close(n)


# --- combined review round (C3, C7, C9) ------------------------------------------------------------

def _waiting_tip(**kw):
    w = _widget(dict(DEFAULTS), **kw)
    n = ui.StartupNotices(w, version="2.2.2")
    assert n.start() == TIP
    return w, n


def test_a_gesture_while_the_tip_waits_uses_it_up(qtbot, screen):
    """C3: the user found the hover card (or double-clicked, or right-clicked) before the tip's moment:
    it has nothing left to teach - saved as shown, never shown."""
    w, n = _waiting_tip()
    n.on_gesture()
    assert w.config["first_run_tip_shown"] is True and w.config["first_run_ever"] is False
    assert not n._timer.isActive()
    n.try_show()
    assert n._live_card() is None
    assert ui.StartupNotices(w, version="2.2.2").start() is None


def test_the_tip_never_shows_while_the_hover_card_is_up(qtbot, screen):
    from PyQt6.QtWidgets import QWidget
    w, n = _waiting_tip()
    w._hover_card = QWidget()
    qtbot.addWidget(w._hover_card)
    w._hover_card.show()
    assert n.blocked_reason() == "the hover card is up"
    n._timer.stop()
    n.try_show()
    assert n._live_card() is None and w.config.get("first_run_tip_shown", False) is False   # still owed
    w._hover_card.hide()
    n.try_show()
    assert _shown(n)
    _close(n)


def test_one_one_time_moment_per_session(qtbot, screen):
    """C7: another one-time moment (the support card, the temperature setup) showed this session - the
    tip waits for the next launch, still owed."""
    w, n = _waiting_tip()
    ui.take_moment(w, ui.MOMENT_SUPPORT)
    n._timer.stop()
    n.try_show()
    assert n._live_card() is None and not n.owes_moment()
    assert w.config.get("first_run_tip_shown", False) is False
    w2 = _widget(dict(w.config))
    assert ui.StartupNotices(w2, version="2.2.2").start() == TIP      # the next launch


def test_a_shown_or_owed_notice_takes_the_session_moment(qtbot, screen):
    w, n = _waiting_tip()
    w._startup_notices = n
    assert n.owes_moment() and ui.one_time_moment_blocked(w, ui.MOMENT_SUPPORT) is not None
    n._timer.stop()
    n.try_show()
    assert ui.moment_taken(w) == TIP
    _close(n)
    assert not n.owes_moment()
    assert ui.one_time_moment_blocked(w, ui.MOMENT_SUPPORT) == "another one-time moment was shown this session"


def test_a_shown_tip_steps_aside_for_a_modal_dialog_and_comes_back(qtbot, screen):
    """C9: an app-modal dialog of ours (Check connection, the update dialog) - the tip hides and is
    re-homed by the retry once the dialog is gone."""
    from PyQt6.QtWidgets import QApplication, QDialog
    w, n = _visible_tip()
    dlg = QDialog()
    qtbot.addWidget(dlg)
    dlg.setModal(True)
    dlg.show()
    qtbot.waitUntil(lambda: QApplication.activeModalWidget() is dlg, timeout=1000)
    n.check_modal()
    assert not _shown(n) and n._live_card() is not None
    assert n._timer.isActive()
    n._timer.stop()
    n.try_show()
    assert not _shown(n)                                       # still waits while it is up
    dlg.close()
    qtbot.waitUntil(lambda: QApplication.activeModalWidget() is None, timeout=1000)
    n.try_show()
    assert _shown(n)
    _close(n)


def test_a_modal_dialog_opening_reaches_the_check(qtbot, screen):
    from PyQt6.QtWidgets import QApplication, QDialog
    w, n = _visible_tip()
    dlg = QDialog()
    qtbot.addWidget(dlg)
    dlg.setModal(True)
    dlg.show()
    dlg.activateWindow()
    qtbot.waitUntil(lambda: not _shown(n), timeout=2000)
    dlg.close()
    _close(n)


def test_a_hover_card_already_open_when_the_tip_is_owed_uses_it_up(qtbot, screen):
    """VM round 2: the pointer already rested where the widget appeared, so the hover card opened
    before StartupNotices existed - that hover was never counted, and the tip showed ~32 s later,
    after the user had already seen what it teaches. An open hover card at start() is the gesture."""
    from PyQt6.QtWidgets import QWidget
    w = _widget(dict(DEFAULTS))
    w._hover_card = QWidget()
    qtbot.addWidget(w._hover_card)
    w._hover_card.show()
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    assert n._pending is None and not n._timer.isActive()
    assert w.config["first_run_tip_shown"] is True and w.config["first_run_ever"] is False


def test_a_hidden_hover_card_at_start_changes_nothing(qtbot, screen):
    from PyQt6.QtWidgets import QWidget
    w = _widget(dict(DEFAULTS))
    w._hover_card = QWidget()                              # built earlier, not on screen now
    qtbot.addWidget(w._hover_card)
    n = ui.StartupNotices(w, version="2.2.2")
    assert n.start() == TIP and n._pending == TIP


def test_an_open_hover_card_never_uses_up_whats_new(qtbot, screen):
    from PyQt6.QtWidgets import QWidget
    w = _widget(dict(DEFAULTS, first_run_ever=False, first_run_v2_seen=True, last_seen_version="2.2.1"))
    w._hover_card = QWidget()
    qtbot.addWidget(w._hover_card)
    w._hover_card.show()
    n = ui.StartupNotices(w, version="2.2.2")
    assert n.start() == ui.logic.NOTICE_WHATS_NEW and n._pending == ui.logic.NOTICE_WHATS_NEW
