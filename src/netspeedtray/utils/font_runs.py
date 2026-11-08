"""
Glyph runs for the taskbar widget's text: every string is drawn in the widget's ONE font, and only the
grapheme clusters that face lacks are drawn in a named face that has them.

Why: a QFont that lists more than one family (``setFamilies``, a QSS family list, a substitute) makes
Qt resolve its whole fallback chain on its first measurement, and so does a glyph the face lacks.
Resolving that chain loads the styles of EVERY installed font family: ~38 MB on a typical PC (323
families), process-wide and never freed. So the widget never lets Qt look for a glyph:

- The text is split into grapheme clusters (QTextBoundaryFinder): a Bengali conjunct "র‍্য", an emoji
  ZWJ family "👨‍👩‍👧", a keycap "1️⃣" each stay whole, and each cluster gets ONE face that has every
  code point of it (ZWJ/ZWNJ and variation selectors are default-ignorable: no face needs them).
- A cluster the widget's face has stays in it - digits, separators, arrows, Latin - so the look and the
  width of the readout are unchanged. Others take the first installed face of a list (CANDIDATE order
  below): Segoe UI first (Hebrew, #194), the UI language's own faces, the other script faces, Windows'
  per-script UI faces. Emoji (VS16, keycaps, emoji-presentation) try Segoe UI Emoji first, symbols (the
  ▴ △ ▵ ⇑ arrow presets) MS UI Gothic - the faces Qt's fallback drew them in until 2.2.0.
- A cluster NO installed face has is drawn in the widget's face with font merging OFF: a missing-glyph
  box, never the 38 MB search.
- Left-to-right text is drawn run by run on one baseline (each run itemized on its own, as Latin a "("
  before Bengali stays Latin). Text with right-to-left letters is laid out by ONE QTextLayout with a
  font per run, so bidi order (a Hebrew Wi-Fi name with an emoji or a Latin word) stays Qt's; its
  neutral runs ("(", digits) join the neighbouring script's face, which Qt itemizes them with.

Each face's coverage is asked of that face alone (a no-merging QRawFont): QFontMetrics.inFont() on a
merging font, or resolving a family name Qt doesn't know, would run the very search this avoids.
"""
import ctypes
import unicodedata
from ctypes import wintypes
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QPointF, QRectF, QTextBoundaryFinder
from PyQt6.QtGui import (QFont, QFontDatabase, QFontMetrics, QFontMetricsF, QTextCharFormat, QTextLayout,
                         QTextOption)
from PyQt6.QtCore import Qt

from netspeedtray import constants

_listed: Optional[Dict[str, str]] = None           # lower-cased name -> the name Qt lists
_registry: Optional[Set[str]] = None               # lower-cased English face names Windows registers
_resolved: Dict[str, Optional[str]] = {}            # requested name -> name to use (None: not installed)
_raw_fonts: Dict[str, object] = {}
_run_cache: Dict[Tuple[str, str, str], tuple] = {}
_font_cache: Dict[Tuple[str, str], QFont] = {}
_language = ""
NO_FACE = ""     # a run no installed face has: the widget's face, font merging off (a box, not a search)


def set_text_language(language: Optional[str]) -> None:
    """The UI language, which puts its own script face first (Japanese and Chinese share Han glyphs)."""
    global _language
    language = str(language or "")
    if language != _language:
        _language = language
        _run_cache.clear()


# --------------------------------------------------------------------------- which faces are installed

def _listed_families() -> Dict[str, str]:
    global _listed
    if _listed is None:
        try:
            _listed = {f.lower(): f for f in QFontDatabase.families()}   # names only: no styles loaded
        except Exception:
            _listed = {}
    return _listed


