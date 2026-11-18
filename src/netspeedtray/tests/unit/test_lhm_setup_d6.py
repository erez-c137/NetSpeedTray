"""
2.2.2 D6 - temperature setup that works with current LibreHardwareMonitor.

LHM v0.9.5+ is read over its web server (#187), which is off until the user picks Options -> Remote Web
Server -> Run. The onboarding used to say temperatures "appear automatically" once LHM runs, and only a
~600-character hint mentioned the web server - so anyone following it with today's LHM got blank temps.

Covers: the onboarding as two numbered steps that follow LHM's real menus (Options -> Remote Web Server
-> Run; Interface / Port only on another port), live state while open, details behind "More details";
"LHM is running but its web server is off" detection (process list + a connect that was refused, at
the probe's own cadence, expiring, suppressed while a WMI source reads); the Settings > Hardware InfoBar;
right-to-left text; no network beyond 127.0.0.1; the retired / added locale keys.
"""
import json
import logging
import socket
import threading
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import lhm_http
from netspeedtray.core.lhm_http import LhmHttpClient, lhm_process_running
from netspeedtray.utils import styles as su
from netspeedtray.utils import taskbar_appearance as ta

L = constants.lhm
EN = I18nStrings("en_US")
LOCALES = Path(constants.__file__).parent / "locales"
MENU_RUN = "Options → Remote Web Server → Run"
MENU_PORT = "Options → Remote Web Server → Interface / Port"


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


def test_onboarding_is_two_numbered_steps_that_follow_lhms_menus(q_app, qt_owned):
    dlg = _dialog(qt_owned)
    assert len(dlg.steps) == 2
    one, two = dlg.steps
    assert one.number == 1 and two.number == 2
    assert one.title() == "Download and open LibreHardwareMonitor"
    assert "LibreHardwareMonitor.zip" in one.description() and "PawnIO" in one.description()
    assert two.title() == "Turn on its web server"
    assert MENU_RUN in two.description()
    assert MENU_PORT not in two.description()           # 8085 is LHM's own default: nothing to change
    # Narrator hears the step number with the title, and the description.
    assert one.accessibleName() == "1. " + one.title() and one.accessibleDescription() == one.description()
    assert two.accessibleName().startswith("2")


def test_another_port_adds_the_interface_port_sentence(q_app, qt_owned):
    two = _dialog(qt_owned, lhm_port=18085).steps[1]
    assert MENU_PORT in two.description() and "18085" in two.description()
    assert "{port}" not in two.description()


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


def test_details_are_collapsed_until_asked_for_and_one_label_per_point(q_app, qt_owned):
    dlg = _dialog(qt_owned, lhm_port=18085)
    dlg.show()
    assert not dlg.details.isExpanded() and not dlg.details_lines[0].isVisible()
    QTest.mouseClick(dlg.details.header(), Qt.MouseButton.LeftButton)
    assert dlg.details.isExpanded() and dlg.details_lines[0].isVisible()
    texts = [lbl.text() for lbl in dlg.details_lines]
    assert len(texts) == 7                               # six points + the unsigned-publisher line
    assert "Run On Windows Startup" in texts[0]          # the restart tip comes first
    assert any("18085" in t and "firewall" in t.lower() for t in texts)
    assert texts[-1] == EN.TEMP_ONBOARDING_UNSIGNED
    assert not any(t.startswith("•") for t in texts)     # the bullet is its own column (hanging indent)
    assert dlg.details.header().minimumHeight() == 40
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


def test_the_steps_follow_lhm_live_while_the_dialog_is_open(q_app, qt_owned):
    state = {"s": L.STATUS_NOT_FOUND}
    dlg = _dialog(qt_owned, status_provider=lambda: state["s"])
    dlg.show()
    assert dlg._status_timer.isActive()
    assert dlg._status_timer.interval() == L.STATUS_REFRESH_MS
    one, two = dlg.steps
    assert one.current and not one.done and not dlg.connected_label.isVisible()
    state["s"] = L.STATUS_SERVER_OFF
    dlg.refresh_status()
    assert one.done and two.current
    state["s"] = L.STATUS_OK
    dlg.refresh_status()
    assert one.done and two.done and not two.current
    assert dlg.connected_label.isVisible() and dlg.connected_label.text() == EN.TEMP_ONBOARDING_CONNECTED
    assert dlg.close_button.property("accent") == "true"
    dlg.close_button.click()
    assert not dlg._status_timer.isActive()             # stops with the dialog


