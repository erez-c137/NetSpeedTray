"""
Regression tests for issue #106 (taskbar text truncation).

The widget is sized once from get_reference_value_string(); the renderer then
draws live values and expands the number area to fit them. If the reserved
reference is narrower than a realistic value, the rendered text clips past the
widget edge. In "always_mbps" mode the value is shown in the mega unit, which at
multi-gig speeds is four integer digits (e.g. 1250 MB/s at 10GbE, ~1192 MiB/s),
so the reference MUST reserve >= 4 integer digits for every unit type — including
the bytes units, which previously only reserved 3 and clipped at ~8 Gbit/s.
"""
import pytest

from netspeedtray.utils.helpers import get_reference_value_string


def _int_part(ref: str) -> str:
    return ref.split(".")[0]


@pytest.mark.parametrize("unit_type", ["bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary"])
def test_always_mbps_reserves_four_integer_digits(unit_type):
    ref = get_reference_value_string(force_mega_unit=True, decimal_places=1, unit_type=unit_type)
    assert len(_int_part(ref)) >= 4, f"{unit_type}: reserved only {ref!r}"


@pytest.mark.parametrize("unit_type", ["bits_decimal", "bytes_decimal"])
def test_auto_mode_stays_compact_three_digits(unit_type):
    # Non-forced (auto) mode scales the unit (Gbps), so 3 digits is enough and we
    # don't want to widen the widget for everyone.
    ref = get_reference_value_string(force_mega_unit=False, decimal_places=1, unit_type=unit_type)
    assert len(_int_part(ref)) == 3


def test_min_digits_still_forces_width():
    ref = get_reference_value_string(False, 1, "bits_decimal", min_digits=4)
    assert _int_part(ref) == "8888"


def test_zero_decimal_places_has_no_dot():
    ref = get_reference_value_string(True, 0, "bytes_decimal")
    assert "." not in ref
    assert len(ref) >= 4
