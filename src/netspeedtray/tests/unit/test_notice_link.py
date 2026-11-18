"""
2.2.2 D4 - "one-click opt-ins". Moment-of-need prompts used to only SAY where to go ("Turn on CPU or GPU
monitoring in Settings -> Hardware" drawn into the Hardware chart; "turn on ... in Settings" under Check
connection). Now each one is a NoticeLink - a notice with a real, keyboard-reachable action - and the
action goes through one settings router, NetworkSpeedWidget.open_settings(page, anchor_key), that opens
Settings on the page and lands on (scrolls to, outlines, focuses) the exact control.

Covers: the component (activation by mouse / Enter / Space, accessible name, focus, swappable action,
theme incl. High Contrast, RTL); the router (page + anchor, collapsed expanders, aliases, unknown keys,
open_data_usage_settings still landing on Data usage); the two replaced prompts.
"""
import logging
from unittest.mock import MagicMock

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import components
from netspeedtray.utils import taskbar_appearance as ta
from netspeedtray.utils.color_scheme import contrast_ratio
from netspeedtray.utils.components import NoticeLink, flash_highlight, readable_on
from netspeedtray.views.settings.dialog import SettingsDialog
from netspeedtray.views.widget.main import NetworkSpeedWidget

EN = I18nStrings("en_US")
DEFAULTS = constants.config.defaults.DEFAULT_CONFIG


@pytest.fixture(autouse=True)
def _no_high_contrast(monkeypatch):
    """The suite never reads the machine's real High Contrast state (a test opts in explicitly)."""
    monkeypatch.setattr(ta, "read_high_contrast_link_colors", lambda: None)


def _drain():
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QApplication.processEvents()


# --- the component ------------------------------------------------------------------------------------

def test_mouse_click_runs_the_action_once_and_emits_activated(q_app, qt_owned):
    calls, seen = [], []
    link = qt_owned(NoticeLink("Hardware history is off.", "Turn on in Settings", lambda: calls.append(1)))
    link.activated.connect(lambda: seen.append(1))
    QTest.mouseClick(link.button(), Qt.MouseButton.LeftButton)
    assert calls == [1] and seen == [1]


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space])
def test_enter_and_space_activate(q_app, qt_owned, key):
    calls = []
    link = qt_owned(NoticeLink("x", "Go", lambda: calls.append(1)))
    QTest.keyClick(link.button(), key)
    assert calls == [1]


def test_held_enter_fires_once_and_never_reaches_the_dialog(q_app, qt_owned):
    """An auto-repeated Enter is swallowed, not passed up to a dialog that would press its default button."""
    calls = []
    link = qt_owned(NoticeLink("x", "Go", lambda: calls.append(1)))
    btn = link.button()
    QApplication.sendEvent(btn, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "", True)
    QApplication.sendEvent(btn, repeat)
    assert calls == [1]
    assert repeat.isAccepted()


def test_it_is_a_real_tab_stop_with_an_accessible_name(q_app, qt_owned):
    link = qt_owned(NoticeLink("Hardware history needs CPU or GPU monitoring.", "Turn on in Settings"))
    btn = link.button()
    assert isinstance(btn, QPushButton)                       # UIA: a Button with Invoke
    assert btn.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert btn.accessibleName() == "Turn on in Settings"      # no arrow read out
    assert btn.accessibleDescription() == "Hardware history needs CPU or GPU monitoring."
    assert not btn.autoDefault() and not btn.isDefault()      # never steals a dialog's Enter
    assert "Turn on in Settings" in btn.text()
    link.show()
    btn.setFocus()
    QApplication.processEvents()
    assert btn.hasFocus() or QApplication.focusWidget() is btn or link.window().focusWidget() is btn


def test_the_action_can_be_swapped(q_app, qt_owned):
    """D5 swaps a Settings deep link for an inline confirm on the same NoticeLink."""
    first, second = [], []
    link = qt_owned(NoticeLink("x", "Go", lambda: first.append(1)))
    link.set_on_click(lambda: second.append(1))
    link.click()
    assert first == [] and second == [1]
    link.set_on_click(None)
    link.click()                                               # no action: nothing breaks
    assert second == [1]


