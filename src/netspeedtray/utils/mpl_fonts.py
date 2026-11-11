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

# Windows system fonts, best-first per script. Every entry also covers Latin. Hebrew (he) is here too
# because DejaVu Sans lacks Hebrew glyphs (tofu); Segoe UI / Arial / David all ship with Windows.
_CJK_CANDIDATES: Dict[str, List[str]] = {
    "ja": ["Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic", "MS UI Gothic", "Microsoft YaHei"],
    "ko": ["Malgun Gothic", "Gulim", "Dotum", "Batang", "Microsoft YaHei"],
    "zh": ["Microsoft YaHei", "Microsoft JhengHei", "SimSun", "SimHei", "Malgun Gothic"],
    "he": ["Segoe UI", "Arial", "David", "Gisha", "Tahoma", "Miriam"],
    # Bengali: Windows' Bengali UI face (it has Latin and digits too). Used only where matplotlib
    # shapes complex text (matplotlib_shapes_text) - before that the graph draws English for Bengali.
    "bn": ["Nirmala UI", "Vrinda", "Shonar Bangla"],
}

# Right-to-left scripts need bidi reshaping before matplotlib renders them (matplotlib has no bidi).
_RTL_SCRIPTS = {"he", "ar", "fa"}

# Locales whose script needs complex text shaping, which matplotlib < 3.11 does not do: there the
# graph's own text (axis labels, legend, peak / boot captions, empty-state text, tick day/month names)
# falls back to en_US. A Bengali font has the glyphs, but unshaped, conjuncts come out as
# letter + hasant + letter and pre-base vowel signs (ি, ে, ো) land on the wrong side - worse than
# boxes, because it looks like text and reads as nonsense (#457). Everything Qt draws around the
# graph (tabs, buttons, the hover readout) stays in the UI language. matplotlib 3.11 lays text out
# with libraqm (HarfBuzz), which shapes Bengali: there these locales draw their own text, in their
# script's graph font (_CJK_CANDIDATES: Nirmala UI). See matplotlib_shapes_text.
MPL_UNSHAPED_LOCALES = frozenset({"bn_BD"})

_MPL_SHAPES: Optional[bool] = None


def matplotlib_shapes_text() -> bool:
    """True when matplotlib lays text out with complex shaping (3.11+: libraqm / HarfBuzz) - Bengali
    conjuncts and vowel signs come out as Qt draws them. Cached; only called where the graph (and so
    matplotlib) is already loaded."""
    global _MPL_SHAPES
    if _MPL_SHAPES is None:
        try:
            from matplotlib import ft2font
            _MPL_SHAPES = bool(getattr(ft2font, "__libraqm_version__", None))
        except Exception as e:  # pragma: no cover - defensive; assume the old, unshaped layout
            logger.debug("Could not inspect matplotlib's text layout: %s", e)
            _MPL_SHAPES = False
    return _MPL_SHAPES


_SCRIPT_FONT_FOUND: Dict[str, bool] = {}


def _script_font_installed(language: str) -> bool:
    """True when one of the graph font candidates for ``language``'s script is installed (cached per
    script). Without one, matplotlib would draw the script in DejaVu Sans - boxes."""
    script = str(language).lower().replace("-", "_").split("_")[0]
    if script not in _CJK_CANDIDATES:
        return True
    if script not in _SCRIPT_FONT_FOUND:
        try:
            import matplotlib.font_manager as fm
            installed = {f.name for f in fm.fontManager.ttflist}
            _SCRIPT_FONT_FOUND[script] = any(name in installed for name in _CJK_CANDIDATES[script])
        except Exception as e:  # pragma: no cover - defensive; English is the safe graph
            logger.debug("Could not enumerate installed fonts: %s", e)
            _SCRIPT_FONT_FOUND[script] = False
        if not _SCRIPT_FONT_FOUND[script]:
            logger.warning("No graph font for '%s' installed (expected one of %s): the graph draws "
                           "English.", language, _CJK_CANDIDATES[script])
    return _SCRIPT_FONT_FOUND[script]


def mpl_unshaped_locales() -> frozenset:
    """The locales whose graph text is English with THIS matplotlib: MPL_UNSHAPED_LOCALES before 3.11;
    once matplotlib shapes text, only those whose script has no graph font installed."""
    return frozenset(lang for lang in MPL_UNSHAPED_LOCALES if graph_text_is_english(lang))


def graph_text_is_english(language: Optional[str]) -> bool:
    """True when the graph's matplotlib text must be English for ``language`` (see above): matplotlib
    does not shape its script, or no font for the script is installed (Nirmala UI / Vrinda / Shonar
    Bangla for Bengali), where shaped text would still be boxes."""
    if not language or str(language) not in MPL_UNSHAPED_LOCALES:
        return False
    return not matplotlib_shapes_text() or not _script_font_installed(str(language))


_EN_I18N = None


def graph_i18n(i18n):
    """The strings object the matplotlib graph should draw with: ``i18n`` itself, or a shared en_US
    I18nStrings when the UI language is in MPL_UNSHAPED_LOCALES. The ONE place that decision is made -
    the graph renderer takes its strings from here, so no draw site needs its own check."""
    if not graph_text_is_english(getattr(i18n, "language", None)):
        return i18n
    global _EN_I18N
    if _EN_I18N is None:
        from netspeedtray.constants.i18n import I18nStrings
        _EN_I18N = I18nStrings("en_US")
    return _EN_I18N


def _script_for_language(language: Optional[str]) -> Optional[str]:
    """Map a locale code (e.g. ``ja_JP`` / ``he_IL``) to a font-script key, or ``None``."""
    if not language:
        return None
    lang = str(language).lower().replace("-", "_").split("_")[0]
    if lang in ("ja", "ko", "zh", "he"):
        return lang
    if lang == "bn" and matplotlib_shapes_text():
        return lang   # only where matplotlib shapes Bengali; else the graph draws English (DejaVu Sans)
    return None


def shape_rtl(text: str, language: Optional[str]) -> str:
    """Reshape RTL (e.g. Hebrew) text into visual order for matplotlib.

    matplotlib does no bidi, so it renders Hebrew in logical order = visually reversed. For RTL
    locales this returns the bidi *display* order (via python-bidi); non-RTL locales and empty text
    pass through unchanged. Safe to call on every label.
    """
    lang = str(language or "").lower().replace("-", "_").split("_")[0]
    if not text or lang not in _RTL_SCRIPTS:
        return text
    try:
        from bidi.algorithm import get_display
        return get_display(text)
    except Exception as e:  # pragma: no cover - defensive; degrade to unshaped text
        logger.debug("bidi reshape failed for %r: %s", text, e)
        return text


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