def _registry_faces() -> Set[str]:
    """English face names of the installed fonts, from the registry - the same on every UI language.
    Value names read "Malgun Gothic Bold (TrueType)" or "MS Gothic & MS UI Gothic & MS PGothic
    (TrueType)"; a face name is matched by prefix, so its style words don't matter."""
    global _registry
    if _registry is None:
        names: Set[str] = set()
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
                except OSError:
                    continue
                with key:
                    i = 0
                    while True:
                        try:
                            value = winreg.EnumValue(key, i)[0]
                        except OSError:
                            break
                        i += 1
                        value = value.split(" (")[0]
                        names.update(part.strip().lower() for part in value.split("&") if part.strip())
        except Exception:
            pass
        _registry = names
    return _registry


_STYLE_WORDS = {"regular", "bold", "italic", "oblique", "light", "semilight", "semibold", "medium", "black",
                "thin", "extralight", "extrabold", "heavy", "demibold", "condensed", "narrow", "book"}


def _in_registry(name: str) -> bool:
    """``name`` is a registered face, maybe followed by style words ("Malgun Gothic Bold")."""
    low = name.lower()
    for face in _registry_faces():
        if face == low or (face.startswith(low + " ")
                           and set(face[len(low):].split()) <= _STYLE_WORDS):
            return True
    return False


def _gdi_face_names(name: str) -> List[str]:
    """The face names GDI knows ``name`` by (it matches English and localized names)."""
    out: List[str] = []
    try:
        class LOGFONTW(ctypes.Structure):
            _fields_ = [("lfHeight", wintypes.LONG), ("lfWidth", wintypes.LONG),
                        ("lfEscapement", wintypes.LONG), ("lfOrientation", wintypes.LONG),
                        ("lfWeight", wintypes.LONG), ("lfItalic", wintypes.BYTE),
                        ("lfUnderline", wintypes.BYTE), ("lfStrikeOut", wintypes.BYTE),
                        ("lfCharSet", wintypes.BYTE), ("lfOutPrecision", wintypes.BYTE),
                        ("lfClipPrecision", wintypes.BYTE), ("lfQuality", wintypes.BYTE),
                        ("lfPitchAndFamily", wintypes.BYTE), ("lfFaceName", wintypes.WCHAR * 32)]
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(LOGFONTW), ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.LPARAM)

        def _cb(lf, _tm, _type, _lp):
            face = lf.contents.lfFaceName
            if face and face not in out:
                out.append(face)
            return 1

        lf = LOGFONTW()
        lf.lfCharSet = 1                       # DEFAULT_CHARSET
        lf.lfFaceName = name[:31]
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.GetDC.restype = wintypes.HDC
        hdc = user32.GetDC(None)
        try:
            gdi32.EnumFontFamiliesExW(wintypes.HDC(hdc), ctypes.byref(lf), proc(_cb), 0, 0)
        finally:
            user32.ReleaseDC(None, wintypes.HDC(hdc))
    except Exception:
        pass
    return out


def resolve_family(name: str) -> Optional[str]:
    """The name to ask Qt for installed face ``name`` - or None when it is not installed. On a Chinese,
    Japanese or Korean Windows Qt lists a face by its LOCALIZED name ("맑은 고딕") and keeps the English
    one only as an alias, which a name-list check can't see; the registry's English names (and GDI)
    can, and Qt matches the alias. Free: no font is loaded and Qt never searches - which it would for
    a name it can't match at all (the full load), so a name nothing confirms is never asked."""
    if name in _resolved:
        return _resolved[name]
    listed = _listed_families()
    hit = listed.get(name.lower())
    if hit is None and listed:
        for alias in _gdi_face_names(name):
            hit = listed.get(alias.lower())
            if hit is not None:
                break
    if hit is None and listed and _in_registry(name):
        hit = name                              # installed; Qt matches it by its English alias
    _resolved[name] = hit
    return hit


def family_installed(name: str) -> bool:
    return resolve_family(name) is not None


_IGNORABLE = {0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF,
              0x034F, 0x180E}


def _ignorable(ch: str) -> bool:
    """Default-ignorable code points (ZWJ, ZWNJ, variation selectors...): shaped away, no face needs them."""
    cp = ord(ch)
    return cp in _IGNORABLE or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0xE0000 <= cp <= 0xE007F


