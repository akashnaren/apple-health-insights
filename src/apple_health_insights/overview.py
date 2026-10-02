"""Write the living overview as aggregate markdown.

Every line is a rollup. Source names, record timestamps, and unused series
stay out of the document.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import pstdev

from apple_health_insights.formatting import (
    day_phrase,
    format_count,
    format_grams,
    format_minutes,
    format_signed_grams,
    night_phrase,
)
from apple_health_insights.insight import Insight
from apple_health_insights.models import Day, NormalizedExport, WorkoutEntry
from apple_health_insights.normalize import inclusive_window, nights_between, values_between

NO_SERIES = "no data in this export"
BANNER = (
    "Observational summary of an Apple Health export. "
    "Not medical advice, a diagnosis, or a training plan."
)


def render_overview(export: NormalizedExport, insight: Insight) -> str:
    lines = [
        "# Health overview",
        "",
        BANNER,
        "",
        f"**As of:** {export.as_of.isoformat() if export.as_of else 'unknown'}",
        f"**Input:** {export.source_name}",
    ]
    if export.timezone:
        lines.append(f"**Timezone:** {export.timezone}")
    if export.synthetic:
        lines.append("")
        lines.append("This run used a synthetic fixture, not a personal export.")
    lines.extend(["", insight.thesis, ""])
    lines.extend(_sleep_section(export))
    lines.extend(_activity_section(export))
    lines.extend(_cardio_section(export))
    lines.extend(_workout_section(export))
    lines.extend(_body_section(export))
    lines.extend(
        [
            "## Insight",
            "",
            f"**{insight.title}**",
            "",
            insight.body,
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_empty_overview() -> str:
    sections = [
        "## Recovery / sleep",
        "",
        NO_SERIES,
        "",
        "## Strain / activity",
        "",
        NO_SERIES,
        "",
        "## Cardio",
        "",
        NO_SERIES,
        "",
        "## Workouts",
        "",
        NO_SERIES,
        "",
        "## Body",
        "",
        NO_SERIES,
        "",
        "## Insight",
        "",
        "**No insight yet**",
        "",
        "There is no export to read, so there is no sleep-debt or strain and recovery comparison. "
        "This is a description of one export. It is not medical advice, a diagnosis, or a recommendation.",
        "",
    ]
    lines = [
        "# Health overview",
        "",
        BANNER,
        "",
        "**Status:** no export",
        "",
        "No Apple Health zip or export.xml was found. Nothing was filled in to cover the gap.",
        "",
        *sections,
    ]
    return "\n".join(lines)


def consistency_label(minutes: list[int]) -> str:
    if len(minutes) < 3:
        return "not enough nights to judge consistency"
    spread = pstdev(minutes)
    if spread < 45:
        return "steady"
    return "variable"


def _sleep_section(export: NormalizedExport) -> list[str]:
    lines = ["## Recovery / sleep", ""]
    if not export.present.get("sleep") or export.as_of is None:
        return lines + [NO_SERIES, ""]
    last = next((night for night in export.nights if night.date == export.as_of), None)
    if last is None:
        lines.append("Last night: no data")
    else:
        lines.append(
            f"Last night: {format_minutes(last.asleep_minutes)} asleep (ended {last.date.isoformat()})."
        )
    start, end = inclusive_window(export.as_of, 6, 0)
    week = nights_between(export.nights, start, end)
    if not week:
        lines.append("7-day average: no data")
        lines.append("Consistency across those nights: not enough nights to judge consistency")
    else:
        average = sum(night.asleep_minutes for night in week) / len(week)
        lines.append(f"7-day average: {format_minutes(average)} ({night_phrase(len(week))}).")
        lines.append(
            "Consistency across those nights: "
            f"{consistency_label([night.asleep_minutes for night in week])}."
        )
    lines.append("")
    return lines


def _activity_section(export: NormalizedExport) -> list[str]:
    lines = ["## Strain / activity", ""]
    if export.as_of is None or not any(
        export.present.get(key) for key in ("steps", "exercise", "active_energy")
    ):
        return lines + [NO_SERIES, ""]
    specs = (
        ("steps", "Steps", "steps", ""),
        ("exercise", "Exercise", "exercise_minutes", " min"),
        ("active_energy", "Active energy", "active_kcal", " kcal"),
    )
    start, end = inclusive_window(export.as_of, 6, 0)
    for present_key, label, attr, suffix in specs:
        if not export.present.get(present_key):
            lines.append(f"{label}: {NO_SERIES}")
            continue
        today = _today_value(export.days, export.as_of, attr)
        if today is None:
            lines.append(f"{label} today: no data")
        else:
            shown = format_count(today) if suffix == "" else f"{format_count(today)}{suffix}"
            lines.append(f"{label} today: {shown}.")
        window = values_between(export.days, start, end, attr)
        if not window:
            lines.append(f"{label}, 7-day average: no data")
        else:
            average = int(round(sum(window) / len(window)))
            shown = format_count(average) if suffix == "" else f"{format_count(average)}{suffix}"
            lines.append(f"{label}, 7-day average: {shown} ({day_phrase(len(window))}).")
    lines.append("")
    return lines


def _cardio_section(export: NormalizedExport) -> list[str]:
    lines = ["## Cardio", ""]
    if export.as_of is None or not any(export.present.get(key) for key in ("resting_hr", "hrv")):
        return lines + [NO_SERIES, ""]
    if not export.present.get("resting_hr"):
        lines.append(f"Resting heart rate: {NO_SERIES}")
    else:
        lines.append(_rate_line(export, "resting_hr_bpm", "Resting heart rate", "bpm"))
    if not export.present.get("hrv"):
        lines.append(f"Heart rate variability (SDNN): {NO_SERIES}")
    else:
        lines.append(_rate_line(export, "hrv_sdnn_ms", "Heart rate variability (SDNN)", "ms"))
    lines.append("")
    return lines


def _rate_line(export: NormalizedExport, attr: str, label: str, unit: str) -> str:
    assert export.as_of is not None
    recent = values_between(export.days, *inclusive_window(export.as_of, 6, 0), attr)
    prior = values_between(export.days, *inclusive_window(export.as_of, 13, 7), attr)
    if not recent:
        return f"{label}: no data"
    recent_avg = int(round(sum(recent) / len(recent)))
    text = f"{label}, 7-day average: {recent_avg} {unit} ({day_phrase(len(recent))})."
    if not prior:
        return text + " Prior 7 days: no data"
    prior_avg = int(round(sum(prior) / len(prior)))
    delta = recent_avg - prior_avg
    if abs(delta) < 2:
        direction = "about the same"
        text += f" Prior 7 days: {prior_avg} {unit} ({day_phrase(len(prior))}), {direction}."
    elif delta > 0:
        text += (
            f" Prior 7 days: {prior_avg} {unit} ({day_phrase(len(prior))}), "
            f"about {delta} {unit} higher."
        )
    else:
        text += (
            f" Prior 7 days: {prior_avg} {unit} ({day_phrase(len(prior))}), "
            f"about {abs(delta)} {unit} lower."
        )
    return text


def _workout_section(export: NormalizedExport) -> list[str]:
    lines = ["## Workouts", ""]
    if not export.present.get("workouts") or export.as_of is None:
        return lines + [NO_SERIES, ""]
    lines.append(_workout_window(export, 6, "Last 7 days"))
    lines.append(_workout_window(export, 29, "Last 30 days"))
    lines.append("")
    return lines


def _workout_window(export: NormalizedExport, start_days_ago: int, label: str) -> str:
    assert export.as_of is not None
    start, end = inclusive_window(export.as_of, start_days_ago, 0)
    chosen = [workout for workout in export.workouts if start <= workout.date <= end]
    if not chosen:
        return f"{label}: no workouts in this window."
    return f"{label}: {len(chosen)} workouts. {_type_list(chosen)}."


def _type_list(workouts: list[WorkoutEntry]) -> str:
    counts: dict[str, int] = {}
    for workout in workouts:
        counts[workout.activity] = counts.get(workout.activity, 0) + 1
    ordered = sorted(counts, key=lambda name: (-counts[name], name))
    return ", ".join(f"{name} {counts[name]}" for name in ordered)


def _body_section(export: NormalizedExport) -> list[str]:
    lines = ["## Body", ""]
    if not export.present.get("body_mass"):
        return lines + [NO_SERIES, ""]
    latest = export.masses[-1]
    lines.append(f"Latest: {format_grams(latest.grams)} on {latest.date.isoformat()}.")
    if export.as_of is None:
        lines.append("Change over the prior 30 days: no data (no export date).")
        lines.append("")
        return lines
    start = export.as_of - timedelta(days=30)
    window = [point for point in export.masses if start <= point.date <= export.as_of]
    if len(window) < 2:
        lines.append("Change over the prior 30 days: not enough points to describe a change.")
    else:
        oldest = window[0]
        newest = window[-1]
        delta = newest.grams - oldest.grams
        lines.append(
            "Change over the prior 30 days: "
            f"{format_signed_grams(delta)} "
            f"({format_grams(newest.grams)} on {newest.date.isoformat()} versus "
            f"{format_grams(oldest.grams)} on {oldest.date.isoformat()})."
        )
    lines.append("")
    return lines


def _today_value(days: list[Day], as_of: date, attr: str) -> int | None:
    for day in days:
        if day.date == as_of:
            return getattr(day, attr)
    return None
