"""rc.2 G1-G3: LibreHardwareMonitor web-server authentication, a configurable port, and the new advice.

LHM 0.9.5/0.9.6 cannot bind 127.0.0.1 alone, so the only setup our loopback request reaches listens
on all interfaces - and LHM's optional HTTP Basic authentication is what guards it on the LAN. These
tests pin that the credentials only ever travel to loopback, that a 401 reads differently from "not
running", that the password is stored only as a DPAPI blob and never reaches a log or the support
bundle, and that the port is validated end to end. Client tests use real loopback servers, as
test_lhm_http.py does.
"""
import base64
import json
import logging
import socket
import threading
import zipfile
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.core.lhm_http import LhmHttpClient
from netspeedtray.utils import secret_store
from netspeedtray.utils import support_bundle

L = constants.lhm
USER, PASSWORD = "nst-user", "S3cret-pässword!"
_TOKEN = base64.b64encode(f"{USER}:{PASSWORD}".encode("utf-8")).decode("ascii")
_TREE = {"Text": "Sensor", "Children": [
    {"Text": "AMD CPU", "HardwareId": "/amdcpu/0", "Children": [
        {"Text": "Package", "SensorId": "/amdcpu/0/power/0", "Type": "Power", "Value": "42 W",
         "Children": []}]}]}

try:
    import win32crypt  # noqa: F401
    HAVE_DPAPI = True
except ImportError:
    HAVE_DPAPI = False


# --- a loopback server that records each request and answers like LHM with auth on ---------------

class _AuthServer:
    """Answers 200 + data.json when the Basic credentials match ``expected`` (or when ``expected`` is
    None, i.e. authentication off), else 401 with LHM's challenge. Records every request's headers."""

    def __init__(self, expected=None):
        self.expected = expected
        self.requests = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self.sock.settimeout(0.05)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(3.0)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")[1:]
                headers = {k.strip().lower(): v.strip() for k, v in
                           (line.split(":", 1) for line in head if ":" in line)}
                self.requests.append(headers)
                if self.expected is None or headers.get("authorization") == f"Basic {self.expected}":
                    body = json.dumps(_TREE).encode()
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                 + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                else:
                    conn.sendall(b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="Libre '
                                 b'Hardware Monitor"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')

    def close(self):
        self._stop.set()
        self._thread.join(1.0)
        self.sock.close()


@pytest.fixture
def auth_server():
    servers = []

    def start(expected=None):
        s = _AuthServer(expected)
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.close()


def _client(port, **kw):
    kw.setdefault("failure_backoff_seconds", 30.0)
    return LhmHttpClient(port=port, **kw)


# --- G2: Basic auth only to loopback --------------------------------------------------------------

def test_credentials_are_sent_as_basic_auth_to_loopback(auth_server):
    server = auth_server(expected=_TOKEN)
    client = _client(server.port, username=USER, password=PASSWORD)
    snapshot = client.get_snapshot()
    assert snapshot is not None and snapshot.cpu_power == 42.0
    assert server.requests[0]["authorization"] == f"Basic {_TOKEN}"
    assert client.status == L.STATUS_OK


def test_no_authorization_header_without_credentials(auth_server):
    server = auth_server(expected=None)
    assert _client(server.port).get_snapshot() is not None
    assert "authorization" not in server.requests[0]


@pytest.mark.parametrize("host", ["192.168.1.5", "10.0.0.2", "localhost", "lhm.example.com", "::1",
                                  "0.0.0.0"])
def test_credentials_never_go_to_a_host_that_is_not_numeric_loopback(host):
    client = LhmHttpClient(host=host, username=USER, password=PASSWORD)
    assert "Authorization" not in client._request_headers()


def test_the_default_host_is_one_credentials_may_go_to():
    assert L.HTTP_HOST in L.AUTH_ALLOWED_HOSTS == ("127.0.0.1",)
    assert "Authorization" in LhmHttpClient(username=USER, password=PASSWORD)._request_headers()


# --- G2: a 401 is told apart from "not running" ---------------------------------------------------

def test_a_401_without_credentials_means_authentication_required(auth_server, caplog):
    server = auth_server(expected=_TOKEN)
    client = _client(server.port)
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.LhmHttp"):
        assert client.get_snapshot() is None
    assert client.status == L.STATUS_AUTH_REQUIRED
    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert "401" in msg and "Settings > Hardware" in msg


