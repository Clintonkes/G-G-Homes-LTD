"""Reusable helper utilities for formatting values such as phone numbers and currency."""

import re


def format_phone_number(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone)
    if digits.startswith("0"):
        digits = "234" + digits[1:]
    if not digits.startswith("234"):
        digits = f"234{digits}"
    return digits


def format_naira(amount: float) -> str:
    return f"N{amount:,.2f}"
