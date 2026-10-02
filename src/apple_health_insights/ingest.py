"""Find an export, normalize it, and write the private overview.

A folder with no zip and no export.xml is a clean empty result. The same file
hash is not written twice unless ``force`` is set. This module does not call
Google Drive.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from apple_health_insights.insight import build_insight
from apple_health_insights.normalize import normalize
from apple_health_insights.overview import render_empty_overview, render_overview
from apple_health_insights.parse import ParseError, export_xml_name, parse_path

OVERVIEW_NAME = "Health overview.md"
STORE_NAME = "normalized-store.json"
MANIFEST_NAME = "ingest-manifest.json"
STATUS_NAME = "inbox-status.json"
SKIP_PARTS = {"processed", "__MACOSX"}


@dataclass(frozen=True)
class IngestResult:
    status: str
    message: str
    overview_path: Path | None
    exit_code: int


def ingest(path: Path, out_dir: Path, *, force: bool = False) -> IngestResult:
    if not path.exists():
        raise ParseError(f"Path not found: {path}")
    source = locate_export(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    overview_path = out_dir / OVERVIEW_NAME
    store_path = out_dir / STORE_NAME
    status_path = out_dir / STATUS_NAME
    if source is None:
        _write_json(status_path, {"status": "no_export", "source_name": None, "insight": None})
        if overview_path.exists():
            message = "No Apple Health zip or export.xml found. Left the existing overview in place."
        else:
            overview_path.write_text(render_empty_overview(), encoding="utf-8")
            message = "No Apple Health zip or export.xml found."
        return IngestResult("no_export", message, overview_path, 0)

    digest = sha256_file(source)
    manifest_path = out_dir / MANIFEST_NAME
    if (
        not force
        and _already_ingested(manifest_path, digest)
        and overview_path.exists()
        and store_path.exists()
    ):
        _write_json(
            status_path,
            {
                "status": "already_ingested",
                "source_name": source.name,
                "sha256": digest,
                "insight": _existing_insight(status_path),
            },
        )
        return IngestResult(
            "already_ingested",
            f"Already ingested {source.name} (same content hash).",
            overview_path,
            0,
        )

    parsed = parse_path(source)
    export = normalize(parsed, source_name=source.name, synthetic=looks_synthetic(source))
    insight = build_insight(export)
    overview_path.write_text(render_overview(export, insight), encoding="utf-8")
    store_path.write_text(
        json.dumps(export.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _remember(manifest_path, source.name, digest)
    _write_json(
        status_path,
        {
            "status": "ingested",
            "source_name": source.name,
            "sha256": digest,
            "synthetic": export.synthetic,
            "insight": insight.kind,
        },
    )
    return IngestResult(
        "ingested",
        f"Ingested {source.name}. Insight: {insight.title}.",
        overview_path,
        0,
    )


def locate_export(path: Path) -> Path | None:
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".zip" or suffix == ".xml" or path.name.lower() == "export.xml":
            return path
        raise ParseError("Expected a .zip or an export.xml file.")
    if not path.is_dir():
        raise ParseError(f"Path not found: {path}")
    zips = _collect(path, {".zip"})
    if zips:
        return zips[-1]
    xmls = [item for item in _collect(path, {".xml"}) if item.name.lower() == "export.xml"]
    if xmls:
        return xmls[-1]
    return None


def looks_synthetic(path: Path) -> bool:
    if "synthetic" in {part.lower() for part in path.parts}:
        return True
    try:
        head = _head_bytes(path)
    except (OSError, ParseError):
        return False
    return b"SYNTHETIC" in head.upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(root: Path, suffixes: set[str]) -> list[Path]:
    found: list[Path] = []
    for item in root.rglob("*"):
        if not item.is_file() or item.suffix.lower() not in suffixes:
            continue
        relative = item.relative_to(root)
        if any(part in SKIP_PARTS or part.startswith(".") for part in relative.parts):
            continue
        found.append(item)
    found.sort(key=lambda item: (item.stat().st_mtime, str(item)))
    return found


def _head_bytes(path: Path) -> bytes:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            name = export_xml_name(archive.namelist())
            if name is None:
                return b""
            with archive.open(name) as handle:
                return handle.read(8192)
    with path.open("rb") as handle:
        return handle.read(8192)


def _already_ingested(manifest_path: Path, digest: str) -> bool:
    payload = _read_json(manifest_path)
    return any(entry.get("sha256") == digest for entry in payload.get("entries", []))


def _remember(manifest_path: Path, name: str, digest: str) -> None:
    payload = _read_json(manifest_path)
    entries = [entry for entry in payload.get("entries", []) if entry.get("sha256") != digest]
    entries.append({"name": name, "sha256": digest})
    _write_json(manifest_path, {"entries": entries})


def _existing_insight(status_path: Path) -> str | None:
    payload = _read_json(status_path)
    insight = payload.get("insight")
    return insight if isinstance(insight, str) else None


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
