"""
The colour scheme paints byte-identically to the code before it (design-color-scheme.md 8.5).

144 scenarios - three layouts, four graph modes, color coding off / on, the factory config and two
migrated rc.1 configs, dark and light taskbar - hashed against ``color_scheme_render_golden.json``,
which color_scheme_render_guard.py wrote from main @ 02a86ff0 (the tree before the scheme). See that
module for what ``literal`` and ``expected`` mean.
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
                    "(fonts / rasterizer) - regenerate it on the old tree to compare here")


def test_the_golden_came_from_the_tree_before_the_scheme():
    assert GOLDEN["has_scheme"] is False
    assert sorted(GOLDEN["scenarios"]) == sorted(guard.scenario_names())


@pytest.mark.parametrize("name", guard.scenario_names())
def test_paints_like_the_old_code(q_app, same_fonts, name):
    assert guard.render(name) == GOLDEN["scenarios"][name]["expected"], (
        f"{name}: the paint changed (render both trees with --png-dir to compare)")


def test_the_dark_widget_and_every_chosen_colour_are_literally_unchanged():
    """``expected`` differs from ``literal`` only where a tuned light-taskbar default is on screen: the
    graph, the factory High / Low (color coding on) or the factory arrows (custom arrow colours on)."""
    for name, h in GOLDEN["scenarios"].items():
        layout, graph, coding, kind, theme = name.split("/")
        no_tuned_colour = theme == "dark" or (graph == "graph_off" and (
            kind == "rc1_custom" or (kind == "factory" and coding == "coding_off")))
        if no_tuned_colour:
            assert h["literal"] == h["expected"], name
        elif graph != "graph_off":
            assert h["literal"] != h["expected"], f"{name}: the tuned graph colours did not show"