def test_a_401_with_credentials_means_they_were_rejected(auth_server, caplog):
    server = auth_server(expected=base64.b64encode(b"nst-user:other").decode())
    client = _client(server.port, username=USER, password=PASSWORD)
    with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.LhmHttp"):
        assert client.get_snapshot() is None
    assert client.status == L.STATUS_AUTH_FAILED
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "rejected" in msg and "restart" in msg
    # Never a credential in the log - not the password, not the user name, not the header token.
    for secret in (PASSWORD, USER, _TOKEN):
        assert secret not in msg


def test_nothing_listening_is_not_found_not_an_auth_problem():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()   # a port nothing listens on
    client = _client(port, username=USER, password=PASSWORD)
    assert client.get_snapshot() is None
    assert client.status == L.STATUS_NOT_FOUND


def test_status_starts_unknown_and_survives_the_backoff_window(auth_server):
    server = auth_server(expected=_TOKEN)
    client = _client(server.port)
    assert client.status == L.STATUS_UNKNOWN
    client.get_snapshot()
    client.get_snapshot()   # inside the backoff: no request, status kept
    assert len(server.requests) == 1
    assert client.status == L.STATUS_AUTH_REQUIRED


def test_entering_the_right_password_reads_on_the_next_poll(auth_server):
    """Settings > Hardware applies credentials through configure(); the 30 s backoff must not delay
    the fix, and the 0.5 s deadline still bounds the retry."""
    server = auth_server(expected=_TOKEN)
    client = _client(server.port)
    assert client.get_snapshot() is None
    client.configure(server.port, USER, PASSWORD)
    assert client.status == L.STATUS_UNKNOWN
    assert client.get_snapshot() is not None
    assert client.status == L.STATUS_OK
    assert client.total_deadline == L.TOTAL_DEADLINE_SEC


def test_configure_with_unchanged_settings_keeps_the_backoff(auth_server):
    server = auth_server(expected=_TOKEN)
    client = _client(server.port)
    client.get_snapshot()
    client.configure(server.port, "", "")
    client.get_snapshot()
    assert len(server.requests) == 1


# --- G3: the port ---------------------------------------------------------------------------------

def test_the_same_failure_on_a_new_port_is_logged_again(auth_server, caplog):
    """Review L3: the dedupe key ignored the port, so a port change that failed the same way was silent."""
    first, second = auth_server(expected=_TOKEN), auth_server(expected=_TOKEN)
    client = _client(first.port)
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.LhmHttp"):
        client.get_snapshot()
        client.configure(second.port)
        client.get_snapshot()
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(lines) == 2 and str(second.port) in lines[1]


def test_configure_moves_the_client_to_the_new_port(auth_server):
    old, new = auth_server(), auth_server()
    client = _client(old.port)
    assert client.get_snapshot() is not None
    client.configure(new.port)
    client._cached = None
    assert client.get_snapshot() is not None
    assert len(new.requests) == 1 and client.url.endswith(f":{new.port}/data.json")


@pytest.mark.parametrize("value,expected", [
    (8085, 8085), (1, 1), (65535, 65535), (18085, 18085),
    (0, 8085), (65536, 8085), (-1, 8085), ("abc", 8085), (None, 8085),
])
def test_lhm_port_config_validation(tmp_path, value, expected):
    from netspeedtray.utils.config import ConfigManager
    cm = ConfigManager(tmp_path / "c.json")
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, lhm_port=value)
    assert cm._validate_config(cfg)["lhm_port"] == expected


def test_lhm_keys_have_defaults_and_schema():
    d = constants.config.defaults
    assert d.DEFAULT_CONFIG["lhm_port"] == L.HTTP_PORT == 8085
    assert d.DEFAULT_CONFIG["lhm_username"] == "" and d.DEFAULT_CONFIG["lhm_password_protected"] == ""
    schema = d.VALIDATION_SCHEMA["lhm_port"]
    assert (schema["min"], schema["max"]) == (1, 65535)
    # A format check would log the rejected value; the password blob must never be logged.
    assert "regex" not in d.VALIDATION_SCHEMA["lhm_password_protected"]


def _monitor_thread(config):
    from netspeedtray.core.monitor_thread import StatsMonitorThread
    t = StatsMonitorThread(interval=1.0, config=config)
    t._lhm_http = LhmHttpClient()   # the real client (conftest stubs it out by default)
    t._apply_lhm_settings()
    return t


@pytest.mark.parametrize("port,expected", [(18085, 18085), (0, 8085), ("x", 8085), (99999, 8085)])
def test_monitor_thread_points_the_client_at_the_configured_port(port, expected):
    t = _monitor_thread({"lhm_port": port})
    assert t._lhm_http.port == expected == t.lhm_port


