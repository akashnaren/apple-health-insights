---
name: health-ingest
description: List Apple Health export zips and ingest the newest one through the package CLI. Use when checking the Health Data inbox or running an ingest. Returns status and counts only.
---

# Health ingest

Call `health_inbox_status`, then `health_ingest_latest`.

`health_ingest_latest` runs `python -m apple_health_insights ingest` (`apple-health-insights ingest` is the same entrypoint). In CI, pass the synthetic fixture path. A Drive download runs only when a token is already in the environment.

Drive layout is `exports/YYYY/MM/apple_health_export/` under Health Data folder `1kmz1nYyZS7je75Ej_QH9r8m0880W62xo`. After a successful Drive ingest, the server deletes that zip in Drive and removes the scratch copy.

Return status, counts, and the overview path. Do not request export XML, zip bytes, record attributes, GPS, or notes. Do not add a tool that reads the raw export.

The Cursor Marketplace listing is parked and unpublished.
