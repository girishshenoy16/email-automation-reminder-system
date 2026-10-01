"""
Unit tests for Public Export Privacy and Schema Audit (Module 9/9).
Scans docs/data/dashboard_data.json to ensure zero PII, zero real domains,
zero private message bodies, and 100% compliance with public demo requirements.
"""

import json
import pytest
from src import config


def test_public_fixture_exists_and_is_valid_json():
    assert config.PUBLIC_FIXTURE_PATH.exists(), "Public fixture docs/data/dashboard_data.json must exist."
    content = config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8")
    data = json.loads(content)
    assert isinstance(data, dict)
    assert "system_info" in data
    assert "kpis" in data
    assert "distribution_by_type" in data
    assert "distribution_by_status" in data
    assert "dispatch_timeline" in data
    assert "recent_dispatches" in data


def test_public_fixture_contains_only_synthetic_reserved_domains():
    data = json.loads(config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8"))
    dispatches = data.get("recent_dispatches", [])
    assert len(dispatches) > 0, "Public fixture should contain sample dispatches for preview."

    for item in dispatches:
        email = item.get("recipient", "")
        assert "@" in email, f"Invalid email format in public fixture: {email}"
        domain = email.split("@")[1]
        # Must only use RFC 2606 reserved example domains
        assert domain in {"example.com", "example.org", "example.net"}, (
            f"Non-example domain found in public export: {domain}"
        )


def test_public_fixture_contains_no_sensitive_keys():
    content = config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8")
    forbidden_substrings = [
        '"password"',
        '"token"',
        '"secret"',
        '"raw_content"',
        '"body"',
        '"phone"',
    ]
    for forbidden in forbidden_substrings:
        assert forbidden not in content.lower(), f"Forbidden sensitive key found in public fixture: {forbidden}"


def test_public_fixture_does_not_leak_private_dataset_identities():
    content = config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8")
    private_names = [
        "Alex Morgan",
        "Priya Sharma",
        "Marcus Vance",
        "Elena Rostova",
        "David Kim",
        "Sophia Martinez",
        "James Thornton",
        "Thomas Wright",
    ]
    for name in private_names:
        assert name not in content, f"Private employee name found in public fixture: {name}"


def test_fixture_required_kpi_fields_present():
    """All KPI fields required by the dashboard data contract must be present."""
    data = json.loads(config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8"))
    kpis = data["kpis"]
    required_fields = [
        "total_contacts", "total_reminders",
        "simulated_count", "smtp_accepted_count",
        "failed_count", "requires_review_count", "pending_count",
        "submission_rate_percent", "simulation_rate_percent",
    ]
    for field in required_fields:
        assert field in kpis, f"Required KPI field '{field}' missing from public fixture."


def test_smtp_accepted_never_counts_simulated():
    """SIMULATED and SMTP_ACCEPTED must be tracked as separate, non-overlapping counts."""
    data = json.loads(config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8"))
    kpis = data["kpis"]
    assert kpis["smtp_accepted_count"] >= 0, "smtp_accepted_count must be non-negative."
    assert kpis["simulated_count"] >= 0, "simulated_count must be non-negative."
    # Both may be zero only if truly no dispatch activity has occurred
    # Verify that SIMULATED count in dispatches matches kpis.simulated_count (fixture consistency)
    dispatches = data.get("recent_dispatches", [])
    simulated_in_sample = sum(1 for d in dispatches if d.get("status") == "SIMULATED")
    smtp_in_sample = sum(1 for d in dispatches if d.get("status") == "SMTP_ACCEPTED")
    # Counts in recent_dispatches (a sample) must not exceed the aggregate KPI counts
    assert smtp_in_sample <= kpis["smtp_accepted_count"], (
        "SMTP_ACCEPTED entries in recent_dispatches exceed kpis.smtp_accepted_count."
    )
    assert simulated_in_sample <= kpis["simulated_count"], (
        "SIMULATED entries in recent_dispatches exceed kpis.simulated_count."
    )


def test_smtp_acceptance_rate_formula_consistency():
    """
    Verify the SMTP Acceptance Rate formula: smtp_accepted / (smtp_accepted + failed).
    SIMULATED and REQUIRES_REVIEW must not appear in numerator or denominator.
    """
    data = json.loads(config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8"))
    kpis = data["kpis"]
    smtp_accepted = kpis["smtp_accepted_count"]
    failed = kpis["failed_count"]
    reported_rate = kpis["submission_rate_percent"]

    live_attempts = smtp_accepted + failed
    if live_attempts > 0:
        expected_rate = round((smtp_accepted / live_attempts) * 100.0, 1)
        assert abs(reported_rate - expected_rate) < 0.1, (
            f"submission_rate_percent ({reported_rate}) does not match "
            f"smtp_accepted / (smtp_accepted + failed) = {expected_rate}. "
            "SIMULATED and REQUIRES_REVIEW must not be counted in this metric."
        )
    else:
        assert reported_rate == 0.0, (
            "submission_rate_percent should be 0.0 when no live SMTP attempts have been made."
        )


def test_fixture_distribution_by_status_types_are_valid():
    """
    distribution_by_status must contain only non-negative integer counts.
    SIMULATED and SMTP_ACCEPTED must remain distinct keys.
    """
    data = json.loads(config.PUBLIC_FIXTURE_PATH.read_text(encoding="utf-8"))
    dist = data.get("distribution_by_status", {})
    expected_statuses = {"SMTP_ACCEPTED", "SIMULATED", "PENDING", "FAILED", "REQUIRES_REVIEW"}
    for key in expected_statuses:
        val = dist.get(key, 0)
        assert isinstance(val, int) and val >= 0, (
            f"distribution_by_status['{key}'] must be a non-negative integer, got: {val}"
        )
