# Security

This repository is **public**. Every file in it is world-readable.

Never commit:

- Real Apple Health exports, zips, `export.xml`, or CSV pulls
- Living overviews or normalized stores built from a real export
- Drive OAuth tokens, API keys, service accounts, or `.env` files

`fixtures/synthetic/` is the only place a sample export belongs. Those files are invented numbers labeled `SYNTHETIC`.

CI runs that fixture only. It does not download from Drive and it has no health credentials.

A real export stays in the private Drive Health Data folder. Soft-prove it only from private scratch (`state/health/`, gitignored), then leave the zip and the XML there. Do not copy either into this repo, CI logs, or a pull request. The living overview from a real export stays in that scratch directory or in Drive, not in git.
