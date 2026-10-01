"""
Security, Fault-Tolerance & Resilience Regression Test Suite.
Validates remediation of audit defects DEF-01 through DEF-11:
- DEF-01: HTML Injection / XSS Prevention in Email Templates
- DEF-02: Email Header & CRLF Injection Prevention
- DEF-03: CSV Formula Injection Prevention (CWE-1236)
- DEF-04 & DEF-10: Ambiguous MTA Data-Phase Quarantine & SMTP Taxonomy
- DEF-05 & DEF-06: UTF-8 BOM Handling & Duplicate Key Deduplication
- DEF-07: Corrupted State Store Automatic Backup & Recovery
- DEF-08: Pandas NaN String Conversion to "N/A"
- DEF-09: Date & Time ISO Format Validation
- DEF-11: Atomic Operational JSON Export
"""

import json
import socket
import ssl
import smtplib
from pathlib import Path
import pytest
import pandas as pd
import numpy as np

from src.csv_loader import validate_email_syntax, load_and_merge_datasets
from src.template_engine import (
    render_template,
    build_mime_message,
    sanitize_placeholder_value,
)
from src.state_manager import StateManager
from src.mailer import classify_smtp_error
from src.reporter import Reporter, sanitize_csv_cell
from src.scheduler import get_due_reminders, setup_schedule
from src.json_exporter import export_operational_dashboard_data


# ---------------------------------------------------------------------------
# DEF-01: HTML Injection / XSS in Email Templates
# ---------------------------------------------------------------------------
def test_xss_prevention_in_html_email_template():
    """XSS payloads in placeholder values must be HTML-escaped in HTML bodies."""
    xss_payload = "<script>alert('xss')</script><img src=x onerror=alert(1)>"
    placeholders = {
        "recipient_name": xss_payload,
        "due_date": "2026-09-30",
        "description": "Security Review",
    }

    # HTML body rendering must escape HTML special characters
    rendered_html = render_template(
        "Hello {recipient_name}, review: {description}",
        placeholders,
        escape_html=True,
    )
    assert "<script>" not in rendered_html
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in rendered_html
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered_html

    # Plain text rendering must preserve characters naturally without escaping
    rendered_text = render_template(
        "Hello {recipient_name}",
        placeholders,
        escape_html=False,
    )
    assert xss_payload in rendered_text


def test_dangerous_url_sanitization_in_templates():
    """Dangerous URL schemes in meeting links must be neutralized to '#'."""
    assert sanitize_placeholder_value("meeting_link", "javascript:alert(1)") == "#"
    assert sanitize_placeholder_value("meeting_link", "data:text/html,<script>alert(1)</script>") == "#"
    assert sanitize_placeholder_value("meeting_link", "file:///etc/passwd") == "#"
    assert sanitize_placeholder_value("meeting_link", "https://meet.google.com/abc-defg-hij") == "https://meet.google.com/abc-defg-hij"
    assert sanitize_placeholder_value("meeting_link", "http://example.com/meeting") == "http://example.com/meeting"


# ---------------------------------------------------------------------------
# DEF-02: Email Header & CRLF Injection Prevention
# ---------------------------------------------------------------------------
def test_crlf_rejection_in_email_syntax():
    """Email validation must strictly reject addresses with CRLF or tab characters."""
    assert validate_email_syntax("user@example.com\r\nBcc: evil@attacker.com") is False
    assert validate_email_syntax("user@example.com\nevil@attacker.com") is False
    assert validate_email_syntax("user@example.com\r") is False
    assert validate_email_syntax("user\t@example.com") is False


def test_crlf_header_injection_stripped_in_mime():
    """build_mime_message must strip newlines from subject to prevent header injection."""
    msg, plain, html, subj = build_mime_message(
        reminder_type="general",
        context={"event_title": "Project Update"},
        recipient_email="user@example.com\r\nBcc: evil@attacker.com",
        custom_subject="Project Update\r\nBcc: victim@leak.org\r\nX-Spam: High",
    )
    # The subject header must not contain raw CRLF or multi-line injection
    assert "\r" not in msg["Subject"]
    assert "\n" not in msg["Subject"]
    assert "Project Update Bcc: victim@leak.org X-Spam: High" == msg["Subject"]
    # The To header must also be sanitized of CRLF
    assert "\r" not in msg["To"]
    assert "\n" not in msg["To"]


