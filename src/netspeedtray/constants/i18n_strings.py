"""
Internationalization strings for the NetSpeedTray application.

This module defines user-facing strings that may need to be translated in the future.
It supports multiple languages using a dictionary-based approach, with logging for
language selection and validation. Ensures fallback to English (en_US) if a string
is missing in the detected language.
"""

import logging
import locale
from typing import Dict, Any

# Define logger at the module level
logger = logging.getLogger("NetSpeedTray.I18nStrings")

class I18nStrings:
    """
    User-facing strings for internationalization.

    Provides a collection of translatable strings for the NetSpeedTray application,
    supporting multiple languages. The current language is determined by the system's
    locale, with a fallback to English (en_US).

    Attributes:
        language (str): Current language code (e.g., "en_US", "fr_FR"). Determined at init.
        _strings (Dict[str, Dict[str, str]]): Dictionary mapping language codes to string dictionaries.
    """

    def __init__(self) -> None:
        """
        Initialize the I18nStrings instance with the user's default language.

        Attempts to determine the user's language from the system locale, falling back
        to "en_US" if detection fails or the language is unsupported. Also performs
        validation of the string dictionaries upon initialization.
        """
        self._strings: Dict[str, Dict[str, str]] = {
            # ================= English (United States) =================
            "en_US": {
                # --- Window and dialog titles ---
                "SETTINGS_WINDOW_TITLE": "Settings",
                "GRAPH_WINDOW_TITLE": "Network Speed History",
                "EXPORT_CSV_TITLE": "Export History",
                "EXPORT_GRAPH_IMAGE_TITLE": "Save Graph Image",
                "EXPORT_ERROR_LOG_TITLE": "Export Error Log",
                "SELECT_COLOR_TITLE": "Select Color",
                "SELECT_FONT_TITLE": "Select Font",
                "ERROR_TITLE": "Error",
                "WARNING_TITLE": "Warning",
                "SUCCESS_TITLE": "Success",
                "INFORMATION_TITLE": "Information",
                "NO_LOG_TITLE": "Log File Not Found",
                "ERROR_WINDOW_TITLE": "Application Error",

                # --- Messages ---
                "NO_DATA_MESSAGE": "No data available for the selected period.",
                "GRAPH_ERROR_MESSAGE": "Failed to show the graph window. Check logs for details.",
                "APP_USAGE_ERROR_MESSAGE": "Failed to load app usage data. Check logs for details.",
                "SETTINGS_ERROR_MESSAGE": "Failed to apply one or more settings.",
                "SAVE_ERROR_MESSAGE": "Failed to save settings:\n{error}",
                "SETUP_ERROR_MESSAGE": "Critical error during UI setup.", # Simplified
                "COLOR_PICKER_ERROR_MESSAGE": "Could not open color picker.",
                "FONT_SELECTOR_ERROR_MESSAGE": "Could not open font selector.",
                "DEFAULT_TEXT": "N/A",
                "EXPORT_SUCCESS_MESSAGE": "Exported successfully to:\n{file_path}",
                "NO_HISTORY_DATA_MESSAGE": "No history data available to export.",
                "EXPORT_ERROR_MESSAGE": "Failed to export data:\n{error}",
                "LOG_COPY_ERROR_MESSAGE": "Could not copy log file:\n{error}",
                "DATA_RETENTION_ERROR": "Data Retention: {days} days (error calculating size)",
                "NO_LOG_MESSAGE": "The error log file does not exist or could not be found.",
                "LOG_EXPORT_SUCCESS_MESSAGE": "Error log exported successfully to:\n{file_path}",
                "PERMISSION_DENIED_MESSAGE": "Permission denied while exporting the log.",
                "LOG_EXPORT_ERROR_MESSAGE": "Failed to export the error log:\n{error}",
                "NO_INTERFACES_DETECTED": "No active network interfaces were detected.",
                "ERROR_UI_SETUP_FAILED": "Failed to set up the settings dialog: {error}",
                "ERROR_GETTING_SETTINGS": "Failed to retrieve current settings from UI.",
                "ERROR_SAVING_CONFIG": "Failed to save configuration file:\n{error}",
                "NO_APP_DATA_MESSAGE": "No application usage data available for this period.",
                "APP_USAGE_CONFIG_ERROR": "Error loading app usage: Configuration or data issue.",
                "GRAPH_DATA_ERROR": "Error displaying graph: Invalid data.",
                "GRAPH_INVALID_DATA_FORMAT": "Invalid data format for graph plotting.", # ADDED
                "GRAPH_UPDATE_ERROR_MESSAGE": "Error updating graph display: {error}", # ADDED

                # --- Labels ---
                "SPEED_GRAPH_TAB_LABEL": "Speed Graph",
                "APP_USAGE_TAB_LABEL": "App Usage",
                "DARK_MODE_LABEL": "Dark Mode",
                "LIVE_UPDATE_LABEL": "Live Update",
                "HISTORY_PERIOD_LABEL": "Timeline: {period}",
                "HISTORY_PERIOD_LABEL_NO_VALUE": "Timeline",
                "GRAPH_SETTINGS_LABEL": "Graph Settings",
                "DATA_RETENTION_LABEL_DAYS": "Data Retention: {days} day{plural}", # {plural} needs to be handled in code
                "REALTIME_LABEL": "Real-time", # Note: LIVE_UPDATE_LABEL also exists, choose one or differentiate
                "DATA_RETENTION_LABEL_NO_VALUE": "Data Retention",
                "DATA_RETENTION_LABEL_YEAR": "Data Retention: 1 Year (DB size: {size_mb:.1f} MB)",
                "LEGEND_POSITION_LABEL": "Legend Position",
                "TIME_LABEL": "Time",
                "SPEED_LABEL": "Speed ({unit})", # Unit will be appended by code
                "UPLOAD_LABEL": "Upload",
                "DOWNLOAD_LABEL": "Download",
                "FILTER_BY_LABEL": "Filter by:",
                "LAST_30_DAYS_LABEL": "Last 30 Days",
                "LAST_7_DAYS_LABEL": "Last 7 Days",
                "SESSION_LABEL": "Current Session",
                "GENERAL_SETTINGS_GROUP": "General",
                "UPDATE_RATE_GROUP_TITLE": "Update Rate",
                "UPDATE_INTERVAL_LABEL": "Update Interval:",
                "OPTIONS_GROUP_TITLE": "Options",
                "FONT_SETTINGS_GROUP_TITLE": "Font Settings",
                "DYNAMIC_UPDATE_RATE_LABEL": "Dynamic Update Rate",
                "START_WITH_WINDOWS_LABEL": "Start with Windows",
                "FREE_MOVE_LABEL": "Free Move (No Snapping)",
                "FONT_SIZE_LABEL": "Font Size:",
                "FONT_FAMILY_LABEL": "Font:",
                "FONT_WEIGHT_LABEL": "Weight:",
                "COLOR_CODING_GROUP": "Speed Color Coding",
                "ENABLE_COLOR_CODING_LABEL": "Enable Color Coding",
                "DEFAULT_COLOR_LABEL": "Default Color:",
                "HIGH_SPEED_THRESHOLD_LABEL": "High Speed:",
                "LOW_SPEED_THRESHOLD_LABEL": "Low Speed:",
                "HIGH_SPEED_COLOR_LABEL": "High Color:",
                "LOW_SPEED_COLOR_LABEL": "Low Color:",
                "MINI_GRAPH_SETTINGS_GROUP": "Mini Graph (Widget)",
                "ENABLE_GRAPH_LABEL": "Show Mini Graph",
                "GRAPH_NOTE_TEXT": "Note: Shows a small real-time graph inside the widget area.",
                "HISTORY_DURATION_LABEL": "Graph Timespan:",
                "GRAPH_OPACITY_LABEL": "Graph Opacity:",
                "UNITS_GROUP": "Speed Units",
                "SPEED_DISPLAY_MODE_LABEL": "Speed Display Mode",
                "SPEED_DISPLAY_MODE_AUTO": "Auto",
                "SPEED_DISPLAY_MODE_MBPS": "Mbps only",
                "DECIMAL_PLACES_LABEL": "Decimal Places",
                "TEXT_ALIGNMENT_LABEL": "Text Alignment",
                "FORCE_DECIMALS_LABEL": "Always Show Decimals",
                "ALIGN_LEFT": "Left",
                "ALIGN_CENTER": "Center",
                "ALIGN_RIGHT": "Right",
                "NETWORK_INTERFACES_GROUP": "Network Interfaces",
                "ALL_INTERFACES_LABEL": "Monitor All Interfaces",
                "NO_INTERFACES_FOUND": "No network interfaces detected.",
                "TROUBLESHOOTING_GROUP": "Troubleshooting",
                "LOG_FILES_FILTER": "Log Files",
                "ALL_FILES_FILTER": "All Files",
                "UPLOAD_ARROW": "\u2191",
                "DOWNLOAD_ARROW": "\u2193",

                # --- Tooltips ---
                "SHOW_GRAPH_SETTINGS_TOOLTIP": "Show graph settings panel",
                "HIDE_GRAPH_SETTINGS_TOOLTIP": "Hide graph settings panel",
                "DEFAULT_COLOR_TOOLTIP": "Select the default text color",
                "HIGH_SPEED_COLOR_TOOLTIP": "Select the color for high speeds",
                "LOW_SPEED_COLOR_TOOLTIP": "Select the color for low speeds",
                "EXPORT_ERROR_LOG_TOOLTIP": "Save a copy of the application error log",

                # --- Buttons and Menu Items ---
                "EXPORT_CSV_BUTTON": "Export History (CSV)",
                "EXPORT_GRAPH_IMAGE_BUTTON": "Save Graph (PNG)",
                "SETTINGS_MENU_ITEM": "&Settings",
                "SHOW_GRAPH_MENU_ITEM": "Show &Graph Window",
                "STARTUP_MENU_ITEM": "Run at Start&up",
                "PAUSE_MENU_ITEM": "&Pause",
                "RESUME_MENU_ITEM": "&Resume",
                "EXIT_MENU_ITEM": "E&xit",
                "SELECT_FONT_BUTTON": "Select",
                "EXPORT_ERROR_LOG_BUTTON": "Export Error Log",
                "SAVE_BUTTON": "&Save",
                "CANCEL_BUTTON": "&Cancel",
                "SMART_MODE_LABEL": "Smart Mode",

                # --- Units and Formatting ---
                "BPS_LABEL": "B/s",
                "BITS_LABEL": "bps",
                "KBPS_LABEL": "KB/s",
                "KBITS_LABEL": "Kbps",
                "MBPS_LABEL": "MB/s",
                "MBITS_LABEL": "Mbps",
                "MBPS_UNIT": "MB/s",
                "MBITS_UNIT": "Mbps",
                "GBPS_LABEL": "GB/s",
                "GBITS_LABEL": "Gbps",
                "BYTES_UNIT": "B",
                "KB_UNIT": "KB",
                "MB_UNIT": "MB",
                "GB_UNIT": "GB",
                "TB_UNIT": "TB",
                "PB_UNIT": "PB",
                "PLURAL_SUFFIX": "s", # For "day{s}"
                "SECONDS_LABEL": "Seconds",
                "MINUTES_LABEL": "Minutes",
                "HOURS_LABEL": "Hours",
                "DAYS_LABEL": "Days",
                "WEEKS_LABEL": "Weeks",
                "MONTHS_LABEL": "Months",
                "CSV_FILE_FILTER": "CSV Files (*.csv);;All Files (*.*)",
                "PNG_FILE_FILTER": "PNG Images (*.png);;All Files (*.*)", # ADDED
                "FONT_WEIGHT_THIN": "Thin", # ADDED
                "FONT_WEIGHT_EXTRALIGHT": "ExtraLight", # ADDED
                "FONT_WEIGHT_LIGHT": "Light", # ADDED
                "FONT_WEIGHT_NORMAL": "Normal", # ADDED
                "FONT_WEIGHT_MEDIUM": "Medium", # ADDED
                "FONT_WEIGHT_DEMIBOLD": "DemiBold", # ADDED (or SemiBold)
                "FONT_WEIGHT_BOLD": "Bold", # ADDED
                "FONT_WEIGHT_EXTRABOLD": "ExtraBold", # ADDED
                "FONT_WEIGHT_BLACK": "Black", # Existing, good
                # Templates expect NUMBERS for speed/total due to :.2f formatting
                "DEFAULT_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.2f} {max_up_unit}, \u2193{max_down:.2f} {max_down_unit} | Total: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "APP_USAGE_STATS_TEXT_TEMPLATE": "Total: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "GRAPH_TITLE_TEMPLATE": "Speed History ({period})", # ADDED
                "GRAPH_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.1f} {unit} | \u2193{max_down:.1f} {unit}", # ADDED
            },

            # ================= French (France) =================
            "fr_FR": {
                # --- Window and dialog titles ---
                "SETTINGS_WINDOW_TITLE": "Paramètres",
                "GRAPH_WINDOW_TITLE": "Historique des Vitesses Réseau",
                "EXPORT_CSV_TITLE": "Exporter l'Historique",
                "EXPORT_GRAPH_IMAGE_TITLE": "Enregistrer le Graphique (Image)",
                "EXPORT_ERROR_LOG_TITLE": "Exporter le Journal d'Erreurs",
                "SELECT_COLOR_TITLE": "Sélectionner une Couleur",
                "SELECT_FONT_TITLE": "Sélectionner une Police",
                "ERROR_TITLE": "Erreur",
                "WARNING_TITLE": "Avertissement",
                "SUCCESS_TITLE": "Succès",
                "INFORMATION_TITLE": "Information",
                "NO_LOG_TITLE": "Fichier Journal Introuvable",
                "ERROR_WINDOW_TITLE": "Erreur de l'Application",

                # --- Messages ---
                "NO_DATA_MESSAGE": "Aucune donnée disponible pour la période sélectionnée.",
                "GRAPH_ERROR_MESSAGE": "Échec de l'affichage de la fenêtre du graphique. Veuillez consulter les journaux.",
                "APP_USAGE_ERROR_MESSAGE": "Échec du chargement des données d'utilisation des applications. Veuillez consulter les journaux.",
                "SETTINGS_ERROR_MESSAGE": "Échec de l'application d'un ou plusieurs paramètres.",
                "SAVE_ERROR_MESSAGE": "Échec de l'enregistrement des paramètres :\n{error}",
                "SETUP_ERROR_MESSAGE": "Erreur critique lors de l'initialisation de l'interface utilisateur.",
                "COLOR_PICKER_ERROR_MESSAGE": "Impossible d'ouvrir le sélecteur de couleurs.",
                "FONT_SELECTOR_ERROR_MESSAGE": "Impossible d'ouvrir le sélecteur de polices.",
                "DEFAULT_TEXT": "N/A", # Or "Indisponible"
                "EXPORT_SUCCESS_MESSAGE": "Exportation réussie vers :\n{file_path}",
                "NO_HISTORY_DATA_MESSAGE": "Aucune donnée d'historique disponible pour l'exportation.",
                "EXPORT_ERROR_MESSAGE": "Échec de l'exportation des données :\n{error}",
                "LOG_COPY_ERROR_MESSAGE": "Impossible de copier le fichier journal :\n{error}",
                "DATA_RETENTION_ERROR": "Rétention des données : {days} jours (erreur de calcul de la taille)",
                "NO_LOG_MESSAGE": "Le fichier journal d'erreurs n'existe pas ou est introuvable.",
                "LOG_EXPORT_SUCCESS_MESSAGE": "Journal d'erreurs exporté avec succès vers :\n{file_path}",
                "PERMISSION_DENIED_MESSAGE": "Permission refusée lors de l'exportation du journal.",
                "LOG_EXPORT_ERROR_MESSAGE": "Échec de l'exportation du journal d'erreurs :\n{error}",
                "NO_INTERFACES_DETECTED": "Aucune interface réseau active n'a été détectée.",
                "ERROR_UI_SETUP_FAILED": "Échec de la configuration de la boîte de dialogue des paramètres : {error}",
                "ERROR_GETTING_SETTINGS": "Échec de la récupération des paramètres actuels depuis l'interface utilisateur.",
                "ERROR_SAVING_CONFIG": "Échec de l'enregistrement du fichier de configuration :\n{error}",
                "NO_APP_DATA_MESSAGE": "Aucune donnée d'utilisation par application disponible pour cette période.",
                "APP_USAGE_CONFIG_ERROR": "Erreur de chargement de l'utilisation des applications : Problème de configuration ou de données.",
                "GRAPH_DATA_ERROR": "Erreur d'affichage du graphique : Données invalides.",
                "GRAPH_INVALID_DATA_FORMAT": "Format de données invalide pour le tracé du graphique.",
                "GRAPH_UPDATE_ERROR_MESSAGE": "Erreur lors de la mise à jour de l'affichage du graphique : {error}",

                # --- Labels ---
                "SPEED_GRAPH_TAB_LABEL": "Graphique des Vitesses",
                "APP_USAGE_TAB_LABEL": "Utilisation par Application",
                "DARK_MODE_LABEL": "Mode Sombre",
                "LIVE_UPDATE_LABEL": "Mise à Jour en Direct",
                "HISTORY_PERIOD_LABEL": "Chronologie : {period}",
                "HISTORY_PERIOD_LABEL_NO_VALUE": "Chronologie",
                "GRAPH_SETTINGS_LABEL": "Paramètres du Graphique",
                "DATA_RETENTION_LABEL_DAYS": "Rétention des données : {days} jour{plural}",
                "REALTIME_LABEL": "Temps réel",
                "DATA_RETENTION_LABEL_NO_VALUE": "Rétention des Données",
                "DATA_RETENTION_LABEL_YEAR": "Rétention : 1 An (Taille BD : {size_mb:.1f} Mo)",
                "LEGEND_POSITION_LABEL": "Position de la Légende",
                "TIME_LABEL": "Temps",
                "SPEED_LABEL": "Vitesse ({unit})",
                "UPLOAD_LABEL": "Envoi",
                "DOWNLOAD_LABEL": "Réception",
                "FILTER_BY_LABEL": "Filtrer par :",
                "LAST_30_DAYS_LABEL": "30 Derniers Jours",
                "LAST_7_DAYS_LABEL": "7 Derniers Jours",
                "SESSION_LABEL": "Session Actuelle",
                "GENERAL_SETTINGS_GROUP": "Général",
                "UPDATE_RATE_GROUP_TITLE": "Fréquence de Mise à Jour",
                "UPDATE_INTERVAL_LABEL": "Intervalle de Mise à Jour :",
                "OPTIONS_GROUP_TITLE": "Options",
                "FONT_SETTINGS_GROUP_TITLE": "Paramètres de Police",
                "DYNAMIC_UPDATE_RATE_LABEL": "Taux de Mise à Jour Dynamique",
                "START_WITH_WINDOWS_LABEL": "Lancer avec Windows",
                "FREE_MOVE_LABEL": "Déplacement Libre (Pas de Collage)",
                "FONT_SIZE_LABEL": "Taille de Police :",
                "FONT_FAMILY_LABEL": "Police :",
                "FONT_WEIGHT_LABEL": "Graisse :",
                "COLOR_CODING_GROUP": "Codage Couleur des Vitesses",
                "ENABLE_COLOR_CODING_LABEL": "Activer Codage Couleur",
                "DEFAULT_COLOR_LABEL": "Couleur par Défaut :",
                "HIGH_SPEED_THRESHOLD_LABEL": "Vitesse Élevée :",
                "LOW_SPEED_THRESHOLD_LABEL": "Vitesse Basse :",
                "HIGH_SPEED_COLOR_LABEL": "Couleur Vitesse Élevée :",
                "LOW_SPEED_COLOR_LABEL": "Couleur Vitesse Basse :",
                "MINI_GRAPH_SETTINGS_GROUP": "Mini Graphique (Widget)",
                "ENABLE_GRAPH_LABEL": "Afficher Mini Graphique",
                "GRAPH_NOTE_TEXT": "Note : Affiche un petit graphique en temps réel dans la zone du widget.",
                "HISTORY_DURATION_LABEL": "Durée de l'Historique :",
                "GRAPH_OPACITY_LABEL": "Opacité du Graphique :",
                "UNITS_GROUP": "Unités de Vitesse",
                "SPEED_DISPLAY_MODE_LABEL": "Mode d'Affichage Vitesse",
                "SPEED_DISPLAY_MODE_AUTO": "Auto",
                "SPEED_DISPLAY_MODE_MBPS": "Mbit/s Seulement",
                "DECIMAL_PLACES_LABEL": "Décimales",
                "TEXT_ALIGNMENT_LABEL": "Alignement du Texte",
                "FORCE_DECIMALS_LABEL": "Toujours Afficher les Décimales",
                "ALIGN_LEFT": "Gauche",
                "ALIGN_CENTER": "Centre",
                "ALIGN_RIGHT": "Droite",
                "NETWORK_INTERFACES_GROUP": "Interfaces Réseau",
                "ALL_INTERFACES_LABEL": "Surveiller Toutes les Interfaces",
                "NO_INTERFACES_FOUND": "Aucune interface réseau détectée.",
                "TROUBLESHOOTING_GROUP": "Dépannage",
                "LOG_FILES_FILTER": "Fichiers Journaux",
                "ALL_FILES_FILTER": "Tous les Fichiers",
                "UPLOAD_ARROW": "\u2191",
                "DOWNLOAD_ARROW": "\u2193",

                # --- Tooltips ---
                "SHOW_GRAPH_SETTINGS_TOOLTIP": "Afficher le panneau des paramètres du graphique",
                "HIDE_GRAPH_SETTINGS_TOOLTIP": "Masquer le panneau des paramètres du graphique",
                "DEFAULT_COLOR_TOOLTIP": "Sélectionner la couleur de texte par défaut",
                "HIGH_SPEED_COLOR_TOOLTIP": "Sélectionner la couleur pour les vitesses élevées",
                "LOW_SPEED_COLOR_TOOLTIP": "Sélectionner la couleur pour les vitesses basses",
                "EXPORT_ERROR_LOG_TOOLTIP": "Enregistrer une copie du journal d'erreurs de l'application",

                # --- Buttons and Menu Items ---
                "EXPORT_CSV_BUTTON": "Exporter Historique (CSV)",
                "EXPORT_GRAPH_IMAGE_BUTTON": "Enregistrer Graphique (PNG)",
                "SETTINGS_MENU_ITEM": "&Paramètres",
                "SHOW_GRAPH_MENU_ITEM": "Afficher Fenêtre du &Graphique",
                "STARTUP_MENU_ITEM": "Lancer au Démarrage",
                "PAUSE_MENU_ITEM": "&Pause",
                "RESUME_MENU_ITEM": "&Reprendre",
                "EXIT_MENU_ITEM": "&Quitter",
                "SELECT_FONT_BUTTON": "Sélectionner",
                "EXPORT_ERROR_LOG_BUTTON": "Exporter Journal d'Erreurs",
                "SAVE_BUTTON": "&Enregistrer",
                "CANCEL_BUTTON": "A&nnuler",
                "SMART_MODE_LABEL": "Mode Intelligent",

                # --- Units and Formatting ---
                "BPS_LABEL": "o/s",       # octets par seconde
                "BITS_LABEL": "bit/s",    # bits par seconde
                "KBPS_LABEL": "Ko/s",
                "KBITS_LABEL": "Kbit/s",  # Kilobits par seconde
                "MBPS_LABEL": "Mo/s",
                "MBITS_LABEL": "Mbit/s",  # Mégabits par seconde
                "MBPS_UNIT": "Mo/s",
                "MBITS_UNIT": "Mbit/s",
                "GBPS_LABEL": "Go/s",
                "GBITS_LABEL": "Gbit/s",  # Gigabits par seconde
                "BYTES_UNIT": "o",        # octet
                "KB_UNIT": "Ko",
                "MB_UNIT": "Mo",
                "GB_UNIT": "Go",
                "TB_UNIT": "To",
                "PB_UNIT": "Po",
                "PLURAL_SUFFIX": "s",
                "SECONDS_LABEL": "Secondes",
                "MINUTES_LABEL": "Minutes",
                "HOURS_LABEL": "Heures",
                "DAYS_LABEL": "Jours",
                "WEEKS_LABEL": "Semaines",
                "MONTHS_LABEL": "Mois",
                "CSV_FILE_FILTER": "Fichiers CSV (*.csv);;Tous les Fichiers (*.*)", # Changed
                "PNG_FILE_FILTER": "Images PNG (*.png);;Tous les Fichiers (*.*)", # ADDED
                "FONT_WEIGHT_THIN": "Fin", # ADDED
                "FONT_WEIGHT_EXTRALIGHT": "Extra-Léger", # ADDED
                "FONT_WEIGHT_LIGHT": "Léger", # ADDED
                "FONT_WEIGHT_NORMAL": "Normal", # ADDED
                "FONT_WEIGHT_MEDIUM": "Moyen", # ADDED
                "FONT_WEIGHT_DEMIBOLD": "Demi-Gras", # ADDED
                "FONT_WEIGHT_BOLD": "Gras", # ADDED
                "FONT_WEIGHT_EXTRABOLD": "Extra-Gras", # ADDED
                "FONT_WEIGHT_BLACK": "Noir", # Existing
                # Templates
                "DEFAULT_STATS_TEXT_TEMPLATE": "Max : \u2191{max_up:.2f} {max_up_unit}, \u2193{max_down:.2f} {max_down_unit} | Total : \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "APP_USAGE_STATS_TEXT_TEMPLATE": "Total : \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "GRAPH_TITLE_TEMPLATE": "Historique des Vitesses ({period})", # ADDED
                "GRAPH_STATS_TEXT_TEMPLATE": "Max : \u2191{max_up:.1f} {unit} | \u2193{max_down:.1f} {unit}", # ADDED
            },
            # Add other languages here
        }

        # --- Determine and set language ---
        try:
            default_locale = locale.getdefaultlocale()
            language_code = default_locale[0].replace('-', '_') if default_locale and default_locale[0] else "en_US"
            self.language = language_code # Initial detected language
            # logger.debug(f"Detected system locale: {language_code}") # Can be verbose
        except Exception as e:
            logger.warning(f"Failed to get default locale: {e}, falling back to en_US.")
            self.language = "en_US"

        # Fallback logic
        effective_language = "en_US"
        if self.language in self._strings:
            effective_language = self.language
        else:
            base_language = self.language.split('_')[0]
            # logger.debug(f"Locale language '{self.language}' not directly supported. Checking base '{base_language}'.")
            found_base_variant = False
            for supported_lang in self._strings.keys():
                if supported_lang.startswith(base_language + '_'):
                    effective_language = supported_lang
                    # logger.debug(f"Falling back to supported language variant: {effective_language}")
                    found_base_variant = True
                    break
            # if not found_base_variant: # No need for else, effective_language remains en_US
                # logger.debug(f"No variant of base language '{base_language}' found. Defaulting to en_US.")
        
        self.language = effective_language # Set final effective language
        logger.info(f"I18nStrings initialized. Effective language: {self.language}") # Use INFO for this important step

        try:
            self.validate()
        except ValueError as e:
            logger.error(f"I18n validation failed on initialization: {e}")


    def __getattr__(self, name: str) -> str:
        """
        Override attribute access for missing attributes to look up translation strings.
        (Docstring from your file, remains excellent)
        """
        try:
            _strings_internal = object.__getattribute__(self, "_strings")
            language_internal = object.__getattribute__(self, "language")
        except AttributeError:
            # This should ideally not happen if __init__ completes.
            # If it does, it indicates a very early access before i18n is fully set up.
            logger.error(f"I18n internal attributes (_strings, language) not yet set when looking for '{name}'.")
            raise AttributeError(f"Attribute '{name}' not found and i18n internals not ready.")

        try:
            current_lang_dict = _strings_internal.get(language_internal)
            if current_lang_dict is None:
                logger.error(f"Internal Error: Language dictionary for '{language_internal}' is missing. Using en_US for '{name}'.")
                current_lang_dict = _strings_internal.get("en_US", {}) # Fallback to en_US dict

            value = current_lang_dict.get(name)
            if value is None:
                # String not found in the current language, try fallback
                if language_internal != "en_US": # Avoid redundant logging if already trying en_US
                    logger.warning(f"String constant '{name}' not found in language '{language_internal}'. Attempting en_US fallback.")
                
                fallback_dict = _strings_internal.get("en_US", {})
                value = fallback_dict.get(name)
                
                if value is None:
                    logger.critical(f"String constant '{name}' not found in fallback language 'en_US'.")
                    raise AttributeError(f"String constant '{name}' is missing from all language definitions.")
                # else: # Successful fallback
                    # logger.debug(f"String constant '{name}' used en_US fallback for language '{language_internal}'.")

            if not isinstance(value, str):
                logger.error(f"Value for '{name}' in language '{language_internal}' is not a string (type: {type(value)}). Fallbacking to default.")
                # Provide a very generic fallback if the type is wrong.
                return f"[ERR: TYPE {name}]" 
            return value
        except AttributeError:
            raise # Re-raise the AttributeError from the critical "missing from all" case
        except Exception as e:
            logger.critical(f"Unexpected error retrieving string '{name}' for language '{language_internal}': {e}", exc_info=True)
            # Fallback to a clearly problematic string to indicate failure
            return f"[ERR: LOOKUP {name}]"


    def set_language(self, language: str) -> None:
        """
        Set the current language dynamically.
        (Docstring from your file)
        """
        normalized_language = language.replace('-', '_')
        if normalized_language not in self._strings:
            logger.error(f"Attempted to set unsupported language: {language} (Normalized: {normalized_language})")
            # Optionally, could fall back to en_US instead of raising, depending on desired behavior.
            # For now, raising ValueError is fine as it indicates a programming error or unsupported request.
            raise ValueError(f"Language '{language}' not supported.")
        
        if self.language != normalized_language:
            self.language = normalized_language
            logger.info(f"Language dynamically set to: {self.language}")
            # If UI elements need to be re-translated, a signal or callback mechanism would be needed here.
            # e.g., self.language_changed_signal.emit(self.language)

    def validate(self) -> None:
        """
        Validate that all string constants are non-empty for all defined languages,
        comparing against the 'en_US' set as the master reference.
        (Docstring from your file)
        """
        logger.debug("Validating all I18n strings...")
        if "en_US" not in self._strings or not isinstance(self._strings["en_US"], dict) or not self._strings["en_US"]:
            logger.error("en_US language strings are missing, empty, or not a dictionary. Cannot perform comprehensive validation.")
            # Perform basic validation for other existing languages
            for lang, translations in self._strings.items():
                if not isinstance(translations, dict):
                     raise ValueError(f"Entry for language '{lang}' is not a dictionary.")
                for key, value in translations.items():
                    if not isinstance(key, str) or not key:
                         raise ValueError(f"Invalid or empty key found in language '{lang}'. Key: '{key}'")
                    if not isinstance(value, str) or not value: # Check for non-string or empty string
                        raise ValueError(f"Invalid or empty string value for key '{key}' in language '{lang}'. Value: '{value}'")
            logger.warning("Validation performed basic checks due to missing/invalid en_US base.")
            return

        master_keys = set(self._strings["en_US"].keys())
        if not all(isinstance(k, str) and k for k in master_keys):
            # This should ideally be caught by the basic validation above if en_US itself has bad keys
            raise ValueError("Found invalid or empty keys in the master en_US dictionary.")

        validation_errors = []
        for lang_code, translations_dict in self._strings.items():
            if not isinstance(translations_dict, dict):
                 validation_errors.append(f"Language entry '{lang_code}' is not a dictionary.")
                 continue

            current_lang_keys = set(translations_dict.keys())
            
            # Check for malformed keys in the current language
            if not all(isinstance(k, str) and k for k in current_lang_keys):
                 malformed = [k for k in current_lang_keys if not (isinstance(k, str) and k)]
                 validation_errors.append(f"Language '{lang_code}' contains invalid or empty keys: {malformed}")

            # Compare keys with master (en_US)
            missing_keys = master_keys - current_lang_keys
            if missing_keys:
                validation_errors.append(f"Language '{lang_code}' is missing keys defined in en_US: {sorted(list(missing_keys))}")

            if lang_code != "en_US": # Only warn for extra keys in non-master languages
                extra_keys = current_lang_keys - master_keys
                if extra_keys:
                    logger.warning(f"Language '{lang_code}' has extra keys not present in en_US: {sorted(list(extra_keys))}")

            # Validate string values
            for key, value_str in translations_dict.items():
                if not isinstance(value_str, str):
                    validation_errors.append(f"Value for key '{key}' in language '{lang_code}' is not a string (type: {type(value_str)}).")
                elif not value_str.strip() and value_str != "": # Allow empty strings if explicitly set, but warn/error on just whitespace
                    # If empty strings are disallowed for specific keys, that's a higher-level validation.
                    # For now, just ensuring it's a string. If you want to disallow empty strings:
                    # elif not value_str:
                    #    validation_errors.append(f"Value for key '{key}' in language '{lang_code}' is empty.")
                    pass # Allow empty strings for now, but not strings with only whitespace if that's an issue


        if validation_errors:
            error_summary = "I18n string validation failed with {} error(s):\n- ".format(len(validation_errors)) + "\n- ".join(validation_errors)
            logger.error(error_summary)
            raise ValueError(error_summary)
        else:
            logger.debug("All I18n strings validated successfully against en_US keys.")