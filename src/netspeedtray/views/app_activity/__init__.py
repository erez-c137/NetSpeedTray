"""App Activity package.

The heavy AppActivityWindow is exposed lazily (PEP 562) so that importing the lightweight
AppActivityWorker — which the Monitor's Network tab reuses — does NOT drag the full window onto that
hot path. `from netspeedtray.views.app_activity import AppActivityWindow` still works unchanged.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from netspeedtray.views.app_activity.window import AppActivityWindow

__all__ = ["AppActivityWindow"]


def __getattr__(name: str):
    if name == "AppActivityWindow":
        from netspeedtray.views.app_activity.window import AppActivityWindow
        return AppActivityWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
