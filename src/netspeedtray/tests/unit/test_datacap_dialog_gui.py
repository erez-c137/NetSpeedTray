"""GUI smoke test for the data-cap settings dialog (pytest-qt, offscreen)."""
from netspeedtray.views.datacap_dialog import DataCapDialog


def test_datacap_dialog_roundtrips_config(qtbot):
    cfg = {"data_cap_enabled": True, "data_cap_gb": 250, "data_cap_reset_day": 15,
           "data_cap_count": "download", "data_cap_alert_enabled": False}
    d = DataCapDialog(cfg, used_bytes=1000)
    qtbot.addWidget(d)
    vals = d.get_values()
    assert vals["data_cap_enabled"] is True
    assert vals["data_cap_gb"] == 250.0
    assert vals["data_cap_reset_day"] == 15
    assert vals["data_cap_count"] == "download"
    assert vals["data_cap_alert_enabled"] is False


def test_datacap_dialog_defaults_are_sane(qtbot):
    d = DataCapDialog({}, used_bytes=0)
    qtbot.addWidget(d)
    vals = d.get_values()
    assert vals["data_cap_count"] == "total"
    assert vals["data_cap_reset_day"] == 1
    assert vals["data_cap_enabled"] is False
