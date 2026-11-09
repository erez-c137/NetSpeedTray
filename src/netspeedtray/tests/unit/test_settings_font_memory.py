"""
Settings gives its memory back when it closes (memory plan 2.2 / 2.3, rc.7).

* The dialog is destroyed on close and rebuilt on open (it used to be hidden for reuse and kept ~26 MB),
  except while a history backup / delete it started is still running.
* Our own font picker replaces QFontDialog, which loaded the styles of every installed font (~38 MB):
  it asks for the styles of the SELECTED family only and draws only the rows it paints.
* After Settings or the Monitor is gone, Qt's font database is reset (utils/font_release) - deferred,
  and skipped while the other window is still open.

The suite paints offscreen, where Qt's font database is nearly empty: the picker is tested against a
fake family table, and the real-font checks run on the Windows platform in a subprocess.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import font_release
from netspeedtray.views.settings import font_picker as fp
from netspeedtray.views.settings.dialog import SettingsDialog
from netspeedtray.views.widget.main import NetworkSpeedWidget

SRC = Path(__file__).resolve().parents[3]
EN = I18nStrings("en_US")
DEFAULTS = constants.config.defaults.DEFAULT_CONFIG


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    yield
    font_release.cancel_pending_release()


def _drain():
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        QApplication.processEvents()


class _Host:
    """The widget's Settings plumbing (the real NetworkSpeedWidget methods) on a light stand-in."""
    _W = NetworkSpeedWidget
    show_settings = _W.show_settings
    _on_monitor_window_closed = _W._on_monitor_window_closed
    # getattr: the module must import (and the tests FAIL) on a build without these.
    _on_settings_dialog_finished = getattr(_W, "_on_settings_dialog_finished", lambda self, *a: None)
    _release_settings_dialog = getattr(_W, "_release_settings_dialog", lambda self: None)
    _font_heavy_window_open = getattr(_W, "_font_heavy_window_open", lambda self: False)
    _on_font_heavy_window_destroyed = getattr(_W, "_on_font_heavy_window_destroyed", lambda self, *a: None)

    def __init__(self):
        import logging
        self.logger = logging.getLogger("test.host")
        self.config = dict(DEFAULTS)
        self.config_manager = MagicMock()
        self.i18n = EN
        self.settings_dialog = None
        self.monitor_window = None
        self.metrics_endpoint = None
        self.widget_state = None
        self.applied = []

    def get_unified_interface_list(self):
        return ["Ethernet"]

    def is_startup_enabled(self):
        return False

    def _primary_interface_for_picker(self):
        return None

    def handle_settings_changed(self, cfg, save_to_disk=True):
        self.applied.append(save_to_disk)
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
def host(q_app, monkeypatch):
    # Never on screen: the window still gets show/hide/close like the real one.
    orig_init = SettingsDialog.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    monkeypatch.setattr(SettingsDialog, "__init__", init)
    h = _Host()
    yield h
    dlg = h.settings_dialog
    if dlg is not None and not sip.isdeleted(dlg):
        dlg.setParent(None)
        sip.delete(dlg)
    _drain()


# --- 2.3: destroyed on close, rebuilt on open -------------------------------------------------------

@pytest.mark.parametrize("how", ["x", "cancel", "save", "escape"])
def test_settings_dialog_is_destroyed_after_close(host, how):
    host.show_settings()
    dlg = host.settings_dialog
    assert dlg is not None and dlg.isVisible()
    if how == "x":
        dlg.close()
    elif how == "cancel":
        dlg.cancel_button.click()
    elif how == "save":
        dlg.save_button.click()
    else:
        from PyQt6.QtTest import QTest
        QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert host.settings_dialog is None, "the closed dialog is still referenced (hidden for reuse)"
    _drain()
    assert sip.isdeleted(dlg), "the closed dialog was not destroyed"
    # Save applied + saved; every other close reverted the preview (no disk write).
    assert host.applied and host.applied[-1] is (how == "save")


