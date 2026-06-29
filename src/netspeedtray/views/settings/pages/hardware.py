"""
Hardware Monitoring Settings Page.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QComboBox, QLabel, QGraphicsOpacityEffect

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.components import Win11Toggle, Win11Slider, SettingCard, SettingExpander
from netspeedtray.views.settings.pages._fluent import page_layout

class HardwarePage(QWidget):
    layout_changed = pyqtSignal()
    # Emitted when a hardware monitor is switched ON. The dialog forwards it to the Widget page (which
    # now owns the display-mode control) so the widget switches out of network-only and the freshly
    # enabled stat is actually visible — the convenience this page used to do itself.
    hardware_enabled = pyqtSignal()

    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: the primary monitoring toggles are Win11 Settings cards under a plain section caption;
        # the lower-frequency groups (display mode / order / load colours) stay behind Fluent expanders.
        # All control objects + the load/get wiring are unchanged.
        layout = page_layout(self)

        # --- Hardware monitoring (primary — sits directly under the page's "Hardware" H1 title, so no
        # redundant section caption here). ---
        self.monitor_cpu = Win11Toggle(label_text="")
        self.monitor_cpu.toggled.connect(self._on_monitor_toggled)
        layout.addWidget(SettingCard(self.i18n.MONITOR_CPU_LABEL, control=self.monitor_cpu))

        self.monitor_ram = Win11Toggle(label_text="")
        self.monitor_ram.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.MONITOR_RAM_LABEL, control=self.monitor_ram))

        self.monitor_gpu = Win11Toggle(label_text="")
        self.monitor_gpu.toggled.connect(self._on_monitor_toggled)
        layout.addWidget(SettingCard(self.i18n.MONITOR_GPU_LABEL, control=self.monitor_gpu))

        self.monitor_vram = Win11Toggle(label_text="")
        self.monitor_vram.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.MONITOR_VRAM_LABEL, control=self.monitor_vram))

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

        self.label_style = QComboBox()
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_COLORED_ICONS, userData="icons_colored")
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_MONOCHROME_ICONS, userData="icons_monochrome")
        self.label_style.addItem(self.i18n.HARDWARE_LABEL_STYLE_TEXT_LABELS, userData="text")
        self.label_style.setMinimumWidth(200)
        self.label_style.currentIndexChanged.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HARDWARE_INDICATOR_STYLE_LABEL, control=self.label_style))

        # Throttle temperature (Statistics): flags time spent at/above this temp in the Stats-detail
        # sheet so thermal throttling is provable (0 = off). Only meaningful when temps are collected.
        self.throttle_temp = Win11Slider(editable=True, suffix="°C")
        self.throttle_temp.setRange(0, 130)
        self.throttle_temp.setMinimumWidth(260)
        self.throttle_temp.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(str(getattr(self.i18n, "HW_THROTTLE_LABEL", "Throttle temp (stats)")),
                                     control=self.throttle_temp))

        # NOTE (2.0 IA): "Widget Display Mode" + "Display Order" moved to the new Widget page — they're
        # about the widget's layout, not hardware monitoring. The monitor toggles here still nudge that
        # layout via the hardware_enabled signal (see _on_monitor_toggled).

        # --- Color-code by load (advanced — collapsible): thresholds for tinting CPU/GPU % by load.
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

        self.monitor_cpu.setChecked(config.get("monitor_cpu_enabled", False))
        self.monitor_gpu.setChecked(config.get("monitor_gpu_enabled", False))
        self.monitor_ram.setChecked(config.get("monitor_ram_enabled", False))
        self.monitor_vram.setChecked(config.get("monitor_vram_enabled", False))
        self.show_temps.setChecked(config.get("show_hardware_temps", False))
        self.show_power.setChecked(config.get("show_hardware_power", False))

        style_val = config.get("hardware_label_style", "icons_colored")
        style_idx = self.label_style.findData(style_val)
        if style_idx >= 0:
            self.label_style.setCurrentIndex(style_idx)

        self.throttle_temp.setValue(int(config.get("throttle_temp_c", 0) or 0))

        self.monitor_cpu.blockSignals(False)
        self.monitor_gpu.blockSignals(False)
        self.monitor_ram.blockSignals(False)
        self.monitor_vram.blockSignals(False)
        self.show_temps.blockSignals(False)
        self.show_power.blockSignals(False)
        # Signals were blocked above, so _on_monitor_toggled never fired — sync the dependent
        # (temp/power) cards' enabled state to the just-loaded CPU/GPU monitor values directly.
        self._sync_dependent_cards()
        # widget_display_mode / widget_display_order are loaded by the Widget page now.

        d = constants.config.defaults
        self.cpu_load_high.setValue(int(config.get("cpu_load_high_threshold", d.DEFAULT_CPU_LOAD_HIGH_THRESHOLD)))
        self.cpu_load_low.setValue(int(config.get("cpu_load_low_threshold", d.DEFAULT_CPU_LOAD_LOW_THRESHOLD)))
        self.gpu_load_high.setValue(int(config.get("gpu_load_high_threshold", d.DEFAULT_GPU_LOAD_HIGH_THRESHOLD)))
        self.gpu_load_low.setValue(int(config.get("gpu_load_low_threshold", d.DEFAULT_GPU_LOAD_LOW_THRESHOLD)))

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
        our fixed-colour custom toggle, so pair it with an opacity effect for the Win11 'unavailable'
        look (and to make the card non-interactive)."""
        card.setEnabled(enabled)
        eff = card.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(card)
            card.setGraphicsEffect(eff)
        eff.setOpacity(1.0 if enabled else 0.4)

    def _sync_dependent_cards(self) -> None:
        """Temperature & power are drawn appended to the CPU/GPU utilisation readout, so with BOTH of
        those monitors off there is nowhere for them to render — gray them out so the toggles can't
        promise something that never shows (Win11 dependent-control pattern)."""
        has_util = self.monitor_cpu.isChecked() or self.monitor_gpu.isChecked()
        self._set_card_enabled(self._temps_card, has_util)
        self._set_card_enabled(self._power_card, has_util)

    def get_settings(self) -> Dict[str, Any]:
        return {
            "monitor_cpu_enabled": self.monitor_cpu.isChecked(),
            "monitor_gpu_enabled": self.monitor_gpu.isChecked(),
            "monitor_ram_enabled": self.monitor_ram.isChecked(),
            "monitor_vram_enabled": self.monitor_vram.isChecked(),
            "show_hardware_temps": self.show_temps.isChecked(),
            "show_hardware_power": self.show_power.isChecked(),
            "hardware_label_style": self.label_style.currentData(),
            "cpu_load_high_threshold": float(self.cpu_load_high.value()),
            "cpu_load_low_threshold": float(self.cpu_load_low.value()),
            "gpu_load_high_threshold": float(self.gpu_load_high.value()),
            "gpu_load_low_threshold": float(self.gpu_load_low.value()),
            "throttle_temp_c": int(self.throttle_temp.value()),
        }
