# Cup Racing Race Processor

Turns Assetto Corsa server result files into an additive historical dataset for
the Cup Racing league. Drop raw result JSONs into per-season folders, run one
command, and get a clean per-season dataset that the portal, the insights tool,
and future analysis consume.

No external dependencies — Python standard library only (≥3.9).

---

## Install

```sh
pip install -e .
```

The CLI is `race-processor`.

---

## Workflow

1. Drop AC server result JSONs into the season folder they belong to:
   `inbox/S22/`, `inbox/S23/`, …
2. Run `race-processor ingest`.

That's it. No per-venue setup, no flags to remember, no fixed race format. The
tool groups whatever sessions it finds into events (venue race-days), infers the
grids, writes `dataset/seasons/<Season>.json` + `dataset/index.json`, and (if
configured) publishes copies to your consumer repos.

Re-run any time you add more files — it only reprocesses what changed.

```
inbox/
├── S22/
│   ├── 2026_5_26_21_22_QUALIFY.json
│   ├── 2026_5_26_21_35_RACE.json
│   └── ...
├── S23/
└── ...
```

Season folders `S1`..`S24` are created automatically. Seasons can be partially
filled or empty — the tool processes whatever is present.

---

## Commands

### `race-processor ingest`  (the main command)

Scans every season folder, builds the dataset additively, publishes.

| Flag | Purpose |
|---|---|
| `--dry-run` | Report what would happen; write nothing |
| `--no-publish` | Build the dataset but don't copy to destinations |
| `--config PATH` | Use a specific `config.json` (default: current directory) |

Events whose source files and relevant config are unchanged since the last run
are skipped. Bad input never aborts the run — duplicates and aborted restarts
are dropped with a warning, unreadable files are reported, and everything valid
still processes.

### `race-processor rebuild`

Same as `ingest` but reprocesses every event from scratch and drops events whose
raw files no longer exist. Use after changing processing logic.

### `race-processor publish`

Copies the existing `dataset/` to the configured destinations without
reprocessing.

### `race-processor roster`

Lists every driver GUID and the display names it has used, for filling in
`driverNames`. Add `-o driver_roster.json` to write an editable template.

---

## Configuration — `config.json`

Auto-created with defaults on first run. Keys:

| Key | Default | Purpose |
|---|---|---|
| `inboxDir` | `"inbox"` | Drop-zone root (season subfolders live here) |
| `datasetDir` | `"dataset"` | Where the output dataset is written |
| `publishDestinations` | `[]` | Folders to mirror the dataset into (consumer repos) |
| `driverNames` | `{}` | Map Steam **GUID** → canonical league name (preferred) |
| `driverAliases` | `{}` | Map display **name** → canonical name (fallback, no GUID) |
| `trackDisplayNames` | `{}` | Map `TrackName\|TrackConfig` → a nice venue name |
| `eventGapHours` | `4` | Sessions more than this far apart start a new event |
| `restartWindowMinutes` | `30` | Window for detecting aborted-restart duplicates |

Relative paths resolve against the config file's directory. Editing
`driverNames`, `driverAliases`, or `trackDisplayNames` automatically reprocesses
affected events on the next `ingest`.

### Mapping drivers to your league names

A driver is identified by their Steam **GUID**, so one person stays one driver
even if they change their Steam display name or cycle through car slots in a
session. Map GUIDs to your canonical names with `driverNames`:

```json
"driverNames": { "76561198056789142": "Josie" }
```

To get the full list of GUIDs to map, run:

```sh
race-processor roster -o driver_roster.json
```

That writes every driver GUID with the display name(s) it has used and the
seasons it appears in. Fill in the canonical name for each, then copy the
`guid → name` pairs into `driverNames` and re-run `ingest`. (`driverAliases` is
only needed for the rare entry that has no GUID.)

Example:

```json
{
  "inboxDir": "inbox",
  "datasetDir": "dataset",
  "publishDestinations": [
    "../cup-racing-test-portal/sim-racing-historical-viz/data/cup-dataset",
    "../cup-racing-insights/data/cup-dataset"
  ],
  "driverAliases": { "toby_racing94": "Toby" },
  "trackDisplayNames": { "csp/2144/../jr_road_atlanta_2022|full": "Road Atlanta" },
  "eventGapHours": 4,
  "restartWindowMinutes": 30
}
```

---

## Output

An additive dataset under `dataset/`:

```
dataset/
├── index.json          # registry of seasons, events, drivers, data-quality notes
├── SCHEMA.md           # the full field-by-field contract for consumers
└── seasons/
    └── S22.json        # one file per season
```

See **[dataset/SCHEMA.md](dataset/SCHEMA.md)** for the complete schema. In short,
each race records per driver: lap & sector times, cuts, tyres, lap-by-lap
positions, positions gained/lost, derived overtakes, qualifying-vs-race deltas,
pace, the finishing result, and car contacts — plus grid inference metadata and
full provenance (which files fed each event, what was dropped and why).

Drivers are tracked by Steam ID + car, so two people sharing a display name stay
separate (`Alex` and `Alex (car 2)`).

---

## How grids are inferred

AC result files don't store the grid. A qualifying session immediately before a
race is trusted as that race's grid (the league convention). For a race that
follows another race, the previous finish and its reverse are compared against
the race's lap-1 order to decide standard vs reverse grid. Every race records
`gridSource`, `gridConfidence`, and `gridScore` so the decision is auditable;
when confidence is low, position-change stats are left `null` rather than
computed from a guessed grid.

---

## Duplicates and restarts

Handled automatically — no manual file deletion:

- **Byte-identical files** → the earliest is kept, the rest dropped.
- **Aborted restarts** (a short session right before a full one on the same
  track) → the aborted one is dropped; a race whose leader completed the
  distance is never dropped.

Every decision is logged to the console and recorded in the event's provenance.

---

## Tests

```sh
pip install pytest
pytest -q
```
