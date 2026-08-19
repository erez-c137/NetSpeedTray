"""
Internationalization strings for the NetSpeedTray application.

This module loads user-facing strings from language-specific JSON files. It
initializes a singleton `strings` instance which provides translated strings
with a fallback to English (en_US).
"""

import logging
import locale
import json
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("NetSpeedTray.I18n")
strings: Optional["I18nStrings"] = None


def get_locales_path() -> Path:
    """Returns the absolute path to the 'locales' directory."""
    return Path(__file__).parent / "locales"


def get_i18n(language_code: Optional[str] = None) -> "I18nStrings":
    """
    Initializes (if needed) and returns the global i18n singleton.
    """
    global strings
    if strings is None:
        logger.debug("First call; initializing i18n singleton.")
        strings = I18nStrings(language_code)
    return strings


class I18nStrings:
    """
    User-facing strings for internationalization, loaded from individual files.
    """

    # BEST PRACTICE: Use native language names (endonyms). These are not translated.
    LANGUAGE_MAP: Dict[str, str] = {
        "en_US": "English (US)",
        "de_DE": "Deutsch (Deutschland)",
        "es_ES": "Español (España)",
        "fr_FR": "Français (France)",
        "nl_NL": "Nederlands (Nederland)",
        "pl_PL": "Polski (Polska)",
        "ru_RU": "Русский (Россия)",
        "ko_KR": "한국어 (대한민국)",
        "sl_SI": "Slovenščina (Slovenija)",
        "ja_JP": "日本語 (日本)",
        "zh_CN": "简体中文 (中国)",
        "zh_TW": "繁體中文 (台灣)",
        "he_IL": "עברית (ישראל)",
    }

    # Right-to-left languages. When one is active, the app flips its layout direction (mirrored
    # settings/monitor layouts + a mirrored widget). Kept as a set so more RTL locales (ar_*, fa_*)
    # can be added later without touching the direction logic.
    RTL_LANGUAGES: set = {"he_IL"}

    # Locale codes that need an explicit target because a bare prefix scan would pick the wrong
    # file. Windows reports script-neutral Chinese as zh_CHS/zh_CHT (LCIDs 4 and 31748), and the
    # regional Chinese codes don't say which script they use - but LANGUAGE_MAP lists zh_CN before
    # zh_TW, so a prefix scan hands every Traditional user the Simplified file. Hong Kong and Macau
    # are Traditional; Singapore is Simplified. zh_Hans/zh_Hant are included because newer CPython
    # releases spell the neutral codes that way. Keys are lower-cased for matching.
    LANGUAGE_ALIASES: Dict[str, str] = {
        "zh_chs": "zh_CN", "zh_hans": "zh_CN", "zh_sg": "zh_CN",
        "zh_cht": "zh_TW", "zh_hant": "zh_TW", "zh_hk": "zh_TW", "zh_mo": "zh_TW",
    }

    def __init__(self, language_code: Optional[str] = None) -> None:
        """
        Initialize the I18nStrings instance by loading language files.
        """
        self._locales_path = get_locales_path()
        self._fallback_strings: Dict[str, str] = self._load_language("en_US")

        if not self._fallback_strings:
            raise RuntimeError("Failed to load base English (en_US) language file. Application cannot continue.")

        self._strings: Dict[str, str] = {}
        self.language = ""
        self._determine_and_set_language(language_code)

        # Locale-parity validation loads ALL 10 locale files purely to compare key sets - a dev/CI
        # concern (enforced by test_locales_parity.py), not a runtime need: per-key lookups already
        # fall back to en_US. Skipping it at runtime saves ~9 JSON parses on the UI thread every launch.
        # Opt in with NST_VALIDATE_I18N=1 when editing locales from source.
        if os.environ.get("NST_VALIDATE_I18N"):
            try:
                self.validate()
            except ValueError as e:
                logger.error(f"I18n validation failed on initialization: {e}")

    def _load_language(self, lang_code: str) -> Dict[str, str]:
        """Loads a language dictionary from its JSON file."""
        lang_file = self._locales_path / f"{lang_code}.json"
        if not lang_file.exists():
            logger.error(f"Language file not found: {lang_file}")
            return {}
        try:
            with lang_file.open('r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load or parse language file {lang_file}: {e}")
            return {}

    @classmethod
    def resolve_language(cls, detected: Optional[str]) -> str:
        """Map an arbitrary locale code onto one of our shipped locales, else en_US.

        Handles three cases in order: an explicit alias (Chinese script disambiguation), an exact
        shipped locale, and a regional variant of a language we do ship (de_AT -> de_DE,
        es_MX -> es_ES, fr_CA -> fr_FR, nl_BE -> nl_NL). Matching is case-insensitive and accepts
        either separator, so 'ko-kr' and 'ko_KR' both work.
        """
        if not detected:
            return "en_US"
        normalized = detected.replace('-', '_')
        lowered = normalized.lower()

        alias = cls.LANGUAGE_ALIASES.get(lowered)
        if alias:
            return alias

        for code in cls.LANGUAGE_MAP:
            if code.lower() == lowered:
                return code

        base = lowered.split('_')[0]
        if base:
            for code in cls.LANGUAGE_MAP:
                if code.lower().startswith(base + '_'):
                    return code

        return "en_US"

    @staticmethod
    def _read_ui_language() -> Optional[str]:
        """The Windows *display* language as a locale code, or None off Windows."""
        try:
            # Imported lazily and dereferenced inside the try: `ctypes.windll` does not exist off
            # Windows, and this module is imported by the whole test suite.
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid)
            if name:
                return name
            logger.debug("UI language LCID %s has no locale.windows_locale entry.", lcid)
        except Exception as e:
            logger.debug("GetUserDefaultUILanguage unavailable (%s); using the locale module.", e)
        return None

    @staticmethod
    def _read_format_locale() -> Optional[str]:
        """The regional-format locale, as the C runtime reports it."""
        try:
            detected_locale = locale.getlocale(locale.LC_CTYPE)
            if detected_locale and detected_locale[0]:
                return detected_locale[0]
        except Exception as e:
            logger.debug("locale.getlocale failed (%s).", e)
        return None

    @classmethod
    def _read_system_locale(cls) -> Optional[str]:
        """The raw system locale code, preferring the Windows *display* language.

        Two independent signals, because neither alone is sufficient:

        * The **display language** (`GetUserDefaultUILanguage`) is the language Windows itself is
          shown in, and is what a user means by "my system language". This is the signal we want.
        * The **regional format** (`locale.getlocale`) is the number/date locale, and is what the
          old code read. On Windows it returns the C-runtime name - 'Korean_Korea', not 'ko_KR' -
          which matches no locale file we ship, so 9 of our 13 languages silently fell back to
          English (#234). Only German, Spanish and French ever resolved, because CPython's
          `locale_alias` happens to carry their CRT names.

        Display language wins. But when it is English we still consult the format locale, because a
        German user on an English-language Windows with German regional format *did* get a German
        app before this fix, and a patch release must not silently take that away.
        """
        ui_language = cls._read_ui_language()
        if ui_language and cls.resolve_language(ui_language) != "en_US":
            return ui_language

        format_language = cls._read_format_locale()
        if format_language and cls.resolve_language(format_language) != "en_US":
            return format_language

        return ui_language or format_language

    @classmethod
    def detect_system_language(cls) -> str:
        """The locale that auto-detect resolves to on this machine.

        Public because Settings shows it on the "Auto-detect (system)" row - without that, a user
        whose language silently failed to resolve has no way to tell.
        """
        return cls.resolve_language(cls._read_system_locale())

    def _determine_and_set_language(self, language_code: Optional[str]) -> None:
        """Determines the most appropriate language to use and loads it."""
        if language_code:
            effective_language = self.resolve_language(language_code)
        else:
            raw = self._read_system_locale()
            effective_language = self.resolve_language(raw)
            logger.info("Language auto-detect: system reported %r -> using '%s'.", raw, effective_language)

        self.set_language(effective_language)

    def __getattr__(self, name: str) -> str:
        """
        Override attribute access to look up translation strings with a fallback to English.
        """
        value = self._strings.get(name)

        if value is None:
            if self.language != "en_US":
                logger.warning(f"String constant '{name}' not found in language '{self.language}'. Attempting en_US fallback.")
            value = self._fallback_strings.get(name)

        if value is None:
            logger.critical(f"String constant '{name}' not found in fallback language 'en_US'.")
            raise AttributeError(f"String constant '{name}' is missing from all language definitions.")

        if not isinstance(value, str):
            logger.error(f"Value for '{name}' is not a string (type: {type(value)}).")
            return f"[ERR: TYPE {name}]"
        
        return value

    @property
    def is_rtl(self) -> bool:
        """True when the active language is right-to-left - drives QApplication.setLayoutDirection."""
        return self.language in self.RTL_LANGUAGES

    def set_language(self, language_code: str) -> None:
        """Sets the current language and loads the corresponding strings from file."""
        normalized_language = language_code.replace('-', '_')
        
        if self.language == normalized_language:
            return

        if normalized_language not in self.LANGUAGE_MAP:
            logger.warning(f"Language '{language_code}' is not supported. Falling back to en_US.")
            normalized_language = "en_US"
        
        self.language = normalized_language
        if self.language == "en_US":
            self._strings = self._fallback_strings
        else:
            self._strings = self._load_language(self.language)
        
        if not self._strings:
             logger.error(f"Failed to load strings for '{self.language}'. Using English fallbacks.")
             self._strings = self._fallback_strings
        
        logger.debug(f"I18nStrings initialized. Effective language: {self.language}")

    def validate(self) -> None:
        """
        Validates that all language files contain the same keys as the en_US master.
        """
        logger.debug("Validating all I18n strings...")
        master_keys = set(self._fallback_strings.keys())

        validation_errors = []
        # Validate all languages defined in our map
        for lang_code in self.LANGUAGE_MAP.keys():
            if lang_code == "en_US":
                continue

            translations_dict = self._load_language(lang_code)
            if not translations_dict:
                validation_errors.append(f"Could not load or parse '{lang_code}'.")
                continue

            current_lang_keys = set(translations_dict.keys())
            
            missing_keys = master_keys - current_lang_keys
            if missing_keys:
                validation_errors.append(f"Language '{lang_code}' is missing keys: {sorted(list(missing_keys))}")

            extra_keys = current_lang_keys - master_keys
            if extra_keys:
                logger.warning(f"Language '{lang_code}' has extra keys not in en_US: {sorted(list(extra_keys))}")

        if validation_errors:
            error_summary = "I18n string validation failed:\n- " + "\n- ".join(validation_errors)
            raise ValueError(error_summary)
        else:
            logger.debug("All I18n strings validated successfully against en_US keys.")
