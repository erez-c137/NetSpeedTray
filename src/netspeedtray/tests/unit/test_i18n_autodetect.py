"""
Regression tests for language auto-detection (#234).

Auto-detect never resolved on Windows: `locale.getlocale(LC_CTYPE)` returns the C-runtime locale
name ("Korean_Korea"), which matches none of our `ko_KR`-style locale files, so every affected user
silently fell back to en_US. German, Spanish and French happened to work only because CPython's own
`locale_alias` carries their CRT names - which is why this went unnoticed across every 2.x release.

These tests cover the resolver (pure, no Windows needed) and the detection path (LCID -> code).
"""

import locale
import sys
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.constants.i18n import I18nStrings


# The C-runtime names Windows reports for each shipped locale. Every one of these resolved to
# en_US before the fix.
CRT_NAMES = [
    ("Korean_Korea", "ko_KR"),
    ("Japanese_Japan", "ja_JP"),
    ("Russian_Russia", "ru_RU"),
    ("Polish_Poland", "pl_PL"),
    ("Dutch_Netherlands", "nl_NL"),
    ("Slovenian_Slovenia", "sl_SI"),
    ("Hebrew_Israel", "he_IL"),
]


class TestResolveLanguage:
    """`resolve_language` maps an arbitrary locale code onto a shipped locale."""

    @pytest.mark.parametrize("code", list(I18nStrings.LANGUAGE_MAP))
    def test_every_shipped_locale_resolves_to_itself(self, code):
        assert I18nStrings.resolve_language(code) == code

    @pytest.mark.parametrize("windows_code,expected", [
        ("ko_KR", "ko_KR"),
        ("ja_JP", "ja_JP"),
        ("he_IL", "he_IL"),
        ("sl_SI", "sl_SI"),
    ])
    def test_windows_locale_codes_resolve(self, windows_code, expected):
        """The codes `locale.windows_locale` actually yields for our languages."""
        assert I18nStrings.resolve_language(windows_code) == expected

    @pytest.mark.parametrize("regional,expected", [
        ("de_AT", "de_DE"), ("de_CH", "de_DE"), ("de_LU", "de_DE"), ("de_LI", "de_DE"),
        ("es_MX", "es_ES"), ("es_AR", "es_ES"), ("es_US", "es_ES"), ("es_CO", "es_ES"),
        ("fr_CA", "fr_FR"), ("fr_BE", "fr_FR"), ("fr_CH", "fr_FR"), ("fr_MC", "fr_FR"),
        ("nl_BE", "nl_NL"),
    ])
    def test_regional_variants_fall_back_to_the_base_locale(self, regional, expected):
        """A Swiss German or Mexican Spanish user should get the language, not English."""
        assert I18nStrings.resolve_language(regional) == expected

    @pytest.mark.parametrize("chinese,expected", [
        # Script-neutral LCIDs 4 and 31748 - what Windows reports for neutral Chinese.
        ("zh_CHS", "zh_CN"),
        ("zh_CHT", "zh_TW"),
        # Newer CPython spellings.
        ("zh_Hans", "zh_CN"),
        ("zh_Hant", "zh_TW"),
        # Regional. HK and Macau are Traditional; Singapore is Simplified.
        ("zh_CN", "zh_CN"),
        ("zh_SG", "zh_CN"),
        ("zh_TW", "zh_TW"),
        ("zh_HK", "zh_TW"),
        ("zh_MO", "zh_TW"),
    ])
    def test_chinese_script_is_disambiguated(self, chinese, expected):
        """A plain zh_ prefix scan hands Traditional users the Simplified file - LANGUAGE_MAP
        lists zh_CN first. These must be resolved by script, not by map order."""
        assert I18nStrings.resolve_language(chinese) == expected

    @pytest.mark.parametrize("code", ["ko-KR", "ko_kr", "KO_KR", "zh-Hant", "ZH_CHT"])
    def test_separator_and_case_are_normalized(self, code):
        assert I18nStrings.resolve_language(code) in ("ko_KR", "zh_TW")

    @pytest.mark.parametrize("code", [None, "", "xx_YY", "Klingon_Kronos", "!!"])
    def test_unknown_input_falls_back_to_english(self, code):
        assert I18nStrings.resolve_language(code) == "en_US"

    def test_crt_names_are_not_silently_accepted(self):
        """`Korean_Korea` must not resolve to Korean by accident - it is not a locale code, and
        pretending otherwise would mask a detection failure. It falls back to English; the fix is
        that we no longer *ask* for this form on Windows."""
        assert I18nStrings.resolve_language("Korean_Korea") == "en_US"


