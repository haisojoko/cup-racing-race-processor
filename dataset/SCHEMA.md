# Cup Racing Dataset — Schema Contract (v2)

This directory is the **additive historical dataset** produced by
`cup-racing-race-processor`. It is meant to be consumed by the portal,
the insights tool, and any future analysis. This document is the contract.

## Versioning policy

- Every file carries `schemaVersion` (currently `2`).
- **Consumers must tolerate unknown keys** — new fields are added without a
  version bump. `schemaVersion` only increases on a *breaking* change (a field
  removed or its meaning/shape changed).
- All times are **milliseconds** unless the field name says otherwise.
- Any value can be `null` when the data to compute it was unavailable; consumers
  should treat `null`/absent as "unknown", not zero.

## Layout & how to consume

```
dataset/
├── index.json          # registry — read this first
├── SCHEMA.md           # this file
└── seasons/
    ├── S22.json        # one file per season
    └── ...
```

Read `index.json` to discover seasons, events, drivers, and data-quality notes,
then load the per-season file(s) you need. Season files are independent and
additive — a new season or a newly backfilled event only touches its own file.

## `index.json`

```jsonc
{
  "schemaVersion": 2,
  "generator": "cup-racing-race-processor 0.2.0",
  "lastUpdated": "<UTC ISO-8601>",
  "seasons": {
    "S22": {
      "file": "seasons/S22.json",
      "events": [
        { "eventId": "2026-05-26-jr-road-atlanta-2022", "venue": "Road Atlanta",
          "venueOrder": 1, "date": "2026-05-26", "track": "csp/.../jr_road_atlanta_2022",
          "races": 3, "qualifying": 2, "practice": 0, "drivers": 19 }
      ],
      "drivers": ["<canonical labels seen this season>"],
      "dataQuality": ["2026-05-26 Road Atlanta: dropped ... (byte-identical)"]
    }
  }
}
```

## Season file — `seasons/<SID>.json`

```jsonc
{
  "schemaVersion": 2,
  "generator": "...",
  "season": "S22",
  "lastUpdated": "<UTC ISO-8601>",
  "unprocessed": [ { "file": "bad.json", "reason": "unreadable|unrecognized", "detail": "..." } ],
  "events": [ <Event>, ... ]   // ordered by date; venueOrder is 1-based by date
}
```

### Event

```jsonc
{
  "eventId": "2026-05-26-jr-road-atlanta-2022",   // <date>-<track-slug> (+ -2 on collision)
  "signature": "<hash>",        // internal: source hashes + config fingerprint (skip-if-unchanged)
  "venue": "Road Atlanta",      // display name (configurable via trackDisplayNames)
  "venueOrder": 1,              // 1-based within the season, by date
  "date": "2026-05-26",
  "track": "csp/2144/../jr_road_atlanta_2022",
  "trackConfig": "full",
  "qualifying": { "qual1": <Qualifying>, "qual2": <Qualifying> },
  "races": { "1": <Race>, "2": <Race>, "3": <Race> },   // chronological, any count
  "practice": [ <Practice>, ... ],
  "provenance": {
    "sourceFiles": [ { "name": "...RACE.json", "sha256": "...", "type": "RACE", "role": "race1" } ],
    "droppedFiles": [ { "file": "...", "sha256": "...", "reason": "byte-identical|aborted-restart", "detail": "..." } ],
    "notes": [ "car 5 had multiple GUIDs — possible driver swap" ]
  }
}
```

### Race

Times are ms. Driver maps are keyed by **driver label** (see Driver identity).

