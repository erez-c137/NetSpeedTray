"""
Text runs for the taskbar widget: the widget draws in ONE font, and its own strings' glyphs that face
lacks are drawn in known faces that have them.

Why: a QFont that lists more than one family (``setFamilies``, a QSS family list, a substitute) makes
Qt resolve its whole fallback chain on its first measurement, and so does a glyph the face lacks.
Resolving that chain loads the styles of EVERY installed font family: ~38 MB on a typical PC (323
families), process-wide and never freed.

The widget's OWN strings - values, units, locale strings, "N/A", arrows and arrow presets - are known:
- split into grapheme clusters (``clusters``: a Bengali conjunct, a keycap, an emoji ZWJ sequence stay
  whole), each cluster in the widget's face where it has it - digits, separators, arrows, Latin - so the
  readout looks and measures as before;
- a cluster the face lacks in the first installed KNOWN face that has it: the UI language's script face
  (Yu Gothic UI, Malgun Gothic, YaHei/JhengHei UI, Nirmala UI), Segoe UI, symbols and emoji in the faces
  Qt's fallback drew them in until 2.2.0 (measured per code point);
- each run laid out on its own with font merging off, so Qt never searches.

USER text - a Wi-Fi name - is arbitrary: any script, bidi, brackets, emoji. A left-to-right one whose
every cluster the widget's face or a known face has (Thai, CJK, Bengali, emoji...) takes the same runs
as an own string. One with right-to-left letters, a bidi control or a control character, and any one
the face lacks in a right-to-left UI (where Qt's bidi reorders its neutrals), goes to Qt
exactly as 2.2.0 drew it (rc6_font: Segoe UI as the variable face's second family, merging on) - and so
does any text with a cluster no known face has. Those cost the font load they always did; they never
look different from 2.2.0.

Each face's coverage is asked of that face alone (a no-merging QRawFont): QFontMetrics.inFont() on a
merging font, or resolving a family name Qt doesn't know, would run the very search this avoids.
"""
import ctypes
import unicodedata
from ctypes import wintypes
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics, QFontMetricsF
from PyQt6.QtCore import Qt

from netspeedtray import constants

_listed: Optional[Dict[str, str]] = None           # lower-cased name -> the name Qt lists
_registry: Optional[Set[str]] = None               # lower-cased English face names Windows registers
_resolved: Dict[str, Optional[str]] = {}            # requested name -> name to use (None: not installed)
_raw_fonts: Dict[str, object] = {}
_run_cache: Dict[Tuple[str, str, str], tuple] = {}
_font_cache: Dict[Tuple[str, str], QFont] = {}
_language = ""
QT_PATH = "\x00qt"   # draw the whole text through Qt's own fallback, as 2.2.0 did (rc6_font)


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


_IGNORABLE = {0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF,
              0x034F, 0x180E}


def _ignorable(ch: str) -> bool:
    """Default-ignorable code points (ZWJ, ZWNJ, variation selectors...): shaped away, no face needs them."""
    cp = ord(ch)
    return cp in _IGNORABLE or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0xE0000 <= cp <= 0xE007F


_gdi_cache: Dict[Tuple[str, str], bool] = {}     # (face, character) -> GDI has a glyph for it
_gdi_faces: Dict[str, bool] = {}                  # face -> GDI selects that very face for its name


