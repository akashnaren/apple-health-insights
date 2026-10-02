# Security

This repository is **public**. Every file in it is world-readable.

Never commit:

- Real Apple Health exports, zips, `export.xml`, or CSV pulls
- Living overviews or normalized stores built from a real export
- Drive OAuth tokens, API keys, service accounts, or `.env` files

`fixtures/synthetic/` is the only place a sample export belongs. Those files are invented numbers labeled `SYNTHETIC`.

CI runs that fixture only. It does not download from Drive and it has no health credentials.

Scratch output under `state/health/` is gitignored. Keep a real overview there, or in the private Drive Health Data folder, not in git.