| key | type | meaning |
|---|---|---|
| `grid` | `[label]` | starting order (see grid inference) |
| `gridSource` | string | `qualifying` \| `previous-race` \| `reversed-previous` \| `first-lap-inferred` \| `unknown` \| `provided` |
| `gridConfidence` | string | `high` \| `medium` \| `low` \| `unknown` |
| `gridScore` | number\|null | Kendall tau of the chosen grid vs lap-1 order (null when inferred/unknown) |
| `drivers` | `{label: {driver, guid, carId, car, skin?, team?}}` | metadata |
| `laps` | `{label: [ms]}` | valid lap times, in order |
| `sectors` | `{label: [[ms|null,…]]}` | per-lap sector times, index-aligned with `laps` |
| `cuts` | `{label: [int]}` | per-lap cut count, index-aligned |
| `tyres` | `{label: [str]}` | per-lap tyre compound, index-aligned |
| `positions` | `{label: [int]}` | position after each leader-lap |
| `positionDetails` | `{label: [{lap, position, lapsCompleted, status}]}` | `status` = `classified` \| `dnf_or_lapped` |
| `positionChanges` | `{label:{gained,lost,net}}` \| null | **null when gridConfidence is low/unknown** |
| `overtakes` | `[{lap, driver, passed, positionsGained}]` | on-track passes (see caveats) |
| `qualVsRace` | `{label:{qualPosition,finishPosition,delta}}` \| null | null when no qualifying preceded the race |
| `result` | `[{driver,driverKey,guid,carId,car,position,totalTimeMs,bestLapMs,laps,ballast,restrictor}]` | classified finishing order |
| `contacts` | `[{lap,lapConfidence,driver1,driver2,impactSpeed,worldPosition?}]` | collisions (see caveats) |
| `pace` | `{label:{avgMs,medianMs,bestMs,lapsUsed}}` | excludes lap 1 and laps >120% of median |

### Qualifying

```jsonc
{ "grid": ["<label>", ...],            // sorted by best lap
  "drivers": { "<label>": {driver, guid, carId, car, skin?, team?} },
  "times":   { "<label>": {bestMs, laps: [ms, ...]} } }
```

### Practice

```jsonc
{ "participants": ["<label>", ...],
  "drivers": { "<label>": {...} },
  "bestLaps": { "<label>": ms },
  "lapCounts": { "<label>": int } }
```

## Grid inference

The AC result files do not record the grid, so it is inferred:

- If a **qualifying ran immediately before** the race, its order is the grid
  (`gridSource: qualifying`, high confidence). This is the league convention.
- Otherwise the previous race's finish (standard) and its reverse are compared
  against the race's lap-1 crossing order; the better correlation wins
  (`previous-race` / `reversed-previous`).
- If nothing correlates, the grid falls back to lap-1 crossing order
  (`first-lap-inferred`, low) or `unknown` when there's no lap data.

`gridScore` (Kendall tau, −1..1) records how strongly the chosen order agrees
with lap-1 crossing order, so every decision is auditable. When confidence is
low/unknown, `positionChanges` is `null` — it would otherwise be derived from an
unreliable start order.

## Driver identity

- Drivers are keyed by a **label**: their canonical name, or `Name (car N)` only
  when two *different* people (different GUIDs) share a display name in one
  session.
- Identity is the Steam **GUID** — one person stays one driver across car-slot
  changes within a session and across display-name changes between sessions.
  The car actually driven is canonical; `Cars[]` supplies skin/team.
- Canonical names come from `driverNames` (GUID → name) in `config.json`, with
  `driverAliases` (name → name) as a fallback for GUID-less entries.
- Cross-session joins (a driver across events/seasons) are **by label string**,
  which is stable once `driverNames` is populated.

## Known limitations

- **Contact lap is usually `null`.** AC collision events carry no timestamp, so
  `contacts[].lap` cannot be attributed; `worldPosition {x,z}` is provided
  instead for locating incidents.
- **Contacts are de-mirrored.** AC logs each car-to-car collision twice (once
  per car, driver1/driver2 swapped, each with its own impact speed/position).
  Mirror pairs — same unordered pair, contact points within 3 m, not a
  different known lap — are collapsed to one contact (higher impact kept), so
  counts reflect physical collisions, not log lines.
- **Overtakes include lapped traffic.** A pass is any classified-vs-classified
  position swap between consecutive leader-laps; being lapped/unlapped is not
  distinguished.
- **Opening-lap (`lap == 1`) overtakes** are the grid → lap-1 launch, recorded
  only when the grid is trusted (`gridConfidence` high/medium). Low/unknown
  grids are derived from lap-1 order itself, so no launch passes are emitted.
- **Lap timestamps are server-uptime ms**, not wall-clock — usable only for
  intra-session ordering.
- **Reverse-grid confidence is often `medium`.** After a reverse start the fast
  cars carve forward before completing lap 1, so lap-1 order only weakly agrees
  with the true grid.
