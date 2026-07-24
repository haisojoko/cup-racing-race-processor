"""Dataset assembly, additive merge, index, and publish.

Turns grouped events into the on-disk dataset: one JSON file per season plus a
top-level index.json. Writes are content-idempotent (the volatile lastUpdated
field is ignored when deciding whether anything actually changed) so repeated
ingests leave the tree — and any git/publish diff — untouched.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import Event
from .gridinfer import infer_grid
from .processor import build_registry, process_practice, process_qualifying, process_race

SCHEMA_VERSION = 2
GENERATOR = "cup-racing-race-processor 0.2.0"


# ---------------------------------------------------------------------------
# Event assembly
# ---------------------------------------------------------------------------

def event_signature(event: Event, config_fingerprint: str) -> str:
    """Stable id for an event's inputs: its source hashes + config fingerprint.

    If a source file is added/removed/changed, or a relevant config value
    changes, the signature changes and the event is reprocessed.
    """
    hashes = sorted(s.sha256 for s in event.sessions)
    payload = config_fingerprint + "|" + "|".join(hashes)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_event(
    event: Event,
    config_fingerprint: str,
    dropped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Process every session in an event into the output event object."""
    notes: list[str] = []

    quali_sorted = sorted(event.qualifying, key=lambda s: s.timestamp)
    qualifying_out: dict[str, Any] = {}
    quali_grids: list[tuple[Any, list[str]]] = []
    source_files: list[dict[str, Any]] = []

    for i, s in enumerate(quali_sorted, 1):
        role = f"qual{i}"
        q = process_qualifying(s.data)
        qualifying_out[role] = q
        quali_grids.append((s.timestamp, q["grid"]))
        source_files.append(_source_record(s, role))

    races_sorted = sorted(event.races, key=lambda s: s.timestamp)
    races_out: dict[str, Any] = {}
    prev_finish: list[str] | None = None
    prev_race_ts = None

    for i, s in enumerate(races_sorted, 1):
        role = str(i)
        # A qualifying counts as "fresh" if it ran after the previous race and
        # before this one — it deterministically sets this race's grid.
        fresh_grid = _fresh_quali_grid(quali_grids, prev_race_ts, s.timestamp)
        # For qualVsRace, use the most recent qualifying at all (fresh or older).
        qgrid = fresh_grid or _latest_grid_before(quali_grids, s.timestamp)
        registry = build_registry(s.data, notes)
        decision = infer_grid(
            s.data,
            fresh_quali_grid=fresh_grid,
            previous_finish=prev_finish,
            registry=registry,
        )
        race = process_race(s.data, grid=decision.grid, grid_meta=decision.as_meta(), notes=notes)
        race["qualVsRace"] = _qual_vs_race(qgrid, race["result"]) if qgrid else None
        races_out[role] = race
        prev_finish = [r["driverKey"] for r in race["result"]]
        prev_race_ts = s.timestamp
        source_files.append(_source_record(s, f"race{role}"))

    practice_out: list[dict[str, Any]] = []
    for s in sorted(event.practice, key=lambda s: s.timestamp):
        practice_out.append(process_practice(s.data))
        source_files.append(_source_record(s, "practice"))

    event_drops = [
        {k: v for k, v in d.items() if k not in ("trackKey", "date")}
        for d in (dropped or [])
        if d.get("trackKey") == f"{event.track}|{event.track_config}" and d.get("date") == event.date
    ]

    return {
        "eventId": event.event_id,
        "signature": event_signature(event, config_fingerprint),
        "venue": event.venue,
        "date": event.date,
        "track": event.track,
        "trackConfig": event.track_config,
        "qualifying": qualifying_out,
        "races": races_out,
        "practice": practice_out,
        "provenance": {
            "sourceFiles": source_files,
            "droppedFiles": event_drops,
            "notes": notes,
        },
    }


def _source_record(session, role: str) -> dict[str, Any]:
    rec = {
        "name": session.name,
        "sha256": session.sha256,
        "type": str(session.data.get("Type", "")),
        "role": role,
    }
    if session.timestamp_inferred:
        rec["note"] = "timestamp inferred from file mtime"
    return rec


def _latest_grid_before(quali_grids: list[tuple[Any, list[str]]], ts) -> list[str] | None:
    grid = None
    for q_ts, g in quali_grids:
        if q_ts <= ts:
            grid = g
    return grid


def _fresh_quali_grid(quali_grids, prev_race_ts, race_ts) -> list[str] | None:
    """Grid of a qualifying that ran after the previous race and before this one."""
    grid = None
    for q_ts, g in quali_grids:
        if q_ts <= race_ts and (prev_race_ts is None or q_ts > prev_race_ts):
            grid = g
    return grid


def _qual_vs_race(qual_grid: list[str], race_result: list[dict]) -> dict[str, Any]:
    qpos = {d: i + 1 for i, d in enumerate(qual_grid)}
    out: dict[str, Any] = {}
    for entry in race_result:
        key = entry.get("driverKey")
        if key in qpos:
            finish = entry["position"]
            out[key] = {
                "qualPosition": qpos[key],
                "finishPosition": finish,
                "delta": qpos[key] - finish,
            }
    return out


