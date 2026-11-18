"""
2.2.2 D6 - temperature setup that works with current LibreHardwareMonitor.

LHM v0.9.5+ is read over its web server (#187), which is off until the user turns on Options -> Remote
Web Server. The onboarding used to say temperatures "appear automatically" once LHM runs, and only a
~600-character hint mentioned the web server - so anyone following it with today's LHM got blank temps.

Covers: the onboarding as two numbered steps (the web server is step 2, the details behind "More
details"); "LHM is running but its web server is off" detection (process list + HTTP, at the probe's
own cadence, suppressed while a WMI source reads); the Settings > Hardware notice that explains it;
no network beyond 127.0.0.1; the retired / added locale keys.
"""
import json
import socket
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QCheckBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import lhm_http
from netspeedtray.core.lhm_http import LhmHttpClient, lhm_process_running
from netspeedtray.utils import taskbar_appearance as ta

L = constants.lhm
EN = I18nStrings("en_US")
LOCALES = Path(constants.__file__).parent / "locales"


@pytest.fixture(autouse=True)
def _no_high_contrast(monkeypatch):
    monkeypatch.setattr(ta, "read_high_contrast_link_colors", lambda: None)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()   # nothing listens on it now
    return port


# --- the onboarding: two numbered steps ---------------------------------------------------------------

def _dialog(qt_owned, **kw):
    from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog
    return qt_owned(TempOnboardingDialog(EN, **kw))


def test_onboarding_is_two_numbered_steps_install_then_web_server(q_app, qt_owned):
    dlg = _dialog(qt_owned, lhm_port=18085)
    assert len(dlg.steps) == 2
    one, two = dlg.steps
    assert one.number == 1 and two.number == 2
    assert "LibreHardwareMonitor" in one.text() and "administrator" in one.text()
    assert "Options → Remote Web Server" in two.text()
    assert "18085" in two.text() and "{port}" not in two.text()
    # Narrator hears the step number with the words.
    assert one.accessibleName().startswith("1") and two.accessibleName().startswith("2")


def test_onboarding_no_longer_says_temperatures_appear_automatically(q_app, qt_owned):
    dlg = _dialog(qt_owned)
    shown = " ".join([dlg.intro.text()] + [s.text() for s in dlg.steps]).lower()
    assert "automatically" not in shown


def test_step_one_links_to_the_official_releases_page_and_keeps_the_dialog_open(q_app, qt_owned):
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL, TempOnboardingDialog
    opened = []
    dlg = _dialog(qt_owned, open_url=opened.append)
    dlg.show()
    assert dlg.get_lhm_link.action_text() == EN.TEMP_ONBOARDING_GET_LHM_BUTTON
    QTest.mouseClick(dlg.get_lhm_link.button(), Qt.MouseButton.LeftButton)
    assert opened == [LHM_RELEASES_URL]
    assert LHM_RELEASES_URL == "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"
    assert dlg.action == TempOnboardingDialog.ACTION_GET_LHM
    assert dlg.isVisible()            # step 2 still to do: the dialog stays
    dlg.close()


def test_details_are_collapsed_until_asked_for(q_app, qt_owned):
    dlg = _dialog(qt_owned, lhm_port=18085)
    dlg.show()
    assert not dlg.details.isExpanded() and not dlg.details_label.isVisible()
    QTest.mouseClick(dlg.details.header(), Qt.MouseButton.LeftButton)
    assert dlg.details.isExpanded() and dlg.details_label.isVisible()
    text = dlg.details_label.text()
    assert "0.9.4" in text and "0.9.6" in text and "firewall" in text and "18085" in text
    QTest.mouseClick(dlg.details.header(), Qt.MouseButton.LeftButton)
    assert not dlg.details_label.isVisible()
    dlg.close()


def test_details_header_is_keyboard_reachable_and_says_its_state(q_app, qt_owned):
    dlg = _dialog(qt_owned)
    h = dlg.details.header()
    assert h.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert h.accessibleName().startswith(EN.TEMP_ONBOARDING_MORE_DETAILS)
    assert "collapsed" in h.accessibleName()
    QTest.keyClick(h, Qt.Key.Key_Space)
    assert dlg.details.isExpanded() and "expanded" in h.accessibleName()


def test_opened_at_step_two_marks_step_one_done_and_step_two_current(q_app, qt_owned):
    dlg = _dialog(qt_owned, focus_step=2)
    one, two = dlg.steps
    assert one.done and not one.current
    assert two.current and not two.done
    plain = _dialog(qt_owned)
    assert plain.steps[0].current and not plain.steps[0].done and not plain.steps[1].current


