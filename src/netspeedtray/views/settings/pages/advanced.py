"""
Advanced Settings Page (2.0 IA) — data history, app-behavior flags, and a fresh start.

The progressive-disclosure valve for genuinely low-frequency / destructive items:
data retention, the app-wide reduce-motion flag (also read by the Monitor window's
hold-still logic), and reset affordances.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QComboBox, QLabel, QPushButton,
)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle
from netspeedtray.utils import styles as style_utils


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
        layout = QVBoxLayout(self)
        layout.setSpacing(constants.layout.GROUP_BOX_SPACING)

        # --- Data ---
        data_group = QGroupBox(self.i18n.ADVANCED_DATA_GROUP)
        dl = QVBoxLayout(data_group)
        dl.addWidget(QLabel(self.i18n.ADVANCED_KEEP_HISTORY_LABEL))
        self.keep_data = QComboBox()
        for idx in sorted(self._days_map):
            days = self._days_map[idx]
            self.keep_data.addItem(self._retention_label(days), userData=days)
        self.keep_data.currentIndexChanged.connect(self.on_change)
        dl.addWidget(self.keep_data)
        layout.addWidget(data_group)

        # --- Behavior ---
        beh_group = QGroupBox(self.i18n.BEHAVIOR_GROUP_TITLE)
        bl = QVBoxLayout(beh_group)
        self.reduce_motion = Win11Toggle(label_text=self.i18n.ADVANCED_REDUCE_MOTION_LABEL)
        self.reduce_motion.toggled.connect(self.on_change)
        bl.addWidget(self.reduce_motion)
        layout.addWidget(beh_group)

        # --- Reset ---
        reset_group = QGroupBox(self.i18n.ADVANCED_RESET_GROUP)
        rl = QVBoxLayout(reset_group)
        note = QLabel(self.i18n.ADVANCED_RESET_NOTE)
        note.setWordWrap(True)
        rl.addWidget(note)
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
        rl.addLayout(btn_row)
        layout.addWidget(reset_group)

        layout.addStretch()

    def _retention_label(self, days: int) -> str:
        i18n = self.i18n
        plural = getattr(i18n, "PLURAL_SUFFIX", "s")
        if days >= 365:
            years = days // 365
            return i18n.RETENTION_YEAR_SINGULAR if years == 1 else i18n.RETENTION_YEARS_TEMPLATE.format(years=years)
        if days >= 30 and days % 30 == 0:
            months = days // 30
            return i18n.RETENTION_MONTHS_TEMPLATE.format(months=months, plural=(plural if months > 1 else ""))
        if days >= 7 and days % 7 == 0:
            weeks = days // 7
            return i18n.RETENTION_WEEKS_TEMPLATE.format(weeks=weeks, plural=(plural if weeks > 1 else ""))
        return i18n.RETENTION_DAYS_TEMPLATE.format(days=days, plural=(plural if days > 1 else ""))

    def load_settings(self, config: Dict[str, Any]) -> None:
        days = int(config.get("keep_data", constants.config.defaults.DEFAULT_KEEP_DATA_DAYS))
        idx = self.keep_data.findData(days)
        if idx < 0:
            # snap to the closest available retention value
            idx = min(range(self.keep_data.count()),
                      key=lambda i: abs(int(self.keep_data.itemData(i)) - days))
        self.keep_data.setCurrentIndex(idx)
        self.reduce_motion.setChecked(bool(config.get("reduce_motion", False)))

    def get_settings(self) -> Dict[str, Any]:
        return {
            "keep_data": int(self.keep_data.currentData()),
            "reduce_motion": self.reduce_motion.isChecked(),
        }
