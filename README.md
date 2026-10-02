# Apple Health insights

A manual Apple Health export is a zip full of XML. This project turns that file into a short private overview: how sleep, activity, cardio, workouts, and body mass look in the export, plus one insight. The code is public. The export is not.

Nothing here connects to an iPhone, and nothing here calls Google Drive. You export on the phone, put the zip in a private Drive folder, and run the ingest on a machine you control. The living overview stays in that private folder or in a local scratch directory.

This is an observational digest. It is not medical advice, a diagnosis, or a training plan.

## What you get

v1 reads a fixed set of Apple types and writes two private files:

- `Health overview.md`, a narrative of aggregates
- `normalized-store.json`, the same rollup as daily and nightly numbers

The overview always includes one insight. Sleep debt versus the prior 14 days comes first. That comparison needs at least seven nights in the baseline window. A gap of 30 minutes or more is stated as shorter or longer; a smaller gap is stated as close to the baseline. If the baseline is too thin, the run looks for a strain and recovery mismatch: recent steps or active energy at least 1.2 times the earlier baseline, together with recent sleep at least 45 minutes shorter than the earlier nights. When the history is there but the pattern is not, the overview says so. Later ideas, such as a standalone resting-heart-rate insight, workout streaks, and goal adherence, are not in this version.

A series that is absent from the export is written as **no data in this export**. A gap is never filled with zero.

## What this is not

- Not a live HealthKit sync
- Not a Drive client, and not an MCP plugin
- Not a place to commit exports, tokens, or a real overview
- Not a medical device

## How a week moves through the system

Once a week, export from the Health app on the iPhone and drop the zip into the private Drive folder `1kmz1nYyZS7je75Ej_QH9r8m0880W62xo` (Health Data). That folder id is documentation of the drop zone. This repository has no Drive token and cannot read the folder.

When a new zip is there, ingest it locally. The package records a content hash so the same file is not written twice. Archiving the zip to a `processed/` folder inside Drive is a later step for the integration layer. This package only skips anything already sitting in a local `processed/` directory.

```mermaid
flowchart LR
  phone[iPhone Health export]
  drive[Private Drive Health Data]
  detect[Find the newest zip]
  parse[Parse export.xml]
  rollup[Normalize the v1 metrics]
  overview[Private Health overview]
  insight[One insight]
  phone --> drive --> detect --> parse --> rollup
  rollup --> overview
  rollup --> insight
```

Inside the package the path is the same whether the input is a zip, a loose `export.xml`, or a folder:

```mermaid
flowchart TD
  start[Path you pass in]
  kind{What is it?}
  zip[Read export.xml from the zip]
  xml[Read the XML file]
  folder[Newest zip, else export.xml]
  empty[Status: no export]
  keep[Keep v1 types only]
  days[Roll up by local day and wake date]
  missing{Is the series in the file?}
  nodata[Write: no data in this export]
  values[Write the aggregate from real samples]
  choose[Choose one insight]
  md[Health overview markdown]
  start --> kind
  kind -->|zip| zip --> keep
  kind -->|xml| xml --> keep
  kind -->|folder with a file| folder --> keep
  kind -->|folder with nothing| empty
  keep --> days --> missing
  missing -->|no| nodata --> choose
  missing -->|yes| values --> choose
  choose --> md
```

The empty folder is a normal result. The command exits 0, says that no export was found, and writes an overview that says so. It does not invent a night of sleep or a step count. If an overview is already in the output directory, an empty run leaves it alone.

```mermaid
flowchart TD
  ask{Enough history?}
  debt[Sleep debt versus the 14-day baseline]
  both{Sleep and activity both have a shorter history?}
  mismatch[Strain and recovery mismatch]
  later[No insight yet]
  ask -->|7 or more baseline nights, plus a recent night| debt
  ask -->|not yet| both
  both -->|yes| mismatch
  both -->|no| later
```

## Metrics

Windows are calendar dates on each sample's own timestamp. The export date is "today" for the digest. Sleep is attached to the wake date, which is the local date when the interval ends. Steps, exercise, energy, resting heart rate, and HRV are attached to the local date when the sample starts.

| Section | Apple types | What the overview says |
|---|---|---|
| Recovery / sleep | Sleep analysis, asleep stages preferred over in-bed | Last night, 7-day average, and whether those nights were steady or variable |
| Strain / activity | Step count, exercise time, active energy. Activity summary fills a day only when that quantity is missing | Today and a 7-day average |
| Cardio | Resting heart rate, HRV SDNN when present | 7-day average against the 7 days before that. HRV is "no data" when the type is absent |
| Workouts | Workout elements | Count and top types for 7 days and 30 days |
| Body | Body mass, optional | Latest value, and the change across the prior 30 days when two points exist |

Displayed averages are rounded. Overlapping sleep intervals are merged, so a watch export and a phone export of the same night are not added together. High-frequency heart rate is ignored on purpose. It is not part of the digest.

"7-day" means the export date and the six days before it. The sleep baseline is the 14 dates before the latest three dates. Workout "30 days" means the export date and the 29 days before it. Body mass includes a point that falls on the day 30 days before the export.

## Run it

```bash
python -m pip install -e ".[dev]"
python -m apple_health_insights ingest fixtures/synthetic/export.zip --out state/health
```

`state/health/` is gitignored. Point `--out` somewhere private if you ever run a real export. A second run of the same file prints `already_ingested` and does not rewrite the overview. `--force` writes it again.

```bash
python -m apple_health_insights ingest /path/to/empty-folder --out state/health
python -m apple_health_insights ingest fixtures/synthetic/export.xml --out state/health --force
```

Exit `0` means ingested, already ingested, or a clean empty folder. Exit `2` means the path was missing, the zip had no `export.xml`, or the XML could not be parsed.

Try the synthetic zip before a real one. The fixture is labeled `SYNTHETIC`, the last three nights are short on purpose, and the overview should land on sleep debt. A heart-rate sample of 177 count/min and a summary energy of 999 kcal are in the file as decoys. They are not overview inputs.

## Privacy

- The GitHub repo is public. Treat every committed file as world-readable.
- Do not commit a real zip, `export.xml`, CSV pull, living overview, or normalized store.
- Do not commit Drive tokens or `.env` files.
- Docs and CI use the synthetic fixture only.
- Tooling that later wraps this package should return the digest, not the XML.

## Development

```bash
python -m pytest
```

GitHub Actions runs that same suite on the synthetic fixture. There is no Drive download in CI.

## License

Private use for the owner. Treat committed files as world-readable.