def _gdi_covers(name: str, chars: Set[str]) -> Optional[bool]:
    """Whether face ``name`` has a glyph for every one of ``chars``, asked of GDI - which parses fonts in
    the font driver host, outside this process: unlike a QRawFont, asking maps none of the face's font
    files here (a Thai Wi-Fi name used to map the files of seven faces on its way to Leelawadee UI).
    None when GDI can't answer for that face: it substituted another face for the name (it has no
    "Segoe UI Variable", only its optical sizes), a character is outside the BMP (GetGlyphIndicesW
    takes UTF-16 units), or a call failed."""
    if any(ord(ch) > 0xFFFF for ch in chars) or _gdi_faces.get(name) is False:
        return None
    todo = [ch for ch in chars if (name, ch) not in _gdi_cache]
    if todo or name not in _gdi_faces:
        try:
            gdi32 = ctypes.windll.gdi32
            gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
            gdi32.CreateFontW.restype = wintypes.HFONT
            gdi32.SelectObject.restype = wintypes.HGDIOBJ
            gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            gdi32.DeleteDC.argtypes = [wintypes.HDC]
            gdi32.GetTextFaceW.argtypes = [wintypes.HDC, ctypes.c_int, wintypes.LPWSTR]
            gdi32.GetGlyphIndicesW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                               ctypes.POINTER(wintypes.WORD), wintypes.DWORD]
            dc = gdi32.CreateCompatibleDC(None)
            if not dc:
                return None
            hfont = gdi32.CreateFontW(-16, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, name[:31])   # DEFAULT_CHARSET
            old = gdi32.SelectObject(dc, hfont) if hfont else None
            try:
                if name not in _gdi_faces:
                    buf = ctypes.create_unicode_buffer(64)
                    gdi32.GetTextFaceW(dc, 64, buf)
                    face = buf.value.lower()
                    _gdi_faces[name] = bool(hfont) and (face == name.lower() or face in
                                                         (a.lower() for a in _gdi_face_names(name)))
                if _gdi_faces[name] and todo:
                    text = "".join(todo)
                    idx = (wintypes.WORD * len(text))()
                    if gdi32.GetGlyphIndicesW(dc, text, len(text), idx, 1) == 0xFFFFFFFF:  # GDI_ERROR
                        return None
                    for ch, glyph in zip(text, idx):
                        _gdi_cache[(name, ch)] = glyph != 0xFFFF      # GGI_MARK_NONEXISTING_GLYPHS
            finally:
                if old:
                    gdi32.SelectObject(dc, old)
                if hfont:
                    gdi32.DeleteObject(hfont)
                gdi32.DeleteDC(dc)
        except Exception:
            return None
    if not _gdi_faces.get(name):
        return None
    return all(_gdi_cache.get((name, ch), False) for ch in chars)


def family_covers(family: str, text: str, keep: bool = True) -> bool:
    """``family`` itself has a glyph for every character of ``text`` (bar whitespace and default-ignorable
    code points) - asked of that ONE face: of GDI where it can answer (``_gdi_covers``: no font file
    mapped into this process), else of a QRawFont of a no-merging QFont. A family that is not installed
    covers nothing (and Qt's stand-in box font must never be asked: QRawFont.familyName() on it
    crashes). ``keep=False`` (the Settings font picker, asking about arbitrary installed faces): the
    QRawFont is not cached, so the face's files are unmapped again once Qt's font database is reset."""
    name = resolve_family(family)
    if name is None:
        return False
    raw = _raw_fonts.get(name)
    if raw is None:
        chars = {ch for ch in text if not ch.isspace() and not _ignorable(ch)}
        if not chars:
            return True
        gdi = _gdi_covers(name, chars)
        if gdi is not None:
            return gdi
        from PyQt6.QtGui import QRawFont
        probe = QFont(name)
        probe.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        raw = QRawFont.fromFont(probe)
        if keep:
            _raw_fonts[name] = raw
    try:
        if not raw.isValid():
            return False
        return all(raw.supportsCharacter(ord(ch)) for ch in set(text)
                   if not ch.isspace() and not _ignorable(ch))
    except Exception:
        return False


# --------------------------------------------------------------------------- clusters and their faces

_EXTEND_RANGES = ((0x200C, 0x200D), (0xFE00, 0xFE0F), (0x20E3, 0x20E3), (0x1F3FB, 0x1F3FF),
                  (0xE0020, 0xE007F), (0xE0100, 0xE01EF))


def _extends(ch: str) -> bool:
    """Joins the cluster before it: a combining mark, ZWJ/ZWNJ, a variation selector, a keycap, a skin
    tone, an emoji tag."""
    if unicodedata.category(ch) in ("Mn", "Mc", "Me"):
        return True
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EXTEND_RANGES)


