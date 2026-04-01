"""Reusable helper utilities for formatting values such as phone numbers and currency."""

import re


def format_phone_number(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone)
    # Strip leading zeros before adding country code
    digits = digits.lstrip("0")
    if not digits:
        return ""
    if digits.startswith("234"):
        # Already has country code
        pass
    elif len(digits) == 10:
        # Local 10-digit number, add country code
        digits = f"234{digits}"
    elif not digits.startswith("234"):
        digits = f"234{digits}"
    return digits


def format_naira(amount: float) -> str:
    return f"N{amount:,.2f}"


def parse_naira_amount(raw_value: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", raw_value or "")
    if cleaned.count(".") > 1 or not cleaned:
        raise ValueError("Invalid naira amount")
    return float(cleaned)
