"""Unit tests for the data-cap UsageAlertController (logic only; deps injected)."""
from netspeedtray.core.usage_alerts import UsageAlertController, used_for_count

_GB = 1000 ** 3


def _make(used_up_gb=0.0, used_down_gb=0.0, period="2026-06-01", state="", **cfg):
    config = {"data_cap_enabled": True, "data_cap_alert_enabled": True,
              "data_cap_gb": 100.0, "data_cap_count": "total", "usage_alert_state": state}
    config.update(cfg)
    fired, saved = [], []
    ctrl = UsageAlertController(
        usage_getter=lambda: (used_up_gb * _GB, used_down_gb * _GB),
        config_getter=lambda: config,
        period_getter=lambda: period,
        notify=lambda title, msg: fired.append((title, msg)),
        save_state=lambda s: saved.append(s),
    )
    return ctrl, config, fired, saved


def test_used_for_count_modes():
    assert used_for_count(10, 90, "total") == 100
    assert used_for_count(10, 90, "download") == 90
    assert used_for_count(10, 90, "upload") == 10


def test_no_alert_below_80_percent():
    ctrl, _, fired, saved = _make(used_down_gb=50)  # 50% of 100GB
    ctrl.check()
    assert fired == [] and saved == []


def test_fires_80_once():
    ctrl, _, fired, saved = _make(used_down_gb=85)  # 85%
    ctrl.check()
    assert len(fired) == 1 and "80%" in fired[0][1]
    assert saved and "80" in saved[-1]
    # A second check at the same level doesn't re-fire (state now records 80).
    ctrl._config()["usage_alert_state"] = saved[-1]
    fired.clear()
    ctrl.check()
    assert fired == []


def test_over_cap_on_first_check_fires_only_reached_not_a_stacked_warning():
    """#9: when 80% and 100% cross in the SAME check (already over cap on a period's first check), fire
    ONLY the highest ('reached') - not two flyouts stacked at the same position - yet record both levels
    so neither re-fires. (The normal gradual path still fires 80% then 100% on separate checks.)"""
    ctrl, _, fired, saved = _make(used_down_gb=120)  # 120%
    ctrl.check()
    titles = [t for t, _ in fired]
    assert len(fired) == 1
    assert any("reached" in t.lower() for t in titles)
    assert not any("warning" in t.lower() for t in titles)
    # Both levels were recorded as fired -> a re-check at the same usage fires nothing.
    ctrl._config()["usage_alert_state"] = saved[-1]
    fired.clear()
    ctrl.check()
    assert fired == []


def test_new_period_clears_fired_state():
    # State says 80 already fired, but for a DIFFERENT period -> should fire again.
    ctrl, _, fired, _ = _make(used_down_gb=85, period="2026-07-01",
                              state="2026-06-01|100|total::80,100")
    ctrl.check()
    assert len(fired) == 1


def test_changing_cap_clears_fired_state():
    # 80 already fired at a 100GB cap; user changes the cap -> re-evaluate at the new cap.
    ctrl, _, fired, _ = _make(used_down_gb=85, data_cap_gb=80.0,
                              state="2026-06-01|100|total::80,100")
    ctrl.check()
    assert len(fired) >= 1  # not suppressed by the stale 100GB-cap state


def test_disabled_cap_is_noop():
    ctrl, _, fired, _ = _make(used_down_gb=120, data_cap_enabled=False)
    ctrl.check()
    assert fired == []


def test_alerts_disabled_is_noop():
    ctrl, _, fired, _ = _make(used_down_gb=120, data_cap_alert_enabled=False)
    ctrl.check()
    assert fired == []


def test_zero_cap_is_noop():
    ctrl, _, fired, _ = _make(used_down_gb=120, data_cap_gb=0.0)
    ctrl.check()
    assert fired == []
