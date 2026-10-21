"""
Mini Graph settings section (Widget page).

Moved from the Appearance page by the 2.2 Settings IA pass: the graph is a "what the widget shows"
decision (it gained split graphs, a fixed scale and a plan-speed line, and the hardware graphs follow
the display mode), so it sits on the Widget page with the other layout choices. It goes LAST there,
so the fixed-scale reveal grows at the bottom of the page and shifts nothing.

Embedded as a section (the way Appearance embeds UnitsPage / ColorsPage). The controls, their config
keys and the load/get wiring moved as-is.
"""
from typing import Dict, Any, Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils.helpers import format_percent
from netspeedtray.utils.components import Win11Slider, Win11Toggle, Win11ComboBox, SettingCard
from netspeedtray.views.settings.pages._fluent import section_header, page_layout, as_int


class MiniGraphSection(QWidget):
    """Show Mini Graph, style, timespan, opacity, split upload/download, fixed scale (+ headroom, plan line)."""

    layout_changed = pyqtSignal()

    def __init__(self, i18n, on_change: Callable[[], None]):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = page_layout(self)

        layout.addWidget(section_header(self.i18n.MINI_GRAPH_SECTION_TITLE))
        self.enable_graph = Win11Toggle(label_text="")
        self.enable_graph.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.ENABLE_GRAPH_LABEL, control=self.enable_graph))

        layout.addWidget(self._note(self.i18n.GRAPH_NOTE_TEXT))

        # Detailed (every sample) or Trend (a smoothed line) - the look of every mini graph, network and
        # hardware alike. Grays out with the graph off, like the split toggle.
        self.graph_style = Win11ComboBox()
        style_names = {"detailed": self.i18n.GRAPH_STYLE_DETAILED, "trend": self.i18n.GRAPH_STYLE_TREND}
        for style in constants.config.defaults.GRAPH_STYLE_CHOICES:
            self.graph_style.addItem(style_names[style], style)
        self.graph_style.currentIndexChanged.connect(lambda _i: self.on_change())
        self._style_card = SettingCard(self.i18n.GRAPH_STYLE_LABEL, self.i18n.GRAPH_STYLE_DESC,
                                       control=self.graph_style)
        layout.addWidget(self._style_card)

        self.history_duration = Win11Slider(editable=False)
        hist_min, hist_max = constants.ui.history.HISTORY_MINUTES_RANGE
        self.history_duration.setRange(hist_min, hist_max)
        self.history_duration.setMinimumWidth(260)
        self.history_duration.valueChanged.connect(self.on_change)
        self._duration_card = SettingCard(self.i18n.HISTORY_DURATION_LABEL, control=self.history_duration)
        layout.addWidget(self._duration_card)

        self.graph_opacity = Win11Slider(editable=False)
        self.graph_opacity.setRange(constants.ui.sliders.OPACITY_MIN, constants.ui.sliders.OPACITY_MAX)
        self.graph_opacity.setMinimumWidth(260)
        self.graph_opacity.valueChanged.connect(self.on_change)
        self._opacity_card = SettingCard(self.i18n.GRAPH_OPACITY_LABEL, control=self.graph_opacity)
        layout.addWidget(self._opacity_card)

        # The hardware graphs reuse the two settings right above, so the note sits under them (it used
        # to hang under Display Mode, a page away from the controls it describes).
        layout.addWidget(self._note(self.i18n.HARDWARE_GRAPH_NOTE))

        # Separate upload/download graphs (DU Meter-style), each on its own scale. It splits the mini
        # graph, so it grays out while the graph itself is off. Off by default.
        self.graph_split = Win11Toggle(label_text="")
        self.graph_split.toggled.connect(self.on_change)
        self._split_card = SettingCard(self.i18n.GRAPH_SPLIT_LABEL, self.i18n.GRAPH_SPLIT_DESC,
                                       control=self.graph_split)
        layout.addWidget(self._split_card)
        self.enable_graph.toggled.connect(lambda _on: self._sync_split_card())
        self.enable_graph.toggled.connect(lambda _on: self._sync_style_card())
        self.enable_graph.toggled.connect(lambda _on: self._sync_graph_dependents())

        # Fixed scale (DU Meter-style): the axis tops out at the advertised plan speed instead of re-fitting
        # to the busiest recent sample. The plan speed is the one already set on the Network page, so there
        # is no second "how fast is my line" number to keep in sync. Off by default.
        self.graph_fixed_scale = Win11Toggle(label_text="")
        self.graph_fixed_scale.toggled.connect(self._on_graph_fixed_scale_toggle)
        self._fixed_card = SettingCard(self.i18n.GRAPH_FIXED_SCALE_LABEL, self.i18n.GRAPH_FIXED_SCALE_DESC,
                                       control=self.graph_fixed_scale)
        layout.addWidget(self._fixed_card)

        # Headroom + plan-speed line only mean something with the fixed scale on, so they sit in a
        # container shown with the toggle (same pattern as the custom arrow font/colors on Appearance).
        self.graph_scale_container = QWidget()
        scale_layout = QVBoxLayout(self.graph_scale_container)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(tokens.SPACE_XS)
        self.graph_scale_headroom = Win11ComboBox()
        for pct in constants.config.defaults.GRAPH_SCALE_HEADROOM_CHOICES:
            self.graph_scale_headroom.addItem(format_percent(pct, self.i18n), pct)   # "%10" in tr_TR
        self.graph_scale_headroom.currentIndexChanged.connect(self._on_graph_headroom_changed)
        scale_layout.addWidget(SettingCard(self.i18n.GRAPH_SCALE_HEADROOM_LABEL, control=self.graph_scale_headroom))
        self.graph_show_ceiling = Win11Toggle(label_text="")
        self.graph_show_ceiling.toggled.connect(self.on_change)
        self._ceiling_card = SettingCard(self.i18n.GRAPH_SHOW_CEILING_LABEL, self.i18n.GRAPH_SHOW_CEILING_DESC,
                                         control=self.graph_show_ceiling)
        scale_layout.addWidget(self._ceiling_card)
        layout.addWidget(self.graph_scale_container)

    @staticmethod
    def _note(text: str) -> QLabel:
        note = QLabel(text)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {style_utils.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        return note

    # --- config ------------------------------------------------------------------
    def load_settings(self, config: Dict[str, Any]) -> None:
        self.enable_graph.setChecked(config.get("graph_enabled", True))
        self.history_duration.setValue(as_int(config.get("history_minutes"),
                                              constants.config.defaults.DEFAULT_HISTORY_MINUTES))
        # graph_opacity may be a float on disk (schema: (int, float)); the slider takes int only.
        self.graph_opacity.setValue(as_int(config.get("graph_opacity"),
                                           constants.config.defaults.DEFAULT_GRAPH_OPACITY))
        d = constants.config.defaults
        self.graph_fixed_scale.setChecked(bool(config.get("graph_fixed_scale", d.DEFAULT_GRAPH_FIXED_SCALE)))
        headroom_idx = self.graph_scale_headroom.findData(
            config.get("graph_scale_headroom", d.DEFAULT_GRAPH_SCALE_HEADROOM))
        if headroom_idx < 0:
            headroom_idx = self.graph_scale_headroom.findData(d.DEFAULT_GRAPH_SCALE_HEADROOM)
        self.graph_scale_headroom.setCurrentIndex(headroom_idx)
        self.graph_show_ceiling.setChecked(bool(config.get("graph_show_ceiling", d.DEFAULT_GRAPH_SHOW_CEILING)))
        self.graph_scale_container.setVisible(self.graph_fixed_scale.isChecked())
        self._sync_ceiling_card()
        self.graph_split.setChecked(bool(config.get("graph_split_directions", d.DEFAULT_GRAPH_SPLIT_DIRECTIONS)))
        self._sync_split_card()
        style_idx = self.graph_style.findData(config.get("graph_style", d.DEFAULT_GRAPH_STYLE))
        if style_idx < 0:   # a hand-edited value the combo doesn't offer
            style_idx = self.graph_style.findData(d.DEFAULT_GRAPH_STYLE)
        self.graph_style.setCurrentIndex(style_idx)
        self._sync_style_card()
        self._sync_graph_dependents()

    def get_settings(self) -> Dict[str, Any]:
        return {
            "graph_enabled": self.enable_graph.isChecked(),
            "history_minutes": self.history_duration.value(),
            "graph_opacity": self.graph_opacity.value(),
            "graph_fixed_scale": self.graph_fixed_scale.isChecked(),
            "graph_scale_headroom": int(self.graph_scale_headroom.currentData()),
            "graph_show_ceiling": self.graph_show_ceiling.isChecked(),
            "graph_split_directions": self.graph_split.isChecked(),
            "graph_style": str(self.graph_style.currentData()),
        }

    # --- behavior ----------------------------------------------------------------
    def _on_graph_fixed_scale_toggle(self, checked: bool):
        self.graph_scale_container.setVisible(checked)
        self.layout_changed.emit()
        self.on_change()

    def _on_graph_headroom_changed(self, _index: int):
        self._sync_ceiling_card()
        self.on_change()

    def _sync_ceiling_card(self) -> None:
        """At 0% headroom the plan speed is the graph's top edge and no line is drawn, so gray the
        plan-speed-line toggle out."""
        self._set_card_enabled(self._ceiling_card, bool(self.graph_scale_headroom.currentData()))

    def _sync_split_card(self) -> None:
        """Separate upload/download graphs split the mini graph, so gray the toggle out while it's off."""
        self._set_card_enabled(self._split_card, self.enable_graph.isChecked())

    def _sync_graph_dependents(self) -> None:
        """Timespan, opacity and the fixed scale (with its headroom / plan line) only shape the mini
        graph, so they grey out while it is off, like the style and split cards (S9)."""
        on = self.enable_graph.isChecked()
        for card in (self._duration_card, self._opacity_card, self._fixed_card):
            self._set_card_enabled(card, on)
        self.graph_scale_container.setEnabled(on)

    def _sync_style_card(self) -> None:
        """The style is the mini graph's look, so it grays out while the graph is off."""
        self._set_card_enabled(self._style_card, self.enable_graph.isChecked())

    @staticmethod
    def _set_card_enabled(card: SettingCard, enabled: bool) -> None:
        """The Win11 dependent-control look HardwarePage uses: setEnabled alone won't dim our fixed-color
        toggle, hence the opacity effect."""
        card.setEnabled(enabled)
        eff = card.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(card)
            card.setGraphicsEffect(eff)
        eff.setOpacity(1.0 if enabled else 0.4)
