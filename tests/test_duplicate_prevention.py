"""
Unit tests for Duplicate Prevention, Multi-Run Idempotency, and Ambiguous Outcome Quarantine (Module 6/9).
"""

import pandas as pd
import pytest
from src.scheduler import get_due_reminders
from src.state_manager import StateManager


@pytest.fixture
def test_df():
    return pd.DataFrame([
        {
            "reminder_id": "REM-IDEM-01",
            "contact_id": "CNT-01",
            "name": "Jordan",
            "email": "jordan@example.com",
            "reminder_type": "meeting",
            "due_date": "2026-09-25",
            "is_valid_email": True,
        },
        {
            "reminder_id": "REM-IDEM-02",
            "contact_id": "CNT-02",
            "name": "Morgan",
            "email": "morgan@example.com",
            "reminder_type": "payment",
            "due_date": "2026-09-25",
            "is_valid_email": True,
        }
    ])


def test_repeated_dry_runs_do_not_block_live(test_df, tmp_path):
    sm = StateManager(state_file_path=tmp_path / "state.json")

    # Run dry-run 5 times
    for _ in range(5):
        for rem_id in test_df["reminder_id"]:
            sm.record_simulation_success(rem_id)

    # Verify both reminders have simulation_count = 5
    for rem_id in test_df["reminder_id"]:
        assert sm.get_simulation_status(rem_id) == "SIMULATED"
        assert sm.state["reminders"][rem_id]["simulation_state"]["simulation_count"] == 5
        # Crucial: Live state MUST remain PENDING
        assert sm.get_live_status(rem_id) == "PENDING"
        assert sm.is_eligible_for_live(rem_id) is True

    # Both must still be selected in live mode
    eligible_live = get_due_reminders(test_df, sm, target_date="2026-09-25", is_live=True)
    assert len(eligible_live) == 2


def test_repeated_live_runs_skip_already_accepted(test_df, tmp_path):
    sm = StateManager(state_file_path=tmp_path / "state.json")

    # First live pass: both reminders are eligible
    pass_1 = get_due_reminders(test_df, sm, target_date="2026-09-25", is_live=True)
    assert len(pass_1) == 2

    # Simulate dispatching REM-IDEM-01 live
    sm.set_live_processing("REM-IDEM-01")
    sm.record_live_accepted("REM-IDEM-01", "DEL-101", "250 OK")

    # Second live pass: REM-IDEM-01 must be skipped; only REM-IDEM-02 remains
    pass_2 = get_due_reminders(test_df, sm, target_date="2026-09-25", is_live=True)
    assert len(pass_2) == 1
    assert pass_2.iloc[0]["reminder_id"] == "REM-IDEM-02"

    # Simulate dispatching REM-IDEM-02 live
    sm.set_live_processing("REM-IDEM-02")
    sm.record_live_accepted("REM-IDEM-02", "DEL-102", "250 OK")

    # Third live pass: Both are completed -> 0 reminders eligible (True Idempotence)
    pass_3 = get_due_reminders(test_df, sm, target_date="2026-09-25", is_live=True)
    assert len(pass_3) == 0


def test_quarantined_requires_review_blocks_live_dispatch(test_df, tmp_path):
    sm = StateManager(state_file_path=tmp_path / "state.json")

    # Simulate ambiguous timeout during DATA transmission for REM-IDEM-01
    sm.record_requires_review("REM-IDEM-01", "Socket reset during DATA transmission")

    # In live mode: REM-IDEM-01 must be quarantined and blocked from selection
    eligible = get_due_reminders(test_df, sm, target_date="2026-09-25", is_live=True)
    assert len(eligible) == 1
    assert eligible.iloc[0]["reminder_id"] == "REM-IDEM-02"
    assert sm.is_eligible_for_live("REM-IDEM-01") is False

    # Manual admin resolution to SMTP_ACCEPTED (verified in server logs)
    sm.resolve_quarantined_record("REM-IDEM-01", "SMTP_ACCEPTED")
    assert sm.get_live_status("REM-IDEM-01") == "SMTP_ACCEPTED"
    assert sm.is_eligible_for_live("REM-IDEM-01") is False