def test_close_has_a_focus_ring_only_for_the_keyboard(q_app, qt_owned):
    from PyQt6.QtWidgets import QApplication
    dlg = _dialog(qt_owned)
    dlg.show()
    dlg.activateWindow()
    QApplication.processEvents()
    b = dlg.close_button
    assert b.hasFocus() or dlg.focusWidget() is b
    assert b.property("kbfocus") == "false"             # opening the dialog is not a key press
    if not dlg.isActiveWindow():
        pytest.skip("the offscreen platform did not activate the dialog")
    dlg.get_lhm_link.button().setFocus(Qt.FocusReason.MouseFocusReason)
    b.setFocus(Qt.FocusReason.TabFocusReason)
    assert b.property("kbfocus") == "true"
    b.setFocus(Qt.FocusReason.MouseFocusReason)   # already focused: no event; move away and back
    dlg.get_lhm_link.button().setFocus(Qt.FocusReason.MouseFocusReason)
    b.setFocus(Qt.FocusReason.MouseFocusReason)
    assert b.property("kbfocus") == "false"
    dlg.close()


def test_from_settings_there_is_no_reminder_checkbox(q_app, qt_owned):
    dlg = _dialog(qt_owned, show_remind=False)
    dlg.show()
    assert not dlg.dont_remind.isVisible()
    assert dlg.dismissed_forever() is False
    assert dlg.close_button.text() == EN.CLOSE_BUTTON
    dlg.close()


def test_the_reminder_box_toggles_by_click_and_space(q_app, qt_owned):
    dlg = _dialog(qt_owned)
    dlg.show()
    QTest.mouseClick(dlg.dont_remind, Qt.MouseButton.LeftButton)
    assert dlg.dismissed_forever() is True
    dlg.dont_remind.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(dlg.dont_remind, Qt.Key.Key_Space)
    assert dlg.dismissed_forever() is False
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
    assert MENU_RUN in dlg.steps[1].description()
    assert dlg.steps[1].accessibleName().startswith("2")


# --- right-to-left text -----------------------------------------------------------------------------------

def test_bidi_isolates_lhm_menu_paths_and_starts_right_to_left():
    he = I18nStrings("he_IL").TEMP_ONBOARDING_STEP_2_DESC
    out = su.bidi_isolate(he, rtl=True)
    assert out.startswith("‏")
    iso = "\u200e" + MENU_RUN.replace(" ", " ") + "\u200e"
    assert iso in out
    assert su.bidi_plain(out) == he
    assert su.bidi_isolate(he, rtl=False) == he                    # LTR UI: untouched
    assert su.bidi_isolate(out, rtl=True) == out                   # idempotent
    # Only marks the UI face has a glyph for (a missing one loads every font family).
    from netspeedtray.utils.font_runs import family_covers
    if family_covers("Segoe UI", "A"):   # fonts visible to this platform plugin
        assert all(family_covers("Segoe UI", ch) for ch in "‏\u200e\u200e ")
    # A path with a slash and the startup item are isolated too; Hebrew "הגדרות ← חומרה" is not.
    port = su.bidi_isolate(I18nStrings("he_IL").TEMP_ONBOARDING_STEP_2_PORT, rtl=True)
    assert "\u200e" + MENU_PORT.replace(" ", " ") + "\u200e" in port
    tip = I18nStrings("he_IL").TEMP_ONBOARDING_DETAILS.split("\n")[2]
    assert "\u200e" not in su.bidi_isolate("הגדרות ← חומרה", rtl=True)
    assert "Authentication" in su.bidi_isolate(tip, rtl=True)


