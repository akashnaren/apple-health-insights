from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from apple_health_insights.mcp_drive import FOLDER_MIME, DriveError, DriveItem, HttpDriveClient
from apple_health_insights.mcp_server import TOOL_NAMES, handle
from apple_health_insights.mcp_tools import (
    HEALTH_DATA_FOLDER_ID,
    ToolContext,
    Settings,
    guard,
    health_inbox_status,
    health_ingest_latest,
    health_insights,
    health_overview,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_XML = ROOT / "fixtures" / "synthetic" / "export.xml"
FIXTURE_ZIP = ROOT / "fixtures" / "synthetic" / "export.zip"
FOLDER_ID = HEALTH_DATA_FOLDER_ID
DRIVE_FILE_ID = re.compile(r"\b1[A-Za-z0-9_-]{20,}\b")
ENV_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass
class _Node:
    item: DriveItem
    parent: str
    data: bytes = b""


class MemoryDrive:
    def __init__(self, nodes: list[_Node]) -> None:
        self.nodes = {node.item.id: node for node in nodes}
        self.deleted: list[str] = []

    def list_children(self, folder_id: str) -> list[DriveItem]:
        return [node.item for node in self.nodes.values() if node.parent == folder_id]

    def download_to(self, file_id: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.nodes[file_id].data)

    def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)
        self.nodes.pop(file_id, None)


def _context(tmp_path: Path, drive: MemoryDrive | None = None, inbox: Path | None = None) -> ToolContext:
    return ToolContext(
        settings=Settings(
            drive_token="token" if drive is not None else None,
            folder_id=FOLDER_ID,
            state_dir=tmp_path / "state" / "health",
            inbox_path=inbox,
        ),
        drive=drive,
    )


def _folder(file_id: str, name: str) -> DriveItem:
    return DriveItem(file_id, name, FOLDER_MIME, "", 0)


def _zip_item(file_id: str, name: str, modified: str, size: int) -> DriveItem:
    return DriveItem(file_id, name, "application/zip", modified, size)


def _record_line() -> str:
    for line in FIXTURE_XML.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("<Record"):
            return line.strip()
    raise AssertionError("fixture has no Record line")


def _assert_aggregate_only(payload: dict) -> None:
    rendered = json.dumps(payload)
    xml = FIXTURE_XML.read_text(encoding="utf-8")
    assert xml not in rendered
    assert _record_line() not in rendered
    for marker in ("<Record", "<?xml", "HKQuantityType", "HKCategoryType", "sourceName=", "Synthetic Watch", "177", "999"):
        assert marker not in rendered
    assert "asleep_minutes" not in rendered
    assert "personal-export" not in rendered


def test_tool_list_is_exactly_four_aggregates():
    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == list(TOOL_NAMES)
    assert names == [
        "health_inbox_status",
        "health_ingest_latest",
        "health_overview",
        "health_insights",
    ]
    unknown = handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_raw_export", "arguments": {}},
        }
    )
    assert unknown["error"]["message"] == "Unknown tool"


def test_stdio_lists_the_same_four_tools():
    payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "soft-prove", "version": "0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-m", "apple_health_insights.mcp_server"],
        input=payload + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    messages = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert [message["id"] for message in messages] == [1, 2]
    names = [tool["name"] for tool in messages[1]["result"]["tools"]]
    assert names == list(TOOL_NAMES)
    assert "<Record" not in completed.stdout


def test_empty_inbox_status_has_no_bodies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HEALTH_DRIVE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HEALTH_INBOX_PATH", raising=False)
    status = health_inbox_status({"out": str(tmp_path / "out")})
    assert status["source"] == "empty"
    assert status["items"] == []
    assert status["folder_id"] == FOLDER_ID
    assert status["layout"] == "exports/YYYY/MM/apple_health_export/"
    assert status["drive_configured"] is False
    payload = json.dumps(status)
    assert set(DRIVE_FILE_ID.findall(payload)) <= {FOLDER_ID}
    assert "overview_id" not in payload


def test_synthetic_ingest_overview_and_insights_omit_xml(tmp_path: Path):
    out = tmp_path / "state" / "health"
    ctx = _context(tmp_path)
    empty = health_inbox_status({"out": str(out)}, ctx)
    assert empty["items"] == []

    ingested = health_ingest_latest({"path": str(FIXTURE_ZIP), "out": str(out)}, ctx)
    _assert_aggregate_only(ingested)
    assert ingested["status"] == "ingested"
    assert ingested["exit_code"] == 0
    assert ingested["synthetic"] is True
    assert ingested["as_of"] == "2026-10-01"
    assert ingested["insight"] == "sleep_debt"
    assert ingested["counts"]["nights"] >= 7
    assert ingested["counts"]["workouts"] == 5
    assert isinstance(ingested["counts"]["nights"], int)
    assert "Ingested. Insight:" in ingested["message"]
    assert "export.zip" not in ingested["message"]

    overview = health_overview({"out": str(out)}, ctx)
    insights = health_insights({"out": str(out)}, ctx)
    _assert_aggregate_only(overview)
    _assert_aggregate_only(insights)
    assert overview["found"] is True
    assert "shorter than the prior 14-day sleep baseline" in overview["thesis"]
    assert "Sleep debt vs 14-day baseline" in overview["markdown"]
    assert "Last night:" in overview["sections"]["recovery_sleep"]
    assert insights["limit"] == 3
    assert len(insights["insights"]) == 1
    assert insights["insights"][0]["rank"] == 1
    assert insights["insights"][0]["kind"] == "sleep_debt"
    assert insights["insights"][0]["title"] == "Sleep debt vs 14-day baseline"

    call = handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "health_overview",
                "arguments": {"out": str(out)},
            },
        }
    )
    assert call["result"]["isError"] is False
    echoed = call["result"]["content"][0]["text"]
    assert FIXTURE_XML.read_text(encoding="utf-8") not in echoed
    assert "<Record" not in echoed


