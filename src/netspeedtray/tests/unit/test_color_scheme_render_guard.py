"""
The widget paints like its reviewed baseline (design-color-scheme.md 8.5; re-based for the legibility
package "L").

144 scenarios - three layouts, four graph modes, color coding off / on, the factory config and two
migrated rc.1 configs, dark and light taskbar - hashed against ``color_scheme_render_golden.json``,
which color_scheme_render_guard.py wrote from the L branch after its visual review. See that module.
"""
import json
from pathlib import Path

import pytest

from netspeedtray.tests.unit import color_scheme_render_guard as guard

GOLDEN = json.loads((Path(__file__).with_name("color_scheme_render_golden.json")).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def same_fonts(q_app):
    if guard.calibration() != GOLDEN["calibration"]:
        pytest.skip("this platform renders text differently from the machine that wrote the golden "
                    "(fonts / rasterizer) - regenerate it to compare here")


def test_the_golden_is_the_legibility_baseline():
    assert GOLDEN["baseline"] == guard.BASELINE
    assert sorted(GOLDEN["scenarios"]) == sorted(guard.scenario_names())
    assert all("expected_png" in v for k, v in GOLDEN["scenarios"].items() if guard.uses_tolerance(k))


def test_the_tolerance_is_far_below_a_real_change(q_app, same_fonts):
    """The per-pixel tolerance can't hide a colour change: the light and dark Detailed frames of the same
    factory scenario differ by far more than it."""
    light = guard.render_image("bar48/detailed/coding_on/factory/light")
    assert guard.max_pixel_difference(light, GOLDEN["scenarios"]["bar48/detailed/coding_on/factory/dark"]
                                      ["expected_png"]) > 10 * guard.TOLERANCE


@pytest.mark.parametrize("name", guard.scenario_names())
def test_paints_like_the_reviewed_baseline(q_app, same_fonts, name):
    golden = GOLDEN["scenarios"][name]
    img = guard.render_image(name)
    if guard._hash(img) == golden["expected"]:
        return
    assert guard.uses_tolerance(name), f"{name}: the paint changed (render it with --png-dir to compare)"
    diff = guard.max_pixel_difference(img, golden["expected_png"])
    assert diff <= guard.TOLERANCE, f"{name}: the paint changed - max {diff} levels per channel"
