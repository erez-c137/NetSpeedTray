"""
Hardware Monitoring Settings Page.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QLabel, QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout,
                             QLineEdit, QPushButton)

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.components import Win11Toggle, Win11Slider, SettingCard, SettingExpander, Win11ComboBox
from netspeedtray.views.settings.pages._fluent import page_layout

class HardwarePage(QWidget):
    layout_changed = pyqtSignal()
    # Emitted when a hardware monitor is switched ON. The dialog forwards it to the Widget page (which
    # now owns the display-mode control) so the widget switches out of network-only and the freshly
    # enabled stat is actually visible - the convenience this page used to do itself.
    hardware_enabled = pyqtSignal()

    # Config keys of the two custom label colours; each has a "<key>_button" swatch and "<key>_input" hex.
    LABEL_COLOR_KEYS = ("cpu_label_color", "gpu_label_color")

    def __init__(self, i18n, on_change: Callable[[], None],
                 color_dialog_callback: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        # SettingsDialog._open_color_dialog(key) - the same QColorDialog flow the Appearance colours use.
        self.open_color_dialog = color_dialog_callback or (lambda _key: None)
        # Last complete #RRGGBB per custom label colour - what get_settings reports while a field is mid-edit.
        self._last_valid_hex = {k: getattr(constants.config.defaults, f"DEFAULT_{k.upper()}")
                                for k in self.LABEL_COLOR_KEYS}
        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: the primary monitoring toggles are Win11 Settings cards under a plain section caption;
        # the lower-frequency groups (display mode / order / load colors) stay behind Fluent expanders.
        # All control objects + the load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Hardware monitoring (primary - sits directly under the page's "Hardware" H1 title, so no
        # redundant section caption here). ---
        self.monitor_cpu = Win11Toggle(label_text="")
        self.monitor_cpu.toggled.connect(self._on_monitor_toggled)
        layout.addWidget(SettingCard(self.i18n.MONITOR_CPU_LABEL, control=self.monitor_cpu))

        self.monitor_ram = Win11Toggle(label_text="")
        self.monitor_ram.toggled.connect(self.on_change)
        self._ram_card = SettingCard(self.i18n.MONITOR_RAM_LABEL, control=self.monitor_ram)
        layout.addWidget(self._ram_card)

        self.monitor_gpu = Win11Toggle(label_text="")
        self.monitor_gpu.toggled.connect(self._on_monitor_toggled)
        layout.addWidget(SettingCard(self.i18n.MONITOR_GPU_LABEL, control=self.monitor_gpu))

        self.monitor_vram = Win11Toggle(label_text="")
        self.monitor_vram.toggled.connect(self.on_change)
        self._vram_card = SettingCard(self.i18n.MONITOR_VRAM_LABEL, control=self.monitor_vram)
        layout.addWidget(self._vram_card)

        # #250: "RAM"/"VRAM" in front of the memory values. Only meaningful when one of them shows,
        # so the RAM/VRAM toggles re-sync its enabled state too.
        self.show_memory_labels = Win11Toggle(label_text="")
        self.show_memory_labels.toggled.connect(self.on_change)
        self._mem_labels_card = SettingCard(self.i18n.SHOW_MEMORY_LABELS_LABEL, control=self.show_memory_labels)
        layout.addWidget(self._mem_labels_card)
        self.monitor_ram.toggled.connect(lambda _on: self._sync_dependent_cards())
        self.monitor_vram.toggled.connect(lambda _on: self._sync_dependent_cards())

        self.show_temps = Win11Toggle(label_text="")
        self.show_temps.toggled.connect(self.on_change)
        self._temps_card = SettingCard(self.i18n.SHOW_HARDWARE_TEMPS_LABEL, control=self.show_temps)
        layout.addWidget(self._temps_card)

        self.show_power = Win11Toggle(label_text="")
        self.show_power.toggled.connect(self.on_change)
        self._power_card = SettingCard(self.i18n.SHOW_HARDWARE_POWER_LABEL, control=self.show_power)
        layout.addWidget(self._power_card)

        temps_note = QLabel(self.i18n.HARDWARE_TEMPS_LIMITATION_NOTE)
        temps_note.setWordWrap(True)
        temps_note.setStyleSheet(
            f"color: {su.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        layout.addWidget(temps_note)

        # Indicator SHAPE (icons / text) and COLOUR (brand / match text / custom) are separate choices;
        # pre-2.2 configs carried both in one three-way style and are split by the config migration.
        self.label_style = Win11ComboBox()
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_ICONS, userData="icons")
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_TEXT_LABELS, userData="text")
        self.label_style.setMinimumWidth(200)
        self.label_style.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HARDWARE_INDICATOR_STYLE_LABEL, control=self.label_style))

        self.label_color = Win11ComboBox()
        self.label_color.addItem(self.i18n.HARDWARE_LABEL_COLOR_BRAND, userData="brand")
        self.label_color.addItem(self.i18n.HARDWARE_LABEL_COLOR_MATCH_TEXT, userData="match_text")
        self.label_color.addItem(self.i18n.HARDWARE_LABEL_COLOR_CUSTOM, userData="custom")
        self.label_color.setMinimumWidth(200)
        self.label_color.setToolTip(self.i18n.HARDWARE_LABEL_COLOR_TOOLTIP)
        self.label_color.currentIndexChanged.connect(self._on_label_color_changed)
        layout.addWidget(SettingCard(self.i18n.HARDWARE_LABEL_COLOR_LABEL, control=self.label_color))

        # Custom: a swatch (opens the colour dialog) + hex field per label, like the arrow colours.
        self.label_colors_container = QWidget()
        lc_layout = QVBoxLayout(self.label_colors_container)
        lc_layout.setContentsMargins(0, 0, 0, 0)
        lc_layout.setSpacing(4)
        for key, label in (("cpu_label_color", self.i18n.CPU_LABEL_COLOR_LABEL),
                           ("gpu_label_color", self.i18n.GPU_LABEL_COLOR_LABEL)):
            button = QPushButton()
            button.setObjectName(key)
            button.setFixedSize(36, 26)
            button.clicked.connect(lambda _=False, k=key: self.open_color_dialog(k))
            line = QLineEdit()
            line.setMaxLength(7)
            line.setFixedWidth(90)
            line.textChanged.connect(lambda _text, k=key: self._on_label_hex_edited(k))
            setattr(self, f"{key}_button", button)
            setattr(self, f"{key}_input", line)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            row_layout.addWidget(button)
            row_layout.addWidget(line)
            lc_layout.addWidget(SettingCard(label, control=row))
        layout.addWidget(self.label_colors_container)
        self.label_colors_container.setVisible(False)

        # Throttle temperature (Statistics): flags time spent at/above this temp in the Stats-detail
        # sheet so thermal throttling is provable (0 = off). Only meaningful when temps are collected.
        self.throttle_temp = Win11Slider(editable=True, suffix="°C")
        self.throttle_temp.setRange(0, 130)
        # 0 turns the feature off: say "Off", not "0°C" (same word as the plan-speed spinbox's 0).
        self.throttle_temp.setSpecialValueText(str(getattr(self.i18n, "CONN_PLAN_OFF", "Off")))
        self.throttle_temp.setMinimumWidth(260)
        self.throttle_temp.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(str(getattr(self.i18n, "HW_THROTTLE_LABEL", "Throttle temp (stats)")),
                                     control=self.throttle_temp))

        # NOTE (2.0 IA): "Widget Display Mode" + "Display Order" moved to the new Widget page - they're
        # about the widget's layout, not hardware monitoring. The monitor toggles here still nudge that
        # layout via the hardware_enabled signal (see _on_monitor_toggled).

        # --- Color-code by load (advanced - collapsible): thresholds for tinting CPU/GPU % by load.
        load_section = SettingExpander(self.i18n.HARDWARE_LOAD_COLOR_SECTION, expanded=False)
        load_section.expandedChanged.connect(lambda _on: self.layout_changed.emit())

        def _make_load_slider() -> Win11Slider:
            s = Win11Slider(editable=True, suffix="%")
            s.setRange(0, 100)
            s.setMinimumWidth(240)
            s.valueChanged.connect(self.on_change)
            return s

        self.cpu_load_high = _make_load_slider()
        self.cpu_load_low = _make_load_slider()
        self.gpu_load_high = _make_load_slider()
        self.gpu_load_low = _make_load_slider()
        for label, widget in [
            (self.i18n.HARDWARE_CPU_HIGH_LOAD_LABEL, self.cpu_load_high),
            (self.i18n.HARDWARE_CPU_LOW_LOAD_LABEL, self.cpu_load_low),
            (self.i18n.HARDWARE_GPU_HIGH_LOAD_LABEL, self.gpu_load_high),
            (self.i18n.HARDWARE_GPU_LOW_LOAD_LABEL, self.gpu_load_low),
        ]:
            load_section.contentLayout().addWidget(SettingCard(label, control=widget))
        layout.addWidget(load_section)

        layout.addStretch()

    def load_settings(self, config: Dict[str, Any]):
        # Block signals to prevent setChecked from triggering _on_monitor_toggled
        # which would auto-switch the display mode dropdown unexpectedly during load.
        self.monitor_cpu.blockSignals(True)
        self.monitor_gpu.blockSignals(True)
        self.monitor_ram.blockSignals(True)
        self.monitor_vram.blockSignals(True)
        self.show_temps.blockSignals(True)
        self.show_power.blockSignals(True)
        self.show_memory_labels.blockSignals(True)

        self.monitor_cpu.setChecked(config.get("monitor_cpu_enabled", False))
        self.monitor_gpu.setChecked(config.get("monitor_gpu_enabled", False))
        self.monitor_ram.setChecked(config.get("monitor_ram_enabled", False))
        self.monitor_vram.setChecked(config.get("monitor_vram_enabled", False))
        self.show_temps.setChecked(config.get("show_hardware_temps", False))
        self.show_power.setChecked(config.get("show_hardware_power", False))
        self.show_memory_labels.setChecked(config.get("show_memory_labels", False))

        d = constants.config.defaults
        style_idx = self.label_style.findData(config.get("hardware_label_style", d.DEFAULT_HARDWARE_LABEL_STYLE))
        self.label_style.setCurrentIndex(max(0, style_idx))
        self.label_color.blockSignals(True)
        color_idx = self.label_color.findData(config.get("hardware_label_color", d.DEFAULT_HARDWARE_LABEL_COLOR))
        if color_idx < 0:
            color_idx = self.label_color.findData(d.DEFAULT_HARDWARE_LABEL_COLOR)
        self.label_color.setCurrentIndex(color_idx)
        self.label_color.blockSignals(False)
        self.label_colors_container.setVisible(self.label_color.currentData() == "custom")
        for key in self.LABEL_COLOR_KEYS:
            self.set_color_input(key, str(config.get(key, getattr(d, f"DEFAULT_{key.upper()}"))))

        self.throttle_temp.setValue(int(config.get("throttle_temp_c", 0) or 0))

        self.monitor_cpu.blockSignals(False)
        self.monitor_gpu.blockSignals(False)
        self.monitor_ram.blockSignals(False)
        self.monitor_vram.blockSignals(False)
        self.show_temps.blockSignals(False)
        self.show_power.blockSignals(False)
        self.show_memory_labels.blockSignals(False)
        # Signals were blocked above, so _on_monitor_toggled never fired - sync the dependent
        # (temp/power) cards' enabled state to the just-loaded CPU/GPU monitor values directly.
        self._sync_dependent_cards()
        # widget_display_mode / widget_display_order are loaded by the Widget page now.

        d = constants.config.defaults
        self.cpu_load_high.setValue(int(config.get("cpu_load_high_threshold", d.DEFAULT_CPU_LOAD_HIGH_THRESHOLD)))
        self.cpu_load_low.setValue(int(config.get("cpu_load_low_threshold", d.DEFAULT_CPU_LOAD_LOW_THRESHOLD)))
        self.gpu_load_high.setValue(int(config.get("gpu_load_high_threshold", d.DEFAULT_GPU_LOAD_HIGH_THRESHOLD)))
        self.gpu_load_low.setValue(int(config.get("gpu_load_low_threshold", d.DEFAULT_GPU_LOAD_LOW_THRESHOLD)))

    # --- CPU/GPU label colour -------------------------------------------------------------------
    @staticmethod
    def _is_hex(text: str) -> bool:
        t = (text or "").strip()
        return len(t) == 7 and t.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in t[1:])

    def _label_hex(self, key: str) -> str:
        """The last COMPLETE #RRGGBB in the field. A half-typed value never reaches the config - saving
        mid-edit keeps the colour the user had, not the default (validation would reset a partial one)."""
        text = getattr(self, f"{key}_input").text().strip()
        if self._is_hex(text):
            self._last_valid_hex[key] = text.upper()
        return self._last_valid_hex[key]

    def _paint_swatch(self, key: str, hex_code: str) -> None:
        if self._is_hex(hex_code):
            getattr(self, f"{key}_button").setStyleSheet(
                f"background-color: {hex_code}; border: 1px solid #5A5A5A; border-radius: 4px;")

    def set_color_input(self, key: str, hex_code: str) -> None:
        """Set a custom label colour (from load_settings or the colour dialog); key is a config key."""
        if key not in self.LABEL_COLOR_KEYS:
            return
        line = getattr(self, f"{key}_input")
        line.blockSignals(True)
        line.setText(QColor(hex_code).name().upper() if QColor.isValidColorName(hex_code) else hex_code)
        line.blockSignals(False)
        if self._is_hex(line.text()):
            self._last_valid_hex[key] = line.text().upper()
        self._paint_swatch(key, line.text())
        self.on_change()

    def _on_label_hex_edited(self, key: str) -> None:
        """Typing in a hex field: only a complete #RRGGBB updates the swatch, the stored colour and the
        live preview; keystrokes in between change nothing."""
        text = getattr(self, f"{key}_input").text().strip()
        if not self._is_hex(text):
            return
        self._last_valid_hex[key] = text.upper()
        self._paint_swatch(key, text)
        self.on_change()

    def _on_label_color_changed(self, _index: int) -> None:
        self.label_colors_container.setVisible(self.label_color.currentData() == "custom")
        self.layout_changed.emit()
        self.on_change()

    def _on_monitor_toggled(self, checked: bool):
        """A monitor was toggled. When turned ON, ask the Widget page (via the dialog) to make sure the
        widget isn't in network-only mode, so the new stat is actually visible. Display-mode itself now
        lives on the Widget page, so this page only signals intent."""
        if checked:
            self.hardware_enabled.emit()
        self._sync_dependent_cards()
        self.on_change()

    def _set_card_enabled(self, card: SettingCard, enabled: bool) -> None:
        """Gray out a dependent card when its prerequisite is off. setEnabled alone won't visually dim
        our fixed-color custom toggle, so pair it with an opacity effect for the Win11 'unavailable'
        look (and to make the card non-interactive)."""
        card.setEnabled(enabled)
        eff = card.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(card)
            card.setGraphicsEffect(eff)
        eff.setOpacity(1.0 if enabled else 0.4)

    def _sync_dependent_cards(self) -> None:
        """Several readouts ride on the CPU/GPU utilization lines, so gray their toggles out when there's
        nowhere for them to render (Win11 dependent-control pattern): temperature & power need at least
        one of CPU/GPU on; RAM rides on the CPU line, and VRAM on the GPU line, so each is grayed when its
        host monitor is off - otherwise turning on RAM with CPU off promised something that never shows."""
        cpu_on, gpu_on = self.monitor_cpu.isChecked(), self.monitor_gpu.isChecked()
        has_util = cpu_on or gpu_on
        self._set_card_enabled(self._temps_card, has_util)
        self._set_card_enabled(self._power_card, has_util)
        self._set_card_enabled(self._ram_card, cpu_on)
        self._set_card_enabled(self._vram_card, gpu_on)
        self._set_card_enabled(self._mem_labels_card,
                               (cpu_on and self.monitor_ram.isChecked()) or (gpu_on and self.monitor_vram.isChecked()))

    def get_settings(self) -> Dict[str, Any]:
        return {
            "monitor_cpu_enabled": self.monitor_cpu.isChecked(),
            "monitor_gpu_enabled": self.monitor_gpu.isChecked(),
            "monitor_ram_enabled": self.monitor_ram.isChecked(),
            "monitor_vram_enabled": self.monitor_vram.isChecked(),
            "show_memory_labels": self.show_memory_labels.isChecked(),
            "show_hardware_temps": self.show_temps.isChecked(),
            "show_hardware_power": self.show_power.isChecked(),
            "hardware_label_style": self.label_style.currentData(),
            "hardware_label_color": self.label_color.currentData(),
            "cpu_label_color": self._label_hex("cpu_label_color"),
            "gpu_label_color": self._label_hex("gpu_label_color"),
            "cpu_load_high_threshold": float(self.cpu_load_high.value()),
            "cpu_load_low_threshold": float(self.cpu_load_low.value()),
            "gpu_load_high_threshold": float(self.gpu_load_high.value()),
            "gpu_load_low_threshold": float(self.gpu_load_low.value()),
            "throttle_temp_c": int(self.throttle_temp.value()),
        }
