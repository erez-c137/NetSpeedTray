"""
Internationalization strings for the NetSpeedTray application.

This module loads user-facing strings from language-specific JSON files. It
initializes a singleton `strings` instance which provides translated strings
with a fallback to English (en_US).
"""

import logging
import locale
import json
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

    def _determine_and_set_language(self, language_code: Optional[str]) -> None:
        """Determines the most appropriate language to use and loads it."""
        if language_code:
            detected_language = language_code.replace('-', '_')
        else:
            try:
                detected_locale = locale.getlocale(locale.LC_CTYPE)
                detected_language = detected_locale[0].replace('-', '_') if detected_locale and detected_locale[0] else "en_US"
            except Exception as e:
                logger.warning(f"Failed to get default locale: {e}, falling back to en_US.")
                detected_language = "en_US"
        
        # Use the keys from LANGUAGE_MAP as the source of truth for available languages
        available_languages = list(self.LANGUAGE_MAP.keys())

        effective_language = "en_US"
        if detected_language in available_languages:
            effective_language = detected_language
        else:
            base_language = detected_language.split('_')[0]
            for supported_lang in available_languages:
                if supported_lang.startswith(base_language + '_'):
                    effective_language = supported_lang
                    break
        
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
        
        logger.info(f"I18nStrings initialized. Effective language: {self.language}")

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