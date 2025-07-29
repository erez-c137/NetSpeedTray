# NetSpeedTray

A lightweight, highly customizable system tray application that monitors and displays real-time network speeds for Windows.

## Screenshots

<div align="center">
  <img src="screenshots/main_new_105b.png" alt="Main Interface" width="200"/><br/>
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

Let’s be real: Windows should have had this feature built-in, but here we are! Instead of waiting for Microsoft to notice, I brewed up a lightweight, bloat-free solution that fits right in with Windows 11.

If you enjoy NetSpeedTray and it makes your workflow a little better, please consider buying me a coffee. Your support helps me dedicate more time to improving the app and keeping it 100% free and open-source.

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support%20Me-yellow?style=flat&logo=buy-me-a-coffee)](https://buymeacoffee.com/erez.c137)

---

## Installation

### Option 1: Windows Installer (Recommended)

1.  Download the `NetSpeedTray-x.x.x-Setup.exe` file from the latest release.
2.  Run the installer and follow the on-screen instructions.

### Option 2: Portable Version

1.  Download the `NetSpeedTray.exe` file from the latest release.
2.  Place it anywhere on your computer and run it. No installation is needed.

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
