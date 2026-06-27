"""
The Monitor window (v2.0 capstone) — one calm, Win11-native window that unifies the history
graph and per-app activity into Overview / Network / Hardware tabs.

Import firewall: nothing in this package may import matplotlib/numpy at module scope. The graph
package is imported lazily inside GraphHost.ensure_loaded() only, so a glance at Overview never
pays the matplotlib cost (preserving the idle-RAM win). Enforced by test_monitor_import_firewall.
"""
