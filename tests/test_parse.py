from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apple_health_insights.normalize import merge_minutes, normalize
from apple_health_insights.parse import (
    ParseError,
    duration_to_minutes,
    energy_to_kcal,
    export_xml_name,
    mass_to_grams,
    parse_health_xml,
    parse_path,
    pretty_activity,
)
from tests.xmlutil import chunked, health_xml, write_zip

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_XML = ROOT / "fixtures" / "synthetic" / "export.xml"
FIXTURE_ZIP = ROOT / "fixtures" / "synthetic" / "export.zip"
TZ = timezone(timedelta(hours=-7))


def _at(day: str, hour: int, minute: int) -> datetime:
    year, month, dom = (int(part) for part in day.split("-"))
    return datetime(year, month, dom, hour, minute, tzinfo=TZ)


def test_merge_minutes_collapses_overlap():
    start = _at("2026-09-21", 23, 0)
    assert merge_minutes([(start, _at("2026-09-22", 6, 30)), (_at("2026-09-21", 23, 30), _at("2026-09-22", 6, 0))]) == 450
    assert merge_minutes([(_at("2026-09-01", 0, 0), _at("2026-09-01", 0, 30)), (_at("2026-09-01", 0, 30), _at("2026-09-01", 1, 0))]) == 60


def test_unit_conversions_and_activity_names():
    assert pretty_activity("HKWorkoutActivityTypeRunning") == "Running"
    assert pretty_activity("HKWorkoutActivityTypeTraditionalStrengthTraining") == "Traditional Strength Training"
    assert duration_to_minutes("1.5", "h") == 90
    assert energy_to_kcal("418.4", "kJ") == 100
    assert mass_to_grams("180", "lb") == 81647
    assert mass_to_grams("81.5", "kg") == 81500


def test_export_xml_name_prefers_the_apple_folder():
    chosen = export_xml_name(
        [
            "other/export.xml",
            "apple_health_export/export.xml",
            "__MACOSX/._export.xml",
            "../export.xml",
        ]
    )
    assert chosen == "apple_health_export/export.xml"
    assert export_xml_name(["readme.txt"]) is None


def test_doctype_and_small_reads_still_parse():
    xml = health_xml(
        """
        <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Watch" unit="count"
         startDate="2026-10-01 00:00:00 -0700" endDate="2026-10-01 23:00:00 -0700" value="400"/>
        <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Watch" unit="count"
         startDate="2026-10-01 12:00:00 -0700" endDate="2026-10-01 18:00:00 -0700" value="600"/>
        """
    )
    parsed = parse_health_xml(chunked(xml))
    export = normalize(parsed, source_name="export.xml", synthetic=True)
    assert export.days[0].steps == 1000
    assert export.as_of.isoformat() == "2026-10-01"


def test_awake_only_does_not_invent_sleep():
    xml = health_xml(
        """
        <Record type="HKCategoryTypeIdentifierSleepAnalysis" unit="min" value="HKCategoryValueSleepAnalysisAwake"
         startDate="2026-10-01 02:00:00 -0700" endDate="2026-10-01 02:20:00 -0700"/>
        """
    )
    export = normalize(parse_health_xml(io.BytesIO(xml.encode())), source_name="export.xml", synthetic=True)
    assert export.present["sleep"] is False
    assert export.nights == []


def test_in_bed_used_only_when_no_asleep_stage():
    xml = health_xml(
        """
        <Record type="HKCategoryTypeIdentifierSleepAnalysis" value="HKCategoryValueSleepAnalysisInBed"
         startDate="2026-09-30 22:00:00 -0700" endDate="2026-10-01 06:00:00 -0700"/>
        """
    )
    export = normalize(parse_health_xml(io.BytesIO(xml.encode())), source_name="export.xml", synthetic=True)
    assert export.nights[0].asleep_minutes == 480


def test_zip_without_export_is_a_clean_error(tmp_path: Path):
    archive = tmp_path / "empty.zip"
    with archive.open("wb") as handle:
        handle.write(b"not a zip")
    try:
        parse_path(archive)
    except ParseError as exc:
        assert "readable zip" in str(exc)
    else:
        raise AssertionError("expected ParseError")

    real = tmp_path / "no-xml.zip"
    write_zip(real, "hello", member="notes.txt")
    try:
        parse_path(real)
    except ParseError as exc:
        assert "export.xml" in str(exc)
    else:
        raise AssertionError("expected ParseError")


def test_synthetic_fixture_rollup():
    assert "SYNTHETIC" in FIXTURE_XML.read_text(encoding="utf-8")
    from_xml = normalize(parse_path(FIXTURE_XML), source_name="export.xml", synthetic=True)
    from_zip = normalize(parse_path(FIXTURE_ZIP), source_name="export.zip", synthetic=True)
    assert [(night.date, night.asleep_minutes) for night in from_xml.nights] == [
        (night.date, night.asleep_minutes) for night in from_zip.nights
    ]

    nights = {night.date.isoformat(): night.asleep_minutes for night in from_xml.nights}
    assert nights["2026-10-01"] == 330
    assert nights["2026-09-20"] == 450
    assert nights["2026-09-21"] == 450
    assert nights["2026-09-15"] == 450

    days = {day.date.isoformat(): day for day in from_xml.days}
    assert days["2026-10-01"].steps == 9100
    assert days["2026-09-25"].steps == 8000
    assert days["2026-09-30"].exercise_minutes == 30
    assert days["2026-10-01"].exercise_minutes == 36
    assert days["2026-10-01"].active_kcal == 450
    assert days["2026-09-30"].active_kcal == 450
    assert days["2026-10-01"].resting_hr_bpm == 62
    assert days["2026-09-24"].resting_hr_bpm == 58
    assert days["2026-10-01"].hrv_sdnn_ms == 45
    assert [point.grams for point in from_xml.masses] == [82000, 81500]
    assert [workout.activity for workout in from_xml.workouts] == [
        "Cycling",
        "Running",
        "Cycling",
        "Running",
        "Running",
    ]

    raw_store = str(from_xml.to_dict())
    assert "177" not in raw_store
    assert "999" not in raw_store
    assert "Synthetic Watch" not in raw_store
    assert "HKQuantity" not in raw_store
