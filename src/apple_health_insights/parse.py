"""Stream an Apple Health export.xml into the v1 sample lists.

The Health DOCTYPE is skipped. Record types outside the v1 set are ignored,
so a high-frequency series such as heart rate never lands in the store.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

from apple_health_insights.models import (
    Interval,
    ParsedExport,
    ParsedSummary,
    ParsedWorkout,
    QuantitySample,
)

SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
STEPS = "HKQuantityTypeIdentifierStepCount"
EXERCISE = "HKQuantityTypeIdentifierAppleExerciseTime"
ENERGY = "HKQuantityTypeIdentifierActiveEnergyBurned"
RESTING_HR = "HKQuantityTypeIdentifierRestingHeartRate"
HRV = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
BODY_MASS = "HKQuantityTypeIdentifierBodyMass"

ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleep",
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
}
IN_BED_VALUE = "HKCategoryValueSleepAnalysisInBed"

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M %z")
_CAMEL = re.compile(r"([a-z])([A-Z])")


class ParseError(Exception):
    """The file was not a readable Apple Health export."""


def parse_path(path: Path) -> ParsedExport:
    if path.suffix.lower() == ".zip":
        return parse_zip(path)
    with path.open("rb") as handle:
        return parse_health_xml(handle)


def parse_zip(path: Path) -> ParsedExport:
    try:
        with zipfile.ZipFile(path) as archive:
            name = export_xml_name(archive.namelist())
            if name is None:
                raise ParseError("Zip has no export.xml.")
            with archive.open(name) as handle:
                return parse_health_xml(handle)
    except zipfile.BadZipFile as exc:
        raise ParseError("File is not a readable zip.") from exc


def export_xml_name(names: list[str]) -> str | None:
    candidates = []
    for name in names:
        parts = [part for part in name.split("/") if part and part != ".."]
        if not parts or parts[0] == "__MACOSX" or any(part.startswith(".") for part in parts):
            continue
        if parts[-1].lower() == "export.xml":
            candidates.append(name)
    if not candidates:
        return None
    preferred = [name for name in candidates if name.rstrip("/").endswith("apple_health_export/export.xml")]
    pool = preferred or candidates
    return sorted(pool, key=lambda name: (name.count("/"), len(name), name))[0]


def parse_health_xml(handle: io.BufferedIOBase) -> ParsedExport:
    parser = ET.XMLPullParser(events=("end",))
    parsed = ParsedExport()
    started = False
    pending = b""
    try:
        while True:
            chunk = handle.read(256 * 1024)
            if not chunk:
                break
            if not started:
                pending += chunk
                marker = pending.find(b"<HealthData")
                if marker == -1:
                    if len(pending) > 2_000_000:
                        raise ParseError("Not an Apple Health export (no HealthData element).")
                    continue
                started = True
                parser.feed(pending[marker:])
                pending = b""
            else:
                parser.feed(chunk)
            _drain(parser, parsed)
        if not started:
            raise ParseError("Not an Apple Health export (no HealthData element).")
        parser.close()
        _drain(parser, parsed)
    except ET.ParseError as exc:
        raise ParseError(f"Could not parse export.xml ({exc}).") from exc
    return parsed


def _drain(parser: ET.XMLPullParser, parsed: ParsedExport) -> None:
    for _, elem in parser.read_events():
        tag = _local(elem.tag)
        if tag == "ExportDate":
            parsed.exported_at = _parse_datetime(elem.attrib.get("value", ""))
        elif tag == "Record":
            _add_record(parsed, elem.attrib)
        elif tag == "Workout":
            _add_workout(parsed, elem.attrib)
        elif tag == "ActivitySummary":
            _add_summary(parsed, elem.attrib)
        elem.clear()


def _add_record(parsed: ParsedExport, attrib: dict[str, str]) -> None:
    kind = attrib.get("type", "")
    if kind == SLEEP:
        _add_sleep(parsed, attrib)
        return
    if kind == BODY_MASS:
        _add_mass(parsed, attrib)
        return
    bucket = {
        STEPS: parsed.steps,
        EXERCISE: parsed.exercise,
        ENERGY: parsed.active_energy,
        RESTING_HR: parsed.resting_hr,
        HRV: parsed.hrv,
    }.get(kind)
    if bucket is None:
        return
    sample = _quantity(attrib, kind)
    if sample is not None:
        bucket.append(sample)


def _add_sleep(parsed: ParsedExport, attrib: dict[str, str]) -> None:
    start = _parse_datetime(attrib.get("startDate", ""))
    end = _parse_datetime(attrib.get("endDate", ""))
    if start is None or end is None or end <= start:
        return
    interval = Interval(start, end)
    value = attrib.get("value", "")
    if value in ASLEEP_VALUES:
        parsed.sleep_asleep.append(interval)
    elif value == IN_BED_VALUE:
        parsed.sleep_in_bed.append(interval)


def _add_mass(parsed: ParsedExport, attrib: dict[str, str]) -> None:
    when = _parse_datetime(attrib.get("startDate", "")) or _parse_datetime(attrib.get("endDate", ""))
    grams = mass_to_grams(attrib.get("value", ""), attrib.get("unit", ""))
    if when is None or grams is None:
        return
    parsed.body_mass_grams.append((when, grams))


def _add_workout(parsed: ParsedExport, attrib: dict[str, str]) -> None:
    start = _parse_datetime(attrib.get("startDate", ""))
    end = _parse_datetime(attrib.get("endDate", ""))
    if start is None:
        return
    if end is None or end < start:
        end = start
    minutes = duration_to_minutes(attrib.get("duration", ""), attrib.get("durationUnit", ""))
    if minutes is None and end > start:
        seconds = Decimal(int((end - start).total_seconds()))
        minutes = int((seconds / Decimal(60)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    parsed.workouts.append(
        ParsedWorkout(
            start=start,
            end=end,
            activity=pretty_activity(attrib.get("workoutActivityType", "")),
            duration_minutes=minutes,
        )
    )


def _add_summary(parsed: ParsedExport, attrib: dict[str, str]) -> None:
    raw_day = attrib.get("dateComponents", "")
    try:
        day = datetime.strptime(raw_day, "%Y-%m-%d").date()
    except ValueError:
        return
    exercise = duration_to_minutes(attrib.get("appleExerciseTime", ""), "min")
    energy = energy_to_kcal(attrib.get("activeEnergyBurned", ""), attrib.get("activeEnergyBurnedUnit", "kcal"))
    if exercise is None and energy is None:
        return
    parsed.summaries.append(ParsedSummary(day=day, exercise_minutes=exercise, active_kcal=energy))


def _quantity(attrib: dict[str, str], kind: str) -> QuantitySample | None:
    start = _parse_datetime(attrib.get("startDate", "")) or _parse_datetime(attrib.get("endDate", ""))
    number = _decimal(attrib.get("value", ""))
    if start is None or number is None:
        return None
    unit = attrib.get("unit", "")
    if kind == EXERCISE:
        minutes = duration_to_minutes(attrib.get("value", ""), unit or "min")
        if minutes is None:
            return None
        number = Decimal(minutes)
    elif kind == ENERGY:
        kcal = energy_to_kcal(attrib.get("value", ""), unit)
        if kcal is None:
            return None
        number = Decimal(kcal)
    elif kind == RESTING_HR and unit not in {"count/min", "bpm"}:
        return None
    elif kind == HRV and unit != "ms":
        return None
    elif kind == STEPS and unit not in {"count", ""}:
        return None
    return QuantitySample(start=start, value=number)


def pretty_activity(raw: str) -> str:
    name = raw.removeprefix("HKWorkoutActivityType").strip()
    if not name:
        return "Workout"
    spaced = _CAMEL.sub(r"\1 \2", name)
    return " ".join(spaced.split())


def mass_to_grams(value: str, unit: str) -> int | None:
    number = _decimal(value)
    if number is None:
        return None
    key = unit.lower()
    if key == "kg":
        grams = number * Decimal(1000)
    elif key in {"lb", "lbs"}:
        grams = number * Decimal("453.59237")
    else:
        return None
    return int(grams.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def duration_to_minutes(value: str, unit: str) -> int | None:
    number = _decimal(value)
    if number is None:
        return None
    key = unit.lower()
    if key in {"min", "mins", "minute", "minutes"}:
        minutes = number
    elif key in {"h", "hr", "hrs", "hour", "hours"}:
        minutes = number * Decimal(60)
    elif key in {"s", "sec", "secs", "second", "seconds"}:
        minutes = number / Decimal(60)
    else:
        return None
    return int(minutes.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def energy_to_kcal(value: str, unit: str) -> int | None:
    number = _decimal(value)
    if number is None:
        return None
    if unit in {"kcal", "Cal"}:
        kcal = number
    elif unit == "kJ":
        kcal = number / Decimal("4.184")
    else:
        return None
    return int(kcal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[-1]
    return tag
