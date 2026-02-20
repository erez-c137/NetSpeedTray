"""
Domain logic and stateless calculations for the Graph module.
"""
import os
import psutil
import time as pytime
from datetime import datetime
from typing import Optional, Tuple, Any, List, Dict

from netspeedtray import constants

class GraphLogic:
    """
    Houses pure logic and calculation methods for the graph window.
    These methods are decoupled from the PyQt6 UI where possible.
    """

    @staticmethod
    def get_time_range(period_value: int, session_start: datetime, 
                       boot_time: Optional[datetime] = None, 
                       earliest_db: Optional[datetime] = None) -> Tuple[Optional[datetime], datetime]:
        """
        Determines the start and end time for a query based on a slider index.
        """
        now = datetime.now()
        period_key = constants.data.history_period.PERIOD_MAP.get(
            period_value, constants.data.history_period.DEFAULT_PERIOD
        )
        
        start_time = constants.data.history_period.get_start_time(
            period_key, 
            now, 
            session_start=session_start,
            boot_time=boot_time,
            earliest_db=earliest_db
        )

        return start_time, now

    @staticmethod
    def get_db_size_mb(db_path: Optional[str]) -> float:
        """Calculates the size of the database file in megabytes."""
        if db_path and os.path.exists(db_path):
            try:
                return os.path.getsize(db_path) / (1024 * 1024)
            except (OSError, PermissionError):
                pass
        return 0.0

    @staticmethod
    def days_to_retention_slider_value(days: int) -> int:
        """Maps a day count to the 0-6 slider index based on constants."""
        for slider_value, mapped_days in constants.data.retention.DAYS_MAP.items():
            if mapped_days == days:
                return slider_value
        return 3 # Default to 30 days

    @staticmethod
    def slider_value_to_retention_days(slider_value: int) -> int:
        """Maps a slider index (0-6) back to a day count."""
        return constants.data.retention.DAYS_MAP.get(slider_value, 30)

    @staticmethod
    def get_boot_time() -> datetime:
        """Safely retrieves the system boot time."""
        return datetime.fromtimestamp(psutil.boot_time())

    @staticmethod
    def get_period_key(slider_value: int) -> str:
        """Returns the period key string for a given slider index."""
        return constants.data.history_period.PERIOD_MAP.get(
            slider_value, constants.data.history_period.DEFAULT_PERIOD
        )

    @staticmethod
    def calculate_stats(history_data: List[Tuple[float, float, float]]) -> Dict[str, float]:
        """
        Calculates Max and Average speeds (Mbps) from raw history data.
        Uses IQR-based outlier filtering to eliminate phantom spikes.
        """
        stats = {
            'max_up': 0.0, 'max_down': 0.0,
            'avg_up': 0.0, 'avg_down': 0.0
        }
        
        if not history_data:
            return stats

        upload_bytes_sec = [up for _, up, _ in history_data if up is not None]
        download_bytes_sec = [down for _, _, down in history_data if down is not None]
        
        # Mbps conversion factors
        mbps_factor = constants.network.units.BITS_PER_BYTE / constants.network.units.MEGA_DIVISOR
        
        def filter_outliers_iqr(values: List[float]) -> List[float]:
            """
            Remove outliers using Interquartile Range (IQR) method.
            Filters values > Q3 + 1.5*IQR (eliminates phantom spikes).
            """
            if len(values) < 4:
                return values
            
            try:
                import numpy as np
                q1 = float(np.percentile(values, 25))
                q3 = float(np.percentile(values, 75))
                iqr = q3 - q1
                
                # If IQR is 0 (all same value), don't filter
                if iqr <= 0:
                    return values
                
                lower_bound = q1 - 1.5 * iqr
                upper_bound = q3 + 1.5 * iqr
                
                filtered = [v for v in values if lower_bound <= v <= upper_bound]
                
                # Ensure we don't filter out all data
                return filtered if filtered else values
            except ImportError:
                return values
            except Exception:
                # If filtering fails, return original data
                return values
        
        filtered_upload = filter_outliers_iqr(upload_bytes_sec)
        filtered_download = filter_outliers_iqr(download_bytes_sec)
        
        if filtered_upload:
            stats['max_up'] = max(filtered_upload) * mbps_factor
            stats['avg_up'] = (sum(filtered_upload) / len(filtered_upload)) * mbps_factor
            
        if filtered_download:
            stats['max_down'] = max(filtered_download) * mbps_factor
            stats['avg_down'] = (sum(filtered_download) / len(filtered_download)) * mbps_factor
            
        return stats
