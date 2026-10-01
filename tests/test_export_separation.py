"""
Unit tests for Public/Private Export Boundary Separation (Module 8/9).
Verifies that operational export routines cannot overwrite or modify the public fixture in docs/.
"""

import pandas as pd
import pytest
from src import config
from src.json_exporter import assert_export_boundary, export_operational_dashboard_data
from src.state_manager import StateManager


def test_assert_export_boundary_rejects_docs_path():
    # Attempting to target the public fixture directly must fail
    with pytest.raises(PermissionError, match="Public/Private Separation Violation"):
        assert_export_boundary(config.PUBLIC_FIXTURE_PATH)

    # Attempting to target any path inside docs/ must fail
    with pytest.raises(PermissionError, match="Public/Private Separation Violation"):
        assert_export_boundary(config.DOCS_DIR / "some_file.json")


def test_assert_export_boundary_allows_outputs_path():
    # Targeting outputs/ must pass cleanly
    assert_export_boundary(config.OPERATIONAL_EXPORT_PATH)
    assert_export_boundary(config.OUTPUTS_DIR / "custom_report.json")


def test_operational_export_leaves_public_fixture_untouched(tmp_path):
    sm = StateManager(state_file_path=tmp_path / "temp_state.json")
    df = pd.DataFrame([{"contact_id": "C1", "reminder_id": "R1", "reminder_type": "meeting", "department": "HR"}])

    # If the public fixture exists, record its modification time or content
    fixture_content_before = None
    if config.PUBLIC_FIXTURE_PATH.exists():
        fixture_content_before = config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8")

    # Run operational export to its legitimate destination in outputs/
    operational_dest = tmp_path / "operational_dashboard_data.json"
    export_operational_dashboard_data(sm, df, output_path=operational_dest)

    assert operational_dest.exists()

    # Verify public fixture was NOT modified
    if fixture_content_before is not None:
        fixture_content_after = config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8")
        assert fixture_content_before == fixture_content_after
