"""
Advanced Settings Page (2.0 IA) — data history, app-behavior flags, and a fresh start.

The progressive-disclosure valve for genuinely low-frequency / destructive items:
data retention, the app-wide reduce-motion flag (also read by the Monitor window's
hold-still logic), and reset affordances.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QLabel, QPushButton

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle, SettingCard
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils import helpers
from netspeedtray.views.settings.pages._fluent import section_header, page_layout


class AdvancedPage(QWidget):
    """Retention + reduce-motion + reset (C6)."""

    def __init__(self, i18n, on_change: Callable[[], None],
                 reset_page_callback: Optional[Callable[[], None]] = None,
                 reset_all_callback: Optional[Callable[[], None]] = None):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._reset_page_cb = reset_page_callback
        self._reset_all_cb = reset_all_callback
        self._days_map = constants.data.retention.DAYS_MAP  # {0:1, ..., 6:365}
        self._setup_ui()

    def _setup_ui(self) -> None:
        # 2.0 IA: Win11 Settings cards under light section captions (was QGroupBox frames). Control
        # objects + load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Data ---
        layout.addWidget(section_header(self.i18n.ADVANCED_DATA_GROUP))
        self.keep_data = QComboBox()
        for idx in sorted(self._days_map):
            days = self._days_map[idx]
            self.keep_data.addItem(self._retention_label(days), userData=days)
        self.keep_data.setMinimumWidth(180)
        self.keep_data.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.ADVANCED_KEEP_HISTORY_LABEL, control=self.keep_data))

        # --- Behavior ---
        layout.addWidget(section_header(self.i18n.BEHAVIOR_GROUP_TITLE))
        self.reduce_motion = Win11Toggle(label_text="")
        self.reduce_motion.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.ADVANCED_REDUCE_MOTION_LABEL, control=self.reduce_motion))

        self.show_usage_hover = Win11Toggle(label_text="")
        self.show_usage_hover.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HOVER_USAGE_CARD_LABEL, control=self.show_usage_hover))

        self.show_hover_tips = Win11Toggle(label_text="")
        self.show_hover_tips.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HOVER_TIPS_LABEL, control=self.show_hover_tips))

        self.pause_in_menu = Win11Toggle(label_text="")
        self.pause_in_menu.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.PAUSE_IN_MENU_LABEL, control=self.pause_in_menu))

        # --- Reset ---
        layout.addWidget(section_header(self.i18n.ADVANCED_RESET_GROUP))
        note = QLabel(self.i18n.ADVANCED_RESET_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {style_utils.semantic_colors()['text_secondary']};"
            f" background: transparent; padding: 0 2px;")
        layout.addWidget(note)
        btn_row = QHBoxLayout()
        self.reset_page_btn = QPushButton(self.i18n.ADVANCED_RESET_PAGE_BUTTON)
        self.reset_all_btn = QPushButton(self.i18n.ADVANCED_RESET_ALL_BUTTON)
        self.reset_page_btn.setStyleSheet(style_utils.button_style())
        self.reset_all_btn.setStyleSheet(style_utils.button_style())
        if self._reset_page_cb:
            self.reset_page_btn.clicked.connect(self._reset_page_cb)
        if self._reset_all_cb:
            self.reset_all_btn.clicked.connect(self._reset_all_cb)
        btn_row.addWidget(self.reset_page_btn)
        btn_row.addWidget(self.reset_all_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

    def _retention_label(self, days: int) -> str:
        return helpers.format_retention_label(days, self.i18n)

    def load_settings(self, config: Dict[str, Any]) -> None:
        days = int(config.get("keep_data", constants.config.defaults.DEFAULT_KEEP_DATA_DAYS))
        idx = self.keep_data.findData(days)
        if idx < 0:
            # snap to the closest available retention value
            idx = min(range(self.keep_data.count()),
                      key=lambda i: abs(int(self.keep_data.itemData(i)) - days))
        self.keep_data.setCurrentIndex(idx)
        self.reduce_motion.setChecked(bool(config.get("reduce_motion", False)))
        self.show_usage_hover.setChecked(bool(config.get("show_usage_on_hover", True)))
        self.show_hover_tips.setChecked(bool(config.get("show_hover_tips", True)))
        self.pause_in_menu.setChecked(bool(config.get("pause_in_menu", False)))

    def get_settings(self) -> Dict[str, Any]:
        return {
            "keep_data": int(self.keep_data.currentData()),
            "reduce_motion": self.reduce_motion.isChecked(),
            "show_usage_on_hover": self.show_usage_hover.isChecked(),
            "show_hover_tips": self.show_hover_tips.isChecked(),
            "pause_in_menu": self.pause_in_menu.isChecked(),
        }
