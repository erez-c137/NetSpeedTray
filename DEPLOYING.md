# Deploying NetSpeedTray

For admins and power users installing NetSpeedTray on more than one PC. Everyday users want the
[Install section of the README](README.md#install) instead.

## What you can install

| Package | What it is |
|---|---|
| `NetSpeedTray-<version>-x64-Setup.exe` | Inno Setup installer. **Per-machine**: installs to `C:\Program Files\NetSpeedTray` by default and needs admin. 64-bit Windows only; on Windows 11 on ARM it installs the x64 build, which runs under Windows' x64 emulation. |
| `NetSpeedTray-Portable-<version>.zip` | The same app, no installer, no admin. Updates itself in its folder when it can; otherwise it saves the new version to Downloads for you to copy over. |
| WinGet `erez-c137.NetSpeedTray` | Installs the Setup.exe above, so it asks for admin too. |
| Microsoft Store `XP9MHWQZJPLQM8` | A Win32 listing that runs the same Setup.exe silently. |

There is no per-user install, MSI or MSIX. Each release also has a `checksums.txt` with SHA-256 hashes.

**Per-machine binaries, per-user data.** The program files are shared; each Windows user gets their own
settings, history and startup entry (see [Where things live](#where-things-live)), and runs their own
copy: one instance per user, so several users on one machine (including Remote Desktop sessions) each
run their own.

## Silent install

```
NetSpeedTray-<version>-x64-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

These are the switches the in-app updater and the Microsoft Store submission use. The in-app updater
also adds `/LOG="<file>"`. Other standard Inno Setup switches (for example `/DIR=`) work too; see
[Inno Setup's command-line reference](https://jrsoftware.org/ishelp/index.php?topic=setupcmdline).

- **Upgrades install into the existing folder**, wherever it is. If that folder was on a local drive that
  no longer exists, Setup falls back to the default folder and logs it; a network location is kept.
- **A running NetSpeedTray is closed first.** Setup asks the copies running in its own session to quit
  cleanly and waits up to 30 seconds, then ends any that remain - including other signed-in users'
  copies, which it cannot ask - with `taskkill /F /IM NetSpeedTray.exe`. Versions up to 2.1.7
  can't receive the request, so upgrading from them always force-closes the app once. Under
  `/SUPPRESSMSGBOXES` the "close the app?" prompt takes its default answer (close it).
- **After a silent install** Setup starts the app through the signed-in user's Explorer, so it runs as
  that user, not elevated. It does **not** start the app when Setup runs as SYSTEM or in session 0 (for
  example from Intune or Configuration Manager), when Setup runs as a different account from the
  signed-in user, or when no Explorer shell is running. On a fresh deployment the app then starts the
  first time the user opens it from the Start menu; after that, its own startup entry (on by default)
  starts it at sign-in.

## Detecting an install

- **Uninstall key:** `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{D3A32B89-C533-4F2C-9F87-23B2395B5B89}_is1`
  (Inno Setup's standard key for this AppId; `DisplayVersion` holds the version, e.g. `2.2.0`).
- **File version** of `NetSpeedTray.exe`: a final release is `X.Y.Z.10000` (2.2.0 is `2.2.0.10000`); a
  prerelease has a lower fourth number (`2.2.0-rc.2` is `2.2.0.2002`), so a final always ranks above its
  betas and release candidates.
- Exit codes are Inno Setup's standard ones (0 = success).

## Uninstall

```
"C:\Program Files\NetSpeedTray\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES
```

- A **silent** uninstall **keeps** user data. Add `/PURGE=true` to delete `%APPDATA%\NetSpeedTray` as
  well; that removes the data of the account running the uninstaller only.
- An **interactive** uninstall asks whether to delete settings, history and logs.
- Uninstall removes the `NetSpeedTray` value from the `HKCU\...\Run` key of the account running it, and
  the desktop shortcut.

## Where things live

| What | Where |
|---|---|
| Program | `C:\Program Files\NetSpeedTray\` (or the folder chosen at first install) |
| Settings | `%APPDATA%\NetSpeedTray\NetSpeedTray_Config.json` (Roaming) |
| History | `%APPDATA%\NetSpeedTray\speed_history.db` (SQLite, with `-wal` / `-shm` while running) |
| Upgrade backups | `%APPDATA%\NetSpeedTray\speed_history.db.bak.v7_<date>_<time>` (from the 2.2 upgrade) and `NetSpeedTray_Config.json.bak.v<settings format>` (for example `.bak.v1.1`, kept when an upgrade changes the settings format) |
| Logs | `%APPDATA%\NetSpeedTray\NetSpeedTray_Log.log` (rotated), `NetSpeedTray_Crash.log`, and the updater's installer log in `%APPDATA%\NetSpeedTray\logs\` |
| Portable update staging | `%LOCALAPPDATA%\NetSpeedTray\updates` |

The portable build uses the same `%APPDATA%\NetSpeedTray` folder.

**Roaming profiles and FSLogix:** the history database lives in Roaming AppData and grows with the
**Keep history for** setting (Settings → Advanced) and the number of adapters. As one data point, a real
2.1 history of 80 MB converted to 18 MB in 2.2. The 2.2 upgrade's backup of the old database sits beside it and also counts; it ages out with the same **Keep history for** setting, but never before 30 days. If roaming size matters, set a shorter retention.

Read the database only read-only while the app runs. Schema, queries and how to restore a backup:
[DATABASE.md](DATABASE.md).

## Start with Windows

The app writes its own startup entry: the value `NetSpeedTray` under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, pointing at the running exe. It's on by default
(**Start with Windows** in Settings → General) and is rewritten if the exe moves. Setup never creates it.
Turning the app off in Task Manager's Startup tab also stops it.

## Updates

- **Checking:** when the app starts, at most once every 24 hours, it asks GitHub's API
  (`api.github.com`) for the latest release. Prereleases are never offered.
- **Turning it off for everyone:** set the machine-wide policy value
  `HKLM\SOFTWARE\Policies\NetSpeedTray DisableUpdateCheck` (a DWORD; any nonzero value turns it on,
  and a value of another type is ignored with a warning in the log). The app then never checks on its
  own, refuses a manual check too, hides **Check for Updates** from its right-click menu, and shows the
  Settings toggle as managed by your organization (the user's own setting is kept). The same value
  under `HKCU\SOFTWARE\Policies\NetSpeedTray` is honored as well; the two are OR-ed, so either one
  turns checks off.

  ```
  reg add "HKLM\SOFTWARE\Policies\NetSpeedTray" /v DisableUpdateCheck /t REG_DWORD /d 1 /f
  ```
- **Turning it off per user:** **Check for updates on startup** in Settings → General, or
  `"check_for_updates": false` in `NetSpeedTray_Config.json` while the app is closed. Users can still
  choose **Check for Updates** from the right-click menu.
- **Installing:** the user clicks Download; the app checks the download's Authenticode signature and the
  SignPath publisher and refuses anything that fails, then runs Setup silently. For a Setup.exe install
  that means a UAC prompt, so a standard user needs admin credentials to update.
- **Portable updates are checked differently:** the portable zip is many files, not one signed
  installer. The app checks the zip's SHA-256 against the release's `checksums.txt` (both fetched over
  HTTPS from the GitHub release), then checks the Authenticode signature and the SignPath publisher of
  the extracted `NetSpeedTray.exe` before anything is staged; if either check fails, nothing is staged,
  the current version keeps running and the release page opens. The rest of the bundle (`_internal\`)
  is vouched for by the checksum only - exactly as trustworthy as downloading the zip from the release
  page yourself.
- **Pinning a version:** turn the check off as above and deploy the version you want.

## Network activity

| What | Destination | When | Default | Turn off |
|---|---|---|---|---|
| Update check | HTTPS to `api.github.com` | At start, at most once per 24 h, and from the right-click menu | On | Settings → General → Check for updates on startup, or the policy above |
| Update download | HTTPS to `github.com` release assets (redirects to GitHub's download CDN), plus a certificate revocation check to the certificate authority | Only when a user clicks Download | - | The policy or the toggle above (no check, no offer) |
| Latency check | ICMP echo to the default gateway | Four every 3 s (about 1.3/s), 12-byte payload `netspeedtray` | On | Settings → Network Interfaces → Connection latency |
| Internet latency | ICMP echo to `1.1.1.1` (or the host you set) | Same rate, in addition | **Off** | Same card |
| DNS lookup | Your normal DNS server | Once per burst (every 3 s), only if the internet host is a *name* | Off | Use an IP address |
| Check connection | 20 pings, 200 ms apart, to the gateway (and the internet host if enabled or ticked) | Only when a user clicks it, even while the latency check is off | - | - |
| Route lookup | UDP `connect()` to `8.8.8.8:80` / `[2001:4860:4860::8888]:80` - **no packet is sent**; it only asks Windows which adapter has the default route | When finding the main connection | On | - |
| LibreHardwareMonitor | HTTP to `127.0.0.1:8085/data.json` (this PC only) | While temperature or power readings are on, or the Monitor window is open; retried every 30 s while LHM doesn't answer | - | Stop LHM's web server (or don't run LHM) |
| Metrics endpoint | **Listens** on `127.0.0.1` and `[::1]`, port 9184 | While turned on | **Off** | Settings → Advanced → Integrations |

The latency check pings the first IPv4 default gateway Windows lists. While a VPN is connected that can
be the VPN's own gateway, so the latency pings can go through the tunnel to the VPN provider. The app
needs no inbound port unless you turn on the metrics endpoint, and that one listens on this PC only. Full detail: [privacy.md](privacy.md).

<a id="metrics-endpoint"></a>
## Metrics endpoint (Prometheus / Grafana)

Off by default. Turn it on in **Settings → Advanced → Integrations → Local metrics endpoint**. It serves
[Prometheus text format](https://prometheus.io/docs/instrumenting/exposition_formats/) at
`http://127.0.0.1:9184/metrics` (the port can be changed).

- It listens on `127.0.0.1` and `[::1]` only, never on all interfaces, and refuses any connection that
  isn't from this PC and any request whose Host header isn't a loopback name, so a web page can't read it.
- It answers `GET` and `HEAD` on `/metrics` only. No authentication and no TLS (it never leaves the machine). It's rate
  limited (5 requests a second, bursts of 10, at most 4 at once).
- It carries download/upload rates (total and per monitored adapter), data usage, the data cap if set,
  latency and loss, the CPU/GPU/RAM/VRAM you already monitor, and the app version, as `netspeedtray_*`
  metrics. Labels include your **adapter names**, which any program on the PC can read while it's on.

```yaml
# prometheus.yml - Prometheus running on the same PC
scrape_configs:
  - job_name: netspeedtray
    scrape_interval: 15s
    static_configs:
      - targets: ["127.0.0.1:9184"]
```

A Prometheus, Grafana or Home Assistant **on another machine** can't reach it, by design. Run a small
agent on the PC (Prometheus in agent mode, or Grafana Alloy) that scrapes it and `remote_write`s to your
server.

<a id="librehardwaremonitor"></a>
## LibreHardwareMonitor

NetSpeedTray reads LHM (v0.9.5 and newer) from its web server at `http://127.0.0.1:8085/data.json`, with
no authentication. LHM's web server is LHM's, not NetSpeedTray's, and LHM must run as admin.

- **LHM 0.9.5 and 0.9.6** can't limit the web server to this PC: their Interface list offers the PC's
  network addresses and `0.0.0.0`, and both `0.0.0.0` and the default listen on all interfaces. Picking
  a network address makes LHM answer `400` to NetSpeedTray's `127.0.0.1` request.
- **The next LHM release after 0.9.6** adds `127.0.0.1` to that list ([LHM PR #2579](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/pull/2579),
  merged 2026-10-15). Choose it, and the web server is reachable from this PC only.
- Until then, don't open port 8085 for inbound traffic, and block it explicitly (block rules win over
  allow rules):

```powershell
# Run as administrator
New-NetFirewallRule -DisplayName "Block LibreHardwareMonitor web server (inbound)" `
  -Direction Inbound -Protocol TCP -LocalPort 8085 -Action Block
```


## Signing

Setup.exe, the portable build's `NetSpeedTray.exe` and the installed `NetSpeedTray.exe` are signed by the
SignPath Foundation. `unins000.exe` (Inno Setup's uninstaller) and the bundled third-party files in
`_internal\` are not signed by SignPath; the third-party files keep their own publishers' signatures where
they have one. Smart App Control: a clean install runs; upgrades are still being confirmed in
[issue #333](https://github.com/erez-c137/NetSpeedTray/issues/333).

## Rollback

Installing an older version over 2.2 works, but 2.2 converts the history database. Going back to 2.1.x
needs the backup restored first to keep recording: [DATABASE.md, Restoring a backup](DATABASE.md#restoring-a-backup).
