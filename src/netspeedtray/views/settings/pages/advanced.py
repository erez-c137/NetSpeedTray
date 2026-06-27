"""
Advanced Settings Page (2.0 IA) — data history, app-behavior flags, and a fresh start.

The progressive-disclosure valve for genuinely low-frequency / destructive items:
data retention, the app-wide reduce-motion flag (also read by the Monitor window's
hold-still logic), and reset affordances. English literals pending the single i18n pass.
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
        data_group = QGroupBox("Data")
        dl = QVBoxLayout(data_group)
        dl.addWidget(QLabel("Keep history for"))
        self.keep_data = QComboBox()
        for idx in sorted(self._days_map):
            days = self._days_map[idx]
            self.keep_data.addItem(self._retention_label(days), userData=days)
        self.keep_data.currentIndexChanged.connect(self.on_change)
        dl.addWidget(self.keep_data)
        layout.addWidget(data_group)

        # --- Behavior ---
        beh_group = QGroupBox("Behavior")
        bl = QVBoxLayout(beh_group)
        self.reduce_motion = Win11Toggle(label_text="Reduce motion (fewer animations)")
        self.reduce_motion.toggled.connect(self.on_change)
        bl.addWidget(self.reduce_motion)
        layout.addWidget(beh_group)

        # --- Reset ---
        reset_group = QGroupBox("Reset")
        rl = QVBoxLayout(reset_group)
        note = QLabel("Restore settings to their defaults. This can't be undone.")
        note.setWordWrap(True)
        rl.addWidget(note)
        btn_row = QHBoxLayout()
        self.reset_page_btn = QPushButton("Reset this page")
        self.reset_all_btn = QPushButton("Reset all to defaults")
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

    @staticmethod
    def _retention_label(days: int) -> str:
        if days >= 365:
            years = days // 365
            return "1 year" if years == 1 else f"{years} years"
        if days >= 30 and days % 30 == 0:
            months = days // 30
            return f"{months} month" + ("s" if months > 1 else "")
        if days >= 7 and days % 7 == 0:
            weeks = days // 7
            return f"{weeks} week" + ("s" if weeks > 1 else "")
        return f"{days} day" + ("s" if days > 1 else "")

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
