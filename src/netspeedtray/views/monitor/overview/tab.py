"""
OverviewTab — the matplotlib-free glance: "the taskbar widget, bigger", with at-a-glance tiles.

By contract this tab NEVER imports matplotlib (it's the default tab, so a glance-only session stays
at the idle-RAM baseline). For 5.0 it shows the live PreviewWidget tile; the sparkline + usage tiles
land in 5.1.
"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.views.widget.preview import PreviewWidget


class OverviewTab(QWidget):
    """Overview tab content. Matplotlib-free by contract."""

    stat_type = "overview"

    def __init__(self, config: Dict[str, Any], i18n, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._config = config
        self._i18n = i18n
        c = su.semantic_colors()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Live preview tile — the real widget, bigger, on a taskbar-ish card.
        tile = QFrame()
        tile.setObjectName("previewTile")
        tile.setStyleSheet(
            f"#previewTile {{ background: {c['subtle_fill']}; border-radius: {tokens.RADIUS_CARD}px; }}")
        tl = QHBoxLayout(tile)
        tl.setContentsMargins(16, 16, 16, 16)
        tl.addStretch(1)
        self._preview = PreviewWidget(config, i18n, width=340, height=46)
        tl.addWidget(self._preview)
        tl.addStretch(1)
        root.addWidget(tile)

        # Transient scaffolding (not localized) — replaced by the real tiles in 5.1.
        note = QLabel("At-a-glance tiles — network · CPU · GPU · RAM · data usage — arrive in the next step.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        note.setFont(su.font(tokens.TYPE_BODY))
        root.addWidget(note)
        root.addStretch(1)

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default
