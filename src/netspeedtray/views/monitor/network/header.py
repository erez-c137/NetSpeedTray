"""
NetworkHeader - the Monitor Network tab's header band: machine-wide Download/Upload totals for the
selected period, plus a segmented timeline control that drives the shared graph.

Standalone by design. It reuses only the pill *style* from utils.styles (matplotlib-free) and the
period keys from constants - never the graph package - so importing this module can't trip the
Monitor's matplotlib firewall, and the Monitor stays decoupled from the graph window it replaces.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QToolButton, QMenu)

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils.helpers import format_data_size
from netspeedtray.views.monitor.timeline_selector import TimelineSelector

# (short label, period key) - mirrors the graph window's pills so the two surfaces feel identical.
_PILLS = [
    ("SESS", "TIMELINE_SESSION"),
    ("BOOT", "TIMELINE_SYSTEM_UPTIME"),
    ("24H", "TIMELINE_24_HOURS"),
    ("WEEK", "TIMELINE_WEEK"),
    ("MONTH", "TIMELINE_MONTH"),
    ("ALL", "TIMELINE_ALL"),
]
_DEFAULT_KEY = "TIMELINE_24_HOURS"


class _ClampedNote(QLabel):
    """A wrapped caption that never grows past ``max_lines``: the text is cut with an ellipsis to fit
    the width it gets, and the tooltip carries all of it. The header band has a fixed height."""

    def __init__(self, text: str, max_lines: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = text
        self._max_lines = max(1, int(max_lines))
        self.setWordWrap(True)
        self.setToolTip(text)
        self.setText(text)

    def full_text(self) -> str:
        return self._full

    def _fits(self, text: str, width: int) -> bool:
        flags = int(Qt.TextFlag.TextWordWrap.value)
        rect = self.fontMetrics().boundingRect(0, 0, max(1, width), 10_000, flags, text)
        return rect.height() <= self.fontMetrics().lineSpacing() * self._max_lines

    def _clamp(self, width: int) -> None:
        text = self._full
        if width > 0 and not self._fits(text, width):
            lo, hi = 0, len(text)
            while lo < hi:                       # the longest prefix that fits with an ellipsis
                mid = (lo + hi + 1) // 2
                if self._fits(text[:mid].rstrip() + "…", width):
                    lo = mid
                else:
                    hi = mid - 1
            text = text[:lo].rstrip() + "…"
        if text != self.text():
            self.setText(text)

    def resizeEvent(self, event) -> None:
        self._clamp(event.size().width())
        super().resizeEvent(event)


class _ElidingMenuButton(QToolButton):
    """A dropdown button whose label elides ("Ethernet 3 (Realt…  ▾") when the band is short of room,
    instead of being squeezed under its neighbours (M9). It asks for its full label's width, but can
    shrink to ``min_width``; the tooltip always carries the whole name."""

    _CHEVRON = "    \u25be"
    _nst_elides = True   # the label elides by design; the rc layout harness skips it in its clip check

    def __init__(self, min_width: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._label = ""
        self._min_w = int(min_width)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)   # may shrink to min

    def set_label(self, label: str) -> None:
        self._label = str(label)
        self.setToolTip(self._label)
        self.setAccessibleName(self._label)
        self._apply()
        self.updateGeometry()

    def _chrome(self) -> int:
        return 26 + self.fontMetrics().horizontalAdvance(self._CHEVRON)   # padding + border + chevron

    def sizeHint(self) -> QSize:
        h = super().sizeHint().height()
        return QSize(max(self._min_w, self._chrome() + self.fontMetrics().horizontalAdvance(self._label)), h)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._min_w, super().minimumSizeHint().height())

    def _apply(self) -> None:
        avail = max(10, self.width() - self._chrome())
        text = self.fontMetrics().elidedText(self._label, Qt.TextElideMode.ElideRight, avail)
        if text + self._CHEVRON != self.text():
            self.setText(text + self._CHEVRON)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply()


def _period_value(period_key: str) -> int:
    """period key -> the legacy PERIOD_MAP index GraphHost/coordinator speak."""
    for idx, key in constants.data.history_period.PERIOD_MAP.items():
        if key == period_key:
            return idx
    return 2


class PeriodSegmentedControl(QWidget):
    """Six exclusive pills (Session/Boot/24H/Week/Month/All) emitting the PERIOD_MAP index."""

    period_changed = pyqtSignal(int)

    def __init__(self, initial_key: str = _DEFAULT_KEY, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("timelinePills")
        lay = QHBoxLayout(self)
        lay.setSpacing(0)
        lay.setContentsMargins(0, 0, 0, 0)
        self._buttons: Dict[str, QPushButton] = {}
        for i, (label, key) in enumerate(_PILLS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("period_key", key)
            btn.setObjectName(
                "pillFirst" if i == 0 else "pillLast" if i == len(_PILLS) - 1 else "pillMid")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.clicked.connect(self._on_clicked)
            lay.addWidget(btn, 1)
            self._buttons[key] = btn
        self.setStyleSheet(su.segmented_pills_style(su.is_dark_mode()))
        self.set_period_key(initial_key, emit=False)

    def _on_clicked(self) -> None:
        sender = self.sender()
        key = sender.property("period_key")
        for btn in self._buttons.values():
            btn.setChecked(btn is sender)
        self.period_changed.emit(_period_value(key))

    def set_period_key(self, period_key: str, emit: bool = False) -> None:
        """Select a pill (no signal unless ``emit``). Falls back to 24H for an unknown key."""
        target = self._buttons.get(period_key) or self._buttons[_DEFAULT_KEY]
        for btn in self._buttons.values():
            btn.blockSignals(True)
            btn.setChecked(btn is target)
            btn.blockSignals(False)
        if emit:
            self.period_changed.emit(_period_value(target.property("period_key")))


class NetworkHeader(QWidget):
    """Machine-wide Download/Upload totals for the active period + the timeline control."""

    period_changed = pyqtSignal(int)     #: re-emits the pills' PERIOD_MAP index
    interface_changed = pyqtSignal(str)  #: NIC name, or "all"
    check_connection_requested = pyqtSignal()   #: the "Check connection" action (2.2 D3)

    def __init__(self, i18n, initial_key: str = _DEFAULT_KEY, parent: Optional[QWidget] = None,
                 graph_host=None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        c = su.semantic_colors()

        # One calm, full-width band: the Download/Upload totals as the headline (left), and the two
        # controls - interface filter + the SAME timeline dropdown the Overview uses - given proper room
        # on the right. (Replaces the cramped six-pill strip; the dropdown also unlocks the finer ranges
        # 30m/1h/4h/8h/12h/48h the pills never had, and keeps all three tabs consistent.)
        # Fixed height + the shared side inset so the controls sit at the SAME vertical center + right
        # edge as the Overview/Hardware bands - switching tabs no longer makes the dropdown jump.
        self.setFixedHeight(constants.layout.MONITOR_HEADER_BAND_HEIGHT)
        root = QHBoxLayout(self)
        _m = constants.layout.MONITOR_BODY_MARGIN
        root.setContentsMargins(_m, 0, _m, 0)
        root.setSpacing(self._SPACING_WIDE)
        self._compact = False
        self._check_full_w: Optional[int] = None   # cached measurement (see _full_check_width)
        self._content_w = None                     # what _refit last fitted for

        self._down = self._stat_block(self._tr("DOWNLOAD_LABEL", "Download"), "↓", c)
        self._up = self._stat_block(self._tr("UPLOAD_LABEL", "Upload"), "↑", c)
        root.addLayout(self._down[0])
        root.addLayout(self._up[0])
        # D5: shown only while part of the totals was estimated from speeds saved before 2.2 - the
        # same note the hover card and the Overview usage card carry.
        self._history_note = _ClampedNote(self._tr("USAGE_RECORDED_HISTORY_NOTE", ""),
                                          constants.layout.MONITOR_HISTORY_NOTE_MAX_LINES)
        self._history_note.setFont(su.font(tokens.TYPE_CAPTION))
        self._history_note.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        self._history_note.setMinimumWidth(constants.layout.MONITOR_HISTORY_NOTE_MIN_WIDTH)
        self._history_note.setMaximumWidth(constants.layout.MONITOR_HISTORY_NOTE_MAX_WIDTH)
        self._history_note.setVisible(False)
        self._note_wanted = False   # set_totals asks for the note; _fit decides whether the band has room
        # Takes the free width first (up to its max) - the stretch after it only gets what's left.
        root.addWidget(self._history_note, 100, Qt.AlignmentFlag.AlignVCenter)
        root.addStretch(1)

        # Right-cluster controls live in their own sub-layout with the SHARED control spacing, so the
        # gap around the Live pill matches the Hardware tab exactly (the totals keep the wider spacing).
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(constants.layout.MONITOR_CONTROL_SPACING)
        root.addLayout(controls)

        # "Check connection" (2.2 D3) - the same action as on the Overview's network hero, leading the
        # right-hand cluster so it reads as a command, not a filter.
        from netspeedtray.views.monitor.connection_check import make_check_button
        self._check_btn = make_check_button(i18n)
        self._check_btn.clicked.connect(self.check_connection_requested)
        controls.addWidget(self._check_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Per-NIC filter - scopes both the graph and these totals to one interface (or all). Built as a
        # QToolButton + menu styled IDENTICALLY to the TimelineSelector (a QComboBox loses its native
        # arrow when styled and renders as a bare box; this also keeps the two right-side controls the
        # same height + look - the owner's consistency note).
        self._iface_value = "all"
        self._iface_btn = _ElidingMenuButton(constants.layout.MONITOR_IFACE_BUTTON_MIN_WIDTH)
        self._iface_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._iface_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._iface_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._iface_btn.setFont(su.font(tokens.TYPE_BODY))
        # Up to its full name; past that it elides rather than overlapping the timeline (M9).
        self._iface_btn.setMaximumWidth(constants.layout.MONITOR_IFACE_BUTTON_MAX_WIDTH)
        _r = tokens.RADIUS_CONTROL
        self._iface_btn.setStyleSheet(
            f"QToolButton {{ background: {c['subtle_fill']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: {_r}px; padding: 4px 12px;"
            f" text-align: left; }}"
            f" QToolButton:hover {{ border-color: {c['accent']}; }}"
            f" QToolButton:focus {{ border-color: {c['accent']}; }}"
            f" QToolButton::menu-indicator {{ image: none; width: 0; }}")
        self._iface_menu = QMenu(self._iface_btn)
        self._iface_menu.setStyleSheet(
            f"QMenu {{ background: {c['card_bg']}; color: {c['text_primary']};"
            f" border: 1px solid {c['card_stroke']}; border-radius: 6px; padding: 4px; }}"
            f" QMenu::item {{ padding: 5px 28px 5px 12px; border-radius: 4px; }}"
            f" QMenu::item:selected {{ background: {c['accent']}; color: white; }}")
        self._iface_btn.setMenu(self._iface_menu)
        self.set_interfaces([])   # build the menu (just "All Interfaces") + label the button
        controls.addWidget(self._iface_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._timeline = TimelineSelector(i18n, current_index=_period_value(initial_key))
        self._timeline.period_changed.connect(self.period_changed)
        controls.addWidget(self._timeline, 0, Qt.AlignmentFlag.AlignVCenter)

        # Live/Pause pill - RIGHTMOST (owner preference), after the timeline. Bound to the shared host's
        # canonical state so the Hardware tab's pill mirrors it (and vice-versa).
        if graph_host is not None:
            from netspeedtray.views.monitor.live_toggle import LiveToggle
            self._live = LiveToggle(graph_host, i18n)
            controls.addWidget(self._live, 0, Qt.AlignmentFlag.AlignVCenter)

    # Gap between the totals blocks: roomy normally, tighter when the window is near its minimum.
    _SPACING_WIDE = 32
    _SPACING_COMPACT = 16

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit(event.size().width())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit(self.width())

    def changeEvent(self, event) -> None:
        # A font / style / language change re-measures the full "Check connection" button.
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.LanguageChange):
            self._check_full_w = None
            self._content_w = None
            if self.isVisible():
                self._fit(self.width())
        super().changeEvent(event)

    def _refit(self) -> None:
        """Content changed (totals / interface name). Re-fit only if that changed the room the
        content asks for - a totals tick that keeps the same width costs one comparison, not a
        layout pass (review item 7)."""
        if not self.isVisible():
            return
        content_w = (self._down[1].sizeHint().width(), self._up[1].sizeHint().width(),
                     self._iface_btn.sizeHint().width(), self._timeline.sizeHint().width(),
                     self._note_wanted)
        if content_w == self._content_w:
            return
        self._content_w = content_w
        self.layout().activate()
        self._fit(self.width())

    def _full_check_width(self) -> int:
        """The width "Check connection" asks for WITH its label - measured once (cached until a
        font / style / language change) instead of building a throwaway button on every fit."""
        if self._check_full_w is None:
            if not self._compact:
                self._check_btn.ensurePolished()   # its stylesheet padding is part of the width
                self._check_full_w = self._check_btn.sizeHint().width()
            else:
                probe = QToolButton()
                probe.setFont(self._check_btn.font())
                probe.setStyleSheet(self._check_btn.styleSheet())
                probe.setIcon(self._check_btn.icon())
                probe.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                probe.setText(self._tr("CHECK_CONN_ACTION", "Check connection"))
                self._check_full_w = probe.sizeHint().width()
                probe.deleteLater()
        return self._check_full_w

    def _fit(self, width: int) -> None:
        """Below the width the full band needs, go compact: "Check connection" shows its icon only
        (the label moves to the tooltip / accessible name) and the totals sit closer, so at the
        Monitor's minimum width in every locale nothing overlaps or clips (M9)."""
        check_text = self._tr("CHECK_CONN_ACTION", "Check connection")
        lay = self.layout()
        # Measured with the history note at its full floor whenever it is wanted, whatever room it got
        # last time - so the compact decision never depends on how the note was squeezed before.
        note_full = (constants.layout.MONITOR_HISTORY_NOTE_MIN_WIDTH + lay.spacing()) if self._note_wanted else 0
        cur_min = self._min_without_note() + note_full
        cur_check = self._check_btn.sizeHint().width()
        spacing_extra = (self._SPACING_WIDE - lay.spacing()) * max(0, lay.count() - 1)
        full_needed = cur_min - cur_check + self._full_check_width() + spacing_extra
        compact = full_needed > width
        if compact != self._compact:
            self._compact = compact
            self._check_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                                               else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self._check_btn.setAccessibleName(check_text)
            lay.setSpacing(self._SPACING_COMPACT if compact else self._SPACING_WIDE)
        self._fit_note(width)

    def _min_without_note(self) -> int:
        """The band's minimum width leaving the history note out."""
        lay = self.layout()
        note = self._history_note
        share = (note.minimumWidth() + lay.spacing()) if not note.isHidden() else 0
        return lay.minimumSize().width() - share

    def _iface_shortfall(self) -> int:
        """How much wider than its eliding floor the interface button wants to be (its whole name, up
        to its max). The note is a secondary caption, so it yields before the NIC name truncates to
        "All (A…" / "Ether…" (#432 review)."""
        btn = self._iface_btn
        preferred = min(btn.sizeHint().width(), btn.maximumWidth())
        return max(0, preferred - btn.minimumSizeHint().width())

    def _fit_note(self, width: int) -> None:
        """The D5 note takes only the room the controls leave (the NIC button counted at its full name):
        its full floor when there is one, less down to MONITOR_HISTORY_NOTE_COMPACT_MIN_WIDTH, and below
        that it hides and its text moves to the totals' tooltips. Holding its 160 px at the 660 px minimum squeezed the timeline and Live
        pill below their own minimums in every locale (rc.2 S7)."""
        note = self._history_note
        # A compact flip in this same _fit resized "Check connection", which sits in the controls
        # sub-layout; QLayout.invalidate() on the root doesn't reach sub-layouts, so their cached
        # minimum would be measured stale until the next activate().
        def _invalidate(lay) -> None:
            lay.invalidate()
            for i in range(lay.count()):
                sub = lay.itemAt(i).layout()
                if sub is not None:
                    _invalidate(sub)
        _invalidate(self.layout())
        room = width - self._min_without_note() - self._iface_shortfall() - self.layout().spacing()
        show = self._note_wanted and room >= constants.layout.MONITOR_HISTORY_NOTE_COMPACT_MIN_WIDTH
        if show:
            want_w = min(constants.layout.MONITOR_HISTORY_NOTE_MIN_WIDTH, room)
            if note.minimumWidth() != want_w:
                note.setMinimumWidth(want_w)
        if note.isHidden() == show:
            note.setVisible(show)
        tip = note.full_text() if (self._note_wanted and not show) else ""
        for lbl in (self._down[1], self._up[1]):
            if lbl.toolTip() != tip:
                lbl.setToolTip(tip)

    def _stat_block(self, label: str, glyph: str, c: dict) -> Tuple[QVBoxLayout, QLabel]:
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFont(su.font(tokens.TYPE_CAPTION))
        name.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        value = QLabel(f"{glyph} -")     # seed the populated shape so the first emit only swaps the number
        value.setFont(su.font(tokens.TYPE_SUBTITLE))
        value.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        col.addWidget(name)
        col.addWidget(value)
        return col, value

    def _period_label(self, period_key: str) -> str:
        """Localized window name for the totals - reuses the existing TIMELINE_* i18n values
        (e.g. '24 Hours'), so no new translator keys."""
        return self._tr(period_key, period_key.replace("TIMELINE_", "").replace("_", " ").title())

    # --- inputs -----------------------------------------------------------------
    def set_totals(self, up_bytes: float, down_bytes: float, period_key: Optional[str] = None,
                   reconstructed: bool = False) -> None:
        dv, du = format_data_size(down_bytes, self._i18n, precision=1)
        uv, uu = format_data_size(up_bytes, self._i18n, precision=1)
        self._down[1].setText(f"↓ {self._fmt(dv)} {du}")
        self._up[1].setText(f"↑ {self._fmt(uv)} {uu}")
        self._note_wanted = bool(reconstructed) and bool(self._history_note.full_text())
        if not self._note_wanted or not self.isVisible():
            # Shown again on demand; _fit (via _refit / showEvent) trims it to the room the band has.
            self._history_note.setVisible(self._note_wanted)
            if not self._note_wanted:
                for lbl in (self._down[1], self._up[1]):
                    lbl.setToolTip("")
        # The timeline dropdown already names the window, so the totals no longer need a separate caption.
        self._refit()

    def set_period_key(self, period_key: str) -> None:
        self._timeline.set_period_index(_period_value(period_key), emit=False)

    def set_interfaces(self, names) -> None:
        """Rebuild the NIC menu ('All Interfaces' + each name), preserving the current selection."""
        names = sorted(names or [])
        if self._iface_value != "all" and self._iface_value not in names:
            self._iface_value = "all"   # the selected NIC disappeared -> fall back to all
        self._iface_menu.clear()
        group = QActionGroup(self._iface_menu)
        group.setExclusive(True)
        all_label = self._tr("ALL_INTERFACES_AGGREGATED_LABEL", "All Interfaces")
        for label, data in [(all_label, "all")] + [(n, n) for n in names]:
            act = self._iface_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(data == self._iface_value)
            act.triggered.connect(lambda _checked=False, d=data, l=label: self._select_iface(d, l))
            group.addAction(act)
        self._sync_iface_text()
        self._refit()

    def _select_iface(self, data: str, label: str) -> None:
        if data != self._iface_value:
            self._iface_value = data
            self._sync_iface_text()
            self.interface_changed.emit(str(data))

    def _sync_iface_text(self) -> None:
        label = (self._tr("ALL_INTERFACES_AGGREGATED_LABEL", "All Interfaces")
                 if self._iface_value == "all" else str(self._iface_value))
        self._iface_btn.set_label(label)   # same chevron-in-text idiom as TimelineSelector, elided

    def _fmt(self, value: float) -> str:
        s = f"{value:.1f}"
        sep = getattr(self._i18n, "DECIMAL_SEPARATOR", ".")
        return s.replace(".", sep) if sep and sep != "." else s

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
