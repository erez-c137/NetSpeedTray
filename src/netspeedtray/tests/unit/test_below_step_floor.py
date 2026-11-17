"""
"Always show megabytes" with light traffic (#556): a non-zero rate that rounds to zero at the chosen precision
reads "<0.1" (the smallest step), never a false-looking "0.0". True zero stays "0.0", auto mode never gets the
floor, no extra digits are added (#303), and the floor never needs more width than the widget reserved.
"""
import pytest
from PyQt6.QtGui import QFont, QFontMetrics

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.helpers import format_speed, get_reference_value_string

UNIT_TYPES = ("bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary")
FLOORS = {0: "<1", 1: "<0.1", 2: "<0.01"}


@pytest.fixture(scope="module")
def en():
    return I18nStrings("en_US")


@pytest.fixture(scope="module")
def de():
    return I18nStrings("de_DE")


@pytest.mark.parametrize("unit_type", UNIT_TYPES)
@pytest.mark.parametrize("decimal_places", [0, 1, 2])
@pytest.mark.parametrize("speed", [0.01, 1, 3, 40, 120])   # bytes/s: well under 0.01 of any mega unit
def test_tiny_traffic_reads_below_the_smallest_step(en, unit_type, decimal_places, speed):
    val, _ = format_speed(speed, en, force_mega_unit=True, unit_type=unit_type,
                          decimal_places=decimal_places, split_unit=True)
    assert val == FLOORS[decimal_places]


@pytest.mark.parametrize("unit_type", UNIT_TYPES)
@pytest.mark.parametrize("decimal_places", [0, 1, 2])
def test_true_zero_still_reads_zero(en, unit_type, decimal_places):
    val, _ = format_speed(0, en, force_mega_unit=True, unit_type=unit_type,
                          decimal_places=decimal_places, split_unit=True)
    assert val == ("0" if decimal_places == 0 else "0." + "0" * decimal_places)


@pytest.mark.parametrize("speed", [float("nan"), float("inf"), -5.0])
def test_non_finite_or_negative_is_zero_not_floor(en, speed):
    val, _ = format_speed(speed, en, force_mega_unit=True, split_unit=True)
    assert val == "0.0"


@pytest.mark.parametrize("unit_type", UNIT_TYPES)
@pytest.mark.parametrize("decimal_places", [0, 1, 2])
@pytest.mark.parametrize("speed", [0.01, 0.4, 1, 3, 40, 999, 1_000, 3_000, 40_000, 124_999])
def test_auto_mode_never_shows_the_floor(en, unit_type, decimal_places, speed):
    val, _ = format_speed(speed, en, unit_type=unit_type, decimal_places=decimal_places, split_unit=True)
    assert constants.network.units.BELOW_STEP_PREFIX not in val


def test_values_that_show_a_digit_keep_it(en):
    # 0.024 Mbps at 2 decimals is a real "0.02", and 0.064 at 1 decimal a real "0.1": no floor there.
    assert format_speed(3_000, en, force_mega_unit=True, decimal_places=2, split_unit=True)[0] == "0.02"
    assert format_speed(8_000, en, force_mega_unit=True, decimal_places=1, split_unit=True)[0] == "0.1"


def test_floor_takes_the_locale_separator(de):
    assert format_speed(3, de, force_mega_unit=True, decimal_places=1, split_unit=True)[0] == "<0,1"
    assert format_speed(3, de, force_mega_unit=True, decimal_places=2, split_unit=True)[0] == "<0,01"


@pytest.mark.parametrize("decimal_places", [0, 1, 2])
@pytest.mark.parametrize("sep", [".", ","])
def test_floor_fits_the_reserved_width_in_the_widget_font(q_app, decimal_places, sep):
    # The widget reserves its number column from the reference string once; the floor must never need more.
    # Checked against the AUTO reference ("888.8"), the narrower of the two, so it holds in either mode.
    fm = QFontMetrics(QFont(constants.config.defaults.DEFAULT_FONT_FAMILY, constants.config.defaults.DEFAULT_FONT_SIZE))
    floor = FLOORS[decimal_places].replace(".", sep)
    ref = get_reference_value_string(False, decimal_places).replace(".", sep)
    assert fm.horizontalAdvance(floor) <= fm.horizontalAdvance(ref), (floor, ref)