def test_rtl_labels_get_the_marks_but_narrator_gets_the_words(q_app, monkeypatch):
    monkeypatch.setattr(su, "_is_rtl", lambda: True)
    text = I18nStrings("he_IL").HARDWARE_TEMPS_LIMITATION_NOTE
    label = su.ui_label(text)
    assert 'dir="rtl"' in label.text() and "\u200e" in label.text()   # rich text: measured right
    assert label.accessibleName() == text
    monkeypatch.setattr(su, "_is_rtl", lambda: False)
    su.set_ui_text(label, "Plain")
    assert label.text() == "Plain" and label.accessibleName() == ""


# --- detection: running, but nothing accepts a connection ---------------------------------------------

class _Proc:
    def __init__(self, name):
        self._name = name
        self.info_written = False

    def name(self):
        if isinstance(self._name, Exception):
            raise self._name
        return self._name


def _iter_of(procs):
    def it(*args, **kwargs):
        assert not args and not kwargs, "process_iter(attrs) writes .info on shared Process objects"
        return iter(procs)
    return it


@pytest.mark.parametrize("names,expected", [
    (["explorer.exe", "LibreHardwareMonitor.exe"], True),
    (["librehardwaremonitor.EXE"], True),
    (["explorer.exe", None, "OpenHardwareMonitor.exe"], False),
    ([], False),
])
def test_lhm_process_running_matches_the_image_name(monkeypatch, names, expected):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", _iter_of([_Proc(n) for n in names]))
    assert lhm_process_running() is expected


def test_a_process_that_denies_its_name_is_skipped(monkeypatch):
    import psutil
    procs = [_Proc(psutil.AccessDenied()), _Proc(psutil.NoSuchProcess(1)), _Proc("LibreHardwareMonitor.exe")]
    monkeypatch.setattr(psutil, "process_iter", _iter_of(procs))
    assert lhm_process_running() is True


def test_lhm_process_running_never_raises(monkeypatch):
    import psutil

    def boom(*a, **k):
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


@pytest.fixture
def silent_server():
    """A server that accepts and never answers - a busy or hung LHM (review M1, slow_lhm.py)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    held, stop = [], threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                held.append(conn)
            except OSError:
                continue
    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield srv.getsockname()[1]
    stop.set()
    t.join(2)
    for conn in held:
        conn.close()
    srv.close()


def test_an_accepted_but_silent_server_is_never_server_off(silent_server):
    probes = []
    c = LhmHttpClient(port=silent_server, process_probe=lambda: probes.append(1) or True)
    assert c.get_snapshot() is None
    assert not c.server_off and c.process_running is None
    assert probes == []                                   # running, not off: no process check either


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


def test_probe_count_across_config_changes():
    """A port / credentials change retries at once (configure drops the backoff) - one check per
    attempt, never more; an unchanged configure keeps the backoff and adds none."""
    probes = []
    port = _free_port()
    c = LhmHttpClient(port=port, process_probe=lambda: probes.append(1) or True)
    c.get_snapshot()
    c.configure(port)                     # unchanged: backoff kept
    for _ in range(5):
        c.get_snapshot()
    assert probes == [1]
    c.configure(port, "user", "pw")       # changed: one new attempt, one new check
    for _ in range(5):
        c.get_snapshot()
    assert probes == [1, 1]
    c.reset_backoff()
    c.get_snapshot()
    c.get_snapshot()
    assert probes == [1, 1, 1]


def test_the_server_off_answer_expires_when_polling_stops():
    """Temperatures turned off -> no more attempts -> no fresh process check: the notice must go."""
    now = [1000.0]
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: True, clock=lambda: now[0])
    c.get_snapshot()
    assert c.server_off
    now[0] += L.PROCESS_RESULT_TTL_SEC - 1
    assert c.server_off
    now[0] += 2
    assert not c.server_off
    t = _thread_with(c)
    assert t.lhm_status == L.STATUS_NOT_FOUND
    assert L.FAILURE_BACKOFF_SEC < L.PROCESS_RESULT_TTL_SEC <= 2 * L.FAILURE_BACKOFF_SEC


def test_server_off_is_logged_once_at_info(caplog):
    c = LhmHttpClient(port=_free_port(), process_probe=lambda: True)
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.LhmHttp"):
        c.get_snapshot()
        c.reset_backoff()
        c.get_snapshot()
    lines = [r for r in caplog.records if r.levelno == logging.INFO and "Remote Web Server" in r.getMessage()]
    assert len(lines) == 1


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
    assert _thread_with(c).lhm_status == L.STATUS_SERVER_OFF


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
    creates no socket at all."""
    targets = []
    real_connect = socket.socket.connect

    def spy(self, address):
        targets.append(address)
        return real_connect(self, address)
    monkeypatch.setattr(socket.socket, "connect", spy)

    sockets_during_probe = []
    real_init = socket.socket.__init__

    def probe():
        def counting_init(self, *a, **k):
            sockets_during_probe.append(1)
            return real_init(self, *a, **k)
        monkeypatch.setattr(socket.socket, "__init__", counting_init)
        try:
            return lhm_process_running() or True     # the real process list; force "running"
        finally:
            monkeypatch.setattr(socket.socket, "__init__", real_init)

    c = LhmHttpClient(port=_free_port(), process_probe=probe)
    c.get_snapshot()
    assert c.server_off
    assert targets and {a[0] for a in targets} == {"127.0.0.1"}, targets
    assert sockets_during_probe == []


