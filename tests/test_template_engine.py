"""
Unit tests for Email Template Engine and MIME Assembler (Module 2/9).
"""

import pytest
from email.message import EmailMessage
from src.template_engine import (
    load_template_pair,
    render_template,
    build_mime_message,
    SafeDict,
)


def test_safe_dict_missing_key():
    sd = SafeDict({"name": "Jordan"})
    assert sd["name"] == "Jordan"
    assert sd["nonexistent_key"] == "N/A"


def test_load_template_pair_all_types():
    for r_type in ["meeting", "payment", "webinar"]:
        html, txt = load_template_pair(r_type)
        assert "<html" in html.lower()
        assert "{name}" in html
        assert "{name}" in txt


def test_load_template_pair_fallback():
    # An unknown reminder type should fall back to meeting reminder
    html, txt = load_template_pair("non_existent_type")
    assert "<html" in html.lower()
    assert "{event_title}" in txt


def test_render_template_variable_substitution():
    tmpl = "Hello {name}, your meeting {event_title} is on {due_date}."
    ctx = {"name": "Elena", "event_title": "Sprint Planning", "due_date": "2026-09-30"}
    rendered = render_template(tmpl, ctx)
    assert "Hello Elena" in rendered
    assert "Sprint Planning" in rendered
    assert "2026-09-30" in rendered


def test_render_template_missing_keys_safe():
    tmpl = "Hello {name}, amount: {amount_due}, link: {meeting_link}."
    ctx = {"name": "Marcus"}  # amount_due and meeting_link are missing
    rendered = render_template(tmpl, ctx)
    assert "Hello Marcus" in rendered
    assert "amount: N/A" in rendered
    assert "link: N/A" in rendered


def test_build_mime_message_multipart():
    ctx = {
        "name": "Priya",
        "event_title": "Ops Sync",
        "due_date": "2026-09-25",
        "due_time": "14:00",
        "department": "Operations",
        "meeting_link": "https://meet.example.com",
    }
    msg, plain, html, subject = build_mime_message(
        reminder_type="meeting",
        context=ctx,
        recipient_email="priya@example.com",
    )

    assert isinstance(msg, EmailMessage)
    assert msg["To"] == "priya@example.com"
    assert "Ops Sync" in subject
    assert msg.is_multipart()

    # Verify payloads
    payloads = msg.get_payload()
    assert len(payloads) == 2
    content_types = [p.get_content_type() for p in payloads]
    assert "text/plain" in content_types
    assert "text/html" in content_types
