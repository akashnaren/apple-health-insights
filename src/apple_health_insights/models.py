"""In-memory shapes for one export.

Samples exist only while a run is parsing. What gets saved is the normalized
daily and nightly aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class Interval:
    start: datetime
    end: datetime


@dataclass
class QuantitySample:
    start: datetime
    value: Decimal


@dataclass
class ParsedWorkout:
    start: datetime
    end: datetime
    activity: str
    duration_minutes: int | None


@dataclass
class ParsedSummary:
    day: date
    exercise_minutes: int | None = None
    active_kcal: int | None = None


@dataclass
class ParsedExport:
    exported_at: datetime | None = None
    sleep_asleep: list[Interval] = field(default_factory=list)
    sleep_in_bed: list[Interval] = field(default_factory=list)
    steps: list[QuantitySample] = field(default_factory=list)
    exercise: list[QuantitySample] = field(default_factory=list)
    active_energy: list[QuantitySample] = field(default_factory=list)
    resting_hr: list[QuantitySample] = field(default_factory=list)
    hrv: list[QuantitySample] = field(default_factory=list)
    body_mass_grams: list[tuple[datetime, int]] = field(default_factory=list)
    workouts: list[ParsedWorkout] = field(default_factory=list)
    summaries: list[ParsedSummary] = field(default_factory=list)


@dataclass
class Night:
    date: date
    asleep_minutes: int


@dataclass
class Day:
    date: date
    steps: int | None = None
    exercise_minutes: int | None = None
    active_kcal: int | None = None
    resting_hr_bpm: int | None = None
    hrv_sdnn_ms: int | None = None


@dataclass
class WorkoutEntry:
    date: date
    activity: str
    duration_minutes: int | None


@dataclass
class MassPoint:
    date: date
    grams: int


@dataclass
class NormalizedExport:
    as_of: date | None
    timezone: str | None
    source_name: str
    synthetic: bool
    nights: list[Night]
    days: list[Day]
    workouts: list[WorkoutEntry]
    masses: list[MassPoint]
    present: dict[str, bool]

    def to_dict(self) -> dict:
        return {
            "schema": "apple-health-insights.v1",
            "synthetic": self.synthetic,
            "source_name": self.source_name,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "timezone": self.timezone,
            "present": self.present,
            "nights": [
                {"date": night.date.isoformat(), "asleep_minutes": night.asleep_minutes}
                for night in self.nights
            ],
            "days": [
                {
                    "date": day.date.isoformat(),
                    "steps": day.steps,
                    "exercise_minutes": day.exercise_minutes,
                    "active_kcal": day.active_kcal,
                    "resting_hr_bpm": day.resting_hr_bpm,
                    "hrv_sdnn_ms": day.hrv_sdnn_ms,
                }
                for day in self.days
            ],
            "workouts": [
                {
                    "date": workout.date.isoformat(),
                    "activity": workout.activity,
                    "duration_minutes": workout.duration_minutes,
                }
                for workout in self.workouts
            ],
            "body_mass": [
                {"date": point.date.isoformat(), "grams": point.grams} for point in self.masses
            ],
        }
