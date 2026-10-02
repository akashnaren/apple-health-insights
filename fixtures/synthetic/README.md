# Synthetic fixture

Invented Apple Health export used by tests and CI. It is not a personal export and it contains no account, device serial, or contact data.

The series is shaped so the sleep-debt insight has a real gap to report: most nights are 7h 30m, and the three nights ending 2026-09-29 through 2026-10-01 are 5h 30m. A heart-rate sample of 177 and activity-summary energy of 999 are decoys. They are not v1 overview inputs, and they must not show up in the written overview or normalized store.

The zip wraps the same `export.xml` at `apple_health_export/export.xml`.
