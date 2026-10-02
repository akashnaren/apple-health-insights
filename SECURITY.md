# Security

This repository is **public**. Every file in it is world-readable.

Never commit:

- Real Apple Health exports, zips, `export.xml`, or CSV pulls
- Living overviews or normalized stores built from a real export
- Drive OAuth tokens, API keys, service accounts, or `.env` files

`fixtures/synthetic/` is the only place a sample export belongs. Those files are invented numbers labeled `SYNTHETIC`.

CI runs that fixture only. It does not download from Drive and it has no health credentials.

A real export stays in the private Drive Health Data folder at `exports/YYYY/MM/apple_health_export/` (the live tree is `exports/2026/10/`). Soft-prove it only from private scratch (`state/health/`, gitignored). After a successful ingest the Drive zip is deleted. Do not copy the zip or the XML into this repo, CI logs, or a pull request. The living overview from a real export stays in that scratch directory or in Drive, not in git.