def family_covers(family: str, text: str) -> bool:
    """``family`` itself has a glyph for every character of ``text`` (bar whitespace and default-ignorable
    code points) - asked of that ONE face (a QRawFont of a no-merging QFont). A family that is not
    installed covers nothing (and Qt's stand-in box font must never be asked: QRawFont.familyName() on
    it crashes)."""
    name = resolve_family(family)
    if name is None:
        return False
    raw = _raw_fonts.get(name)
    if raw is None:
        from PyQt6.QtGui import QRawFont
        probe = QFont(name)
        probe.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        raw = QRawFont.fromFont(probe)
        _raw_fonts[name] = raw
    try:
        if not raw.isValid():
            return False
        return all(raw.supportsCharacter(ord(ch)) for ch in set(text)
                   if not ch.isspace() and not _ignorable(ch))
    except Exception:
        return False


# --------------------------------------------------------------------------- clusters and their faces

def clusters(text: str) -> List[str]:
    """``text`` split into grapheme clusters (Unicode extended clusters, Indic conjuncts and emoji ZWJ
    sequences included)."""
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    # The finder counts UTF-16 units (an emoji is two); map them to Python indices.
    index, unit = {}, 0
    for i, ch in enumerate(text):
        index[unit] = i
        unit += 2 if ord(ch) > 0xFFFF else 1
    index[unit] = len(text)
    out, start = [], 0
    while True:
        pos = finder.toNextBoundary()
        if pos < 0:
            break
        end = index.get(pos)
        if end is None or end > len(text):
            break
        if end > start:
            out.append(text[start:end])
            start = end
    if start < len(text):
        out.append(text[start:])
    return out


# Emoji_Presentation=Yes below U+1F000 (the rest of the emoji are above it).
_EMOJI_PRESENTATION = set(map(ord, "⌚⌛⏩⏪⏫⏬⏰⏳◽◾☔☕♈♉♊♋♌♍♎♏♐♑♒♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽✅✊✋✨❌❎❓❔❕❗➕➖➗➰➿⬛⬜⭐⭕"))


def _is_emoji(cluster: str) -> bool:
    """Drawn as a colour emoji: VS16 or a keycap asks for it, VS15 refuses it, else the code point's
    default presentation (as Qt's emoji segmentation, which sent these to Segoe UI Emoji until 2.2.0)."""
    if "︎" in cluster:
        return False
    if "️" in cluster or "⃣" in cluster:
        return True
    cp = ord(cluster[0])
    return cp in _EMOJI_PRESENTATION or (0x1F000 <= cp <= 0x1FAFF and not 0x1F100 <= cp <= 0x1F1E5)


def _candidates(cluster: str) -> List[str]:
    """Faces to draw ``cluster`` in when the widget's face lacks it, best first."""
    f = constants.fonts
    own = list(f.SCRIPT_FAMILIES.get(_language, ()))
    others = [fam for lang, fams in f.SCRIPT_FAMILIES.items() if lang != _language for fam in fams]
    general = [f.FALLBACK_FONT] + own + others + list(f.EXTRA_SCRIPT_FAMILIES)
    if _is_emoji(cluster):
        return list(f.EMOJI_FAMILIES) + list(f.SYMBOL_FAMILIES) + general
    if unicodedata.category(cluster[0]).startswith("S"):
        # Symbols (the arrow presets ▴ △ ▵ ⇑): the face Qt's fallback drew them in until 2.2.0, so they
        # look exactly as before; Segoe UI Symbol where it is missing.
        return list(f.SYMBOL_FAMILIES) + general + list(f.EMOJI_FAMILIES)
    return general + list(f.SYMBOL_FAMILIES) + list(f.EMOJI_FAMILIES)