def _is_ri(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def clusters(text: str) -> List[str]:
    """``text`` split into grapheme clusters, by code point: marks, ZWJ/ZWNJ, variation selectors,
    keycaps, skin tones and tags join the cluster before them; a ZWJ joins the next character too (an
    emoji ZWJ sequence, a Bengali "র‍্য"); a virama joins the next letter (an Indic conjunct); regional
    indicators pair into a flag. Not Qt's QTextBoundaryFinder: on Qt 6.10 it reports a boundary at
    every UTF-16 unit once Indic text appears - inside an emoji's surrogate pair too."""
    out: List[str] = []
    for ch in text:
        if out:
            prev = out[-1][-1]
            if (_extends(ch)
                    or (prev == "‍" and not ch.isspace())
                    or (unicodedata.combining(prev) == 9 and unicodedata.category(ch).startswith("L"))
                    or (_is_ri(ch) and len(out[-1]) == 1 and _is_ri(out[-1]))):
                out[-1] += ch
                continue
        out.append(ch)
    return out


# Code points below U+1F000 that Qt's emoji segmentation drew in Segoe UI Emoji in 2.2.0 (measured on
# Qt 6.10: every code point of U+00A0-00FF, 2000-2BFF, 3000-303F, 3290-329F laid out in the old
# two-family widget font) - the arrows ⬆ ⬇, ☕, ⭐...
_RC6_EMOJI_RANGES = (
    (0x21EB, 0x21FF), (0x231A, 0x231B), (0x2328, 0x2328), (0x23CF, 0x23CF), (0x23E9, 0x23F3),
    (0x23F8, 0x23FA), (0x25F0, 0x25FF), (0x2614, 0x2615), (0x2618, 0x2618), (0x261D, 0x261D),
    (0x2648, 0x2653), (0x2672, 0x267F), (0x2692, 0x2697), (0x2699, 0x2699), (0x269B, 0x269C),
    (0x26A0, 0x26AB), (0x26B0, 0x26B1), (0x26BD, 0x26BE), (0x26C4, 0x26C5), (0x26C7, 0x26C8),
    (0x26CE, 0x26CF), (0x26D1, 0x26D1), (0x26D3, 0x26D4), (0x26DD, 0x26DD), (0x26E3, 0x26E3),
    (0x26E9, 0x26EA), (0x26F0, 0x26F5), (0x26F7, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705),
    (0x270A, 0x270D), (0x2728, 0x2728), (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755),
    (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF), (0x2936, 0x2937),
    (0x2B05, 0x2B07), (0x2B12, 0x2B1C), (0x2B50, 0x2B52), (0x2B55, 0x2B55))


def _is_emoji(cluster: str) -> bool:
    """Drawn as a colour emoji: VS16 or a keycap asks for it, VS15 refuses it, else the code point's
    default presentation (as Qt's emoji segmentation, which sent these to Segoe UI Emoji until 2.2.0)."""
    if "︎" in cluster:
        return False
    if "️" in cluster or "⃣" in cluster:
        return True
    cp = ord(cluster[0])
    if cp < 0x1F000:
        return any(lo <= cp <= hi for lo, hi in _RC6_EMOJI_RANGES)
    return 0x1F000 <= cp <= 0x1FAFF and not 0x1F100 <= cp <= 0x1F1E5


def _candidates(cluster: str) -> List[str]:
    """Faces to draw ``cluster`` in when the widget's face lacks it, best first."""
    f = constants.fonts
    own = list(f.SCRIPT_FAMILIES.get(_language, ()))
    others = [fam for lang, fams in f.SCRIPT_FAMILIES.items() if lang != _language for fam in fams]
    general = [f.FALLBACK_FONT] + own + others + list(f.EXTRA_SCRIPT_FAMILIES)
    if _is_emoji(cluster):
        return list(f.EMOJI_FAMILIES) + list(f.SYMBOL_FAMILIES) + general
    if not unicodedata.category(cluster[0]).startswith(("L", "M")):
        # Symbols, punctuation, numbers (the arrow presets ▴ △ ▵ ⇑, ①, ℹ): the order Qt's fallback drew
        # them in until 2.2.0 - Segoe UI, Tahoma, MS UI Gothic, Segoe UI Symbol - so they look as before.
        return [f.FALLBACK_FONT] + list(f.SYMBOL_FAMILIES) + general + list(f.EMOJI_FAMILIES)
    return general + list(f.SYMBOL_FAMILIES) + list(f.EMOJI_FAMILIES)


def _cluster_face(base: str, cluster: str) -> Optional[str]:
    """None = the widget's own face; QT_PATH = no known face has it (Qt's own fallback); else the face."""
    if cluster.isspace() or all(_ignorable(ch) for ch in cluster):
        return None
    emoji = _is_emoji(cluster)
    if not emoji and family_covers(base, cluster):
        return None
    for cand in _candidates(cluster):
        if family_covers(cand, cluster):
            return resolve_family(cand)
    if emoji and family_covers(base, cluster):
        return None
    return QT_PATH


def _is_rtl(text: str) -> bool:
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text)


# Explicit bidi controls (embeddings, overrides, isolates, marks) - they reorder text around them, which
# only Qt's bidi does; a run-by-run layout would ignore them.
_BIDI_CONTROLS = {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}


