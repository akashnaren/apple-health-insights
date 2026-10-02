"""Display helpers for aggregate numbers.

Missing measurements stay missing. Callers pass a real value only when at
least one sample existed.
"""

from __future__ import annotations


def format_minutes(minutes: float) -> str:
    total = int(round(minutes))
    hours, mins = divmod(abs(total), 60)
    sign = "-" if total < 0 else ""
    if hours and mins:
        body = f"{hours}h {mins}m"
    elif hours:
        body = f"{hours}h"
    else:
        body = f"{mins}m"
    return f"{sign}{body}"


def format_count(value: int) -> str:
    return f"{value:,}"


def format_grams(grams: int) -> str:
    negative = grams < 0
    remainder = abs(grams)
    whole = remainder // 1000
    tenth = int(round((remainder % 1000) / 100))
    if tenth == 10:
        whole += 1
        tenth = 0
    text = f"{whole}.{tenth} kg"
    if negative and (whole or tenth):
        return f"-{text}"
    return text


def format_signed_grams(grams: int) -> str:
    if grams > 0:
        return f"+{format_grams(grams)}"
    return format_grams(grams)


def night_phrase(count: int) -> str:
    return "1 night" if count == 1 else f"{count} nights"


def day_phrase(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"
