"""
Interfaces Settings Page.
"""
from typing import Dict, Any, Callable, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QRadioButton, QScrollArea
)

from netspeedtray import constants
from netspeedtray.utils.components import Win11Toggle
from netspeedtray.constants import styles as style_constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils.interface_classifier import InterfaceClassifier, get_classifier, group_interfaces
from netspeedtray.views.settings.pages._fluent import section_header, page_layout
from netspeedtray.views.settings.pages.datacap_section import DataCapSettings
from netspeedtray.views.settings.pages.connection_section import ConnectionSettings
from netspeedtray.views.settings.pages.network_identity_section import NetworkIdentitySettings

class InterfacesPage(QWidget):
    layout_changed = pyqtSignal()

    def __init__(self, i18n, available_interfaces: List[str], on_change: Callable[[], None],
                 classifier: Optional[InterfaceClassifier] = None,
                 primary_interface: Optional[str] = None):
        super().__init__()
        self.i18n = i18n
        self.available_interfaces = available_interfaces
        self.on_change = on_change
        # Same classifier the all_physical aggregation uses, so "Physical adapters" here is exactly
        # the set that mode sums. The user's excluded_interfaces keywords are applied in load_settings.
        self._classifier = classifier or get_classifier()
        self._exclusions = list(constants.network.interface.DEFAULT_EXCLUSIONS)
        self.primary_interface = primary_interface
        self.interface_checkboxes: Dict[str, Win11Toggle] = {}
        self._setup_ui()

    def _setup_ui(self):
        # 2.0 IA: the monitoring-mode radios live in one Fluent card (a grouped option list, the native
        # Win11 idiom) instead of loose rows; the connection + data-cap sections are Fluent expanders.
        layout = page_layout(self)
        c = style_utils.semantic_colors()

        # --- Monitoring mode (radio group in a card) ---
        layout.addWidget(section_header(self.i18n.MONITORING_MODE_LABEL))
        mode_card = QFrame()
        mode_card.setObjectName("modeCard")
        mode_card.setStyleSheet(
            f"#modeCard {{ background: {c['card_bg']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}")
        ml = QVBoxLayout(mode_card)
        ml.setContentsMargins(16, 12, 16, 12)
        ml.setSpacing(2)

        self.auto_interface_radio = QRadioButton(self.i18n.MONITORING_MODE_AUTO)
        self.all_physical_interfaces_radio = QRadioButton(self.i18n.MONITORING_MODE_PHYSICAL)
        self.all_virtual_interfaces_radio = QRadioButton(self.i18n.MONITORING_MODE_VIRTUAL)
        self.selected_interfaces_radio = QRadioButton(self.i18n.MONITORING_MODE_SELECTED)

        # Subtitle under each option (muted, indented to align under the radio label) - the Win11
        # "option + helper text" pattern.
        subtitle_style = (f"color: {c['text_secondary']}; font-size: 11px;"
                          f" margin-left: 24px; margin-bottom: 6px; background: transparent;")
        radio_subtitle_pairs = [
            (self.auto_interface_radio, self.i18n.MONITORING_MODE_AUTO_SUBTITLE),
            (self.all_physical_interfaces_radio, self.i18n.MONITORING_MODE_PHYSICAL_SUBTITLE),
            (self.all_virtual_interfaces_radio, self.i18n.MONITORING_MODE_VIRTUAL_SUBTITLE),
            (self.selected_interfaces_radio, self.i18n.MONITORING_MODE_SELECTED_SUBTITLE),
        ]
        # D5 (rc.2): what a mode METERS, for the data cap too - Auto follows the one routing adapter
        # (the VPN's, with a full-tunnel VPN up), Physical sums the real NICs and leaves tunnels out.
        tooltips = {
            self.auto_interface_radio: self.i18n.MONITORING_MODE_AUTO_TOOLTIP.format(
                mode=self.i18n.MONITORING_MODE_PHYSICAL),
            self.all_physical_interfaces_radio: self.i18n.MONITORING_MODE_PHYSICAL_TOOLTIP,
        }
        for radio, subtitle_text in radio_subtitle_pairs:
            radio.toggled.connect(self._on_mode_toggled)
            ml.addWidget(radio)
            subtitle = QLabel(subtitle_text)
            subtitle.setWordWrap(True)
            subtitle.setStyleSheet(subtitle_style)
            if radio in tooltips:
                radio.setToolTip(tooltips[radio])
                subtitle.setToolTip(tooltips[radio])
            ml.addWidget(subtitle)

        # The per-interface checkbox list (revealed only for "selected" mode), inside the same card.
        self.interface_scroll = QScrollArea()
        self.interface_scroll.setWidgetResizable(True)
        self.interface_scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollArea > QWidget > QWidget { background: transparent; }
        """)
        self.interface_scroll.viewport().setAutoFillBackground(False)
        self.interface_scroll.setVisible(False)
        self.interface_scroll.setAccessibleName(self.i18n.MONITORING_MODE_SELECTED)   # it takes focus

        interfaces_container = QWidget()
        interfaces_container.setStyleSheet("background: transparent;")
        self.interfaces_container_layout = QVBoxLayout(interfaces_container)
        self.interfaces_container_layout.setSpacing(constants.layout.VERTICAL_SPACING)
        self._populate_interface_list()
        interfaces_container.setLayout(self.interfaces_container_layout)
        self.interface_scroll.setWidget(interfaces_container)
        ml.addWidget(self.interface_scroll)

        layout.addWidget(mode_card)

        # Order (2.2 IA pass): Data usage, Connection, Network identity. Data usage has its own tray
        # entry and was last on the page below two open expanders; identity is the most cosmetic.

        # Data-usage / data-cap section (the feature's in-settings home, 2.0 IA).
        self.datacap = DataCapSettings(self.on_change, self.i18n)
        layout.addWidget(self.datacap)

        # Connection section - latency monitoring (gateway + opt-in public anchor) + advertised plan.
        self.connection = ConnectionSettings(self.on_change, self.i18n)
        layout.addWidget(self.connection)

        # Network identity (v2.1) - the Wi-Fi band / network indicator on the widget.
        self.identity = NetworkIdentitySettings(self.on_change, self.i18n)
        layout.addWidget(self.identity)

        layout.addStretch()

    def _on_mode_toggled(self, checked: bool):
        self._update_visibility()
        if checked:
            self.on_change()

    def _update_visibility(self):
        should_be_visible = self.selected_interfaces_radio.isChecked()
        if self.interface_scroll.isVisible() != should_be_visible:
            self.interface_scroll.setVisible(should_be_visible)
            self.layout_changed.emit()

    def _populate_interface_list(self):
        # Clear existing
        while self.interfaces_container_layout.count():
            child = self.interfaces_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self.interface_checkboxes.clear()

        listed = set(self.available_interfaces or [])
        missing = [n for n in getattr(self, "_missing_selected", []) if n not in listed]
        if self.available_interfaces or missing:
            # Two sections - real adapters, then virtual ones (VPNs, WSL/Hyper-V, VM host-only,
            # Wi-Fi Direct) - with the routing connection first in its section and marked, so the
            # user can tell which entry is their actual connection (2.2 scope C).
            groups = group_interfaces(
                self.available_interfaces,
                lambda n: self._classifier.kind(n, self._exclusions),
                self.primary_interface)
            sections = ((self.i18n.INTERFACE_GROUP_PHYSICAL, groups.physical),
                        (self.i18n.INTERFACE_GROUP_VIRTUAL, groups.virtual))
            # A selected adapter that isn't present right now (VPN off, dongle out) keeps its row - and
            # its tick - under its own heading, so an untouched Save doesn't drop it (S7).
            sections = sections + ((getattr(self.i18n, "INTERFACE_GROUP_NOT_CONNECTED",
                                            "Not connected right now"), missing),)
            first = True
            for title, names in sections:
                if not names:
                    continue
                self.interfaces_container_layout.addWidget(self._group_header(title, first))
                first = False
                for iface in names:
                    self.interfaces_container_layout.addWidget(
                        self._interface_row(iface, iface == groups.primary))
        else:
            no_iface_label = QLabel(self.i18n.NO_INTERFACES_FOUND)
            is_dark = style_utils.is_dark_mode()
            subtle_color = style_constants.SUBTLE_TEXT_COLOR_DARK if is_dark else style_constants.SUBTLE_TEXT_COLOR_LIGHT
            no_iface_label.setStyleSheet(f"color: {subtle_color}; font-style: italic;")
            self.interfaces_container_layout.addWidget(no_iface_label)

        self.interfaces_container_layout.addStretch()

    def _group_header(self, text: str, first: bool) -> QLabel:
        """A muted caption over each section (the Win11 grouped-list idiom - no band, no stroke)."""
        c = style_utils.semantic_colors()
        lbl = QLabel(text)
        lbl.setProperty("pickerRole", "header")
        lbl.setFont(style_utils.font(tokens.TYPE_CAPTION))
        top = 0 if first else tokens.SPACE_S
        lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;"
                          f" padding: {top}px 0px 0px 0px; margin: 0px;")
        return lbl

    def _interface_row(self, iface: str, is_primary: bool) -> QWidget:
        """The toggle as before; the primary connection gets a small accent caption after it."""
        row = QWidget()
        row.setProperty("pickerRole", "row")
        row.setProperty("interfaceName", iface)
        row.setProperty("isPrimary", is_primary)
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(tokens.SPACE_M)
        checkbox = Win11Toggle(iface)
        self.interface_checkboxes[iface] = checkbox
        checkbox.toggled.connect(self.on_change)
        hl.addWidget(checkbox)
        if is_primary:
            c = style_utils.semantic_colors()
            mark = QLabel(self.i18n.INTERFACE_ACTIVE_CONNECTION)
            mark.setProperty("pickerRole", "primaryMark")
            mark.setFont(style_utils.font(tokens.TYPE_CAPTION))
            mark.setStyleSheet(f"color: {c['accent']}; background: transparent;")
            hl.addWidget(mark)
            # On the focusable control, not the row: a screen reader announces what has focus.
            checkbox.checkbox.setAccessibleName(f"{iface} - {self.i18n.INTERFACE_ACTIVE_CONNECTION}")
        hl.addStretch(1)
        return row

    def update_interface_list(self, new_interfaces: List[str],
                              primary_interface: Optional[str] = None):
        self.available_interfaces = new_interfaces or []
        # None means "no route right now": clear the mark rather than leave it on a stale adapter.
        self.primary_interface = primary_interface
        self._populate_interface_list()
        # Note: calling code needs to re-apply selection state (load_settings) after update if needed

    def load_settings(self, config: Dict[str, Any]):
        exclusions = list(config.get("excluded_interfaces", constants.network.interface.DEFAULT_EXCLUSIONS) or [])
        if exclusions != self._exclusions:
            # A customised exclusion list moves adapters between the sections, exactly as it moves
            # them in and out of the all_physical sum. Regroup before the selection is re-applied.
            self._exclusions = exclusions
            self._populate_interface_list()
        mode = config.get("interface_mode", "auto")
        if mode == "auto":
            self.auto_interface_radio.setChecked(True)
        elif mode == "all_physical":
            self.all_physical_interfaces_radio.setChecked(True)
        elif mode == "all_virtual":
            self.all_virtual_interfaces_radio.setChecked(True)
        elif mode == "selected":
            self.selected_interfaces_radio.setChecked(True)
        
        self._update_visibility()
        
        selected_list = list(config.get("selected_interfaces", []) or [])
        missing = [n for n in selected_list if n not in set(self.available_interfaces or [])]
        if missing != getattr(self, "_missing_selected", []):
            self._missing_selected = missing
            self._populate_interface_list()
        for iface, checkbox in self.interface_checkboxes.items():
            if iface in selected_list:
                checkbox.setChecked(True)
            else:
                checkbox.setChecked(False)

        self.identity.load_settings(config)
        self.connection.load_settings(config)
        self.datacap.load_settings(config)

    def get_settings(self) -> Dict[str, Any]:
        mode = "auto"
        if self.all_physical_interfaces_radio.isChecked(): mode = "all_physical"
        elif self.all_virtual_interfaces_radio.isChecked(): mode = "all_virtual"
        elif self.selected_interfaces_radio.isChecked(): mode = "selected"

        selected = [iface for iface, cb in self.interface_checkboxes.items() if cb.isChecked()]

        settings = {
            "interface_mode": mode,
            "selected_interfaces": selected,
        }
        settings.update(self.identity.get_settings())
        settings.update(self.connection.get_settings())
        settings.update(self.datacap.get_settings())
        return settings
