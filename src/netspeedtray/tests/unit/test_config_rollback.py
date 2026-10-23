"""
rc.2 S8 - a failed settings apply must roll the LIVE app back, not just the file.

apply_all_settings walks the components in order (renderer -> controller -> monitor thread -> widget
state -> font/position -> ...). When one of them throws, the ones before it already run the new
values. rollback_config used to restore the dict and save it, but never re-applied it, so the widget
kept drawing half-applied settings until the next restart.
"""
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core.config_controller import ConfigController

OLD_FONT, NEW_FONT = 9, 13


def _widget():
    w = MagicMock()
    w.config = dict(constants.config.defaults.DEFAULT_CONFIG)
    w.config["font_size"] = OLD_FONT
    w.settings_dialog = None
    w.monitor_window = None
    return w


def _fail_on_new_font(cfg):
    """The widget-state step - halfway down apply_all_settings - rejects the new value."""
    if cfg.get("font_size") == NEW_FONT:
        raise ValueError("widget state rejected the new config")


def _recorder(seen, then=None):
    """Mocks keep a reference to the dict they were given (and the rollback restores it in place),
    so record the font each call actually saw."""
    def record(cfg, *a, **k):
        seen.append(cfg.get("font_size"))
        if then is not None:
            then(cfg)
    return record


@pytest.fixture
def setup():
    w = _widget()
    seen = {name: [] for name in ("renderer", "controller", "thread", "state", "save")}
    w.renderer.update_config.side_effect = _recorder(seen["renderer"])
    w.controller.apply_config.side_effect = _recorder(seen["controller"])
    w.monitor_thread.update_config.side_effect = _recorder(seen["thread"])
    w.widget_state.apply_config.side_effect = _recorder(seen["state"], _fail_on_new_font)
    cm = MagicMock()
    cm.save.side_effect = _recorder(seen["save"])
    with patch("netspeedtray.core.config_controller.set_app_reduce_motion"):
        yield ConfigController(w, cm), w, seen


def test_a_failed_apply_reapplies_the_old_config_to_every_component(setup):
    cc, w, seen = setup
    holder = w.config                         # e.g. a component that kept the widget's dict
    with pytest.raises(RuntimeError):
        cc.handle_settings_changed({"font_size": NEW_FONT}, save_to_disk=True)

    # The components before the failing step had already taken the new font ...
    assert NEW_FONT in seen["renderer"] and NEW_FONT in seen["controller"] and NEW_FONT in seen["thread"]
    # ... and the rollback put the old one back through the same apply path.
    for name in ("renderer", "controller", "thread", "state", "save"):
        assert seen[name][-1] == OLD_FONT, f"{name} was left on the new config: {seen[name]}"
    w.layout_manager.set_font.assert_called()          # the steps after the failure ran on the old config

    assert w.config["font_size"] == OLD_FONT
    assert holder is w.config and holder["font_size"] == OLD_FONT


def test_a_failed_live_preview_rolls_back_without_writing_the_file(setup):
    """The preview path (save_to_disk=False) never wrote config.json; its rollback must not either -
    the in-memory dict can hold earlier previewed values that Cancel would then fail to undo."""
    cc, w, seen = setup
    with pytest.raises(RuntimeError):
        cc.handle_settings_changed({"font_size": NEW_FONT}, save_to_disk=False)
    assert seen["save"] == [], f"a preview rollback wrote the config file: {seen['save']}"
    assert w.config["font_size"] == OLD_FONT
    assert seen["renderer"][-1] == OLD_FONT          # still re-applied live


def test_a_rollback_whose_reapply_also_fails_does_not_loop_or_mask_the_error(setup):
    cc, w, _seen = setup
    w.widget_state.apply_config.side_effect = ValueError("broken either way")
    with pytest.raises(RuntimeError, match="broken either way"):
        cc.handle_settings_changed({"font_size": NEW_FONT}, save_to_disk=True)
    # One failed apply + one re-apply attempt; no recursion.
    assert w.widget_state.apply_config.call_count == 2
    assert w.config["font_size"] == OLD_FONT
    assert cc._rolling_back is False