# --- Settings > Hardware: the InfoBar ------------------------------------------------------------------

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
    assert page.temps_setup_link.isVisible()


def test_the_notice_shows_when_lhm_runs_without_its_web_server(page):
    page.status["value"] = (L.STATUS_SERVER_OFF, 8085)
    page.refresh_lhm_status()
    n = page.lhm_server_off_notice
    assert n.isVisible()
    assert n.text() == EN.LHM_SERVER_OFF_NOTICE == "LibreHardwareMonitor's Remote Web Server is off."
    assert n.action_text() == EN.LHM_SERVER_OFF_ACTION
    assert not page.temps_setup_link.isVisible()        # the bar's own link is the next step
    assert n.icon.pixmap() is not None and not n.icon.pixmap().isNull()
    # The port card still says what the client found.
    assert page._lhm_port_card._desc_label.text() == EN.LHM_STATUS_NOT_FOUND.format(port=8085)


def test_on_another_port_the_notice_only_says_what_is_known(page):
    page.lhm_port.setValue(18085)
    page.status["value"] = (L.STATUS_SERVER_OFF, 18085)
    page.refresh_lhm_status()
    assert page.lhm_server_off_notice.text() == EN.LHM_SERVER_NO_ANSWER_NOTICE.format(port=18085)


@pytest.mark.parametrize("status", [L.STATUS_OK, L.STATUS_NOT_FOUND, L.STATUS_AUTH_REQUIRED,
                                    L.STATUS_AUTH_FAILED, L.STATUS_OTHER, L.STATUS_UNKNOWN])
def test_the_notice_hides_for_every_other_status(page, status):
    page.status["value"] = (L.STATUS_SERVER_OFF, 8085)
    page.refresh_lhm_status()
    page.status["value"] = (status, 8085)
    page.refresh_lhm_status()
    assert not page.lhm_server_off_notice.isVisible()
    assert page.temps_setup_link.isVisible()


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


def test_the_temps_note_names_the_menu_item_and_not_automatic_detection():
    note = EN.HARDWARE_TEMPS_LIMITATION_NOTE
    assert MENU_RUN in note and "automatically" not in note.lower()


