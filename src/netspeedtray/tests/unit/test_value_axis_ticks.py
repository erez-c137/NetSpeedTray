"""
The Monitor graph's value (y) axis must never print the same label twice, nor a misleading one.

Regression: the Network graph's speed axis (Mbps) formatted every tick >= 1 with int(), so a 0-2 Mbps
scale read "0.0, 0.5, 1, 1, 2" (1.5 printed as "1"), and below 1 it used one fixed decimal, so the
0-0.1 Mbps floor scale read "0.0, 0.0, 0.0, 0.1, 0.1, 0.1". The Hardware tab's auto-scaled % axes had
the same int() truncation ("2%" for a 2.5% tick).

These drive the real GraphRenderer (network) and _apply_hw_ylim (hardware) and read back the labels
matplotlib painted.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.graph.renderer import GraphRenderer

END = datetime(2026, 10, 19, 18, 16, 37)

# Mbps scale tops the speed axis really shows: the 0.1 floor (MINIMUM_Y_AXIS_MBPS), sub-Mbps, the
# 1-10 integer tops of _get_nice_y_axis_top, a mid range, and gigabit.
NETWORK_TOPS = [0.1, 0.2, 1.0, 2.0, 3.0, 7.0, 15.0, 1500.0]


def _agg_init(self):
    """A real Agg figure instead of the Qt canvas (render() rebuilds its own axes on it)."""
    self.figure = Figure(figsize=(9.3, 4.3), dpi=100)
    self.canvas = FigureCanvasAgg(self.figure)
    self._setup_standard_axes()


@pytest.fixture
def q_app(monkeypatch):
    # Per-test, so it also undoes test_renderer_logic's permanent GraphRenderer._init_matplotlib stub.
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _agg_init)


def _visible_labels(ax, fig):
    fig.canvas.draw()
    lo, hi = ax.get_ylim()
    return [t.get_text() for t, loc in zip(ax.yaxis.get_majorticklabels(), ax.yaxis.get_majorticklocs())
            if lo - 1e-12 <= loc <= hi + 1e-12 and t.get_text()]


def _network_labels(top, language="en_US"):
    r = GraphRenderer(MagicMock(), I18nStrings(language))
    data = [(END - timedelta(seconds=60 - i), 50_000.0, 100_000.0) for i in range(61)]
    r.render(data, END - timedelta(seconds=60), END, "TIMELINE_SESSION", stat_type="network")
    out = {}
    for name, ax in (("download", r.ax_download), ("upload", r.ax_upload)):
        r._set_smart_y_ticks(ax, top)
        ax.set_ylim(0, top)
        out[name] = _visible_labels(ax, r.figure)
    return out


def _number(label, sep="."):
    return float(label.rstrip("%").replace(sep, "."))


@pytest.mark.parametrize("top", NETWORK_TOPS)
def test_speed_axis_labels_unique_and_true(q_app, top):
    for name, labels in _network_labels(top).items():
        assert len(labels) >= 3, f"{name} 0-{top}: {labels}"
        dupes = sorted({x for x in labels if labels.count(x) > 1})
        assert not dupes, f"{name} 0-{top}: repeated labels {dupes} in {labels}"
        # Not misleading: each label reads as the tick's own value (1.5 must not print as "1").
        values = [_number(x) for x in labels]
        assert values == sorted(values)
        steps = {round(b - a, 9) for a, b in zip(values, values[1:])}
        assert len(steps) == 1, f"{name} 0-{top}: uneven label steps {labels}"


@pytest.mark.parametrize("top", NETWORK_TOPS)
def test_speed_axis_labels_are_short(q_app, top):
    """No padding zeros: '1.5', not '1.50'; '2', not '2.0' when the step is 0.5 - kept minimal."""
    for name, labels in _network_labels(top).items():
        for x in labels:
            if "." in x:
                assert not x.endswith("0"), f"{name} 0-{top}: padded label {x!r} in {labels}"


def test_speed_axis_uses_locale_decimal_separator(q_app):
    i18n = I18nStrings("de_DE")
    assert i18n.DECIMAL_SEPARATOR == ","
    labels = _network_labels(2.0, "de_DE")["download"]
    assert "1,5" in labels, labels
    assert not any("." in x for x in labels), labels


@pytest.mark.parametrize("top", [10.0, 12.5, 14.0, 17.0, 30.0, 100.0])
def test_hardware_percent_axis_labels_unique_and_true(top):
    fig = Figure(figsize=(8, 4), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [0, 1])
    fake = SimpleNamespace(i18n=I18nStrings("en_US"))
    GraphRenderer._apply_hw_ylim(fake, ax, False, top / 1.2)
    labels = _visible_labels(ax, fig)
    assert all(x.endswith("%") for x in labels), labels
    dupes = sorted({x for x in labels if labels.count(x) > 1})
    assert not dupes, f"0-{top}%: repeated labels {dupes} in {labels}"
    values = [_number(x) for x in labels]
    steps = {round(b - a, 9) for a, b in zip(values, values[1:])}
    assert len(steps) == 1, f"0-{top}%: uneven label steps {labels}"
