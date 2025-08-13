"""
Internationalization strings for the NetSpeedTray application.

This module defines user-facing strings that may need to be translated. It
provides a singleton instance `strings` which detects the system locale and
provides translated strings with a fallback to English (en_US).
"""

import logging
import locale
from typing import Dict, Any

# Define logger at the module level
logger = logging.getLogger("NetSpeedTray.I18n")

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
                "COLLECTING_DATA_MESSAGE": "Collecting data for current session...",
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
                "GRAPH_INVALID_DATA_FORMAT": "Invalid data format for graph plotting.",
                "GRAPH_UPDATE_ERROR_MESSAGE": "Error updating graph display: {error}",

                # --- Labels ---
                "SPEED_GRAPH_TAB_LABEL": "Speed Graph",
                "APP_USAGE_TAB_LABEL": "App Usage",
                "DARK_MODE_LABEL": "Dark Mode",
                "LIVE_UPDATE_LABEL": "Live Update",
                "HISTORY_PERIOD_LABEL": "Timeline: {period}",
                "HISTORY_PERIOD_LABEL_NO_VALUE": "Timeline",
                "GRAPH_SETTINGS_LABEL": "Graph Settings",
                "DATA_RETENTION_LABEL_DAYS": "Data Retention: {days} day{plural}",
                "REALTIME_LABEL": "Real-time",
                "DATA_RETENTION_LABEL_NO_VALUE": "Data Retention",
                "DATA_RETENTION_LABEL_YEAR": "Data Retention: 1 Year (DB size: {size_mb:.1f} MB)",
                "LEGEND_POSITION_LABEL": "Legend Position",
                "TIME_LABEL": "Time",
                "SPEED_LABEL": "Speed ({unit})",
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
                "PLURAL_SUFFIX": "s",
                "SECONDS_LABEL": "Seconds",
                "MINUTES_LABEL": "Minutes",
                "HOURS_LABEL": "Hours",
                "DAYS_LABEL": "Days",
                "WEEKS_LABEL": "Weeks",
                "MONTHS_LABEL": "Months",
                "CSV_FILE_FILTER": "CSV Files (*.csv);;All Files (*.*)",
                "PNG_FILE_FILTER": "PNG Images (*.png);;All Files (*.*)",
                "FONT_WEIGHT_THIN": "Thin",
                "FONT_WEIGHT_EXTRALIGHT": "ExtraLight",
                "FONT_WEIGHT_LIGHT": "Light",
                "FONT_WEIGHT_NORMAL": "Normal",
                "FONT_WEIGHT_MEDIUM": "Medium",
                "FONT_WEIGHT_DEMIBOLD": "DemiBold",
                "FONT_WEIGHT_BOLD": "Bold",
                "FONT_WEIGHT_EXTRABOLD": "ExtraBold",
                "FONT_WEIGHT_BLACK": "Black",
                "DEFAULT_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.2f} {max_up_unit}, \u2193{max_down:.2f} {max_down_unit} | Total: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "APP_USAGE_STATS_TEXT_TEMPLATE": "Total: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "GRAPH_TITLE_TEMPLATE": "Speed History ({period})",
                "GRAPH_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.1f} {unit} | \u2193{max_down:.1f} {unit}",
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
                "COLLECTING_DATA_MESSAGE": "Collecte des données pour la session en cours...",
                "GRAPH_ERROR_MESSAGE": "Échec de l'affichage de la fenêtre du graphique. Veuillez consulter les journaux.",
                "APP_USAGE_ERROR_MESSAGE": "Échec du chargement des données d'utilisation des applications. Veuillez consulter les journaux.",
                "SETTINGS_ERROR_MESSAGE": "Échec de l'application d'un ou plusieurs paramètres.",
                "SAVE_ERROR_MESSAGE": "Échec de l'enregistrement des paramètres :\n{error}",
                "SETUP_ERROR_MESSAGE": "Erreur critique lors de l'initialisation de l'interface utilisateur.",
                "COLOR_PICKER_ERROR_MESSAGE": "Impossible d'ouvrir le sélecteur de couleurs.",
                "FONT_SELECTOR_ERROR_MESSAGE": "Impossible d'ouvrir le sélecteur de polices.",
                "DEFAULT_TEXT": "N/A",
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
                "BPS_LABEL": "o/s",
                "BITS_LABEL": "bit/s",
                "KBPS_LABEL": "Ko/s",
                "KBITS_LABEL": "Kbit/s",
                "MBPS_LABEL": "Mo/s",
                "MBITS_LABEL": "Mbit/s",
                "MBPS_UNIT": "Mo/s",
                "MBITS_UNIT": "Mbit/s",
                "GBPS_LABEL": "Go/s",
                "GBITS_LABEL": "Gbit/s",
                "BYTES_UNIT": "o",
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
                "CSV_FILE_FILTER": "Fichiers CSV (*.csv);;Tous les Fichiers (*.*)",
                "PNG_FILE_FILTER": "Images PNG (*.png);;Tous les Fichiers (*.*)",
                "FONT_WEIGHT_THIN": "Fin",
                "FONT_WEIGHT_EXTRALIGHT": "Extra-Léger",
                "FONT_WEIGHT_LIGHT": "Léger",
                "FONT_WEIGHT_NORMAL": "Normal",
                "FONT_WEIGHT_MEDIUM": "Moyen",
                "FONT_WEIGHT_DEMIBOLD": "Demi-Gras",
                "FONT_WEIGHT_BOLD": "Gras",
                "FONT_WEIGHT_EXTRABOLD": "Extra-Gras",
                "FONT_WEIGHT_BLACK": "Noir",
                "DEFAULT_STATS_TEXT_TEMPLATE": "Max : \u2191{max_up:.2f} {max_up_unit}, \u2193{max_down:.2f} {max_down_unit} | Total : \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "APP_USAGE_STATS_TEXT_TEMPLATE": "Total : \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "GRAPH_TITLE_TEMPLATE": "Historique des Vitesses ({period})",
                "GRAPH_STATS_TEXT_TEMPLATE": "Max : \u2191{max_up:.1f} {unit} | \u2193{max_down:.1f} {unit}",
            },

                        # ================= German (Germany) =================
            "de_DE": {
                # --- Window and dialog titles ---
                "SETTINGS_WINDOW_TITLE": "Einstellungen",
                "GRAPH_WINDOW_TITLE": "Verlauf der Netzwerkgeschwindigkeit",
                "EXPORT_CSV_TITLE": "Verlauf exportieren",
                "EXPORT_GRAPH_IMAGE_TITLE": "Grafik speichern (Bild)",
                "EXPORT_ERROR_LOG_TITLE": "Fehlerprotokoll exportieren",
                "SELECT_COLOR_TITLE": "Farbe auswählen",
                "SELECT_FONT_TITLE": "Schriftart auswählen",
                "ERROR_TITLE": "Fehler",
                "WARNING_TITLE": "Warnung",
                "SUCCESS_TITLE": "Erfolg",
                "INFORMATION_TITLE": "Information",
                "NO_LOG_TITLE": "Protokolldatei nicht gefunden",
                "ERROR_WINDOW_TITLE": "Anwendungsfehler",

                # --- Messages ---
                "NO_DATA_MESSAGE": "Keine Daten für den ausgewählten Zeitraum verfügbar.",
                "COLLECTING_DATA_MESSAGE": "Daten für die aktuelle Sitzung werden gesammelt...",
                "GRAPH_ERROR_MESSAGE": "Anzeige des Grafikfensters fehlgeschlagen. Details im Protokoll.",
                "APP_USAGE_ERROR_MESSAGE": "Laden der App-Nutzungsdaten fehlgeschlagen. Details im Protokoll.",
                "SETTINGS_ERROR_MESSAGE": "Anwenden einer oder mehrerer Einstellungen fehlgeschlagen.",
                "SAVE_ERROR_MESSAGE": "Speichern der Einstellungen fehlgeschlagen:\n{error}",
                "SETUP_ERROR_MESSAGE": "Kritischer Fehler beim UI-Setup.",
                "COLOR_PICKER_ERROR_MESSAGE": "Farbauswahl konnte nicht geöffnet werden.",
                "FONT_SELECTOR_ERROR_MESSAGE": "Schriftauswahl konnte nicht geöffnet werden.",
                "DEFAULT_TEXT": "N/A",
                "EXPORT_SUCCESS_MESSAGE": "Erfolgreich exportiert nach:\n{file_path}",
                "NO_HISTORY_DATA_MESSAGE": "Keine Verlaufsdaten zum Exportieren vorhanden.",
                "EXPORT_ERROR_MESSAGE": "Export der Daten fehlgeschlagen:\n{error}",
                "LOG_COPY_ERROR_MESSAGE": "Kopieren der Protokolldatei fehlgeschlagen:\n{error}",
                "DATA_RETENTION_ERROR": "Datenaufbewahrung: {days} Tage (Fehler bei Größenberechnung)",
                "NO_LOG_MESSAGE": "Die Fehlerprotokolldatei existiert nicht oder wurde nicht gefunden.",
                "LOG_EXPORT_SUCCESS_MESSAGE": "Fehlerprotokoll erfolgreich exportiert nach:\n{file_path}",
                "PERMISSION_DENIED_MESSAGE": "Berechtigung beim Exportieren des Protokolls verweigert.",
                "LOG_EXPORT_ERROR_MESSAGE": "Export des Fehlerprotokolls fehlgeschlagen:\n{error}",
                "NO_INTERFACES_DETECTED": "Keine aktiven Netzwerkschnittstellen erkannt.",
                "ERROR_UI_SETUP_FAILED": "Einrichten des Einstellungsdialogs fehlgeschlagen: {error}",
                "ERROR_GETTING_SETTINGS": "Abrufen der aktuellen Einstellungen von der UI fehlgeschlagen.",
                "ERROR_SAVING_CONFIG": "Speichern der Konfigurationsdatei fehlgeschlagen:\n{error}",
                "NO_APP_DATA_MESSAGE": "Keine Anwendungsnutzungsdaten für diesen Zeitraum verfügbar.",
                "APP_USAGE_CONFIG_ERROR": "Fehler beim Laden der App-Nutzung: Konfigurations- oder Datenproblem.",
                "GRAPH_DATA_ERROR": "Fehler bei der Grafikanzeige: Ungültige Daten.",
                "GRAPH_INVALID_DATA_FORMAT": "Ungültiges Datenformat zum Plotten der Grafik.",
                "GRAPH_UPDATE_ERROR_MESSAGE": "Fehler beim Aktualisieren der Grafikanzeige: {error}",

                # --- Labels ---
                "SPEED_GRAPH_TAB_LABEL": "Geschwindigkeitsgrafik",
                "APP_USAGE_TAB_LABEL": "App-Nutzung",
                "DARK_MODE_LABEL": "Dunkelmodus",
                "LIVE_UPDATE_LABEL": "Live-Aktualisierung",
                "HISTORY_PERIOD_LABEL": "Zeitachse: {period}",
                "HISTORY_PERIOD_LABEL_NO_VALUE": "Zeitachse",
                "GRAPH_SETTINGS_LABEL": "Grafik-Einstellungen",
                "DATA_RETENTION_LABEL_DAYS": "Datenaufbewahrung: {days} Tag{plural}",
                "REALTIME_LABEL": "Echtzeit",
                "DATA_RETENTION_LABEL_NO_VALUE": "Datenaufbewahrung",
                "DATA_RETENTION_LABEL_YEAR": "Aufbewahrung: 1 Jahr (DB-Größe: {size_mb:.1f} MB)",
                "LEGEND_POSITION_LABEL": "Position der Legende",
                "TIME_LABEL": "Zeit",
                "SPEED_LABEL": "Geschwindigkeit ({unit})",
                "UPLOAD_LABEL": "Upload",
                "DOWNLOAD_LABEL": "Download",
                "FILTER_BY_LABEL": "Filtern nach:",
                "LAST_30_DAYS_LABEL": "Letzte 30 Tage",
                "LAST_7_DAYS_LABEL": "Letzte 7 Tage",
                "SESSION_LABEL": "Aktuelle Sitzung",
                "GENERAL_SETTINGS_GROUP": "Allgemein",
                "UPDATE_RATE_GROUP_TITLE": "Aktualisierungsrate",
                "UPDATE_INTERVAL_LABEL": "Aktualisierungsintervall:",
                "OPTIONS_GROUP_TITLE": "Optionen",
                "FONT_SETTINGS_GROUP_TITLE": "Schrift-Einstellungen",
                "DYNAMIC_UPDATE_RATE_LABEL": "Dynamische Aktualisierungsrate",
                "START_WITH_WINDOWS_LABEL": "Mit Windows starten",
                "FREE_MOVE_LABEL": "Frei bewegen (Kein Einrasten)",
                "FONT_SIZE_LABEL": "Schriftgröße:",
                "FONT_FAMILY_LABEL": "Schriftart:",
                "FONT_WEIGHT_LABEL": "Schriftstärke:",
                "COLOR_CODING_GROUP": "Farbkodierung der Geschwindigkeit",
                "ENABLE_COLOR_CODING_LABEL": "Farbkodierung aktivieren",
                "DEFAULT_COLOR_LABEL": "Standardfarbe:",
                "HIGH_SPEED_THRESHOLD_LABEL": "Hohe Geschwindigkeit:",
                "LOW_SPEED_THRESHOLD_LABEL": "Niedrige Geschwindigkeit:",
                "HIGH_SPEED_COLOR_LABEL": "Farbe (Hoch):",
                "LOW_SPEED_COLOR_LABEL": "Farbe (Niedrig):",
                "MINI_GRAPH_SETTINGS_GROUP": "Mini-Grafik (Widget)",
                "ENABLE_GRAPH_LABEL": "Mini-Grafik anzeigen",
                "GRAPH_NOTE_TEXT": "Hinweis: Zeigt eine kleine Echtzeit-Grafik im Widget-Bereich.",
                "HISTORY_DURATION_LABEL": "Grafik-Zeitspanne:",
                "GRAPH_OPACITY_LABEL": "Grafik-Deckkraft:",
                "UNITS_GROUP": "Einheiten",
                "SPEED_DISPLAY_MODE_LABEL": "Anzeigemodus",
                "SPEED_DISPLAY_MODE_AUTO": "Automatisch",
                "SPEED_DISPLAY_MODE_MBPS": "Nur Mbit/s",
                "DECIMAL_PLACES_LABEL": "Dezimalstellen",
                "TEXT_ALIGNMENT_LABEL": "Textausrichtung",
                "FORCE_DECIMALS_LABEL": "Dezimalstellen immer anzeigen",
                "ALIGN_LEFT": "Links",
                "ALIGN_CENTER": "Mitte",
                "ALIGN_RIGHT": "Rechts",
                "NETWORK_INTERFACES_GROUP": "Netzwerkschnittstellen",
                "ALL_INTERFACES_LABEL": "Alle Schnittstellen überwachen",
                "NO_INTERFACES_FOUND": "Keine Netzwerkschnittstellen erkannt.",
                "TROUBLESHOOTING_GROUP": "Fehlerbehebung",
                "LOG_FILES_FILTER": "Protokolldateien",
                "ALL_FILES_FILTER": "Alle Dateien",
                "UPLOAD_ARROW": "\u2191",
                "DOWNLOAD_ARROW": "\u2193",

                # --- Tooltips ---
                "SHOW_GRAPH_SETTINGS_TOOLTIP": "Grafik-Einstellungen anzeigen",
                "HIDE_GRAPH_SETTINGS_TOOLTIP": "Grafik-Einstellungen ausblenden",
                "DEFAULT_COLOR_TOOLTIP": "Standard-Textfarbe auswählen",
                "HIGH_SPEED_COLOR_TOOLTIP": "Farbe für hohe Geschwindigkeiten auswählen",
                "LOW_SPEED_COLOR_TOOLTIP": "Farbe für niedrige Geschwindigkeiten auswählen",
                "EXPORT_ERROR_LOG_TOOLTIP": "Eine Kopie des Anwendungs-Fehlerprotokolls speichern",

                # --- Buttons and Menu Items ---
                "EXPORT_CSV_BUTTON": "Verlauf exportieren (CSV)",
                "EXPORT_GRAPH_IMAGE_BUTTON": "Grafik speichern (PNG)",
                "SETTINGS_MENU_ITEM": "&Einstellungen",
                "SHOW_GRAPH_MENU_ITEM": "&Grafikfenster anzeigen",
                "STARTUP_MENU_ITEM": "Beim Start ausführe&n",
                "PAUSE_MENU_ITEM": "&Pause",
                "RESUME_MENU_ITEM": "&Fortsetzen",
                "EXIT_MENU_ITEM": "&Beenden",
                "SELECT_FONT_BUTTON": "Auswählen",
                "EXPORT_ERROR_LOG_BUTTON": "Fehlerprotokoll exportieren",
                "SAVE_BUTTON": "&Speichern",
                "CANCEL_BUTTON": "&Abbrechen",
                "SMART_MODE_LABEL": "Intelligenter Modus",

                # --- Units and Formatting ---
                "BPS_LABEL": "B/s",
                "BITS_LABEL": "bit/s",
                "KBPS_LABEL": "KB/s",
                "KBITS_LABEL": "kbit/s",
                "MBPS_LABEL": "MB/s",
                "MBITS_LABEL": "Mbit/s",
                "MBPS_UNIT": "MB/s",
                "MBITS_UNIT": "Mbit/s",
                "GBPS_LABEL": "GB/s",
                "GBITS_LABEL": "Gbit/s",
                "BYTES_UNIT": "B",
                "KB_UNIT": "KB",
                "MB_UNIT": "MB",
                "GB_UNIT": "GB",
                "TB_UNIT": "TB",
                "PB_UNIT": "PB",
                "PLURAL_SUFFIX": "e", # For "Tage" (Days)
                "SECONDS_LABEL": "Sekunden",
                "MINUTES_LABEL": "Minuten",
                "HOURS_LABEL": "Stunden",
                "DAYS_LABEL": "Tage",
                "WEEKS_LABEL": "Wochen",
                "MONTHS_LABEL": "Monate",
                "CSV_FILE_FILTER": "CSV-Dateien (*.csv);;Alle Dateien (*.*)",
                "PNG_FILE_FILTER": "PNG-Bilder (*.png);;Alle Dateien (*.*)",
                "FONT_WEIGHT_THIN": "Dünn",
                "FONT_WEIGHT_EXTRALIGHT": "Extra-Leicht",
                "FONT_WEIGHT_LIGHT": "Leicht",
                "FONT_WEIGHT_NORMAL": "Normal",
                "FONT_WEIGHT_MEDIUM": "Mittel",
                "FONT_WEIGHT_DEMIBOLD": "Halbfett",
                "FONT_WEIGHT_BOLD": "Fett",
                "FONT_WEIGHT_EXTRABOLD": "Extra-Fett",
                "FONT_WEIGHT_BLACK": "Schwarz",
                "DEFAULT_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.2f} {max_up_unit}, \u2193{max_down:.2f} {max_down_unit} | Gesamt: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "APP_USAGE_STATS_TEXT_TEMPLATE": "Gesamt: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "GRAPH_TITLE_TEMPLATE": "Geschwindigkeitsverlauf ({period})",
                "GRAPH_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.1f} {unit} | \u2193{max_down:.1f} {unit}",
            },

                        # ================= Dutch (Netherlands) 2025.08.13 =================
            "nl_NL": {
                # --- Window and dialog titles ---
                "SETTINGS_WINDOW_TITLE": "Instellingen",
                "GRAPH_WINDOW_TITLE": "Netwerksnelheid geschiedenis",
                "EXPORT_CSV_TITLE": "Geschiedenis exporteren",
                "EXPORT_GRAPH_IMAGE_TITLE": "Grafiek afbeelding opslaan",
                "EXPORT_ERROR_LOG_TITLE": "Foutenlogboek exporteren",
                "SELECT_COLOR_TITLE": "Kleur selecteren",
                "SELECT_FONT_TITLE": "Lettertype selecteren",
                "ERROR_TITLE": "Fout",
                "WARNING_TITLE": "Waarschuwing",
                "SUCCESS_TITLE": "Succes",
                "INFORMATION_TITLE": "Informatie",
                "NO_LOG_TITLE": "Logboek bestand niet gevonden",
                "ERROR_WINDOW_TITLE": "Applicatiefout",

                # --- Messages ---
                "NO_DATA_MESSAGE": "Geen gegevens beschikbaar voor de geselecteerde periode.",
                "COLLECTING_DATA_MESSAGE": "Gegevens verzamelen voor huidige sessie...",
                "GRAPH_ERROR_MESSAGE": "Kan grafiekvenster niet tonen. Controleer logbestanden voor details.",
                "APP_USAGE_ERROR_MESSAGE": "Kan app-gebruiksgegevens niet laden. Controleer logbestanden voor details.",
                "SETTINGS_ERROR_MESSAGE": "Kon een of meer instellingen niet toepassen.",
                "SAVE_ERROR_MESSAGE": "Kon instellingen niet opslaan:\n{error}",
                "SETUP_ERROR_MESSAGE": "Kritieke fout tijdens UI-instelling.",
                "COLOR_PICKER_ERROR_MESSAGE": "Kon kleurenkiezer niet openen.",
                "FONT_SELECTOR_ERROR_MESSAGE": "Kon lettertype selector niet openen.",
                "DEFAULT_TEXT": "N/B",
                "EXPORT_SUCCESS_MESSAGE": "Succesvol geëxporteerd naar:\n{file_path}",
                "NO_HISTORY_DATA_MESSAGE": "Geen geschiedenisgegevens beschikbaar om te exporteren.",
                "EXPORT_ERROR_MESSAGE": "Kon gegevens niet exporteren:\n{error}",
                "LOG_COPY_ERROR_MESSAGE": "Kon logbestand niet kopiëren:\n{error}",
                "DATA_RETENTION_ERROR": "Gegevensbewaring: {days} dagen (fout bij berekening grootte)",
                "NO_LOG_MESSAGE": "Het foutenlogboek bestaat niet of kon niet worden gevonden.",
                "LOG_EXPORT_SUCCESS_MESSAGE": "Foutenlogboek succesvol geëxporteerd naar:\n{file_path}",
                "PERMISSION_DENIED_MESSAGE": "Toestemming geweigerd tijdens exporteren van logboek.",
                "LOG_EXPORT_ERROR_MESSAGE": "Kon foutenlogboek niet exporteren:\n{error}",
                "NO_INTERFACES_DETECTED": "Geen actieve netwerkinterfaces gedetecteerd.",
                "ERROR_UI_SETUP_FAILED": "Kon instellingenvenster niet opzetten: {error}",
                "ERROR_GETTING_SETTINGS": "Kon huidige instellingen niet ophalen uit UI.",
                "ERROR_SAVING_CONFIG": "Kon configuratiebestand niet opslaan:\n{error}",
                "NO_APP_DATA_MESSAGE": "Geen applicatiegebruiksgegevens beschikbaar voor deze periode.",
                "APP_USAGE_CONFIG_ERROR": "Fout bij laden app-gebruik: configuratie- of gegevensprobleem.",
                "GRAPH_DATA_ERROR": "Fout bij weergeven grafiek: ongeldige gegevens.",
                "GRAPH_INVALID_DATA_FORMAT": "Ongeldig gegevensformaat voor grafiekweergave.",
                "GRAPH_UPDATE_ERROR_MESSAGE": "Fout bij bijwerken grafiekweergave: {error}",

                # --- Labels ---
                "SPEED_GRAPH_TAB_LABEL": "Snelheidsgrafiek",
                "APP_USAGE_TAB_LABEL": "App gebruik",
                "DARK_MODE_LABEL": "Donkere modus",
                "LIVE_UPDATE_LABEL": "Live bijwerken",
                "HISTORY_PERIOD_LABEL": "Tijdlijn: {period}",
                "HISTORY_PERIOD_LABEL_NO_VALUE": "Tijdlijn",
                "GRAPH_SETTINGS_LABEL": "Grafiekinstellingen",
                "DATA_RETENTION_LABEL_DAYS": "Gegevensbewaring: {days} dag{plural}",
                "REALTIME_LABEL": "Real-time",
                "DATA_RETENTION_LABEL_NO_VALUE": "Gegevensbewaring",
                "DATA_RETENTION_LABEL_YEAR": "Gegevensbewaring: 1 jaar (DB grootte: {size_mb:.1f} MB)",
                "LEGEND_POSITION_LABEL": "Legenda positie",
                "TIME_LABEL": "Tijd",
                "SPEED_LABEL": "Snelheid ({unit})",
                "UPLOAD_LABEL": "Upload",
                "DOWNLOAD_LABEL": "Download",
                "FILTER_BY_LABEL": "Filteren op:",
                "LAST_30_DAYS_LABEL": "Laatste 30 dagen",
                "LAST_7_DAYS_LABEL": "Laatste 7 dagen",
                "SESSION_LABEL": "Huidige sessie",
                "GENERAL_SETTINGS_GROUP": "Algemeen",
                "UPDATE_RATE_GROUP_TITLE": "Update frequentie",
                "UPDATE_INTERVAL_LABEL": "Update interval:",
                "OPTIONS_GROUP_TITLE": "Opties",
                "FONT_SETTINGS_GROUP_TITLE": "Lettertype instellingen",
                "DYNAMIC_UPDATE_RATE_LABEL": "Dynamische update frequentie",
                "START_WITH_WINDOWS_LABEL": "Starten met Windows",
                "FREE_MOVE_LABEL": "Vrij bewegen (geen uitlijning)",
                "FONT_SIZE_LABEL": "Lettergrootte:",
                "FONT_FAMILY_LABEL": "Lettertype:",
                "FONT_WEIGHT_LABEL": "Dikte:",
                "COLOR_CODING_GROUP": "Snelheidskleurcodering",
                "ENABLE_COLOR_CODING_LABEL": "Kleurcodering inschakelen",
                "DEFAULT_COLOR_LABEL": "Standaardkleur:",
                "HIGH_SPEED_THRESHOLD_LABEL": "Hoge snelheid:",
                "LOW_SPEED_THRESHOLD_LABEL": "Lage snelheid:",
                "HIGH_SPEED_COLOR_LABEL": "Hoge snelheid kleur:",
                "LOW_SPEED_COLOR_LABEL": "Lage snelheid kleur:",
                "MINI_GRAPH_SETTINGS_GROUP": "Mini grafiek (widget)",
                "ENABLE_GRAPH_LABEL": "Mini grafiek tonen",
                "GRAPH_NOTE_TEXT": "Opmerking: toont een kleine real-time grafiek in het widgetgebied.",
                "HISTORY_DURATION_LABEL": "Grafiek tijdsduur:",
                "GRAPH_OPACITY_LABEL": "Grafiek doorzichtigheid:",
                "UNITS_GROUP": "Snelheidseenheden",
                "SPEED_DISPLAY_MODE_LABEL": "Snelheidsweergave modus",
                "SPEED_DISPLAY_MODE_AUTO": "Auto",
                "SPEED_DISPLAY_MODE_MBPS": "Alleen Mbps",
                "DECIMAL_PLACES_LABEL": "Decimalen",
                "TEXT_ALIGNMENT_LABEL": "Tekst uitlijning",
                "FORCE_DECIMALS_LABEL": "Altijd decimalen tonen",
                "ALIGN_LEFT": "Links",
                "ALIGN_CENTER": "Midden",
                "ALIGN_RIGHT": "Rechts",
                "NETWORK_INTERFACES_GROUP": "Netwerkinterfaces",
                "ALL_INTERFACES_LABEL": "Alle interfaces monitoren",
                "NO_INTERFACES_FOUND": "Geen netwerkinterfaces gedetecteerd.",
                "TROUBLESHOOTING_GROUP": "Probleemoplossing",
                "LOG_FILES_FILTER": "Logbestanden",
                "ALL_FILES_FILTER": "Alle bestanden",
                "UPLOAD_ARROW": "\u2191",
                "DOWNLOAD_ARROW": "\u2193",

                # --- Tooltips ---
                "SHOW_GRAPH_SETTINGS_TOOLTIP": "Toon grafiekinstellingen paneel",
                "HIDE_GRAPH_SETTINGS_TOOLTIP": "Verberg grafiekinstellingen paneel",
                "DEFAULT_COLOR_TOOLTIP": "Selecteer de standaard tekstkleur",
                "HIGH_SPEED_COLOR_TOOLTIP": "Selecteer de kleur voor hoge snelheden",
                "LOW_SPEED_COLOR_TOOLTIP": "Selecteer de kleur voor lage snelheden",
                "EXPORT_ERROR_LOG_TOOLTIP": "Kopie van applicatiefoutenlogboek opslaan",

                # --- Buttons and Menu Items ---
                "EXPORT_CSV_BUTTON": "Geschiedenis exporteren (CSV)",
                "EXPORT_GRAPH_IMAGE_BUTTON": "Grafiek opslaan (PNG)",
                "SETTINGS_MENU_ITEM": "&Instellingen",
                "SHOW_GRAPH_MENU_ITEM": "Toon &grafiekvenster",
                "STARTUP_MENU_ITEM": "Uitvoeren bij &opstarten",
                "PAUSE_MENU_ITEM": "&Pauzeren",
                "RESUME_MENU_ITEM": "&Hervatten",
                "EXIT_MENU_ITEM": "A&fsluiten",
                "SELECT_FONT_BUTTON": "Selecteren",
                "EXPORT_ERROR_LOG_BUTTON": "Foutenlogboek exporteren",
                "SAVE_BUTTON": "&Opslaan",
                "CANCEL_BUTTON": "&Annuleren",
                "SMART_MODE_LABEL": "Slimme modus",

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
                "PLURAL_SUFFIX": "en",
                "SECONDS_LABEL": "seconden",
                "MINUTES_LABEL": "minuten",
                "HOURS_LABEL": "uren",
                "DAYS_LABEL": "dagen",
                "WEEKS_LABEL": "weken",
                "MONTHS_LABEL": "maanden",
                "CSV_FILE_FILTER": "CSV bestanden (*.csv);;Alle bestanden (*.*)",
                "PNG_FILE_FILTER": "PNG afbeeldingen (*.png);;Alle bestanden (*.*)",
                "FONT_WEIGHT_THIN": "Dun",
                "FONT_WEIGHT_EXTRALIGHT": "Extra licht",
                "FONT_WEIGHT_LIGHT": "Licht",
                "FONT_WEIGHT_NORMAL": "Normaal",
                "FONT_WEIGHT_MEDIUM": "Medium",
                "FONT_WEIGHT_DEMIBOLD": "Half vet",
                "FONT_WEIGHT_BOLD": "Vet",
                "FONT_WEIGHT_EXTRABOLD": "Extra vet",
                "FONT_WEIGHT_BLACK": "Zwart",
                "DEFAULT_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.2f} {max_up_unit}, \u2193{max_down:.2f} {max_down_unit} | Totaal: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "APP_USAGE_STATS_TEXT_TEMPLATE": "Totaal: \u2191{up_total:.2f} {up_unit}, \u2193{down_total:.2f} {down_unit}",
                "GRAPH_TITLE_TEMPLATE": "Snelheidsgeschiedenis ({period})",
                "GRAPH_STATS_TEXT_TEMPLATE": "Max: \u2191{max_up:.1f} {unit} | \u2193{max_down:.1f} {unit}",
            },
        }

        # --- Determine and set language ---
        try:
            detected_locale = locale.getlocale(locale.LC_CTYPE)
            language_code = detected_locale[0] if detected_locale and detected_locale[0] else "en_US"
            language_code = language_code.replace('-', '_')
            
            self.language = language_code
        except Exception as e:
            logger.warning(f"Failed to get default locale: {e}, falling back to en_US.")
            self.language = "en_US"

        # Fallback logic
        effective_language = "en_US"
        if self.language in self._strings:
            effective_language = self.language
        else:
            base_language = self.language.split('_')[0]
            for supported_lang in self._strings.keys():
                if supported_lang.startswith(base_language + '_'):
                    effective_language = supported_lang
                    break
        
        self.language = effective_language
        logger.info(f"I18nStrings initialized. Effective language: {self.language}")

        try:
            self.validate()
        except ValueError as e:
            logger.error(f"I18n validation failed on initialization: {e}")


    def __getattr__(self, name: str) -> str:
        """
        Override attribute access for missing attributes to look up translation strings.
        """
        try:
            _strings_internal = object.__getattribute__(self, "_strings")
            language_internal = object.__getattribute__(self, "language")
        except AttributeError:
            logger.error(f"I18n internal attributes (_strings, language) not yet set when looking for '{name}'.")
            raise AttributeError(f"Attribute '{name}' not found and i18n internals not ready.")

        try:
            current_lang_dict = _strings_internal.get(language_internal)
            if current_lang_dict is None:
                logger.error(f"Internal Error: Language dictionary for '{language_internal}' is missing. Using en_US for '{name}'.")
                current_lang_dict = _strings_internal.get("en_US", {})

            value = current_lang_dict.get(name)
            if value is None:
                if language_internal != "en_US":
                    logger.warning(f"String constant '{name}' not found in language '{language_internal}'. Attempting en_US fallback.")
                
                fallback_dict = _strings_internal.get("en_US", {})
                value = fallback_dict.get(name)
                
                if value is None:
                    logger.critical(f"String constant '{name}' not found in fallback language 'en_US'.")
                    raise AttributeError(f"String constant '{name}' is missing from all language definitions.")

            if not isinstance(value, str):
                logger.error(f"Value for '{name}' in language '{language_internal}' is not a string (type: {type(value)}). Fallbacking to default.")
                return f"[ERR: TYPE {name}]" 
            return value
        except AttributeError:
            raise
        except Exception as e:
            logger.critical(f"Unexpected error retrieving string '{name}' for language '{language_internal}': {e}", exc_info=True)
            return f"[ERR: LOOKUP {name}]"


    def set_language(self, language: str) -> None:
        """
        Set the current language dynamically.
        """
        normalized_language = language.replace('-', '_')
        if normalized_language not in self._strings:
            raise ValueError(f"Language '{language}' not supported.")
        
        if self.language != normalized_language:
            self.language = normalized_language
            logger.info(f"Language dynamically set to: {self.language}")


    def validate(self) -> None:
        """
        Validate that all string constants are non-empty for all defined languages,
        comparing against the 'en_US' set as the master reference.
        """
        logger.debug("Validating all I18n strings...")
        if "en_US" not in self._strings or not isinstance(self._strings["en_US"], dict) or not self._strings["en_US"]:
            logger.error("en_US language strings are missing, empty, or not a dictionary. Cannot perform comprehensive validation.")
            for lang, translations in self._strings.items():
                if not isinstance(translations, dict):
                     raise ValueError(f"Entry for language '{lang}' is not a dictionary.")
                for key, value in translations.items():
                    if not isinstance(key, str) or not key:
                         raise ValueError(f"Invalid or empty key found in language '{lang}'. Key: '{key}'")
                    if not isinstance(value, str) or not value:
                        raise ValueError(f"Invalid or empty string value for key '{key}' in language '{lang}'. Value: '{value}'")
            logger.warning("Validation performed basic checks due to missing/invalid en_US base.")
            return

        master_keys = set(self._strings["en_US"].keys())
        if not all(isinstance(k, str) and k for k in master_keys):
            raise ValueError("Found invalid or empty keys in the master en_US dictionary.")

        validation_errors = []
        for lang_code, translations_dict in self._strings.items():
            if not isinstance(translations_dict, dict):
                 validation_errors.append(f"Language entry '{lang_code}' is not a dictionary.")
                 continue

            current_lang_keys = set(translations_dict.keys())
            
            if not all(isinstance(k, str) and k for k in current_lang_keys):
                 malformed = [k for k in current_lang_keys if not (isinstance(k, str) and k)]
                 validation_errors.append(f"Language '{lang_code}' contains invalid or empty keys: {malformed}")

            missing_keys = master_keys - current_lang_keys
            if missing_keys:
                validation_errors.append(f"Language '{lang_code}' is missing keys defined in en_US: {sorted(list(missing_keys))}")

            if lang_code != "en_US":
                extra_keys = current_lang_keys - master_keys
                if extra_keys:
                    logger.warning(f"Language '{lang_code}' has extra keys not present in en_US: {sorted(list(extra_keys))}")

            for key, value_str in translations_dict.items():
                if not isinstance(value_str, str):
                    validation_errors.append(f"Value for key '{key}' in language '{lang_code}' is not a string (type: {type(value_str)}).")

        if validation_errors:
            error_summary = "I18n string validation failed with {} error(s):\n- ".format(len(validation_errors)) + "\n- ".join(validation_errors)
            logger.error(error_summary)
            raise ValueError(error_summary)
        else:
            logger.debug("All I18n strings validated successfully against en_US keys.")

# Singleton instance for easy access throughout the application
strings = I18nStrings()