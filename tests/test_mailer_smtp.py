"""
Unit tests for SMTP Mailer, STARTTLS, and Failure Taxonomy (Module 4/9).
"""

import smtplib
import socket
from unittest.mock import MagicMock, patch
import pytest
from src.mailer import Mailer, classify_smtp_error


def test_dry_run_no_network():
    mailer = Mailer(dry_run=True)
    res = mailer.dispatch(
        reminder_id="REM-DRY-1",
        recipient_email="user@example.com",
        subject="Test Dry Run",
        plain_body="Plain text",
        html_body="<p>Html</p>",
    )
    assert res["status"] == "SIMULATED"
    assert res["delivery_id"].startswith("DEL-")
    assert res["error"] is None


def test_failure_taxonomy_permanent():
    auth_err = smtplib.SMTPAuthenticationError(535, b"Authentication credentials invalid")
    cat, retryable = classify_smtp_error(auth_err)
    assert cat == "PERMANENT"
    assert retryable is False

    refused_err = smtplib.SMTPRecipientsRefused({"bad@domain.com": (550, b"User unknown")})
    cat, retryable = classify_smtp_error(refused_err)
    assert cat == "PERMANENT"
    assert retryable is False


def test_failure_taxonomy_retryable():
    dns_err = socket.gaierror("Name or service not known")
    cat, retryable = classify_smtp_error(dns_err)
    assert cat == "RETRYABLE"
    assert retryable is True

    transient_err = smtplib.SMTPResponseException(421, b"Service temporarily unavailable")
    cat, retryable = classify_smtp_error(transient_err)
    assert cat == "RETRYABLE"
    assert retryable is True


def test_failure_taxonomy_ambiguous():
    ambig_err = Exception("broken pipe during socket send")
    cat, retryable = classify_smtp_error(ambig_err)
    assert cat == "AMBIGUOUS"
    assert retryable is False


def test_live_dispatch_missing_credentials(monkeypatch):
    # Temporarily clear credentials
    monkeypatch.setattr("src.config.SMTP_USER", "")
    monkeypatch.setattr("src.config.SMTP_PASSWORD", "")

    mailer = Mailer(dry_run=False)
    res = mailer.dispatch(
        reminder_id="REM-NO-CRED",
        recipient_email="user@example.com",
        subject="No creds",
        plain_body="text",
        html_body="html",
    )
    assert res["status"] == "FAILED"
    assert "must be configured" in res["error"]


@patch("smtplib.SMTP")
def test_live_dispatch_mock_success(mock_smtp, monkeypatch):
    # Mock credentials in config
    monkeypatch.setattr("src.config.SMTP_USER", "sender@example.com")
    monkeypatch.setattr("src.config.SMTP_PASSWORD", "app_password_1234")

    # Setup SMTP mock instance
    mock_instance = MagicMock()
    mock_smtp.return_value = mock_instance

    mailer = Mailer(dry_run=False, rate_limit_delay=0.0)
    res = mailer.dispatch(
        reminder_id="REM-MOCK-1",
        recipient_email="recipient@example.com",
        subject="Live Mock Test",
        plain_body="text",
        html_body="<p>html</p>",
    )

    assert res["status"] == "SMTP_ACCEPTED"
    assert "250 OK" in res["response"]
    assert mock_instance.starttls.called
    assert mock_instance.login.called
    assert mock_instance.sendmail.called or mock_instance.send_message.called
