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
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Since 2.2.2 its exact format is

```
"C:\Program Files\NetSpeedTray\NetSpeedTray.exe" --autostart
```

(the running exe, quoted, then `--autostart`, which tells a second launch at sign-in apart from a user
opening the app). It's on by default (**Start with Windows** in Settings → General). Setup never
creates it. Turning the app off in Task Manager's Startup tab also stops it, and the app never undoes
that on its own.

- At each launch the app rewrites the value only when it names **this** exe in another format (an
  older build's `"...\NetSpeedTray.exe"` without `--autostart`, other quoting), or names an exe that no
  longer exists (the app moved). So a value IT pushed in the old format is rewritten once, at the
  next launch, to the format above. A Group Policy Preferences **Replace** item should use the new
  format, or it and the app keep rewriting each other.
- A value that names a **different** NetSpeedTray exe that still exists (an installed copy while a
  portable one runs, or the other way round) is left alone and logged. Only turning **Start with
  Windows** off and on in that copy's Settings takes the entry over (also logged).
- With **Start with Windows** off, the app removes a value that names its own exe, in either format.

## Updates

- **Checking:** at most once every 24 hours, the app asks GitHub's API (`api.github.com`) for the
  latest release. It looks for a due check 5 s after it starts, every 3 hours while it runs and 60 s
  after a wake from sleep (a PC that sleeps instead of rebooting can keep one instance for weeks);
  GitHub is contacted only when the last successful check is more than 24 hours old, so a failed
  check (offline, say) is retried at the next of those. Prereleases are never offered.
- **Offering:** an automatic check offers a new version with a non-modal notice that opens the update
  dialog only when clicked - never a dialog the user did not ask for. The notice waits (retried every
  60 s) while a fullscreen app or a modal dialog is up, the session is locked or in a screen saver, or
  there has been no keyboard/mouse input for 2 minutes. **Not Now** in the dialog holds back automatic
  offers of that version for 3 days, across restarts (`update_snooze` in the config); a newer release
  is offered at once. Only the right-click **Check for Updates** opens the dialog directly.
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
- **Turning it off per user:** **Check for updates automatically** in Settings → General, or
  `"check_for_updates": false` in `NetSpeedTray_Config.json` while the app is closed. Users can still
  choose **Check for Updates** from the right-click menu.
- **Installing:** the user clicks Download; the app checks the download's Authenticode signature and the
  SignPath publisher and refuses anything that fails, then runs Setup silently. For a Setup.exe install
  that means a UAC prompt, so a standard user needs admin credentials to update.
- **Portable updates are checked differently:** the portable zip is many files, not one signed
  installer. The app checks the zip's SHA-256 against the release's `checksums.txt` (both fetched over
  HTTPS from the GitHub release), then checks the Authenticode signature and the SignPath publisher of
  the extracted `NetSpeedTray.exe`, and then every other file of the new version against a list of
  SHA-256 hashes that the build embeds inside that exe before it is signed - so the signature vouches
  for the whole bundle, `_internal\` included, not just the exe (since 2.2.1). A missing, extra or
  changed file is refused. If any check fails, nothing is staged, the current version keeps running
  and the release page opens.
- **Pinning a version:** turn the check off as above and deploy the version you want.

## First-run tip, "what's new" and the one-time support card

Since 2.2.2 the app shows, at most once each, a small card beside the widget:

- **First-run tip** - on a new install's first launch, pointing at the widget: point to it for
  details, double-click for the Monitor (or the configured double-click action), right-click for the
  menu and Settings. Buttons: **Open Monitor** / **Got it**. Doing any of those three - or opening the
  Monitor or Settings from the "NetSpeedTray is running" panel - also counts as an answer, even before
  the tip has shown (it then never shows).
- **What's new** - on the first launch of a newer final release: one sentence, with **What's new**
  (opens that version's GitHub release page in the browser) and **Got it**. Prereleases never show it;
  a beta tester sees it once, on the final. Not on a downgrade, never on the same launch as the tip.

Neither takes the keyboard from the app in front (unless a screen reader is running, so it can be
read), times out or repeats. Each waits while the widget is hidden and (retried every 30 s) while a
fullscreen app or a modal dialog is up, Settings is open, another NetSpeedTray notice or the hover card
is showing, the "NetSpeedTray is running" panel (opened from Start) is open, the session is locked or
idle for 2 minutes, or the session signed in less than 2 minutes ago. A card already up steps aside for
a fullscreen app (even with **Keep visible in fullscreen**), a modal dialog, the running panel or another
notice, and comes back after. One one-time moment per session: a session that already showed the
support card or the temperature setup (below) leaves the tip / "what's new" for the next launch. Nothing is sent anywhere; the release page opens only if the user clicks. The
state is two settings in `NetSpeedTray_Config.json`: `first_run_tip_shown` and `last_seen_version`.

**The one-time "rate / star / support" card (2.2.2):** about a month (30 days) after a user first runs
2.2.2 or newer - counted from that run, never from older history - the next time they open the Monitor,
one inline card at the top of the Monitor says once: "A month with NetSpeedTray - It's free and always
will be. If it's earned its spot on your taskbar, a star helps others find it." with **Star on
GitHub**, a **Support the project** link (the app's Support dialog) and a close button. A Microsoft
Store install also gets **Rate it in the Store** (the Store only accepts ratings from people who have
the app in their Store library; Setup records a Store install when the Store runs it with `/STORE`, as
`InstalledFrom` = `Store` under `HKLM\SOFTWARE\NetSpeedTray` - active once the Store submission passes
that switch). It never appears at launch (not in the app's first 2 minutes, nor in the 2 minutes after
sign-in), over a fullscreen app, with a modal dialog or Settings open, while another NetSpeedTray
card or the running panel is showing (not a panel that is closing because its Monitor row was used),
or in a session that showed - or still owes - the first-run tip, "what's new" or the temperature setup. Any action - or about 4.5 seconds on screen while the Monitor is the active window -
ends it for good; nothing in the app changes whichever was chosen, nothing is recorded about the
choice, and nothing is sent anywhere (a link opens only on a click). The state is `first_seen_at`
(UTC) and `support_prompt_shown` in the config; Settings > Reset all keeps both. A PC clock set before
November 2026 never starts or advances the wait. The same `DisableTips` policy below turns it off.

- **Turning all three off for everyone:** set the policy value
  `HKLM\SOFTWARE\Policies\NetSpeedTray DisableTips` (a DWORD; any nonzero value turns them off; the same
  value under `HKCU\SOFTWARE\Policies\NetSpeedTray` is honored too, OR-ed, like `DisableUpdateCheck`).
  The app then never shows any of the three cards, and still records the version, so lifting the
  policy later never brings back a stale tip or "what's new". (The rate / star / support card is not
  used up by the policy: lifted, it can still show once, on a later Monitor open.) A Store or silent
  install is not "managed" by itself: set the policy.

  ```
  reg add "HKLM\SOFTWARE\Policies\NetSpeedTray" /v DisableTips /t REG_DWORD /d 1 /f
  ```
- **Per user, without a policy:** pre-seed the config with `"first_run_ever": false,
  "first_run_v2_seen": true, "first_run_tip_shown": true` and `"last_seen_version": "<the version you
  deploy>"` before the first launch, plus `"support_prompt_shown": true` for the rate / star / support card.
- **Not covered by `DisableTips`:** the Monitor's one-click offer to add the internet check ("Router"
  only -> "Also check the internet?", 2.2.2) and the temperature setup dialog. The temperature setup
  opens on its own at most once ever - when temperatures or power are turned on but no sensor reads -
  behind the same gates as the cards above, and afterwards only from Settings > Hardware; pre-seed
  `"temp_onboarding_dismissed": true` to skip it.