def test_local_inbox_label_hides_the_filename(tmp_path: Path):
    inbox = tmp_path / "exports" / "2026" / "10" / "apple_health_export"
    inbox.mkdir(parents=True)
    target = inbox / "personal-export.zip"
    target.write_bytes(FIXTURE_ZIP.read_bytes())
    ctx = _context(tmp_path)
    status = health_inbox_status({"inbox_path": str(tmp_path), "out": str(tmp_path / "out")}, ctx)
    assert status["source"] == "local"
    assert len(status["items"]) == 1
    item = status["items"][0]
    assert set(item) == {"id", "modifiedTime", "size", "label"}
    assert item["label"] == "export-2026-10"
    assert item["size"] == target.stat().st_size
    _assert_aggregate_only(status)


def test_drive_ingest_deletes_zip_only_after_success(tmp_path: Path):
    zip_bytes = FIXTURE_ZIP.read_bytes()
    older = "drive-old"
    newer = "drive-new"
    bad = "drive-bad"
    drive = MemoryDrive(
        [
            _Node(_folder("exports", "exports"), FOLDER_ID),
            _Node(_folder("year", "2026"), "exports"),
            _Node(_folder("month", "10"), "year"),
            _Node(_folder("bundle", "apple_health_export"), "month"),
            _Node(
                _zip_item(older, "personal-export.zip", "2026-10-01T00:00:00Z", 10),
                "bundle",
                zip_bytes,
            ),
            _Node(
                _zip_item(newer, "personal-export.zip", "2026-10-02T00:00:00Z", len(zip_bytes)),
                "bundle",
                zip_bytes,
            ),
        ]
    )
    ctx = _context(tmp_path, drive)
    out = tmp_path / "state" / "health"
    status = health_inbox_status({"out": str(out)}, ctx)
    assert [item["id"] for item in status["items"]] == [older, newer]
    assert {item["label"] for item in status["items"]} == {"export-2026-10"}
    _assert_aggregate_only(status)

    (out).mkdir(parents=True)
    (out / "mcp-drive-processed.json").write_text(json.dumps({"ids": [older]}) + "\n", encoding="utf-8")
    ingested = health_ingest_latest({"out": str(out)}, ctx)
    _assert_aggregate_only(ingested)
    assert ingested["status"] == "ingested"
    assert drive.deleted == [newer]
    assert not (out / "inbox" / f"{newer}.zip").exists()
    assert older in drive.nodes

    again = health_ingest_latest({"out": str(out)}, ctx)
    assert again["status"] == "none_pending"
    assert drive.deleted == [newer]

    drive.nodes[bad] = _Node(_zip_item(bad, "notes.zip", "2026-10-03T00:00:00Z", 4), "bundle", b"nope")
    failed = health_ingest_latest({"out": str(out), "force": True}, ctx)
    assert failed["status"] == "error"
    assert bad not in drive.deleted
    assert (out / "inbox" / f"{bad}.zip").exists()
    assert "<Record" not in json.dumps(failed)


def test_guard_drops_markup():
    refused = guard({"markdown": "<Record type=\"HKQuantityTypeIdentifierStepCount\"/>"})
    assert refused == {
        "refused": True,
        "reason": "Response contained export markup and was dropped.",
    }


def test_drive_id_with_a_quote_never_calls_the_network():
    client = HttpDriveClient("token")
    with pytest.raises(DriveError):
        client.list_children("abc' or '1'='1")


def test_plugin_descriptor_matches_stdio_launch():
    plugin = json.loads((ROOT / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "apple-health-insights"
    assert plugin["mcpServers"] == "./mcp.json"
    assert "parked" in plugin["description"].lower()
    assert "unpublished" in plugin["description"].lower()
    server = mcp["mcpServers"]["apple-health-insights"]
    assert server["command"] in {"python", "python3"}
    assert server["args"] == ["-m", "apple_health_insights.mcp_server"]
    for skill in ("health-ingest", "health-overview"):
        text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "<Record" not in text
    assert not (ROOT / ".cursor-plugin" / "marketplace.json").exists()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    overview_skill = (ROOT / "skills" / "health-overview" / "SKILL.md").read_text(encoding="utf-8")
    assert "Marketplace listing is parked and unpublished" in readme
    assert "exports/YYYY/MM/apple_health_export/" in readme
    assert "private under the Health Data root or in local scratch" in readme
    assert ENV_UUID.search(readme) is None
    for text in (readme, security, overview_skill):
        assert set(DRIVE_FILE_ID.findall(text)) <= {FOLDER_ID}
        assert "local scratch" in text or "scratch directory" in text
    assert "aggregates" in security.lower()
    assert "do not embed a Drive file id" in overview_skill


def test_docs_and_plugin_files_do_not_embed_export_xml():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "fixtures"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".md", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "<Record" not in text
        assert "HKQuantityType" not in text
        assert "<?xml" not in text
