"""Fixture-only helper: lists the fields a claim intake checklist expects.

This file is part of an end-to-end test fixture. The product never executes
a Skill package's scripts -- it only stores them and marks them as such
("saved, not executed") in the import preview.
"""

REQUIRED_FIELDS = ["claimant_name", "policy_number", "incident_date"]


def check_fields(claim: dict) -> list[str]:
    """Return the required fields missing from `claim`."""
    return [field for field in REQUIRED_FIELDS if field not in claim]
