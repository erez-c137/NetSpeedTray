
from PyQt6.QtCore import QObject, pyqtSignal
import logging
import time
from typing import List, Tuple, Dict, Any
from typing import List, Tuple, Optional
from datetime import datetime
from netspeedtray.views.graph.request import DataRequest

class GraphDataWorker(QObject):
    """
    Processes graph data in a background thread to keep the UI responsive.
    
    TODO: FUTURE OPTIMIZATION - Implement Matplotlib blitting for real-time updates.
    Blitting only redraws changed parts of the canvas, which can significantly
    reduce render time for live data updates. See: https://matplotlib.org/stable/users/explain/animations/blitting.html
    """
    # NOTE: Overview emits a dict payload (multi-dataset), while other tabs emit a list of tuples.
    # Using `object` avoids PyQt type coercion issues that can silently break live updates on Overview.
    data_ready = pyqtSignal(object, float, float, int) # history_data, total_up, total_down, sequence_id
    # (sequence_id, reconstructed): emitted just before a single-stat data_ready - True when part of
    # its totals was estimated from speeds recorded before 2.2 (D5), so the Network header can say so.
    totals_provenance = pyqtSignal(int, bool)
    # (sequence_id, {"resolution", "measured"}): emitted just before a DB-backed network data_ready -
    # WidgetState.get_graph_coverage, so the renderer can name the statistic (S3) and tell measured idle
    # from unmeasured time (S4). Absent for the in-memory Session, which claims neither.
    graph_coverage = pyqtSignal(int, object)
    error = pyqtSignal(str)

    # Maximum data points to return (prevents excessive rendering time)
    MAX_DATA_POINTS = 2000

    @staticmethod
    def _preserve_global_peaks(data: List[Tuple[float, float, float]], sampled: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
        """
        Ensures stride downsampling does not drop global upload/download peaks.
        """
        if not data or not sampled:
            return sampled

        peak_up = max(data, key=lambda point: float(point[1] or 0.0))
        peak_down = max(data, key=lambda point: float(point[2] or 0.0))

        sampled_set = set(sampled)
        if peak_up not in sampled_set:
            sampled.append(peak_up)
            sampled_set.add(peak_up)
        if peak_down not in sampled_set:
            sampled.append(peak_down)

        return sorted(sampled, key=lambda point: point[0])

    @staticmethod
    def _thin_keep_peaks(data: List[Tuple[float, float, float]], max_points: int) -> List[Tuple[float, float, float]]:
        """At most ``max_points`` of ``data`` (time-ordered (ts, up, down) rows), keeping every bucket's
        peaks: the rows are cut into max_points // 2 consecutive buckets and each keeps its
        highest-download row and its highest-upload row (one row when they coincide).

        The old thinning kept every Nth row plus the single biggest peak, so every other spike was
        halved or dropped depending on where the stride fell (rc.2 review of #436: on 4 h a 45 Mbps
        spike drew as 22 and a 3.7 Mbps upload spike vanished). The kept rows are stored rows,
        unchanged, so the hover still reports a measured value - the bucket's peak - and the line
        still passes through every bucket's top."""
        n = len(data)
        if n <= max_points or max_points < 2:
            return list(data)
        buckets = max_points // 2
        size = -(-n // buckets)                          # ceil: every row lands in a bucket
        keep = []
        for b0 in range(0, n, size):
            chunk = range(b0, min(n, b0 + size))
            i_down = max(chunk, key=lambda i: float(data[i][2] or 0.0))
            i_up = max(chunk, key=lambda i: float(data[i][1] or 0.0))
            keep.extend(sorted({i_down, i_up}))
        return [data[i] for i in keep]

    def __init__(self, widget_state):
        """
        Initializes the worker.
        
        Args:
            widget_state: A direct reference to the application's WidgetState object.
        """
        super().__init__()
        self.widget_state = widget_state
        self.logger = logging.getLogger(__name__)
        self._last_received_id = -1
        
        # Timeline data cache: {period_key: (data, total_up, total_down, timestamp)}
        # Cache expires after 30 seconds to ensure data freshness
        self._cache: Dict[str, tuple] = {}
        self._cache_ttl = 30.0  # seconds


    @staticmethod
    def _session_volume(samples) -> Tuple[float, float]:
        """Bytes up/down for in-memory ``(ts, up_Bps, down_Bps)`` samples: each speed times the time it
        covers (the gap since the previous sample). Summing the B/s values counted one second per
        sample, so at a 2/5/10 s update rate a 600 MB session read 300/120/60 MB (M5). A gap far past
        the typical interval (sleep, pause, stall) counts as one typical interval - the controller
        re-primes after such a gap instead of measuring across it."""
        if not samples:
            return 0.0, 0.0
        from statistics import median
        from netspeedtray import constants
        net = constants.network.speed
        gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:]) if b[0] > a[0]]
        typical = median(gaps) if gaps else 1.0
        cap = max(float(net.RATE_VALIDITY_MIN_SECONDS), float(net.RATE_VALIDITY_POLL_MULTIPLE) * typical)
        up = down = 0.0
        prev = None
        for ts, u, d in samples:
            dt = typical if prev is None else ts - prev
            if dt <= 0 or dt > cap:
                dt = typical
            up += float(u) * dt
            down += float(d) * dt
            prev = ts
        return up, down

    def _period_totals(self, request: DataRequest) -> Tuple[float, float, bool]:
        """(up_bytes, down_bytes, reconstructed) for the request's window from the database.
        ``reconstructed`` (decision D5) is True when any part was estimated from stored rates."""
        from netspeedtray.core.db.history_reader import VolumeResult
        volume = getattr(self.widget_state, "get_history_volume_for_period", None)
        if volume is not None:
            try:
                v = volume(request.start_time, request.end_time, request.interface_name)
                if isinstance(v, VolumeResult):
                    return float(v.up_bytes), float(v.down_bytes), bool(v.reconstructed)
            except Exception as e:
                self.logger.debug("volume with provenance failed, plain totals instead: %s", e)
        up, down = self.widget_state.get_total_bandwidth_for_period(
            start_time=request.start_time, end_time=request.end_time,
            interface_name=request.interface_name)
        return up, down, False

    def process_data(self, request: DataRequest):
        """
        Processes speed history data in a background thread.
        
        Args:
            request: DataRequest object containing all parameters for data fetching
        """
        try:
            # Validate request
            if not isinstance(request, DataRequest):
                self.error.emit(f"Invalid request type: {type(request).__name__}. Expected DataRequest.")
                return
            
            # Check if this request is already obsolete
            if request.sequence_id < self._last_received_id:
                return
            self._last_received_id = request.sequence_id

            if not self.widget_state:
                self.error.emit("Data source (WidgetState) not available.")
                return

            # The fast in-memory session path only has the ALL-interfaces aggregate, so it can't honor a
            # per-NIC filter. When the user scopes the graph to one interface, fall through to the DB path
            # (which is per-interface) even for the session range. "all"/None keeps the in-memory path.
            net_use_memory = request.is_session_view and not request.interface_name

            if request.stat_type == "overview":
                # Multi-dataset fetch for Overview tab
                total_up = 0.0
                total_down = 0.0
                if net_use_memory:
                    net_data = self.widget_state.get_aggregated_speed_history()
                    # Copy getters, NOT the live deques: this runs on the worker thread while the GUI
                    # thread appends via add_hardware_stat(); iterating a deque mid-append raises
                    # "deque mutated during iteration" (the GIL doesn't protect a multi-bytecode loop).
                    cpu_data = self.widget_state.get_cpu_history()
                    gpu_data = self.widget_state.get_gpu_history()

                    start_ts = request.start_time.timestamp() if request.start_time else 0
                    end_ts = request.end_time.timestamp()
                    
                    filtered_net = [d for d in net_data if start_ts <= d.timestamp.timestamp() <= end_ts]
                    total_up = sum(float(d.upload or 0.0) for d in filtered_net)
                    total_down = sum(float(d.download or 0.0) for d in filtered_net)

                    history_data = {
                        "network": [(d.timestamp.timestamp(), d.upload, d.download) for d in filtered_net],
                        "cpu": [(d.timestamp.timestamp(), d.value, 0.0) for d in cpu_data if start_ts <= d.timestamp.timestamp() <= end_ts],
                        "gpu": [(d.timestamp.timestamp(), d.value, 0.0) for d in gpu_data if start_ts <= d.timestamp.timestamp() <= end_ts]
                    }
                else:
                    net_data = self.widget_state.get_speed_history(request.start_time, request.end_time, request.interface_name, return_raw=True)
                    cpu_data = self.widget_state.get_hardware_history("cpu", request.start_time, request.end_time)
                    gpu_data = self.widget_state.get_hardware_history("gpu", request.start_time, request.end_time)

                    # Totals for the selected interval (DB-backed views)
                    total_up, total_down = self.widget_state.get_total_bandwidth_for_period(
                        start_time=request.start_time,
                        end_time=request.end_time,
                        interface_name=request.interface_name
                    )
                    
                    history_data = {
                        "network": [(dt, up, dw) for dt, up, dw in net_data],
                        "cpu": [(dt, val, 0.0) for dt, val in cpu_data],
                        "gpu": [(dt, val, 0.0) for dt, val in gpu_data]
                    }
                # For Overview, we don't downsample here for simplicity, or handle per-key
                # We'll return early with this dict
                self.data_ready.emit(history_data, total_up, total_down, request.sequence_id)
                return

            if request.stat_type in ("hwcombined", "hwseparate"):
                # CPU + GPU for the Monitor's Hardware graph (one shared axis, or two stacked axes) -
                # same fetch as Overview's cpu/gpu, minus network. Dict payload, like Overview.
                if request.is_session_view:
                    start_ts = request.start_time.timestamp() if request.start_time else 0
                    end_ts = request.end_time.timestamp()
                    history_data = {
                        role: [(d.timestamp.timestamp(), d.value, 0.0) for d in series
                               if start_ts <= d.timestamp.timestamp() <= end_ts]
                        for role, series in (("cpu", self.widget_state.get_cpu_history()),   # copies (worker thread)
                                             ("gpu", self.widget_state.get_gpu_history()),
                                             ("ram", self.widget_state.get_ram_history()))
                    }
                else:
                    history_data = {
                        role: [(dt, val, 0.0) for dt, val in
                               self.widget_state.get_hardware_history(role, request.start_time, request.end_time)]
                        for role in ("cpu", "gpu", "ram")
                    }
                self.data_ready.emit(history_data, 0.0, 0.0, request.sequence_id)
                return

            total_up = 0.0
            total_down = 0.0
            reconstructed = False   # live (in-memory) totals are measured, never estimated

            if request.stat_type in ("cpu", "gpu"):
                if request.is_session_view:
                    # In-memory session data - copy getter (worker thread vs GUI-thread appends).
                    aggregated_data = (self.widget_state.get_cpu_history() if request.stat_type == "cpu"
                                       else self.widget_state.get_gpu_history())
                    start_ts = request.start_time.timestamp() if request.start_time else 0
                    end_ts = request.end_time.timestamp()
                    
                    history_data = []
                    for d in aggregated_data:
                        ts = d.timestamp.timestamp()
                        if ts < start_ts or ts > end_ts: continue
                        history_data.append((ts, d.value, 0.0)) # Hardware is single value, reuse second slot for convenience or pass 0
                else:
                    # Database data
                    raw_history = self.widget_state.get_hardware_history(request.stat_type, request.start_time, request.end_time)
                    history_data = [(dt, val, 0.0) for dt, val in raw_history]

            elif net_use_memory:
                # OPTIMIZATION: Use the pre-calculated aggregated history from WidgetState.
                # (Only when not scoped to a single NIC - see net_use_memory; per-NIC falls to the DB path.)
                aggregated_data = self.widget_state.get_aggregated_speed_history()
                
                start_ts = request.start_time.timestamp() if request.start_time else 0
                end_ts = request.end_time.timestamp()

                processed_history = []
                for d in aggregated_data:
                    ts = float(d.timestamp.timestamp())
                    # Filter for zoom if applicable
                    if ts < start_ts or ts > end_ts:
                        continue
                    processed_history.append((ts, float(d.upload), float(d.download)))

                history_data = processed_history
                total_up, total_down = self._session_volume(processed_history)
            else:
                # For all other timelines, get data from the database.
                history_data = self.widget_state.get_speed_history(
                    start_time=request.start_time,
                    end_time=request.end_time,
                    interface_name=request.interface_name,
                    return_raw=True
                )

                # Fetch totals from DB as well (DURING the worker thread to avoid UI freeze), with
                # their provenance: part of them may be estimated from pre-2.2 speeds (D5).
                total_up, total_down, reconstructed = self._period_totals(request)
                try:
                    coverage = self.widget_state.get_graph_coverage(
                        request.start_time, request.end_time, request.interface_name)
                except Exception as e:   # the graph still draws; every silence then breaks the line
                    self.logger.debug("graph coverage read failed: %s", e)
                    coverage = {"resolution": None, "measured": None}
                self.graph_coverage.emit(request.sequence_id, coverage)

            # Smart Downsampling: Cap at MAX_DATA_POINTS for rendering performance
            if len(history_data) > self.MAX_DATA_POINTS:
                stride = len(history_data) // self.MAX_DATA_POINTS
                downsampled = history_data[::stride]
                if request.stat_type == "network":
                    history_data = self._thin_keep_peaks(history_data, self.MAX_DATA_POINTS)
                else:
                    history_data = downsampled

            # The history is never padded (S4), so a ranged network window can hold ONE stored row:
            # drawn as a point, not dropped as "no traffic".
            min_rows = 1 if (request.stat_type == "network" and not net_use_memory) else 2
            if not history_data or len(history_data) < min_rows:
                self.totals_provenance.emit(request.sequence_id, False)
                self.data_ready.emit([], 0.0, 0.0, request.sequence_id)
                return

            # Ahead of data_ready (same thread, same receiver: delivered in order).
            self.totals_provenance.emit(request.sequence_id, bool(reconstructed))

            # Pass the processed data and pre-calculated totals back to the UI.
            self.logger.debug(f"DataWorker emitting data. StatType: {request.stat_type}, IsSession: {request.is_session_view}, Items: {len(history_data) if not isinstance(history_data, dict) else {k: len(v) for k,v in history_data.items()}}")
            self.data_ready.emit(history_data, total_up, total_down, request.sequence_id)
        except Exception as e:
            logging.getLogger(__name__).error(f"Error in data worker: {e}", exc_info=True)
            self.error.emit(str(e))
