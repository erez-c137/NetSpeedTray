"""
#556 follow-up: on an accent-coloured taskbar the widget's paint-time guard swaps a too-faint themed
colour (text, speed bands, arrows, graph lines) for a readable text colour. That swap used to be silent:
Settings kept showing the picked colour, and its preview strip was a neutral grey, so the user could not
tell why the widget painted white. Now

- ``paint_colors`` records which stems it swapped, and ``accent_swapped`` keeps only the pens the widget
  really paints (band colours only with colour coding on, and so on);
- Settings > Appearance and Settings > Widget > Mini graph show ACCENT_TASKBAR_COLORS_NOTE while one of
  their colours is swapped, live as the user edits;
- the preview strip is the accent bar's shade, so the preview shows the colours on what the widget
  judges them against.
"""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.tests.color_helpers import themed
from netspeedtray.utils import color_scheme, taskbar_appearance
from netspeedtray.utils.taskbar_appearance import TaskbarAppearance
from netspeedtray.views.settings.dialog import SettingsDialog
from netspeedtray.tests.unit.test_rc_settings_fixes import _Main

D = constants.config.defaults
EN = I18nStrings("en_US")
GREEN_BAR = TaskbarAppearance(accent_on=True, accent_bar="#0B5A2B")
FAINT_GREEN = "#1E7A3C"      # under 3:1 on GREEN_BAR - the guard swaps it
READABLE = "#FFA500"         # reads on GREEN_BAR - kept


@pytest.fixture(autouse=True)
def _dark_shell_and_quiet_boxes(monkeypatch):
    monkeypatch.setattr(color_scheme, "_shell_light", False)
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


@pytest.fixture
def appearance(monkeypatch):
    def set_(app):
        monkeypatch.setattr(color_scheme, "read_taskbar_appearance", lambda shell_light=False: app)
        monkeypatch.setattr(color_scheme, "_appearance", app)
    return set_


def _cfg(**over):
    cfg = dict(D.DEFAULT_CONFIG, background_opacity=0)
    cfg.update(over)
    return themed(cfg)


# --------------------------------------------------------------------------- the colour logic

def test_paint_colors_records_the_stems_it_swaps(appearance):
    appearance(GREEN_BAR)
    pc = color_scheme.paint_colors(_cfg(high_speed_color=FAINT_GREEN, low_speed_color=READABLE))
    assert "high_speed_color" in pc.replaced and pc["high_speed_color"] == "#FFFFFF"
    assert "low_speed_color" not in pc.replaced


def test_a_plain_taskbar_swaps_nothing(appearance):
    appearance(taskbar_appearance.PLAIN)
    cfg = _cfg(color_coding=True, high_speed_color="#202021")   # invisible on #202020, but no guard there
    assert color_scheme.paint_colors(cfg).replaced == ()
    assert color_scheme.accent_swapped(cfg) == ()


def test_only_the_pens_the_widget_paints_count(appearance):
    appearance(GREEN_BAR)
    faint = dict(high_speed_color=FAINT_GREEN, medium_speed_color=FAINT_GREEN,
                 arrow_up_color=FAINT_GREEN, graph_download_color=FAINT_GREEN)
    # Coding, Medium, custom arrows and the graph all off: none of those pens is painted.
    off = _cfg(color_coding=False, medium_speed_threshold=0.0, use_custom_arrow_colors=False,
               graph_enabled=False, **faint)
    assert color_scheme.accent_swapped(off) == ()
    on = _cfg(color_coding=True, medium_speed_threshold=5.0, use_custom_arrow_colors=True,
              graph_enabled=True, **faint)
    assert set(color_scheme.accent_swapped(on)) == {
        "high_speed_color", "medium_speed_color", "arrow_up_color", "graph_download_color",
        "graph_upload_color"}   # the factory upload line is swapped on this bar too, and says so
    # Medium off with coding on: its colour is not painted.
    assert "medium_speed_color" not in color_scheme.accent_swapped(
        _cfg(color_coding=True, medium_speed_threshold=0.0, **faint))


