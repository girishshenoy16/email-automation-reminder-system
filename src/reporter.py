"""
Operational Logging and Audit CSV Reporter Module.
Handles colorized console feedback, persistent file logging,
log sanitization, and timestamped CSV compliance audit reports in outputs/.
"""

import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from colorama import Fore, Style, init
from src import config

# Initialize colorama for Windows/cross-platform ANSI color support
init(autoreset=True)

logger = logging.getLogger("email_system")


def mask_email(email: str) -> str:
    """
    Sanitizes recipient email addresses for safe logging without exposing full PII.
    Example: alex.m@example.com -> a***@example.com
    """
    if not isinstance(email, str) or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        masked_local = "*"
    else:
        masked_local = local[0] + "***"
    return f"{masked_local}@{domain}"


# Track handlers owned and attached by the email automation system
_OWNED_HANDLERS: List[logging.Handler] = []


def setup_system_logging(
    log_file_path: Optional[Path] = None,
    log_level: Optional[str] = None,
    enable_console: bool = True
) -> logging.Logger:
    """
    Configures persistent root, file, and console logging.
    Guarantees clean handler ownership without duplicate handlers, file leaks,
    or removing unrelated third-party/test handlers from the root logger.
    """
    global _OWNED_HANDLERS
    target_path = Path(log_file_path or config.LOG_FILE_PATH)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    level_name = (log_level or config.LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Cleanly flush, close, and detach only previously registered owned handlers
    for h in list(_OWNED_HANDLERS):
        try:
            h.flush()
            h.close()
        except Exception as e:
            sys.stderr.write(f"[LOGGING WARNING] Error closing previous handler: {e}\n")
        if h in root_logger.handlers:
            root_logger.removeHandler(h)
    _OWNED_HANDLERS.clear()

    # Attach new FileHandler pointing to target_path with fallback to stderr if file I/O fails
    try:
        file_handler = logging.FileHandler(target_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
        _OWNED_HANDLERS.append(file_handler)
    except (OSError, PermissionError) as err:
        sys.stderr.write(
            f"[LOGGING WARNING] Failed to initialize file logger at {target_path}: {err}. Falling back to console.\n"
        )

    # Attach StreamHandler for console output if enabled
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
        _OWNED_HANDLERS.append(console_handler)

    return root_logger


def close_system_logging() -> None:
    """
    Flushes, closes, and unregisters only handlers owned by the email automation system.
    Strictly preserves external or test-framework handlers on the root logger.
    Critical for Windows file lock release during testing and teardown.
    """
    global _OWNED_HANDLERS
    root_logger = logging.getLogger()
    for h in list(_OWNED_HANDLERS):
        try:
            h.flush()
            h.close()
        except Exception as e:
            sys.stderr.write(f"[LOGGING CLEANUP WARNING] Error releasing handler: {e}\n")
        if h in root_logger.handlers:
            root_logger.removeHandler(h)
    _OWNED_HANDLERS.clear()


def sanitize_csv_cell(val: Any) -> Any:
    """
    Prevents CSV Formula Injection (CWE-1236) by escaping cells starting with
    formula trigger characters (=, +, -, @, tab, carriage return).
    """
    if isinstance(val, str) and len(val) > 0 and val[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val}"
    return val


class Reporter:
    """
    Manages operational console display and compliance CSV audit trail exports.
    """

    def __init__(
        self,
        outputs_dir: Optional[Path] = None,
        logs_dir: Optional[Path] = None,
        log_level: Optional[str] = None
    ):
        self.outputs_dir = Path(outputs_dir or config.OUTPUTS_DIR)
        self.logs_dir = Path(logs_dir or config.LOGS_DIR)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        setup_system_logging(
            log_file_path=self.logs_dir / "email_system.log",
            log_level=log_level
        )

        self.session_records: List[Dict[str, Any]] = []

    def record_dispatch(
        self,
        reminder_id: str,
        recipient: str,
        department: str,
        reminder_type: str,
        subject: str,
        status: str,
        delivery_id: str,
        notes: str = ""
    ) -> None:
        """
        Logs dispatch event to memory and emits colored console and sanitized file output.
        Defensively protects dispatch recording from logging I/O exceptions while emitting
        diagnostic fallbacks to standard error.
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "timestamp": now_iso,
            "reminder_id": reminder_id,
            "recipient": recipient,
            "department": department,
            "reminder_type": reminder_type,
            "subject": subject,
            "status": status,
            "delivery_id": delivery_id,
            "notes": notes,
        }
        self.session_records.append(entry)

        # Colorized console feedback
        if status in ("SIMULATED", "SMTP_ACCEPTED"):
            color = Fore.GREEN
            prefix = "[OK]"
        elif status == "REQUIRES_REVIEW":
            color = Fore.YELLOW
            prefix = "[QUARANTINED]"
        else:
            color = Fore.RED
            prefix = "[ERROR]"

        # Mask recipient email for safe log persistence
        masked = mask_email(recipient)

        try:
            print(
                f"{color}{prefix} {Style.BRIGHT}{status}{Style.RESET_ALL} | "
                f"{reminder_id} -> {masked} | {subject} (ID: {delivery_id})"
            )
        except Exception as e:
            sys.stderr.write(f"[CONSOLE WRITE FALLBACK] Could not format console output: {e}\n")

        try:
            logger.info(f"{status} | {reminder_id} | {masked} | {subject} | {delivery_id} | {notes}")
        except Exception as e:
            sys.stderr.write(f"[LOGGING SUBSYSTEM WARNING] Failed to record dispatch log entry: {e}\n")

    def export_csv_report(self) -> Path:
        """
        Exports session dispatch records to a timestamped CSV report in outputs/.
        Applies formula-injection neutralization to all string values.
        """
        now_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_path = self.outputs_dir / f"dispatch_report_{now_stamp}.csv"

        fieldnames = [
            "timestamp",
            "reminder_id",
            "recipient",
            "department",
            "reminder_type",
            "subject",
            "status",
            "delivery_id",
            "notes",
        ]

        sanitized_records = [
            {k: sanitize_csv_cell(v) for k, v in row.items()}
            for row in self.session_records
        ]

        with open(report_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(sanitized_records)

        try:
            logger.info(f"Audit CSV report saved: {report_path.name}")
        except Exception as e:
            sys.stderr.write(f"[LOGGING SUBSYSTEM WARNING] Failed to log CSV report creation: {e}\n")

        print(f"{Fore.CYAN}[AUDIT REPORT] CSV generated: {report_path.name}{Style.RESET_ALL}")
        return report_path
