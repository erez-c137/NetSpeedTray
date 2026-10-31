"""
Widget Settings Page (2.0 IA).

Everything about the on-taskbar widget itself - how its stats are laid out (display mode + order),
where it sits and how it behaves on the taskbar (preferred monitor, show on all taskbars, free-move,
free-float, keep-visible-in-fullscreen), and the mini graph. These controls used to be scattered across the
General page (behavior, preferred monitor), the Hardware page (layout) and the Appearance page (mini
graph); the 2.0 IA and the 2.2 IA pass gather them here so "the widget" is one place.

Slider→Segmented and QListWidget upgrades are intentionally NOT done here - the controls move as-is
(same widgets, same config mappings) so the reshuffle can't silently corrupt a saved value.
"""
from typing import Dict, Any, Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QWidget, QComboBox, QLabel

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.components import Win11Toggle, SettingCard, Win11ComboBox
from netspeedtray.views.settings.pages._fluent import section_header, page_layout
from netspeedtray.views.settings.pages.mini_graph_section import MiniGraphSection


class WidgetPage(QWidget):
    """Layout (display mode + order), on-taskbar placement/behavior, and the mini graph."""

    layout_changed = pyqtSignal()

    def __init__(self, i18n, on_change: Callable[[], None],
                 color_dialog_callback: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.i18n = i18n
        self.on_change = on_change
        self._color_dialog_callback = color_dialog_callback   # the Mini graph colour swatches
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = page_layout(self)

        # --- Display mode ---
        layout.addWidget(section_header(self.i18n.WIDGET_DISPLAY_MODE_LABEL))
        self.display_mode_combo = Win11ComboBox()
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_NETWORK, userData="network_only")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_COMBINED, userData="side_by_side")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_STACKED_COLUMN, userData="side_by_stack")
        self.display_mode_combo.addItem(self.i18n.DISPLAY_MODE_CYCLE, userData="cycle")
        self.display_mode_combo.currentIndexChanged.connect(self.on_change)
        # Under a section header, not in a SettingCard - so it names itself.
        self.display_mode_combo.setAccessibleName(self.i18n.WIDGET_DISPLAY_MODE_LABEL)
        layout.addWidget(self.display_mode_combo)
        # (HARDWARE_GRAPH_NOTE moved to the Mini Graph section below, next to the opacity/timespan
        # controls it describes.)
        # ONE note slot under Display Mode, saying where the hardware went (set_display_mode_note):
        # "side_bar" - docked to a side taskbar with no room for it; "hover_card" - the Hardware page's
        # "Show hardware only when hovering the widget" is on, so the bar shows network only on any edge.
        self.display_mode_note = QLabel("")
        self.display_mode_note.setWordWrap(True)
        self.display_mode_note.setStyleSheet(
            f"color: {su.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        self.display_mode_note.setVisible(False)
        self._display_mode_note_kind: Optional[str] = None
        layout.addWidget(self.display_mode_note)

        # --- Display order ---
        layout.addWidget(section_header(self.i18n.WIDGET_DISPLAY_ORDER_LABEL))
        self.pos_combos: List[QComboBox] = []
        for i in range(3):
            combo = Win11ComboBox()
            combo.addItem(self.i18n.ORDER_TYPE_NETWORK, userData="network")
            combo.addItem(self.i18n.ORDER_TYPE_CPU, userData="cpu")
            combo.addItem(self.i18n.ORDER_TYPE_GPU, userData="gpu")
            combo.addItem(self.i18n.ORDER_TYPE_NONE, userData="none")
            combo.setMinimumWidth(160)
            combo.currentIndexChanged.connect(lambda _, idx=i: self._on_pos_changed(idx))
            layout.addWidget(SettingCard(getattr(self.i18n, f"ORDER_POSITION_{i+1}"), control=combo))
            self.pos_combos.append(combo)

        # --- Behavior (was on the General page) ---
        layout.addWidget(section_header(self.i18n.BEHAVIOR_GROUP_TITLE))
        # Preferred Monitor (#72) - pin the widget to a specific display (default = primary). It picks
        # the taskbar the widget docks to, and "Float on a display without a taskbar" below only acts on
        # it, so it lives here with the other placement controls (moved from General, 2.2 IA pass).
        self.preferred_monitor_combo = Win11ComboBox()
        self.preferred_monitor_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preferred_monitor_combo.setMinimumWidth(220)
        self._populate_monitor_combo()   # (re)connects on_change itself - connecting here too doubled it
        layout.addWidget(SettingCard(self.i18n.PREFERRED_MONITOR_LABEL, control=self.preferred_monitor_combo))
        # rc.2 W13: a chosen monitor with no taskbar of its own, with floating off, silently docked the
        # widget to the PRIMARY taskbar. Say so right under the picker (see _sync_monitor_note).
        self.monitor_note = QLabel("")
        self.monitor_note.setWordWrap(True)
        self.monitor_note.setStyleSheet(
            f"color: {su.semantic_colors()['text_secondary']}; background: transparent; padding: 0 2px;")
        self.monitor_note.setVisible(False)
        layout.addWidget(self.monitor_note)

        # "Show on all taskbars" (#229): a copy of the widget on every other taskbar. Right under
        # Preferred Monitor, which still picks where the main widget (the one you can drag) docks.
        self.show_on_all_taskbars = Win11Toggle(label_text="")
        self.show_on_all_taskbars.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.SHOW_ON_ALL_TASKBARS_LABEL,
                                     description=self.i18n.SHOW_ON_ALL_TASKBARS_DESC,
                                     control=self.show_on_all_taskbars))

        self.free_move = Win11Toggle(label_text="")
        self.free_move.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.FREE_MOVE_LABEL, control=self.free_move))

        # #188: float the widget on a preferred monitor that has no taskbar of its own.
        self.free_float = Win11Toggle(label_text="")
        self.free_float.toggled.connect(self.on_change)
        self.free_float.toggled.connect(lambda _checked: self._sync_monitor_note())
        self._free_float_card = SettingCard(
            getattr(self.i18n, "FREE_FLOAT_LABEL", "Float on a display without a taskbar"),
            control=self.free_float)
        layout.addWidget(self._free_float_card)
        # Floating only ever applies to a chosen monitor (get_free_float_screen): with "Primary (auto)"
        # the switch does nothing, so it greys out (S9) - see _on_monitor_changed.
        self._sync_free_float_card()

        self.keep_visible_fullscreen = Win11Toggle(label_text="")
        self.keep_visible_fullscreen.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.KEEP_VISIBLE_FULLSCREEN_LABEL,
                                     control=self.keep_visible_fullscreen))

        # #24: on a horizontal Win11 taskbar the widget steps aside while the app buttons reach it.
        # Off = it stays put (and may cover them). The way back for a user who prefers that.
        self.hide_when_apps_cover = Win11Toggle(label_text="")
        self.hide_when_apps_cover.toggled.connect(self.on_change)
        layout.addWidget(SettingCard(self.i18n.HIDE_WHEN_APPS_COVER_LABEL,
                                     description=self.i18n.HIDE_WHEN_APPS_COVER_DESC,
                                     control=self.hide_when_apps_cover))

        # NOTE: there is intentionally no tray-offset slider. Free Move (drag the widget anywhere) is the
        # reposition escape hatch; the auto-position uses sensible default offsets. tray_offset_x/y remain
        # in config for the position engine (position_manager) but aren't surfaced as fiddly px sliders.

        # --- Mini Graph (moved from Appearance, 2.2 IA pass) - LAST on the page, so its fixed-scale
        # reveal grows at the bottom and shifts nothing above it.
        self.mini_graph_section = MiniGraphSection(self.i18n, self.on_change, self._color_dialog_callback)
        self.mini_graph_section.layout_changed.connect(self.layout_changed)
        layout.addWidget(self.mini_graph_section)

        layout.addStretch()

    # --- preferred monitor ------------------------------------------------------
    def _populate_monitor_combo(self) -> None:
        """Fill the preferred-monitor dropdown with detected screens.

        First entry is always "Primary (auto)" mapped to None. Subsequent
        entries are real QScreens labeled with their resolution. We store
        the screen's name() as userData so reselection survives reboots,
        while showing a friendlier label.

        Called on construction AND on every showEvent so users who plug in
        a monitor after the app started still see it in the dropdown.
        """
        # Remember the current selection so we can preserve it across refresh.
        previous_selection = self.preferred_monitor_combo.currentData() if self.preferred_monitor_combo.count() else None

        # Temporarily disconnect to avoid firing on_change during the rebuild.
        try:
            self.preferred_monitor_combo.currentIndexChanged.disconnect(self._on_monitor_changed)
        except (TypeError, RuntimeError):
            pass

        self.preferred_monitor_combo.clear()
        self.preferred_monitor_combo.addItem(self.i18n.PREFERRED_MONITOR_PRIMARY, userData=None)

        app = QGuiApplication.instance()
        if app is not None:
            primary = app.primaryScreen()
            for i, screen in enumerate(app.screens(), start=1):
                geom = screen.geometry()
                label = self.i18n.PREFERRED_MONITOR_ITEM.format(index=i, width=geom.width(),
                                                                height=geom.height())
                if screen is primary:
                    label = f"{label} {self.i18n.PREFERRED_MONITOR_PRIMARY_TAG}"
                self.preferred_monitor_combo.addItem(label, userData=screen.name())

        # The saved monitor, while it is unplugged (S7): listed as "(not connected)" and kept, so an
        # untouched Save doesn't silently reset the choice to Primary. The runtime already falls back
        # to the primary display while it is away (position_manager).
        saved = getattr(self, "_saved_monitor", None)
        if saved and self.preferred_monitor_combo.findData(saved) < 0:
            # The device name ("\\.\DISPLAY2") is left-to-right text whose leading "\\." are
            # direction-neutral: in Hebrew they jumped to the far end ("2DISPLAY\.\\"). An LTR
            # embedding (LRE ... PDF) plus an LRM after it keeps it one left-to-right unit - its
            # trailing digit included - in any UI direction (Qt's bidi, checked with QTextLayout).
            # In a right-to-left UI a leading RLM makes the line itself RTL (Qt picks a label's
            # direction from its first strong character, which would otherwise be the "D").
            rtl = QGuiApplication.layoutDirection() == Qt.LayoutDirection.RightToLeft
            self.preferred_monitor_combo.addItem(
                f"{chr(0x200F) if rtl else ''}\u202a{saved}\u202c\u200e {self.i18n.SETTINGS_NOT_CONNECTED_TAG}",
                userData=saved)

        # Restore previous selection if it still exists; otherwise fall back to "Primary (auto)".
        if previous_selection is not None:
            idx = self.preferred_monitor_combo.findData(previous_selection)
            if idx >= 0:
                self.preferred_monitor_combo.setCurrentIndex(idx)

        # Reconnect the change signal.
        self.preferred_monitor_combo.currentIndexChanged.connect(self._on_monitor_changed)
        self._sync_free_float_card()

    def _on_monitor_changed(self, _index: int = 0) -> None:
        self._sync_free_float_card()
        self.on_change()

    def _sync_free_float_card(self) -> None:
        if hasattr(self, "_free_float_card"):
            MiniGraphSection._set_card_enabled(self._free_float_card,
                                               self.preferred_monitor_combo.currentData() is not None)
        self._sync_monitor_note()

    def _sync_monitor_note(self) -> None:
        """Show the "no taskbar on this monitor" note (rc.2 W13) when the chosen monitor is connected,
        has no taskbar of its own and floating is off - the one case where the widget docks to the
        primary taskbar instead. With floating on it floats there (#188); an unplugged monitor already
        reads "(not connected)" in the list. Re-checked on every change and each time the page shows."""
        note = getattr(self, "monitor_note", None)
        if note is None or not hasattr(self, "free_float"):
            return
        name = self.preferred_monitor_combo.currentData()
        show = False
        if name and not self.free_float.isChecked():
            try:
                from netspeedtray.utils.taskbar_utils import get_free_float_screen
                show = get_free_float_screen(name) is not None   # connected AND taskbar-less
            except Exception:
                show = False
        if show:
            note.setText(self.i18n.PREFERRED_MONITOR_NO_TASKBAR_NOTE.format(
                option=self.i18n.FREE_FLOAT_LABEL))
        if note.isVisibleTo(self) != show:
            note.setVisible(show)
            self.layout_changed.emit()

    def showEvent(self, event):  # type: ignore[override]
        """Refresh the monitor dropdown whenever the page becomes visible.

        Catches the case where a user plugs/unplugs a monitor while the app
        is running, then opens Settings - the dropdown should reflect the
        current monitor topology, not the topology at app launch.
        """
        super().showEvent(event)
        if hasattr(self, "preferred_monitor_combo"):
            self._populate_monitor_combo()

    # --- behavior ---------------------------------------------------------------
    def set_display_mode_note(self, kind: Optional[str]) -> None:
        """The note under Display Mode: None (hidden), "side_bar" (a side taskbar has no room for the
        hardware - it is in the hover card) or "hover_card" (the hover-card switch is on: the bar shows
        network only, and this Display Mode returns when it is turned off). The dialog decides on every
        preview update, so it follows the settings live; one label, so only one note ever shows."""
        texts = {"side_bar": self.i18n.SIDE_TASKBAR_HOVER_NOTE,
                 "hover_card": self.i18n.HOVER_CARD_DISPLAY_MODE_NOTE}
        kind = kind if kind in texts else None
        if kind == self._display_mode_note_kind:
            return
        self._display_mode_note_kind = kind
        if kind is not None:
            self.display_mode_note.setText(texts[kind])
        self.display_mode_note.setVisible(kind is not None)
        self.layout_changed.emit()

    def ensure_hardware_visible(self) -> None:
        """Called by the dialog when a hardware monitor is enabled on the Hardware page: switch the
        widget out of network-only so the freshly-enabled stat is actually visible. This is the
        convenience the Hardware page did itself before display-mode moved to this page."""
        if self.display_mode_combo.currentData() == "network_only":
            # Back to the side-by-side layout the user had: stacked if that is what they last chose.
            target = "side_by_stack" if getattr(self, "_stack_loaded", False) else "side_by_side"
            idx = self.display_mode_combo.findData(target)
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
        # Remembered for the modes the dropdown can't express it in (network only / cycle): an
        # untouched Save there must not flip the stored preference (review item 3).
        self._stack_loaded = bool(config.get("stack_hardware_stats",
                                             constants.config.defaults.DEFAULT_CONFIG.get("stack_hardware_stats", False)))
        # "side_by_stack" is the side-by-side layout with the hardware column stacked - encoded as
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

        # Preferred monitor (#72) - match by stored screen name. A saved monitor that isn't
        # connected right now stays selectable as "(not connected)" (S7) - it used to fall to
        # "Primary (auto)" here, and an untouched Save then erased the user's choice.
        preferred_name = config.get("preferred_monitor")
        self._saved_monitor = preferred_name
        self._populate_monitor_combo()
        idx = self.preferred_monitor_combo.findData(preferred_name)
        self.preferred_monitor_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.show_on_all_taskbars.setChecked(
            config.get("show_on_all_taskbars", constants.config.defaults.DEFAULT_SHOW_ON_ALL_TASKBARS))
        self.free_move.setChecked(config.get("free_move", False))
        self.free_float.setChecked(config.get("free_float", True))
        self._sync_monitor_note()
        self.keep_visible_fullscreen.setChecked(
            config.get("keep_visible_fullscreen", constants.config.defaults.DEFAULT_KEEP_VISIBLE_FULLSCREEN))
        self.hide_when_apps_cover.setChecked(
            config.get("hide_when_apps_cover", constants.config.defaults.DEFAULT_HIDE_WHEN_APPS_COVER))

        self.mini_graph_section.load_settings(config)

    def get_settings(self) -> Dict[str, Any]:
        mode = self.display_mode_combo.currentData()
        order = [c.currentData() for c in self.pos_combos]
        return {
            "widget_display_mode": "side_by_side" if mode == "side_by_stack" else mode,
            # The two side-by-side entries ARE the stack choice; every other mode keeps the stored one.
            "stack_hardware_stats": (mode == "side_by_stack") if mode in ("side_by_side", "side_by_stack")
                                    else getattr(self, "_stack_loaded", False),
            "widget_display_order": order,
            "preferred_monitor": self.preferred_monitor_combo.currentData(),
            "show_on_all_taskbars": self.show_on_all_taskbars.isChecked(),
            "free_move": self.free_move.isChecked(),
            "free_float": self.free_float.isChecked(),
            "keep_visible_fullscreen": self.keep_visible_fullscreen.isChecked(),
            "hide_when_apps_cover": self.hide_when_apps_cover.isChecked(),
            **self.mini_graph_section.get_settings(),
        }
