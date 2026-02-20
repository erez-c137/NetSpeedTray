"""
Unit tests for DataRequest type-safe request objects.
"""

import pytest
from datetime import datetime, timedelta
from netspeedtray.views.graph.request import DataRequest


class TestDataRequest:
    """Tests for the DataRequest dataclass."""
    
    def test_create_valid_request_all_interfaces(self):
        """Test creating a valid DataRequest for all interfaces."""
        start = datetime(2026, 2, 15)
        end = datetime(2026, 2, 18)
        
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name=None,
            is_session_view=False,
            sequence_id=1
        )
        
        assert request.start_time == start
        assert request.end_time == end
        assert request.interface_name is None
        assert request.is_session_view is False
        assert request.sequence_id == 1
    
    def test_create_valid_request_specific_interface(self):
        """Test creating a DataRequest for a specific interface."""
        start = datetime(2026, 2, 15)
        end = datetime(2026, 2, 18)
        
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name="Ethernet",
            is_session_view=False,
            sequence_id=5
        )
        
        assert request.interface_name == "Ethernet"
        assert request.sequence_id == 5
    
    def test_create_session_view_request(self):
        """Test creating a session view DataRequest."""
        start = datetime.now() - timedelta(hours=1)
        end = datetime.now()
        
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name=None,
            is_session_view=True,
            sequence_id=10
        )
        
        assert request.is_session_view is True
    
    def test_request_with_none_start_time(self):
        """Test creating a request with None as start_time (for current session)."""
        end = datetime.now()
        
        request = DataRequest(
            start_time=None,
            end_time=end,
            interface_name=None,
            is_session_view=True,
            sequence_id=1
        )
        
        assert request.start_time is None
        assert request.end_time == end
    
    def test_invalid_end_time_type(self):
        """Test that invalid end_time type raises TypeError."""
        with pytest.raises(TypeError, match="end_time must be datetime"):
            DataRequest(
                start_time=datetime.now(),
                end_time="2026-02-18",  # type: ignore  # Invalid: string instead of datetime
                interface_name=None,
                is_session_view=False,
                sequence_id=1
            )
    
    def test_invalid_start_time_type(self):
        """Test that invalid start_time type raises TypeError."""
        with pytest.raises(TypeError, match="start_time must be datetime or None"):
            DataRequest(
                start_time="2026-02-15",  # type: ignore  # Invalid: string instead of datetime
                end_time=datetime.now(),
                interface_name=None,
                is_session_view=False,
                sequence_id=1
            )
    
    def test_start_after_end(self):
        """Test that start_time > end_time raises ValueError."""
        start = datetime(2026, 2, 20)
        end = datetime(2026, 2, 15)
        
        with pytest.raises(ValueError, match="start_time .* must be before end_time"):
            DataRequest(
                start_time=start,
                end_time=end,
                interface_name=None,
                is_session_view=False,
                sequence_id=1
            )
    
    def test_invalid_is_session_view_type(self):
        """Test that invalid is_session_view type raises TypeError."""
        with pytest.raises(TypeError, match="is_session_view must be bool"):
            DataRequest(
                start_time=datetime.now(),
                end_time=datetime.now(),
                interface_name=None,
                is_session_view="true",  # type: ignore  # Invalid: string instead of bool
                sequence_id=1
            )
    
    def test_invalid_sequence_id_negative(self):
        """Test that negative sequence_id raises ValueError."""
        with pytest.raises(ValueError, match="sequence_id must be non-negative int"):
            DataRequest(
                start_time=datetime.now(),
                end_time=datetime.now(),
                interface_name=None,
                is_session_view=False,
                sequence_id=-1
            )
    
    def test_invalid_sequence_id_type(self):
        """Test that non-int sequence_id raises ValueError."""
        with pytest.raises(ValueError, match="sequence_id must be non-negative int"):
            DataRequest(
                start_time=datetime.now(),
                end_time=datetime.now(),
                interface_name=None,
                is_session_view=False,
                sequence_id=1.5  # type: ignore  # Invalid: float instead of int
            )
    
    def test_invalid_interface_name_type(self):
        """Test that invalid interface_name type raises TypeError."""
        with pytest.raises(TypeError, match="interface_name must be str or None"):
            DataRequest(
                start_time=datetime.now(),
                end_time=datetime.now(),
                interface_name=123,  # type: ignore  # Invalid: int instead of str or None
                is_session_view=False,
                sequence_id=1
            )
    
    def test_request_equality(self):
        """Test that two identical requests compare as equal."""
        start = datetime(2026, 2, 15)
        end = datetime(2026, 2, 18)
        
        request1 = DataRequest(
            start_time=start,
            end_time=end,
            interface_name="Ethernet",
            is_session_view=False,
            sequence_id=5
        )
        
        request2 = DataRequest(
            start_time=start,
            end_time=end,
            interface_name="Ethernet",
            is_session_view=False,
            sequence_id=5
        )
        
        assert request1 == request2
    
    def test_request_inequality(self):
        """Test that different requests compare as unequal."""
        start = datetime(2026, 2, 15)
        end = datetime(2026, 2, 18)
        
        request1 = DataRequest(
            start_time=start,
            end_time=end,
            interface_name="Ethernet",
            is_session_view=False,
            sequence_id=5
        )
        
        request2 = DataRequest(
            start_time=start,
            end_time=end,
            interface_name="WiFi",  # Different interface
            is_session_view=False,
            sequence_id=5
        )
        
        assert request1 != request2
    
    def test_request_hash(self):
        """Test that requests can be used as dict keys (hashable)."""
        start = datetime(2026, 2, 15)
        end = datetime(2026, 2, 18)
        
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name="Ethernet",
            is_session_view=False,
            sequence_id=5
        )
        
        # Should not raise
        request_dict = {request: "value"}
        assert request_dict[request] == "value"
    
    def test_request_repr(self):
        """Test that repr provides useful debugging information."""
        start = datetime(2026, 2, 15)
        end = datetime(2026, 2, 18)
        
        request = DataRequest(
            start_time=start,
            end_time=end,
            interface_name="Ethernet",
            is_session_view=False,
            sequence_id=5
        )
        
        repr_str = repr(request)
        assert "DataRequest" in repr_str
        assert "sequence_id=5" in repr_str
        assert "Ethernet" in repr_str
    
    def test_sequence_id_zero_valid(self):
        """Test that sequence_id=0 is valid."""
        request = DataRequest(
            start_time=datetime.now(),
            end_time=datetime.now(),
            interface_name=None,
            is_session_view=False,
            sequence_id=0
        )
        
        assert request.sequence_id == 0
    
    def test_sequence_id_large_value(self):
        """Test that large sequence_id values work correctly."""
        request = DataRequest(
            start_time=datetime.now(),
            end_time=datetime.now(),
            interface_name=None,
            is_session_view=False,
            sequence_id=999999
        )
        
        assert request.sequence_id == 999999
