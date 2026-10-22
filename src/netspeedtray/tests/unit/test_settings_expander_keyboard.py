"""
rc.2 S5 - every Settings expander opens from the keyboard and tells a screen reader its state.

An expander without a header toggle ("Advertised plan speed", "Color-code by load") used to open only
on a mouse press: its header was a plain QFrame with a patched mousePressEvent - no Tab stop, no key
handling, no name. The header is now a button: Tab reaches it, Space / Enter open and close it, and
its accessible name carries the state ("Advertised plan speed, collapsed"). Expanders WITH a header
toggle are opened by that toggle, which is already a named Tab stop.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.components import SettingExpander
from netspeedtray.views.settings.dialog import SettingsDialog


def _focusable(w) -> bool:
    return bool(w.focusPolicy().value & Qt.FocusPolicy.TabFocus.value)


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_header_opens_and_closes_from_the_keyboard(qtbot, key):
    exp = SettingExpander("Advertised plan speed", "Flags time spent below your plan", expanded=False)
    qtbot.addWidget(exp)
    header = exp._header
    assert _focusable(header), "the header is not a Tab stop"
    qtbot.keyClick(header, key)
    assert exp.isExpanded()
    qtbot.keyClick(header, key)
    assert not exp.isExpanded()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_holding_enter_never_reaches_the_dialogs_default_button(qtbot, key):
    """Holding Enter sends one press plus auto-repeats. The header handled only the first; the repeats
    went up to the dialog, which clicked its default Save button - saving and closing Settings."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout
    dlg = QDialog()
    qtbot.addWidget(dlg)
    lay = QVBoxLayout(dlg)
    exp = SettingExpander("Advertised plan speed", expanded=False)
    lay.addWidget(exp)
    save = QPushButton("Save")
    save.setDefault(True)
    lay.addWidget(save)
    saves = []
    save.clicked.connect(lambda: saves.append(1))
    dlg.accepted.connect(lambda: saves.append("accepted"))
    save.clicked.connect(dlg.accept)
    dlg.show()                      # QDialog only presses a VISIBLE default button
    qtbot.waitExposed(dlg)
    for repeat in (False, True, True, True):
        ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "\r", repeat)
        QApplication.sendEvent(exp._header, ev)
    QApplication.sendEvent(exp._header, QKeyEvent(QEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier))
    qtbot.wait(400)                 # the dialog's press is an animateClick (fires on a timer)
    assert exp.isExpanded(), "the first press must still toggle"
    assert saves == [], f"the dialog's default button fired: {saves}"


def test_header_names_its_title_and_state(qtbot):
    exp = SettingExpander("Advertised plan speed", "Flags time spent below your plan", expanded=False)
    qtbot.addWidget(exp)
    header = exp._header
    assert header.accessibleName() == "Advertised plan speed, collapsed"
    assert header.accessibleDescription() == "Flags time spent below your plan"
    exp.setExpanded(True)
    assert header.accessibleName() == "Advertised plan speed, expanded"
    exp.set_description("Off")
    assert header.accessibleDescription() == "Off"


def test_header_state_is_localized(qtbot):
    i18n = I18nStrings("de_DE")
    exp = SettingExpander("Tarifgeschwindigkeit", expanded=True, i18n=i18n)
    qtbot.addWidget(exp)
    assert exp._header.accessibleName() == i18n.EXPANDER_A11Y_EXPANDED.format(title="Tarifgeschwindigkeit")


def test_mouse_click_still_toggles(qtbot):
    exp = SettingExpander("Color-code by load", expanded=False)
    qtbot.addWidget(exp)
    exp._header.resize(300, 58)
    qtbot.mouseClick(exp._header, Qt.MouseButton.LeftButton)
    assert exp.isExpanded()


def test_a_header_toggle_stays_the_only_tab_stop(qtbot):
    exp = SettingExpander("Data usage", "Warn me as I approach a monthly limit",
                          header_toggle=True, initial_on=False)
    qtbot.addWidget(exp)
    assert not _focusable(exp._header)          # no second, redundant stop beside the toggle
    assert _focusable(exp._toggle.checkbox)
    exp._header.resize(300, 58)
    qtbot.mouseClick(exp._header, Qt.MouseButton.LeftButton, pos=exp._header.rect().topLeft())
    assert exp.isChecked() and exp.isExpanded()   # a click on the header row still flips the toggle


def test_every_settings_expander_is_keyboard_operable(qtbot, monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    m = MagicMock()
    m.config = {}
    m.config_manager = MagicMock()
    dlg = SettingsDialog(main_widget=m, config=cfg, version="0", i18n=I18nStrings("en_US"),
                         available_interfaces=["Ethernet", "Wi-Fi"], is_startup_enabled=False,
                         primary_interface="Wi-Fi")
    qtbot.addWidget(dlg)
    expanders = [e for i in range(dlg.stack.count()) for e in dlg.stack.widget(i).widget().findChildren(SettingExpander)]
    assert len(expanders) >= 5
    plain = [e for e in expanders if not e._has_toggle]
    assert plain, "expected at least one expander without a header toggle"
    for e in expanders:
        stop = e._toggle.checkbox if e._has_toggle else e._header
        assert _focusable(stop), f"{e._title!r} cannot be reached from the keyboard"
    for e in plain:
        before = e.isExpanded()
        qtbot.keyClick(e._header, Qt.Key.Key_Space)
        assert e.isExpanded() != before, f"{e._title!r} did not open from the keyboard"
        assert e._header.accessibleName().startswith(e._title)
