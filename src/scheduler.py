"""
Reminder Scheduler and Due Selection Module.
Filters eligible reminders based on target due date, category, email validity,
and persistent state status. Supports cron-like background execution using schedule.
"""

import logging
import time
from datetime import datetime, date
from typing import Callable, Optional
import pandas as pd
import schedule
from src.state_manager import StateManager

logger = logging.getLogger(__name__)


def get_current_date_str() -> str:
    """Returns today's date string in YYYY-MM-DD format."""
    return date.today().strftime("%Y-%m-%d")


def get_due_reminders(
    df: pd.DataFrame,
    state_manager: StateManager,
    target_date: Optional[str] = None,
    reminder_type: Optional[str] = None,
    is_live: bool = False
) -> pd.DataFrame:
    """
    Selects reminders eligible for dispatch based on:
    1. Due date check (due_date <= target_date).
    2. Optional reminder_type category filter.
    3. Syntax validity (is_valid_email == True).
    4. State eligibility: In live mode, excludes completed, processing, failed, or quarantined items.
    """
    if df.empty:
        return df.copy()

    cutoff_date = target_date or get_current_date_str()
    try:
        datetime.strptime(cutoff_date, "%Y-%m-%d")
    except (ValueError, TypeError) as err:
        raise ValueError(
            f"Invalid target_date format '{target_date}'. Expected ISO calendar format YYYY-MM-DD."
        ) from err

    filtered = df.copy()

    # 1. Filter by Due Date (due on or before target date)
    if "due_date" in filtered.columns:
        filtered = filtered[filtered["due_date"] <= cutoff_date]

    # 2. Filter by Reminder Type (if specified and not 'all')
    if reminder_type and reminder_type.lower() != "all" and "reminder_type" in filtered.columns:
        filtered = filtered[filtered["reminder_type"].str.lower() == reminder_type.lower()]

    # 3. Filter out invalid email syntax
    if "is_valid_email" in filtered.columns:
        filtered = filtered[filtered["is_valid_email"]]

    # 4. Filter by State Eligibility in Live Mode
    if is_live and not filtered.empty:
        eligible_mask = filtered["reminder_id"].apply(state_manager.is_eligible_for_live)
        filtered = filtered[eligible_mask]

    # Attach current live and simulation statuses for logging/reporting visibility
    if not filtered.empty:
        filtered["live_status"] = filtered["reminder_id"].apply(state_manager.get_live_status)
        filtered["simulation_status"] = filtered["reminder_id"].apply(state_manager.get_simulation_status)

    logger.info(
        f"Selected {len(filtered)} due reminder(s) (Cutoff: {cutoff_date}, Type: {reminder_type or 'all'}, Live: {is_live})"
    )
    return filtered


def setup_schedule(
    job_callback: Callable[[], None],
    interval_minutes: Optional[int] = None,
    run_at_time: Optional[str] = None
) -> None:
    """
    Configures recurring schedule triggers using the schedule library.
    Validates HH:MM time format before scheduling.
    """
    schedule.clear()
    if run_at_time:
        try:
            datetime.strptime(run_at_time, "%H:%M")
        except (ValueError, TypeError) as err:
            raise ValueError(
                f"Invalid run_at_time format '{run_at_time}'. Expected 24-hour time format HH:MM."
            ) from err
        schedule.every().day.at(run_at_time).do(job_callback)
        logger.info(f"Scheduled daily reminder job at {run_at_time}")
    else:
        mins = interval_minutes or 60
        schedule.every(mins).minutes.do(job_callback)
        logger.info(f"Scheduled reminder polling every {mins} minute(s)")


def run_scheduler_loop(interval_seconds: int = 1, max_runs: Optional[int] = None) -> None:
    """
    Executes the schedule event loop. Terminates gracefully on KeyboardInterrupt or max_runs.
    """
    logger.info("Scheduler event loop started. Press Ctrl+C to terminate.")
    runs = 0
    try:
        while True:
            schedule.run_pending()
            time.sleep(interval_seconds)
            runs += 1
            if max_runs is not None and runs >= max_runs:
                break
    except KeyboardInterrupt:
        logger.info("Scheduler loop terminated by user.")