class TestReadSystemLocale:
    """`_read_system_locale` prefers the Windows display language over the CRT locale."""

    def _fake_windll(self, lcid):
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.return_value = lcid
        return windll

    @pytest.mark.parametrize("lcid,expected", [
        (1042, "ko_KR"),    # Korean (Korea)
        (1041, "ja_JP"),    # Japanese
        (1037, "he_IL"),    # Hebrew
        (2052, "zh_CN"),    # Chinese (PRC)
        (1028, "zh_TW"),    # Chinese (Taiwan)
        (1033, "en_US"),    # English (US)
    ])
    def test_lcid_is_translated_to_a_locale_code(self, lcid, expected):
        with patch("ctypes.windll", self._fake_windll(lcid), create=True):
            assert I18nStrings._read_system_locale() == expected

    def test_unmapped_lcid_falls_through_to_the_locale_module(self):
        """An LCID with no `locale.windows_locale` entry must not return None outright."""
        with patch("ctypes.windll", self._fake_windll(0x9999), create=True), \
             patch("locale.getlocale", return_value=("nl_NL", "UTF-8")):
            assert I18nStrings._read_system_locale() == "nl_NL"

    def test_missing_windll_falls_through_to_the_locale_module(self):
        """Off Windows (and in CI containers) `ctypes.windll` does not exist. The import and the
        attribute access both sit inside the try, so this must degrade rather than raise."""
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.side_effect = AttributeError("no windll")
        with patch("ctypes.windll", windll, create=True), \
             patch("locale.getlocale", return_value=("pl_PL", "UTF-8")):
            assert I18nStrings._read_system_locale() == "pl_PL"

    def test_returns_none_when_every_source_fails(self):
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.side_effect = OSError("boom")
        with patch("ctypes.windll", windll, create=True), \
             patch("locale.getlocale", side_effect=ValueError("unknown locale")):
            assert I18nStrings._read_system_locale() is None


class TestDisplayLanguageVersusRegionalFormat:
    """These are two independent Windows settings and routinely differ.

    Display language is the better signal and wins. But the regional format locale is what the old
    code read, and it is the *only* reason German, Spanish and French users ever got their own
    language - so a patch release must not silently take that away from them.
    """

    def _windll(self, lcid):
        w = MagicMock()
        w.kernel32.GetUserDefaultUILanguage.return_value = lcid
        return w

    def test_display_language_wins_when_both_are_supported(self):
        """Korean display, German regional format -> Korean. The user reads Windows in Korean."""
        with patch("ctypes.windll", self._windll(1042), create=True), \
             patch("locale.getlocale", return_value=("de_DE", "ISO8859-1")):
            assert I18nStrings(None).language == "ko_KR"

    @pytest.mark.parametrize("format_locale,expected", [
        ("de_DE", "de_DE"), ("es_ES", "es_ES"), ("fr_FR", "fr_FR"),
    ])
    def test_english_display_falls_back_to_a_supported_regional_format(self, format_locale, expected):
        """The behavior this fix must NOT take away: English-language Windows with a German,
        Spanish or French regional format is common across Europe, and those users got a localized
        app in 2.1.2. CPython's locale_alias resolves exactly these three CRT names."""
        with patch("ctypes.windll", self._windll(1033), create=True), \
             patch("locale.getlocale", return_value=(format_locale, "ISO8859-1")):
            assert I18nStrings(None).language == expected

    def test_english_display_with_english_format_stays_english(self):
        with patch("ctypes.windll", self._windll(1033), create=True), \
             patch("locale.getlocale", return_value=("en_US", "UTF-8")):
            assert I18nStrings(None).language == "en_US"

    def test_english_display_with_an_unresolvable_format_stays_english(self):
        """The CRT names for our other 9 locales resolve to nothing, so there is no second signal
        to fall back to - which is exactly the #234 population."""
        with patch("ctypes.windll", self._windll(1033), create=True), \
             patch("locale.getlocale", return_value=("Korean_Korea", "949")):
            assert I18nStrings(None).language == "en_US"

    def test_english_display_with_an_unsupported_format_stays_english(self):
        with patch("ctypes.windll", self._windll(1033), create=True), \
             patch("locale.getlocale", return_value=("it_IT", "UTF-8")):
            assert I18nStrings(None).language == "en_US"