def test_the_text_colour_itself_counts(appearance):
    appearance(GREEN_BAR)
    assert color_scheme.accent_swapped(_cfg(default_color=FAINT_GREEN)) == ("default_color",)


# --------------------------------------------------------------------------- the Settings notes

def _dialog(qtbot, cfg):
    main = _Main(cfg)
    dlg = SettingsDialog(main_widget=main, config=cfg, version="2.2.1", i18n=EN,
                         available_interfaces=["Ethernet"], is_startup_enabled=False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.settings_changed.connect(lambda c: main.handle_settings_changed(c, save_to_disk=False))
    qtbot.addWidget(dlg)
    dlg.show()        # the live preview (and so the notes' live update) runs only while shown
    return dlg


def _shown(note) -> bool:
    return not note.isHidden()


def _notes(dlg):
    page = dlg.appearance_page
    return {"text": page.accent_text_note, "bands": page.colors_section.accent_bands_note,
            "arrows": page.accent_arrows_note, "graph": dlg.widget_page.mini_graph_section.accent_graph_note}


def _shown_notes(dlg):
    return {name for name, note in _notes(dlg).items() if _shown(note)}


def test_the_band_note_says_so_while_a_band_colour_is_swapped(qtbot, appearance):
    appearance(GREEN_BAR)
    dlg = _dialog(qtbot, _cfg(color_coding=True, high_speed_color=FAINT_GREEN))
    colors = dlg.appearance_page.colors_section
    assert colors.accent_bands_note.text() == EN.ACCENT_TASKBAR_COLORS_NOTE
    assert _shown_notes(dlg) == {"bands"}              # set when the page is built; only the bands'
    # The user picks a readable High colour: the note goes, live.
    colors.high_speed_color_dark_input.setText(READABLE)
    dlg._emit_settings_changed_throttled()
    assert _shown_notes(dlg) == set()
    # ...the faint colour back, then colour coding off: nothing painted is swapped.
    colors.high_speed_color_dark_input.setText(FAINT_GREEN)
    dlg._emit_settings_changed_throttled()
    assert _shown_notes(dlg) == {"bands"}
    colors.enable_colors.setChecked(False)
    dlg._emit_settings_changed_throttled()
    assert _shown_notes(dlg) == set()


@pytest.mark.parametrize("over, shown", [
    (dict(default_color=FAINT_GREEN), {"text"}),
    (dict(use_custom_arrow_colors=True, arrow_down_color=FAINT_GREEN), {"arrows"}),
    (dict(graph_enabled=True, graph_download_color=FAINT_GREEN), {"graph"}),
])
def test_each_note_speaks_only_for_its_own_rows(qtbot, appearance, over, shown):
    appearance(GREEN_BAR)
    cfg = _cfg(**over)
    if over.get("graph_enabled"):   # keep the (factory, also swapped) upload line out of the picture
        cfg.update(themed(dict(graph_upload_color=READABLE)))
    assert _shown_notes(_dialog(qtbot, cfg)) == shown


def test_no_note_on_a_plain_taskbar(qtbot, appearance):
    appearance(taskbar_appearance.PLAIN)
    dlg = _dialog(qtbot, _cfg(color_coding=True, high_speed_color=FAINT_GREEN, graph_enabled=True,
                              graph_download_color=FAINT_GREEN, default_color="#202021"))
    assert _shown_notes(dlg) == set()


def test_the_graph_note_goes_with_the_graph(qtbot, appearance):
    appearance(GREEN_BAR)
    dlg = _dialog(qtbot, _cfg(graph_enabled=True, graph_download_color=FAINT_GREEN))
    graph = dlg.widget_page.mini_graph_section
    assert _shown(graph.accent_graph_note)
    graph.enable_graph.setChecked(False)              # graph off: its colours are not painted
    dlg._emit_settings_changed_throttled()
    assert not _shown(graph.accent_graph_note)


def test_each_note_sits_right_under_its_rows(qtbot, appearance):
    """Under, never above: showing a note never moves the colour field being edited (the first cut put
    one note under the text colour, ~730 px above the band / arrow fields, and shifted them ~47 px)."""
    appearance(GREEN_BAR)
    dlg = _dialog(qtbot, _cfg(color_coding=True, use_custom_arrow_colors=True))
    page = dlg.appearance_page
    colors = page.colors_section
    cc = colors.color_container.layout()
    assert cc.itemAt(cc.count() - 1).widget() is colors.accent_bands_note
    ac = page.arrow_colors_container.layout()
    assert ac.itemAt(ac.count() - 1).widget() is page.accent_arrows_note
    flat = page.layout()
    i = flat.indexOf(page.themed_colors_note)
    assert flat.itemAt(i + 1).widget() is page.accent_text_note
    for field, faint_over in ((colors.high_speed_color_dark_input, "high_speed_color_dark"),
                              (page.arrow_up_color_dark_input, "arrow_up_color_dark")):
        qtbot.wait(10)
        y0 = field.mapTo(page, field.rect().topLeft()).y()
        field.setText(FAINT_GREEN)
        dlg._emit_settings_changed_throttled()
        qtbot.wait(10)
        assert _shown_notes(dlg) & {"bands", "arrows"}, faint_over
        assert field.mapTo(page, field.rect().topLeft()).y() == y0, faint_over
        field.setText(READABLE)
        dlg._emit_settings_changed_throttled()


# --------------------------------------------------------------------------- the preview strip

def test_the_preview_strip_is_the_accent_bar(qtbot, appearance):
    appearance(GREEN_BAR)
    dlg = _dialog(qtbot, _cfg())
    assert GREEN_BAR.accent_bar in dlg._preview_strip.styleSheet()
    assert "#FFFFFF" in dlg._preview_label.styleSheet()   # the "Preview" caption reads on the dark bar
    appearance(taskbar_appearance.PLAIN)              # accent turned off, then a theme refresh
    dlg.on_taskbar_theme_changed()
    assert constants.color.PREVIEW_STRIP_DARK in dlg._preview_strip.styleSheet()


def test_high_contrast_keeps_the_neutral_strip(qtbot, appearance):
    appearance(TaskbarAppearance(high_contrast=True, hc_text="#FFFFFF", hc_background="#000000",
                                 accent_on=True, accent_bar="#0B5A2B"))
    dlg = _dialog(qtbot, _cfg())
    assert constants.color.PREVIEW_STRIP_DARK in dlg._preview_strip.styleSheet()


def test_a_factory_colour_counts(appearance):
    """A silent swap is the #556 surprise whoever picked the colour: the factory upload line on a green
    bar and the factory Low on a red one (#C42B1C) are swapped, and the note says so."""
    appearance(GREEN_BAR)
    assert color_scheme.accent_swapped(_cfg(graph_enabled=True)) == ("graph_upload_color",)
    appearance(TaskbarAppearance(accent_on=True, accent_bar="#C42B1C"))
    assert "low_speed_color" in color_scheme.accent_swapped(_cfg(color_coding=True))


def test_hidden_arrows_paint_no_arrow_colour(appearance):
    appearance(GREEN_BAR)
    cfg = _cfg(use_custom_arrow_colors=True, arrow_up_color=FAINT_GREEN)
    assert color_scheme.accent_swapped(cfg) == ("arrow_up_color",)
    assert color_scheme.accent_swapped(dict(cfg, hide_arrows=True)) == ()


@pytest.mark.parametrize("medium, counts", [(5.0, True), (20.0, False), (0.5, False), (10.0, False),
                                            (0.0, False)])
def test_medium_counts_only_strictly_between_low_and_high(appearance, medium, counts):
    """The renderer's rule (_speed_band): a Medium outside Low..High is never painted."""
    appearance(GREEN_BAR)
    cfg = _cfg(color_coding=True, low_speed_threshold=1.0, high_speed_threshold=10.0,
               medium_speed_threshold=medium, medium_speed_color=FAINT_GREEN)
    assert ("medium_speed_color" in color_scheme.accent_swapped(cfg)) is counts
