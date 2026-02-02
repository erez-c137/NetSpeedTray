# NetSpeedTray Graph Module Architecture

This module provides the speed history visualization and bandwidth analytics for NetSpeedTray. To ensure maintainability and high code quality, we follow a strict **Separation of Concerns** (SoC) architecture.

## Role of Components

### 1. [The Controller] window.py
Acts as the **Orchestrator**. It manages the lifecycle of the graph window, handles high-level application signals (e.g., global settings changes), and coordinates the interaction between other components.
- **Responsibility**: Window state, signal routing, data flow orchestration.
- **Rules**: NO direct Matplotlib manipulation, NO complex layout math, NO file I/O logic.

### 2. [The Layout Engine] ui.py
Encapsulates the **Structural Design**. It builds the PyQt6 widget tree, defines the visual hierarchy, and manages dynamic positioning of overlay elements.
- **Responsibility**: Widget creation, layouts, positioning, CSS application.
- **Rules**: Purely UI and positioning logic.

### 3. [The High-Performance Renderer] renderer.py
Owns the **Visual Representation**. It handles the complex Matplotlib logic required for 60-FPS rendering, gradient fills, and analytical markers.
- **Responsibility**: Matplotlib Figure/Canvas/Axes management, data-to-pixel mapping.
- **Rules**: Purely graphical; knows nothing about the application state outside the data it receives.

### 4. [The Interaction Handler] interaction.py
Manages **User Input Logic**. It captures mouse events, handles crosshairs, tooltips, and the custom SpanSelector for brush zooming.
- **Responsibility**: Hover events, click detection, coordinate interpolation.

### 5. [The Background Processing] worker.py
Handles **Data Fetching & Transformation**. To keep the UI responsive, all database queries and heavy data processing occur here on a separate thread.
- **Responsibility**: DB queries via `widget_state`, data downsampling, period calculation.

### 6. [The Domain Logic] logic.py
Contains **Pure Domain Knowledge**. This file houses the non-UI logic specific to graph management.
- **Responsibility**: Mapping slider values to time periods, calculating DB statistics, determining query ranges.
- **Rules**: Stateless functions where possible; no PyQt/GUI dependencies.

### 7. [The specialized Exporters] exporters.py (in utils)
Handles **Data Persistence**. Moving file-saving logic here ensures the UI code is strictly about the view.
- **Responsibility**: CSV generation, PNG snapshot saving.

## Principles

1. **DRY (Don't Repeat Yourself)**: Shared formatting and mapping logic must reside in `logic.py` or `utils/helpers.py`.
2. **Loosely Coupled**: Components should interact via defined APIs or signals. For example, `InteractionHandler` emits a `zoom_range_selected` signal rather than calling a method on `window.py` directly.
3. **Lean Controller**: `window.py` should remain thin, serving as a high-level roadmap of the module's behavior.
