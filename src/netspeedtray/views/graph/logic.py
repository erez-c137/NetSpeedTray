"""
Domain logic and stateless calculations for the Graph module.
"""
import psutil
from datetime import datetime
from typing import Optional, Tuple

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
    def get_boot_time() -> datetime:
        """Safely retrieves the system boot time."""
        return datetime.fromtimestamp(psutil.boot_time())

    @staticmethod
    def get_period_key(slider_value: int) -> str:
        """Returns the period key string for a given slider index."""
        return constants.data.history_period.PERIOD_MAP.get(
            slider_value, constants.data.history_period.DEFAULT_PERIOD
        )

