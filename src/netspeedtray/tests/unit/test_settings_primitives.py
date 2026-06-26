"""
GUI smoke + behavior tests for the design-system primitives (pytest-qt, offscreen):
SettingCard, SettingExpander, Win11Segmented, ColorField.
"""
import pytest

from netspeedtray.utils.components import (
    SettingCard, SettingExpander, Win11Segmented, ColorField,
)
from PyQt6.QtWidgets import QLabel


# --- SettingCard -------------------------------------------------------------

def test_setting_card_constructs_with_control(qtbot):
    ctrl = QLabel("x")
    card = SettingCard("Title", "A description", control=ctrl)
    qtbot.addWidget(card)
    assert card.control() is ctrl


def test_setting_card_without_description_or_control(qtbot):
    card = SettingCard("Just a title")
    qtbot.addWidget(card)
    assert card.control() is None


# --- SettingExpander ---------------------------------------------------------

def test_expander_plain_toggles_expansion(qtbot):
    exp = SettingExpander("Group", expanded=False)
    qtbot.addWidget(exp)
    assert exp.isExpanded() is False
    exp.setExpanded(True)
    assert exp.isExpanded() is True


def test_expander_header_toggle_drives_state_and_signal(qtbot):
    exp = SettingExpander("Color-code by speed", header_toggle=True, initial_on=False)
    qtbot.addWidget(exp)
    assert exp.isChecked() is False
    assert exp.isExpanded() is False
    with qtbot.waitSignal(exp.toggled, timeout=500) as blocker:
        exp.setChecked(True)
    assert blocker.args == [True]
    assert exp.isChecked() is True
    assert exp.isExpanded() is True  # toggling on reveals the body


def test_expander_content_layout_accepts_children(qtbot):
    exp = SettingExpander("Group")
    qtbot.addWidget(exp)
    exp.contentLayout().addWidget(QLabel("child"))
    assert exp.contentLayout().count() == 1


# --- Win11Segmented ----------------------------------------------------------

def test_segmented_set_value_and_signal(qtbot):
    seg = Win11Segmented([("0", 0), ("1", 1), ("2", 2)])
    qtbot.addWidget(seg)
    with qtbot.waitSignal(seg.valueChanged, timeout=500) as blocker:
        seg.setValue(2)
    assert blocker.args == [2]
    assert seg.value() == 2


def test_segmented_no_signal_when_value_unchanged(qtbot):
    seg = Win11Segmented([("a", "a"), ("b", "b")])
    qtbot.addWidget(seg)
    seg.setValue("a")
    # Setting the same value again must not emit.
    fired = []
    seg.valueChanged.connect(lambda v: fired.append(v))
    seg.setValue("a")
    assert fired == []


# --- ColorField --------------------------------------------------------------

def test_color_field_normalizes_initial(qtbot):
    cf = ColorField("ff0000")
    qtbot.addWidget(cf)
    assert cf.color() == "#FF0000"


def test_color_field_set_color_emits_on_change(qtbot):
    cf = ColorField("#FF0000")
    qtbot.addWidget(cf)
    with qtbot.waitSignal(cf.colorChanged, timeout=500) as blocker:
        cf.setColor("#00FF00")
    assert blocker.args == ["#00FF00"]
    assert cf.color() == "#00FF00"


def test_color_field_rejects_invalid_hex(qtbot):
    cf = ColorField("#123456")
    qtbot.addWidget(cf)
    fired = []
    cf.colorChanged.connect(lambda v: fired.append(v))
    cf.setColor("not-a-color")
    assert cf.color() == "#123456"  # unchanged
    assert fired == []