def test_from_settings_there_is_no_reminder_checkbox(q_app, qt_owned):
    dlg = _dialog(qt_owned, show_remind=False)
    dlg.show()
    assert not dlg.dont_remind.isVisible()
    assert dlg.dismissed_forever() is False
    assert dlg.close_button.text() == EN.CLOSE_BUTTON
    dlg.close()


def test_close_dismisses(q_app, qt_owned):
    from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog
    dlg = _dialog(qt_owned)
    dlg.show()
    dlg.close_button.click()
    assert dlg.action == TempOnboardingDialog.ACTION_DISMISS and not dlg.isVisible()


@pytest.mark.parametrize("code", ["he_IL", "de_DE", "bn_BD"])
def test_onboarding_builds_in_other_languages(q_app, qt_owned, code):
    from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog
    dlg = qt_owned(TempOnboardingDialog(I18nStrings(code), lhm_port=8085, focus_step=2))
    assert "8085" in dlg.steps[1].text()
    assert dlg.steps[1].accessibleName().startswith("2")


# --- detection: running, but the web server does not answer -----------------------------------------

class _Proc:
    def __init__(self, name):
        self.info = {"name": name}


@pytest.mark.parametrize("names,expected", [
    (["explorer.exe", "LibreHardwareMonitor.exe"], True),
    (["librehardwaremonitor.EXE"], True),
    (["explorer.exe", None, "OpenHardwareMonitor.exe"], False),
    ([], False),
])
def test_lhm_process_running_matches_the_image_name(monkeypatch, names, expected):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([_Proc(n) for n in names]))
    assert lhm_process_running() is expected


def test_lhm_process_running_never_raises(monkeypatch):
    import psutil

    def boom(attrs=None):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, "process_iter", boom)
    assert lhm_process_running() is False


def test_nothing_listening_and_lhm_running_is_server_off():
    probes = []
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: probes.append(1) or True)
    assert c.get_snapshot() is None
    assert c.status == L.STATUS_NOT_FOUND and c.process_running is True and c.server_off
    assert probes == [1]


def test_nothing_listening_and_lhm_not_running_is_not_server_off():
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: False)
    c.get_snapshot()
    assert c.status == L.STATUS_NOT_FOUND and c.process_running is False and not c.server_off


def test_the_process_check_runs_at_the_probe_cadence_not_every_poll():
    """One check per failed HTTP attempt - so once per FAILURE_BACKOFF_SEC window, never per poll."""
    probes = []
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: probes.append(1) or True)
    for _ in range(20):
        c.get_snapshot()
    assert probes == [1]
    c.reset_backoff()
    c.get_snapshot()
    assert probes == [1, 1]


def test_an_answering_endpoint_is_never_server_off(monkeypatch):
    probes = []
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: probes.append(1) or True)
    monkeypatch.setattr(c, "_fetch_json", lambda: (_ for _ in ()).throw(lhm_http.LhmHttpStatusError(401)))
    c.get_snapshot()
    assert c.status == L.STATUS_AUTH_REQUIRED and not c.server_off and probes == []


def test_a_good_read_or_a_new_port_forgets_the_process_result(monkeypatch):
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: True)
    c.get_snapshot()
    assert c.server_off
    c.configure(c.port + 1)
    assert c.process_running is None and not c.server_off
    c.get_snapshot()
    assert c.server_off
    c.reset_backoff()
    monkeypatch.setattr(c, "_fetch_json", lambda: {"Text": "Sensor", "Children": []})
    c.get_snapshot()
    assert c.status == L.STATUS_OK and c.process_running is None and not c.server_off


def test_a_failing_probe_is_unknown_not_an_error():
    def boom():
        raise RuntimeError("x")
    c = LhmHttpClient(port=_free_port(), process_probe=boom)
    assert c.get_snapshot() is None
    assert c.process_running is None and not c.server_off


def _thread_with(client):
    from netspeedtray.core.monitor_thread import StatsMonitorThread
    t = StatsMonitorThread(interval=1.0, config={})
    t._lhm_http = client
    return t


def test_the_monitor_thread_reports_server_off():
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: True)
    c.get_snapshot()
    t = _thread_with(c)
    assert t.lhm_status == L.STATUS_SERVER_OFF


def test_a_wmi_source_that_reads_means_no_server_off():
    """LHM v0.9.4 (or OpenHardwareMonitor) is read over WMI and needs no web server."""
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: True)
    c.get_snapshot()
    t = _thread_with(c)
    t._wmi_ohm = object()
    assert t.lhm_status == L.STATUS_NOT_FOUND


