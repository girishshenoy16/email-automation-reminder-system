"""
Unit tests for Scheduler and Due Reminder Selection Logic (Module 5/9).
"""

import pandas as pd
import pytest
import schedule
from src.scheduler import get_due_reminders, setup_schedule
from src.state_manager import StateManager


@pytest.fixture
def sample_dataset():
    data = [
        {"reminder_id": "R1", "reminder_type": "meeting", "due_date": "2026-09-20", "is_valid_email": True},
        {"reminder_id": "R2", "reminder_type": "payment", "due_date": "2026-09-25", "is_valid_email": True},
        {"reminder_id": "R3", "reminder_type": "webinar", "due_date": "2026-09-30", "is_valid_email": True},
        {"reminder_id": "R4", "reminder_type": "meeting", "due_date": "2026-09-24", "is_valid_email": False},  # invalid email
    ]
    return pd.DataFrame(data)


def test_get_due_reminders_date_filtering(sample_dataset, tmp_path):
    sm = StateManager(state_file_path=tmp_path / "state.json")
    # With cutoff 2026-09-25: R1 and R2 are due, R3 is future, R4 has invalid email
    due = get_due_reminders(sample_dataset, sm, target_date="2026-09-25", reminder_type="all")
    assert len(due) == 2
    assert set(due["reminder_id"]) == {"R1", "R2"}


def test_get_due_reminders_category_filtering(sample_dataset, tmp_path):
    sm = StateManager(state_file_path=tmp_path / "state.json")
    # Filter only payment
    due = get_due_reminders(sample_dataset, sm, target_date="2026-09-25", reminder_type="payment")
    assert len(due) == 1
    assert due.iloc[0]["reminder_id"] == "R2"


def test_get_due_reminders_live_eligibility(sample_dataset, tmp_path):
    sm = StateManager(state_file_path=tmp_path / "state.json")

    # Mark R1 as already completed in live mode
    sm.record_live_accepted("R1", "DEL-1", "250 OK")

    # In Live mode: R1 should be excluded
    due_live = get_due_reminders(sample_dataset, sm, target_date="2026-09-25", is_live=True)
    assert len(due_live) == 1
    assert due_live.iloc[0]["reminder_id"] == "R2"

    # In Dry-run mode: R1 is NOT excluded from simulation view
    due_dry = get_due_reminders(sample_dataset, sm, target_date="2026-09-25", is_live=False)
    assert len(due_dry) == 2
    assert set(due_dry["reminder_id"]) == {"R1", "R2"}


def test_setup_schedule_registers_job():
    schedule.clear()
    executed = []

    def mock_job():
        executed.append(True)

    setup_schedule(mock_job, interval_minutes=15)
    assert len(schedule.jobs) == 1
    schedule.clear()
