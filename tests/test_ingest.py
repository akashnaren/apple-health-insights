from __future__ import annotations

import json
import os
from pathlib import Path

from apple_health_insights.cli import main
from apple_health_insights.ingest import ingest
from apple_health_insights.overview import NO_SERIES
from tests.xmlutil import health_xml, write_zip

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_XML = ROOT / "fixtures" / "synthetic" / "export.xml"
FIXTURE_ZIP = ROOT / "fixtures" / "synthetic" / "export.zip"

BANNED = ("177", "999", "99", "Synthetic Watch", "HKQuantity", "HKCategory", "<Record", "sourceName")


def test_fixture_overview_is_aggregates_and_sleep_debt(tmp_path: Path):
    result = ingest(FIXTURE_ZIP, tmp_path)
    assert result.status == "ingested"
    assert result.exit_code == 0
    text = result.overview_path.read_text(encoding="utf-8")
    store = (tmp_path / "normalized-store.json").read_text(encoding="utf-8")
    for banned in BANNED:
        assert banned not in text
        assert banned not in store
    assert "Sleep debt vs 14-day baseline" in text
    assert "2h shorter than that baseline" in text
    assert "Last night: 5h 30m asleep (ended 2026-10-01)." in text
    assert "7-day average: 6h 39m (7 nights)." in text
    assert "Consistency across those nights: variable." in text
    assert "Steps today: 9,100." in text
    assert "Steps, 7-day average: 8,200 (7 days)." in text
    assert "Exercise today: 36 min." in text
    assert "Exercise, 7-day average: 31 min (7 days)." in text
    assert "Active energy today: 450 kcal." in text
    assert "Active energy, 7-day average: 450 kcal (7 days)." in text
    assert "about 4 bpm higher" in text
    assert "45 ms" in text
    assert "about the same" in text
    assert "Last 7 days: 3 workouts. Running 2, Cycling 1." in text
    assert "Last 30 days: 5 workouts. Running 3, Cycling 2." in text
    assert "Latest: 81.5 kg on 2026-10-01." in text
    assert "-0.5 kg" in text
    assert "82.0 kg on 2026-09-05" in text
    assert "synthetic fixture" in text
    assert "not medical advice" in text.lower() or "Not medical advice" in text

    again = ingest(FIXTURE_ZIP, tmp_path)
    assert again.status == "already_ingested"
    forced = ingest(FIXTURE_XML, tmp_path, force=True)
    assert forced.status == "ingested"
    assert "Sleep debt vs 14-day baseline" in forced.overview_path.read_text(encoding="utf-8")


def test_missing_series_says_no_data(tmp_path: Path):
    xml = health_xml(
        """
        <Record type="HKQuantityTypeIdentifierStepCount" unit="count" sourceName="Synthetic Watch"
         startDate="2026-10-01 00:00:00 -0700" endDate="2026-10-01 20:00:00 -0700" value="1000"/>
        <Record type="HKQuantityTypeIdentifierHeartRate" unit="count/min" value="177"
         startDate="2026-10-01 15:04:05 -0700" endDate="2026-10-01 15:04:06 -0700"/>
        """
    )
    path = tmp_path / "inbox" / "export.xml"
    path.parent.mkdir()
    path.write_text(xml, encoding="utf-8")
    out = tmp_path / "out"
    result = ingest(path, out)
    text = result.overview_path.read_text(encoding="utf-8")
    assert "Steps today: 1,000." in text
    assert f"Heart rate variability (SDNN): {NO_SERIES}" in text or NO_SERIES in text
    assert "Body mass: no data in this export" in text or "## Body" in text
    assert NO_SERIES in text.split("## Cardio", 1)[1].split("##", 1)[0]
    assert NO_SERIES in text.split("## Body", 1)[1].split("##", 1)[0]
    assert NO_SERIES in text.split("## Recovery / sleep", 1)[1].split("##", 1)[0]
    assert "0 bpm" not in text
    assert "0 kg" not in text
    assert "0m" not in text
    assert "177" not in text
    store = json.loads((out / "normalized-store.json").read_text(encoding="utf-8"))
    assert store["present"]["hrv"] is False
    assert store["present"]["body_mass"] is False
    assert store["present"]["sleep"] is False
    assert "177" not in json.dumps(store)


def test_empty_folder_is_clean_and_does_not_clobber(tmp_path: Path):
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "readme.txt").write_text("no zip here\n", encoding="utf-8")
    out = tmp_path / "out"
    result = ingest(folder, out)
    assert result.status == "no_export"
    assert result.exit_code == 0
    text = result.overview_path.read_text(encoding="utf-8")
    assert "no export" in text
    assert "Nothing was filled in" in text
    assert text.count(NO_SERIES) >= 5
    assert "0" not in text.split("## Recovery", 1)[1]
    status = json.loads((out / "inbox-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "no_export"
    assert not (out / "normalized-store.json").exists()

    result.overview_path.write_text("KEEP THIS\n", encoding="utf-8")
    again = ingest(folder, out)
    assert again.status == "no_export"
    assert result.overview_path.read_text(encoding="utf-8") == "KEEP THIS\n"


def test_processed_zip_is_ignored_and_newest_inbox_zip_wins(tmp_path: Path):
    drop = tmp_path / "drop"
    (drop / "processed").mkdir(parents=True)
    (drop / "inbox").mkdir()
    poison = health_xml(
        """
        <Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="123456"
         startDate="2026-10-01 00:00:00 -0700" endDate="2026-10-01 12:00:00 -0700"/>
        """
    )
    write_zip(drop / "processed" / "old.zip", poison)
    write_zip(drop / "inbox" / "older.zip", poison)
    write_zip(drop / "inbox" / "newer.zip", FIXTURE_XML.read_text(encoding="utf-8"))
    os.utime(drop / "processed" / "old.zip", (2_000_000_000, 2_000_000_000))
    os.utime(drop / "inbox" / "older.zip", (1_000_000_000, 1_000_000_000))
    os.utime(drop / "inbox" / "newer.zip", (1_500_000_000, 1_500_000_000))
    result = ingest(drop, tmp_path / "out")
    text = result.overview_path.read_text(encoding="utf-8")
    assert "123456" not in text
    assert "Sleep debt vs 14-day baseline" in text
    assert "Input:** newer.zip" in text or "**Input:** newer.zip" in text


def test_cli_empty_and_bad_zip(tmp_path: Path, capsys):
    folder = tmp_path / "empty"
    folder.mkdir()
    assert main(["ingest", str(folder), "--out", str(tmp_path / "out"), "--quiet"]) == 0
    captured = capsys.readouterr()
    assert "no_export" in captured.out

    bad = tmp_path / "notes.zip"
    bad.write_bytes(b"nope")
    assert main(["ingest", str(bad), "--out", str(tmp_path / "bad")]) == 2
    err = capsys.readouterr().err
    assert "error:" in err

    assert main(["ingest", str(FIXTURE_ZIP), "--out", str(tmp_path / "ok"), "--quiet"]) == 0
