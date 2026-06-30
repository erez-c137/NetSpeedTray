"""
Curated arrow-glyph presets for the speed readout (#129).

Six styles that cover most tastes - all native, monochrome Unicode glyphs that render
reliably in Segoe UI (no emoji/color presentation), so they always feel like part of
Windows. "Classic" is the default (empty config -> the i18n arrow, which is Classic).

Used by the arrow-style picker; custom glyphs / PNG images are the power-user options
layered on top.
"""
from typing import Final, List, Tuple

# (display name, up glyph, down glyph)
ARROW_PRESETS: Final[List[Tuple[str, str, str]]] = [
    ("Classic",         "↑", "↓"),   # ↑ ↓  - clean and simple (the default)
    ("Solid",           "▲", "▼"),   # ▲ ▼  - bold filled triangles, high visibility
    ("Compact",         "▴", "▾"),   # ▴ ▾  - small filled triangles, subtle on a busy taskbar
    ("Outline",         "△", "▽"),   # △ ▽  - light hollow triangles
    ("Outline Compact", "▵", "▿"),   # ▵ ▿  - small hollow triangles, the lightest option
    ("Double",          "⇑", "⇓"),   # ⇑ ⇓  - double-line arrows, distinctive
]