def test_monitor_thread_decrypts_the_saved_password_for_the_client():
    with patch.object(secret_store, "unprotect", return_value=PASSWORD) as un:
        t = _monitor_thread({"lhm_username": USER, "lhm_password_protected": "QkxPQg=="})
        t._apply_lhm_settings()   # an unchanged blob is not decrypted again
    assert un.call_count == 1
    assert t._lhm_http._request_headers()["Authorization"] == f"Basic {_TOKEN}"


def test_monitor_thread_reapplies_settings_when_the_config_changes(auth_server):
    server = auth_server()
    t = _monitor_thread({"lhm_port": 8085})
    t.update_config({"lhm_port": server.port})
    t._apply_lhm_settings()   # what run() does on its own thread after update_config
    assert t._lhm_http.port == server.port


# --- G2: DPAPI ------------------------------------------------------------------------------------

@pytest.mark.skipif(not HAVE_DPAPI, reason="needs Windows DPAPI (pywin32)")
def test_dpapi_round_trip_is_real_and_not_plain_text():
    blob = secret_store.protect(PASSWORD)
    assert blob and PASSWORD not in blob
    assert PASSWORD.encode("utf-8") not in base64.b64decode(blob)
    assert secret_store.unprotect(blob) == PASSWORD
    # Bound to this app's entropy: a plain CryptUnprotectData by another program fails.
    assert secret_store.unprotect(blob, entropy=b"someone else") is None


def test_dpapi_round_trip_mocked():
    class FakeCrypt:
        @staticmethod
        def CryptProtectData(data, desc, entropy, reserved, prompt, flags):
            assert entropy == L.DPAPI_ENTROPY and flags == 0x1
            return b"ENC" + data[::-1]

        @staticmethod
        def CryptUnprotectData(blob, entropy, reserved, prompt, flags):
            assert entropy == L.DPAPI_ENTROPY
            return "", blob[3:][::-1]

    with patch.object(secret_store, "_win32crypt", return_value=FakeCrypt):
        blob = secret_store.protect(PASSWORD)
        assert secret_store.unprotect(blob) == PASSWORD


def test_without_dpapi_the_password_is_not_stored_at_all(caplog):
    with patch.object(secret_store, "_win32crypt", return_value=None):
        with caplog.at_level(logging.DEBUG):
            assert secret_store.protect(PASSWORD) == ""
    assert PASSWORD not in caplog.text


@pytest.mark.parametrize("stored", ["", None, "not base64 !!", "QUJD", 42])
def test_unprotect_of_garbage_is_none_and_never_raises(stored):
    assert secret_store.unprotect(stored) is None


def test_a_dpapi_failure_never_logs_the_secret(caplog):
    class Boom:
        @staticmethod
        def CryptProtectData(*a):
            raise RuntimeError(f"failed for {PASSWORD}")

    with patch.object(secret_store, "_win32crypt", return_value=Boom):
        with caplog.at_level(logging.DEBUG):
            assert secret_store.protect(PASSWORD) == ""
    assert PASSWORD not in caplog.text


# --- G2: never in the support bundle --------------------------------------------------------------

def test_the_support_bundle_redacts_the_lhm_credentials(tmp_path, monkeypatch):
    log_dir = tmp_path / "AppData"
    log_dir.mkdir()
    (log_dir / "NetSpeedTray_Log.log").write_text("2026-10-22 10:00:00 - INFO - hello\n", encoding="utf-8")
    monkeypatch.setattr(support_bundle, "get_app_data_path", lambda: log_dir)
    blob = base64.b64encode(b"\x01\x00\x00\x00dpapi-blob-bytes").decode()
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG,
               lhm_username=USER, lhm_password_protected=blob, lhm_port=18085)
    dest = tmp_path / "bundle.zip"
    support_bundle.build_support_bundle(dest, cfg)
    with zipfile.ZipFile(dest) as zf:
        everything = b"".join(zf.read(n) for n in zf.namelist()).decode("utf-8", "replace")
        bundled = json.loads(zf.read("config.json"))
    for secret in (USER, blob, PASSWORD):
        assert secret not in everything
    assert bundled["lhm_username"] == "<redacted>" and bundled["lhm_password_protected"] == "<redacted>"
    assert bundled["lhm_port"] == 18085   # the port is diagnostic, not a secret


def test_unset_credentials_stay_visibly_unset_in_the_bundle():
    out = support_bundle._sanitize_config(dict(constants.config.defaults.DEFAULT_CONFIG))
    assert out["lhm_username"] == "" and out["lhm_password_protected"] == ""


# --- Settings > Hardware --------------------------------------------------------------------------

@pytest.fixture
def page(qtbot):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    status = {"value": None}
    p = HardwarePage(I18nStrings("en_US"), lambda: None, lhm_status_provider=lambda: status["value"])
    qtbot.addWidget(p)
    p.status = status
    return p


