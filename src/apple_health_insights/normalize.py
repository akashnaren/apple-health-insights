"""Collapse samples into nightly sleep, daily totals, and workout counts.

Quantity records win over ActivitySummary on the same day so the two Apple
sources are not added together. A missing day stays missing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from apple_health_insights.models import (
    Day,
    Interval,
    MassPoint,
    Night,
    NormalizedExport,
    ParsedExport,
    WorkoutEntry,
)


def normalize(parsed: ParsedExport, *, source_name: str, synthetic: bool) -> NormalizedExport:
    nights = _sleep_nights(parsed.sleep_asleep, parsed.sleep_in_bed)
    days = _days_from_quantities(parsed)
    _apply_summary_fallback(days, parsed)
    workouts = _workouts(parsed)
    masses = _masses(parsed)
    as_of, timezone = _as_of(parsed, nights, days, workouts, masses)
    ordered_days = [days[key] for key in sorted(days)]
    return NormalizedExport(
        as_of=as_of,
        timezone=timezone,
        source_name=source_name,
        synthetic=synthetic,
        nights=nights,
        days=ordered_days,
        workouts=workouts,
        masses=masses,
        present={
            "sleep": bool(nights),
            "steps": any(day.steps is not None for day in ordered_days),
            "exercise": any(day.exercise_minutes is not None for day in ordered_days),
            "active_energy": any(day.active_kcal is not None for day in ordered_days),
            "resting_hr": any(day.resting_hr_bpm is not None for day in ordered_days),
            "hrv": any(day.hrv_sdnn_ms is not None for day in ordered_days),
            "workouts": bool(workouts),
            "body_mass": bool(masses),
        },
    )


def merge_minutes(intervals: list[tuple[datetime, datetime]]) -> int:
    usable = sorted((start, end) for start, end in intervals if end > start)
    if not usable:
        return 0
    total = 0
    cursor_start, cursor_end = usable[0]
    for start, end in usable[1:]:
        if start <= cursor_end:
            cursor_end = max(cursor_end, end)
            continue
        total += int((cursor_end - cursor_start).total_seconds())
        cursor_start, cursor_end = start, end
    total += int((cursor_end - cursor_start).total_seconds())
    return int((Decimal(total) / Decimal(60)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _sleep_nights(asleep: list[Interval], in_bed: list[Interval]) -> list[Night]:
    asleep_by_day: dict[date, list[tuple[datetime, datetime]]] = defaultdict(list)
    bed_by_day: dict[date, list[tuple[datetime, datetime]]] = defaultdict(list)
    for interval in asleep:
        asleep_by_day[interval.end.date()].append((interval.start, interval.end))
    for interval in in_bed:
        bed_by_day[interval.end.date()].append((interval.start, interval.end))
    nights: list[Night] = []
    for day in sorted(set(asleep_by_day) | set(bed_by_day)):
        chosen = asleep_by_day[day] or bed_by_day[day]
        minutes = merge_minutes(chosen)
        if minutes <= 0:
            continue
        nights.append(Night(date=day, asleep_minutes=minutes))
    return nights


def _days_from_quantities(parsed: ParsedExport) -> dict[date, Day]:
    days: dict[date, Day] = {}
    sums: dict[tuple[date, str], Decimal] = defaultdict(lambda: Decimal(0))
    counts: dict[tuple[date, str], int] = defaultdict(int)

    def day_for(when: date) -> Day:
        return days.setdefault(when, Day(date=when))

    for sample in parsed.steps:
        sums[(sample.start.date(), "steps")] += sample.value
    for sample in parsed.exercise:
        sums[(sample.start.date(), "exercise")] += sample.value
    for sample in parsed.active_energy:
        sums[(sample.start.date(), "energy")] += sample.value
    for sample in parsed.resting_hr:
        key = (sample.start.date(), "rhr")
        sums[key] += sample.value
        counts[key] += 1
    for sample in parsed.hrv:
        key = (sample.start.date(), "hrv")
        sums[key] += sample.value
        counts[key] += 1

    for (day, kind), total in sums.items():
        slot = day_for(day)
        if kind == "steps":
            slot.steps = _round_int(total)
        elif kind == "exercise":
            slot.exercise_minutes = _round_int(total)
        elif kind == "energy":
            slot.active_kcal = _round_int(total)
        elif kind == "rhr":
            slot.resting_hr_bpm = _round_int(total / Decimal(counts[(day, kind)]))
        elif kind == "hrv":
            slot.hrv_sdnn_ms = _round_int(total / Decimal(counts[(day, kind)]))
    return days


def _apply_summary_fallback(days: dict[date, Day], parsed: ParsedExport) -> None:
    for summary in parsed.summaries:
        slot = days.setdefault(summary.day, Day(date=summary.day))
        if slot.exercise_minutes is None and summary.exercise_minutes is not None:
            slot.exercise_minutes = summary.exercise_minutes
        if slot.active_kcal is None and summary.active_kcal is not None:
            slot.active_kcal = summary.active_kcal


def _workouts(parsed: ParsedExport) -> list[WorkoutEntry]:
    entries = [
        WorkoutEntry(
            date=workout.start.date(),
            activity=workout.activity,
            duration_minutes=workout.duration_minutes,
        )
        for workout in parsed.workouts
    ]
    return sorted(entries, key=lambda item: (item.date, item.activity, item.duration_minutes or 0))


def _masses(parsed: ParsedExport) -> list[MassPoint]:
    # One point per calendar day: the last sample that day.
    latest: dict[date, tuple[datetime, int]] = {}
    for when, grams in parsed.body_mass_grams:
        current = latest.get(when.date())
        if current is None or when >= current[0]:
            latest[when.date()] = (when, grams)
    return [MassPoint(date=day, grams=latest[day][1]) for day in sorted(latest)]


def _as_of(
    parsed: ParsedExport,
    nights: list[Night],
    days: dict[date, Day],
    workouts: list[WorkoutEntry],
    masses: list[MassPoint],
) -> tuple[date | None, str | None]:
    if parsed.exported_at is not None:
        label = parsed.exported_at.strftime("%z") or None
        return parsed.exported_at.date(), label
    dates: list[date] = [night.date for night in nights]
    dates.extend(days)
    dates.extend(workout.date for workout in workouts)
    dates.extend(point.date for point in masses)
    if not dates:
        return None, None
    return max(dates), None


def values_between(days: list[Day], start: date, end: date, attr: str) -> list[int]:
    found = []
    for day in days:
        if start <= day.date <= end:
            value = getattr(day, attr)
            if value is not None:
                found.append(value)
    return found


def nights_between(nights: list[Night], start: date, end: date) -> list[Night]:
    return [night for night in nights if start <= night.date <= end]


def inclusive_window(as_of: date, start_days_ago: int, end_days_ago: int = 0) -> tuple[date, date]:
    return as_of - timedelta(days=start_days_ago), as_of - timedelta(days=end_days_ago)


def _round_int(number: Decimal) -> int:
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
