"""
The "Reduce motion" setting (Settings > Advanced) must actually reduce motion. Until 2.2 beta.4 the
config value was read only by the Settings page: the one animation gate, prefers_reduced_motion(),
looked at the Windows "Animation effects" switch alone. Now the app setting ON forces reduced motion
and OFF follows Windows, and the settings apply path (startup + every apply) pushes it in.
"""
from unittest.mock import MagicMock

import pytest

from netspeedtray.utils import styles


@pytest.fixture(autouse=True)
def _reset_flag():
    styles.set_app_reduce_motion(False)
    yield
    styles.set_app_reduce_motion(False)


def _windows_animations(monkeypatch, enabled: bool):
    """Pretend the Windows 'Animation effects' switch is on/off (SPI_GETCLIENTAREAANIMATION)."""
    import ctypes

    def fake_spi(_action, _param, pv, _flags):
        ctypes.cast(pv, ctypes.POINTER(ctypes.c_int)).contents.value = 1 if enabled else 0
        return 1
    monkeypatch.setattr(ctypes.windll.user32, "SystemParametersInfoW", fake_spi)


def test_app_setting_on_forces_reduced_motion(monkeypatch):
    _windows_animations(monkeypatch, enabled=True)
    assert styles.prefers_reduced_motion() is False
    styles.set_app_reduce_motion(True)
    assert styles.prefers_reduced_motion() is True


def test_app_setting_off_follows_windows(monkeypatch):
    _windows_animations(monkeypatch, enabled=False)
    assert styles.prefers_reduced_motion() is True


def test_settings_apply_pushes_the_config_value():
    from netspeedtray.core.config_controller import ConfigController
    w = MagicMock()
    w.config = {"reduce_motion": True}
    ConfigController(w, MagicMock()).apply_all_settings()
    assert styles.prefers_reduced_motion() is True
    w.config = {"reduce_motion": False}
    ConfigController(w, MagicMock()).apply_all_settings()
    assert styles._app_reduce_motion is False


def test_toggle_snaps_instead_of_sliding_when_app_setting_on(q_app, monkeypatch):
    from netspeedtray.utils.components import Win11Toggle
    _windows_animations(monkeypatch, enabled=True)
    styles.set_app_reduce_motion(True)
    t = Win11Toggle(label_text="")
    t.setChecked(False)
    t._on_checkbox_toggled(True)       # the user's click path (animate=True)
    assert t.thumb_animation.state() != t.thumb_animation.State.Running
    assert t.thumb.pos().x() == t._END_X_POS