def test_settings_opens_the_onboarding_with_the_page_port_live_and_without_the_reminder(q_app, monkeypatch):
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
    host = type("D", (), {"i18n": EN, "hardware_page": page, "logger": logging.getLogger("t"),
                          "_lhm_status": lambda self: (L.STATUS_SERVER_OFF, 18085),
                          "_lhm_status_for_setup": SettingsDialog._lhm_status_for_setup})()
    SettingsDialog._open_temp_setup(host, 2)
    kw = made[0]
    assert (kw["lhm_port"], kw["focus_step"], kw["show_remind"]) == (18085, 2, False)
    assert kw["status_provider"]() == L.STATUS_SERVER_OFF
    host._lhm_status = lambda: (L.STATUS_SERVER_OFF, 8085)   # describes another port: not followed
    assert SettingsDialog._lhm_status_for_setup(host) is None


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

        def deleteLater(self):
            pass
    monkeypatch.setattr(tod, "TempOnboardingDialog", _Fake)
    from netspeedtray.views.widget import startup_notices as sn
    monkeypatch.setattr(sn, "_seconds_since_sign_in", lambda: 3600.0)   # not in the sign-in rush
    for status, step in ((L.STATUS_SERVER_OFF, 2), (L.STATUS_NOT_FOUND, 1)):
        mt = type("MT", (), {"lhm_port": 8085, "lhm_status": status})()
        host = type("W", (), {"config": dict(_WANTS_TEMPS), "monitor_thread": mt, "i18n": EN,
                              "logger": logging.getLogger("t"),
                              "update_config": lambda self, u: None})()
        NetworkSpeedWidget._on_lhm_not_detected(host)
        assert made[-1]["focus_step"] == step and made[-1]["lhm_port"] == 8085
        assert made[-1]["status_provider"]() == status   # follows the monitor thread live


# --- strings --------------------------------------------------------------------------------------------

def _locale(code):
    return json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))


RETIRED = ("LHM_WEB_SERVER_HINT", "TEMP_ONBOARDING_BODY", "TEMP_ONBOARDING_NOT_NOW_BUTTON")
ADDED = ("TEMP_ONBOARDING_INTRO", "TEMP_ONBOARDING_STEP_1", "TEMP_ONBOARDING_STEP_1_DESC",
         "TEMP_ONBOARDING_STEP_2", "TEMP_ONBOARDING_STEP_2_DESC", "TEMP_ONBOARDING_STEP_2_PORT",
         "TEMP_ONBOARDING_CONNECTED", "TEMP_ONBOARDING_MORE_DETAILS", "TEMP_ONBOARDING_DETAILS",
         "TEMP_ONBOARDING_UNSIGNED", "HARDWARE_TEMPS_SETUP_ACTION", "LHM_SERVER_OFF_NOTICE",
         "LHM_SERVER_NO_ANSWER_NOTICE", "LHM_SERVER_OFF_ACTION")
CHANGED = ("TEMP_ONBOARDING_TITLE", "HARDWARE_TEMPS_LIMITATION_NOTE", "LHM_STATUS_AUTH_REQUIRED",
           "TEMP_ONBOARDING_GET_LHM_BUTTON")


@pytest.mark.parametrize("code", sorted(constants.I18nStrings.LANGUAGE_MAP))
def test_every_locale_has_the_new_keys_and_none_of_the_retired_ones(code):
    data = _locale(code)
    assert not [k for k in RETIRED if k in data]
    assert all(data.get(k, "").strip() for k in ADDED)


@pytest.mark.parametrize("code", sorted(constants.I18nStrings.LANGUAGE_MAP))
def test_every_locale_keeps_lhm_menu_names_and_the_port(code):
    data = _locale(code)
    assert MENU_RUN in data["TEMP_ONBOARDING_STEP_2_DESC"]          # LHM's own menus stay in English
    assert MENU_PORT in data["TEMP_ONBOARDING_STEP_2_PORT"]
    assert data["TEMP_ONBOARDING_STEP_2_PORT"].count("{port}") == 1
    assert MENU_RUN in data["HARDWARE_TEMPS_LIMITATION_NOTE"]
    assert "Options → Remote Web Server → Authentication" in data["LHM_STATUS_AUTH_REQUIRED"]
    assert data["LHM_SERVER_NO_ANSWER_NOTICE"].count("{port}") == 1
    assert "Remote Web Server" in data["LHM_SERVER_OFF_NOTICE"]
    details = data["TEMP_ONBOARDING_DETAILS"].split("\n")
    assert len(details) == 6 and not any(d.lstrip().startswith("•") for d in details)
    assert "Options → Run On Windows Startup" in details[0]
    assert "Options → Remote Web Server → Authentication" in details[2]
    assert details[3].count("{port}") == 1 and "0.9.6" in details[3]
    assert "0.9.4" in details[4]
    assert "PawnIO" in data["TEMP_ONBOARDING_STEP_1_DESC"]
    assert "LibreHardwareMonitor.exe" in data["TEMP_ONBOARDING_STEP_1_DESC"]
    for k in ADDED + CHANGED:
        assert "<" not in data[k], k   # plain text: no markup for translators to keep in step
        data[k].format(port=8085)