def test_reopen_builds_a_new_dialog_at_the_saved_position(host):
    host.show_settings()
    first = host.settings_dialog
    avail = (first.screen() or QApplication.primaryScreen()).availableGeometry()
    # The offscreen screen is narrower than the dialog, so x is clamped to the screen's left edge; y
    # moves off the centred default.
    target = QPoint(avail.left(), avail.top() + 23)
    assert target != first.pos()
    first.move(target)
    first.close()
    _drain()
    assert host.config["settings_window_pos"] == {"x": target.x(), "y": target.y()}
    host.show_settings()
    second = host.settings_dialog
    assert second is not None and second is not first and sip.isdeleted(first)
    assert second.isVisible()
    assert second.pos() == target


def test_open_twice_raises_the_same_dialog(host):
    host.show_settings()
    dlg = host.settings_dialog
    host.show_settings()
    assert host.settings_dialog is dlg and dlg.isVisible()


def test_a_dialog_with_a_running_backup_is_kept_until_it_is_done(host, monkeypatch):
    busy = [True]
    monkeypatch.setattr(SettingsDialog, "has_pending_work", lambda self: busy[0])
    monkeypatch.setattr(constants.timeouts, "SETTINGS_DESTROY_RETRY_MS", 10)
    host.show_settings()
    dlg = host.settings_dialog
    dlg.close()
    _drain()
    assert host.settings_dialog is dlg and not sip.isdeleted(dlg) and not dlg.isVisible()
    host.show_settings()                       # reopened while busy: the same dialog comes back
    assert host.settings_dialog is dlg and dlg.isVisible()
    dlg.close()
    busy[0] = False
    from pytestqt.qtbot import QtBot
    QtBot(QApplication.instance()).waitUntil(lambda: host.settings_dialog is None, timeout=2000)
    _drain()
    assert sip.isdeleted(dlg)


_DELETE_WHILE_CLOSED_PROBE = r"""
import os, sys, faulthandler
faulthandler.enable()
sys.path.insert(0, sys.argv[1])
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QTimer
app = QApplication([])
from test_settings_font_memory import _Host
from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker
h = _Host(); h.show_settings(); dlg = h.settings_dialog
dlg._delete_timeout.start()                      # a Delete all history is in flight
dlg._cancel_and_close()                          # the user closes Settings meanwhile: kept, hidden
print("kept", h.settings_dialog is dlg, flush=True)
QTimer.singleShot(200, lambda: dlg._on_history_deleted(sys.argv[2]))      # the worker answers
def close_boxes():
    for w in app.topLevelWidgets():
        if isinstance(w, QMessageBox):
            w.accept()
QTimer.singleShot(constants.timeouts.SETTINGS_DESTROY_RETRY_MS * 3, close_boxes)
QTimer.singleShot(constants.timeouts.SETTINGS_DESTROY_RETRY_MS * 4, app.quit)
app.exec()
print("survived", h.settings_dialog is None, flush=True)
"""