def test_a_failing_action_is_logged_not_raised(q_app, qt_owned, caplog):
    def boom():
        raise RuntimeError("nope")
    link = qt_owned(NoticeLink("x", "Go", boom))
    with caplog.at_level(logging.ERROR):
        link.click()
    assert "nope" in caplog.text


def test_text_can_be_empty_and_changed(q_app, qt_owned):
    link = qt_owned(NoticeLink("", "Go"))
    assert link.label().isHidden()
    link.set_text("Now there is text")
    link.set_action_text("Elsewhere")
    assert not link.label().isHidden() and link.text() == "Now there is text"
    assert link.button().accessibleName() == "Elsewhere"
    assert link.button().accessibleDescription() == "Now there is text"


def test_rtl_flips_the_arrow(q_app, qt_owned):
    link = qt_owned(NoticeLink("x", "Go"))
    assert link.button().text().endswith("→")
    link.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    assert link.button().text().endswith("←")
    assert link.button().accessibleName() == "Go"


@pytest.mark.parametrize("dark", [True, False])
def test_the_link_colour_is_readable_in_both_themes(q_app, qt_owned, monkeypatch, dark):
    """A dark accent on the dark theme (or a pale one on light) is nudged to 4.5:1 on the card."""
    from netspeedtray.utils import styles
    from PyQt6.QtGui import QColor
    monkeypatch.setattr(components, "is_dark_mode", lambda: dark)
    monkeypatch.setattr(styles, "is_dark_mode", lambda: dark)
    accent = "#003A70" if dark else "#9AD0FF"
    monkeypatch.setattr(styles, "get_accent_color", lambda: QColor(accent))
    link = qt_owned(NoticeLink("x", "Go"))
    surface = constants.styles.CARD_BG_DARK if dark else constants.styles.CARD_BG_LIGHT
    c = link.colors()
    assert contrast_ratio(c["link"], surface) >= 4.5
    assert c["background"] is None and c["underline"] is False
    assert c["link"] in link.styleSheet()


def test_readable_on_keeps_an_already_readable_colour():
    assert readable_on("#000000", "#FFFFFF") == "#000000"


def test_high_contrast_uses_the_theme_colours_and_underlines(q_app, qt_owned, monkeypatch):
    hc = {"text": "#FFFFFF", "background": "#000000", "link": "#FFFF00", "focus": "#1AEBFF"}
    monkeypatch.setattr(ta, "read_high_contrast_link_colors", lambda: dict(hc))
    link = qt_owned(NoticeLink("x", "Go"))
    c = link.colors()
    assert (c["text"], c["link"], c["focus"], c["background"]) == ("#FFFFFF", "#FFFF00", "#1AEBFF", "#000000")
    qss = link.styleSheet()
    assert "#FFFF00" in qss and "#000000" in qss and "text-decoration: underline" in qss


def test_flash_highlight_outlines_then_goes_away(q_app, qt_owned):
    card = qt_owned(QWidget())
    card.resize(200, 60)
    overlay = flash_highlight(card, duration_ms=10)
    assert overlay is not None and overlay.parentWidget() is card
    assert overlay.geometry() == card.rect()
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    QTest.qWait(40)
    _drain()
    assert sip.isdeleted(overlay)


# --- the router -----------------------------------------------------------------------------------------

class _Host:
    """The widget's Settings plumbing (the real NetworkSpeedWidget methods) on a light stand-in."""
    _W = NetworkSpeedWidget
    show_settings = _W.show_settings
    open_settings = getattr(_W, "open_settings", None)
    open_data_usage_settings = _W.open_data_usage_settings
    _on_settings_dialog_finished = getattr(_W, "_on_settings_dialog_finished", lambda self, *a: None)
    _release_settings_dialog = getattr(_W, "_release_settings_dialog", lambda self: None)
    _font_heavy_window_open = getattr(_W, "_font_heavy_window_open", lambda self: False)
    _on_font_heavy_window_destroyed = getattr(_W, "_on_font_heavy_window_destroyed", lambda self, *a: None)

    def __init__(self, **cfg):
        self.logger = logging.getLogger("test.host")
        self.config = dict(DEFAULTS, **cfg)
        self.config_manager = MagicMock()
        self.i18n = EN
        self.settings_dialog = None
        self.monitor_window = None
        self.metrics_endpoint = None
        self.widget_state = None

    def get_unified_interface_list(self):
        return ["Ethernet"]

    def is_startup_enabled(self):
        return False

    def _primary_interface_for_picker(self):
        return None

    def handle_settings_changed(self, cfg, save_to_disk=True):
        self.config.update(cfg)

    def toggle_startup(self, on):
        pass

    def update(self):
        pass

    def width(self):
        return 200

    def _execute_refresh(self):
        pass


