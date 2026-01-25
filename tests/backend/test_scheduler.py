"""Tests for scheduler module."""
import pytest
from unittest.mock import Mock, patch, call
from backend.scheduler import DataScheduler
from backend.database import Database


@pytest.fixture
def mock_db():
    """Create mock database."""
    return Mock(spec=Database)


@pytest.fixture
def scheduler(mock_db):
    """Create scheduler instance."""
    with patch('backend.scheduler.APIClient'), \
         patch('backend.scheduler.DataService'), \
         patch('backend.scheduler.SolkoffCalculator'):
        sched = DataScheduler(mock_db, "CL")
        sched.data_service = Mock()
        sched.calculator = Mock()
        return sched


def test_scheduler_initialization(scheduler, mock_db):
    """Test scheduler initialization."""
    assert scheduler.db == mock_db
    assert scheduler.competition_id == "CL"
    assert scheduler.scheduler is not None


def test_update_data_success(scheduler):
    """Test successful data update."""
    scheduler.update_data()
    
    scheduler.data_service.sync_all.assert_called_once_with("CL")
    scheduler.calculator.calculate_all.assert_called_once()


def test_update_data_error_handling(scheduler):
    """Test error handling in update_data."""
    scheduler.data_service.sync_all.side_effect = Exception("API Error")
    
    # Should not raise, but log error
    try:
        scheduler.update_data()
    except Exception:
        pytest.fail("update_data should handle errors gracefully")


def test_start_scheduler(scheduler):
    """Test starting the scheduler."""
    scheduler.start(interval_seconds=1800)
    
    assert scheduler.scheduler.running
    scheduler.scheduler.shutdown()


def test_stop_scheduler(scheduler):
    """Test stopping the scheduler."""
    scheduler.start()
    scheduler.stop()
    
    assert not scheduler.scheduler.running


def test_trigger_update(scheduler):
    """Test manual update trigger."""
    scheduler.trigger_update()
    
    scheduler.data_service.sync_all.assert_called_once_with("CL")
    scheduler.calculator.calculate_all.assert_called_once()

