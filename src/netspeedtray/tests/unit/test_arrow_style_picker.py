"""
ArrowStylePicker (#129) round-trip: presets ↔ config, Custom, and Classic-as-native-default.
"""
import pytest

from netspeedtray.utils.components import ArrowStylePicker
from netspeedtray.constants.arrows import ARROW_PRESETS


@pytest.fixture
def picker(q_app):
    return ArrowStylePicker()


def test_default_empty_is_classic_and_round_trips_empty(picker):
    picker.set_values("", "")
    vals = picker.get_values()
    assert vals == {"arrow_up_symbol": "", "arrow_down_symbol": ""}


def test_explicit_classic_glyphs_normalize_to_empty(picker):
    # ↑/↓ is the Classic preset; it must store empty so it tracks the OS locale arrow.
    picker.set_values("↑", "↓")
    assert picker.get_values() == {"arrow_up_symbol": "", "arrow_down_symbol": ""}


@pytest.mark.parametrize("name,up,down", [p for p in ARROW_PRESETS if p[0] != "Classic"])
def test_non_classic_presets_round_trip(picker, name, up, down):
    picker.set_values(up, down)
    assert picker.get_values() == {"arrow_up_symbol": up, "arrow_down_symbol": down}


def test_custom_glyphs_round_trip(picker):
    picker.set_values("»", "«")  # not a preset → Custom
    vals = picker.get_values()
    assert vals == {"arrow_up_symbol": "»", "arrow_down_symbol": "«"}


def test_changed_signal_fires_on_segment_change(picker, qtbot=None):
    fired = []
    picker.changed.connect(lambda: fired.append(1))
    picker._seg.setValue("Solid")
    assert fired, "changing the segment should emit changed"
