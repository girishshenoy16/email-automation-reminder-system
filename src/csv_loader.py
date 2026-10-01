"""
CSV Ingestion and Email Address Syntax Validation Module.
Handles schema validation, dataset relational joins, and regex email validation.
"""

import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import pandas as pd
from src import config

logger = logging.getLogger(__name__)

# Standard email address syntax regex pattern (user@domain.tld)
# Strictly disallows whitespace, control characters, or newlines
EMAIL_REGEX_PATTERN = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)

REQUIRED_CONTACT_COLUMNS = {"contact_id", "name", "email"}
REQUIRED_REMINDER_COLUMNS = {"reminder_id", "contact_id", "reminder_type", "due_date"}


def validate_email_syntax(email: str) -> bool:
    """
    Validates standard email address syntax.
    Returns True if valid structure, False otherwise.
    Strictly rejects newlines, carriage returns, or control characters.
    """
    if not isinstance(email, str) or not email.strip():
        return False
    if any(c in email for c in ("\r", "\n", "\t", " ")):
        return False
    clean_email = email.strip()
    return bool(EMAIL_REGEX_PATTERN.match(clean_email))


def validate_iso_date(date_str: str) -> bool:
    """Validates that a string conforms to calendar ISO YYYY-MM-DD format."""
    try:
        datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def load_contacts(file_path: Optional[Path] = None) -> pd.DataFrame:
    """
    Loads contacts CSV with UTF-8 BOM compatibility, validates required schema,
    and deduplicates by contact_id to prevent cartesian product anomalies.
    """
    path = file_path or config.CONTACTS_FILE_PATH
    if not Path(path).exists():
        raise FileNotFoundError(f"Contacts file not found: {path}")

    # Use utf-8-sig to automatically handle Excel UTF-8 Byte Order Marks (BOM)
    df = pd.read_csv(path, encoding="utf-8-sig")

    if df.empty:
        raise ValueError(f"Contacts CSV '{Path(path).name}' contains no rows.")

    missing = REQUIRED_CONTACT_COLUMNS - set(df.columns)
    if missing:
        logger.error(f"Contacts CSV missing required columns: {missing}")
        raise ValueError(f"Contacts CSV is missing required columns: {missing}")

    # Check for duplicate contact_id entries to prevent join explosion
    if df["contact_id"].duplicated().any():
        dup_ids = df[df["contact_id"].duplicated()]["contact_id"].tolist()
        logger.warning(
            f"Duplicate contact_id detected in {Path(path).name}: {dup_ids}. "
            f"Deduplicating and retaining first occurrence."
        )
        df = df.drop_duplicates(subset=["contact_id"], keep="first").copy()

    logger.info(f"Loaded {len(df)} contacts from {Path(path).name}")
    return df


def load_reminders(file_path: Optional[Path] = None) -> pd.DataFrame:
    """
    Loads reminders CSV with UTF-8 BOM compatibility, validates required schema,
    deduplicates by reminder_id, and checks due_date syntax.
    """
    path = file_path or config.REMINDERS_FILE_PATH
    if not Path(path).exists():
        raise FileNotFoundError(f"Reminders file not found: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig")

    if df.empty:
        raise ValueError(f"Reminders CSV '{Path(path).name}' contains no rows.")

    missing = REQUIRED_REMINDER_COLUMNS - set(df.columns)
    if missing:
        logger.error(f"Reminders CSV missing required columns: {missing}")
        raise ValueError(f"Reminders CSV is missing required columns: {missing}")

    # Check for duplicate reminder_id entries to prevent dual dispatch in the same batch
    if df["reminder_id"].duplicated().any():
        dup_ids = df[df["reminder_id"].duplicated()]["reminder_id"].tolist()
        logger.warning(
            f"Duplicate reminder_id detected in {Path(path).name}: {dup_ids}. "
            f"Deduplicating and retaining first occurrence."
        )
        df = df.drop_duplicates(subset=["reminder_id"], keep="first").copy()

    # Validate ISO date formatting
    invalid_dates = df[~df["due_date"].apply(validate_iso_date)]
    if not invalid_dates.empty:
        logger.warning(
            f"Found {len(invalid_dates)} reminder(s) with non-ISO YYYY-MM-DD due_date: "
            f"{invalid_dates['reminder_id'].tolist()}"
        )

    logger.info(f"Loaded {len(df)} reminders from {Path(path).name}")
    return df


def load_and_merge_datasets(
    contacts_path: Optional[Path] = None,
    reminders_path: Optional[Path] = None
) -> pd.DataFrame:
    """
    Loads and joins contacts and reminders on contact_id.
    Validates email address syntax and flags invalid rows with is_valid_email=False.
    Warns on any orphaned reminders that cannot be matched to a contact.
    """
    contacts_df = load_contacts(contacts_path)
    reminders_df = load_reminders(reminders_path)

    # Clean whitespace in string/object columns
    for col in contacts_df.columns:
        if pd.api.types.is_string_dtype(contacts_df[col]) or pd.api.types.is_object_dtype(contacts_df[col]):
            contacts_df[col] = contacts_df[col].astype(str).str.strip()
    for col in reminders_df.columns:
        if pd.api.types.is_string_dtype(reminders_df[col]) or pd.api.types.is_object_dtype(reminders_df[col]):
            reminders_df[col] = reminders_df[col].astype(str).str.strip()

    # Check for unmatched reminders (orphans)
    unmatched_mask = ~reminders_df["contact_id"].isin(contacts_df["contact_id"])
    if unmatched_mask.any():
        unmatched_ids = reminders_df[unmatched_mask]["reminder_id"].tolist()
        logger.warning(
            f"Unmatched reminder records: {len(unmatched_ids)} reminders could not be linked to any contact: {unmatched_ids}"
        )

    merged_df = pd.merge(reminders_df, contacts_df, on="contact_id", how="inner")

    # Syntax validate emails and annotate dataframe
    merged_df["is_valid_email"] = merged_df["email"].apply(validate_email_syntax)

    invalid_count = int((~merged_df["is_valid_email"]).sum())
    if invalid_count > 0:
        invalid_ids = merged_df[~merged_df["is_valid_email"]]["reminder_id"].tolist()
        logger.warning(
            f"Found {invalid_count} reminder(s) with invalid email address syntax: {invalid_ids}"
        )

    logger.info(
        f"Merged dataset: {len(merged_df)} total records ({len(merged_df) - invalid_count} valid, {invalid_count} invalid email)"
    )
    return merged_df