def test_the_conftest_stub_without_process_info_is_plain_not_found():
    from netspeedtray.core.monitor_thread import StatsMonitorThread
    t = StatsMonitorThread(interval=1.0, config={})
    assert t.lhm_status == L.STATUS_NOT_FOUND


def test_no_network_beyond_loopback(monkeypatch):
    """The detection adds no traffic: the HTTP attempt goes to 127.0.0.1 only, and the process check
    opens no socket at all."""
    targets = []
    real_connect = socket.socket.connect

    def spy(self, address):
        targets.append(address)
        return real_connect(self, address)
    monkeypatch.setattr(socket.socket, "connect", spy)
    created = []
    real_cc = socket.create_connection

    def spy_cc(address, *a, **k):
        created.append(address)
        return real_cc(address, *a, **k)
    monkeypatch.setattr(socket, "create_connection", spy_cc)

    seen = []
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: seen.append(len(targets) + len(created)) or True)
    c.get_snapshot()
    assert c.server_off
    hosts = {a[0] for a in targets + created}
    assert hosts <= {"127.0.0.1"}, hosts
    before = len(targets) + len(created)
    assert lhm_process_running() in (True, False)   # the real process list
    assert len(targets) + len(created) == before


# --- Settings > Hardware: the notice ------------------------------------------------------------------

@pytest.fixture
def page(qtbot):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    status = {"value": None}
    calls = []
    p = HardwarePage(EN, lambda: None, lhm_status_provider=lambda: status["value"],
                     temp_setup_callback=calls.append)
    qtbot.addWidget(p)
    p.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG, show_hardware_temps=True))
    p.status, p.setup_calls = status, calls
    p.show()
    return p


def test_the_notice_is_hidden_by_default(page):
    page.refresh_lhm_status()
    assert not page.lhm_server_off_notice.isVisible()


def test_the_notice_shows_when_lhm_runs_without_its_web_server(page):
    page.status["value"] = (L.STATUS_SERVER_OFF, 8085)
    page.refresh_lhm_status()
    n = page.lhm_server_off_notice
    assert n.isVisible()
    assert n.text() == EN.LHM_SERVER_OFF_NOTICE
    assert n.action_text() == EN.LHM_SERVER_OFF_ACTION
    # The port card still says what the client found.
    assert page._lhm_port_card._desc_label.text() == EN.LHM_STATUS_NOT_FOUND.format(port=8085)


@pytest.mark.parametrize("status", [L.STATUS_OK, L.STATUS_NOT_FOUND, L.STATUS_AUTH_REQUIRED,
                                    L.STATUS_AUTH_FAILED, L.STATUS_OTHER, L.STATUS_UNKNOWN])
def test_the_notice_hides_for_every_other_status(page, status):
    page.status["value"] = (L.STATUS_SERVER_OFF, 8085)
    page.refresh_lhm_status()
    page.status["value"] = (status, 8085)
    page.refresh_lhm_status()
    assert not page.lhm_server_off_notice.isVisible()


def test_the_notice_hides_for_another_port_and_with_temps_and_power_off(page):
    page.status["value"] = (L.STATUS_SERVER_OFF, 9999)
    page.refresh_lhm_status()
    assert not page.lhm_server_off_notice.isVisible()
    page.status["value"] = (L.STATUS_SERVER_OFF, 8085)
    page.show_temps.setChecked(False)
    page.show_power.setChecked(False)
    page.refresh_lhm_status()
    assert not page.lhm_server_off_notice.isVisible()
    page.show_power.setChecked(True)   # power readings come from LHM too
    page.refresh_lhm_status()
    assert page.lhm_server_off_notice.isVisible()


def test_how_to_turn_it_on_opens_the_onboarding_at_step_two(page):
    page.status["value"] = (L.STATUS_SERVER_OFF, 8085)
    page.refresh_lhm_status()
    page.lhm_server_off_notice.click()
    assert page.setup_calls == [2]


def test_the_temps_note_has_a_setup_link_at_step_one(page):
    assert page.temps_setup_link.action_text() == EN.HARDWARE_TEMPS_SETUP_ACTION
    page.temps_setup_link.click()
    assert page.setup_calls == [1]


def test_the_temps_note_names_the_web_server_and_not_automatic_detection():
    note = EN.HARDWARE_TEMPS_LIMITATION_NOTE
    assert "Remote Web Server" in note and "automatically" not in note.lower()


