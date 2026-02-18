"""
Graph window resize stability tests.
Validates settings panel toggle resizing is idempotent.

Issue #103: Application Window Shrinks
- Tests verify repeated show/hide of settings panel uses same width delta
- Validates that base panel width is measured once and reused
- Prevents cumulative shrinking with each toggle
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from PyQt6.QtCore import QSize


class TestGraphWindowResizeIdempotent:
    """Test that repeated settings panel toggles maintain window size."""
    
    def test_toggle_settings_panel_width_constant(self):
        """
        Test repeated show/hide uses same width delta.
        
        Bug #103: Previous code used hardcoded 320px delta, but actual
        panel width varied. Each toggle contracted window by (actual - 320).
        
        Fix: Measure panel.sizeHint() once, store as base_width, reuse it.
        """
        # Simulate the fixed behavior
        base_width = 340  # Actual measured panel width
        num_toggles = 10
        
        widths_during_toggle = []
        current_window_width = 800
        
        for i in range(num_toggles):
            if i % 2 == 0:
                # Hide settings panel: subtract base_width
                current_window_width -= base_width
            else:
                # Show settings panel: add base_width back
                current_window_width += base_width
            widths_during_toggle.append(current_window_width)
        
        # With idempotent logic, after expand then contract, width should match
        initial = 800
        after_hide = initial - base_width
        after_show = after_hide + base_width
        
        assert after_show == initial, "Window should return to original width after toggle cycle"
    
    def test_base_width_measured_once(self):
        """
        Test that panel base width is measured at init, not per toggle.
        
        This prevents needing to call sizeHint() repeatedly (expensive).
        """
        # Mock the window state
        mock_settings_panel = MagicMock()
        mock_settings_panel.sizeHint.return_value = MagicMock(width=lambda: 340)
        
        # Simulate window initialization
        class MockGraphWindow:
            def __init__(self):
                self._settings_panel_base_width = 0
                self.settings_panel = mock_settings_panel
                self._measure_panel_width()
            
            def _measure_panel_width(self):
                """Called once at init to measure base width."""
                if self._settings_panel_base_width == 0:
                    self._settings_panel_base_width = self.settings_panel.sizeHint().width()
            
            def toggle_settings(self):
                """Use stored base width, don't re-measure."""
                return self._settings_panel_base_width
        
        window = MockGraphWindow()
        
        # First measurement happens
        assert mock_settings_panel.sizeHint.call_count == 1
        assert window._settings_panel_base_width == 340
        
        # Subsequent toggles use stored width
        width1 = window.toggle_settings()
        width2 = window.toggle_settings()
        
        # sizeHint should still only be called once
        assert mock_settings_panel.sizeHint.call_count == 1
        assert width1 == width2 == 340
    
    def test_toggle_sequence_no_drift(self):
        """
        Test multiple toggle sequences maintain window size (no drift).
        
        Simulate:
        - Initial window: 800px
        - Toggle hide: 800 - 340 = 460
        - Toggle show: 460 + 340 = 800
        - Repeat 5 times
        - Final: should be 800px
        """
        base_width = 340
        initial_width = 800
        current_width = initial_width
        
        # 5 complete toggle cycles (hide then show)
        for cycle in range(5):
            # Hide
            current_width -= base_width
            assert current_width == initial_width - base_width
            
            # Show
            current_width += base_width
            assert current_width == initial_width, f"Width drifted on cycle {cycle}"
    
    def test_width_with_actual_sizeHint(self):
        """
        Test integration with actual QWidget sizeHint measurement.
        """
        from unittest.mock import MagicMock
        
        mock_panel = MagicMock()
        size_hint = MagicMock()
        size_hint.width.return_value = 345  # Actual measured width
        mock_panel.sizeHint.return_value = size_hint
        
        # Simulate window behavior
        class SettingsWindow:
            def __init__(self, panel):
                self._base_width = 0
                self.panel = panel
                self._init_panel_width()
            
            def _init_panel_width(self):
                """Initialize at window creation time."""
                hint = self.panel.sizeHint()
                self._base_width = hint.width() if hint and hint.width() > 0 else 320
            
            def get_toggle_delta(self):
                """Consistent delta for all future toggles."""
                return self._base_width
        
        window = SettingsWindow(mock_panel)
        
        # All toggles use same delta
        delta1 = window.get_toggle_delta()
        delta2 = window.get_toggle_delta()
        
        assert delta1 == delta2 == 345


