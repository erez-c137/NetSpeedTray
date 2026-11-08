"""
Glyph runs for the taskbar widget's text: every string is drawn in the widget's ONE font, and only the
characters that face lacks are drawn - run by run - in a named face that has them.

Why: a QFont that lists more than one family (``setFamilies``, a QSS family list, a substitute) makes
Qt resolve its whole fallback chain on its first measurement, and so does a glyph the face lacks.
Resolving that chain loads the styles of EVERY installed font family: ~38 MB on a typical PC (323
families), process-wide and never freed. So the widget never lets Qt look for a glyph. It checks each
character against its own face (a no-merging QRawFont - asking a merging QFontMetrics.inFont() would
walk the chain itself) and draws the ones the face lacks in a face picked here: the face Qt's fallback
used to pick where that is a good one, the script's Windows UI face otherwise. Digits, separators,
arrows and Latin text stay in the widget's face, so its look and width are unchanged; a Japanese "秒"
or a Chinese "N/A" is drawn in Yu Gothic UI / Microsoft YaHei UI at the same size, on the same
baseline - what Qt's merging font engine did, minus the 38 MB.

Right-to-left text (a Hebrew Wi-Fi name) is never split into runs - splitting would break its bidi
order - but drawn whole in the first face that has all of it (Segoe UI: #194).
"""
import ctypes
import unicodedata
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics, QFontMetricsF

from netspeedtray import constants

_listed: Optional[Dict[str, str]] = None           # lower-cased name -> the name Qt lists
_resolved: Dict[str, Optional[str]] = {}            # requested name -> listed name (None: not installed)
_raw_fonts: Dict[str, object] = {}
_run_cache: Dict[Tuple[str, str, str], tuple] = {}
_font_cache: Dict[Tuple[str, str], QFont] = {}
_language = ""


def set_text_language(language: Optional[str]) -> None:
    """The UI language, which puts its own script face first (Japanese and Chinese share Han glyphs)."""
    global _language
    language = str(language or "")
    if language != _language:
        _language = language
        _run_cache.clear()


def _listed_families() -> Dict[str, str]:
    global _listed
    if _listed is None:
        try:
            _listed = {f.lower(): f for f in QFontDatabase.families()}   # names only: no styles loaded
        except Exception:
            _listed = {}
    return _listed


def _gdi_face_names(name: str) -> List[str]:
    """The face names GDI knows ``name`` by. On a Chinese, Japanese or Korean Windows Qt lists a font
    under its LOCALIZED name ("맑은 고딕") and keeps the English one only as an alias, which a name check
    can't see - and asking Qt to resolve a name it can't find runs the very fallback search this module
    avoids. GDI matches either name and reports the one in use."""
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
    """The installed family ``name`` is, by the name Qt lists it under - or None. Free: Qt's family
    NAME list plus GDI; no font is loaded and Qt never searches."""
    if name in _resolved:
        return _resolved[name]
    listed = _listed_families()
    hit = listed.get(name.lower())
    if hit is None and listed:
        for alias in _gdi_face_names(name):
            hit = listed.get(alias.lower())
            if hit is not None:
                break
    _resolved[name] = hit
    return hit


def family_installed(name: str) -> bool:
    return resolve_family(name) is not None


def family_covers(family: str, text: str) -> bool:
    """``family`` itself has a glyph for every non-space character of ``text`` - asked of that ONE face
    (a QRawFont of a no-merging QFont). A family that is not installed covers nothing (and Qt's
    stand-in box font must never be asked: QRawFont.familyName() on it crashes)."""
    listed = resolve_family(family)
    if listed is None:
        return False
    raw = _raw_fonts.get(listed)
    if raw is None:
        from PyQt6.QtGui import QRawFont
        probe = QFont(listed)
        probe.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        raw = QRawFont.fromFont(probe)
        _raw_fonts[listed] = raw
    try:
        if not raw.isValid():
            return False
        return all(raw.supportsCharacter(ord(ch)) for ch in set(text) if not ch.isspace())
    except Exception:
        return False


def _candidates(ch: str) -> List[str]:
    """Faces to draw ``ch`` in when the widget's face lacks it, best first."""
    f = constants.fonts
    own = list(f.SCRIPT_FAMILIES.get(_language, ()))
    others = [fam for lang, fams in f.SCRIPT_FAMILIES.items() if lang != _language for fam in fams]
    if ord(ch) >= 0x1F000:                     # emoji: the face Qt's fallback drew them in
        return list(f.EMOJI_FAMILIES) + list(f.SYMBOL_FAMILIES)
    if unicodedata.category(ch).startswith("S"):
        # Symbols (the arrow presets ▴ △ ▵ ⇑): the face Qt's fallback drew them in until 2.2.0, so they
        # look exactly as before; Segoe UI Symbol where it is missing.
        return list(f.SYMBOL_FAMILIES) + [f.FALLBACK_FONT] + own + others + list(f.EMOJI_FAMILIES)
    return [f.FALLBACK_FONT] + own + others + list(f.SYMBOL_FAMILIES) + list(f.EMOJI_FAMILIES)


