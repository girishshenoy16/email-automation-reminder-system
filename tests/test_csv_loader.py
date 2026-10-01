"""
Unit tests for CSV Ingestion and Email Address Syntax Validation (Module 1/9).
"""

import pytest
import pandas as pd
from pathlib import Path
from src.csv_loader import (
    validate_email_syntax,
    load_contacts,
    load_reminders,
    load_and_merge_datasets,
)


def test_validate_email_syntax_valid():
    valid_emails = [
        "alex.morgan@acmecorp.com",
        "user+tag@domain.co.uk",
        "support@example.org",
        "first.last123@sub.domain.io",
    ]
    for email in valid_emails:
        assert validate_email_syntax(email) is True, f"Expected {email} to be valid syntax"


def test_validate_email_syntax_invalid():
    invalid_emails = [
        "plainaddress",
        "@missingusername.com",
        "username@.com",
        "username@com",
        "spaces in@address.com",
        "double@@domain.com",
        "",
        None,
        123,
    ]
    for email in invalid_emails:
        assert validate_email_syntax(email) is False, f"Expected {email} to be invalid syntax"


def test_load_contacts_success():
    df = load_contacts()
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "contact_id" in df.columns
    assert "email" in df.columns
    assert "name" in df.columns


def test_load_contacts_missing_columns(tmp_path):
    bad_csv = tmp_path / "bad_contacts.csv"
    bad_csv.write_text("contact_id,phone\nCNT-1,555-0101\n")
    with pytest.raises(ValueError, match="missing required columns"):
        load_contacts(bad_csv)


def test_load_reminders_missing_columns(tmp_path):
    bad_csv = tmp_path / "bad_reminders.csv"
    bad_csv.write_text("reminder_id,title\nREM-1,Meeting\n")
    with pytest.raises(ValueError, match="missing required columns"):
        load_reminders(bad_csv)


def test_load_and_merge_datasets_flags_invalid():
    df = load_and_merge_datasets()
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "is_valid_email" in df.columns

    # Verify that the known malformed record in contacts.csv is flagged
    invalid_records = df[~df["is_valid_email"]]
    assert len(invalid_records) >= 1
    assert "malformed-address-no-at-sign" in invalid_records["email"].values