def test_settings_opens_the_onboarding_with_the_page_port_and_without_the_reminder(q_app, monkeypatch):
    from netspeedtray.views import temp_onboarding_dialog as tod
    from netspeedtray.views.settings.dialog import SettingsDialog
    made = []

    class _Fake:
        ACTION_DISMISS = "dismiss"

        def __init__(self, i18n, parent=None, **kw):
            made.append(kw)

        def exec(self):
            return 0
    monkeypatch.setattr(tod, "TempOnboardingDialog", _Fake)
    page = type("P", (), {"lhm_port": type("S", (), {"value": lambda self: 18085})()})()
    host = type("D", (), {"i18n": EN, "hardware_page": page, "logger": __import__("logging").getLogger("t")})()
    SettingsDialog._open_temp_setup(host, 2)
    assert made == [{"lhm_port": 18085, "focus_step": 2, "show_remind": False}]


# --- the widget's one-time nudge opens at step 2 when LHM already runs ---------------------------------

def test_the_nudge_opens_at_step_two_when_the_web_server_is_off(q_app, monkeypatch):
    from netspeedtray.views import temp_onboarding_dialog as tod
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    made = []

    class _Fake:
        ACTION_DISMISS, ACTION_GET_LHM = "dismiss", "get_lhm"

        def __init__(self, i18n, parent=None, **kw):
            made.append(kw)
            self.action = "dismiss"

        def exec(self):
            return 0

        def dismissed_forever(self):
            return False
    monkeypatch.setattr(tod, "TempOnboardingDialog", _Fake)
    for status, step in ((L.STATUS_SERVER_OFF, 2), (L.STATUS_NOT_FOUND, 1)):
        mt = type("MT", (), {"lhm_port": 8085, "lhm_status": status})()
        host = type("W", (), {"config": {}, "monitor_thread": mt, "i18n": EN,
                              "logger": __import__("logging").getLogger("t"),
                              "update_config": lambda self, u: None})()
        NetworkSpeedWidget._on_lhm_not_detected(host)
        assert made[-1]["focus_step"] == step and made[-1]["lhm_port"] == 8085


# --- strings --------------------------------------------------------------------------------------------

def _locale(code):
    return json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))


RETIRED = ("LHM_WEB_SERVER_HINT", "TEMP_ONBOARDING_BODY", "TEMP_ONBOARDING_NOT_NOW_BUTTON")
ADDED = ("TEMP_ONBOARDING_INTRO", "TEMP_ONBOARDING_STEP_1", "TEMP_ONBOARDING_STEP_2",
         "TEMP_ONBOARDING_MORE_DETAILS", "TEMP_ONBOARDING_DETAILS", "HARDWARE_TEMPS_SETUP_ACTION",
         "LHM_SERVER_OFF_NOTICE", "LHM_SERVER_OFF_ACTION")


@pytest.mark.parametrize("code", sorted(constants.I18nStrings.LANGUAGE_MAP))
def test_every_locale_has_the_new_keys_and_none_of_the_retired_ones(code):
    data = _locale(code)
    assert not [k for k in RETIRED if k in data]
    assert all(data.get(k, "").strip() for k in ADDED)


@pytest.mark.parametrize("code", sorted(constants.I18nStrings.LANGUAGE_MAP))
def test_every_locale_keeps_lhm_menu_names_and_the_port(code):
    data = _locale(code)
    step2 = data["TEMP_ONBOARDING_STEP_2"]
    assert "Options → Remote Web Server" in step2     # LHM's own menu stays in English
    assert step2.count("{port}") == 1
    details = data["TEMP_ONBOARDING_DETAILS"]
    assert details.count("{port}") == 1 and "0.9.4" in details and "0.9.6" in details
    assert "Remote Web Server" in data["HARDWARE_TEMPS_LIMITATION_NOTE"]
    step2.format(port=8085), details.format(port=8085)
    for k in ADDED:
        assert "<" not in data[k], k   # plain text: no markup for translators to keep in step


def test_english_strings_are_short():
    en = _locale("en_US")
    for k in ("TEMP_ONBOARDING_STEP_1", "TEMP_ONBOARDING_STEP_2", "LHM_SERVER_OFF_NOTICE",
              "LHM_SERVER_OFF_ACTION", "HARDWARE_TEMPS_SETUP_ACTION", "TEMP_ONBOARDING_MORE_DETAILS"):
        assert len(en[k]) <= 120, k
    assert len(en["TEMP_ONBOARDING_DETAILS"]) < 500   # was a ~600-character wall


def test_translators_md_flags_the_d6_strings():
    text = (Path(constants.__file__).parents[3] / "TRANSLATORS.md").read_text(encoding="utf-8")
    for k in ADDED + RETIRED + ("HARDWARE_TEMPS_LIMITATION_NOTE",):
        assert f"`{k}`" in text, k