def _is_rtl(text: str) -> bool:
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text)


def text_runs(font: QFont, text: str) -> tuple:
    """``text`` as ((family or None, run), ...): None = the font's own face. One run, the font's own,
    for text the face covers - the common case, measured and drawn exactly as before."""
    base = font.family()
    key = (base, _language, text)
    hit = _run_cache.get(key)
    if hit is not None:
        return hit
    if not text or family_covers(base, text):
        runs = ((None, text),)
    elif _is_rtl(text):
        need = "".join(ch for ch in text if not ch.isspace())
        face = next((c for c in _candidates(next(ch for ch in need if not family_covers(base, ch)))
                     if family_covers(c, need)), None)
        runs = ((resolve_family(face) if face else None, text),)
    else:
        out: List[list] = []
        for ch in text:
            if out and (ch.isspace() or unicodedata.category(ch).startswith("M")):
                out[-1][1] += ch                       # spaces and marks stay with their run
                continue
            fam = None
            if not family_covers(base, ch):
                fam = next((resolve_family(c) for c in _candidates(ch) if family_covers(c, ch)), None)
            if out and out[-1][0] == fam:
                out[-1][1] += ch
            else:
                out.append([fam, ch])
        runs = tuple((fam, run) for fam, run in out)
    if len(_run_cache) > 512:
        _run_cache.clear()
    _run_cache[key] = runs
    return runs


def run_font(font: QFont, family: Optional[str]) -> QFont:
    """``font`` in another face at the same size and weight, with no font merging (its coverage was
    checked, so Qt never needs a fallback chain for it)."""
    if family is None:
        return font
    key = (font.key(), family)
    hit = _font_cache.get(key)
    if hit is None:
        hit = QFont(font)
        hit.setFamily(family)
        hit.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        if len(_font_cache) > 64:
            _font_cache.clear()
        _font_cache[key] = hit
    return hit


def _runs_width(font: QFont, runs: tuple) -> float:
    return sum(QFontMetricsF(run_font(font, fam)).horizontalAdvance(run) for fam, run in runs)


class WidgetFontMetrics(QFontMetrics):
    """QFontMetrics whose horizontalAdvance() measures a string the way draw_text draws it: a string
    the face covers exactly as QFontMetrics does; one with other glyphs as the sum of its runs. Every
    other metric (height, ascent...) is the face's own, as a merging font's always was."""

    def __init__(self, font: QFont):
        super().__init__(font)
        self._run_base = QFont(font)

    def horizontalAdvance(self, text, *args):   # noqa: N802 (Qt name)
        if args or not isinstance(text, str):
            return super().horizontalAdvance(text, *args)
        runs = text_runs(self._run_base, text)
        if len(runs) == 1 and runs[0][0] is None:
            return super().horizontalAdvance(text)
        return round(_runs_width(self._run_base, runs))


def draw_text(painter, x, y, text: str) -> None:
    """painter.drawText(x, y, text) in the painter's font, with the glyphs that face lacks drawn in
    their own faces on the same baseline."""
    font = painter.font()
    runs = text_runs(font, text)
    if len(runs) == 1 and runs[0][0] is None:
        painter.drawText(x, y, text)
        return
    cx = float(x)
    try:
        for fam, run in runs:
            face = run_font(font, fam)
            painter.setFont(face)
            painter.drawText(QPointF(cx, float(y)), run)
            cx += QFontMetricsF(face).horizontalAdvance(run)
    finally:
        painter.setFont(font)


def draw_text_centered(painter, rect, text: str) -> None:
    """painter.drawText(rect, AlignCenter, text), runs and all: centred on the painter's font's line box
    (a merging font's line box was its first face's), so a run in another face sits where it always did."""
    font = painter.font()
    runs = text_runs(font, text)
    if len(runs) == 1 and runs[0][0] is None:
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        return
    rect = QRectF(rect)
    fm = QFontMetricsF(font)
    width = _runs_width(font, runs)
    baseline = rect.y() + (rect.height() - fm.height()) / 2.0 + fm.ascent()
    cx = rect.x() + (rect.width() - width) / 2.0
    try:
        for fam, run in runs:
            face = run_font(font, fam)
            painter.setFont(face)
            painter.drawText(QPointF(cx, baseline), run)
            cx += QFontMetricsF(face).horizontalAdvance(run)
    finally:
        painter.setFont(font)


def reset_caches() -> None:
    """Forget everything learned about installed fonts (tests; a font install needs a restart anyway)."""
    global _listed
    _listed = None
    _resolved.clear()
    _raw_fonts.clear()
    _run_cache.clear()
    _font_cache.clear()
