"""
Unit tests for the design-system tokens: the Fluent type ramp (font() factory) and
the theme-resolved semantic colors. These lock the token contract the upcoming
primitives (SettingCard/SettingExpander/etc.) depend on.
"""
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils import styles as style_utils


def test_type_tokens_are_family_size_weight_tuples():
    for tok in (tokens.TYPE_CAPTION, tokens.TYPE_BODY, tokens.TYPE_BODY_STRONG,
                tokens.TYPE_SUBTITLE, tokens.TYPE_TITLE):
        family, px, weight = tok
        assert isinstance(family, str) and family
        assert isinstance(px, int) and px > 0
        assert isinstance(weight, int) and 1 <= weight <= 1000


def test_font_factory_applies_pixel_size_and_weight(qtbot):
    f = style_utils.font(tokens.TYPE_BODY_STRONG)
    assert f.pixelSize() == 14
    assert f.weight() == 600


def test_font_factory_caption_is_light_and_small(qtbot):
    f = style_utils.font(tokens.TYPE_CAPTION)
    assert f.pixelSize() == 12
    assert f.weight() == 400


def test_type_ramp_is_monotonic_in_size():
    sizes = [t[1] for t in (tokens.TYPE_CAPTION, tokens.TYPE_BODY,
                            tokens.TYPE_SUBTITLE, tokens.TYPE_TITLE)]
    assert sizes == sorted(sizes), "type ramp sizes should increase Caption -> Title"


def test_semantic_colors_has_all_keys():
    sc = style_utils.semantic_colors()
    for key in ("card_bg", "card_stroke", "subtle_fill",
                "text_primary", "text_secondary", "accent"):
        assert key in sc, f"missing semantic token: {key}"
        assert isinstance(sc[key], str) and sc[key]
