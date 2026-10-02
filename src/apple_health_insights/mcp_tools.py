"""Four aggregates-only tools. Ingest is the package CLI, not a second parser.

Responses are status, counts, and the private overview. Export XML, zip bytes,
record attributes, GPS, and notes are never copied into a result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from apple_health_insights.ingest import MANIFEST_NAME, OVERVIEW_NAME, STATUS_NAME, STORE_NAME
from apple_health_insights.mcp_drive import FOLDER_MIME, DriveClient, DriveError, DriveItem, HttpDriveClient

HEALTH_DATA_FOLDER_ID = "1kmz1nYyZS7je75Ej_QH9r8m0880W62xo"
DRIVE_LAYOUT = "exports/YYYY/MM/apple_health_export/"
PROCESSED_NAME = "mcp-drive-processed.json"
MAX_INSIGHTS = 3
_SKIP = {"processed", "__MACOSX"}
_YEAR = re.compile(r"^\d{4}$")
_MONTH = re.compile(r"^\d{2}$")
_LAYOUT = re.compile(r"exports/(\d{4})/(\d{2})")
_MARKERS = (
    "<Record",
    "</Record>",
    "<HealthData",
    "<?xml",
    "<!DOCTYPE",
    "HKQuantityType",
    "HKCategoryType",
    "HKWorkout",
    "sourceName=",
    "sourceVersion=",
    "creationDate=",
    "startDate=",
    "endDate=",
)


@dataclass
class Settings:
    drive_token: str | None
    folder_id: str
    state_dir: Path
    inbox_path: Path | None


@dataclass
class ToolContext:
    settings: Settings
    drive: DriveClient | None = None


def load_settings() -> Settings:
    token = os.environ.get("HEALTH_DRIVE_ACCESS_TOKEN") or os.environ.get("GOOGLE_ACCESS_TOKEN")
    token = token.strip() if isinstance(token, str) and token.strip() else None
    folder = os.environ.get("HEALTH_DRIVE_FOLDER_ID") or HEALTH_DATA_FOLDER_ID
    state = Path(os.environ.get("HEALTH_STATE_DIR") or "state/health")
    inbox_raw = os.environ.get("HEALTH_INBOX_PATH")
    inbox = Path(inbox_raw) if isinstance(inbox_raw, str) and inbox_raw.strip() else None
    return Settings(drive_token=token, folder_id=folder.strip() or HEALTH_DATA_FOLDER_ID, state_dir=state, inbox_path=inbox)


def default_context() -> ToolContext:
    settings = load_settings()
    drive = HttpDriveClient(settings.drive_token) if settings.drive_token else None
    return ToolContext(settings=settings, drive=drive)


def health_inbox_status(arguments: dict | None = None, context: ToolContext | None = None) -> dict:
    args = arguments or {}
    ctx = context or default_context()
    out = _out_dir(args, ctx)
    inbox = _optional_path(args.get("inbox_path")) or ctx.settings.inbox_path
    base = {
        "folder_id": ctx.settings.folder_id,
        "layout": DRIVE_LAYOUT,
        "drive_configured": ctx.drive is not None,
        "last_processed_id": None,
    }
    if ctx.drive is not None:
        try:
            items = list_drive_zips(ctx.drive, ctx.settings.folder_id)
        except DriveError:
            return guard({**base, "source": "drive", "items": [], "error": "Drive list failed."})
        processed = _processed_ids(out)
        return guard(
            {
                **base,
                "source": "drive",
                "items": items,
                "last_processed_id": processed[-1] if processed else None,
            }
        )
    if inbox is not None:
        items = list_local_zips(inbox)
        manifest_ids = _manifest_ids(out)
        return guard(
            {
                **base,
                "source": "local",
                "items": items,
                "last_processed_id": manifest_ids[-1] if manifest_ids else None,
            }
        )
    manifest_ids = _manifest_ids(out)
    return guard(
        {
            **base,
            "source": "empty",
            "items": [],
            "last_processed_id": manifest_ids[-1] if manifest_ids else None,
        }
    )


def health_ingest_latest(arguments: dict | None = None, context: ToolContext | None = None) -> dict:
    args = arguments or {}
    ctx = context or default_context()
    out = _out_dir(args, ctx)
    force = args.get("force") is True
    explicit = _optional_path(args.get("path"))
    if explicit is not None:
        return _ingest_path(explicit, out, force=force)
    if ctx.drive is not None:
        return _ingest_drive(ctx, out, force=force)
    inbox = _optional_path(args.get("inbox_path")) or ctx.settings.inbox_path
    if inbox is not None:
        return _ingest_local_inbox(inbox, out, force=force)
    return guard(
        {
            "status": "no_export",
            "exit_code": 0,
            "message": "No Drive credentials and no local export path.",
            "overview_path": None,
            "counts": None,
            "present": None,
            "as_of": None,
            "synthetic": None,
            "insight": None,
        }
    )


def health_overview(arguments: dict | None = None, context: ToolContext | None = None) -> dict:
    args = arguments or {}
    ctx = context or default_context()
    out = _out_dir(args, ctx)
    return guard(_read_overview(out))


def health_insights(arguments: dict | None = None, context: ToolContext | None = None) -> dict:
    args = arguments or {}
    ctx = context or default_context()
    out = _out_dir(args, ctx)
    overview = _read_overview(out)
    if not overview["found"]:
        return guard({"insights": [], "limit": MAX_INSIGHTS})
    title, body = _insight_block(overview["sections"].get("insight", ""))
    kind = _status_insight(out)
    thesis = overview["thesis"]
    if not title and not body and not thesis:
        return guard({"insights": [], "limit": MAX_INSIGHTS})
    insight = {
        "rank": 1,
        "kind": kind,
        "title": title or "Insight",
        "thesis": thesis,
        "body": body,
    }
    return guard({"insights": [insight][:MAX_INSIGHTS], "limit": MAX_INSIGHTS})


def list_drive_zips(drive: DriveClient, folder_id: str) -> list[dict]:
    found: list[dict] = []

    def walk(current: str, prefix: str) -> None:
        for item in drive.list_children(current):
            if item.mime_type == FOLDER_MIME:
                child = _child_prefix(prefix, item.name)
                if child is None:
                    continue
                walk(item.id, child)
                continue
            if not _is_zip(item):
                continue
            found.append(
                {
                    "id": item.id,
                    "modifiedTime": item.modified_time,
                    "size": item.size,
                    "label": sanitized_label(prefix),
                }
            )

    walk(folder_id, "")
    found.sort(key=lambda row: (row["modifiedTime"], row["id"]))
    return found


def list_local_zips(root: Path) -> list[dict]:
    if not root.exists() or not root.is_dir():
        return []
    found: list[dict] = []
    for item in root.rglob("*"):
        if not item.is_file() or item.suffix.lower() != ".zip":
            continue
        relative = item.relative_to(root)
        if any(part in _SKIP or part.startswith(".") for part in relative.parts):
            continue
        stat = item.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        found.append(
            {
                "id": _sha256(item),
                "modifiedTime": modified,
                "size": stat.st_size,
                "label": sanitized_label("/".join(relative.parts[:-1])),
            }
        )
    found.sort(key=lambda row: (row["modifiedTime"], row["id"]))
    return found


def sanitized_label(path_hint: str) -> str:
    match = _LAYOUT.search(path_hint.replace("\\", "/"))
    if match:
        return f"export-{match.group(1)}-{match.group(2)}"
    return "export-zip"


def guard(payload: dict) -> dict:
    rendered = json.dumps(payload, ensure_ascii=True)
    if any(marker in rendered for marker in _MARKERS):
        return {
            "refused": True,
            "reason": "Response contained export markup and was dropped.",
        }
    return payload


def _ingest_path(path: Path, out: Path, *, force: bool) -> dict:
    if not path.exists():
        return guard(
            {
                "status": "error",
                "exit_code": 2,
                "message": "Ingest failed.",
                "overview_path": None,
                "counts": None,
                "present": None,
                "as_of": None,
                "synthetic": None,
                "insight": None,
            }
        )
    code, status = _run_cli(path, out, force=force)
    if code != 0 or status is None:
        return guard(_failed(code or 2))
    return guard(_summarize(out, status, code))


def _ingest_drive(ctx: ToolContext, out: Path, *, force: bool) -> dict:
    assert ctx.drive is not None
    try:
        items = list_drive_zips(ctx.drive, ctx.settings.folder_id)
    except DriveError:
        return guard(_failed(2, "Drive list failed."))
    processed = set(_processed_ids(out))
    chosen = _newest_unprocessed(items, processed)
    if chosen is None:
        summary = _summarize(out, "none_pending", 0)
        summary["message"] = "No unprocessed export zip."
        return guard(summary)
    dest = out / "inbox" / f"{_safe_id(chosen['id'])}.zip"
    try:
        ctx.drive.download_to(chosen["id"], dest)
    except DriveError:
        return guard(_failed(2, "Drive download failed."))
    code, status = _run_cli(dest, out, force=force)
    if code != 0 or status is None:
        return guard(_failed(code or 2))
    try:
        ctx.drive.delete(chosen["id"])
    except DriveError:
        dest.unlink(missing_ok=True)
        return guard(_failed(2, "Ingest succeeded but Drive delete failed. The zip was left in Drive."))
    dest.unlink(missing_ok=True)
    _remember_processed(out, chosen["id"])
    return guard(_summarize(out, status, code))


def _ingest_local_inbox(inbox: Path, out: Path, *, force: bool) -> dict:
    if not inbox.exists() or not inbox.is_dir():
        return guard(_failed(2))
    items = list_local_zips(inbox)
    if not items:
        return _ingest_path(inbox, out, force=force)
    known = set(_manifest_ids(out))
    pending = [item for item in items if item["id"] not in known]
    if not pending:
        summary = _summarize(out, "none_pending", 0)
        summary["message"] = "No unprocessed export zip."
        return guard(summary)
    chosen = pending[-1]
    match = _zip_with_hash(inbox, chosen["id"])
    if match is None:
        return guard(_failed(2))
    return _ingest_path(match, out, force=force)


def _run_cli(path: Path, out: Path, *, force: bool) -> tuple[int, str | None]:
    out.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "apple_health_insights",
        "ingest",
        str(path),
        "--out",
        str(out),
        "--quiet",
    ]
    if force:
        command.append("--force")
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    status = None
    for line in completed.stdout.splitlines():
        if line.startswith("status: "):
            status = line.split(": ", 1)[1].strip()
            break
    if any(marker in (completed.stdout + completed.stderr) for marker in _MARKERS):
        return completed.returncode, None
    return completed.returncode, status


def _summarize(out: Path, status: str, exit_code: int) -> dict:
    overview = out / OVERVIEW_NAME
    store = _read_json(out / STORE_NAME)
    meta = _read_json(out / STATUS_NAME)
    counts = None
    present = None
    as_of = None
    synthetic = None
    if store:
        counts = {
            "nights": _len(store.get("nights")),
            "days": _len(store.get("days")),
            "workouts": _len(store.get("workouts")),
            "body_mass_points": _len(store.get("body_mass")),
        }
        if isinstance(store.get("present"), dict):
            present = {key: bool(value) for key, value in store["present"].items()}
        if isinstance(store.get("as_of"), str):
            as_of = store["as_of"]
        if isinstance(store.get("synthetic"), bool):
            synthetic = store["synthetic"]
    insight = meta.get("insight") if isinstance(meta.get("insight"), str) else None
    return {
        "status": status,
        "exit_code": exit_code,
        "message": _message(status, out),
        "overview_path": str(overview) if overview.exists() else None,
        "counts": counts,
        "present": present,
        "as_of": as_of,
        "synthetic": synthetic,
        "insight": insight,
    }


def _read_overview(out: Path) -> dict:
    path = out / OVERVIEW_NAME
    store = _read_json(out / STORE_NAME)
    counts = None
    present = None
    as_of = None
    synthetic = None
    if store:
        counts = {
            "nights": _len(store.get("nights")),
            "days": _len(store.get("days")),
            "workouts": _len(store.get("workouts")),
            "body_mass_points": _len(store.get("body_mass")),
        }
        if isinstance(store.get("present"), dict):
            present = {key: bool(value) for key, value in store["present"].items()}
        if isinstance(store.get("as_of"), str):
            as_of = store["as_of"]
        if isinstance(store.get("synthetic"), bool):
            synthetic = store["synthetic"]
    if not path.exists():
        return {
            "found": False,
            "as_of": as_of,
            "synthetic": synthetic,
            "thesis": "",
            "counts": counts,
            "present": present,
            "sections": {},
            "markdown": "",
        }
    markdown = path.read_text(encoding="utf-8")
    sections, thesis, header_as_of = _split_overview(markdown)
    if as_of is None:
        as_of = header_as_of
    if synthetic is None:
        synthetic = "synthetic fixture" in markdown
    return {
        "found": True,
        "as_of": as_of,
        "synthetic": synthetic,
        "thesis": thesis,
        "counts": counts,
        "present": present,
        "sections": sections,
        "markdown": markdown,
    }


def _split_overview(markdown: str) -> tuple[dict[str, str], str, str | None]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    preamble: list[str] = []
    as_of = None
    for line in markdown.splitlines():
        if line.startswith("## "):
            current = _slug(line[3:].strip())
            sections[current] = []
            continue
        if current is None:
            if line.startswith("**As of:**"):
                as_of = line.split(":", 1)[1].strip() or None
            preamble.append(line)
        else:
            sections[current].append(line)
    rendered = {key: "\n".join(lines).strip() for key, lines in sections.items()}
    thesis = _thesis(preamble)
    return rendered, thesis, as_of


def _thesis(preamble: list[str]) -> str:
    paragraphs: list[str] = []
    for line in preamble:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("**"):
            continue
        if stripped.startswith("Observational summary"):
            continue
        if stripped.startswith("This run used a synthetic fixture"):
            continue
        if stripped.startswith("No Apple Health zip"):
            continue
        paragraphs.append(stripped)
    return paragraphs[-1] if paragraphs else ""


def _insight_block(section: str) -> tuple[str, str]:
    title = ""
    body: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not title and stripped.startswith("**") and stripped.endswith("**"):
            title = stripped.strip("*").strip()
            continue
        body.append(stripped)
    return title, " ".join(body)


def _message(status: str, out: Path) -> str:
    if status == "no_export":
        return "No Apple Health zip or export.xml found."
    if status == "already_ingested":
        return "Already ingested this export."
    if status == "none_pending":
        return "No unprocessed export zip."
    if status == "ingested":
        title, _body = _insight_block(_read_overview(out)["sections"].get("insight", ""))
        if title:
            return f"Ingested. Insight: {title}."
        return "Ingested."
    return "Ingest failed."


def _failed(exit_code: int, message: str = "Ingest failed.") -> dict:
    return {
        "status": "error",
        "exit_code": exit_code,
        "message": message,
        "overview_path": None,
        "counts": None,
        "present": None,
        "as_of": None,
        "synthetic": None,
        "insight": None,
    }


def _newest_unprocessed(items: list[dict], processed: set[str]) -> dict | None:
    pending = [item for item in items if item["id"] not in processed]
    if not pending:
        return None
    return pending[-1]


def _child_prefix(prefix: str, name: str) -> str | None:
    if prefix == "" and name == "exports":
        return "exports"
    if prefix == "exports" and _YEAR.fullmatch(name):
        return f"exports/{name}"
    parts = prefix.split("/") if prefix else []
    if len(parts) == 2 and _MONTH.fullmatch(name):
        return f"{prefix}/{name}"
    if len(parts) == 3 and name == "apple_health_export":
        return f"{prefix}/{name}"
    return None


def _is_zip(item: DriveItem) -> bool:
    if item.name.lower().endswith(".zip"):
        return True
    return item.mime_type in {"application/zip", "application/x-zip-compressed"}


def _out_dir(args: dict, ctx: ToolContext) -> Path:
    raw = args.get("out")
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return ctx.settings.state_dir


def _optional_path(value: object) -> Path | None:
    if isinstance(value, str) and value.strip():
        return Path(value)
    return None


def _processed_ids(out: Path) -> list[str]:
    payload = _read_json(out / PROCESSED_NAME)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return []
    return [item for item in ids if isinstance(item, str)]


def _remember_processed(out: Path, file_id: str) -> None:
    ids = [item for item in _processed_ids(out) if item != file_id]
    ids.append(file_id)
    _write_json(out / PROCESSED_NAME, {"ids": ids})


def _manifest_ids(out: Path) -> list[str]:
    payload = _read_json(out / MANIFEST_NAME)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    found = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("sha256"), str):
            found.append(entry["sha256"])
    return found


def _status_insight(out: Path) -> str | None:
    insight = _read_json(out / STATUS_NAME).get("insight")
    return insight if isinstance(insight, str) else None


def _zip_with_hash(root: Path, digest: str) -> Path | None:
    for item in root.rglob("*"):
        if not item.is_file() or item.suffix.lower() != ".zip":
            continue
        relative = item.relative_to(root)
        if any(part in _SKIP or part.startswith(".") for part in relative.parts):
            continue
        if _sha256(item) == digest:
            return item
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


def _safe_id(file_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", file_id)[:80]
    return cleaned or "export"
