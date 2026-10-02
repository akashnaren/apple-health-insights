from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}


def _files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP for part in path.parts):
            continue
        yield path


def test_repo_has_no_raw_health_outside_the_synthetic_fixture():
    xml_files = []
    zip_files = []
    for path in _files():
        assert path.suffix.lower() != ".csv"
        assert path.suffix.lower() not in {".pem", ".key"}
        if path.name == "export.xml":
            xml_files.append(path)
        if path.suffix.lower() == ".zip":
            zip_files.append(path)
        if path.name == "Health overview.md":
            raise AssertionError(f"living overview is tracked: {path}")
    assert xml_files == [ROOT / "fixtures" / "synthetic" / "export.xml"]
    assert zip_files == [ROOT / "fixtures" / "synthetic" / "export.zip"]


def test_synthetic_fixture_is_labeled_and_small():
    xml = ROOT / "fixtures" / "synthetic" / "export.xml"
    text = xml.read_text(encoding="utf-8")
    assert "SYNTHETIC" in text
    assert "@" not in text
    assert "HKCharacteristicTypeIdentifierDateOfBirth" not in text
    archive = ROOT / "fixtures" / "synthetic" / "export.zip"
    with zipfile.ZipFile(archive) as zf:
        names = [name for name in zf.namelist() if not name.endswith("/")]
        assert names == ["apple_health_export/export.xml"]
        payload = zf.read("apple_health_export/export.xml").decode("utf-8")
    assert payload == text
    assert archive.stat().st_size < 200_000
