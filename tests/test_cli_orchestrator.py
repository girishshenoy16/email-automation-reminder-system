"""
Automated Integration Tests for main.py CLI Orchestrator.
Tests command-line parsing, mutually exclusive flags, dry-run vs live dispatch,
quarantine resolution, dashboard export, and scheduler execution.
"""

import json
import subprocess
import sys
from pathlib import Path
import pytest
from src.reporter import close_system_logging
from src.state_manager import StateManager


@pytest.fixture(autouse=True)
def teardown_logging():
    """Ensure handlers are closed between test invocations."""
    yield
    close_system_logging()


def test_cli_help():
    """Verifies that main.py --help returns exit code 0 and includes key usage documentation."""
    res = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0
    assert "Enterprise Email Automation & Reminder System" in res.stdout
    assert "--dry-run" in res.stdout
    assert "--live" in res.stdout
    assert "--mode" in res.stdout


def test_cli_mutually_exclusive_dry_run_and_live():
    """Verifies that passing both --dry-run and --live raises a CLI argument validation error."""
    res = subprocess.run(
        [sys.executable, "main.py", "--dry-run", "--live"],
        capture_output=True,
        text=True
    )
    assert res.returncode != 0
    assert "not allowed with argument" in res.stderr.lower() or "error" in res.stderr.lower()


def test_cli_instant_dry_run_execution(tmp_path):
    """Verifies that main.py --mode instant runs in safe dry-run mode and updates simulation state."""
    state_file = tmp_path / "test_dry_state.json"
    res = subprocess.run(
        [sys.executable, "main.py", "--mode", "instant", "--type", "meeting", "--state-file", str(state_file)],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0
    assert "DRY-RUN SIMULATION" in res.stdout
    assert "Executing DRY-RUN SIMULATION Batch" in res.stdout
    assert state_file.exists()


def test_cli_quarantine_resolution(tmp_path):
    """Verifies manual quarantine resolution via CLI using an isolated test state file."""
    state_path = tmp_path / "quarantine_state.json"
    sm = StateManager(state_path)
    # Seed reminder and put in quarantine
    sm.get_or_create_reminder("REM-TEST-RESOLVE", contact_id="CNT-01", due_date="2026-09-25")
    sm.record_requires_review("REM-TEST-RESOLVE", "Simulated mid-DATA network drop")
    assert sm.get_live_status("REM-TEST-RESOLVE") == "REQUIRES_REVIEW"

    # Resolve via CLI
    res = subprocess.run(
        [
            sys.executable, "main.py",
            "--resolve", "REM-TEST-RESOLVE",
            "--status", "accepted",
            "--state-file", str(state_path)
        ],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0
    assert "successfully resolved" in res.stdout

    # Verify state is updated in the isolated state store
    sm_check = StateManager(state_path)
    assert sm_check.get_live_status("REM-TEST-RESOLVE") == "SMTP_ACCEPTED"


def test_cli_quarantine_resolution_missing_status():
    """Verifies error handling when --resolve is passed without --status."""
    res = subprocess.run(
        [sys.executable, "main.py", "--resolve", "REM-1001"],
        capture_output=True,
        text=True
    )
    assert res.returncode != 0
    assert "--status" in res.stderr or "--status" in res.stdout


def test_cli_export_dashboard(tmp_path):
    """Verifies that --export-dashboard creates outputs/operational_dashboard_data.json."""
    state_file = tmp_path / "export_dash_state.json"
    res = subprocess.run(
        [sys.executable, "main.py", "--mode", "instant", "--export-dashboard", "--state-file", str(state_file)],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0
    operational_path = Path("outputs/operational_dashboard_data.json")
    with open(operational_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "kpis" in data
    assert "total_contacts" in data["kpis"]
    assert "distribution_by_type" in data
