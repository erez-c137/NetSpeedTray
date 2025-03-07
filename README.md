# Network Speed Tray Monitor

A lightweight system tray application that monitors and displays real-time network speeds with customizable features.

## Screenshots

<div align="center">
  <img src="screenshots/main.png" alt="Main Interface"/><br/>
  <p><em>Main interface in system tray</em></p>
</div>

<div align="center">
  <img src="screenshots/settings_1.0.4.png" alt="Settings"/><br/>
  <p><em>Settings dialog with customization options</em></p>
</div>

<div align="center">
  <img src="screenshots/graph.png" alt="Graph View"/><br/>
  <p><em>Optional speed history graph</em></p>
</div>

<div align="center">
  <img src="screenshots/main_graph.png" alt="Full Graph View"/><br/>
  <p><em>Full graph view</em></p>
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

I built this tool because, let’s be honest — Windows **should have had this feature** from the start. Instead of waiting for Microsoft, I made a lightweight, seamless solution that **blends perfectly with Windows 11**.

If you enjoy this tool and want to **support its development**, consider **buying me a coffee!** ☕

### **Why Donate?**

💻 **Helps me improve the app** (bug fixes, optimizations, new features).  
🔧 **Keeps it 100% free & bloat-free** (no ads, no nonsense).  
🤖 **Funds AI tools & development costs** (because AI helps accelerate development, but isn’t free 😅).

### **🎯 Goal: $100**

I’m aiming to hit **$100** to upgrade my **dev tools & AI setup**. If just **20 people** buy me a coffee, we’ll get there!

👉 [![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support%20Me-yellow?style=flat&logo=buy-me-a-coffee)](https://buymeacoffee.com/erez.c137)

Even if you can’t donate, **sharing this tool** helps a ton. Thanks for your support! ❤️

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
  - Right-click to access settings and options
  - Quick access to graph view toggle
  - Exit option available
- **Speed Display**:
  - Real-time updates based on configured interval
  - Color coding indicates speed thresholds (customizable)
  - Upload (↑) and Download (↓) speeds shown separately
- **Settings**:
  - Customize update frequency
  - Set color thresholds for speed indicators
  - Configure graph display options
  - Enable/disable auto-start with Windows
- **Persistence**:
  - All settings and position preferences are automatically saved
  - Configuration stored in AppData folder (Run->%AppData%\NetSpeedTray\)

### Speed Color Coding

When enabled, the speed display uses color coding based on thresholds:

- **Green** (> 5 Mbps): High speed connection
- **Orange** (1-5 Mbps): Medium speed connection
- **White** (< 1 Mbps): Low speed connection

These thresholds and colors can be customized in the settings dialog:

1. Right-click the widget
2. Select "Settings"
3. Under "Speed Color Coding", adjust the threshold values

## Building from Source

### Prerequisites

- Windows OS
- Python 3.11+
- Inno Setup (for installer)

### Setup

```bash
# Clone repository
git clone https://github.com/erez-c137/NetSpeedTray.git
cd NetSpeedTray

# Install requirements
pip install -r requirements.txt

# Build all distributions
build.bat
```

## Contributing

Contributions are welcome! Please feel free to submit pull requests.

## License

[GPL-3.0 License](LICENSE)
