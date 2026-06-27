"""Custom arrow glyph override (#129) — empty falls back to the native i18n arrow."""
from netspeedtray.utils.widget_renderer import RenderConfig


def test_custom_arrows_pass_through_render_config():
    rc = RenderConfig.from_dict({"arrow_up_symbol": "▲", "arrow_down_symbol": "▼"})
    assert rc.arrow_up_symbol == "▲"
    assert rc.arrow_down_symbol == "▼"


def test_default_arrows_empty_so_renderer_uses_native_default():
    rc = RenderConfig.from_dict({})
    assert rc.arrow_up_symbol == ""
    assert rc.arrow_down_symbol == ""
