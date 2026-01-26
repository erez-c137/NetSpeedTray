"""
Benchmark for Graph Gap Detection: Loop vs Vectorized.

This script compares the performance of the original loop-based gap detection
versus the proposed NumPy vectorized approach.
"""
import time
import numpy as np
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from typing import List, Tuple

def generate_data_epochs(num_points: int, gap_probability: float = 0.01) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generates synthetic history data as raw epochs and speeds."""
    start_ts = time.time() - num_points
    
    # Vectorized generation for speed
    timestamps = np.arange(start_ts, start_ts + num_points)
    # Add random gaps by adding offsets
    gaps = np.random.rand(num_points) < gap_probability
    timestamps[gaps] += np.random.randint(60, 300, size=np.sum(gaps))
    # Cumulative sum to keep order? No, simpler to just simulate linear time with random large jumps
    
    # Better:
    deltas = np.ones(num_points)
    deltas[gaps] = np.random.randint(61, 300, size=np.sum(gaps))
    timestamps = np.cumsum(deltas) + start_ts
    
    ups = np.random.rand(num_points) * 100
    downs = np.random.rand(num_points) * 100
    return timestamps, ups, downs

def old_implementation_full_pipeline(timestamps: np.ndarray, ups: np.ndarray, downs: np.ndarray):
    """Simulates: DB fetch (obj creation) -> Graph Loop -> Plot prep."""
    start_time = time.perf_counter()
    
    # 1. DB Layer Simulation: Convert raw epochs to datetime objects
    # This is currently done in get_speed_history
    history_data = [
        (datetime.fromtimestamp(ts), u, d) 
        for ts, u, d in zip(timestamps, ups, downs)
    ]
    
    # 2. Graph Loop Logic
    plot_timestamps = []
    plot_ups = []
    plot_downs = []
    
    ts_list = [x[0] for x in history_data]
    ups_list = [x[1] for x in history_data]
    downs_list = [x[2] for x in history_data]
    
    for i in range(len(ts_list)):
        curr_time = ts_list[i]
        curr_up = ups_list[i]
        curr_down = downs_list[i]
        
        if i > 0:
            prev_time = ts_list[i-1]
            if (curr_time - prev_time).total_seconds() > 60:
                gap_time = prev_time + timedelta(seconds=1)
                plot_timestamps.append(gap_time)
                plot_ups.append(np.nan)
                plot_downs.append(np.nan)

        plot_timestamps.append(curr_time)
        plot_ups.append(curr_up)
        plot_downs.append(curr_down)
        
    end_time = time.perf_counter()
    return end_time - start_time, len(plot_timestamps)

def new_vectorized_full_pipeline(timestamps: np.ndarray, ups: np.ndarray, downs: np.ndarray):
    """Simulates: DB fetch (raw) -> Vector Gap Detect -> epoch2num -> Plot prep."""
    start_time = time.perf_counter()
    
    # 1. DB Layer returns raw arrays (simulation: we already have them)
    # So 0 cost here compared to old logic's massive list comprehension.
    
    # 2. Vector Gap Detection on raw epochs
    GAP_THRESHOLD_SEC = 60.0
    
    diffs = np.diff(timestamps)
    gap_indices = np.where(diffs > GAP_THRESHOLD_SEC)[0] + 1
    
    if len(gap_indices) > 0:
        gap_times = timestamps[gap_indices - 1] + 1.0
        
        ts_final = np.insert(timestamps, gap_indices, gap_times)
        ups_final = np.insert(ups, gap_indices, np.nan)
        downs_final = np.insert(downs, gap_indices, np.nan)
    else:
        ts_final = timestamps
        ups_final = ups
        downs_final = downs
        
    # 3. Validation / Plot Prep
    # Matplotlib dates = (unix_timestamp / 86400) + 719163
    # This is a pure vector operation.
    final_mpl_dates = (ts_final / 86400.0) + 719163
    
    end_time = time.perf_counter()
    return end_time - start_time, len(ts_final)

if __name__ == "__main__":
    N = 1000000
    print(f"Generating synthetic Epoch data (N={N})...")
    ts, ups, downs = generate_data_epochs(N)
    
    print("Running Old Full Pipeline (DB Object Creation + Loop)...")
    t_old, count_old = old_implementation_full_pipeline(ts, ups, downs)
    print(f"Old Time: {t_old:.6f} s | Output Points: {count_old}")
    
    print("Running New Vectorized Pipeline (Raw DB + Vector + epoch2num)...")
    t_new, count_new = new_vectorized_full_pipeline(ts, ups, downs)
    print(f"New Time: {t_new:.6f} s | Output Points: {count_new}")
    
    speedup = t_old / t_new if t_new > 0 else 0
    print(f"Speedup: {speedup:.2f}x")