@pytest.mark.parametrize("status", ["done", "compact_pending", "backup_kept", "failed"])
def test_a_delete_answer_after_settings_closed_does_not_crash(status, tmp_path):
    """#490 review P0: the answer's QMessageBox was parented to the hidden dialog; the release retry
    fired inside the box's exec loop and destroyed the dialog under it (0xc0000374). Now the answer
    is a notice and the release never deletes from inside a nested loop."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8",
               APPDATA=str(tmp_path / "R"), LOCALAPPDATA=str(tmp_path / "L"))
    res = subprocess.run([sys.executable, "-c", _DELETE_WHILE_CLOSED_PROBE, str(Path(__file__).parent), status],
                         env=env, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, (res.returncode, res.stderr[-1500:])
    assert "survived True" in res.stdout, res.stdout


def test_release_waits_while_a_modal_loop_runs(host, monkeypatch):
    host.show_settings()
    dlg = host.settings_dialog
    dlg.hide()
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: dlg))
    host._release_settings_dialog()
    _drain()
    assert host.settings_dialog is dlg and not sip.isdeleted(dlg)
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: None))
    host._release_settings_dialog()
    _drain()
    assert host.settings_dialog is None and sip.isdeleted(dlg)


def test_a_visible_child_box_counts_as_pending_work(host):
    host.show_settings()
    dlg = host.settings_dialog
    box = QMessageBox(dlg)
    box.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    box.show()
    assert dlg.has_pending_work() is True
    box.hide()
    assert dlg.has_pending_work() is False


def test_no_release_is_scheduled_after_shutdown(monkeypatch):
    font_release.cancel_pending_release(shutdown=True)
    try:
        font_release.schedule_font_release(lambda: False, 10)
        assert not font_release.release_pending()
    finally:
        font_release._state["shut_down"] = False


def test_has_pending_work_sees_a_backup_and_a_delete(host):
    host.show_settings()
    dlg = host.settings_dialog
    assert dlg.has_pending_work() is False
    dlg._delete_timeout.start()
    assert dlg.has_pending_work() is True
    dlg._delete_timeout.stop()
    dlg.advanced_page.backup_running = lambda: True
    assert dlg.has_pending_work() is True


# --- lazy pages: only the page shown is built ----------------------------------------------------------

# Non-default values on every page (General, Widget, Appearance, Hardware, Network, Advanced).
_SPREAD = {"reduce_motion": True, "graph_opacity": 37, "font_size": 11, "monitor_cpu_enabled": True,
           "interface_mode": "selected", "selected_interfaces": ["Ethernet"], "keep_data": 365,
           "metrics_endpoint_port": 9555}


def _lazy_dialog(qt_owned, **extra):
    cfg = dict(DEFAULTS, **_SPREAD, **extra)
    host = _Host()
    host.config = dict(cfg)
    dlg = qt_owned(SettingsDialog(main_widget=host, config=cfg, version="x", i18n=EN,
                                  available_interfaces=["Ethernet", "Wi-Fi"]))
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    return dlg, host, cfg


def test_settings_opens_with_only_its_first_page_built(qt_owned):
    dlg, _, _ = _lazy_dialog(qt_owned)
    assert set(dlg._built_pages) == {"general"}
    assert [dlg.stack.widget(i).widget() is not None for i in range(dlg.stack.count())] == \
        [True, False, False, False, False, False]


def test_save_keeps_the_unbuilt_pages_settings(qt_owned):
    """Save with only General built: every other page's keys are the config's, never a fresh page's
    constructor defaults."""
    dlg, host, cfg = _lazy_dialog(qt_owned)
    saved = []
    host.handle_settings_changed = lambda c, save_to_disk=True: saved.append((dict(c), save_to_disk))
    dlg.save_button.click()
    assert saved and saved[-1][1] is True
    got = saved[-1][0]
    assert set(dlg._built_pages) == {"general"}
    for key in ("graph_opacity", "font_size", "monitor_cpu_enabled", "interface_mode",
                "selected_interfaces", "keep_data", "metrics_endpoint_port"):
        assert got[key] == cfg[key], key


def test_live_preview_with_unbuilt_pages_changes_nothing_else(qt_owned):
    dlg, _, cfg = _lazy_dialog(qt_owned)
    got = dlg.get_settings()
    # start_with_windows mirrors the Run key (General shows the live state), the position is the window's.
    changed = sorted(k for k in cfg if k not in ("settings_window_pos", "start_with_windows")
                     and got.get(k) != cfg[k])
    assert changed == [], changed


def test_a_page_is_built_and_loaded_on_first_visit(qt_owned):
    dlg, _, cfg = _lazy_dialog(qt_owned)
    dlg.navigate_to_page(dlg.PAGE_NETWORK)
    assert "interfaces" in dlg._built_pages
    assert dlg.stack.currentIndex() == dlg.PAGE_NETWORK
    assert dlg.stack.currentWidget().widget() is dlg._built_pages["interfaces"]
    got = dlg._built_pages["interfaces"].get_settings()
    assert got["interface_mode"] == "selected" and got["selected_interfaces"] == ["Ethernet"]
    dlg.navigate_to_page(dlg.PAGE_HARDWARE)
    assert dlg._built_pages["hardware"].monitor_cpu.isChecked() is True


def test_open_to_the_network_page(host):
    host.open_data_usage_settings = NetworkSpeedWidget.open_data_usage_settings.__get__(host)
    host.open_data_usage_settings()
    dlg = host.settings_dialog
    assert dlg.stack.currentIndex() == dlg.PAGE_NETWORK
    assert set(dlg._built_pages) == {"general", "interfaces"}
    assert dlg.sidebar.currentRow() == dlg.PAGE_NETWORK


def test_record_card_follows_the_live_hardware_toggles_when_built_later(qt_owned):
    """S6 with lazy pages: switch CPU monitoring off on Hardware, THEN open Advanced for the first time -
    its Record-hardware-history card must follow the live toggle, not the config it loads from."""
    dlg, _, _ = _lazy_dialog(qt_owned)   # config: monitor_cpu_enabled True
    dlg.navigate_to_page(dlg.PAGE_HARDWARE)
    dlg._built_pages["hardware"].monitor_cpu.setChecked(False)
    dlg.navigate_to_page(5)
    assert dlg._built_pages["advanced"]._record_card.isEnabled() is False


def test_reset_all_reaches_every_page(qt_owned, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg, _, _ = _lazy_dialog(qt_owned)
    dlg._reset_all_to_defaults()
    got = dlg.get_settings()
    for key in ("graph_opacity", "font_size", "monitor_cpu_enabled", "keep_data", "metrics_endpoint_port"):
        assert got[key] == DEFAULTS[key], key


# --- font-database release ---------------------------------------------------------------------------

def _count_releases(monkeypatch):
    calls = []
    monkeypatch.setattr(font_release, "release_font_database", lambda: calls.append(1) or True)
    monkeypatch.setattr(constants.timeouts, "FONT_RELEASE_DELAY_MS", 20)
    return calls


def test_font_release_runs_after_settings_is_destroyed(host, qtbot, monkeypatch):
    calls = _count_releases(monkeypatch)
    host.show_settings()
    host.settings_dialog.close()
    assert calls == []                      # deferred: never inside the close itself
    _drain()
    qtbot.waitUntil(lambda: calls == [1], timeout=2000)


def test_font_release_is_skipped_while_the_monitor_is_open(host, qtbot, monkeypatch):
    calls = _count_releases(monkeypatch)
    host.show_settings()
    host.monitor_window = object()          # the Monitor is open
    host.settings_dialog.close()
    _drain()
    qtbot.wait(150)
    assert calls == []


def test_font_release_is_skipped_while_settings_is_open_again(host, qtbot, monkeypatch):
    calls = _count_releases(monkeypatch)
    host.show_settings()
    host.settings_dialog.close()
    _drain()
    host.show_settings()                    # reopened before the deferred release ran
    qtbot.wait(150)
    assert calls == []


def test_font_release_runs_after_the_monitor_closes(host, qtbot, monkeypatch):
    from PyQt6.QtWidgets import QWidget
    calls = _count_releases(monkeypatch)
    win = QWidget()
    win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    host.monitor_window = win
    host._on_monitor_window_closed()
    assert host.monitor_window is None
    win.deleteLater()
    _drain()
    qtbot.waitUntil(lambda: calls == [1], timeout=2000)


def test_release_font_database_never_raises(monkeypatch):
    monkeypatch.setattr(QFontDatabase, "addApplicationFont", staticmethod(lambda p: (_ for _ in ()).throw(OSError("x"))))
    assert font_release.release_font_database() is False
    monkeypatch.setattr(QFontDatabase, "addApplicationFont", staticmethod(lambda p: -1))
    assert font_release.release_font_database() is False


def test_release_adds_and_removes_one_installed_font(monkeypatch):
    seen = []
    monkeypatch.setattr(QFontDatabase, "addApplicationFont", staticmethod(lambda p: seen.append(("add", p)) or 7))
    monkeypatch.setattr(QFontDatabase, "removeApplicationFont", staticmethod(lambda i: seen.append(("rm", i)) or True))
    assert font_release.release_font_database() is True
    assert seen[0][0] == "add" and os.path.isfile(seen[0][1]) and seen[1] == ("rm", 7)


# --- 2.2: our own font picker --------------------------------------------------------------------------

FAKE_FAMILIES = [f"Family {i:03d}" for i in range(400)] + ["Arial", "Segoe UI", "@MS Gothic"]


@pytest.fixture
def fake_fonts(monkeypatch):
    calls = {"styles": [], "writing": []}

    def styles(family):
        calls["styles"].append(family)
        return ["Regular", "Bold", "Light"]

    def weight(family, style):
        return {"Regular": 400, "Bold": 700, "Light": 300}[style]

    def writing(family):
        calls["writing"].append(family)
        return [QFontDatabase.WritingSystem.Latin]
    monkeypatch.setattr(QFontDatabase, "families", staticmethod(lambda *a: sorted(FAKE_FAMILIES)))
    monkeypatch.setattr(QFontDatabase, "styles", staticmethod(styles))
    monkeypatch.setattr(QFontDatabase, "weight", staticmethod(weight))
    monkeypatch.setattr(QFontDatabase, "writingSystems", staticmethod(writing))
    return calls


def _picker(qt_owned, family="Arial", size=9, weight=600, target="main"):
    dlg = qt_owned(fp.FontPickerDialog(EN, family, size, weight, preview_config=None, target=target))
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.resize(420, 480)
    dlg.show()
    dlg.list.grab()          # paint the visible rows, as the screen would
    return dlg


def test_picker_loads_styles_for_the_selected_family_only(qt_owned, fake_fonts):
    dlg = _picker(qt_owned)
    assert dlg.selected_family() == "Arial"
    assert set(fake_fonts["styles"]) == {"Arial"}
    dlg.list.setCurrentIndex(dlg._proxy.index(5, 0))
    dlg.list.setCurrentIndex(dlg._proxy.index(6, 0))
    assert set(fake_fonts["styles"]) == {"Arial", dlg._proxy.index(5, 0).data(), dlg._proxy.index(6, 0).data()}


def test_picker_draws_only_the_rows_it_paints(qt_owned, fake_fonts):
    dlg = _picker(qt_owned)
    painted = dlg.delegate.painted_families()
    assert 0 < len(painted) < 60, len(painted)          # a screenful, not 400 families
    assert set(fake_fonts["writing"]) <= set(painted)
    assert "@MS Gothic" not in dlg._model.stringList()


def test_picker_preselects_and_returns_family_weight_size(qt_owned, fake_fonts):
    dlg = _picker(qt_owned, family="segoe ui", size=10, weight=650)
    assert dlg.selected_family() == "Segoe UI"
    assert dlg.selected_weight() == 700                 # nearest the current weight
    assert dlg.selected_size() == 10
    dlg.size_spin.setValue(8)
    dlg.weight_combo.setCurrentIndex(0)
    assert (dlg.selected_size(), dlg.selected_weight()) == (8, 300)


def test_picker_filter_and_keyboard(qt_owned, fake_fonts):
    from PyQt6.QtTest import QTest
    dlg = _picker(qt_owned)
    QTest.keyClicks(dlg.search, "family 01")
    assert dlg._proxy.rowCount() == 10
    first = dlg.selected_family()
    assert first.startswith("Family 01")
    QTest.keyClick(dlg.search, Qt.Key.Key_Down)
    assert dlg.selected_family() != first and dlg.selected_family().startswith("Family 01")
    dlg.search.setText("no such font")
    assert dlg._proxy.rowCount() == 0 and not dlg.ok_button.isEnabled()


def test_arrow_picker_has_no_weight(qt_owned, fake_fonts):
    dlg = _picker(qt_owned, target="arrow", weight=None)
    assert dlg.selected_weight() is None and dlg.weight_combo.isHidden()
    assert fake_fonts["styles"] == []


def test_settings_uses_our_picker_not_qfontdialog(q_app, qt_owned, fake_fonts, monkeypatch):
    from PyQt6 import QtWidgets
    if hasattr(QtWidgets, "QFontDialog"):
        monkeypatch.setattr(QtWidgets.QFontDialog, "getFont",
                            staticmethod(lambda *a, **k: pytest.fail("QFontDialog loads every font's styles")))
    main = _Host()
    dlg = qt_owned(SettingsDialog(main_widget=main, config=dict(DEFAULTS), version="x", i18n=EN))

    def accept_with(self):
        self.list.setCurrentIndex(self._proxy.index(self._model.stringList().index("Family 007"), 0))
        self.size_spin.setValue(11)
        self.weight_combo.setCurrentIndex(self.weight_combo.findData(700))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(fp.FontPickerDialog, "exec", accept_with)
    dlg._open_font_dialog(dlg.appearance_page.current_font, "main")
    got = dlg.appearance_page.get_settings()
    assert (got["font_family"], got["font_size"], got["font_weight"]) == ("Family 007", 11, 700)
    dlg._open_font_dialog(dlg.appearance_page.current_arrow_font, "arrow")
    got = dlg.appearance_page.get_settings()
    assert (got["arrow_font_family"], got["arrow_font_size"]) == ("Family 007", 11)
    assert set(fake_fonts["styles"]) <= {"Family 007", "Segoe UI Variable", "Segoe UI", dlg.appearance_page.current_font.family()}


def test_picker_cancel_changes_nothing(q_app, qt_owned, fake_fonts, monkeypatch):
    main = _Host()
    dlg = qt_owned(SettingsDialog(main_widget=main, config=dict(DEFAULTS), version="x", i18n=EN))
    before = dlg.appearance_page.get_settings()
    monkeypatch.setattr(fp.FontPickerDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    dlg._open_font_dialog(dlg.appearance_page.current_font, "main")
    assert dlg.appearance_page.get_settings() == before


def test_no_qfontdialog_left_in_settings():
    root = SRC / "netspeedtray" / "views" / "settings"
    import re
    use = re.compile(r"QFontDialog(\.\w+)?\s*\(|import[^\n]*QFontDialog|QFontDialog,")
    hits = [p.name for p in root.rglob("*.py") if use.search(p.read_text(encoding="utf-8"))]
    assert hits == []


# --- real fonts, Windows platform -----------------------------------------------------------------------

_NATIVE_PROBE = r"""
import json, sys, psutil
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
app = QApplication(sys.argv[:1])
from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.font_release import release_font_database
from netspeedtray.views.widget.preview import PreviewWidget
uss = lambda: psutil.Process().memory_full_info().uss / 2**20
out = {}
cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
for lang in ("en_US", "ja_JP", "he_IL"):
    i18n = I18nStrings(lang)
    pv = PreviewWidget(cfg, i18n, width=300, height=40)
    pv.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    a = pv.grab().toImage(); w0 = pv.width()
    release_font_database()
    b = pv.grab().toImage(); w1 = pv.width()
    pv.set_config(cfg)
    c = pv.grab().toImage(); w2 = pv.width()
    out[lang] = [a == b, a == c, w0 == w1 == w2]
    pv.deleteLater()