def _fake_crypt(monkeypatch):
    monkeypatch.setattr(secret_store, "protect", lambda s, entropy=L.DPAPI_ENTROPY: "B64:" + s[::-1] if s else "")
    monkeypatch.setattr(secret_store, "unprotect",
                        lambda b, entropy=L.DPAPI_ENTROPY: b[4:][::-1] if b and b.startswith("B64:") else None)


def test_the_page_saves_only_the_protected_password(page, monkeypatch):
    _fake_crypt(monkeypatch)
    page.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG))
    page.lhm_username.setText(USER)
    page.lhm_password.setText(PASSWORD)
    out = page.get_settings()
    assert out["lhm_username"] == USER
    assert out["lhm_password_protected"] == "B64:" + PASSWORD[::-1]
    assert PASSWORD not in json.dumps(out)


def test_the_page_round_trips_without_re_encrypting(page, monkeypatch):
    _fake_crypt(monkeypatch)
    calls = []
    real = secret_store.protect
    monkeypatch.setattr(secret_store, "protect", lambda s, entropy=L.DPAPI_ENTROPY: calls.append(s) or real(s))
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, lhm_port=18085, lhm_username=USER,
               lhm_password_protected="B64:" + PASSWORD[::-1])
    page.load_settings(cfg)
    assert page.lhm_password.text() == PASSWORD
    assert page.lhm_password.echoMode() == page.lhm_password.EchoMode.Password
    out = page.get_settings()
    assert out["lhm_password_protected"] == cfg["lhm_password_protected"] and calls == []
    assert out["lhm_port"] == 18085
    page.lhm_password.setText("")
    assert page.get_settings()["lhm_password_protected"] == ""


def test_a_blob_this_user_cannot_decrypt_is_kept_with_a_hint(page, monkeypatch):
    """Review L4: an unreadable blob (other account / PC, broken DPAPI) was dropped silently on Save."""
    _fake_crypt(monkeypatch)
    page.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG, lhm_password_protected="FOREIGN"))
    assert page.lhm_password.text() == ""
    assert page.get_settings()["lhm_password_protected"] == "FOREIGN"
    assert page._lhm_password_card._desc_label.text() == page.i18n.LHM_PASSWORD_UNREADABLE


def test_typing_a_new_password_replaces_an_unreadable_blob(page, monkeypatch):
    _fake_crypt(monkeypatch)
    page.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG, lhm_password_protected="FOREIGN"))
    page.lhm_password.setText(PASSWORD)
    page.lhm_password.textEdited.emit(PASSWORD)   # what a keystroke emits (setText does not)
    assert page.get_settings()["lhm_password_protected"] == "B64:" + PASSWORD[::-1]
    assert page._lhm_password_card._desc_label.text().strip() == ""


def test_clearing_the_field_removes_an_unreadable_blob(page, monkeypatch):
    _fake_crypt(monkeypatch)
    page.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG, lhm_password_protected="FOREIGN"))
    page.lhm_password.textEdited.emit("")   # typed then deleted: the user cleared it
    assert page.get_settings()["lhm_password_protected"] == ""


def test_an_unreadable_blob_logs_one_warning_and_never_the_blob(caplog):
    secret_store._warned_unreadable.clear()
    with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.SecretStore"):
        for _ in range(3):
            assert secret_store.unprotect("!!not-a-blob!!") is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "!!not-a-blob!!" not in caplog.text


def test_the_plain_password_is_wiped_when_the_page_hides_and_restored_on_show(page, monkeypatch, qtbot):
    """Review L1: the Settings dialog is hidden, not destroyed, so the plain text sat in memory."""
    _fake_crypt(monkeypatch)
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, lhm_password_protected="B64:" + PASSWORD[::-1])
    page.load_settings(cfg)
    page.show()
    qtbot.waitExposed(page)
    page.hide()
    assert page.lhm_password.text() == "" and page._lhm_password_plain == ""
    assert page.get_settings()["lhm_password_protected"] == cfg["lhm_password_protected"]
    page.show()
    assert page.lhm_password.text() == PASSWORD   # decrypted again for the user


def test_a_pending_edit_survives_the_wipe_on_hide(page, monkeypatch, qtbot):
    _fake_crypt(monkeypatch)
    page.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG))
    page.show()
    qtbot.waitExposed(page)
    page.lhm_password.setText("typed-not-saved")
    page.hide()   # e.g. the user switched to another Settings page before saving
    assert page.lhm_password.text() == ""
    assert page.get_settings()["lhm_password_protected"] == "B64:" + "typed-not-saved"[::-1]


