"""
Persistent State Manager Module.
Maintains atomic JSON persistence for dual-track execution state (simulation vs live),
prevents duplicate sends, and quarantines ambiguous outcomes as REQUIRES_REVIEW.
"""

import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from src import config

logger = logging.getLogger(__name__)

# Valid state enum definitions
SIMULATION_STATUSES = {"NOT_SIMULATED", "SIMULATED", "SIMULATION_FAILED"}
LIVE_STATUSES = {"PENDING", "PROCESSING", "SMTP_ACCEPTED", "FAILED", "REQUIRES_REVIEW"}


def get_current_iso_timestamp() -> str:
    """Returns current UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


class StateManager:
    """
    Manages persistent dual-track reminder execution state with atomic writes,
    automatic backup of corrupted state stores, and orphaned processing lock recovery.
    """

    def __init__(self, state_file_path: Optional[Path] = None):
        self.state_file_path = Path(state_file_path or config.STATE_FILE_PATH)
        self.state: Dict[str, Any] = {
            "version": "1.0",
            "last_updated": get_current_iso_timestamp(),
            "reminders": {},
        }
        self.load()
        self.recover_orphaned_locks()

    def load(self) -> None:
        """
        Loads state from disk. If state file is corrupted, creates an immediate
        timestamped backup before reinitializing a clean state structure.
        """
        if self.state_file_path.exists():
            try:
                content = self.state_file_path.read_text(encoding="utf-8")
                loaded = json.loads(content)
                if not isinstance(loaded, dict) or "reminders" not in loaded or not isinstance(loaded.get("reminders"), dict):
                    raise ValueError("State JSON structure is invalid (missing 'reminders' dictionary)")
                self.state = loaded
            except Exception as e:
                stamp = int(datetime.now(timezone.utc).timestamp())
                backup_path = self.state_file_path.with_name(
                    f"{self.state_file_path.name}.corrupted_{stamp}"
                )
                try:
                    shutil.copy2(self.state_file_path, backup_path)
                    logger.critical(
                        f"State file {self.state_file_path} was corrupted: {e}. "
                        f"Preserved backup at {backup_path}. Reinitializing clean state."
                    )
                except Exception as copy_err:
                    logger.critical(f"Failed to copy corrupted state backup: {copy_err}")

                self.state = {
                    "version": "1.0",
                    "last_updated": get_current_iso_timestamp(),
                    "reminders": {},
                }
        else:
            self.state = {
                "version": "1.0",
                "last_updated": get_current_iso_timestamp(),
                "reminders": {},
            }

    def save_atomic(self) -> None:
        """
        Saves current state atomically using a unique temporary file and atomic replace.
        Prevents file corruption on process termination or system crash and handles
        transient Windows file lock contention.
        """
        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.state["last_updated"] = get_current_iso_timestamp()

        unique_id = uuid.uuid4().hex[:8]
        temp_path = self.state_file_path.with_name(f"{self.state_file_path.name}.tmp_{unique_id}")
        payload = json.dumps(self.state, indent=2, ensure_ascii=False)

        try:
            # Write to unique temp file and flush
            temp_path.write_text(payload, encoding="utf-8")

            # Atomic replace with retry for transient Windows file-lock contention
            for attempt in range(4):
                try:
                    os.replace(temp_path, self.state_file_path)
                    break
                except PermissionError:
                    if attempt < 3:
                        time.sleep(0.05)
                    else:
                        raise
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def save(self) -> None:
        """Alias for save_atomic."""
        self.save_atomic()

    def get_or_create_reminder(
        self,
        reminder_id: str,
        contact_id: str = "",
        due_date: str = ""
    ) -> Dict[str, Any]:
        """
        Retrieves existing reminder state or initializes dual-track structure.
        """
        reminders = self.state["reminders"]
        if reminder_id not in reminders:
            reminders[reminder_id] = {
                "contact_id": contact_id,
                "due_date": due_date,
                "simulation_state": {
                    "status": "NOT_SIMULATED",
                    "last_simulated_at": None,
                    "simulation_count": 0,
                    "last_error": None,
                },
                "live_state": {
                    "status": "PENDING",
                    "last_attempt_at": None,
                    "delivery_id": None,
                    "retry_count": 0,
                    "smtp_response": None,
                    "last_error": None,
                },
            }
            self.save_atomic()
        return reminders[reminder_id]

    # --- Simulation State Methods (Track 1) ---

    def record_simulation_success(self, reminder_id: str, contact_id: str = "", due_date: str = "") -> None:
        """
        Records a successful dry-run simulation.
        Does NOT touch or alter live_state.
        """
        rem = self.get_or_create_reminder(reminder_id, contact_id, due_date)
        sim = rem["simulation_state"]
        sim["status"] = "SIMULATED"
        sim["last_simulated_at"] = get_current_iso_timestamp()
        sim["simulation_count"] = sim.get("simulation_count", 0) + 1
        sim["last_error"] = None
        self.save_atomic()
        logger.info(f"State transition: Reminder {reminder_id} -> SIMULATED (count={sim['simulation_count']})")

    def record_simulation_failure(self, reminder_id: str, error_msg: str, contact_id: str = "", due_date: str = "") -> None:
        """Records a simulation validation error."""
        rem = self.get_or_create_reminder(reminder_id, contact_id, due_date)
        sim = rem["simulation_state"]
        sim["status"] = "SIMULATION_FAILED"
        sim["last_simulated_at"] = get_current_iso_timestamp()
        sim["last_error"] = error_msg
        self.save_atomic()
        logger.warning(f"State transition: Reminder {reminder_id} -> SIMULATION_FAILED ({error_msg})")

    def get_simulation_status(self, reminder_id: str) -> str:
        """Returns current simulation status (default: NOT_SIMULATED)."""
        rem = self.state["reminders"].get(reminder_id)
        if not rem:
            return "NOT_SIMULATED"
        return rem["simulation_state"].get("status", "NOT_SIMULATED")

    # --- Live Execution State Methods (Track 2) ---

    def get_live_status(self, reminder_id: str) -> str:
        """Returns current live execution status (default: PENDING)."""
        rem = self.state["reminders"].get(reminder_id)
        if not rem:
            return "PENDING"
        return rem["live_state"].get("status", "PENDING")

    def is_eligible_for_live(self, reminder_id: str) -> bool:
        """
        Determines if a reminder is eligible for live sending.
        Eligible ONLY if live_state.status is PENDING.
        Completed, processing, failed, or quarantined items are skipped.
        """
        status = self.get_live_status(reminder_id)
        if status != "PENDING":
            logger.debug(
                f"Duplicate prevention: Reminder {reminder_id} has live status '{status}', skipping live dispatch."
            )
            return False
        return True

    def set_live_processing(self, reminder_id: str, contact_id: str = "", due_date: str = "") -> None:
        """Sets live_state status to PROCESSING before initiating SMTP transaction."""
        rem = self.get_or_create_reminder(reminder_id, contact_id, due_date)
        live = rem["live_state"]
        live["status"] = "PROCESSING"
        live["last_attempt_at"] = get_current_iso_timestamp()
        self.save_atomic()
        logger.info(f"State transition: Reminder {reminder_id} -> PROCESSING")

    def record_live_accepted(
        self,
        reminder_id: str,
        delivery_id: str,
        smtp_response: str,
        contact_id: str = "",
        due_date: str = ""
    ) -> None:
        """
        Records successful SMTP server acceptance (250 OK).
        Terminal state: Permanently prevents duplicate live dispatch.
        """
        rem = self.get_or_create_reminder(reminder_id, contact_id, due_date)
        live = rem["live_state"]
        live["status"] = "SMTP_ACCEPTED"
        live["delivery_id"] = delivery_id
        live["smtp_response"] = smtp_response
        live["last_error"] = None
        self.save_atomic()
        logger.info(f"State transition: Reminder {reminder_id} -> SMTP_ACCEPTED (delivery_id={delivery_id})")

    def record_live_failure(
        self,
        reminder_id: str,
        error_msg: str,
        is_retryable: bool,
        contact_id: str = "",
        due_date: str = ""
    ) -> None:
        """
        Handles live SMTP failures:
        - If retryable and retry_count < MAX_RETRIES (3 retries after initial attempt = 4 total attempts):
          increments retry_count and resets status to PENDING.
        - Otherwise: transitions to FAILED (terminal).
        """
        rem = self.get_or_create_reminder(reminder_id, contact_id, due_date)
        live = rem["live_state"]
        live["last_error"] = error_msg
        current_retries = live.get("retry_count", 0)

        if is_retryable and current_retries < config.MAX_RETRIES:
            live["retry_count"] = current_retries + 1
            live["status"] = "PENDING"
            logger.info(
                f"Reminder {reminder_id} experienced retryable failure ({error_msg}). "
                f"Retry {live['retry_count']}/{config.MAX_RETRIES} scheduled."
            )
        else:
            live["status"] = "FAILED"
            logger.warning(
                f"Reminder {reminder_id} transitioned to FAILED: {error_msg} "
                f"(Retryable: {is_retryable}, Retries: {current_retries}/{config.MAX_RETRIES})"
            )
        self.save_atomic()

    def record_requires_review(
        self,
        reminder_id: str,
        reason: str,
        contact_id: str = "",
        due_date: str = ""
    ) -> None:
        """
        Quarantines record as REQUIRES_REVIEW when outcome is ambiguous
        (e.g., timeout during DATA transmission, uncoordinated crash).
        Automatic retries are strictly prohibited.
        """
        rem = self.get_or_create_reminder(reminder_id, contact_id, due_date)
        live = rem["live_state"]
        live["status"] = "REQUIRES_REVIEW"
        live["last_error"] = f"QUARANTINED: {reason}"
        logger.warning(
            f"[QUARANTINE] Reminder {reminder_id} marked REQUIRES_REVIEW: {reason}. "
            f"Manual administrative resolution required."
        )
        self.save_atomic()

    def recover_orphaned_locks(self) -> int:
        """
        Checks for any reminder left in PROCESSING status (indicating an engine crash
        or interrupted execution mid-handshake). Transitions them to REQUIRES_REVIEW.
        """
        orphaned_count = 0
        for reminder_id, rem in self.state.get("reminders", {}).items():
            live = rem.get("live_state", {})
            if live.get("status") == "PROCESSING":
                orphaned_count += 1
                live["status"] = "REQUIRES_REVIEW"
                live["last_error"] = (
                    "QUARANTINED: Process terminated while in PROCESSING state. "
                    "Manual verification required to prevent duplicate delivery."
                )
                logger.warning(
                    f"Recovered orphaned lock for {reminder_id}: transitioned to REQUIRES_REVIEW."
                )

        if orphaned_count > 0:
            self.save_atomic()
        return orphaned_count

    def resolve_quarantined_record(self, reminder_id: str, resolved_status: str) -> bool:
        """
        Allows an administrator to manually resolve a quarantined record.
        Accepted targets: 'SMTP_ACCEPTED' or 'PENDING'.
        """
        clean_target = resolved_status.upper().strip()
        if clean_target not in {"SMTP_ACCEPTED", "PENDING"}:
            raise ValueError(f"Invalid resolution status '{resolved_status}'. Must be 'SMTP_ACCEPTED' or 'PENDING'.")

        rem = self.state.get("reminders", {}).get(reminder_id)
        if not rem:
            return False

        live = rem["live_state"]
        live["status"] = clean_target
        live["last_error"] = f"Manually resolved to {clean_target} by administrator at {get_current_iso_timestamp()}"
        self.save_atomic()
        logger.info(f"Quarantined reminder {reminder_id} resolved to {clean_target}.")
        return True