**Launching NetSpeedTray while it runs (2.2.2):** the widget comes back; a launch the user made - a
Start menu, taskbar or desktop shortcut, or an exe double-clicked in Explorer - also opens the small
"NetSpeedTray is running" panel. An autostart (the Run key, which since 2.2.2 carries `--autostart`, or
a Startup-folder shortcut), the updater's relaunch, and any other start (Task Scheduler, a script) never
open it.

## Network activity

| What | Destination | When | Default | Turn off |
|---|---|---|---|---|
| Update check | HTTPS to `api.github.com` | At most once per 24 h (looked for at start, every 3 h and after a wake; a failed check is retried), and from the right-click menu | On | Settings → General → Check for updates automatically, or the policy above |
| Update download | HTTPS to `github.com` release assets (redirects to GitHub's download CDN), plus a certificate revocation check to the certificate authority | Only when a user clicks Download | - | The policy or the toggle above (no check, no offer) |
| Latency check | ICMP echo to the default gateway (a connected physical adapter's, when there are several) | Four every 3 s (about 1.3/s), 12-byte payload `netspeedtray` | On | Settings → Network → Connection latency |
| Internet latency | ICMP echo to `1.1.1.1` (or the host you set) | Same rate, in addition | **Off** | Same card |
| DNS lookup | Your normal DNS server | Once per burst (every 3 s), only if the internet host is a *name* | Off | Use an IP address |
| Check connection | 20 pings, 200 ms apart, to the gateway (and the internet host if enabled or ticked) | Only when a user clicks it, even while the latency check is off | - | - |
| Route lookup | UDP `connect()` to `8.8.8.8:80` / `[2001:4860:4860::8888]:80` - **no packet is sent**; it only asks Windows which adapter has the default route | When finding the main connection | On | - |
| LibreHardwareMonitor | HTTP to `127.0.0.1:8085/data.json` (this PC only) | While temperature or power readings are on, or the Monitor window is open; retried every 30 s while LHM doesn't answer | - | Stop LHM's web server (or don't run LHM) |
| Metrics endpoint | **Listens** on `127.0.0.1` and `[::1]`, port 9184 | While turned on | **Off** | Settings → Advanced → Integrations |

The latency check pings the default gateway of a connected physical adapter (Ethernet or Wi-Fi) when
the PC has more than one default route, so a full-tunnel VPN's own default route does not turn the
"router" hop into one across the internet. With no physical adapter's default route it uses the route
Windows uses (or, if the route table cannot be read, the first IPv4 default gateway Windows lists);
while a VPN is connected that can be the VPN's own gateway, so the latency pings can go through the
tunnel to the VPN provider. The app needs no inbound port unless you turn on the metrics endpoint, and
that one listens on this PC only. Full detail: [privacy.md](privacy.md).

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
they have one. Since 2.2.1 the signed `NetSpeedTray.exe` carries a SHA-256 of every bundled file (an
`RT_RCDATA` resource named `NST_MANIFEST`, covered by its signature), which the portable updater
checks. Smart App Control: a clean install runs; upgrades are still being confirmed in
[issue #333](https://github.com/erez-c137/NetSpeedTray/issues/333).

**Manifest format contract.** The manifest is format v1 (`{"v": 1, ...}`), and every shipped updater
since 2.2.1 accepts only v1 and refuses an exe without it. Every future build must therefore keep
embedding a v1-readable `NST_MANIFEST`; a new format goes in a second resource beside it (e.g.
`NST_MANIFEST_V2`), never in place of it. A build-config test pins the embed step to v1.

## Rollback

Installing an older version over 2.2 works, but 2.2 converts the history database. Going back to 2.1.x
needs the backup restored first to keep recording: [DATABASE.md, Restoring a backup](DATABASE.md#restoring-a-backup).