def _rtl_ui() -> bool:
    """The app is laid out right to left (Hebrew): a paragraph's direction is then right to left, and Qt's
    bidi moves a left-to-right Wi-Fi name's trailing emoji or punctuation to its left end - which only
    Qt's own layout of the whole string reproduces."""
    try:
        from PyQt6.QtGui import QGuiApplication
        return QGuiApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft
    except Exception:
        return False


def _needs_qt_layout(text: str) -> bool:
    """User text only Qt lays out right: right-to-left letters, a bidi control, a control character, or
    any text the widget's face lacks in a right-to-left UI."""
    return (_is_rtl(text) or _rtl_ui()
            or any(ord(ch) in _BIDI_CONTROLS or unicodedata.category(ch) == "Cc" for ch in text))


def _is_letter(cluster: str) -> bool:
    return unicodedata.category(cluster[0]).startswith(("L", "M")) and not _is_emoji(cluster)


def _one_face_per_word(cls: List[str], faces: List[Optional[str]]) -> List[Tuple[str, Optional[str]]]:
    """Consecutive letters drawn in other faces than the widget's - a word - in ONE face when a known face
    has the whole word: 我的网络 all in Microsoft YaHei UI, not 我的网 in Yu Gothic UI (the first Han face)
    and 络 in YaHei because Yu Gothic UI lacks it."""
    i = 0
    while i < len(cls):
        if faces[i] in (None, QT_PATH) or not _is_letter(cls[i]):
            i += 1
            continue
        j = i
        while j < len(cls) and faces[j] not in (None, QT_PATH) and _is_letter(cls[j]):
            j += 1
        if len(set(faces[i:j])) > 1:
            word = "".join(cls[i:j])
            face = next((resolve_family(c) for c in _candidates(cls[i]) if family_covers(c, word)), None)
            if face is not None:
                faces[i:j] = [face] * (j - i)
        i = j
    return list(zip(cls, faces))


def text_runs(font: QFont, text: str, user: bool = False) -> tuple:
    """``text`` as ((face, run), ...): face None = the font's own face, a family name = that face,
    QT_PATH = the whole text goes to Qt as 2.2.0 drew it (rc6_font, merging on).

    Text the face lacks a glyph of splits into runs of known faces; right-to-left text, or a cluster no
    known face has, takes the Qt path. User text (``user=True``: a Wi-Fi name) also takes the Qt path
    for a bidi control, a control character, or in a right-to-left UI: its layout stays Qt's, exactly
    as in 2.2.0 (and such a name costs the font load it always did)."""
    base = font.family()
    key = (base, _language, text, user, user and _rtl_ui())
    hit = _run_cache.get(key)
    if hit is not None:
        return hit
    if (not text or (text.isascii() and family_covers(base, text))   # the readout: digits, units, Latin
            or resolve_family(base) is None):     # a face we can't see (offscreen tests): as before
        runs = ((None, text),)
    elif _is_rtl(text) or (user and _needs_qt_layout(text)):
        runs = ((QT_PATH, text),)
    else:
        out: List[list] = []
        for cl, face in _one_face_per_word(clusters(text), [_cluster_face(base, cl) for cl in clusters(text)]):
            if out and (face == out[-1][0] or (face is None and (cl.isspace() or all(map(_ignorable, cl))))):
                out[-1][1] += cl                       # spaces and ignorables stay with their run
            else:
                out.append([face, cl])
        runs = tuple((face, run) for face, run in out)
        if any(face == QT_PATH for face, _run in runs):
            runs = ((QT_PATH, text),)
    if len(_run_cache) > 512:
        _run_cache.clear()
    _run_cache[key] = runs
    return runs


def rc6_font(font: QFont) -> QFont:
    """``font`` as 2.2.0 built it for Qt's own fallback: Segoe UI Variable lists Segoe UI as its second
    family (#194: Hebrew in Segoe UI); any other face is itself. Font merging on: Qt finds every glyph -
    and loads the styles of every installed family the first time it has to (~38 MB)."""
    key = (font.key(), "rc6")
    hit = _font_cache.get(key)
    if hit is None:
        hit = QFont(font)
        f = constants.fonts
        if font.family() == f.VARIABLE_FAMILY:
            hit.setFamilies([f.VARIABLE_FAMILY, f.FALLBACK_FONT])
        _font_cache[key] = hit
    return hit


def run_font(font: QFont, family: Optional[str]) -> QFont:
    """``font`` in another known face at the same size and weight, with no font merging (its coverage was
    checked, so Qt never needs a fallback chain for it)."""
    if family is None:
        return font
    if family == QT_PATH:
        return rc6_font(font)
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


