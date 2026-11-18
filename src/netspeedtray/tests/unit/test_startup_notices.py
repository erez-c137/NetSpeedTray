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


@pytest.mark.parametrize("why", ["hidden", "fullscreen", "yielding", "sign_in", "other_card"])
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
    else:
        from netspeedtray.views.flyout import Flyout
        other = Flyout("Update", "x", auto_dismiss_ms=0)
        qtbot.addWidget(other)
        other.show()
        w._update_flyout = other
    n = ui.StartupNotices(w, version="2.2.2")
    n.start()
    n.try_show()
    assert n._live_card() is None
    assert n._timer.isActive() and n._timer.interval() == constants.app.STARTUP_NOTICE_RETRY_MS
    assert w.config.get("first_run_tip_shown") is False      # still owed
    # The moment passes: it shows on the retry.
    w.visible, w.blocked, w.yielding, screen.since_sign_in = True, False, False, 3600.0
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
    one_x.update_config.assert_called_once_with({"first_run_v2_seen": True,
                                                 "last_seen_version": constants.app.VERSION})


# --- the card -------------------------------------------------------------------------------------

def test_flyout_secondary_button_and_keyboard(qtbot):
    from netspeedtray.views.flyout import Flyout
    f = Flyout("T", "M", action_text="Go", secondary_text="Got it", close_text="Close",
               auto_dismiss_ms=0, focusable=True)
    qtbot.addWidget(f)
    assert not (f.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
    assert f._close_btn.accessibleName() == "Close"
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
    card = f.findChild(type(f._close_btn.parentWidget()), "flyoutCard")
    assert "#000000" in card.styleSheet() and "#FFFF00" in card.styleSheet()
    assert "#1AEBFF" in f._action_btn.styleSheet()


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
