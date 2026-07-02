"""
Locale-aware CJK font selection for the matplotlib graph (#161).

The graph's default font (DejaVu Sans, bundled with matplotlib) has no CJK
glyphs, so the axis / peak / series labels render as tofu boxes (the empty
rectangle) under Japanese, Korean, and Chinese locales. This module picks a
CJK-capable font that already ships with Windows - a *system* font, so nothing
is bundled and the installer stays slim - and points matplotlib at it for those
locales only. Non-CJK locales are left completely untouched (DejaVu Sans as
before).

The picked fonts all cover Latin + digits too, so mixed labels (e.g. a
localized word next to a number) render in one consistent font rather than
mixing two.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger("NetSpeedTray.MplFonts")

# Windows system fonts, best-first per script. Every entry also covers Latin.
_CJK_CANDIDATES: Dict[str, List[str]] = {
    "ja": ["Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic", "MS UI Gothic", "Microsoft YaHei"],
    "ko": ["Malgun Gothic", "Gulim", "Dotum", "Batang", "Microsoft YaHei"],
    "zh": ["Microsoft YaHei", "Microsoft JhengHei", "SimSun", "SimHei", "Malgun Gothic"],
}


def _script_for_language(language: Optional[str]) -> Optional[str]:
    """Map a locale code (e.g. ``ja_JP``) to a CJK script key, or ``None``."""
    if not language:
        return None
    lang = str(language).lower().replace("-", "_").split("_")[0]
    if lang == "ja":
        return "ja"
    if lang == "ko":
        return "ko"
    if lang == "zh":
        return "zh"
    return None


def pick_cjk_font(language: Optional[str]) -> Optional[str]:
    """
    Return the name of the first *installed* CJK font suitable for ``language``,
    or ``None`` if the locale is not CJK or no candidate font is installed.

    Pure function - no matplotlib state is mutated - so it is cheap and safe to
    unit-test.
    """
    script = _script_for_language(language)
    if script is None:
        return None
    try:
        import matplotlib.font_manager as fm
        installed = {f.name for f in fm.fontManager.ttflist}
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Could not enumerate installed fonts: %s", e)
        return None
    for name in _CJK_CANDIDATES[script]:
        if name in installed:
            return name
    return None


def configure_cjk_font(language: Optional[str]) -> Optional[str]:
    """
    If ``language`` is a CJK locale, point matplotlib's default sans-serif at an
    installed CJK system font (prepended; DejaVu Sans kept as the Latin fallback)
    and disable the fancy unicode minus (U+2212), which some CJK fonts lack.

    Idempotent and safe to call on every graph render. Returns the chosen font
    name, or ``None`` when nothing needed to change (non-CJK locale, or no CJK
    font installed - in which case the graph renders exactly as before).
    """
    font = pick_cjk_font(language)
    if font is None:
        script = _script_for_language(language)
        if script is not None:
            logger.warning(
                "No CJK-capable system font found for locale '%s'; graph labels "
                "may show as boxes. Expected one of: %s",
                language, _CJK_CANDIDATES[script],
            )
        return None
    try:
        import matplotlib as mpl
        sans = [f for f in mpl.rcParams.get("font.sans-serif", []) if f != font]
        mpl.rcParams["font.sans-serif"] = [font] + sans
        mpl.rcParams["font.family"] = "sans-serif"
        mpl.rcParams["axes.unicode_minus"] = False  # some CJK fonts lack U+2212
        logger.info("Graph font set to '%s' for CJK locale '%s'.", font, language)
    except Exception as e:  # pragma: no cover - defensive
        logger.error("Failed to set CJK graph font: %s", e)
        return None
    return font
