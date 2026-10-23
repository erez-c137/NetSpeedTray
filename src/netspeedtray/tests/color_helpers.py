"""Test helpers for the 2.2 rc.2 colour scheme (each themed colour is a <stem>_dark/<stem>_light pair)."""
from typing import Any, Dict

from netspeedtray import constants


def themed(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """``cfg`` with every themed colour given by its STEM (``default_color="#123456"``) expanded to the
    same value on both taskbars, so a test paints that colour whatever theme the suite is pinned to
    (NST_TEST_WINDOWS_THEME). The stem key itself is dropped - it is inert since 1.3."""
    out = dict(cfg)
    for stem in constants.config.defaults.THEMED_COLOR_STEMS:
        if stem in out:
            value = out.pop(stem)
            out[f"{stem}_dark"] = value
            out[f"{stem}_light"] = value
    return out
