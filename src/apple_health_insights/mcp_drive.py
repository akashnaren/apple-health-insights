"""Optional Google Drive client for the MCP inbox.

The ingest command never imports this module. List, download, and delete run
only when a caller passes a bearer token. File bodies stay on disk; this
module does not return them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

FOLDER_MIME = "application/vnd.google-apps.folder"
_API = "https://www.googleapis.com/drive/v3/files"
_ID = r"[A-Za-z0-9_-]+"


class DriveError(Exception):
    """Drive call failed. The message is safe to show; it has no file body."""


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    modified_time: str
    size: int


class DriveClient(Protocol):
    def list_children(self, folder_id: str) -> list[DriveItem]:
        """Return immediate children. Do not include file bodies."""

    def download_to(self, file_id: str, dest: Path) -> None:
        """Write one file to dest. Raise DriveError on failure."""

    def delete(self, file_id: str) -> None:
        """Delete one Drive file after a successful ingest."""


class HttpDriveClient:
    def __init__(self, token: str) -> None:
        self._token = token

    def list_children(self, folder_id: str) -> list[DriveItem]:
        _check_id(folder_id)
        items: list[DriveItem] = []
        page_token: str | None = None
        while True:
            payload = self._get_json(self._list_url(folder_id, page_token))
            for row in payload.get("files", []):
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                size_raw = row.get("size")
                try:
                    size = int(size_raw) if size_raw is not None else 0
                except (TypeError, ValueError):
                    size = 0
                items.append(
                    DriveItem(
                        id=row["id"],
                        name=row.get("name") if isinstance(row.get("name"), str) else "",
                        mime_type=row.get("mimeType") if isinstance(row.get("mimeType"), str) else "",
                        modified_time=(
                            row.get("modifiedTime") if isinstance(row.get("modifiedTime"), str) else ""
                        ),
                        size=size,
                    )
                )
            token = payload.get("nextPageToken")
            page_token = token if isinstance(token, str) and token else None
            if page_token is None:
                return items

    def download_to(self, file_id: str, dest: Path) -> None:
        _check_id(file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".partial")
        url = f"{_API}/{quote(file_id)}?alt=media&supportsAllDrives=true"
        request = Request(url, headers=self._headers())
        try:
            with urlopen(request, timeout=120) as response, partial.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise DriveError(f"Drive download failed ({_status_of(exc)}).") from None
        partial.replace(dest)

    def delete(self, file_id: str) -> None:
        _check_id(file_id)
        url = f"{_API}/{quote(file_id)}?supportsAllDrives=true"
        request = Request(url, method="DELETE", headers=self._headers())
        try:
            with urlopen(request, timeout=60):
                return
        except HTTPError as exc:
            if exc.code == 404:
                return
            raise DriveError(f"Drive delete failed ({exc.code}).") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise DriveError(f"Drive delete failed ({_status_of(exc)}).") from None

    def _list_url(self, folder_id: str, page_token: str | None) -> str:
        query = f"'{folder_id}' in parents and trashed = false"
        params = {
            "q": query,
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
            "pageSize": "100",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        encoded = "&".join(f"{key}={quote(value)}" for key, value in params.items())
        return f"{_API}?{encoded}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _get_json(self, url: str) -> dict:
        request = Request(url, headers=self._headers())
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise DriveError(f"Drive list failed ({_status_of(exc)}).") from None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DriveError("Drive list failed (unreadable response).") from exc
        if not isinstance(payload, dict):
            raise DriveError("Drive list failed (unexpected response).")
        return payload


def _check_id(file_id: str) -> None:
    import re

    if re.fullmatch(_ID, file_id) is None:
        raise DriveError("Drive id was rejected.")


def _status_of(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return str(exc.code)
    return "network"
