"""
Lazy tab descriptor for the Monitor window.

Each tab starts life as a cheap placeholder in the QStackedWidget; the real page (and, for chart
tabs, the matplotlib canvas) is built only on first activation via ``factory()``. This is what
preserves the idle-RAM win - a glance at Overview never constructs the graph tabs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from PyQt6.QtWidgets import QWidget


@dataclass
class LazyTabDescriptor:
    """One Monitor tab. ``factory`` builds the real page the first time the tab is activated."""

    tab_id: str                                   # "overview" | "network" | "hardware"
    label: str                                    # already-localized tab caption
    factory: Callable[[], QWidget]                # builds the real page on first activation
    needs_graph: bool = False                     # True -> activation lazily loads matplotlib
    is_visible: Callable[[Dict[str, Any]], bool] = field(default=lambda cfg: True)

    # Filled in by the window as it wires the stack.
    stack_index: int = -1
    page: Optional[QWidget] = None                # None until first built (still a placeholder)