def _plain(runs: tuple) -> bool:
    """One run in the widget's own face with nothing to drop: drawn and measured exactly as before."""
    return len(runs) == 1 and runs[0][0] is None and not any(map(_ignorable, runs[0][1]))


def _qt_path(runs: tuple) -> bool:
    return len(runs) == 1 and runs[0][0] == QT_PATH


def _placed(font: QFont, runs: tuple) -> Tuple[list, float]:
    """Known-face runs, left to right: ((font, run, x), ...) and the total advance - each run drawn on
    its own, so Qt itemizes it alone (a "(" before Bengali stays Latin). Default-ignorables are dropped
    from the widget face's runs: they shape to nothing, and asking the face for one it lacks would make
    Qt search."""
    out, x = [], 0.0
    for face, run in runs:
        if face is None:
            run = "".join(ch for ch in run if not _ignorable(ch))
            if not run:
                continue
        f = run_font(font, face)
        out.append((f, run, x))
        x += QFontMetricsF(f).horizontalAdvance(run)
    return out, x


class WidgetFontMetrics(QFontMetrics):
    """QFontMetrics whose horizontalAdvance() measures a string the way draw_text draws it: a string the
    face covers exactly as QFontMetrics does; one with known-face runs as their sum; one on the Qt path
    as 2.2.0 measured it. user_advance() is the same for user text (draw_text(..., user=True)). Every
    other metric (height, ascent...) is the face's own, as a merging font's always was."""

    def __init__(self, font: QFont):
        super().__init__(font)
        self._run_base = QFont(font)

    def _advance(self, text: str, user: bool) -> int:
        runs = text_runs(self._run_base, text, user)
        if _plain(runs):
            return super().horizontalAdvance(text)
        if _qt_path(runs):
            return QFontMetrics(rc6_font(self._run_base)).horizontalAdvance(text)
        return round(_placed(self._run_base, runs)[1])

    def horizontalAdvance(self, text, *args):   # noqa: N802 (Qt name)
        if args or not isinstance(text, str):
            return super().horizontalAdvance(text, *args)
        return self._advance(text, False)

    def user_advance(self, text: str) -> int:
        return self._advance(text, True)


def user_text_advance(fm, text: str) -> int:
    """The width of user text (a Wi-Fi name) under ``fm`` - a WidgetFontMetrics or a plain one."""
    return fm.user_advance(text) if isinstance(fm, WidgetFontMetrics) else fm.horizontalAdvance(text)


def draw_text(painter, x, y, text: str, user: bool = False) -> None:
    """painter.drawText(x, y, text) in the painter's font: a string the face covers as before, known-face
    runs on one baseline, the Qt path exactly as 2.2.0 drew it."""
    font = painter.font()
    runs = text_runs(font, text, user) if isinstance(font, QFont) else ((None, text),)
    if _plain(runs):
        painter.drawText(x, y, text)
        return
    try:
        if _qt_path(runs):
            painter.setFont(rc6_font(font))
            painter.drawText(x, y, text)
            return
        for f, run, dx in _placed(font, runs)[0]:
            painter.setFont(f)
            painter.drawText(QPointF(float(x) + dx, float(y)), run)
    finally:
        painter.setFont(font)


def draw_text_centered(painter, rect, text: str, user: bool = False) -> None:
    """painter.drawText(rect, AlignCenter, text), runs and all: centred on the painter's font's line box
    (a merging font's line box was its first face's), so a run in another face sits where it always did."""
    font = painter.font()
    runs = text_runs(font, text, user) if isinstance(font, QFont) else ((None, text),)
    if _plain(runs):
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        return
    try:
        if _qt_path(runs):
            painter.setFont(rc6_font(font))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            return
        rect = QRectF(rect)
        fm = QFontMetrics(font)
        placed, width = _placed(font, runs)
        baseline = rect.y() + (rect.height() - fm.height()) / 2.0 + fm.ascent()
        x = rect.x() + (rect.width() - width) / 2.0
        for f, run, dx in placed:
            painter.setFont(f)
            painter.drawText(QPointF(x + dx, baseline), run)
    finally:
        painter.setFont(font)


def reset_caches() -> None:
    """Forget everything learned about installed fonts (tests; a font install needs a restart anyway)."""
    global _listed, _registry
    _listed = None
    _registry = None
    _resolved.clear()
    _raw_fonts.clear()
    _gdi_cache.clear()
    _gdi_faces.clear()
    _run_cache.clear()
    _font_cache.clear()