app.processEvents()
release_font_database()
# The picker: styles() asked only for the selected family, and opening + painting it stays small.
asked = []
orig_styles = QFontDatabase.styles
QFontDatabase.styles = staticmethod(lambda f: asked.append(f) or orig_styles(f))
from netspeedtray.views.settings.font_picker import FontPickerDialog
# What Settings' own chrome costs is paid first (its QSS, the same kinds of controls), so the
# measurement below is the picker's own: the family list, the faces it draws, the preview.
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QListView, QLineEdit, QComboBox, QSpinBox, QPushButton, QLabel
from netspeedtray.utils import styles as su
base = QDialog(); base.setStyleSheet(su.dialog_style()); base.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
lay = QVBoxLayout(base)
for w in (QLineEdit("x"), QListView(), QComboBox(), QSpinBox(), QLabel("Font size")):
    lay.addWidget(w)
b = QPushButton("Select"); b.setStyleSheet(su.button_style(accent=True)); lay.addWidget(b)
base.show(); app.processEvents(); base.grab()
from netspeedtray.views.widget.preview import PreviewWidget as _PW
_pv = _PW(cfg, I18nStrings("en_US"), width=300, height=40); _pv.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen); _pv.grab()
app.processEvents()
u0 = uss()
dlg = FontPickerDialog(I18nStrings("en_US"), "Arial", 9, 400, preview_config=cfg)
dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
dlg.resize(420, 520); dlg.show(); app.processEvents()
dlg.list.grab()
for _ in range(3):
    dlg.list.verticalScrollBar().setValue(dlg.list.verticalScrollBar().value() + 400); dlg.list.grab()
out["picker_mb"] = round(uss() - u0, 1)
out["families"] = len(QFontDatabase.families())
out["asked"] = sorted(set(asked))
out["painted"] = len(dlg.delegate.painted_families())
print(json.dumps(out))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts")
def test_native_release_keeps_the_widget_pixel_identical_and_the_picker_lazy():
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _NATIVE_PROBE], env=env, capture_output=True, text=True,
                         timeout=600)
    assert res.returncode == 0, res.stderr[-2000:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    for lang in ("en_US", "ja_JP", "he_IL"):
        assert out[lang] == [True, True, True], (lang, out[lang])
    assert out["asked"] == ["Arial"], out["asked"]
    assert out["painted"] < out["families"] / 2
    # QFontDialog (every family's styles) cost ~38 MB here; the lazy picker a few.
    assert out["picker_mb"] < 20, out
