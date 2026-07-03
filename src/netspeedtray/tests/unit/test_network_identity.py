"""
Tests for the v2.1 network-identity sensing layer (network_utils.py).

The tentpole feature shows the connected Wi-Fi band (2.4G/5G) and, opt-in, the SSID on the widget.
The load-bearing, testable-without-a-screen piece is the sensing helper. Key invariants:

  * band_from_channel maps 802.11 channels to coarse bands (2.4/5), with 6 GHz deliberately NOT
    inferrable from channel number alone (it collides with 2.4 GHz — see KICKOFF.md §2).
  * get_connected_network_identity() NEVER raises and always returns a NetworkIdentity, however the
    WLAN API / Location gate / adapter state behaves. This is what lets the widget degrade cleanly
    (band-only, or wired fallback, or empty) instead of crashing the poll loop.
"""
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core.controller import StatsController
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.utils import network_utils
from netspeedtray.utils.network_utils import (
    BAND_ALERT_COLOR,
    BAND_COLORS,
    MAX_SSID_DISPLAY_CHARS,
    NetworkIdentity,
    band_from_channel,
    get_connected_network_identity,
    resolve_band_presentation,
    truncate_ssid,
)


@pytest.mark.parametrize("band,mode,exp", [
    # always: text shown, no tint, outline pill (solid False)
    ("5G", "always", ("5G", None, False)),
    ("2.4G", "always", ("2.4G", None, False)),
    # colored: text shown, tinted by band, outline pill
    ("5G", "colored", ("5G", BAND_COLORS["5G"], False)),
    ("2.4G", "colored", ("2.4G", BAND_COLORS["2.4G"], False)),
    ("6G", "colored", ("6G", BAND_COLORS["6G"], False)),
    # alert_only: ONLY the degraded 2.4G shows, in the warning color, as a SOLID pill; good bands hidden
    ("2.4G", "alert_only", ("2.4G", BAND_ALERT_COLOR, True)),
    ("5G", "alert_only", (None, None, False)),
    ("6G", "alert_only", (None, None, False)),
    # unknown / wired band -> nothing, in every mode
    (None, "always", (None, None, False)),
    (None, "colored", (None, None, False)),
    (None, "alert_only", (None, None, False)),
])
def test_resolve_band_presentation(band, mode, exp):
    assert resolve_band_presentation(band, mode) == exp


@pytest.mark.parametrize("name,expected", [
    (None, None),
    ("", None),
    ("   ", None),
    ("MyNet", "MyNet"),
    ("A" * MAX_SSID_DISPLAY_CHARS, "A" * MAX_SSID_DISPLAY_CHARS),        # exactly at the cap: unchanged
    ("B" * (MAX_SSID_DISPLAY_CHARS + 5), "B" * (MAX_SSID_DISPLAY_CHARS - 1) + "…"),  # over cap: ellipsized
])
def test_truncate_ssid(name, expected):
    assert truncate_ssid(name) == expected


@pytest.mark.parametrize("channel,expected", [
    (1, "2.4G"), (6, "2.4G"), (11, "2.4G"), (14, "2.4G"),   # 2.4 GHz band
    (32, "5G"), (36, "5G"), (100, "5G"), (149, "5G"), (177, "5G"),  # 5 GHz band
    (0, None), (-1, None), (15, None), (200, None), (233, None),    # unknown / gap / 6E-ambiguous
])
def test_band_from_channel(channel, expected):
    assert band_from_channel(channel) == expected


def test_get_identity_never_raises_and_returns_dataclass():
    """Whatever the platform / WLAN state, the helper returns a NetworkIdentity and does not raise."""
    ident = get_connected_network_identity()
    assert isinstance(ident, NetworkIdentity)
    # band, when present, is always one of the coarse tags.
    assert ident.band in (None, "2.4G", "5G", "6G")
    # A blocked SSID must never leak a name (honesty: absent, not wrong).
    if ident.ssid_blocked:
        assert ident.name is None


def test_non_windows_falls_back_to_wired(monkeypatch):
    """On a non-win32 platform the helper returns a wired-style identity from the primary interface."""
    monkeypatch.setattr(network_utils.sys, "platform", "linux")
    monkeypatch.setattr(network_utils, "get_primary_interface_name", lambda: "eth0")
    ident = get_connected_network_identity()
    assert ident == NetworkIdentity(name="eth0", band=None, is_wireless=False,
                                    ssid_blocked=False, connected=True)


