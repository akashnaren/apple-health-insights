from __future__ import annotations

import re
from datetime import date, timedelta

from apple_health_insights.insight import build_insight
from apple_health_insights.models import Day, Night, NormalizedExport
from apple_health_insights.overview import consistency_label, render_overview


def _export(*, nights: list[Night], days: list[Day] | None = None, as_of: date = date(2026, 10, 1)) -> NormalizedExport:
    days = days or []
    return NormalizedExport(
        as_of=as_of,
        timezone="-0700",
        source_name="export.xml",
        synthetic=True,
        nights=nights,
        days=days,
        workouts=[],
        masses=[],
        present={
            "sleep": bool(nights),
            "steps": any(day.steps is not None for day in days),
            "exercise": False,
            "active_energy": any(day.active_kcal is not None for day in days),
            "resting_hr": False,
            "hrv": False,
            "workouts": False,
            "body_mass": False,
        },
    )


def test_sleep_debt_when_the_baseline_exists():
    nights = [Night(date(2026, 10, 1) - timedelta(days=offset), 450) for offset in range(16, 2, -1)]
    nights.extend(
        [
            Night(date(2026, 9, 29), 330),
            Night(date(2026, 9, 30), 330),
            Night(date(2026, 10, 1), 330),
        ]
    )
    insight = build_insight(_export(nights=nights))
    assert insight.kind == "sleep_debt"
    assert insight.title == "Sleep debt vs 14-day baseline"
    assert "5h 30m" in insight.body
    assert "7h 30m" in insight.body
    assert "14 nights" in insight.body
    assert "2h shorter" in insight.body
    assert "not medical advice" in insight.body


def test_close_to_baseline_is_still_the_sleep_insight():
    # Sep 15 through Oct 1, all 7h 30m. Baseline is the 14 nights before the last 3 dates.
    nights = [Night(date(2026, 10, 1) - timedelta(days=offset), 450) for offset in range(16, -1, -1)]
    insight = build_insight(_export(nights=nights))
    assert insight.kind == "sleep_debt"
    assert "within 30 minutes" in insight.body
    assert "14 nights" in insight.body


def test_mismatch_when_sleep_baseline_is_too_short():
    nights = [Night(date(2026, 9, day), 480) for day in range(24, 29)]
    nights.extend(
        [
            Night(date(2026, 9, 29), 300),
            Night(date(2026, 9, 30), 300),
            Night(date(2026, 10, 1), 300),
        ]
    )
    days = [Day(date(2026, 9, day), steps=5000) for day in range(24, 29)]
    days.extend(
        [
            Day(date(2026, 9, 29), steps=12000),
            Day(date(2026, 9, 30), steps=12000),
            Day(date(2026, 10, 1), steps=12000),
        ]
    )
    insight = build_insight(_export(nights=nights, days=days))
    assert insight.kind == "strain_recovery_mismatch"
    assert "12,000" in insight.body
    assert "5,000" in insight.body
    assert "higher than its recent baseline" in insight.body
    assert "5h" in insight.body
    assert "8h" in insight.body


def test_no_insight_without_sleep():
    days = [Day(date(2026, 10, 1), steps=4000)]
    insight = build_insight(_export(nights=[], days=days))
    assert insight.kind == "none"
    assert "No insight yet" == insight.title


def test_consistency_flag():
    assert consistency_label([450, 450]) == "not enough nights to judge consistency"
    assert consistency_label([450, 440, 455]) == "steady"
    assert consistency_label([450, 450, 450, 330, 330, 330, 330]) == "variable"


def test_missing_day_is_not_zero_in_the_overview():
    export = _export(
        nights=[Night(date(2026, 9, 30), 400)],
        days=[Day(date(2026, 9, 30), steps=1000, resting_hr_bpm=60)],
    )
    export.present["resting_hr"] = True
    export.present["hrv"] = False
    export.present["body_mass"] = False
    text = render_overview(export, build_insight(export))
    assert "Steps today: no data" in text
    assert "Last night: no data" in text
    assert "Heart rate variability (SDNN): no data in this export" in text
    assert "Resting heart rate, 7-day average: 60 bpm (1 day)." in text
    assert "Prior 7 days: no data" in text
    assert re.search(r"(?<!\d)0 bpm", text) is None
    assert re.search(r"(?<!\d)0 kg", text) is None
    assert re.search(r"(?<!\d)0 min", text) is None
