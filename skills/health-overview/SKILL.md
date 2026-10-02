---
name: health-overview
description: Read the private Apple Health overview and its ranked insight. Use when summarizing sleep, activity, cardio, workouts, or body mass from the latest ingest.
---

# Health overview

Call `health_overview` for the structured digest and the short markdown. Call `health_insights` for the ranked list. Version 1 returns one insight, never more than three.

Both tools read the private `--out` directory (`state/health/` unless overridden). They return aggregates only. They do not return raw export XML.

The living overview document `1Olwsx1Utz3nqGq8D5DyaTJ5mzS1MsqUG` stays in private Drive. Do not copy it into the repository.

This is an observational digest, not medical advice. The Cursor Marketplace listing is parked and unpublished.
