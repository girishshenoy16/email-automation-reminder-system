"""
Email Template Rendering Engine and Multipart MIME Assembler.
Compiles dual-part MIME EmailMessage objects with HTML and plain-text fallback.
Uses regex placeholder substitution to avoid collisions with CSS { ... } blocks.
"""

import html
import logging
import math
import re
from collections import defaultdict
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from src import config
from src.reporter import mask_email

logger = logging.getLogger(__name__)

KNOWN_PLACEHOLDERS = {
    "name",
    "event_title",
    "due_date",
    "due_time",
    "amount_due",
    "meeting_link",
    "department",
    "company",
    "invoice_id",
    "email",
    "contact_id",
    "reminder_id",
    "phone",
}


def sanitize_placeholder_value(key: str, val: Any, escape_html: bool = False) -> str:
    """
    Sanitizes raw context values:
    - Replaces None, float NaN, or 'nan'/'null' strings with 'N/A'.
    - Neutralizes dangerous URL schemes (javascript:, data:, vbscript:) in meeting links.
    - Applies HTML entity escaping when escape_html=True to prevent template XSS.
    """
    if val is None:
        return "N/A"
    if isinstance(val, float) and math.isnan(val):
        return "N/A"

    val_str = str(val).strip()
    if val_str.lower() in ("nan", "none", "null", ""):
        return "N/A"

    # Neutralize dangerous URL schemes in link placeholders
    if key == "meeting_link":
        lower_val = val_str.lower()
        if lower_val.startswith(("javascript:", "data:", "vbscript:", "file:")):
            logger.warning(f"Blocked dangerous URL scheme in meeting_link: {val_str[:20]}")
            return "#"

    if escape_html:
        return html.escape(val_str, quote=True)
    return val_str


class SafeDict(defaultdict):
    """Dictionary that returns 'N/A' for missing known keys instead of raising KeyError."""
    def __init__(self, *args, **kwargs):
        super().__init__(lambda: "N/A", *args, **kwargs)

    def __missing__(self, key: str) -> str:
        return "N/A"


SUBJECT_PATTERNS = {
    "meeting": "Upcoming Meeting Alert: {event_title}",
    "payment": "Important: Payment Reminder for {event_title}",
    "webinar": "Reminder: Upcoming Live Webinar on {event_title}",
}


def load_template_pair(reminder_type: str, templates_dir: Optional[Path] = None) -> Tuple[str, str]:
    """
    Loads corresponding HTML and Plain-text templates from disk.
    Falls back to meeting reminder if specific type not found.
    """
    base_dir = templates_dir or config.TEMPLATES_DIR
    clean_type = (reminder_type or "meeting").lower().strip()

    html_file = base_dir / f"{clean_type}_reminder.html"
    txt_file = base_dir / f"{clean_type}_reminder.txt"

    if not html_file.exists() or not txt_file.exists():
        logger.warning(
            f"Template pair for '{clean_type}' not found in {base_dir}; falling back to 'meeting'."
        )
        clean_type = "meeting"
        html_file = base_dir / f"{clean_type}_reminder.html"
        txt_file = base_dir / f"{clean_type}_reminder.txt"

    if not html_file.exists() or not txt_file.exists():
        logger.error(f"Fallback meeting templates missing in {base_dir}")
        raise FileNotFoundError(f"Template files for '{clean_type}' not found in {base_dir}")

    html_content = html_file.read_text(encoding="utf-8")
    txt_content = txt_file.read_text(encoding="utf-8")

    return html_content, txt_content


def render_template(content: str, context: Dict[str, Any], escape_html: bool = False) -> str:
    """
    Safely injects context variables into template content.
    Matches {variable_name} while safely preserving CSS { ... } declarations.
    Missing or NaN known variables are replaced with 'N/A'.
    Applies HTML entity escaping when escape_html=True.
    """
    safe_context = SafeDict({
        k: sanitize_placeholder_value(k, v, escape_html=escape_html)
        for k, v in context.items()
    })

    def replace_placeholder(match: re.Match) -> str:
        key = match.group(1)
        if key in context:
            return safe_context[key]
        if key in KNOWN_PLACEHOLDERS:
            logger.debug(f"Missing template placeholder '{key}' replaced with fallback 'N/A'")
            return safe_context[key]
        return match.group(0)

    # Matches only single identifiers inside curly braces (e.g., {name}, {due_date})
    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", replace_placeholder, content)


def build_mime_message(
    reminder_type: str,
    context: Dict[str, Any],
    recipient_email: str,
    custom_subject: Optional[str] = None,
    templates_dir: Optional[Path] = None
) -> Tuple[EmailMessage, str, str, str]:
    """
    Builds a multipart/alternative EmailMessage with both plaintext and HTML alternatives.
    Sanitizes subject and recipient to prevent CRLF email header injection.
    Returns (msg, plain_body, html_body, subject).
    """
    clean_type = (reminder_type or "meeting").lower().strip()
    html_raw, txt_raw = load_template_pair(clean_type, templates_dir)

    # HTML body gets HTML entity escaping; plaintext body retains raw text
    html_body = render_template(html_raw, context, escape_html=True)
    plain_body = render_template(txt_raw, context, escape_html=False)

    if custom_subject:
        raw_subject = custom_subject
    else:
        subject_pattern = SUBJECT_PATTERNS.get(clean_type, "Reminder: {event_title}")
        raw_subject = render_template(subject_pattern, context, escape_html=False)

    # Strict CRLF sanitization to prevent Email Header Injection (CWE-93)
    subject = re.sub(r"[\r\n]+", " ", raw_subject).strip()
    clean_recipient = re.sub(r"[\r\n]+", "", recipient_email).strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SENDER_NAME, config.SENDER_EMAIL))
    msg["To"] = clean_recipient

    # Plain text is primary payload; HTML is added as alternative
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    logger.debug(
        f"MIME assembly complete: type='{clean_type}', subject='{subject}', to='{mask_email(clean_recipient)}'"
    )
    return msg, plain_body, html_body, subject
