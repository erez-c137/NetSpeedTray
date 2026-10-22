"""
Hardware Monitoring Settings Page.
"""
from typing import Dict, Any, Callable, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QLabel, QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout,
                             QLineEdit, QPushButton)

from netspeedtray import constants
from netspeedtray.core import gpu_adapters
from netspeedtray.utils import styles as su, helpers
from netspeedtray.utils.components import Win11Toggle, Win11Slider, SettingCard, SettingExpander, Win11ComboBox
from netspeedtray.views.settings.pages._fluent import section_header, page_layout

class HardwarePage(QWidget):
    layout_changed = pyqtSignal()
    # Emitted when a hardware monitor is switched ON. The dialog forwards it to the Widget page (which
    # now owns the display-mode control) so the widget switches out of network-only and the freshly
    # enabled stat is actually visible - the convenience this page used to do itself.
    hardware_enabled = pyqtSignal()

    # Config keys of the two custom label colours; each has a "<key>_button" swatch and "<key>_input" hex.
    # Throttle temperature ceiling, Celsius - the config schema's max for throttle_temp_c.
    THROTTLE_MAX_C = int(constants.config.defaults.VALIDATION_SCHEMA["throttle_temp_c"]["max"])
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

        # GPU picker (#236), small form: Automatic (the busiest GPU - the readout before the picker)
        # or one adapter by its DXGI name. Shown only on a machine with two or more GPUs (or with a
        # pick saved for a GPU that is unplugged now, so the user can switch back); load_settings
        # fills it. Temperature / power follow only a sensor of the picked GPU's maker (description).
        self.gpu_adapter = Win11ComboBox()
        self.gpu_adapter.setMinimumWidth(200)
        self.gpu_adapter.currentIndexChanged.connect(self.on_change)
        self._gpu_adapter_card = SettingCard(self.i18n.GPU_ADAPTER_LABEL,
                                             description=self.i18n.GPU_ADAPTER_DESCRIPTION,
                                             control=self.gpu_adapter)
        layout.addWidget(self._gpu_adapter_card)
        self._gpu_adapter_card.setVisible(False)

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

        # °C / °F (rc.2 F8). Display only - readings, history, the export and throttle_temp_c stay
        # Celsius. Never greyed: the Monitor shows temperatures whatever the taskbar toggles say.
        self.temperature_unit = Win11ComboBox()
        self.temperature_unit.addItem(self.i18n.TEMPERATURE_UNIT_CELSIUS,
                                      userData=constants.hardware.temperature.CELSIUS)
        self.temperature_unit.addItem(self.i18n.TEMPERATURE_UNIT_FAHRENHEIT,
                                      userData=constants.hardware.temperature.FAHRENHEIT)
        self.temperature_unit.setMinimumWidth(200)
        self.temperature_unit.currentIndexChanged.connect(self._on_temperature_unit_changed)
        layout.addWidget(SettingCard(self.i18n.TEMPERATURE_UNIT_LABEL, control=self.temperature_unit))

        self.show_power = Win11Toggle(label_text="")
        self.show_power.toggled.connect(self.on_change)
        self._power_card = SettingCard(self.i18n.SHOW_HARDWARE_POWER_LABEL, control=self.show_power)
        layout.addWidget(self._power_card)

        temps_note = QLabel(self.i18n.HARDWARE_TEMPS_LIMITATION_NOTE)
        temps_note.setWordWrap(True)
        temps_note.setStyleSheet(
            f"color: {su.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        layout.addWidget(temps_note)
        # LHM v0.9.5+ is read over its web server (#187), which is off until the user enables it.
        self.lhm_web_server_hint = QLabel(
            self.i18n.LHM_WEB_SERVER_HINT.format(port=constants.lhm.HTTP_PORT))
        self.lhm_web_server_hint.setWordWrap(True)
        self.lhm_web_server_hint.setStyleSheet(temps_note.styleSheet())
        layout.addWidget(self.lhm_web_server_hint)

        # Throttle temperature (Statistics): flags time spent at/above this temp in the Stats-detail
        # sheet so thermal throttling is provable (0 = off). Only meaningful when temps are collected,
        # so it sits right after the temperature toggle and the sensor note (2.2 IA pass).
        # Shown in the temperature unit, stored as throttle_temp_c (see _throttle_c / _show_throttle).
        self._throttle_unit = constants.hardware.temperature.CELSIUS
        self.throttle_temp = Win11Slider(editable=True, suffix=helpers.temperature_symbol(self._throttle_unit))
        self.throttle_temp.setRange(0, self.THROTTLE_MAX_C)
        # 0 turns the feature off: say "Off", not "0°C" (same word as the plan-speed spinbox's 0).
        self.throttle_temp.setSpecialValueText(str(getattr(self.i18n, "CONN_PLAN_OFF", "Off")))
        self.throttle_temp.setMinimumWidth(260)
        self.throttle_temp.valueChanged.connect(self.on_change)
        layout.addWidget(SettingCard(str(getattr(self.i18n, "HW_THROTTLE_LABEL", "Throttle temp (stats)")),
                                     control=self.throttle_temp))

        # --- Appearance: how the CPU/GPU readouts look (indicator shape/colour, colour by load). Reuses
        # the sidebar's "Appearance" string as the section caption, so it needs no new translation.
        layout.addWidget(section_header(self.i18n.APPEARANCE_SETTINGS_GROUP))

        # Where the readouts show (side-taskbar design phase C): first under Appearance, because it is
        # the biggest "how hardware looks" choice - on the taskbar, or only in the hover card, leaving
        # a clean speed-only widget. Keeps the Widget page's Display Mode for when it is turned off.
        # Greyed with CPU and GPU both off (nothing to move; RAM/VRAM ride on those lines).
        self.hardware_in_hover_card = Win11Toggle(label_text="")
        self.hardware_in_hover_card.toggled.connect(self.on_change)
        self._hover_card_card = SettingCard(self.i18n.HARDWARE_IN_HOVER_CARD_LABEL,
                                            description=self.i18n.HARDWARE_IN_HOVER_CARD_DESCRIPTION,
                                            control=self.hardware_in_hover_card)
        layout.addWidget(self._hover_card_card)

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
        self.hardware_in_hover_card.blockSignals(True)
        self.temperature_unit.blockSignals(True)
        self.gpu_adapter.blockSignals(True)

        self.hardware_in_hover_card.setChecked(bool(config.get(
            "hardware_in_hover_card", constants.config.defaults.DEFAULT_HARDWARE_IN_HOVER_CARD)))
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

        unit_idx = self.temperature_unit.findData(helpers.temperature_unit(config))
        self.temperature_unit.setCurrentIndex(max(0, unit_idx))
        self._load_gpu_adapters(str(config.get("gpu_adapter", constants.gpu.ADAPTER_AUTO) or ""))
        self._show_throttle(float(config.get("throttle_temp_c", 0) or 0),
                            self.temperature_unit.currentData())

        self.monitor_cpu.blockSignals(False)
        self.monitor_gpu.blockSignals(False)
        self.monitor_ram.blockSignals(False)
        self.monitor_vram.blockSignals(False)
        self.show_temps.blockSignals(False)
        self.show_power.blockSignals(False)
        self.show_memory_labels.blockSignals(False)
        self.hardware_in_hover_card.blockSignals(False)
        self.temperature_unit.blockSignals(False)
        self.gpu_adapter.blockSignals(False)
        # Signals were blocked above, so _on_monitor_toggled never fired - sync the dependent
        # (temp/power) cards' enabled state to the just-loaded CPU/GPU monitor values directly.
        self._sync_dependent_cards()
        # widget_display_mode / widget_display_order are loaded by the Widget page now.

        d = constants.config.defaults
        self.cpu_load_high.setValue(int(config.get("cpu_load_high_threshold", d.DEFAULT_CPU_LOAD_HIGH_THRESHOLD)))
        self.cpu_load_low.setValue(int(config.get("cpu_load_low_threshold", d.DEFAULT_CPU_LOAD_LOW_THRESHOLD)))
        self.gpu_load_high.setValue(int(config.get("gpu_load_high_threshold", d.DEFAULT_GPU_LOAD_HIGH_THRESHOLD)))
        self.gpu_load_low.setValue(int(config.get("gpu_load_low_threshold", d.DEFAULT_GPU_LOAD_LOW_THRESHOLD)))

    # --- GPU picker / temperature unit -------------------------------------------------------
    def _load_gpu_adapters(self, saved: str) -> None:
        """Fill the GPU picker from this machine's real adapters (DXGI, ~0.3 ms, no device created).
        A saved pick that is not present right now stays listed, so saving does not drop it."""
        self.gpu_adapter.clear()
        self.gpu_adapter.addItem(self.i18n.GPU_ADAPTER_AUTO, userData=constants.gpu.ADAPTER_AUTO)
        names = []
        for a in gpu_adapters.hardware_adapters(gpu_adapters.enumerate_adapters() or []):
            name = a.name.strip()
            if name and name.casefold() not in {n.casefold() for n in names}:
                names.append(name)
        for name in names:
            self.gpu_adapter.addItem(name, userData=name)
        saved = saved.strip()
        idx = 0
        if saved:
            idx = next((i for i in range(self.gpu_adapter.count())
                        if str(self.gpu_adapter.itemData(i)).casefold() == saved.casefold()), -1)
            if idx < 0:
                self.gpu_adapter.addItem(saved, userData=saved)
                idx = self.gpu_adapter.count() - 1
        self.gpu_adapter.setCurrentIndex(idx)
        self._gpu_adapter_card.setVisible(len(names) >= 2 or bool(saved))

    def _throttle_c(self) -> int:
        """The throttle slider's value as stored: whole Celsius, 0 = off."""
        shown = int(self.throttle_temp.value())
        if shown <= self.throttle_temp.slider.minimum():   # the "Off" stop (0 °C / 32 °F)
            return 0
        c = int(round(helpers.unit_to_celsius(shown, self._throttle_unit)))
        return max(1, min(self.THROTTLE_MAX_C, c))   # a non-zero pick never collapses to "Off"

    def _show_throttle(self, celsius: float, unit: str) -> None:
        """Show a stored throttle temperature (Celsius) on the slider in ``unit``."""
        self._throttle_unit = unit if unit else constants.hardware.temperature.CELSIUS
        self.throttle_temp.blockSignals(True)
        self.throttle_temp.setSuffix(helpers.temperature_symbol(self._throttle_unit))
        # The scale starts at the user-unit value of the Celsius minimum (0 °C = 32 °F, shown "Off"),
        # so every stop above it is a real, positive Celsius threshold (L6: a 0 °F floor saved
        # anything under ~33 °F as 1 °C).
        lo = int(round(helpers.celsius_to_unit(0, self._throttle_unit)))
        hi = int(round(helpers.celsius_to_unit(self.THROTTLE_MAX_C, self._throttle_unit)))
        self.throttle_temp.setRange(lo, hi)
        self.throttle_temp.setValue(int(round(helpers.celsius_to_unit(celsius, self._throttle_unit)))
                                    if celsius > 0 else lo)
        self.throttle_temp.blockSignals(False)
        self.throttle_temp.setSpecialValueText(str(getattr(self.i18n, "CONN_PLAN_OFF", "Off")))

    def _on_temperature_unit_changed(self, _index: int) -> None:
        self._show_throttle(self._throttle_c(), self.temperature_unit.currentData())
        self.on_change()

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
        self._set_card_enabled(self._hover_card_card, has_util)
        self._set_card_enabled(self._ram_card, cpu_on)
        self._set_card_enabled(self._vram_card, gpu_on)
        self._set_card_enabled(self._gpu_adapter_card, gpu_on)
        mem_on = (cpu_on and self.monitor_ram.isChecked()) or (gpu_on and self.monitor_vram.isChecked())
        self._set_card_enabled(self._mem_labels_card, mem_on)
        # A greyed card says why (rc.2 G6). Qt shows a tooltip on a disabled widget, and the card's
        # children (the dimmed toggle) carry none of their own, so hovering anywhere on it explains.
        needs_util = self.i18n.RECORD_HARDWARE_HISTORY_INACTIVE_SUB
        self._temps_card.setToolTip(self.i18n.SHOW_HARDWARE_TEMPS_TOOLTIP if has_util
                                    else f"{needs_util}\n\n{self.i18n.SHOW_HARDWARE_TEMPS_TOOLTIP}")
        self._power_card.setToolTip(self.i18n.SHOW_HARDWARE_POWER_TOOLTIP if has_util
                                    else f"{needs_util}\n\n{self.i18n.SHOW_HARDWARE_POWER_TOOLTIP}")
        self._hover_card_card.setToolTip("" if has_util else needs_util)
        self._ram_card.setToolTip("" if cpu_on else self.i18n.HARDWARE_NEEDS_CPU_TOOLTIP)
        self._vram_card.setToolTip("" if gpu_on else self.i18n.HARDWARE_NEEDS_GPU_TOOLTIP)
        self._gpu_adapter_card.setToolTip("" if gpu_on else self.i18n.HARDWARE_NEEDS_GPU_TOOLTIP)
        self._mem_labels_card.setToolTip("" if mem_on else self._mem_labels_reason(cpu_on, gpu_on))

    def _mem_labels_reason(self, cpu_on: bool, gpu_on: bool) -> str:
        """Why the memory-labels card is greyed, for the switches as they are now (L5): the memory
        readouts are off, or they are on but their host monitor (CPU for RAM, GPU for VRAM) is off."""
        ram, vram = self.monitor_ram.isChecked(), self.monitor_vram.isChecked()
        needs_cpu, needs_gpu = ram and not cpu_on, vram and not gpu_on
        if needs_cpu and needs_gpu:
            return self.i18n.RECORD_HARDWARE_HISTORY_INACTIVE_SUB   # "Turn on CPU or GPU monitoring..."
        if needs_cpu:
            return self.i18n.HARDWARE_NEEDS_CPU_TOOLTIP
        if needs_gpu:
            return self.i18n.HARDWARE_NEEDS_GPU_TOOLTIP
        return self.i18n.SHOW_MEMORY_LABELS_INACTIVE_TOOLTIP

    def get_settings(self) -> Dict[str, Any]:
        return {
            "monitor_cpu_enabled": self.monitor_cpu.isChecked(),
            "monitor_gpu_enabled": self.monitor_gpu.isChecked(),
            "monitor_ram_enabled": self.monitor_ram.isChecked(),
            "monitor_vram_enabled": self.monitor_vram.isChecked(),
            "show_memory_labels": self.show_memory_labels.isChecked(),
            "show_hardware_temps": self.show_temps.isChecked(),
            "show_hardware_power": self.show_power.isChecked(),
            "hardware_in_hover_card": self.hardware_in_hover_card.isChecked(),
            "hardware_label_style": self.label_style.currentData(),
            "hardware_label_color": self.label_color.currentData(),
            "cpu_label_color": self._label_hex("cpu_label_color"),
            "gpu_label_color": self._label_hex("gpu_label_color"),
            "cpu_load_high_threshold": float(self.cpu_load_high.value()),
            "cpu_load_low_threshold": float(self.cpu_load_low.value()),
            "gpu_load_high_threshold": float(self.gpu_load_high.value()),
            "gpu_load_low_threshold": float(self.gpu_load_low.value()),
            "throttle_temp_c": self._throttle_c(),
            "temperature_unit": self.temperature_unit.currentData() or constants.hardware.temperature.CELSIUS,
            "gpu_adapter": str(self.gpu_adapter.currentData() or constants.gpu.ADAPTER_AUTO),
        }