@pytest.fixture
def make_host(q_app, monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    orig_init = SettingsDialog.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    monkeypatch.setattr(SettingsDialog, "__init__", init)
    hosts = []

    def make(**cfg):
        h = _Host(**cfg)
        hosts.append(h)
        return h
    yield make
    for h in hosts:
        dlg = h.settings_dialog
        if dlg is not None and not sip.isdeleted(dlg):
            dlg.setParent(None)
            sip.delete(dlg)
    _drain()


def _landed(dlg):
    key, targets = getattr(dlg, "_last_anchor", (None, []))
    return key, targets


def _inside(widget, ancestor) -> bool:
    w = widget
    while w is not None:
        if w is ancestor:
            return True
        w = w.parentWidget()
    return False


def test_the_widget_has_a_settings_router():
    assert callable(getattr(NetworkSpeedWidget, "open_settings", None))


def test_router_lands_on_the_hardware_monitoring_switches(make_host):
    host = make_host(monitor_cpu_enabled=False, monitor_gpu_enabled=False)
    host.open_settings("hardware", "hardware_monitoring")
    dlg = host.settings_dialog
    QApplication.processEvents()
    assert dlg.stack.currentIndex() == SettingsDialog.PAGE_HARDWARE
    assert dlg.sidebar.currentRow() == SettingsDialog.PAGE_HARDWARE
    page = dlg._built_pages["hardware"]
    key, targets = _landed(dlg)
    assert key == "hardware_monitoring"
    assert [t.objectName() for t in targets] == ["settingCard", "settingCard"]
    assert _inside(page.monitor_cpu, targets[0]) and _inside(page.monitor_gpu, targets[1])
    # outlined, and keyboard focus is on the CPU switch (a screen reader says where it landed)
    for t in targets:
        assert t.findChildren(QWidget, "anchorHighlight")
    assert _inside(dlg.focusWidget(), page.monitor_cpu)
    # landing never changes the setting
    assert page.monitor_cpu.isChecked() is False and page.monitor_gpu.isChecked() is False


def test_router_lands_on_record_hardware_history(make_host):
    host = make_host(monitor_cpu_enabled=True, record_hardware_history=False)
    host.open_settings("advanced", "record_hardware_history")
    dlg = host.settings_dialog
    assert dlg.stack.currentIndex() == SettingsDialog._PAGE_NAMES.index("advanced")
    key, targets = _landed(dlg)
    assert targets == [dlg._built_pages["advanced"]._record_card]


def test_router_lands_on_the_internet_check_switch(make_host):
    host = make_host(latency_enabled=True, latency_public_enabled=False)
    host.open_settings("network", "latency_public_enabled")
    dlg = host.settings_dialog
    assert dlg.stack.currentIndex() == SettingsDialog.PAGE_NETWORK
    conn = dlg._built_pages["interfaces"].connection
    key, targets = _landed(dlg)
    assert len(targets) == 1 and targets[0].objectName() == "settingCard"
    assert _inside(conn._public, targets[0])
    assert _inside(dlg.focusWidget(), conn._public)
    assert conn._public.isChecked() is False


def test_with_latency_off_it_lands_on_the_section_switch_without_turning_it_on(make_host):
    """The internet switch lives in the latency section's body, hidden while latency is off. Opening
    that body means turning latency ON - a deep link never changes a setting - so it lands on the
    section's header switch instead."""
    host = make_host(latency_enabled=False, latency_public_enabled=False)
    host.open_settings("network", "latency_public_enabled")
    dlg = host.settings_dialog
    conn = dlg._built_pages["interfaces"].connection
    key, targets = _landed(dlg)
    assert targets == [conn._lat.header()]
    assert conn._lat.isChecked() is False and conn._lat.isExpanded() is False


def test_a_collapsed_plain_expander_is_opened(make_host):
    host = make_host()
    host.open_settings("network", "plan_speed")
    dlg = host.settings_dialog
    conn = dlg._built_pages["interfaces"].connection
    key, targets = _landed(dlg)
    assert targets == [conn._plan.header()]


def test_data_usage_link_still_lands_on_data_usage(make_host):
    host = make_host()
    host.open_data_usage_settings()
    dlg = host.settings_dialog
    assert dlg.stack.currentIndex() == SettingsDialog.PAGE_NETWORK
    key, targets = _landed(dlg)
    assert targets == [dlg._built_pages["interfaces"].datacap._expander.header()]


def test_page_ids_aliases_and_indices(make_host):
    host = make_host()
    host.show_settings()
    dlg = host.settings_dialog
    assert dlg.page_index("network") == dlg.page_index("interfaces") == SettingsDialog.PAGE_NETWORK
    assert dlg.page_index("hardware") == SettingsDialog.PAGE_HARDWARE
    assert dlg.page_index(SettingsDialog.PAGE_HARDWARE) == SettingsDialog.PAGE_HARDWARE
    assert dlg.page_index("nope") == -1 and dlg.page_index(99) == -1


def test_unknown_anchor_still_opens_the_page_and_unknown_page_changes_nothing(make_host):
    host = make_host()
    host.open_settings("hardware", "no_such_control")
    dlg = host.settings_dialog
    assert dlg.stack.currentIndex() == SettingsDialog.PAGE_HARDWARE
    assert _landed(dlg) == (None, [])
    host.open_settings("no_such_page", "monitor_cpu_enabled")
    assert dlg.stack.currentIndex() == SettingsDialog.PAGE_HARDWARE


def test_the_router_reuses_an_open_dialog(make_host):
    host = make_host()
    host.open_settings("hardware", "hardware_monitoring")
    first = host.settings_dialog
    host.open_settings("advanced", "record_hardware_history")
    assert host.settings_dialog is first
    assert first.stack.currentIndex() == SettingsDialog._PAGE_NAMES.index("advanced")


# --- the replaced prompts ------------------------------------------------------------------------------

def test_check_connection_hint_is_a_notice_link_to_the_internet_switch(q_app, qt_owned, monkeypatch):
    from netspeedtray.core import latency_probe as LP
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    monkeypatch.setattr(LP, "network_details", lambda: None)
    routed = []
    sheet = qt_owned(ConnectionCheckSheet({"latency_public_enabled": False}, EN, gateway_ip="192.0.2.1",
                                          autostart=False,
                                          open_settings=lambda page, anchor=None: routed.append((page, anchor))))
    hint = sheet._hint
    assert isinstance(hint, NoticeLink)
    assert hint.text() == EN.CHECK_CONN_ALWAYS_NOTICE
    assert hint.button().accessibleName() == EN.NOTICE_TURN_ON_IN_SETTINGS
    assert not hint.isHidden() and not hint.button().isHidden()
    QTest.keyClick(hint.button(), Qt.Key.Key_Return)
    assert routed == [("network", "latency_public_enabled")]
    assert sheet.result() == 0 and not sheet.isVisible()       # the modal sheet got out of the way


def test_check_connection_hint_hidden_when_the_internet_check_is_on(q_app, qt_owned, monkeypatch):
    from netspeedtray.core import latency_probe as LP
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    monkeypatch.setattr(LP, "network_details", lambda: None)
    sheet = qt_owned(ConnectionCheckSheet({"latency_public_enabled": True}, EN, gateway_ip="192.0.2.1",
                                          autostart=False, open_settings=lambda *a: None))
    assert sheet._hint.isHidden()


def test_open_connection_check_hands_the_sheet_the_router(q_app, monkeypatch):
    from types import SimpleNamespace
    from netspeedtray.views.monitor import connection_check as view
    seen = {}

    class _Sheet:
        def __init__(self, config, i18n, **kw):
            seen.update(kw)

        def exec(self):
            return 0
    monkeypatch.setattr(view, "ConnectionCheckSheet", _Sheet)
    router = MagicMock()
    view.open_connection_check(SimpleNamespace(latency_probe=None, open_settings=router), {}, EN)
    assert seen["open_settings"] is router


def _bare_renderer(monkeypatch):
    from matplotlib.figure import Figure
    from netspeedtray.views.graph.renderer import GraphRenderer

    def bare(self):
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = MagicMock()
        self.ax_download = self.figure.add_subplot(2, 1, 1)
        self.ax_upload = self.figure.add_subplot(2, 1, 2)
        self.axes = [self.ax_download, self.ax_upload]
        self._is_dark_mode = False
        self._current_text_color = constants.styles.LIGHT_MODE_TEXT_COLOR
        self._current_grid_color = constants.styles.GRID_COLOR_LIGHT
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", bare)
    return GraphRenderer(MagicMock(), EN)


@pytest.mark.parametrize("off,kind", [({"cpu": "monitor", "gpu": "monitor"}, "monitor"),
                                      ({"cpu": "record", "gpu": "record"}, "record"),
                                      ({"cpu": None, "gpu": None}, None)])
@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate", "cpu"])
def test_renderer_reports_the_notice_instead_of_drawing_it(monkeypatch, off, kind, stat):
    from datetime import datetime, timedelta
    r = _bare_renderer(monkeypatch)
    r.notice_overlay = True
    end = datetime.now()
    data = {"cpu": [], "gpu": [], "ram": []} if stat.startswith("hw") else []
    r.render(data, end - timedelta(hours=24), end, "TIMELINE_24_HOURS", stat_type=stat,
             hw_styles={"history_off": off})
    assert r.history_off_notice == kind
    texts = [t.get_text() for ax in r.figure.axes for t in ax.texts]
    assert EN.HARDWARE_HISTORY_OFF_MESSAGE not in texts
    assert EN.HARDWARE_HISTORY_RECORD_OFF_MESSAGE not in texts


@pytest.mark.parametrize("kind,target,text", [
    ("monitor", ("hardware", "hardware_monitoring"), "HW_HISTORY_MONITOR_OFF_NOTICE"),
    ("record", ("advanced", "record_hardware_history"), "HW_HISTORY_RECORD_OFF_NOTICE"),
])
def test_hardware_chart_notice_has_a_working_settings_button(q_app, qt_owned, kind, target, text):
    from netspeedtray.views.monitor.graph_host import HistoryOffNotice
    canvas = qt_owned(QWidget())
    canvas.resize(800, 400)
    canvas.show()                                              # the Monitor's canvas is on screen
    routed = []
    notice = HistoryOffNotice(EN, lambda page, anchor=None: routed.append((page, anchor)))
    notice.sync(canvas, kind)
    link = notice.widget
    assert isinstance(link, NoticeLink) and link.parentWidget() is canvas and not link.isHidden()
    assert link.text() == getattr(EN, text)
    assert link.button().accessibleName() == EN.NOTICE_TURN_ON_IN_SETTINGS
    # centred on the canvas, and stays centred when the canvas resizes
    assert abs(link.geometry().center().x() - canvas.rect().center().x()) <= 2
    canvas.resize(600, 300)
    QApplication.processEvents()
    assert abs(link.geometry().center().x() - canvas.rect().center().x()) <= 2
    QTest.keyClick(link.button(), Qt.Key.Key_Space)
    assert routed == [target]
    notice.sync(canvas, None)                                  # data arrived / network graph
    assert link.isHidden() and notice.kind is None


def test_graph_host_shows_the_notice_only_on_hardware_stats(q_app, qt_owned):
    from types import SimpleNamespace
    from netspeedtray.views.monitor.graph_host import GraphHost, HistoryOffNotice
    canvas = qt_owned(QWidget())
    canvas.resize(800, 400)
    routed = []
    host = GraphHost.__new__(GraphHost)
    host._main_widget = SimpleNamespace(open_settings=lambda p, a=None: routed.append((p, a)))
    host.renderer = SimpleNamespace(canvas=canvas, history_off_notice="monitor")
    host._history_notice = HistoryOffNotice(EN, host._route_settings)
    host._current_stat = "hwcombined"
    host._sync_history_notice()
    assert host._history_notice.kind == "monitor"
    host._history_notice.widget.click()
    assert routed == [("hardware", "hardware_monitoring")]
    host._current_stat = "network"
    host._sync_history_notice()
    assert host._history_notice.kind is None and host._history_notice.widget.isHidden()
