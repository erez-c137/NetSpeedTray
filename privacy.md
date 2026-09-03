# Privacy Policy for NetSpeedTray

Last Updated: August 24, 2026

This privacy policy outlines how NetSpeedTray handles information. As an open-source project created by a single developer, transparency and user privacy are top priorities.

### Data Collection and Usage

**NetSpeedTray does not collect, store, or transmit any personal data or network traffic content.**

The application is designed to be completely self-contained on your computer. It does not include any analytics, telemetry, advertising, or crash-reporting services.

- **Network Monitoring:** The application monitors your network adapters locally to calculate your current upload and download speeds. It measures the *volume* of traffic (bytes per second) - it never inspects the *content* of your traffic. This information is **only displayed to you** on the widget and is **never sent to any server**.
- **Network Identity (optional):** If you enable the network-identity indicator, NetSpeedTray can show the Wi-Fi **band** (2.4 GHz / 5 GHz) and, optionally, the **network name (SSID)** on the widget. The band is read from your wireless adapter locally and needs no permission. The SSID is different: **Windows only reveals the network name to apps that have Location access**, so enabling the "Network name" option requires you to turn on Windows Location. This is a **Windows privacy gate, not GPS or geolocation** - NetSpeedTray does not use your position, and reads the network name **locally only to display it on the widget**. Like everything else here, the band and SSID are **never stored to the history database and never transmitted**.
- **Hardware Monitoring:** The application reads CPU, GPU, and RAM utilization figures from your own hardware locally, and **records this utilization history to the local history database by default** — even if the hardware widgets are turned off — so the Monitor window's hardware graphs have data when you open them. (A setting to turn this recording off is planned for an upcoming release.) Temperature and power figures are read when you enable the corresponding monitoring features, and while the Monitor window is open (its hardware graphs read them live). All of it stays on your computer: displayed to you, stored only in the local database described below, and never transmitted.
- **App Activity (optional):** The App Activity window shows which running applications are using the network, using process information from your own computer. This data is the most sensitive the app touches (it can include process names and remote addresses), so it is computed and displayed **live only** - it is **never written to disk, never stored in the history database, and never transmitted**. It also never leaves your machine in a Support Bundle (see below).
- **Configuration File:** Your settings are saved locally on your computer in a `NetSpeedTray_Config.json` file located in your `%appdata%\NetSpeedTray` folder. It contains only your preferences (such as colors, position, and which features are enabled) - no personal data. This file is never transmitted.
- **History Database:** A local SQLite database (`speed_history.db`) is created in the same folder to store your network speed history and your hardware utilization history (recorded by default, as described above). This data remains on your computer and is never transmitted.

### Support Bundles

NetSpeedTray can generate a "Support Bundle" - a single `.zip` file you can **choose** to attach to a GitHub issue to help diagnose a bug. This file is created locally and is only ever shared if **you** decide to send it.

- **What it contains:** your application logs, your settings file (`config.json`), and basic system info (app version, OS, and monitor layout).
- **Logs are scrubbed:** before being added to the bundle, log files are passed through a redaction step that replaces file paths, IP addresses (IPv4 and IPv6), MAC addresses, network-interface GUIDs, and your computer's hostname with placeholders. Network-adapter friendly names (which you may have renamed to something personal) and display device names are replaced, on a best-effort basis, with a **stable placeholder** (e.g. `NIC-1a2b3c4d`) — a one-way hash, so repeated log lines still correlate for debugging but never contain the name itself.
- **What is deliberately left out:** App Activity per-process / per-connection data, your hostname, MAC addresses, and full GPU model strings. Network-interface friendly names never appear verbatim: they are redacted from the bundled settings file and replaced with the stable placeholders described above in logs.
- The bundle includes a `MANIFEST.txt` listing exactly what is and isn't inside, so you can review it before sharing. Nothing is uploaded automatically.

### Update Checking

To check for new versions, NetSpeedTray may periodically contact the GitHub.com API. This is a standard and secure process.

- **Information Sent:** A request is sent to the GitHub API for the NetSpeedTray repository to check for the latest release. As with any internet connection, this request includes basic, non-identifiable information such as your IP address. No personal or user-specific information is added by NetSpeedTray.
- **Information Received:** The response contains public release information - the latest version number, the release notes, and the download links for that release - which is used to tell you an update is available and show you what changed. Choosing to download an update takes you to (or fetches from) the official GitHub Releases page; NetSpeedTray never installs anything without your action.
- **No Personal Data:** No personal or user-specific information is sent during the update check.

### Open Source Transparency

NetSpeedTray is fully open-source. You are encouraged to review the code on GitHub to verify all claims made in this policy.

### Contact

If you have any questions about this privacy policy, please open an issue on the [GitHub repository](https://github.com/erez-c137/NetSpeedTray/issues).
