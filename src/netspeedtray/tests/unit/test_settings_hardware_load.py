"""Hardware page "Color-code by load" thresholds (4 formerly-orphan keys) round-trip."""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.pages.hardware import HardwarePage


@pytest.fixture
def page(q_app):
    return HardwarePage(I18nStrings("en_US"), lambda: None)


def test_load_thresholds_round_trip(page):
    page.load_settings({
        "cpu_load_high_threshold": 90, "cpu_load_low_threshold": 40,
        "gpu_load_high_threshold": 85, "gpu_load_low_threshold": 35,
    })
    g = page.get_settings()
    assert g["cpu_load_high_threshold"] == 90.0
    assert g["cpu_load_low_threshold"] == 40.0
    assert g["gpu_load_high_threshold"] == 85.0
    assert g["gpu_load_low_threshold"] == 35.0


def test_load_thresholds_default_when_unset(page):
    page.load_settings({})
    g = page.get_settings()
    from netspeedtray import constants
    d = constants.config.defaults
    assert g["cpu_load_high_threshold"] == float(d.DEFAULT_CPU_LOAD_HIGH_THRESHOLD)
    assert g["gpu_load_low_threshold"] == float(d.DEFAULT_GPU_LOAD_LOW_THRESHOLD)
