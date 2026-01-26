
import os

file_path = 'src/netspeedtray/views/graph.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Verify target lines
# Line 1786 (index 1785) should start with "    def _render_graph_deprecated"
# Line 1963 (index 1962) should start with "    def _update_stats_bar"

line_1786 = lines[1785]
line_1963 = lines[1962]

print(f"Index 1785: {line_1786.strip()}")
print(f"Index 1962: {line_1963.strip()}")

if "_render_graph_deprecated" not in line_1786:
    print("Error: Start line mismatch")
    exit(1)
if "_update_stats_bar" not in line_1963:
    print("Error: End line mismatch")
    exit(1)

# Keep lines 0..1784 (inclusive) -> lines[:1785]
# Skip lines 1785..1961 (inclusive)
# Keep lines 1962..end -> lines[1962:]

new_lines = lines[:1785] + lines[1962:]

# Update the stats bar signature (now at index 1785)
# "    def _update_stats_bar(self, history_data: List[Tuple[datetime, float, float]]) -> None:\n"
# to
# "    def _update_stats_bar(self, history_data: List[Tuple[float, float, float]]) -> None:\n"

target_line = new_lines[1785]
if "List[Tuple[datetime, float, float]]" in target_line:
    new_lines[1785] = target_line.replace("datetime, float, float", "float, float, float")
    print("Updated type hint.")

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Cleanup complete.")
