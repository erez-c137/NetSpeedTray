"""
Core submodule for NetSpeedTray.

Contains the controller and potentially other core logic components.
Exports the main classes for use by other parts of the application.

The export is lazy (PEP 562): importing any ``netspeedtray.core.<module>`` runs this file first,
and an eager ``StatsController`` import here pulled PyQt6, psutil and the whole stats stack into
light callers too - ``NetSpeedTray.exe --shutdown`` needs only ``core.shutdown_channel``.
"""

__all__ = [
    "StatsController",
]


def __getattr__(name):
    if name == "StatsController":
        from netspeedtray.core.controller import StatsController
        return StatsController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