def test_hebrew_keeps_the_english_menu_name_for_the_web_server():
    he = _locale("he_IL")
    for k in ("LHM_SERVER_OFF_NOTICE", "TEMP_ONBOARDING_STEP_2", "TEMP_ONBOARDING_DETAILS"):
        assert "שרת האינטרנט" not in he[k], k
    assert "Remote Web Server" in he["TEMP_ONBOARDING_STEP_2"]
    assert he["TEMP_ONBOARDING_GET_LHM_BUTTON"].startswith("הורידו")
    assert "herunterladen" in _locale("de_DE")["TEMP_ONBOARDING_GET_LHM_BUTTON"]


def test_english_strings_match_the_review():
    en = _locale("en_US")
    assert en["TEMP_ONBOARDING_TITLE"] == "Show CPU and GPU temperatures"
    assert en["TEMP_ONBOARDING_STEP_1"] == "Download and open LibreHardwareMonitor"
    assert en["TEMP_ONBOARDING_STEP_2"] == "Turn on its web server"
    assert en["TEMP_ONBOARDING_STEP_2_DESC"] == (
        "In LibreHardwareMonitor, choose Options → Remote Web Server → Run. Temperatures appear here within "
        "a minute. NetSpeedTray reads it only from this PC.")
    assert en["TEMP_ONBOARDING_STEP_1_DESC"].endswith("If it offers to install PawnIO, its sensor driver, choose OK.")
    for k in ("TEMP_ONBOARDING_STEP_1", "TEMP_ONBOARDING_STEP_2", "LHM_SERVER_OFF_NOTICE",
              "LHM_SERVER_OFF_ACTION", "HARDWARE_TEMPS_SETUP_ACTION", "TEMP_ONBOARDING_MORE_DETAILS",
              "TEMP_ONBOARDING_CONNECTED"):
        assert len(en[k]) <= 80, k


def test_translators_md_flags_the_d6_strings():
    text = (Path(constants.__file__).parents[3] / "TRANSLATORS.md").read_text(encoding="utf-8")
    for k in ADDED + RETIRED + CHANGED:
        assert f"`{k}`" in text, k
    assert "~~`LHM_WEB_SERVER_HINT`~~" in text and "~~`TEMP_ONBOARDING_BODY`~~" in text


# --- combined review C10/C11: the automatic temperature setup shows once ever, through the card gates --

class _FakeSetup:
    made = []

    def __init__(self, i18n, parent=None, **kw):
        _FakeSetup.made.append(kw)
        self.deleted = False

    def exec(self):
        return 0

    def dismissed_forever(self):
        return False

    def deleteLater(self):
        self.deleted = True


_WANTS_TEMPS = {"show_hardware_temps": True, "monitor_cpu_enabled": True}


def _nag_host(config=None, **attrs):
    import types
    mt = types.SimpleNamespace(lhm_port=8085, lhm_status=L.STATUS_NOT_FOUND)
    host = types.SimpleNamespace(config=dict(_WANTS_TEMPS) if config is None else config, monitor_thread=mt, i18n=EN,
                                 logger=logging.getLogger("t"), settings_dialog=None, _running_panel=None,
                                 _update_flyout=None, _notice_flyout=None, _usage_alert_flyout=None,
                                 _startup_notices=None, _update_offer_blocked=lambda: False,
                                 _app_is_quitting=lambda: False)
    host.update_config = lambda u: host.config.update(u)
    host.__dict__.update(attrs)
    return host


