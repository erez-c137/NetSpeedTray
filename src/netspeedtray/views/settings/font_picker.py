"""
The Settings font picker (memory plan 2.2) - replaces ``QFontDialog.getFont``.

QFontDialog fills its family, style and size lists for every installed font, which makes Qt
populate the styles of every family (~38 MB on a machine with ~320 families) and keep them for the
life of the process. This picker asks Qt only for what it shows:

* the family list is ``QFontDatabase.families()`` - names only, cheap;
* each row's name is drawn in its own face LAZILY, by the delegate, for the rows actually painted
  (a few dozen, never the whole list), with font merging off so a face that lacks a character never
  sends Qt searching the fallback chain (#487) - such a name, and a symbol font's, is drawn in the
  list's own font, the UI face (#489: one face per language, never a family list), with merging off
  when that face lacks a character too;
* weights come from ``QFontDatabase.styles()`` of the SELECTED family only;
* the preview is the widget's own renderer (PreviewWidget) on the taskbar-coloured strip Settings uses.

What it returns is what the Appearance page stores: a family, a weight (main font only - the arrow
font's weight is fixed) and a size. Keyboard: type in the search box, Up/Down/PageUp/PageDown move
the selection from there, Enter selects, Esc cancels. Right-to-left follows the app.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import (QEvent, QModelIndex, QObject, QSortFilterProxyModel, QStringListModel, Qt,
                          QSize)
from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics, QKeyEvent, QPalette, QShowEvent
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListView,
                             QPushButton, QSpinBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                             QVBoxLayout, QWidget)

from netspeedtray import constants
from netspeedtray.utils import styles as style_utils
from netspeedtray.utils import color_scheme
from netspeedtray.utils.font_runs import family_covers

logger = logging.getLogger("NetSpeedTray.FontPicker")

_ROW_PX = 30           # list row height
_NAME_PX = 15          # pixel size of a family name drawn in its own face
_FACE_CACHE_MAX = 256  # faces kept by the delegate (only painted rows ever get one)


def picker_families() -> List[str]:
    """Every installed family, by name only (no styles are loaded). Vertical-writing aliases
    ("@MS Gothic") are Windows plumbing, not fonts to pick."""
    return [f for f in QFontDatabase.families() if f and not f.startswith("@")]


def family_weights(family: str, i18n=None) -> Tuple[List[int], Dict[int, str]]:
    """The weights ``family`` has, ascending, and a display name per weight (the localized Fluent
    name when the weight is a standard one). Loads ``family``'s styles only."""
    weights: List[int] = []
    names: Dict[int, str] = {}
    for style in QFontDatabase.styles(family):
        w = QFontDatabase.weight(family, style)
        if w <= 0:
            continue
        if w not in weights:
            weights.append(w)
        key = constants.fonts.WEIGHT_MAP.get(w)
        display = getattr(i18n, key, key) if (key and i18n is not None) else (key or style)
        current = names.get(w, "")
        if not current or ("italic" in current.lower() and "italic" not in display.lower()):
            names[w] = display
    weights.sort()
    return weights, names


class FamilyDelegate(QStyledItemDelegate):
    """Draws a family's name in that family - built on paint, so only visible rows load a face."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._faces: Dict[str, Optional[QFont]] = {}

    def face_for(self, family: str) -> Optional[QFont]:
        """``family`` as a single-family, non-merging font when it can draw its own name; None when
        the name must be drawn in the list's font (a symbol font, or a character the face lacks)."""
        if family in self._faces:
            return self._faces[family]
        face: Optional[QFont] = None
        try:
            f = QFont(family)
            f.setPixelSize(_NAME_PX)
            # One family, merging off: a missing glyph is never looked up in other fonts (#487).
            f.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
            symbol = QFontDatabase.WritingSystem.Symbol in QFontDatabase.writingSystems(family)
            # Coverage asked of that one face (GDI first: no font file mapped just to ask).
            if not symbol and family_covers(family, family, keep=False):
                face = f
        except Exception as e:
            logger.debug("No face for %r: %s", family, e)
        if len(self._faces) >= _FACE_CACHE_MAX:
            self._faces.clear()
        self._faces[family] = face
        return face

    def fallback_font(self, base: QFont, family: str) -> QFont:
        """The list's own font (the UI face, #489) for a name drawn outside its face. If the UI face
        lacks a character of the name too, merging is switched off: a missing glyph shows as a box
        rather than sending Qt through every installed family."""
        try:
            if family_covers(style_utils.ui_family(), family, keep=False):
                return base
        except Exception:
            pass
        f = QFont(base)
        f.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        return f

    def painted_families(self) -> List[str]:
        """Families a face was built for (tests: proof the list stays lazy)."""
        return list(self._faces)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), _ROW_PX))

    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        family = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        face = self.face_for(family)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.save()
        try:
            painter.setPen(opt.palette.color(role))
            painter.setFont(face if face is not None else self.fallback_font(opt.font, family))
            rect = opt.rect.adjusted(10, 0, -10, 0)
            text = QFontMetrics(painter.font()).elidedText(family, Qt.TextElideMode.ElideRight, rect.width())
            align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeading
            painter.setLayoutDirection(opt.direction)
            painter.drawText(rect, int(align), text)
        finally:
            painter.restore()