# ---------------------------------------------------------------------------
# Season files
# ---------------------------------------------------------------------------

def season_path(dataset_dir: Path, season_id: str) -> Path:
    return dataset_dir / "seasons" / f"{season_id}.json"


def load_season(dataset_dir: Path, season_id: str) -> dict[str, Any] | None:
    path = season_path(dataset_dir, season_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def existing_signatures(season_data: dict | None) -> dict[str, str]:
    if not season_data:
        return {}
    return {e["eventId"]: e.get("signature", "") for e in season_data.get("events", [])}


def assemble_season(
    season_id: str,
    event_objects: list[dict[str, Any]],
    unprocessed: list[dict[str, Any]],
) -> dict[str, Any]:
    """Order events by date, (re)compute venueOrder, wrap in the season record."""
    ordered = sorted(event_objects, key=lambda e: (e["date"], e["eventId"]))
    for i, e in enumerate(ordered, 1):
        e["venueOrder"] = i
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generator": GENERATOR,
        "season": season_id,
        "lastUpdated": _now(),
        "unprocessed": unprocessed,
        "events": ordered,
    }


def write_json_if_changed(path: Path, data: dict[str, Any]) -> bool:
    """Write pretty JSON only if content (ignoring lastUpdated) changed.

    Returns True if the file was written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if _strip_timestamp(old) == _strip_timestamp(new_text):
            return False
    path.write_text(new_text, encoding="utf-8")
    return True


def _strip_timestamp(text: str) -> str:
    lines = [ln for ln in text.splitlines() if '"lastUpdated"' not in ln]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def sync_schema_doc(dataset_dir: Path) -> bool:
    """Copy the packaged SCHEMA.md into the dataset dir if missing/outdated.

    Keeps the contract shipping with the data (and surviving a dataset wipe).
    Returns True if it wrote the file.
    """
    src = Path(__file__).with_name("SCHEMA.md")
    if not src.exists():
        return False
    dest = dataset_dir / "SCHEMA.md"
    new_text = src.read_text(encoding="utf-8")
    if dest.exists() and dest.read_text(encoding="utf-8") == new_text:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(new_text, encoding="utf-8")
    return True


def build_index(dataset_dir: Path) -> dict[str, Any]:
    """Build index.json from all season files currently on disk."""
    seasons_dir = dataset_dir / "seasons"
    seasons: dict[str, Any] = {}
    for path in sorted(seasons_dir.glob("*.json")) if seasons_dir.exists() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        season_id = data.get("season", path.stem)
        events_summary = []
        driver_set: set[str] = set()
        quality: list[str] = []
        for e in data.get("events", []):
            drivers = _event_drivers(e)
            driver_set.update(drivers)
            events_summary.append({
                "eventId": e["eventId"],
                "venue": e["venue"],
                "venueOrder": e.get("venueOrder"),
                "date": e["date"],
                "track": e["track"],
                "races": len(e.get("races", {})),
                "qualifying": len(e.get("qualifying", {})),
                "practice": len(e.get("practice", [])),
                "drivers": len(drivers),
            })
            for drop in e.get("provenance", {}).get("droppedFiles", []):
                quality.append(f"{e['date']} {e['venue']}: dropped {drop['file']} ({drop['reason']})")
            for note in e.get("provenance", {}).get("notes", []):
                quality.append(f"{e['date']} {e['venue']}: {note}")
        for un in data.get("unprocessed", []):
            quality.append(f"{season_id}: unprocessed {un['file']} ({un['reason']})")
        seasons[season_id] = {
            "file": f"seasons/{path.name}",
            "events": events_summary,
            "drivers": sorted(driver_set),
            "dataQuality": quality,
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generator": GENERATOR,
        "lastUpdated": _now(),
        "seasons": seasons,
    }


def _event_drivers(event: dict) -> set[str]:
    drivers: set[str] = set()
    for race in event.get("races", {}).values():
        drivers.update(race.get("drivers", {}).keys())
    for q in event.get("qualifying", {}).values():
        drivers.update(q.get("drivers", {}).keys())
    return drivers


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def publish(dataset_dir: Path, destinations, *, warn=print) -> list[Path]:
    """Mirror the dataset dir into each destination leaf.

    The destination leaf is fully owned by the publisher: it is removed and
    recopied. As a guard against pointing at a shared folder, refuse to delete
    a non-empty destination that has no index.json (i.e. it wasn't ours).
    """
    written: list[Path] = []
    for dest in destinations:
        dest = Path(dest)
        if dest.exists() and any(dest.iterdir()) and not (dest / "index.json").exists():
            warn(f"WARNING: skipping publish to {dest} — not empty and has no index.json "
                 f"(refusing to overwrite a folder that isn't ours)")
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(dataset_dir, dest)
        written.append(dest)
    return written


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