@pytest.fixture
def nag_world(monkeypatch):
    from netspeedtray.views import temp_onboarding_dialog as tod
    from netspeedtray.views.widget import startup_notices as sn
    _FakeSetup.made = []
    monkeypatch.setattr(tod, "TempOnboardingDialog", _FakeSetup)
    state = type("S", (), {"since": 3600.0})()
    monkeypatch.setattr(sn, "_seconds_since_sign_in", lambda: state.since)
    return state


def test_the_automatic_setup_shows_once_ever_closing_counts_as_dismissed(q_app, nag_world):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    from netspeedtray.views.widget import startup_notices as sn
    host = _nag_host()
    NetworkSpeedWidget._on_lhm_not_detected(host)
    assert len(_FakeSetup.made) == 1
    assert _FakeSetup.made[-1]["show_remind"] is False          # closing IS the dismissal: no check box
    assert host.config["temp_onboarding_dismissed"] is True     # used up, whatever the close
    assert sn.moment_taken(host) == sn.MOMENT_TEMP_SETUP
    nxt = _nag_host(config=dict(host.config))                   # the next launch
    NetworkSpeedWidget._on_lhm_not_detected(nxt)
    assert len(_FakeSetup.made) == 1                            # never again on its own


@pytest.mark.parametrize("why", ["sign_in", "blocked", "settings", "card", "panel", "notice_owed", "moment"])
def test_the_automatic_setup_waits_behind_the_card_gates(q_app, nag_world, why):
    import types
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    from netspeedtray.views.widget import startup_notices as sn
    visible = types.SimpleNamespace(isVisible=lambda: True)
    host = _nag_host()
    if why == "sign_in":
        nag_world.since = 30.0
    elif why == "blocked":                                      # fullscreen, a modal dialog, away
        host._update_offer_blocked = lambda: True
    elif why == "settings":
        host.settings_dialog = visible
    elif why == "card":
        host._update_flyout = visible
    elif why == "panel":
        host._running_panel = visible
    elif why == "notice_owed":
        host._startup_notices = types.SimpleNamespace(owes_moment=lambda: True)
    elif why == "moment":
        sn.take_moment(host, sn.MOMENT_SUPPORT)
    assert NetworkSpeedWidget._temp_setup_blocked_reason(host) is not None
    NetworkSpeedWidget._on_lhm_not_detected(host)
    assert _FakeSetup.made == []
    assert host.config.get("temp_onboarding_dismissed", False) is False     # still owed


def test_the_widgets_setup_dialog_is_deleted_after_it_closes(q_app, nag_world):
    """C10: the dialog parented to the widget is released once exec returns (it lived until quit)."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    host = _nag_host()
    made = []
    orig = _FakeSetup.__init__

    def init(self, *a, **kw):
        orig(self, *a, **kw)
        made.append(self)
    _FakeSetup.__init__ = init
    try:
        NetworkSpeedWidget._on_lhm_not_detected(host)
    finally:
        _FakeSetup.__init__ = orig
    assert made and made[0].deleted is True


@pytest.mark.parametrize("why", ["temps_and_power_off", "cpu_and_gpu_off", "lhm_connected"])
def test_a_deferred_setup_that_is_no_longer_needed_is_dropped_not_used_up(q_app, nag_world, why):
    """C11 follow-up: between the notice and its moment (it may wait behind the gates) the user turned
    temperatures off, or LibreHardwareMonitor started answering - the dialog is dropped, and its one
    showing is NOT used up."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    host = _nag_host()
    if why == "temps_and_power_off":
        host.config.update(show_hardware_temps=False, show_hardware_power=False)
    elif why == "cpu_and_gpu_off":
        host.config.update(monitor_cpu_enabled=False, monitor_gpu_enabled=False)
    else:
        host.monitor_thread.lhm_status = L.STATUS_OK
    NetworkSpeedWidget._on_lhm_not_detected(host)
    assert _FakeSetup.made == []
    assert host.config.get("temp_onboarding_dismissed", False) is False
    from netspeedtray.views.widget import startup_notices as sn
    assert sn.moment_taken(host) is None
