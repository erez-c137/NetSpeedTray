# Network Speed Tray Monitor

A lightweight system tray application that monitors and displays real-time network speeds with customizable features.

## Screenshots

<div align="center">
  <img src="screenshots/main.png" alt="Main Interface"/><br/>
  <p><em>Main interface in system tray</em></p>
</div>

<div align="center">
  <img src="screenshots/settings_1.0.3.png" alt="Settings"/><br/>
  <p><em>Settings dialog with customization options</em></p>
</div>

<div align="center">
  <img src="screenshots/graph.png" alt="Graph View"/><br/>
  <p><em>Optional speed history graph</em></p>
</div>

## Features

- 💻 System tray integration
- 📊 Real-time upload/download speed monitoring
- 🎨 Customizable color coding based on speed thresholds
- 📈 Optional speed history graph
- 🚀 Drag-and-drop positioning
- ⚙️ Configurable update rates
- 🔄 Auto-start with Windows option

## Download

- [Latest Release](https://github.com/erez-c137/NetSpeedTray/releases/latest)
  - **NetSpeedTray-Portable.zip** - Portable version, just extract and run
  - **NetSpeedTray-Setup.exe** - Windows installer

## ☕ Support My Work

Let’s be real: Windows should have had this feature built-in, but here we are! Instead of waiting for Microsoft to notice, I brewed up a lightweight, bloat-free solution that fits right in with Windows 11.

If you enjoy NetSpeedTray, if it saves you time, frustration, or even just a few brain cells, why not buy me a coffee? ☕ (I promise to spend it on code, not caffeine-induced bug creation.)

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support%20Me-yellow?style=flat&logo=buy-me-a-coffee)](https://buymeacoffee.com/erez.c137)

### Why Donate?

- 💻 Keeps the app improving (bug fixes, new features, less rage at Windows)
- 🔧 Guarantees it stays 100% free & bloat-free (no ads, no crypto miners, no nonsense)
- 🤖 Funds AI tools & dev costs (because even robots need to eat... electricity)

**Goal:** $100 for better dev tools & AI magic. If just 20 people chip in for a coffee, we’re there!

If you can’t donate, sharing the project or leaving feedback is just as awesome. Thanks for helping keep Windows a little more sane! ❤️

---

## Installation

### Option 1: Portable Version (Recommended)

1. Download `NetSpeedTray-Portable.zip`
2. Extract anywhere
3. Run `NetSpeedTray.exe`

### Option 2: Windows Installer

1. Download `NetSpeedTray-Setup.exe`
2. Run the installer
3. Follow the installation wizard

## Usage

- **Widget Positioning**:
  - Left-click and drag horizontally to position anywhere on the taskbar
  - Position is automatically saved and restored on restart
  - Widget stays aligned with taskbar even after resolution changes
- **Context Menu**:
  - Right-click to access settings and exit
  - Double click on the widget to access the full graph view
- **Speed Display**:
  - Real-time updates based on configured interval
  - Color coding indicates speed thresholds (customizable)
  - Upload (↑) and Download (↓) speeds shown separately
- **Settings**:
  - Show mini-graph on the taskbar
  - Set color thresholds for speed indicators
  - Configure graph display options
  - Enable/disable auto-start with Windows
- **Persistence**:
  - All settings and position preferences are automatically saved
  - Configuration stored in AppData folder (Run->%AppData%\NetSpeedTray)

## Building from Source

### Prerequisites

### Project Code Structure

```
src/
└── netspeedtray/
    ├── constants/                  # Constants and internationalization
    ├── core/                       # Core application components
    ├── tests/                      # Test suites
    ├── utils/                      # Utility functions
    ├── views/                      # User interface components
    └── monitor.py                  # Main monitoring module
```

### Build & Run from Source

```bash
# Clone repository
git clone https://github.com/erez-c137/NetSpeedTray.git
cd NetSpeedTray

# (Recommended) Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# Install requirements
pip install -r [requirements.txt]

# Run the app (development mode)
python [monitor.py]

# (Optional) Build Windows executables
[build.bat]

## Contributing

Contributions are welcome! Please feel free to submit pull requests.

## License

This project is licensed under the [GNU GPL v3.0](LICENSE).
```
