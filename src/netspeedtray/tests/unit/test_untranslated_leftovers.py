"""
Two English strings that every locale showed: the graph's boot marker (' ↑ Boot') and the support
bundle save dialog's "Zip files (*.zip)" filter. Both now come from the locale files.
"""
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from matplotlib.figure import Figure

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.graph.renderer import GraphRenderer

DE = I18nStrings("de_DE")


def test_boot_marker_is_translated(monkeypatch):
    def bare_init(self):
        self.figure = Figure()
        self.canvas = MagicMock()
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", bare_init)
    r = GraphRenderer(MagicMock(), DE)
    ax = r.figure.add_subplot(1, 1, 1)
    end = datetime.now()
    ax.set_xlim(end - timedelta(hours=2), end)
    r._add_event_markers(ax, end - timedelta(hours=2), end, boot_time=end - timedelta(hours=1))
    texts = [t.get_text() for t in ax.texts]
    assert texts == [f" ↑ {DE.GRAPH_BOOT_MARKER}"]
    assert "Boot" not in DE.GRAPH_BOOT_MARKER


def test_support_bundle_save_filter_is_translated(monkeypatch):
    from netspeedtray.views.settings import dialog as D
    seen = {}

    def fake_save(_parent, _title, _path, file_filter):
        seen["filter"] = file_filter
        return "", ""        # cancelled - nothing is written
    monkeypatch.setattr(D.QFileDialog, "getSaveFileName", staticmethod(fake_save))
    fake = SimpleNamespace(i18n=DE, logger=logging.getLogger("test"), config={})
    D.SettingsDialog.export_support_bundle(fake)
    assert seen["filter"] == f"{DE.STATS_DETAIL_EXPORT_ZIP_LABEL} (*.zip)"
    assert "Zip files" not in seen["filter"]
