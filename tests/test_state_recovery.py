"""
Unit tests for Persistent State Management and Crash Recovery (Module 3/9).
"""

import json
import pytest
from pathlib import Path
from src.state_manager import StateManager


def test_atomic_persistence(tmp_path):
    state_file = tmp_path / "test_state.json"
    sm = StateManager(state_file_path=state_file)
    sm.record_simulation_success("REM-TEST-1", "CNT-1", "2026-09-25")

    assert state_file.exists()
    assert not state_file.with_suffix(".json.tmp").exists()

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "REM-TEST-1" in data["reminders"]
    assert data["reminders"]["REM-TEST-1"]["simulation_state"]["status"] == "SIMULATED"


def test_dual_track_independence(tmp_path):
    state_file = tmp_path / "test_state.json"
    sm = StateManager(state_file_path=state_file)

    # Simulate 3 times
    for _ in range(3):
        sm.record_simulation_success("REM-100", "CNT-1", "2026-09-25")

    rem = sm.get_or_create_reminder("REM-100")
    assert rem["simulation_state"]["status"] == "SIMULATED"
    assert rem["simulation_state"]["simulation_count"] == 3

    # Live state must still be PENDING and eligible
    assert rem["live_state"]["status"] == "PENDING"
    assert sm.is_eligible_for_live("REM-100") is True


def test_state_transitions(tmp_path):
    state_file = tmp_path / "test_state.json"
    sm = StateManager(state_file_path=state_file)

    sm.set_live_processing("REM-200")
    assert sm.get_live_status("REM-200") == "PROCESSING"
    assert sm.is_eligible_for_live("REM-200") is False

    sm.record_live_accepted("REM-200", "DEL-999", "250 OK")
    assert sm.get_live_status("REM-200") == "SMTP_ACCEPTED"
    assert sm.is_eligible_for_live("REM-200") is False


def test_quarantine_ambiguous_outcome(tmp_path):
    state_file = tmp_path / "test_state.json"
    sm = StateManager(state_file_path=state_file)

    # Set record to PROCESSING (simulating crash before server reply was persisted)
    sm.set_live_processing("REM-CRASH")
    assert sm.get_live_status("REM-CRASH") == "PROCESSING"

    # Reinitialize StateManager (simulating process restart)
    sm_recovered = StateManager(state_file_path=state_file)

    # Orphaned record must be quarantined as REQUIRES_REVIEW
    assert sm_recovered.get_live_status("REM-CRASH") == "REQUIRES_REVIEW"
    assert sm_recovered.is_eligible_for_live("REM-CRASH") is False


def test_resolve_quarantined_record(tmp_path):
    state_file = tmp_path / "test_state.json"
    sm = StateManager(state_file_path=state_file)

    sm.record_requires_review("REM-Q", "Timeout during DATA")
    assert sm.get_live_status("REM-Q") == "REQUIRES_REVIEW"

    # Resolve to SMTP_ACCEPTED
    success = sm.resolve_quarantined_record("REM-Q", "SMTP_ACCEPTED")
    assert success is True
    assert sm.get_live_status("REM-Q") == "SMTP_ACCEPTED"

    # Invalid resolution target must raise ValueError
    with pytest.raises(ValueError):
        sm.resolve_quarantined_record("REM-Q", "INVALID_STATUS")


def test_retry_taxonomy_limit(tmp_path):
    state_file = tmp_path / "test_state.json"
    sm = StateManager(state_file_path=state_file)

    # Retryable errors: retry count increments up to MAX_RETRIES (3 retries after initial)
    sm.record_live_failure("REM-RETRY", "DNS drop", is_retryable=True)
    assert sm.get_live_status("REM-RETRY") == "PENDING"
    assert sm.state["reminders"]["REM-RETRY"]["live_state"]["retry_count"] == 1

    sm.record_live_failure("REM-RETRY", "DNS drop", is_retryable=True)
    assert sm.state["reminders"]["REM-RETRY"]["live_state"]["retry_count"] == 2

    sm.record_live_failure("REM-RETRY", "DNS drop", is_retryable=True)
    assert sm.state["reminders"]["REM-RETRY"]["live_state"]["retry_count"] == 3

    # 4th failure (exceeding MAX_RETRIES=3) transitions to FAILED
    sm.record_live_failure("REM-RETRY", "DNS drop", is_retryable=True)
    assert sm.get_live_status("REM-RETRY") == "FAILED"
    assert sm.is_eligible_for_live("REM-RETRY") is False