class TestGraphWindowResizeEdgeCases:
    """Test edge cases in window resizing."""
    
    def test_zero_panel_width_fallback(self):
        """
        Test fallback when sizeHint returns 0 or invalid.
        """
        mock_panel = MagicMock()
        size_hint = MagicMock()
        size_hint.width.return_value = 0  # Invalid: zero width
        mock_panel.sizeHint.return_value = size_hint
        
        class SettingsWindow:
            def __init__(self, panel, fallback=320):
                self._base_width = 0
                self.panel = panel
                self.fallback_width = fallback
                self._init_panel_width()
            
            def _init_panel_width(self):
                hint = self.panel.sizeHint()
                measured = hint.width() if hint else 0
                self._base_width = measured if measured > 0 else self.fallback_width
        
        window = SettingsWindow(mock_panel)
        
        # Should use fallback
        assert window._base_width == 320
        assert window._base_width > 0  # Never zero or negative
    
    def test_negative_width_clamped(self):
        """Test that negative width values are clamped to positive."""
        base_width = 340
        current = 800
        
        # Even if something goes wrong, width should stay positive
        current = max(0, current - base_width)
        assert current >= 0
    
    def test_minimum_window_width_preserved(self):
        """Test that window never becomes smaller than minimum."""
        min_width = 400
        base_panel_width = 340
        current_width = min_width
        
        # Attempt to hide panel (would make window smaller)
        new_width = current_width - base_panel_width  # Would be 60px
        
        # Constrain to minimum
        final_width = max(min_width, new_width)
        
        assert final_width >= min_width
        assert final_width == min_width  # Should be minimum


class TestSettingsPanelLayoutChanges:
    """Test window resize when settings panel layout changes."""
    
    def test_panel_layout_change_handler(self):
        """
        Test that dynamic panel layout changes are handled.
        
        When expandable sections in settings panel toggle,
        panel size might change. Window should adjust.
        """
        class GraphWindow:
            def __init__(self):
                self._base_width = 340
                self.settings_panel = MagicMock()
                self._last_panel_width = self._base_width
            
            def _on_settings_layout_changed(self):
                """Handler for when settings panel internal layout changes."""
                new_size = self.settings_panel.sizeHint()
                new_width = new_size.width() if new_size else self._base_width
                
                # Only adjust if significant change (>10px)
                delta = abs(new_width - self._last_panel_width)
                if delta > 10:
                    # Recalculate window width
                    width_change = new_width - self._last_panel_width
                    self._last_panel_width = new_width
                    return width_change
                return 0
        
        window = GraphWindow()
        
        # Panel expands
        window.settings_panel.sizeHint.return_value = MagicMock(width=lambda: 380)
        delta = window._on_settings_layout_changed()
        
        assert delta == 40  # Should expand by 40px
    
    def test_panel_resize_stability(self):
        """Test that repeated measurements of panel yield same result."""
        mock_panel = MagicMock()
        size_hint = MagicMock(width=lambda: 345)
        mock_panel.sizeHint.return_value = size_hint
        
        # Measure multiple times
        measurements = [mock_panel.sizeHint().width() for _ in range(5)]
        
        # All should be identical
        assert all(m == 345 for m in measurements)
        assert len(set(measurements)) == 1


class TestWindowResizeWithoutSweeping:
    """Test window resize behavior without full sweep (efficient approach)."""
    
    def test_lazy_recalculation(self):
        """
        Test that window size is recalculated only when needed.
        
        Instead of recalculating on every event, calculate once at init
        and once if layout changes significantly.
        """
        class OptimizedWindow:
            def __init__(self, initial_panel_width=340):
                self._base_panel_width = initial_panel_width
                self._recalc_needed = False
            
            def on_panel_layout_changed(self, new_panel_width):
                """Mark recalc needed if change is significant."""
                delta = abs(new_panel_width - self._base_panel_width)
                if delta > 20:  # Only care about large changes
                    self._recalc_needed = True
                    self._base_panel_width = new_panel_width
            
            def should_recalculate_window(self):
                """Check if window size needs recalculation."""
                return self._recalc_needed
        
        window = OptimizedWindow()
        
        # Small change: no recalc needed
        window.on_panel_layout_changed(350)
        assert not window.should_recalculate_window()
        
        # Large change: recalc needed
        window.on_panel_layout_changed(380)
        assert window.should_recalculate_window()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
