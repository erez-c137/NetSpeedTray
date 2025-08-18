# NetSpeedTray

![GitHub release (latest by date)](https://img.shields.io/github/v/release/erez-c137/NetSpeedTray) ![GitHub all releases](https://img.shields.io/github/downloads/erez-c137/NetSpeedTray/total) [![winget install NetSpeedTray](https://img.shields.io/badge/winget-install--NetSpeedTray-blue?logo=windows&logoColor=white)](https://github.com/microsoft/winget-pkgs) ![GitHub license](https://img.shields.io/github/license/erez-c137/NetSpeedTray) ![GitHub stars](https://img.shields.io/github/stars/erez-c137/NetSpeedTray?style=social)

![NetSpeedTray Banner](./screenshots/netspeedtray-hero.jpg)

A lightweight, open-source network monitor for Windows that displays live upload/download speeds directly on the Taskbar. It's the feature Windows forgot.

---

## Installation

The easiest way to install and get automatic updates is with a package manager.

**Using [Winget](https://docs.microsoft.com/en-us/windows/package-manager/winget/) (Recommended, built into Windows):**

```powershell
winget install --id erez-c137.NetSpeedTray
```

### Manual Download
If you prefer, you can download the latest files directly from the [**Releases Page**](https://github.com/erez-c137/NetSpeedTray/releases/latest).

-   **`NetSpeedTray-x.x.x-Setup.exe`:** The standard Windows installer. Recommended for most users.
-   **`NetSpeedTray-x.x.x-Portable.zip`:** The portable version. No installation needed—just extract the folder and run `NetSpeedTray.exe`.

---

## Key Features

-   💻 **Lightweight & Efficient:** Sits quietly in your system tray without hogging resources. Features a "Dynamic Update Rate" that automatically reduces update frequency when the network is idle to conserve CPU and battery life.

-   ✨ **Native Look & Feel:** Designed to blend in perfectly with the Windows 10/11 UI. Includes smart detection for light and dark taskbar themes to ensure text is always visible.

-   🚀 **Intelligent & Stable Positioning:** A fully event-driven core provides rock-solid positioning logic. The widget responds instantly without flickering or fighting with system elements.

-   **Seamless OS Integration:** Behaves like a native OS component.
    -   Hides instantly with the **auto-hiding taskbar**.
    -   Hides instantly when you use a **fullscreen application**.

-   📈 **Smart Network Monitoring:**
    -   Automatically identifies your primary internet connection to provide the most accurate reading out of the box.
    -   Allows you to select and monitor specific network interfaces or aggregate them all.

-   🎨 **Total Visual Customization:**
    -   **Free Move Mode:** Unlock the widget and place it anywhere on your screen.
    -   **Optional Mini-Graph:** Display a real-time graph of recent activity directly on the widget, with adjustable opacity.
    -   **Color Coding:** Set custom colors and speed thresholds to see your network status at a glance.

-   ✍️ **Granular Display Control:**
    -   **Text & Font:** Fine-tune the font family, size, weight, and text alignment (left/center/right).
    -   **Units:** Choose between an automatic unit (B/s, KB/s, MB/s) or a fixed `Mbps` display.
    -   **Precision:** Control the number of decimal places and choose to always show them for a uniform look.

-   📊 **Detailed & Intelligent History Graph:** Double-click the widget to open a powerful and insightful history graph.
    -   **Always-Readable Smart Scale:** A dynamic logarithmic scale lets you see fine-grained detail in low-level traffic while still clearly showing massive download spikes.
    -   **Per-Interface Filtering:** Isolate the speed history for any specific network adapter on your system (Wi-Fi, Ethernet, VPN, etc.).
    -   **Safe & Efficient Data Management:** Adjust data retention with an accidental-deletion grace period, while the database is automatically cleaned and optimized to save space.
    -   **Easy Data Export:** Export raw data to a `.csv` file for spreadsheet analysis or save the graph itself as a high-quality `.png` image.

## Usage & Screenshots

#### The Widget

The core of NetSpeedTray. It sits on your taskbar, showing your live network speeds.

-   **Right-click** to access Settings or Exit.
-   **Double-click** to open the full history graph.
-   **Left-click and drag** to adjust its position along the taskbar.

<div align="center">
  <img src="screenshots/main_new_105b.png" alt="Main Interface" width="600"/><br/>
</div>

#### Modern Settings

A clean, modern UI to control every aspect of the widget's appearance and behavior.

<div align="center">
  <img src="screenshots/settings_1.1.1.png" alt="Settings Dialog" width="600"/><br/>
</div>

#### Detailed History Graph

Double-click the widget to see a detailed, filterable graph of your network history.

<div align="center">
  <img src="screenshots/main_graph_1.1.0.png" alt="Graph View" width="600"/><br/>
</div>

---

## ☕ Support This Project

Let’s be real: Windows should have had this feature built-in. Instead of waiting, I brewed up a lightweight, bloat-free solution that feels like it belongs.

If you enjoy NetSpeedTray and it makes your workflow a little better, please consider supporting its development. Your contribution helps me dedicate more time to the project and keep it a high-quality, ad-free tool for everyone.

<p align="center">
  <a href="https://github.com/sponsors/erez-c137">
    <img src="https://img.shields.io/badge/GitHub%20Sponsors-Support%20Me-white?style=for-the-badge&logo=githubsponsors">
  </a>
   
  <a href="https://ko-fi.com/erezc137">
    <img src="https://img.shields.io/badge/Ko--fi-Buy%20me%20a%20coffee-29abe0?style=for-the-badge&logo=ko-fi&logoColor=white">
  </a>
   
  <a href="https://buymeacoffee.com/erez.c137">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support%20Me-yellow?style=for-the-badge&logo=buy-me-a-coffee">
  </a>
</p>

Sharing and Starring the repo is also a huge and deeply appreciated way to show support! ❤️

---

## Building from Source

<details>
<summary>Click to expand</summary>

### Prerequisites

-   [Python 3.11+](https://www.python.org/downloads/)
-   [Git](https://git-scm.com/downloads/)
-   (Optional but Recommended) [Inno Setup 6](https://jrsoftware.org/isinfo.php) for building the final Windows installer.

### Development & Build Instructions

1.  **Clone the Repository:**

    ```bash
    git clone https://github.com/erez-c137/NetSpeedTray.git
    cd NetSpeedTray
    ```

2.  **Create and Activate a Virtual Environment:**

    ```powershell
    # PowerShell (Recommended on Windows)
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    ```

    ```bash
    # CMD
    python -m venv .venv
    .\.venv\Scripts\activate.bat
    ```

3.  **Install All Dependencies:**
    This project uses `pip-tools` for robust dependency management. The following command will install all necessary packages for running, testing, and building the application.

    ```bash
    pip install -r dev-requirements.txt
    ```

4.  **Run the Application from Source:**

    ```bash
    python src/monitor.py
    ```

5.  **Run the Test Suite (Optional):**

    ```bash
    pytest -v
    ```

6.  **Build the Executable and Installer (Optional):**
    -   Ensure Inno Setup 6 is installed and in your system's PATH.
    -   Run the automated build script:
    ```bash
    .\build\build.bat
    ```
    -   The final installer and portable executable will be created in the `dist` folder.

</details>

## Contributing

Contributions, issues, and feature requests are welcome! Please feel free to open an issue or submit a pull request.

> **A Note on System UI Integration:** To ensure a clean and predictable experience, NetSpeedTray is designed to integrate seamlessly with core Windows UI. Due to how Windows layers its system menus, elements like the Start Menu and Action Center will always appear on top.
>
> Instead of being awkwardly covered, the widget will gracefully hide when these menus are active and **reappear instantly** the moment they are closed. This behavior is intentional and ensures the widget feels like a polished, non-intrusive part of the operating system.

## License

This project is licensed under the [GNU GPL v3.0](LICENSE).