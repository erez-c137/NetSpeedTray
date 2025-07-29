# NetSpeedTray

A lightweight, highly customizable system tray application that monitors and displays real-time network speeds for Windows.

## Screenshots

<div align="center">
  <img src="screenshots/main_new_105b.png" alt="Main Interface"/><br/>
  <p><em>The widget, docked to the taskbar.</em></p>
</div>
<br>
<div align="center">
  <img src="screenshots/settings_1.0.5b2.png" alt="Settings Dialog"/><br/>
  <p><em>The modern settings dialog with extensive customization options.</em></p>
</div>
<br>
<div align="center">
  <img src="screenshots/main_graph_1.0.5b1.png" alt="Graph View"/><br/>
  <p><em>The detailed network history graph.</em></p>
</div>

## Features

- 💻 **Lightweight & Efficient:** Sits quietly in your system tray without hogging resources.
- 📊 **Real-time Monitoring:** Displays upload and download speeds with a clean, modern look.
- 🚀 **Intelligent Positioning:**
  - **Free Move Mode:** Unlock the widget and place it anywhere on your screen.
  - **Adaptive Tray Mode:** Remembers your preferred spacing from the system tray and automatically shifts to avoid being covered by new icons.
- ✨ **Total Customization:**
  - **Mini-Graph:** An optional, real-time graph displayed directly on the widget.
  - **Color Coding:** Set custom colors and speed thresholds to see your network status at a glance.
  - **Text Control:** Fine-tune the text alignment, decimal precision, and number format.
- 📈 **Detailed History Graph:** Double-click the widget to open a full history of your network activity.
- ⚙️ **Highly Configurable:** Adjust the update rate, enable/disable auto-start, and more.

## Download

- Go to the [**Latest Release**](https://github.com/erez-c137/NetSpeedTray/releases/latest) page.
  - **`NetSpeedTray-x.x.x-Setup.exe`:** The recommended Windows installer.
  - **`NetSpeedTray.exe`:** The standalone portable version.

## ☕ Support My Work

Let’s be real: Windows should have had this feature built-in. Instead of waiting, I brewed up a lightweight, bloat-free solution that feels like it belongs.

If you enjoy NetSpeedTray and it makes your workflow a little better, please consider supporting its development. Your contribution directly helps me dedicate more time to the project and keep it a high-quality tool for everyone.

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

### Why Support NetSpeedTray?

- **🚀 Fund New Features:** Your support helps me build the features you want to see next.
- **🔧 Ensure Long-Term Maintenance:** Guarantees the app stays compatible with future Windows updates.
- **🚫 Keep it 100% Free & Ad-Free:** NetSpeedTray will always be free, without ads, tracking, or "pro" versions.
- **🤖 Fuel Development:** Contributions help cover the costs of development tools and services.

Every little bit helps and is deeply appreciated. If you can’t contribute financially, starring the repo or sharing the project is just as awesome. Thank you! ❤️```

---

## Installation

### With a Package Manager (Recommended)

The easiest way to install and receive automatic updates is by using a command-line package manager.

**Using [Winget](https://docs.microsoft.com/en-us/windows/package-manager/winget/) (built into Windows):**

```powershell
winget install erez-c137.NetSpeedTray
```

**Using [Scoop](https://scoop.sh/):**

```powershell
scoop bucket add extras
scoop install netspeedtray
```

_(Note: It may take 24-48 hours after a new release for it to be available via package managers.)_

### Manual Installation

If you prefer not to use a package manager, you can download the latest files directly from the [**Releases Page**](https://github.com/erez-c137/NetSpeedTray/releases/latest).

- **`NetSpeedTray-x.x.x-Setup.exe`:** The standard Windows installer.
- **`NetSpeedTray.exe`:** The standalone portable version (no installation needed).

## Usage

- **Widget Positioning (Two Modes):**

  - **Adaptive Tray Mode (Default):** The widget is locked to your taskbar. Left-click and drag it to set your preferred distance from the system tray icons. It will automatically move to maintain that spacing as new icons appear.
  - **Free Move Mode:** Enable this in the settings to unlock the widget. You can then drag and drop it anywhere on your screen, and its position will be saved.

- **Context Menu & Shortcuts:**

  - **Right-click** the widget to access Settings or Exit.
  - **Double-click** the widget to open the full history graph.

- **Speed Display & Customization:**

  - Upload (↑) and Download (↓) speeds are shown separately.
  - **Settings -> Color Coding:** Set speed thresholds and custom colors for when the widget _is_ visible.
  - **Settings -> Mini-Graph:** Toggle the real-time graph background on the widget.
  - **Settings -> Speed Units:** Control text alignment, decimal places, and display format (e.g., always show Mbps).
  - **Settings -> General:** Enable/disable auto-start with Windows.
  - **Dynamic Update Rate (in General settings):** An intelligent power-saving feature. When enabled, the app reduces its update frequency during network inactivity to save resources, and instantly returns to the normal update rate as soon as activity is detected.

- **Persistence:**
  - All settings and position preferences are automatically saved.
  - Configuration is stored in your AppData folder (`%appdata%\NetSpeedTray`).

## Building from Source

### Prerequisites

- [Python 3.9+](https://www.python.org/downloads/)
- [Git](https://git-scm.com/downloads/)
- (Optional) [Inno Setup 6](https://jrsoftware.org/isinfo.php) for building the installer.

### Project Structure

```
src/
└── netspeedtray/
    ├── constants/      # Constants and internationalization
    ├── core/           # Core application components
    ├── tests/          # Test suites
    ├── utils/          # Utility functions
    ├── views/          # User interface components
    └── monitor.py      # Main application entry point
```

### Build & Run Instructions

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/erez-c137/NetSpeedTray.git
    cd NetSpeedTray
    ```

2.  **Create and activate a virtual environment:**

    ```bash
    python -m venv .venv
    .\.venv\Scripts\activate
    ```

3.  **Install the required packages:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Run the application from source:**

    ```bash
    python src/monitor.py
    ```

5.  **(Optional) Build the executable and installer:**
    - Ensure [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed.
    - Run the build script located in the `build` directory:
    ```bash
    .\build\build.bat
    ```
    - The final installer and portable executable will be placed in the `dist` folder.

## Contributing

Contributions, issues, and feature requests are welcome! Please feel free to open an issue or submit a pull request.

## License

This project is licensed under the [GNU GPL v3.0](LICENSE).
