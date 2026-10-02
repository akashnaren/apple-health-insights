"""Pick one insight from the normalized export.

Sleep debt runs first when the 14-day baseline exists. Otherwise, if a shorter
activity and sleep history exists, report whether strain and sleep moved in
opposite directions. Neither path gives advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from apple_health_insights.formatting import format_count, format_minutes, night_phrase
from apple_health_insights.models import NormalizedExport
from apple_health_insights.normalize import inclusive_window, nights_between, values_between

NOT_ADVICE = (
    "This is a description of one export. It is not medical advice, a diagnosis, or a recommendation."
)
SLEEP_DEBT_GAP_MINUTES = 30
SHORT_SLEEP_GAP_MINUTES = 45
STRAIN_RATIO = 1.2
MIN_SLEEP_BASELINE = 7
MIN_MISMATCH_SLEEP = 4
MIN_MISMATCH_ACTIVITY = 4


@dataclass(frozen=True)
class Insight:
    kind: str
    title: str
    body: str
    thesis: str


def build_insight(export: NormalizedExport) -> Insight:
    if export.as_of is None:
        return _none(export, "This export has no readable dates, so the windows for an insight are undefined.")
    debt = _sleep_debt(export)
    if debt is not None:
        return debt
    mismatch = _mismatch(export)
    if mismatch is not None:
        return mismatch
    return _none(
        export,
        "Sleep debt needs at least 7 nights in the 14 days before the latest 3 dates. "
        "The strain and recovery check needs a shorter stretch of both activity and sleep. "
        "This export does not have enough of either.",
    )


def _sleep_debt(export: NormalizedExport) -> Insight | None:
    if not export.present.get("sleep") or export.as_of is None:
        return None
    recent_start, recent_end = inclusive_window(export.as_of, 2, 0)
    base_start, base_end = inclusive_window(export.as_of, 16, 3)
    recent = nights_between(export.nights, recent_start, recent_end)
    baseline = nights_between(export.nights, base_start, base_end)
    if len(recent) < 1 or len(baseline) < MIN_SLEEP_BASELINE:
        return None
    recent_avg = fmean(night.asleep_minutes for night in recent)
    base_avg = fmean(night.asleep_minutes for night in baseline)
    gap = base_avg - recent_avg
    when = export.as_of.isoformat()
    if gap >= SLEEP_DEBT_GAP_MINUTES:
        comparison = f"Recent nights are {format_minutes(gap)} shorter than that baseline."
        thesis = f"As of {when}, the latest nights in this export are shorter than the prior 14-day sleep baseline."
    elif gap <= -SLEEP_DEBT_GAP_MINUTES:
        comparison = f"Recent nights are {format_minutes(abs(gap))} longer than that baseline."
        thesis = f"As of {when}, the latest nights in this export are longer than the prior 14-day sleep baseline."
    else:
        comparison = "Recent nights are within 30 minutes of that baseline."
        thesis = f"As of {when}, the latest nights in this export sit close to the prior 14-day sleep baseline."
    body = " ".join(
        [
            (
                f"Across the last 3 dates, {night_phrase(len(recent))} averaged "
                f"{format_minutes(recent_avg)} asleep."
            ),
            (
                f"The prior 14-day baseline averaged {format_minutes(base_avg)} "
                f"across {night_phrase(len(baseline))}."
            ),
            comparison,
            NOT_ADVICE,
        ]
    )
    return Insight("sleep_debt", "Sleep debt vs 14-day baseline", body, thesis)


def _mismatch(export: NormalizedExport) -> Insight | None:
    if export.as_of is None:
        return None
    if not export.present.get("sleep"):
        return None
    if not (export.present.get("steps") or export.present.get("active_energy")):
        return None
    recent_start, recent_end = inclusive_window(export.as_of, 2, 0)
    base_start, base_end = inclusive_window(export.as_of, 16, 3)
    recent_sleep = nights_between(export.nights, recent_start, recent_end)
    base_sleep = nights_between(export.nights, base_start, base_end)
    metric, recent_activity, base_activity = _activity_sides(
        export, recent_start, recent_end, base_start, base_end
    )
    if metric is None or len(recent_sleep) < 2 or len(base_sleep) < MIN_MISMATCH_SLEEP:
        return None
    if len(recent_activity) < 1 or len(base_activity) < MIN_MISMATCH_ACTIVITY:
        return None
    recent_sleep_avg = fmean(night.asleep_minutes for night in recent_sleep)
    base_sleep_avg = fmean(night.asleep_minutes for night in base_sleep)
    recent_act_avg = fmean(recent_activity)
    base_act_avg = fmean(base_activity)
    short_sleep = recent_sleep_avg <= base_sleep_avg - SHORT_SLEEP_GAP_MINUTES
    high_strain = base_act_avg > 0 and recent_act_avg >= base_act_avg * STRAIN_RATIO
    when = export.as_of.isoformat()
    label = "Steps" if metric == "steps" else "Active energy"
    rendered_unit = "" if metric == "steps" else " kcal"
    activity_sentence = (
        f"{label} over the last 3 dates averaged {format_count(int(round(recent_act_avg)))}{rendered_unit} "
        f"versus {format_count(int(round(base_act_avg)))}{rendered_unit} on the prior baseline "
        f"({len(base_activity)} days)."
    )
    sleep_sentence = (
        f"Sleep over those recent dates averaged {format_minutes(recent_sleep_avg)} "
        f"versus {format_minutes(base_sleep_avg)} on the earlier nights in this export "
        f"({night_phrase(len(base_sleep))})."
    )
    if high_strain and short_sleep:
        closing = (
            "Those two gaps show up together: activity is higher than its recent baseline, "
            "and sleep is shorter than the earlier nights."
        )
        thesis = (
            f"As of {when}, recent activity in this export runs higher than its baseline "
            "while recent sleep runs shorter."
        )
    else:
        closing = (
            "Activity and sleep are both present, and they do not show that pattern together "
            "(higher activity than baseline, and shorter sleep than the earlier nights)."
        )
        thesis = (
            f"As of {when}, activity and sleep are both present, and this export does not "
            "show a strain and recovery mismatch."
        )
    body = " ".join([activity_sentence, sleep_sentence, closing, NOT_ADVICE])
    return Insight("strain_recovery_mismatch", "Strain and recovery mismatch", body, thesis)


def _activity_sides(export, recent_start, recent_end, base_start, base_end):
    if export.present.get("steps"):
        recent = values_between(export.days, recent_start, recent_end, "steps")
        baseline = values_between(export.days, base_start, base_end, "steps")
        if recent and baseline:
            return "steps", recent, baseline
    if export.present.get("active_energy"):
        recent = values_between(export.days, recent_start, recent_end, "active_kcal")
        baseline = values_between(export.days, base_start, base_end, "active_kcal")
        if recent and baseline:
            return "active_energy", recent, baseline
    return None, [], []


def _none(export: NormalizedExport, reason: str) -> Insight:
    if export.as_of is None:
        thesis = "This export does not have enough dated history for an insight."
    else:
        thesis = (
            f"As of {export.as_of.isoformat()}, this export does not have enough sleep "
            "and activity history for an insight."
        )
    return Insight("none", "No insight yet", f"{reason} {NOT_ADVICE}", thesis)
