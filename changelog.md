CHANGELOG.md

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2024-02-21

### Added

- Network interface selection feature:
  - Interface monitoring modes (All/Selected/Exclude)
  - Dynamic interface detection and status
  - Per-interface bandwidth monitoring
  - Interface selection persistence
- Enhanced error logging system:
  - Detailed error reporting with system information
  - Error log rotation (10MB limit, 3 files)
  - Error log export functionality in settings
  - Comprehensive system diagnostics in logs

### Enhanced

- Settings dialog improvements:
  - Streamlined layout and organization
  - Smart collapsible sections
  - Dynamic position adjustment
  - Interface selection controls
- Default speed thresholds adjusted:
  - High speed threshold: 5 Mbps (was 1 Mbps)
  - Low speed threshold: 1 Mbps (was 0.1 Mbps)

### Fixed

- Settings dialog now properly shows application icon in title bar
- Fixed application being drawn over fullscreen windows
- Application now correctly hides with taskbar
- Settings dialog behavior:
  - Proper expand/collapse animation
  - Maintains screen position when expanding
  - Consistent spacing and alignment
  - Better visual hierarchy

## [1.0.0] - 2024-02-21

### Added

- Initial release
- Real-time network speed monitoring in system tray
- Upload and download speed display
- Customizable color coding based on speed thresholds
- Optional speed history graph
- Drag-and-drop positioning
- Settings dialog with:
  - Update rate configuration
  - Color coding options
  - Graph settings
  - Auto-start with Windows
- Portable and installer versions
- Windows taskbar integration
- System tray context menu
- Configuration file saving/loading
- Error logging system

### Known Issues

- Two processes appearing in Task Manager
- Startup delay when loading application
- Application doesn't reappear with hides with taskbar set to auto hide

[1.0.1]: https://github.com/erez-c137/NetSpeedTray/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/erez-c137/NetSpeedTray/releases/tag/v1.0.0
