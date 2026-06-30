# Privacy Policy for NetSpeedTray

Last Updated: June 26, 2026

This privacy policy outlines how NetSpeedTray handles information. As an open-source project created by a single developer, transparency and user privacy are top priorities.

### Data Collection and Usage

**NetSpeedTray does not collect, store, or transmit any personal data or network traffic content.**

The application is designed to be completely self-contained on your computer. It does not include any analytics, telemetry, advertising, or crash-reporting services.

- **Network Monitoring:** The application monitors your network adapters locally to calculate your current upload and download speeds. It measures the *volume* of traffic (bytes per second) - it never inspects the *content* of your traffic. This information is **only displayed to you** on the widget and is **never sent to any server**.
- **Hardware Monitoring (optional):** If you enable the CPU/GPU/RAM monitoring features, the application reads utilization, memory, temperature, and power figures from your own hardware locally. This is displayed to you and, like speed history, may be stored in the local database below. It is never transmitted.
- **App Activity (optional):** The App Activity window shows which running applications are using the network, using process information from your own computer. This data is the most sensitive the app touches (it can include process names and remote addresses), so it is computed and displayed **live only** - it is **never written to disk, never stored in the history database, and never transmitted**. It also never leaves your machine in a Support Bundle (see below).
- **Configuration File:** Your settings are saved locally on your computer in a `NetSpeedTray_Config.json` file located in your `%appdata%\NetSpeedTray` folder. It contains only your preferences (such as colors, position, and which features are enabled) - no personal data. This file is never transmitted.
- **History Database:** If you use the graph or hardware-history features, a local SQLite database (`speed_history.db`) is created in the same folder to store your network speed history and, if enabled, your hardware utilization history. This data remains on your computer and is never transmitted.

### Support Bundles

NetSpeedTray can generate a "Support Bundle" - a single `.zip` file you can **choose** to attach to a GitHub issue to help diagnose a bug. This file is created locally and is only ever shared if **you** decide to send it.

- **What it contains:** your application logs, your settings file (`config.json`), and basic system info (app version, OS, and monitor layout).
- **Logs are scrubbed:** before being added to the bundle, log files are passed through a redaction step that replaces file paths, IP addresses (IPv4 and IPv6), MAC addresses, network-interface GUIDs, and your computer's hostname with placeholders.
- **What is deliberately left out:** App Activity per-process / per-connection data, your hostname, MAC addresses, network-interface friendly names, and full GPU model strings.
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