# ---------------------------------------------------------------------------
# DEF-03: CSV Formula Injection Prevention (CWE-1236)
# ---------------------------------------------------------------------------
def test_csv_formula_injection_escaping():
    """Formulas starting with =, +, -, @, tab, or CR must be escaped with a single quote."""
    assert sanitize_csv_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert sanitize_csv_cell("+12345") == "'+12345"
    assert sanitize_csv_cell("-2+3") == "'-2+3"
    assert sanitize_csv_cell("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_cell("\tDDE") == "'\tDDE"
    assert sanitize_csv_cell("Normal Text") == "Normal Text"
    assert sanitize_csv_cell(12345) == 12345


def test_reporter_export_csv_escapes_formulas(tmp_path):
    """CSV report export must sanitize formula injection cells."""
    reporter = Reporter(outputs_dir=tmp_path, logs_dir=tmp_path)
    reporter.record_dispatch(
        reminder_id="REM-9999",
        recipient="user@example.com",
        department="Finance",
        reminder_type="general",
        subject="=cmd|'/C calc'!A0",
        status="SIMULATED",
        delivery_id="SIM-1234",
        notes="Safe note",
    )
    report_file = reporter.export_csv_report()

    content = report_file.read_text(encoding="utf-8")
    assert "'=cmd|'/C calc'!A0" in content


# ---------------------------------------------------------------------------
# DEF-04 & DEF-10: SMTP Error Taxonomy & Ambiguous Data-Phase Quarantine
# ---------------------------------------------------------------------------
def test_classify_smtp_error_data_phase_ambiguity():
    """Socket timeouts and disconnects during DATA transmission must be AMBIGUOUS and not retried."""
    sock_timeout = socket.timeout("timed out")
    cat, retryable = classify_smtp_error(sock_timeout, in_data_phase=True)
    assert cat == "AMBIGUOUS"
    assert retryable is False

    # Pre-data timeout is RETRYABLE
    cat_predata, retryable_predata = classify_smtp_error(sock_timeout, in_data_phase=False)
    assert cat_predata == "RETRYABLE"
    assert retryable_predata is True


def test_classify_smtp_error_security_exceptions():
    """TLS and SSL certificate errors must be classified as PERMANENT and non-retryable."""
    ssl_err = ssl.SSLError("certificate verify failed: self-signed certificate")
    cat, retryable = classify_smtp_error(ssl_err)
    assert cat == "PERMANENT"
    assert retryable is False

    not_supported = smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server")
    cat_sup, retryable_sup = classify_smtp_error(not_supported)
    assert cat_sup == "PERMANENT"
    assert retryable_sup is False


# ---------------------------------------------------------------------------
# DEF-05 & DEF-06: UTF-8 BOM Handling & Duplicate Key Deduplication
# ---------------------------------------------------------------------------
def test_utf8_bom_and_duplicate_deduplication(tmp_path):
    """CSV loader must strip UTF-8 BOM and deduplicate repeated IDs without join explosion."""
    contacts_file = tmp_path / "contacts.csv"
    reminders_file = tmp_path / "reminders.csv"

    # Write contacts with UTF-8 BOM and duplicate contact_id
    contacts_content = (
        "\ufeffcontact_id,name,email,department,preferred_time\n"
        "C-01,Alice Smith,alice@example.com,Finance,09:00\n"
        "C-01,Alice Smith Duplicate,alice@example.com,Finance,09:00\n"
        "C-02,Bob Jones,bob@example.com,Engineering,10:00\n"
    )
    contacts_file.write_text(contacts_content, encoding="utf-8")

    # Write reminders with duplicate reminder_id
    reminders_content = (
        "reminder_id,contact_id,reminder_type,due_date,status,description,meeting_link\n"
        "R-01,C-01,invoice,2026-09-30,pending,Invoice 101,\n"
        "R-01,C-01,invoice,2026-09-30,pending,Invoice 101 duplicate,\n"
        "R-02,C-02,meeting,2026-09-30,pending,Sync,https://meet.google.com/xyz\n"
        "R-03,C-99,general,2026-09-30,pending,Orphan reminder,\n"
    )
    reminders_file.write_text(reminders_content, encoding="utf-8")

    df = load_and_merge_datasets(contacts_file, reminders_file)

    # Exactly 2 matched reminders (R-01 deduplicated, R-02 valid; R-03 has missing contact)
    assert len(df) == 2
    assert set(df["reminder_id"]) == {"R-01", "R-02"}
    # Contact ID column must be clean (no BOM character)
    assert "contact_id" in df.columns
    assert "\ufeffcontact_id" not in df.columns


# ---------------------------------------------------------------------------
# DEF-07: Corrupted State Store Automatic Backup & Recovery
# ---------------------------------------------------------------------------
def test_corrupted_state_file_automatic_backup(tmp_path):
    """When a state file contains invalid JSON, StateManager must back it up and recover cleanly."""
    state_file = tmp_path / "reminder_state.json"
    corrupted_data = "{ invalid_json_syntax: true, unclosed... "
    state_file.write_text(corrupted_data, encoding="utf-8")

    # Instantiate StateManager with corrupted file
    manager = StateManager(state_file_path=state_file)

    # State manager should initialize cleanly with default schema
    assert "reminders" in manager.state
    assert manager.state.get("version") == "1.0"

    # A backup file must have been generated
    backup_files = list(tmp_path.glob("reminder_state.json.corrupted_*"))
    assert len(backup_files) == 1
    assert backup_files[0].read_text(encoding="utf-8") == corrupted_data


# ---------------------------------------------------------------------------
# DEF-08: Pandas NaN Value Sanitization in Templates
# ---------------------------------------------------------------------------
def test_nan_placeholder_sanitization():
    """Float NaN and 'nan' strings must be sanitized to 'N/A'."""
    assert sanitize_placeholder_value("amount", float("nan")) == "N/A"
    assert sanitize_placeholder_value("amount", np.nan) == "N/A"
    assert sanitize_placeholder_value("amount", "NaN") == "N/A"
    assert sanitize_placeholder_value("amount", "nan") == "N/A"
    assert sanitize_placeholder_value("amount", None) == "N/A"
    assert sanitize_placeholder_value("amount", "Valid String") == "Valid String"

    template = "Invoice: {invoice_id} | Amount: {amount}"
    rendered = render_template(template, {"invoice_id": np.nan, "amount": float("nan")})
    assert rendered == "Invoice: N/A | Amount: N/A"


# ---------------------------------------------------------------------------
# DEF-09: Date & Time ISO Format Validation in Scheduler
# ---------------------------------------------------------------------------
def test_scheduler_cutoff_date_validation(tmp_path):
    """Scheduler get_due_reminders must reject non-ISO dates."""
    state_mgr = StateManager(state_file_path=tmp_path / "sched_state.json")
    df = pd.DataFrame({
        "reminder_id": ["R-1"],
        "due_date": ["2026-09-30"],
        "reminder_type": ["invoice"],
        "is_valid_email": [True],
    })

    with pytest.raises(ValueError, match="Invalid target_date format"):
        get_due_reminders(df, state_manager=state_mgr, target_date="09-30-2026")

    with pytest.raises(ValueError, match="Invalid target_date format"):
        get_due_reminders(df, state_manager=state_mgr, target_date="2026/09/30")

    with pytest.raises(ValueError, match="Invalid target_date format"):
        get_due_reminders(df, state_manager=state_mgr, target_date="not-a-date")

    # Valid ISO date must succeed
    result = get_due_reminders(df, state_manager=state_mgr, target_date="2026-09-30")
    assert len(result) == 1


def test_scheduler_run_at_time_validation():
    """Scheduler setup_schedule must reject non-24h HH:MM times."""
    df = pd.DataFrame({"reminder_id": ["R-1"], "due_date": ["2026-09-30"]})

    with pytest.raises(ValueError, match="Invalid run_at_time format"):
        setup_schedule(df, lambda x: None, run_at_time="25:00")

    with pytest.raises(ValueError, match="Invalid run_at_time format"):
        setup_schedule(df, lambda x: None, run_at_time="9am")

    with pytest.raises(ValueError, match="Invalid run_at_time format"):
        setup_schedule(df, lambda x: None, run_at_time="12:61")


# ---------------------------------------------------------------------------
# DEF-11: Atomic JSON Exporter Write
# ---------------------------------------------------------------------------
def test_json_exporter_atomic_write(tmp_path):
    """Operational JSON exporter must write atomically and leave no tmp artifacts."""
    output_file = tmp_path / "operational_dashboard.json"
    state_file = tmp_path / "test_state.json"
    state_mgr = StateManager(state_file_path=state_file)

    df = pd.DataFrame({
        "contact_id": ["C-1", "C-2"],
        "reminder_id": ["R-1", "R-2"],
        "reminder_type": ["invoice", "meeting"],
        "department": ["Finance", "Engineering"],
        "is_valid_email": [True, True],
    })

    result_path = export_operational_dashboard_data(state_mgr, df, output_path=output_file)
    assert result_path.exists()
    assert result_path == output_file

    # File should be valid JSON
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["kpis"]["total_contacts"] == 2
    assert data["kpis"]["total_reminders"] == 2

    # Ensure no leftover temporary files in directory
    temp_files = list(tmp_path.glob("*.tmp_*"))
    assert len(temp_files) == 0