def _cluster_face(base: str, cluster: str) -> Optional[str]:
    """None = the widget's own face; NO_FACE = none installed has it; else the face to draw it in."""
    if cluster.isspace() or all(_ignorable(ch) for ch in cluster):
        return None
    emoji = _is_emoji(cluster)
    # Right-to-left letters always take a face with the whole script (Segoe UI, #194): the variable face
    # has a stray Hebrew letter or two (ת), which drew one letter of a word at another size.
    rtl = any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in cluster)
    if not emoji and not rtl and family_covers(base, cluster):
        return None
    for cand in _candidates(cluster):
        if family_covers(cand, cluster):
            return resolve_family(cand)
    if (emoji or rtl) and family_covers(base, cluster):
        return None
    return NO_FACE


def text_runs(font: QFont, text: str) -> tuple:
    """``text`` as ((face or None, run), ...) in LOGICAL order: None = the font's own face, NO_FACE = the
    font's own face without merging. One run, the font's own, for text the face covers - the common
    case, measured and drawn exactly as before."""
    base = font.family()
    key = (base, _language, text)
    hit = _run_cache.get(key)
    if hit is not None:
        return hit
    if (not text or (text.isascii() and family_covers(base, text))   # the readout: digits, units, Latin
            or resolve_family(base) is None):     # a face we can't see (offscreen tests): as before
        runs = ((None, text),)
    else:
        out: List[list] = []
        for cl in clusters(text):
            face = _cluster_face(base, cl)
            if out and (face == out[-1][0] or (face is None and (cl.isspace() or all(map(_ignorable, cl))))):
                out[-1][1] += cl                       # spaces and ignorables stay with their run
            else:
                out.append([face, cl])
        runs = tuple((face, run) for face, run in out)
    if len(_run_cache) > 512:
        _run_cache.clear()
    _run_cache[key] = runs
    return runs


def run_font(font: QFont, family: Optional[str]) -> QFont:
    """``font`` in another face (or, for NO_FACE, its own) at the same size and weight, with no font
    merging (its coverage was checked, so Qt never needs a fallback chain for it)."""
    if family is None:
        return font
    key = (font.key(), family)
    hit = _font_cache.get(key)
    if hit is None:
        hit = QFont(font)
        if family != NO_FACE:
            hit.setFamily(family)
        hit.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        if len(_font_cache) > 64:
            _font_cache.clear()
        _font_cache[key] = hit
    return hit


def _layout(font: QFont, text: str, runs: tuple, direction=None) -> Tuple[QTextLayout, object]:
    """One QTextLayout for ``text`` with a font per run: Qt does bidi and shaping across the runs."""
    # Every run's font, the widget's own included, has no font merging: each one's coverage is known,
    # and a merging font in the layout makes Qt resolve a fallback chain for emoji items regardless.
    layout = QTextLayout(text, run_font(font, NO_FACE))
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.NoWrap)
    if direction is not None:
        option.setTextDirection(direction)
    layout.setTextOption(option)
    formats, pos = [], 0
    for face, run in runs:
        units = len(run.encode("utf-16-le")) // 2      # Qt counts UTF-16 units: an emoji is two
        if face not in (None, NO_FACE):
            rng = QTextLayout.FormatRange()
            rng.start, rng.length = pos, units
            fmt = QTextCharFormat()
            fmt.setFont(run_font(font, face), QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesAll)
            rng.format = fmt
            formats.append(rng)
        pos += units
    layout.setFormats(formats)
    layout.beginLayout()
    line = layout.createLine()
    layout.endLayout()
    return layout, line


def _mixed(runs: tuple) -> bool:
    return not (len(runs) == 1 and runs[0][0] is None)


def _is_rtl(text: str) -> bool:
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text)


def _neutral(run: str) -> bool:
    """No letters: punctuation, digits, spaces - characters Qt itemizes with the script next to them."""
    return not any(unicodedata.category(ch).startswith("L") for ch in run)


