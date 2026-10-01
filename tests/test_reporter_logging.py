"""
Unit tests for Operational Logging, Sanitization, and CSV Audit Reporter.
Tests setup_system_logging, mask_email sanitization, log level filtering,
handler cleanup, and CSV export.
"""

import logging
from pathlib import Path
import pytest
from src.reporter import (
    Reporter,
    close_system_logging,
    mask_email,
    setup_system_logging,
)


@pytest.fixture(autouse=True)
def cleanup_logging():
    """Ensure logging handlers are flushed and closed after each test."""
    yield
    close_system_logging()


def test_mask_email_sanitization():
    """Verifies email address masking to protect private recipient data."""
    assert mask_email("alex.morgan@example.com") == "a***@example.com"
    assert mask_email("priya.sharma@domain.org") == "p***@domain.org"
    assert mask_email("x@test.com") == "*@test.com"
    assert mask_email("invalid_email") == "***"
    assert mask_email("") == "***"
    assert mask_email(None) == "***"


def test_setup_system_logging_creates_file_and_logs(tmp_path):
    """Verifies that setup_system_logging creates log file and writes structured entries."""
    log_file = tmp_path / "test_exec.log"
    setup_system_logging(log_file_path=log_file, log_level="INFO", enable_console=False)

    test_logger = logging.getLogger("test_module")
    test_logger.info("Operational event executed successfully")

    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()

    assert log_file.exists(), "Log file must be created on disk"
    content = log_file.read_text(encoding="utf-8")
    assert "[INFO]" in content
    assert "[test_module]" in content
    assert "Operational event executed successfully" in content


def test_setup_system_logging_level_filtering(tmp_path):
    """Verifies that configured log level is strictly respected."""
    log_file = tmp_path / "level_test.log"
    setup_system_logging(log_file_path=log_file, log_level="WARNING", enable_console=False)

    test_logger = logging.getLogger("level_module")
    test_logger.debug("Debug event that should be filtered out")
    test_logger.info("Info event that should be filtered out")
    test_logger.warning("Warning event that should appear")

    for h in logging.getLogger().handlers:
        h.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "Debug event" not in content
    assert "Info event" not in content
    assert "Warning event that should appear" in content


def test_no_duplicate_handlers_on_repeated_setup(tmp_path):
    """Verifies that repeated setup_system_logging calls do not accumulate duplicate handlers."""
    log1 = tmp_path / "log1.log"
    log2 = tmp_path / "log2.log"

    setup_system_logging(log_file_path=log1, log_level="INFO", enable_console=False)
    setup_system_logging(log_file_path=log2, log_level="INFO", enable_console=False)

    system_file_handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename).name in ("log1.log", "log2.log")
    ]
    assert len(system_file_handlers) == 1, "Only one system FileHandler should remain active"
    assert Path(system_file_handlers[0].baseFilename).resolve() == log2.resolve()


def test_reporter_record_dispatch_masks_recipient_in_log(tmp_path):
    """Verifies that Reporter.record_dispatch masks recipient emails in the persistent log."""
    out_dir = tmp_path / "outputs"
    log_dir = tmp_path / "logs"

    reporter = Reporter(outputs_dir=out_dir, logs_dir=log_dir, log_level="INFO")
    reporter.record_dispatch(
        reminder_id="REM-999",
        recipient="confidential.employee@enterprise.com",
        department="Finance",
        reminder_type="Payment",
        subject="Invoice Notification",
        status="SMTP_ACCEPTED",
        delivery_id="DEL-TEST01",
        notes="Handoff confirmed"
    )

    log_file = log_dir / "email_system.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")

    # Raw recipient email must NOT appear in logs
    assert "confidential.employee@enterprise.com" not in content
    # Masked recipient must appear
    assert "c***@enterprise.com" in content
    assert "REM-999" in content
    assert "SMTP_ACCEPTED" in content


def test_reporter_export_csv_report(tmp_path):
    """Verifies that Reporter exports a structured audit CSV report in outputs/."""
    out_dir = tmp_path / "outputs"
    log_dir = tmp_path / "logs"

    reporter = Reporter(outputs_dir=out_dir, logs_dir=log_dir)
    reporter.record_dispatch(
        reminder_id="REM-101",
        recipient="user1@example.com",
        department="Operations",
        reminder_type="Meeting",
        subject="Meeting Reminder",
        status="SIMULATED",
        delivery_id="DEL-SIM01"
    )

    report_path = reporter.export_csv_report()
    assert report_path.exists()
    assert report_path.parent.resolve() == out_dir.resolve()

    content = report_path.read_text(encoding="utf-8")
    assert "reminder_id,recipient,department,reminder_type,subject,status,delivery_id,notes" in content
    assert "REM-101" in content
    assert "user1@example.com" in content
    assert "SIMULATED" in content


def test_close_system_logging_releases_file_locks(tmp_path):
    """Verifies that close_system_logging releases file handles cleanly on Windows."""
    log_file = tmp_path / "lock_test.log"
    setup_system_logging(log_file_path=log_file, log_level="INFO", enable_console=False)

    logging.info("Writing before close")
    close_system_logging()

    # On Windows, open file handles prevent deletion or renaming
    # This must succeed without PermissionError
    log_file.unlink()
    assert not log_file.exists()


def test_logging_failure_does_not_abort_record_dispatch(tmp_path, monkeypatch, capsys):
    """Verifies that an unexpected exception in the logging layer does not abort dispatch recording and emits fallback."""
    out_dir = tmp_path / "outputs"
    log_dir = tmp_path / "logs"

    reporter = Reporter(outputs_dir=out_dir, logs_dir=log_dir)

    # Simulate logger failure (e.g. disk write failure or formatter crash)
    def crash_logger(*args, **kwargs):
        raise OSError("Simulated disk full or I/O failure")

    monkeypatch.setattr("logging.Logger.info", crash_logger)

    # Must complete safely without raising
    reporter.record_dispatch(
        reminder_id="REM-RESILIENT",
        recipient="resilient@example.com",
        department="Engineering",
        reminder_type="Deployment",
        subject="Resilience Test",
        status="SMTP_ACCEPTED",
        delivery_id="DEL-RES-01",
        notes="Testing logging fault tolerance"
    )

    # Session record must still be intact
    assert len(reporter.session_records) == 1
    rec = reporter.session_records[0]
    assert rec["reminder_id"] == "REM-RESILIENT"
    assert rec["status"] == "SMTP_ACCEPTED"
    assert rec["delivery_id"] == "DEL-RES-01"

    # Diagnostic fallback must be emitted to stderr, not silently concealed
    captured = capsys.readouterr()
    assert "[LOGGING SUBSYSTEM WARNING]" in captured.err


def test_close_system_logging_preserves_unrelated_handlers(tmp_path):
    """Verifies that closing system logging does NOT remove third-party or test handlers on root logger."""
    root_logger = logging.getLogger()

    # Create and attach a mock third-party / external application handler
    external_handler = logging.NullHandler()
    root_logger.addHandler(external_handler)

    try:
        # Setup system logging
        log_file = tmp_path / "sys_test.log"
        setup_system_logging(log_file_path=log_file, log_level="INFO", enable_console=False)

        # External handler must still be present while system logger is active
        assert external_handler in root_logger.handlers

        # Close system logging
        close_system_logging()

        # External handler must STILL be present after system logger cleanup
        assert external_handler in root_logger.handlers
    finally:
        if external_handler in root_logger.handlers:
            root_logger.removeHandler(external_handler)