def test_the_status_line_is_only_touched_on_a_change(page, monkeypatch):
    """Review L2: the 1 s timer re-set the description every tick (relayout + accessible reset)."""
    calls = []
    real = page._lhm_port_card.set_description
    monkeypatch.setattr(page._lhm_port_card, "set_description", lambda t: calls.append(t) or real(t))
    page.status["value"] = (L.STATUS_OK, 8085)
    for _ in range(5):
        page.refresh_lhm_status()
    page.status["value"] = (L.STATUS_AUTH_FAILED, 8085)
    page.refresh_lhm_status()
    page.refresh_lhm_status()
    assert len(calls) == 2


def test_the_port_spin_box_is_bounded_and_drives_the_hint(page):
    page.load_settings(dict(constants.config.defaults.DEFAULT_CONFIG, lhm_port=99999))
    assert page.lhm_port.value() == 8085
    assert (page.lhm_port.minimum(), page.lhm_port.maximum()) == (1, 65535)
    page.lhm_port.setValue(18085)
    assert "18085" in page.lhm_web_server_hint.text()
    assert page.get_settings()["lhm_port"] == 18085


@pytest.mark.parametrize("status,key", [
    (L.STATUS_OK, "LHM_STATUS_CONNECTED"), (L.STATUS_NOT_FOUND, "LHM_STATUS_NOT_FOUND"),
    (L.STATUS_AUTH_REQUIRED, "LHM_STATUS_AUTH_REQUIRED"), (L.STATUS_AUTH_FAILED, "LHM_STATUS_AUTH_FAILED"),
    (L.STATUS_OTHER, "LHM_STATUS_OTHER"),
])
def test_the_status_line_tells_each_result_apart(page, status, key):
    page.status["value"] = (status, 8085)
    page.refresh_lhm_status()
    assert page._lhm_port_card._desc_label.text() == getattr(page.i18n, key).format(port=8085)


@pytest.mark.parametrize("value", [None, (L.STATUS_UNKNOWN, 8085), (L.STATUS_OK, 9999)])
def test_no_status_line_for_unknown_or_for_another_port(page, value):
    page.status["value"] = value
    page.refresh_lhm_status()
    assert page._lhm_port_card._desc_label.text().strip() == ""


def test_reset_all_returns_the_lhm_settings_to_factory(qtbot, monkeypatch):
    from unittest.mock import MagicMock
    from PyQt6.QtWidgets import QMessageBox
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.dialog import SettingsDialog
    _fake_crypt(monkeypatch)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    m = MagicMock(); m.config = {}; m.config_manager = MagicMock()
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, lhm_port=18085, lhm_username=USER,
               lhm_password_protected="B64:" + PASSWORD[::-1])
    d = SettingsDialog(main_widget=m, config=cfg, version="2.2.0", i18n=I18nStrings("en_US"),
                       available_interfaces=["Ethernet"], is_startup_enabled=False)
    qtbot.addWidget(d)
    assert d.get_settings()["lhm_port"] == 18085
    d._reset_all_to_defaults()
    out = d.get_settings()
    assert (out["lhm_port"], out["lhm_username"], out["lhm_password_protected"]) == (8085, "", "")


# --- G1: the advice -------------------------------------------------------------------------------

def _locale(code):
    from pathlib import Path
    path = Path(constants.__file__).parent / "locales" / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", sorted(constants.I18nStrings.LANGUAGE_MAP))
def test_every_locale_gives_the_new_lhm_advice(code):
    hint = _locale(code)["LHM_WEB_SERVER_HINT"]
    assert "Options → Remote Web Server" in hint     # LHM's own menu stays in English
    assert "127.0.0.1" in hint                       # pick loopback once LHM offers it
    assert hint.count("{port}") == 2                 # the server's port, and the firewall port
    hint.format(port=8085)


def test_the_english_advice_no_longer_says_to_turn_authentication_off():
    hint = _locale("en_US")["LHM_WEB_SERVER_HINT"].lower()
    assert "authentication off" not in hint and "leave its authentication" not in hint
    assert "firewall" in hint and "user name and password" in hint
    assert "re-enter it in lhm after each lhm restart" in hint   # review L5: the 0.9.6 re-hash


@pytest.mark.parametrize("code", sorted(constants.I18nStrings.LANGUAGE_MAP))
def test_every_locale_warns_about_re_entering_the_password_after_an_lhm_restart(code):
    data = _locale(code)
    assert "0.9.6" in data["LHM_WEB_SERVER_HINT"].rsplit("127.0.0.1", 1)[1]   # in the closing part
    assert data["LHM_PASSWORD_UNREADABLE"]
