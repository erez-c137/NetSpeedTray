"""
Widget Settings Page (2.0 IA).

Everything about the on-taskbar widget itself — how its stats are laid out (display mode + order),
and how it behaves/sits on the taskbar (free-move, keep-visible-in-fullscreen, tray offset). These
controls used to be scattered across the General page (behaviour) and the Hardware page (layout); the
2.0 IA gathers them here so "the widget" is one place.

Slider→Segmented and QListWidget upgrades are intentionally NOT done here — the controls move as-is
(same widgets, same config mappings) so the reshuffle can't silently corrupt a saved value.
"""
from typing import Dict, Any, Callable, List

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QComboBox, QLabel

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.components import Win11Toggle, SettingCard
from netspeedtray.views.settings.pages._fluent import section_header, page_layout


class WidgetPage(QWidget):
    """Layout (display mode + order) and on-taskbar behaviour (free-move, fullscreen, offset)."""

    layout_changed = pyqtSignal()

    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = page_layout(self)

        # --- Display mode ---
        layout.addWidget(section_header(self.i18n.WIDGET_DISPLAY_MODE_LABEL))
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_NETWORK, userData="network_only")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_COMBINED, userData="side_by_side")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_STACKED_COLUMN, userData="side_by_stack")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_CYCLE, userData="cycle")
        self.display_mode_combo.currentIndexChanged.connect(self.on_change)
        layout.addWidget(self.display_mode_combo)
        note = QLabel(self.i18n.HARDWARE_GRAPH_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {su.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        layout.addWidget(note)

        # --- Display order ---
        layout.addWidget(section_header(self.i18n.WIDGET_DISPLAY_ORDER_LABEL))
        self.pos_combos: List[QComboBox] = []
        for i in range(3):
            combo = QComboBox()
            combo.addItem(self.i18n.ORDER_TYPE_NETWORK, userData="network")
            combo.addItem(self.i18n.ORDER_TYPE_CPU, userData="cpu")
            combo.addItem(self.i18n.ORDER_TYPE_GPU, userData="gpu")
            combo.addItem(self.i18n.ORDER_TYPE_NONE, userData="none")
            combo.setMinimumWidth(160)
            combo.currentIndexChanged.connect(lambda _, idx=i: self._on_pos_changed(idx))
            layout.addWidget(SettingCard(getattr(self.i18n, f"ORDER_POSITION_{i+1}"), control=combo))
            self.pos_combos.append(combo)

        # --- Behaviour (was on the General page) ---
        layout.addWidget(section_header(self.i18n.BEHAVIOR_GROUP_TITLE))
        self.free_move = Win11Toggle(label_text="")
        self.free_move.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.FREE_MOVE_LABEL, control=self.free_move))

        self.keep_visible_fullscreen = Win11Toggle(label_text="")
        self.keep_visible_fullscreen.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.KEEP_VISIBLE_FULLSCREEN_LABEL,
                                     control=self.keep_visible_fullscreen))

        # NOTE: there is intentionally no tray-offset slider. Free Move (drag the widget anywhere) is the
        # reposition escape hatch; the auto-position uses sensible default offsets. tray_offset_x/y remain
        # in config for the position engine (position_manager) but aren't surfaced as fiddly px sliders.

        layout.addStretch()

    # --- behaviour ---------------------------------------------------------------
    def ensure_hardware_visible(self) -> None:
        """Called by the dialog when a hardware monitor is enabled on the Hardware page: switch the
        widget out of network-only so the freshly-enabled stat is actually visible. This is the
        convenience the Hardware page did itself before display-mode moved to this page."""
        if self.display_mode_combo.currentData() == "network_only":
            idx = self.display_mode_combo.findData("side_by_side")
            if idx >= 0:
                self.display_mode_combo.setCurrentIndex(idx)   # fires on_change

    def _on_pos_changed(self, combo_index: int) -> None:
        """Prevents duplicate positional items by auto-swapping with the absent item."""
        values = [c.currentData() for c in self.pos_combos]
        new_val = values[combo_index]
        if new_val == "none":
            self.on_change()
            return
        for i in range(3):
            if i != combo_index and values[i] == new_val:
                used = set(values)
                missing = {"network", "cpu", "gpu"} - used
                if missing:
                    next_item = list(missing)[0]
                    idx = self.pos_combos[i].findData(next_item)
                    if idx >= 0:
                        self.pos_combos[i].blockSignals(True)
                        self.pos_combos[i].setCurrentIndex(idx)
                        self.pos_combos[i].blockSignals(False)
                break
        self.on_change()

    # --- config ------------------------------------------------------------------
    def load_settings(self, config: Dict[str, Any]) -> None:
        mode = config.get("widget_display_mode", "network_only")
        # "side_by_stack" is the side-by-side layout with the hardware column stacked — encoded as
        # widget_display_mode="side_by_side" + stack_hardware_stats=True on disk.
        if mode == "side_by_side" and config.get("stack_hardware_stats", False):
            mode = "side_by_stack"
        index = self.display_mode_combo.findData(mode)
        if index >= 0:
            self.display_mode_combo.setCurrentIndex(index)

        order = config.get("widget_display_order", ["network", "cpu", "gpu"])
        for i, combo in enumerate(self.pos_combos):
            val = order[i] if i < len(order) else "none"
            idx = combo.findData(val)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        self.free_move.setChecked(config.get("free_move", False))
        self.keep_visible_fullscreen.setChecked(
            config.get("keep_visible_fullscreen", constants.config.defaults.DEFAULT_KEEP_VISIBLE_FULLSCREEN))

    def get_settings(self) -> Dict[str, Any]:
        mode = self.display_mode_combo.currentData()
        order = [c.currentData() for c in self.pos_combos]
        return {
            "widget_display_mode": "side_by_side" if mode == "side_by_stack" else mode,
            "stack_hardware_stats": mode == "side_by_stack",
            "widget_display_order": order,
            "free_move": self.free_move.isChecked(),
            "keep_visible_fullscreen": self.keep_visible_fullscreen.isChecked(),
        }
