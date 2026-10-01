"""
Unit tests for Operational Dashboard JSON Exporter (Module 7/9).
"""

import json
import pandas as pd
import pytest
from src.json_exporter import export_operational_dashboard_data
from src.state_manager import StateManager


@pytest.fixture
def mock_operational_env(tmp_path):
    sm = StateManager(state_file_path=tmp_path / "test_state.json")
    df = pd.DataFrame([
        {"contact_id": "C1", "reminder_id": "R1", "reminder_type": "meeting", "department": "Finance"},
        {"contact_id": "C2", "reminder_id": "R2", "reminder_type": "payment", "department": "HR"},
        {"contact_id": "C3", "reminder_id": "R3", "reminder_type": "webinar", "department": "Operations"},
    ])

    # Record states: R1 simulated, R2 accepted live, R3 requires review
    sm.record_simulation_success("R1", "C1")
    sm.record_live_accepted("R2", "DEL-1", "250 OK")
    sm.record_requires_review("R3", "Timeout during DATA")

    return sm, df, tmp_path


def test_export_operational_dashboard_data_structure(mock_operational_env):
    sm, df, tmp_path = mock_operational_env
    output_file = tmp_path / "operational_export.json"

    result_path = export_operational_dashboard_data(sm, df, output_path=output_file)
    assert result_path.exists()

    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert "system_info" in data
    assert "kpis" in data
    assert "distribution_by_type" in data
    assert "distribution_by_status" in data
    assert "distribution_by_department" in data


def test_kpis_calculation(mock_operational_env):
    sm, df, tmp_path = mock_operational_env
    output_file = tmp_path / "operational_export.json"

    export_operational_dashboard_data(sm, df, output_path=output_file)
    data = json.loads(output_file.read_text(encoding="utf-8"))
    kpis = data["kpis"]

    assert kpis["total_contacts"] == 3
    assert kpis["total_reminders"] == 3
    assert kpis["simulated_count"] == 1
    assert kpis["smtp_accepted_count"] == 1
    assert kpis["requires_review_count"] == 1
    assert kpis["submission_rate_percent"] == 100.0


def test_empty_dataframe_handled(tmp_path):
    sm = StateManager(state_file_path=tmp_path / "empty_state.json")
    empty_df = pd.DataFrame(columns=["contact_id", "reminder_id", "reminder_type", "department"])
    output_file = tmp_path / "empty_export.json"

    export_operational_dashboard_data(sm, empty_df, output_path=output_file)
    data = json.loads(output_file.read_text(encoding="utf-8"))

    assert data["kpis"]["total_contacts"] == 0
    assert data["kpis"]["total_reminders"] == 0
    assert data["kpis"]["submission_rate_percent"] == 0.0
