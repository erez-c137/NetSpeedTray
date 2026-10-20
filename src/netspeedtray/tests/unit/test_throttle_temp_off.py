"""
Settings > Hardware > "Throttle temp (stats)": 0 turns the feature off, so the slider must read "Off",
not "0°C" (2.2.0-beta.1 smoke test). Win11Slider grew QSpinBox's setSpecialValueText for it.
"""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.components import Win11Slider


@pytest.fixture(scope="module")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _shown(slider) -> str:
    return (slider._value_input or slider._value_label).text()


@pytest.mark.parametrize("editable", [True, False])
def test_special_value_text_replaces_the_minimum_only(q_app, editable):
    s = Win11Slider(editable=editable, suffix="°C")
    s.setRange(0, 130)
    s.setSpecialValueText("Off")
    assert _shown(s) == "Off"
    s.setValue(85)
    assert _shown(s) == "85°C" and s.value() == 85
    s.setValue(0)
    assert _shown(s) == "Off" and s.value() == 0


def test_without_special_text_the_number_is_shown(q_app):
    s = Win11Slider(editable=True, suffix="°C")
    s.setRange(0, 130)
    assert _shown(s) == "0°C"


def test_hardware_page_throttle_temp_reads_off_at_zero(q_app):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    page = HardwarePage(I18nStrings("en_US"), lambda: None)
    page.throttle_temp.setValue(90)
    page.throttle_temp.setValue(0)
    assert _shown(page.throttle_temp) == "Off"
    page.throttle_temp.setValue(90)
    assert _shown(page.throttle_temp) == "90°C"