def _bidi_runs(runs: tuple) -> tuple:
    """For one QTextLayout: a neutral run of the widget's face next to another face's run joins that
    run where the face has it. Qt itemizes "(" or "2" beside Hebrew as Hebrew, and asking the widget's
    face for a script it lacks makes Qt search every family for one that has it (the 38 MB)."""
    out = [list(r) for r in runs]
    for i, (face, run) in enumerate(out):
        if face is not None or not _neutral(run):
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(out) and out[j][0] not in (None, NO_FACE) and family_covers(out[j][0], run):
                out[i][0] = out[j][0]
                break
    merged: List[list] = []
    for face, run in out:
        if merged and merged[-1][0] == face:
            merged[-1][1] += run
        else:
            merged.append([face, run])
    return tuple((face, run) for face, run in merged)


def _placed(font: QFont, runs: tuple) -> Tuple[list, float]:
    """Left-to-right text: ((font, run, x), ...) and the total advance - each run drawn on its own, so Qt
    itemizes it alone (a "(" before Bengali is Latin there, not Bengali)."""
    out, x = [], 0.0
    for face, run in runs:
        f = run_font(font, face)
        out.append((f, run, x))
        x += QFontMetricsF(f).horizontalAdvance(run)
    return out, x


def _advance(font: QFont, text: str, runs: tuple) -> float:
    if _is_rtl(text):
        return _layout(font, text, _bidi_runs(runs))[1].horizontalAdvance()
    return _placed(font, runs)[1]


def _draw_runs(painter, font: QFont, text: str, runs: tuple, x: float, baseline: float) -> None:
    if _is_rtl(text):
        layout, line = _layout(font, text, _bidi_runs(runs), painter.layoutDirection())
        layout.draw(painter, QPointF(x, baseline - line.ascent()))
        return
    placed, _width = _placed(font, runs)
    try:
        for f, run, dx in placed:
            painter.setFont(f)
            painter.drawText(QPointF(x + dx, baseline), run)
    finally:
        painter.setFont(font)


class WidgetFontMetrics(QFontMetrics):
    """QFontMetrics whose horizontalAdvance() measures a string the way draw_text draws it: a string
    the face covers exactly as QFontMetrics does; one with other faces as their runs. Every other metric
    (height, ascent...) is the face's own, as a merging font's always was."""

    def __init__(self, font: QFont):
        super().__init__(font)
        self._run_base = QFont(font)

    def horizontalAdvance(self, text, *args):   # noqa: N802 (Qt name)
        if args or not isinstance(text, str):
            return super().horizontalAdvance(text, *args)
        runs = text_runs(self._run_base, text)
        if not _mixed(runs):
            return super().horizontalAdvance(text)
        return round(_advance(self._run_base, text, runs))


def draw_text(painter, x, y, text: str) -> None:
    """painter.drawText(x, y, text) in the painter's font, with the clusters that face lacks drawn in
    their own faces on the same baseline."""
    font = painter.font()
    runs = text_runs(font, text) if isinstance(font, QFont) else ((None, text),)
    if not _mixed(runs):
        painter.drawText(x, y, text)
        return
    _draw_runs(painter, font, text, runs, float(x), float(y))


def draw_text_centered(painter, rect, text: str) -> None:
    """painter.drawText(rect, AlignCenter, text), runs and all: centred on the painter's font's line box
    (a merging font's line box was its first face's), so a run in another face sits where it always did."""
    font = painter.font()
    runs = text_runs(font, text) if isinstance(font, QFont) else ((None, text),)
    if not _mixed(runs):
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        return
    rect = QRectF(rect)
    fm = QFontMetrics(font)
    baseline = rect.y() + (rect.height() - fm.height()) / 2.0 + fm.ascent()
    x = rect.x() + (rect.width() - _advance(font, text, runs)) / 2.0
    _draw_runs(painter, font, text, runs, x, baseline)


def reset_caches() -> None:
    """Forget everything learned about installed fonts (tests; a font install needs a restart anyway)."""
    global _listed, _registry
    _listed = None
    _registry = None
    _resolved.clear()
    _raw_fonts.clear()
    _run_cache.clear()
    _font_cache.clear()