def test_wired_fallback_when_no_primary_interface(monkeypatch):
    """No resolvable interface -> a safe, empty-but-valid identity (connected=False)."""
    monkeypatch.setattr(network_utils.sys, "platform", "linux")
    monkeypatch.setattr(network_utils, "get_primary_interface_name", lambda: None)
    ident = get_connected_network_identity()
    assert ident.is_wireless is False
    assert ident.connected is False
    assert ident.name is None


# --- Controller plumbing: the identity flows thread -> controller -> widget slot ---

def _controller() -> StatsController:
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    return StatsController(config=config, widget_state=MagicMock(spec=WidgetState))


def test_controller_forwards_network_identity():
    """handle_stats with a 'network_identity' key emits it to the view's update_network_identity slot."""
    controller = _controller()
    view = MagicMock()
    controller.set_view(view)
    ident = NetworkIdentity(name=None, band="5G", is_wireless=True, ssid_blocked=True, connected=True)
    controller.handle_stats({"network_identity": ident})
    view.update_network_identity.assert_called_once_with(ident)


def test_controller_no_identity_key_no_emit():
    """A stats dict without the key must NOT fire the identity slot (present-key-emits contract)."""
    controller = _controller()
    view = MagicMock()
    controller.set_view(view)
    controller.handle_stats({})
    view.update_network_identity.assert_not_called()


# --- Render: the band tag reserves + widens the network readout (guards the #106 clip class) ---

def test_band_tag_widens_content_rect(qapp):
    """Passing identity_text to draw_network_speeds reserves the band slot, widening the content rect."""
    from PyQt6.QtGui import QImage, QPainter
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.utils.widget_renderer import WidgetRenderer

    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    config["hide_arrows"] = True
    r = WidgetRenderer(config, I18nStrings())
    img = QImage(320, 48, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        r.draw_network_speeds(p, 1_500_000, 9_000_000, 320, 48, r.config, "horizontal")
        w_off = r.get_last_text_rect().width()
        r.draw_network_speeds(p, 1_500_000, 9_000_000, 320, 48, r.config, "horizontal", identity_text="5G")
        w_on = r.get_last_text_rect().width()
    finally:
        p.end()
    # The band adds gap + the fixed worst-case "2.4G" reserve; drawing "5G" never clips or shrinks it.
    assert w_on > w_off


def test_identity_layout_modes(qapp):
    """identity_layout: band-only / ssid-only / compound geometry; the compound nests the band flush right."""
    from PyQt6.QtGui import QFont, QFontMetrics
    from netspeedtray.utils.widget_renderer import identity_layout

    fm = QFontMetrics(QFont("Segoe UI", 13))
    w_band, p_band = identity_layout(fm, None, "5G")
    w_ssid, p_ssid = identity_layout(fm, "HomeWiFi", None)
    w_both, p_both = identity_layout(fm, "HomeWiFi", "5G")
    w_none, p_none = identity_layout(fm, None, None)

    assert p_band["mode"] == "band" and w_band > 0
    assert p_ssid["mode"] == "ssid" and w_ssid > 0
    assert p_both["mode"] == "both"
    assert w_both > w_ssid and w_both > w_band          # compound is wider than either element alone
    assert p_both["band_x"] + p_both["band_w"] == w_both  # the nested band pill is flush to the right edge
    assert w_none == 0 and p_none["mode"] == "none"


def test_identity_settings_roundtrip(qapp):
    """The Network-identity settings section round-trips show_network_identity + band_display."""
    from netspeedtray.views.settings.pages.network_identity_section import NetworkIdentitySettings

    sec = NetworkIdentitySettings(on_change=lambda: None, i18n=None)
    sec.load_settings({"show_network_identity": True, "identity_mode": "both", "band_display": "alert_only"})
    out = sec.get_settings()
    assert out["show_network_identity"] is True
    assert out["identity_mode"] == "both"
    assert out["band_display"] == "alert_only"

    sec.load_settings({"show_network_identity": False, "identity_mode": "ssid", "band_display": "colored"})
    assert sec.get_settings() == {
        "show_network_identity": False, "identity_mode": "ssid", "band_display": "colored"}