def test_the_module_imports_without_windll():
    """A module-level `ctypes.windll` reference would make the package un-importable off Windows,
    and CI (windows-latest only) would never catch it. Asserted by actually importing with windll
    removed, rather than by grepping the source for how the call happens to be spelled."""
    import os
    import subprocess
    from pathlib import Path

    import netspeedtray

    env = dict(os.environ, PYTHONPATH=str(Path(netspeedtray.__file__).parents[1]))
    result = subprocess.run(
        [sys.executable, "-c",
         "import ctypes; del ctypes.windll; import netspeedtray.constants.i18n"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"import failed without windll:\n{result.stderr}"


class TestEndToEndDetection:
    """The whole path: no config value -> system locale -> loaded strings."""

    @pytest.mark.parametrize("lcid,expected", [(1042, "ko_KR"), (1041, "ja_JP"), (2052, "zh_CN")])
    def test_auto_detect_loads_the_system_language(self, lcid, expected):
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.return_value = lcid
        with patch("ctypes.windll", windll, create=True):
            assert I18nStrings(None).language == expected

    @pytest.mark.parametrize("crt_name,expected", CRT_NAMES)
    def test_the_display_language_wins_over_the_crt_locale(self, crt_name, expected):
        """The regression itself, pinned by priority order.

        Both signals are present and both name the same language, but only the LCID form is
        resolvable. The pre-fix code read *only* `locale.getlocale()`, got 'Korean_Korea', matched
        nothing, and fell back to English - so this fails against the old code for the right
        reason. Asserting the old outcome instead would pass against the bug.
        """
        lcid = {v: k for k, v in locale.windows_locale.items()}[expected]
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.return_value = lcid
        with patch("ctypes.windll", windll, create=True), \
             patch("locale.getlocale", return_value=(crt_name, "949")):
            assert I18nStrings(None).language == expected

    @pytest.mark.parametrize("crt_name,_unused", CRT_NAMES)
    def test_crt_name_alone_still_cannot_resolve(self, crt_name, _unused):
        """With the LCID path unavailable and only the CRT name to go on, we still fall back to
        English - the stdlib genuinely cannot resolve these. Documents *why* the Windows API call
        is load-bearing rather than a stylistic preference."""
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.side_effect = AttributeError("no windll")
        with patch("ctypes.windll", windll, create=True), \
             patch("locale.getlocale", return_value=(crt_name, "949")):
            assert I18nStrings(None).language == "en_US"

    def test_explicit_config_value_wins_over_the_system(self):
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.return_value = 1042  # Korean
        with patch("ctypes.windll", windll, create=True):
            assert I18nStrings("ja_JP").language == "ja_JP"

    def test_detect_system_language_matches_what_auto_detect_loads(self):
        """Settings shows `detect_system_language()` on the auto-detect row; if it disagreed with
        what actually loads, the label would lie."""
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.return_value = 1042
        with patch("ctypes.windll", windll, create=True):
            assert I18nStrings.detect_system_language() == I18nStrings(None).language

    def test_hebrew_autodetect_flips_rtl(self):
        """he_IL is our only RTL locale, and auto-detect can now reach it - which means a Hebrew
        display-language user gets a mirrored app from a patch update. Pinned deliberately."""
        windll = MagicMock()
        windll.kernel32.GetUserDefaultUILanguage.return_value = 1037  # Hebrew (Israel)
        with patch("ctypes.windll", windll, create=True):
            i18n = I18nStrings(None)
        assert i18n.language == "he_IL"
        assert i18n.is_rtl is True
