# Cup Racing Race Processor

Parses Assetto Corsa server result files into structured race detail data
for the Cup Racing league. Feeds into Cup Racing Data (portal) and
Cup Racing Insights.

No external dependencies. Python standard library only.

---

## Install

```sh
pip install -e .
```

The CLI is `race-processor`.

---

## Workflow

1. Create a venue folder and config: `race-processor init`
2. Copy AC server result files into the folder
3. Process: `race-processor process <folder>`

Repeat for each venue. Use `race-processor batch` to process all venues at once.

---

## Commands

### `race-processor init`

Creates a venue folder with a `venue.json` config.

```sh
race-processor init process/S20_Imola \
  --season S20 \
  --venue "Imola 2025" \
  --venue-order 1 \
  --format qual-standard-reverse
```

| Flag | Default | Purpose |
|---|---|---|
| `--season` | — | Season ID (e.g. `S20`) |
| `--venue` | — | Venue name (e.g. `"Imola 2025"`) |
| `--venue-order` | — | Venue number in the season (1, 2, 3...) |
| `--format` | `qual-standard-reverse` | Race format (see `race-processor formats`) |

### `race-processor process`

Processes one venue folder.

```sh
race-processor process process/S20_Imola -o data/Cup_Racing_Race_Details.json
```

| Flag | Default | Purpose |
|---|---|---|
| `-o`, `--output` | `Cup_Racing_Race_Details.json` | Output path (merged into existing) |
| `--dry-run` | off | Preview without writing |

### `race-processor batch`

Processes every venue folder in a parent directory.

```sh
race-processor batch process/ -o data/Cup_Racing_Race_Details.json
```

| Flag | Default | Purpose |
|---|---|---|
| `-o`, `--output` | `Cup_Racing_Race_Details.json` | Output path |
| `--dry-run` | off | Preview without writing |

### `race-processor formats`

Lists available race formats.

---

## Race formats

**`qual-standard-reverse`** (default)
Q1 → Race 1 (standard) → Race 2 (reverse of Race 1) → Q2 → Race 3 (standard) → Race 4 (reverse of Race 3)

**`qual-standard-standard`**
Q1 → Race 1 → Race 2 → Q2 → Race 3 → Race 4 (all standard grid)

---

## Step by step example

```sh
# 1. Create a venue folder
race-processor init process/S20_Imola \
  --season S20 --venue "Imola 2025" --venue-order 1

# 2. Copy your 6 AC server result files into the folder
#    (they sort chronologically by filename, so order is automatic)
cp ~/ac_server/results/260601_18*.json process/S20_Imola/
cp ~/ac_server/results/260601_19*.json process/S20_Imola/
cp ~/ac_server/results/260601_20*.json process/S20_Imola/

# 3. Process
race-processor process process/S20_Imola -o data/Cup_Racing_Race_Details.json

# Or process all venues at once
race-processor batch process/ -o data/Cup_Racing_Race_Details.json
```

The folder should look like this after step 2:

```
process/S20_Imola/
├── venue.json                  ← created by init
├── 260601_180000_Q.json        ← qualifying 1
├── 260601_183000_R.json        ← race 1
├── 260601_190000_R.json        ← race 2
├── 260601_193000_Q.json        ← qualifying 2
├── 260601_200000_R.json        ← race 3
└── 260601_203000_R.json        ← race 4
```

Files are matched to sessions in filename order (which is chronological, since AC names them by timestamp).

---

## Handling restarts

If a race was restarted, delete the abandoned result file from the venue
folder before processing. Only keep the completed session files.

---

## Driver name aliases

If a driver's Steam name doesn't match their Cup Racing name, open
`race_processor/processor.py` and add an entry to `DRIVER_ALIASES`:

```python
DRIVER_ALIASES = {
    "SteamName": "Cup Racing Name",
    "toby_racing94": "Toby",
}
```

Matching is case-insensitive. Set it once; it applies to all future runs.

---

## Tests

```sh
pip install pytest
pytest tests/ -v
```
