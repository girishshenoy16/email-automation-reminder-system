"""
Email Automation & Reminder System - Main CLI Orchestrator.
Supports safe Dry-Run simulation by default, authenticated live SMTP over STARTTLS,
state reconciliation, and operational dashboard metric export.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional
from colorama import Fore, Style, init

from src import config
from src.csv_loader import load_and_merge_datasets
from src.json_exporter import export_operational_dashboard_data
from src.mailer import Mailer
from src.reporter import Reporter, close_system_logging, mask_email, setup_system_logging
from src.scheduler import get_due_reminders, run_scheduler_loop, setup_schedule
from src.state_manager import StateManager
from src.template_engine import build_mime_message

init(autoreset=True)
logger = logging.getLogger("email_system")


def build_cli_parser() -> argparse.ArgumentParser:
    """Constructs and returns the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Enterprise Email Automation & Reminder System — Dual-Track Execution Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --mode instant                          # Safe Dry-Run simulation (all due types)
  python main.py --mode instant --type payment           # Dry-run payment reminders only
  python main.py --mode instant --export-dashboard       # Dry-run + export outputs/operational_dashboard_data.json
  python main.py --live --mode instant                   # Opt-in live SMTP delivery (requires .env)
  python main.py --resolve REM-1001 --status accepted   # Resolve quarantined record
        """
    )

    # Mutually exclusive execution mode group (Dry-Run vs Live)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Execute in safe Dry-Run simulation mode without network calls (default)."
    )
    mode_group.add_argument(
        "--live",
        dest="live",
        action="store_true",
        default=None,
        help="Execute in live authenticated SMTP delivery mode (requires .env credentials)."
    )

    # Operational parameters
    parser.add_argument(
        "--mode",
        dest="mode",
        choices=["instant", "schedule"],
        default="instant",
        help="Execution mode: instant one-off batch or recurring schedule (default: instant)."
    )
    parser.add_argument(
        "--type",
        dest="type",
        choices=["all", "meeting", "payment", "webinar"],
        default="all",
        help="Filter reminders by type (default: all)."
    )
    parser.add_argument(
        "--target-date",
        dest="target_date",
        type=str,
        default=None,
        help="Target cutoff date in YYYY-MM-DD format (default: today)."
    )
    parser.add_argument(
        "--export-dashboard",
        dest="export_dashboard",
        action="store_true",
        default=False,
        help="Export operational dashboard JSON metrics to outputs/ (never touches docs/)."
    )

    # Administrative quarantine resolution
    parser.add_argument(
        "--resolve",
        dest="resolve_id",
        type=str,
        default=None,
        help="Reminder ID of a quarantined record (REQUIRES_REVIEW) to manually resolve."
    )
    parser.add_argument(
        "--status",
        dest="resolve_status",
        choices=["accepted", "pending", "SMTP_ACCEPTED", "PENDING"],
        default=None,
        help="Target status for manual quarantine resolution: accepted (SMTP_ACCEPTED) or pending."
    )

    # Diagnostic & scheduler configuration
    parser.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override logging verbosity level (default from config/env: INFO)."
    )
    parser.add_argument(
        "--schedule-time",
        dest="schedule_time",
        type=str,
        default="09:00",
        help="Daily scheduled execution time in HH:MM format (default: 09:00)."
    )
    parser.add_argument(
        "--schedule-interval",
        dest="schedule_interval",
        type=int,
        default=None,
        help="Recurring schedule interval in minutes (overrides --schedule-time if provided)."
    )
    parser.add_argument(
        "--max-schedule-runs",
        dest="max_schedule_runs",
        type=int,
        default=None,
        help="Maximum recurring schedule loop iterations before termination (useful for tests)."
    )
    parser.add_argument(
        "--state-file",
        dest="state_file",
        type=str,
        default=None,
        help="Optional custom path to reminder_state.json (defaults to data/reminder_state.json)."
    )

    return parser


def handle_quarantine_resolution(state_manager: StateManager, reminder_id: str, raw_status: Optional[str]) -> int:
    """Manually resolves a quarantined record as SMTP_ACCEPTED or PENDING."""
    if not raw_status:
        msg = "Error: --status [accepted|pending] is required when using --resolve <id>."
        print(f"{Fore.RED}[CLI ERROR] {msg}{Style.RESET_ALL}", file=sys.stderr)
        logger.error(msg)
        return 1

    clean_status = "SMTP_ACCEPTED" if raw_status.lower() in ("accepted", "smtp_accepted") else "PENDING"
    success = state_manager.resolve_quarantined_record(reminder_id, clean_status)

    if success:
        msg = f"Quarantined reminder '{reminder_id}' successfully resolved to '{clean_status}'."
        print(f"{Fore.GREEN}[RESOLVED] {msg}{Style.RESET_ALL}")
        logger.info(msg)
        return 0
    else:
        msg = f"Reminder ID '{reminder_id}' not found in state store."
        print(f"{Fore.YELLOW}[NOT FOUND] {msg}{Style.RESET_ALL}", file=sys.stderr)
        logger.warning(msg)
        return 1


def process_reminder_batch(
    merged_df,
    state_manager: StateManager,
    mailer: Mailer,
    reporter: Reporter,
    target_date: Optional[str] = None,
    reminder_type: str = "all",
    is_live: bool = False,
    export_dashboard: bool = False
) -> int:
    """
    Executes a single processing batch: queries due reminders, formats MIME messages,
    dispatches via Mailer (simulation or live), transitions state, and exports reports.
    """
    mode_label = "LIVE SMTP" if is_live else "DRY-RUN SIMULATION"
    logger.info(f"--- Starting batch execution: Mode={mode_label}, Type={reminder_type}, Date={target_date or 'today'} ---")

    due_df = get_due_reminders(
        df=merged_df,
        state_manager=state_manager,
        target_date=target_date,
        reminder_type=reminder_type,
        is_live=is_live
    )

    if due_df.empty:
        msg = f"No eligible reminders found due for processing (Mode: {mode_label}, Type: {reminder_type}, Date: {target_date or 'today'})."
        print(f"{Fore.YELLOW}[NOTICE] {msg}{Style.RESET_ALL}")
        logger.info(msg)
        if export_dashboard:
            json_path = export_operational_dashboard_data(state_manager, merged_df, config.OPERATIONAL_EXPORT_PATH)
            print(f"{Fore.CYAN}[DASHBOARD EXPORT] Operational data saved: {json_path.name}{Style.RESET_ALL}")
        return 0

    print(
        f"\n{Style.BRIGHT}======================================================{Style.RESET_ALL}\n"
        f"{Style.BRIGHT} Executing {mode_label} Batch ({len(due_df)} due record(s)){Style.RESET_ALL}\n"
        f"{Style.BRIGHT}======================================================{Style.RESET_ALL}"
    )

    batch_processed = 0
    for _, row in due_df.iterrows():
        if batch_processed >= config.MAX_EMAILS_PER_BATCH:
            logger.warning(f"Batch ceiling of {config.MAX_EMAILS_PER_BATCH} reached. Remaining reminders deferred.")
            break

        reminder_id = row["reminder_id"]
        recipient_email = row["email"]
        r_type = row.get("reminder_type", "meeting")
        department = row.get("department", "General")
        contact_id = row.get("contact_id", "")
        due_date_val = str(row.get("due_date", ""))

        context = row.to_dict()

        # Build multipart MIME message
        try:
            mime_msg, plain_body, html_body, subject = build_mime_message(
                reminder_type=r_type,
                context=context,
                recipient_email=recipient_email,
                templates_dir=config.TEMPLATES_DIR
            )
        except Exception as e:
            logger.error(f"Template rendering failed for {reminder_id}: {e}")
            if is_live:
                state_manager.record_live_failure(reminder_id, f"Template error: {e}", is_retryable=False, contact_id=contact_id, due_date=due_date_val)
            else:
                state_manager.record_simulation_failure(reminder_id, f"Template error: {e}", contact_id=contact_id, due_date=due_date_val)
            reporter.record_dispatch(
                reminder_id=reminder_id,
                recipient=recipient_email,
                department=department,
                reminder_type=r_type,
                subject=f"Failed Template: {r_type}",
                status="FAILED",
                delivery_id="DEL-TEMPL-ERR",
                notes=str(e)
            )
            continue

        # If live execution, transition state to PROCESSING before network handoff
        if is_live:
            state_manager.set_live_processing(reminder_id, contact_id=contact_id, due_date=due_date_val)

        # Dispatch via Mailer
        result = mailer.dispatch(
            reminder_id=reminder_id,
            recipient_email=recipient_email,
            subject=subject,
            plain_body=plain_body,
            html_body=html_body,
            mime_message=mime_msg
        )

        status = result.get("status", "FAILED")
        delivery_id = result.get("delivery_id", "N/A")
        response_msg = result.get("response") or ""
        error_msg = result.get("error")
        is_retryable = result.get("retryable", False)

        # Synchronize persistent execution state
        if status == "SIMULATED":
            state_manager.record_simulation_success(reminder_id, contact_id=contact_id, due_date=due_date_val)
        elif status == "SMTP_ACCEPTED":
            state_manager.record_live_accepted(reminder_id, delivery_id, response_msg, contact_id=contact_id, due_date=due_date_val)
        elif status == "REQUIRES_REVIEW":
            state_manager.record_requires_review(reminder_id, error_msg or "Ambiguous SMTP outcome", contact_id=contact_id, due_date=due_date_val)
        elif status == "FAILED":
            if is_live:
                state_manager.record_live_failure(reminder_id, error_msg or "Dispatch failed", is_retryable=is_retryable, contact_id=contact_id, due_date=due_date_val)
            else:
                state_manager.record_simulation_failure(reminder_id, error_msg or "Simulation failed", contact_id=contact_id, due_date=due_date_val)

        # Record operational dispatch audit
        notes_detail = error_msg if error_msg else response_msg
        reporter.record_dispatch(
            reminder_id=reminder_id,
            recipient=recipient_email,
            department=department,
            reminder_type=r_type,
            subject=subject,
            status=status,
            delivery_id=delivery_id,
            notes=notes_detail
        )
        batch_processed += 1

    # Generate timestamped CSV audit report in outputs/
    if reporter.session_records:
        csv_path = reporter.export_csv_report()
        logger.info(f"Audit log generated: {csv_path.name}")

    # Generate operational dashboard JSON if requested
    if export_dashboard:
        json_path = export_operational_dashboard_data(state_manager, merged_df, config.OPERATIONAL_EXPORT_PATH)
        print(f"{Fore.CYAN}[DASHBOARD EXPORT] Operational data saved: {json_path.name}{Style.RESET_ALL}")
        logger.info(f"Operational dashboard export complete: {json_path.name}")

    print(f"\n{Fore.GREEN}[DONE] Successfully processed {batch_processed} reminder(s).{Style.RESET_ALL}\n")
    return batch_processed


def main() -> int:
    """Application CLI entry point."""
    parser = build_cli_parser()
    args = parser.parse_args()

    # Determine execution track (Safe Dry-Run by default)
    is_live = bool(args.live)
    effective_log_level = args.log_level or config.LOG_LEVEL

    # Initialize persistent system logging
    setup_system_logging(
        log_file_path=config.LOG_FILE_PATH,
        log_level=effective_log_level,
        enable_console=True
    )

    logger.info("=======================================================")
    logger.info("Email Automation & Reminder System — Execution Started")
    logger.info(f"Track: {'LIVE SMTP' if is_live else 'DRY-RUN SIMULATION'} | Mode: {args.mode} | Log Level: {effective_log_level}")
    logger.info("=======================================================")

    try:
        # Initialize persistent state manager
        target_state_file = Path(args.state_file) if args.state_file else config.STATE_FILE_PATH
        state_manager = StateManager(target_state_file)

        # Check for administrative quarantine resolution
        if args.resolve_id:
            return handle_quarantine_resolution(state_manager, args.resolve_id, args.resolve_status)

        # Ingestion: Load and merge contact and reminder datasets
        try:
            merged_df = load_and_merge_datasets(
                contacts_path=config.CONTACTS_FILE_PATH,
                reminders_path=config.REMINDERS_FILE_PATH
            )
        except Exception as e:
            msg = f"Failed to ingest datasets: {e}"
            print(f"{Fore.RED}[INGESTION ERROR] {msg}{Style.RESET_ALL}", file=sys.stderr)
            logger.error(msg)
            return 1

        # Instantiate transport and reporting services
        mailer = Mailer(dry_run=(not is_live))
        reporter = Reporter(
            outputs_dir=config.OUTPUTS_DIR,
            logs_dir=config.LOGS_DIR,
            log_level=effective_log_level
        )

        # Mode execution
        if args.mode == "instant":
            process_reminder_batch(
                merged_df=merged_df,
                state_manager=state_manager,
                mailer=mailer,
                reporter=reporter,
                target_date=args.target_date,
                reminder_type=args.type,
                is_live=is_live,
                export_dashboard=args.export_dashboard
            )
            return 0

        elif args.mode == "schedule":
            print(f"{Fore.CYAN}[SCHEDULER] Configuring recurring job runner...{Style.RESET_ALL}")

            def scheduled_job():
                # Re-instantiate reporter per cycle to produce cycle-specific audit logs
                cycle_reporter = Reporter(
                    outputs_dir=config.OUTPUTS_DIR,
                    logs_dir=config.LOGS_DIR,
                    log_level=effective_log_level
                )
                process_reminder_batch(
                    merged_df=merged_df,
                    state_manager=state_manager,
                    mailer=mailer,
                    reporter=cycle_reporter,
                    target_date=args.target_date,
                    reminder_type=args.type,
                    is_live=is_live,
                    export_dashboard=args.export_dashboard
                )

            setup_schedule(
                job_callback=scheduled_job,
                interval_minutes=args.schedule_interval,
                run_at_time=args.schedule_time if not args.schedule_interval else None
            )

            print(
                f"{Fore.GREEN}[SCHEDULER ACTIVE] Polling loop running. "
                f"Interval: {args.schedule_interval or args.schedule_time}. Press Ctrl+C to terminate.{Style.RESET_ALL}"
            )
            run_scheduler_loop(max_runs=args.max_schedule_runs)
            return 0

        return 0

    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[TERMINATING] Process interrupted by user.{Style.RESET_ALL}")
        logger.info("Process terminated by user KeyboardInterrupt.")
        return 0
    except Exception as e:
        logger.exception(f"Unhandled system error during execution: {e}")
        print(f"{Fore.RED}[CRITICAL ERROR] {e}{Style.RESET_ALL}", file=sys.stderr)
        return 1
    finally:
        logger.info("Execution complete. Releasing system logging handlers.")
        close_system_logging()


if __name__ == "__main__":
    sys.exit(main())