class FontPickerDialog(QDialog):
    """Pick a family (+ weight for the main font) and a size. ``exec()`` -> Accepted / Rejected."""

    def __init__(self, i18n, family: str, size: int, weight: Optional[int] = None, *,
                 preview_config: Optional[Dict[str, Any]] = None, target: str = "main",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._target = target
        self._show_weight = target == "main"
        self._weights: List[int] = []
        self._wanted_weight = int(weight) if weight else int(constants.fonts.WEIGHT_NORMAL)
        self._preview_config = dict(preview_config or {})
        self._preview = None
        self.setWindowTitle(i18n.FONT_PICKER_TITLE)
        self.setModal(True)
        self.setStyleSheet(style_utils.dialog_style())
        self._build(family, size)

    # --- UI -------------------------------------------------------------------------------------
    def _build(self, family: str, size: int) -> None:
        dark = style_utils.is_dark_mode()
        sem = style_utils.semantic_colors()
        accent = style_utils.get_accent_color()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self.search = QLineEdit()
        self.search.setProperty("fieldStyle", "input")
        self.search.setPlaceholderText(self.i18n.FONT_PICKER_SEARCH)
        self.search.setAccessibleName(self.i18n.FONT_PICKER_SEARCH)
        self.search.setClearButtonEnabled(True)
        root.addWidget(self.search)

        self._model = QStringListModel(picker_families(), self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.list = QListView()
        self.list.setModel(self._proxy)
        self.list.setUniformItemSizes(True)          # one sizeHint for all rows: no per-row face
        self.delegate = FamilyDelegate(self.list)
        self.list.setItemDelegate(self.delegate)
        self.list.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.list.setAccessibleName(self.i18n.FONT_FAMILY_LABEL)
        self.list.setMinimumSize(360, 300)
        input_bg = "#383838" if dark else "#FFFFFF"
        border = "#404040" if dark else "#D0D0D0"
        text = "#FFFFFF" if dark else "#000000"
        self.list.setStyleSheet(
            f"QListView {{ background-color: {input_bg}; color: {text}; border: 1px solid {border};"
            f" border-radius: 4px; outline: 0px; padding: 2px; }}"
            f" QListView::item {{ border-radius: 4px; }}"
            f" QListView::item:selected {{ background-color: {accent.name()}; color: white; }}"
            f" QListView::item:hover:!selected {{ background-color: {sem.get('card_stroke', border)}; }}"
            f" QListView:focus {{ border: 2px solid {accent.name()}; }}")
        root.addWidget(self.list, 1)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.weight_combo = QComboBox()
        self.weight_combo.setAccessibleName(self.i18n.FONT_WEIGHT_LABEL)
        self._weight_label = QLabel(self.i18n.FONT_WEIGHT_LABEL)
        self._weight_label.setBuddy(self.weight_combo)
        if self._show_weight:
            controls.addWidget(self._weight_label)
            controls.addWidget(self.weight_combo, 1)
        else:
            self._weight_label.hide()
            self.weight_combo.hide()
        self.size_spin = QSpinBox()
        self.size_spin.setRange(constants.fonts.FONT_SIZE_MIN, constants.fonts.FONT_SIZE_MAX)
        self.size_spin.setValue(max(constants.fonts.FONT_SIZE_MIN, min(constants.fonts.FONT_SIZE_MAX, int(size))))
        self.size_spin.setAccessibleName(self.i18n.FONT_SIZE_LABEL)
        size_label = QLabel(self.i18n.FONT_SIZE_LABEL)
        size_label.setBuddy(self.size_spin)
        controls.addWidget(size_label)
        controls.addWidget(self.size_spin)
        if not self._show_weight:
            controls.addStretch(1)
        root.addLayout(controls)

        if self._preview_config:
            try:
                from netspeedtray.views.widget.preview import PreviewWidget
                strip = QWidget()
                strip.setObjectName("fontPreviewStrip")
                c = (constants.color.PREVIEW_STRIP_LIGHT if color_scheme.shell_is_light()
                     else constants.color.PREVIEW_STRIP_DARK)
                strip.setStyleSheet(f"#fontPreviewStrip {{ background: {c}; border-radius: 6px; }}")
                sl = QHBoxLayout(strip)
                sl.setContentsMargins(12, 6, 12, 6)
                sl.addStretch(1)
                self._preview = PreviewWidget(self._preview_settings(family), self.i18n, width=300, height=40)
                sl.addWidget(self._preview)
                sl.addStretch(1)
                root.addWidget(strip)
            except Exception as e:
                logger.debug("Font preview not built: %s", e)
                self._preview = None

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.ok_button = QPushButton(self.i18n.SELECT_FONT_BUTTON)
        self.ok_button.setStyleSheet(style_utils.button_style(accent=True))
        self.ok_button.setDefault(True)
        self.cancel_button = QPushButton(self.i18n.CANCEL_BUTTON)
        self.cancel_button.setStyleSheet(style_utils.button_style())
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)
        root.addLayout(buttons)

        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.search.textChanged.connect(self._on_filter)
        self.search.installEventFilter(self)
        self.list.selectionModel().currentChanged.connect(self._on_current_changed)
        self.list.doubleClicked.connect(lambda _i: self.accept())
        self.weight_combo.currentIndexChanged.connect(self._refresh_preview)
        self.size_spin.valueChanged.connect(self._refresh_preview)

        self._select_family(family)
        self.search.setFocus()

    # --- behaviour ------------------------------------------------------------------------------
    def _select_family(self, family: str) -> None:
        row = -1
        if family:
            want = family.casefold()
            names = self._model.stringList()
            row = next((i for i, n in enumerate(names) if n.casefold() == want), -1)
        if row < 0:
            self._load_weights(None)
            self.ok_button.setEnabled(False)
            return
        idx = self._proxy.mapFromSource(self._model.index(row, 0))
        self.list.setCurrentIndex(idx)
        self.list.scrollTo(idx, QListView.ScrollHint.PositionAtCenter)

    def _on_filter(self, text: str) -> None:
        keep = self.selected_family()
        self._proxy.setFilterFixedString(text.strip())
        if self._proxy.rowCount() == 0:
            self.ok_button.setEnabled(False)
            return
        if keep:
            src = next((i for i, n in enumerate(self._model.stringList()) if n == keep), -1)
            idx = self._proxy.mapFromSource(self._model.index(src, 0)) if src >= 0 else QModelIndex()
            if idx.isValid():
                self.list.setCurrentIndex(idx)
                self.list.scrollTo(idx)
                return
        self.list.setCurrentIndex(self._proxy.index(0, 0))

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex = QModelIndex()) -> None:
        family = current.data() if current.isValid() else None
        self.ok_button.setEnabled(bool(family))
        if self.weight_combo.count() and self._weights:
            i = self.weight_combo.currentIndex()
            if 0 <= i < len(self._weights):
                self._wanted_weight = self._weights[i]   # keep the user's weight across families
        self._load_weights(family)
        self._refresh_preview()

    def _load_weights(self, family: Optional[str]) -> None:
        """The weight list for the SELECTED family only (its styles; no other family is touched)."""
        self.weight_combo.blockSignals(True)
        try:
            self.weight_combo.clear()
            self._weights = []
            if family and self._show_weight:
                self._weights, names = family_weights(family, self.i18n)
                for w in self._weights:
                    self.weight_combo.addItem(names.get(w, str(w)), w)
                if self._weights:
                    best = min(self._weights, key=lambda w: abs(w - self._wanted_weight))
                    self.weight_combo.setCurrentIndex(self._weights.index(best))
            self.weight_combo.setEnabled(bool(self._weights))
        finally:
            self.weight_combo.blockSignals(False)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Arrow / page keys typed in the search box move the list's selection.
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                forwarded = QKeyEvent(QEvent.Type.KeyPress, key, event.modifiers())
                self.list.keyPressEvent(forwarded)
                return True
        return super().eventFilter(obj, event)

    def _preview_settings(self, family: Optional[str]) -> Dict[str, Any]:
        cfg = dict(self._preview_config)
        size = int(self.size_spin.value())
        if self._target == "main":
            if family:
                cfg["font_family"] = family
            cfg["font_size"] = size
            w = self.selected_weight()
            if w:
                cfg["font_weight"] = w
        else:
            if family:
                cfg["arrow_font_family"] = family
            cfg["arrow_font_size"] = size
            cfg["use_separate_arrow_font"] = True
        return cfg

    def _refresh_preview(self, *_args) -> None:
        if self._preview is None:
            return
        try:
            self._preview.set_config(self._preview_settings(self.selected_family()))
        except Exception as e:
            logger.debug("Font preview refresh failed: %s", e)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        try:
            from netspeedtray.utils.dwm import apply_win11_chrome
            apply_win11_chrome(int(self.winId()), dark=style_utils.is_dark_mode())
        except Exception as e:
            logger.debug("Win11 chrome not applied: %s", e)

    # --- result ---------------------------------------------------------------------------------
    def selected_family(self) -> Optional[str]:
        idx = self.list.currentIndex()
        return idx.data() if idx.isValid() else None

    def selected_weight(self) -> Optional[int]:
        """The chosen weight; None for the arrow font (fixed) or a family with no weight list."""
        if not self._show_weight:
            return None
        data = self.weight_combo.currentData()
        return int(data) if data is not None else None

    def selected_size(self) -> int:
        return int(self.size_spin.value())
