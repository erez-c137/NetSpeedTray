"""
Every keyboard-focusable control in Settings must carry an accessible name.

A setting's title is a separate QLabel beside the control, so a screen reader that tabbed onto a
SettingCard's toggle, slider, spin box or URL field announced only "check box" / "slider" / "edit".
SettingCard and SettingExpander now name their inner controls from the title (and describe them with
the subtitle); the few controls outside a card name themselves.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractButton, QMessageBox, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.components import SettingCard, SettingExpander, Win11Slider, Win11Toggle
from netspeedtray.views.settings.dialog import SettingsDialog


def _name(w: QWidget) -> str:
    # A button with visible text is named by that text (Qt's accessible interface reads it).
    return w.accessibleName() or (w.text() if isinstance(w, QAbstractButton) else "")


def _focusable(w: QWidget) -> bool:
    return bool(w.focusPolicy().value & Qt.FocusPolicy.TabFocus.value)


@pytest.fixture
def dialog(qtbot, monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(metrics_endpoint_enabled=True, interface_mode="selected")
    m = MagicMock()
    m.config = {}
    m.config_manager = MagicMock()
    dlg = SettingsDialog(main_widget=m, config=cfg, version="0", i18n=I18nStrings("en_US"),
                         available_interfaces=["Ethernet", "Wi-Fi"], is_startup_enabled=False,
                         primary_interface="Wi-Fi")
    qtbot.addWidget(dlg)
    return dlg


def test_every_focusable_settings_control_is_named(dialog):
    unnamed = []
    for i in range(dialog.stack.count()):
        page = dialog.stack.widget(i).widget()
        for w in page.findChildren(QWidget):
            # A combo box's popup list lives in its own top-level window and is named by its items.
            if not _focusable(w) or w.window() is not dialog:
                continue
            if not _name(w).strip():
                unnamed.append(f"page {i} ({type(page).__name__}): {type(w).__name__} "
                               f"in {type(w.parentWidget()).__name__}")
    assert not unnamed, "Settings controls with no accessible name:\n" + "\n".join(unnamed)


def test_setting_card_names_its_control_from_the_title(q_app):
    toggle = Win11Toggle()
    card = SettingCard("Record hardware history", "Keeps CPU and GPU history", control=toggle)
    assert toggle.checkbox.accessibleName() == "Record hardware history"
    assert toggle.checkbox.accessibleDescription() == "Keeps CPU and GPU history"
    card.set_description("Port 9184 is already in use")     # a live status keeps the description true
    assert toggle.checkbox.accessibleDescription() == "Port 9184 is already in use"


def test_setting_card_names_both_parts_of_a_slider_and_drops_the_colon(q_app):
    slider = Win11Slider(editable=True)
    card = SettingCard("Update Interval:", control=slider)   # noqa: F841 - keeps the control alive
    assert slider.slider.accessibleName() == "Update Interval"
    assert slider._value_input.accessibleName() == "Update Interval"


def test_setting_card_keeps_a_button_named_by_its_own_text(q_app):
    from PyQt6.QtWidgets import QLineEdit, QPushButton
    from netspeedtray.utils.components import ReflowRow
    url, copy = QLineEdit(), QPushButton("Copy")
    card = SettingCard("Address", control=ReflowRow([url, copy]))   # noqa: F841 - keeps the row alive
    assert url.accessibleName() == "Address"
    assert copy.accessibleName() == "" and copy.text() == "Copy"


def test_expander_header_toggle_is_named(q_app):
    exp = SettingExpander("Connection latency", "Ping + packet loss to your router", header_toggle=True)
    assert exp._toggle.checkbox.accessibleName() == "Connection latency"
    exp.set_description("Ping + packet loss to the internet test server")
    assert exp._toggle.checkbox.accessibleDescription() == "Ping + packet loss to the internet test server"


def test_interface_row_names_the_focusable_toggle(dialog):
    page = dialog.interfaces_page
    toggle = page.interface_checkboxes["Wi-Fi"]
    name = toggle.checkbox.accessibleName()
    assert name.startswith("Wi-Fi") and dialog.i18n.INTERFACE_ACTIVE_CONNECTION in name
    assert page.interface_checkboxes["Ethernet"].checkbox.accessibleName() == "Ethernet"
