"""
SMTP Dispatch and Dry-Run Simulator Module.
Implements safe zero-network simulation by default, authenticated live SMTP with STARTTLS,
strict failure classification (retryable vs permanent vs ambiguous), and rate-limiting.
"""

import logging
import smtplib
import socket
import ssl
import time
import uuid
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple
from src import config
from src.reporter import mask_email

logger = logging.getLogger(__name__)


def classify_smtp_error(error: Exception, in_data_phase: bool = False) -> Tuple[str, bool]:
    """
    Classifies dispatch exceptions into strict operational buckets:
    1. PERMANENT (False): 5xx codes, authentication failures, bad syntax, unsupported STARTTLS, TLS cert failures.
    2. AMBIGUOUS (False): Any failure occurring during/after DATA transmission begins, socket reset during DATA,
                          or uncertain handoff state (quarantined as REQUIRES_REVIEW to prevent duplicates).
    3. RETRYABLE (True): Safe pre-data connection timeouts, DNS resolution drops, 4xx transient codes.
    """
    error_str = str(error)

    # 1. Permanent Failures (Do not retry)
    if isinstance(error, smtplib.SMTPAuthenticationError) or "535" in error_str:
        return "PERMANENT", False
    if isinstance(error, smtplib.SMTPRecipientsRefused) or "550" in error_str:
        return "PERMANENT", False
    if isinstance(error, smtplib.SMTPSenderRefused) or "501" in error_str:
        return "PERMANENT", False
    if isinstance(error, smtplib.SMTPNotSupportedError):
        return "PERMANENT", False
    if isinstance(error, ssl.SSLError) or "certificate verify failed" in error_str.lower():
        return "PERMANENT", False
    if "554" in error_str or "553" in error_str:
        return "PERMANENT", False

    # 2. Ambiguous Outcomes (Data may have been received or queued by MTA; quarantine to avoid duplicates)
    if in_data_phase:
        return "AMBIGUOUS", False
    if isinstance(error, smtplib.SMTPDataError):
        return "AMBIGUOUS", False
    if "mid_data_disconnect" in error_str.lower() or "broken pipe" in error_str.lower():
        return "AMBIGUOUS", False

    # 3. Retryable Failures (Safe to retry; data was NOT sent)
    if isinstance(error, (socket.gaierror, ConnectionRefusedError, smtplib.SMTPConnectError)):
        return "RETRYABLE", True
    if isinstance(error, (socket.timeout, TimeoutError)):
        # Pre-DATA timeout is safe to retry
        return "RETRYABLE", True
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return "RETRYABLE", True
    if "421" in error_str or "450" in error_str or "451" in error_str:
        return "RETRYABLE", True

    # Default fallback for unknown network errors prior to data transmission
    return "RETRYABLE", True


class Mailer:
    """
    SMTP Dispatcher supporting safe Dry-Run simulation (default) and live STARTTLS delivery.
    """

    def __init__(
        self,
        dry_run: bool = True,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        rate_limit_delay: Optional[float] = None
    ):
        self.dry_run = dry_run
        self.smtp_host = smtp_host or config.SMTP_HOST
        self.smtp_port = smtp_port or config.SMTP_PORT
        self.smtp_user = smtp_user or config.SMTP_USER
        self.smtp_password = smtp_password or config.SMTP_PASSWORD
        self.rate_limit_delay = rate_limit_delay if rate_limit_delay is not None else config.RATE_LIMIT_DELAY_SECONDS

    def dispatch(
        self,
        reminder_id: str,
        recipient_email: str,
        subject: str,
        plain_body: str,
        html_body: str,
        mime_message: Optional[EmailMessage] = None
    ) -> Dict[str, Any]:
        """
        Dispatches email via Dry-Run simulation or Live SMTP over STARTTLS.
        Returns dispatch result dict with status, delivery_id, and response details.
        """
        delivery_id = f"DEL-{uuid.uuid4().hex[:8].upper()}"

        if self.dry_run:
            return self._simulate_dispatch(reminder_id, recipient_email, subject, delivery_id)

        return self._live_dispatch(
            reminder_id=reminder_id,
            recipient_email=recipient_email,
            subject=subject,
            mime_message=mime_message,
            delivery_id=delivery_id
        )

    def _simulate_dispatch(
        self,
        reminder_id: str,
        recipient_email: str,
        subject: str,
        delivery_id: str
    ) -> Dict[str, Any]:
        """
        Simulates dispatch with zero external network activity.
        """
        masked_email = mask_email(recipient_email)
        logger.info(
            f"[DRY-RUN SIMULATION] Reminder {reminder_id} -> {masked_email} | Subject: '{subject}'"
        )
        # Simulate slight processing latency
        time.sleep(0.05)

        return {
            "status": "SIMULATED",
            "delivery_id": delivery_id,
            "response": "Simulation mode: Zero network socket activity",
            "error": None,
            "retryable": False,
        }

    def _live_dispatch(
        self,
        reminder_id: str,
        recipient_email: str,
        subject: str,
        mime_message: Optional[EmailMessage],
        delivery_id: str
    ) -> Dict[str, Any]:
        """
        Executes live SMTP dispatch over STARTTLS with strict failure taxonomy and rate limiting.
        """
        is_valid_cred, cred_err = config.validate_smtp_credentials()
        if not is_valid_cred:
            logger.error(f"Cannot dispatch live email for {reminder_id}: {cred_err}")
            return {
                "status": "FAILED",
                "delivery_id": delivery_id,
                "response": None,
                "error": cred_err,
                "retryable": False,
            }

        server = None
        in_data_phase = False
        masked_email = mask_email(recipient_email)
        try:
            logger.info(
                f"[LIVE DISPATCH INITIATED] Reminder {reminder_id} -> {masked_email} via {self.smtp_host}:{self.smtp_port}"
            )
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15)
            ssl_context = ssl.create_default_context()
            server.starttls(context=ssl_context)
            server.login(self.smtp_user, self.smtp_password)

            # Mark entry into DATA transmission phase
            in_data_phase = True
            if mime_message is not None:
                server.send_message(mime_message)
            else:
                server.sendmail(config.SENDER_EMAIL, [recipient_email], f"Subject: {subject}\n\nEmail Content")

            # Note: SMTP acceptance (250 OK) confirms server handoff, NOT guaranteed inbox delivery.
            logger.info(
                f"[SMTP_ACCEPTED] Reminder {reminder_id} handed off to MTA successfully. Delivery ID: {delivery_id}"
            )

            # Enforce rate-limiting delay between dispatches
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

            return {
                "status": "SMTP_ACCEPTED",
                "delivery_id": delivery_id,
                "response": "250 OK - Message accepted for delivery",
                "error": None,
                "retryable": False,
            }

        except Exception as e:
            category, is_retryable = classify_smtp_error(e, in_data_phase=in_data_phase)
            logger.error(
                f"SMTP dispatch error for {reminder_id} ({category}): {e}"
            )

            if category == "AMBIGUOUS":
                return {
                    "status": "REQUIRES_REVIEW",
                    "delivery_id": delivery_id,
                    "response": None,
                    "error": f"Ambiguous outcome during SMTP transaction: {e}",
                    "retryable": False,
                }

            return {
                "status": "FAILED",
                "delivery_id": delivery_id,
                "response": None,
                "error": str(e),
                "retryable": is_retryable,
            }

        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass
