"""
Operational Dashboard JSON Exporter and Export Boundary Enforcement Module.
Generates structured local operational analytics in outputs/ while strictly preventing
any programmatic modification of the immutable public fixture in docs/data/dashboard_data.json.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import pandas as pd
from src import config
from src.state_manager import StateManager

logger = logging.getLogger(__name__)


def assert_export_boundary(target_path: Path) -> None:
    """
    Guarantees strict public/private data separation.
    Raises PermissionError if the target path is located inside docs/ or matches the public fixture.
    """
    resolved_target = Path(target_path).resolve()
    resolved_docs = Path(config.DOCS_DIR).resolve()
    resolved_fixture = Path(config.PUBLIC_FIXTURE_PATH).resolve()

    if resolved_target == resolved_fixture or resolved_docs in resolved_target.parents:
        raise PermissionError(
            f"Public/Private Separation Violation: The automation engine is prohibited "
            f"from writing operational data to public directory: {resolved_target}"
        )


def export_operational_dashboard_data(
    state_manager: StateManager,
    merged_df: pd.DataFrame,
    output_path: Optional[Path] = None
) -> Path:
    """
    Aggregates operational state metrics and exports them exclusively to outputs/.
    """
    target = Path(output_path or config.OPERATIONAL_EXPORT_PATH)

    # Strictly enforce boundary protection
    assert_export_boundary(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    reminders_state = state_manager.state.get("reminders", {})

    total_contacts = int(merged_df["contact_id"].nunique()) if not merged_df.empty and "contact_id" in merged_df else 0
    total_reminders = len(merged_df) if not merged_df.empty else 0

    # Tally statuses from state manager
    simulated_count = sum(
        1 for r in reminders_state.values()
        if r.get("simulation_state", {}).get("status") == "SIMULATED"
    )
    smtp_accepted_count = sum(
        1 for r in reminders_state.values()
        if r.get("live_state", {}).get("status") == "SMTP_ACCEPTED"
    )
    failed_count = sum(
        1 for r in reminders_state.values()
        if r.get("live_state", {}).get("status") == "FAILED"
    )
    requires_review_count = sum(
        1 for r in reminders_state.values()
        if r.get("live_state", {}).get("status") == "REQUIRES_REVIEW"
    )
    pending_count = sum(
        1 for r in reminders_state.values()
        if r.get("live_state", {}).get("status") == "PENDING"
    )

    # Submission rate (Live Mode) - MTA handoff rate, not guaranteed inbox delivery
    total_live_attempts = smtp_accepted_count + failed_count
    if total_live_attempts > 0:
        submission_rate = round((smtp_accepted_count / total_live_attempts) * 100.0, 1)
    else:
        submission_rate = 0.0

    # Simulation rate (Dry-Run Mode)
    if total_reminders > 0:
        simulation_rate = round((simulated_count / total_reminders) * 100.0, 1)
    else:
        simulation_rate = 0.0

    # Distribution by type
    distribution_by_type = {}
    if not merged_df.empty and "reminder_type" in merged_df:
        distribution_by_type = merged_df["reminder_type"].str.capitalize().value_counts().to_dict()

    # Distribution by department
    distribution_by_department = {}
    if not merged_df.empty and "department" in merged_df:
        distribution_by_department = merged_df["department"].value_counts().to_dict()

    # Build operational payload
    payload: Dict[str, Any] = {
        "system_info": {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "environment": "Local Operational",
            "delivery_disclaimer": "SMTP acceptance confirms MTA handoff (250 OK); inbox delivery depends on recipient mail servers.",
        },
        "kpis": {
            "total_contacts": total_contacts,
            "total_reminders": total_reminders,
            "simulated_count": simulated_count,
            "smtp_accepted_count": smtp_accepted_count,
            "failed_count": failed_count,
            "requires_review_count": requires_review_count,
            "pending_count": pending_count,
            "submission_rate_percent": submission_rate,
            "simulation_rate_percent": simulation_rate,
        },
        "distribution_by_type": distribution_by_type,
        "distribution_by_status": {
            "SMTP_ACCEPTED": smtp_accepted_count,
            "SIMULATED": simulated_count,
            "PENDING": pending_count,
            "FAILED": failed_count,
            "REQUIRES_REVIEW": requires_review_count,
        },
        "distribution_by_department": distribution_by_department,
    }

    # Atomic write to prevent partial/corrupted reads
    temp_target = target.with_name(f"{target.name}.tmp_{uuid.uuid4().hex[:6]}")
    try:
        temp_target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # Atomic replace with retry for Windows file locks
        max_retries = 5
        for attempt in range(max_retries):
            try:
                os.replace(temp_target, target)
                break
            except PermissionError:
                if attempt == max_retries - 1:
                    raise
                time.sleep(0.05 * (2 ** attempt))
    finally:
        if temp_target.exists():
            try:
                temp_target.unlink()
            except OSError:
                pass

    logger.info(f"Operational dashboard JSON exported to {target}")
    return target
