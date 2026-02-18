"""
Data request objects for graph worker thread communication.

This module provides type-safe request objects that encapsulate parameters
for graph data processing. Using dataclasses instead of loose tuples provides:
- Type safety and IDE autocomplete
- Self-documenting code
- Easy extension for future parameters
- Better error messages on invalid requests
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class DataRequest:
    """
    Encapsulates all parameters needed to request graph data processing.
    
    This replaces the loose tuple of (start, end, interface, is_session, sequence_id)
    with a typed, self-documenting object.
    
    Attributes:
        start_time: Start of the time range to fetch (None for current session)
        end_time: End of the time range to fetch
        interface_name: Specific interface to filter, or None for all interfaces
        is_session_view: True if viewing current session data (affects aggregation)
        sequence_id: Request ID for deduplicating stale responses
    
    Example:
        >>> request = DataRequest(
        ...     start_time=datetime(2026, 2, 15),
        ...     end_time=datetime(2026, 2, 18),
        ...     interface_name="Ethernet",
        ...     is_session_view=False,
        ...     sequence_id=5
        ... )
        >>> # Pass to worker
        >>> worker.process_data(request)
    """
    start_time: Optional[datetime]
    end_time: datetime
    interface_name: Optional[str]
    is_session_view: bool
    sequence_id: int
    
    def __post_init__(self):
        """Validate request parameters."""
        if not isinstance(self.end_time, datetime):
            raise TypeError(f"end_time must be datetime, got {type(self.end_time)}")
        
        if self.start_time is not None and not isinstance(self.start_time, datetime):
            raise TypeError(f"start_time must be datetime or None, got {type(self.start_time)}")
        
        if self.start_time is not None and self.start_time > self.end_time:
            raise ValueError(f"start_time ({self.start_time}) must be before end_time ({self.end_time})")
        
        if not isinstance(self.is_session_view, bool):
            raise TypeError(f"is_session_view must be bool, got {type(self.is_session_view)}")
        
        if not isinstance(self.sequence_id, int) or self.sequence_id < 0:
            raise ValueError(f"sequence_id must be non-negative int, got {self.sequence_id}")
        
        if self.interface_name is not None and not isinstance(self.interface_name, str):
            raise TypeError(f"interface_name must be str or None, got {type(self.interface_name)}")
    
    def __hash__(self):
        """Allow DataRequest to be used in sets/dicts if needed."""
        return hash((self.start_time, self.end_time, self.interface_name, self.is_session_view, self.sequence_id))
    
    def __eq__(self, other):
        """Compare requests by their content."""
        if not isinstance(other, DataRequest):
            return False
        return (
            self.start_time == other.start_time
            and self.end_time == other.end_time
            and self.interface_name == other.interface_name
            and self.is_session_view == other.is_session_view
            and self.sequence_id == other.sequence_id
        )
    
    def __repr__(self):
        """Provide a clear representation for debugging."""
        return (
            f"DataRequest(sequence_id={self.sequence_id}, "
            f"start={self.start_time}, end={self.end_time}, "
            f"interface={self.interface_name}, session={self.is_session_view})"
        )
