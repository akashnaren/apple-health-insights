"""Tiny SYNTHETIC Health documents for tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def health_xml(body: str, export_date: str = "2026-10-01 08:00:00 -0700") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!-- SYNTHETIC fixture. Not a real Apple Health export. No personal data. -->
]>
<HealthData locale="en_US">
 <ExportDate value="{export_date}"/>
 {body}
</HealthData>
"""


def write_zip(path: Path, xml_text: str, member: str = "apple_health_export/export.xml") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, xml_text)


def chunked(xml_text: str, size: int = 20) -> io.BytesIO:
    class _Tiny(io.BytesIO):
        def read(self, n: int = -1) -> bytes:
            amount = size if n is None or n < 0 else min(size, n)
            return super().read(amount)

    return _Tiny(xml_text.encode("utf-8"))
