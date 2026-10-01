"""
Central Configuration Module for Email Automation & Reminder System.
Loads environment variables safely and defines project-wide constants and filesystem paths.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base Project Paths (resolved relative to repository root)
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
LOGS_DIR = PROJECT_ROOT / "logs"
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"

# File Specific Paths
STATE_FILE_PATH = DATA_DIR / "reminder_state.json"
CONTACTS_FILE_PATH = DATA_DIR / "contacts.csv"
REMINDERS_FILE_PATH = DATA_DIR / "reminders.csv"
ENRON_SAMPLE_PATH = DATA_DIR / "enron_sample_analysis.csv"

# Public vs Operational Export Destinations (Strict Separation)
PUBLIC_FIXTURE_PATH = DOCS_DATA_DIR / "dashboard_data.json"
OPERATIONAL_EXPORT_PATH = OUTPUTS_DIR / "operational_dashboard_data.json"
LOG_FILE_PATH = LOGS_DIR / "email_system.log"

# Load environment variables from .env if present in project root
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# SMTP Server Settings (Standard STARTTLS on port 587)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# Default Sender Identity
SENDER_NAME = os.getenv("SENDER_NAME", "Acme Enterprise Reminders")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USER)

# Safety Defaults & Throttling Policies
DRY_RUN_DEFAULT = os.getenv("DRY_RUN_DEFAULT", "True").lower() in ("true", "1", "yes")
MAX_EMAILS_PER_BATCH = int(os.getenv("MAX_EMAILS_PER_BATCH", "50"))
RATE_LIMIT_DELAY_SECONDS = float(os.getenv("RATE_LIMIT_DELAY_SECONDS", "1.0"))

# Policy: MAX_RETRIES = 3 means up to 3 retries after initial attempt (4 total attempts maximum)
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BASE_BACKOFF_SECONDS = 2.0


def validate_smtp_credentials() -> tuple[bool, str]:
    """
    Validate that live SMTP credentials are non-empty and well-formed.
    Returns (is_valid, error_message).
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        return False, "SMTP_USER and SMTP_PASSWORD must be configured in .env for live mode."
    if "@" not in SMTP_USER:
        return False, "SMTP_USER must be a valid email address."
    return True, ""
