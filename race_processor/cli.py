"""CLI entry point for the race processor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .processor import (
    load_server_result,
    process_qualifying,
    process_race,
    merge_into_output,
)


FORMATS = {
    "qual-standard-reverse": {
        "description": "Q1 → R1 (standard) → R2 (reverse) → Q2 → R3 (standard) → R4 (reverse)",
        "sessions": [
            {"type": "qualifying", "id": "qual1"},
            {"type": "race", "id": "1", "grid_from": "qual1"},
            {"type": "race", "id": "2", "grid_from": "reverse_1"},
            {"type": "qualifying", "id": "qual2"},
            {"type": "race", "id": "3", "grid_from": "qual2"},
            {"type": "race", "id": "4", "grid_from": "reverse_3"},
        ],
    },
    "qual-standard-standard": {
        "description": "Q1 → R1 → R2 → Q2 → R3 → R4 (all standard grid)",
        "sessions": [
            {"type": "qualifying", "id": "qual1"},
            {"type": "race", "id": "1", "grid_from": "qual1"},
            {"type": "race", "id": "2", "grid_from": "qual1"},
            {"type": "qualifying", "id": "qual2"},
            {"type": "race", "id": "3", "grid_from": "qual2"},
            {"type": "race", "id": "4", "grid_from": "qual2"},
        ],
    },
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="race-processor",
        description="Process Assetto Corsa race results for the Cup Racing league.",
    )
    sub = parser.add_subparsers(dest="command")

    proc = sub.add_parser("process", help="Process a venue folder")
    proc.add_argument("folder", type=Path, help="Path to venue folder containing venue.json and result files")
    proc.add_argument("--output", "-o", type=Path, default=Path("Cup_Racing_Race_Details.json"), help="Output JSON path")
    proc.add_argument("--dry-run", action="store_true", help="Preview without writing")

    batch = sub.add_parser("batch", help="Process all venue folders in a directory")
    batch.add_argument("folder", type=Path, nargs="?", default=Path("process"), help="Parent folder containing venue subfolders (default: process/)")
    batch.add_argument("--output", "-o", type=Path, default=Path("Cup_Racing_Race_Details.json"), help="Output JSON path")
    batch.add_argument("--dry-run", action="store_true", help="Preview without writing")

    fmt = sub.add_parser("formats", help="List available race formats")

    init = sub.add_parser("init", help="Create a venue.json template in a folder")
    init.add_argument("folder", type=Path, help="Venue folder path")
    init.add_argument("--season", required=True, help="Season ID (e.g. S20)")
    init.add_argument("--venue", required=True, help="Venue name (e.g. 'Imola 2025')")
    init.add_argument("--venue-order", required=True, type=int, help="Venue number in the season")
    init.add_argument("--format", dest="fmt", default="qual-standard-reverse", choices=list(FORMATS.keys()), help="Race format")

    args = parser.parse_args(argv)

    if args.command == "formats":
        cmd_formats()
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "process":
        cmd_process(args.folder, args.output, args.dry_run)
    elif args.command == "batch":
        cmd_batch(args.folder, args.output, args.dry_run)
    else:
        parser.print_help()


def cmd_formats() -> None:
    print("Available race formats:\n")
    for key, fmt in FORMATS.items():
        print(f"  {key}")
        print(f"    {fmt['description']}\n")


def cmd_init(args) -> None:
    args.folder.mkdir(parents=True, exist_ok=True)
    venue_json = args.folder / "venue.json"

    config = {
        "season": args.season,
        "venue": args.venue,
        "venueOrder": args.venue_order,
        "format": args.fmt,
    }

    venue_json.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Created {venue_json}")
    print(f"Now copy your AC server result files ({_count_sessions(args.fmt)} session files) into {args.folder}/")


def _count_sessions(fmt_key: str) -> int:
    return len(FORMATS.get(fmt_key, {}).get("sessions", []))


def cmd_process(folder: Path, output: Path, dry_run: bool) -> None:
    venue_json = folder / "venue.json"
    if not venue_json.exists():
        print(f"Error: {venue_json} not found. Run 'race-processor init {folder} ...' first.", file=sys.stderr)
        sys.exit(1)

    config = json.loads(venue_json.read_text())
    season = config["season"]
    venue = config["venue"]
    venue_order = config["venueOrder"]
    fmt_key = config.get("format", "qual-standard-reverse")

    if fmt_key not in FORMATS:
        print(f"Error: unknown format '{fmt_key}'. Run 'race-processor formats' to see options.", file=sys.stderr)
        sys.exit(1)

    fmt = FORMATS[fmt_key]
    sessions = fmt["sessions"]

    print(f"Season {season} | {venue} (venue {venue_order})")
    print(f"Format: {fmt['description']}")

    file_map, data_map, dropped, errors = _prepare_sessions(folder, sessions)

    for f in dropped:
        print(f"  Skipping non-scored session: {f.name}")

    if errors:
        print()
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        print(
            "\nFix the venue folder so it contains exactly the files this format "
            "expects (delete abandoned restart files, remove practice sessions), "
            "then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)

    print()

    qual_outputs: dict[str, dict] = {}
    race_outputs: dict[str, dict] = {}

    for session in sessions:
        file = file_map.get(session["id"])
        data = data_map.get(session["id"])
        if not file or data is None:
            print(f"  [{session['id']}] SKIPPED — no file assigned")
            continue

        if session["type"] == "qualifying":
            print(f"  [{session['id']}] Qualifying: {file.name}")
            qual_result = process_qualifying(data)
            qual_outputs[session["id"]] = qual_result
            print(f"    Grid: {', '.join(qual_result['grid'])}")

        elif session["type"] == "race":
            grid = _resolve_grid(session, qual_outputs, race_outputs)
            race_result = process_race(data, grid=grid)
            race_outputs[session["id"]] = race_result

            laps = max((len(t) for t in race_result["laps"].values()), default=0)
            contacts = len(race_result["contacts"])
            winner = race_result["result"][0]["driver"] if race_result["result"] else "?"
            grid_label = session.get("grid_from", "auto")
            print(f"  [Race {session['id']}] {file.name} | grid: {grid_label} | {laps} laps | {contacts} contacts | Winner: {winner}")

    print()

    if dry_run:
        print("Dry run — no output written.")
        return

    merge_into_output(output, season, venue, venue_order, qual_outputs or None, race_outputs)
    print(f"Output: {output}")


def cmd_batch(parent: Path, output: Path, dry_run: bool) -> None:
    if not parent.exists():
        print(f"Error: {parent} does not exist.", file=sys.stderr)
        sys.exit(1)

    venue_folders = sorted(
        [d for d in parent.iterdir() if d.is_dir() and (d / "venue.json").exists()]
    )

    if not venue_folders:
        print(f"No venue folders with venue.json found in {parent}/", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(venue_folders)} venue(s) to process\n")
    for folder in venue_folders:
        print(f"{'='*60}")
        cmd_process(folder, output, dry_run)
        print()

    print(f"{'='*60}")
    print(f"Done. {len(venue_folders)} venue(s) processed.")


def _discover_result_files(folder: Path) -> list[Path]:
    """Find AC server result JSONs in a folder, sorted chronologically by filename."""
    files = [f for f in folder.glob("*.json") if f.name != "venue.json"]
    files.sort(key=lambda f: f.name)
    return files


def _classify_session(data: dict) -> str:
    """Classify a result file by its internal Type field.

    Authoritative and version-independent — unlike trusting filename order,
    which silently misassigns when an abandoned restart file is left in place.
    """
    type_value = str(data.get("Type", "")).strip().upper()
    if type_value.startswith("QUAL"):
        return "qualifying"
    if type_value.startswith("RACE"):
        return "race"
    if type_value.startswith("PRAC"):
        return "practice"
    return "unknown"


def _prepare_sessions(
    folder: Path, sessions: list[dict]
) -> tuple[dict[str, Path], dict[str, dict], list[Path], list[str]]:
    """Classify every result file and map it to the correct session slot.

    Returns (file_map, data_map, dropped_files, errors). Files are matched to
    sessions by their internal Type, in chronological order within each type,
    so qualifying files always land in qualifying slots and races in race
    slots regardless of how many files (or stray restarts) are present.
    """
    files = _discover_result_files(folder)
    buckets: dict[str, list[tuple[Path, dict]]] = {
        "qualifying": [], "race": [], "practice": [], "unknown": [],
    }
    for f in files:
        try:
            data = load_server_result(f)
        except (ValueError, OSError):
            buckets["unknown"].append((f, {}))
            continue
        buckets[_classify_session(data)].append((f, data))

    expected = {
        "qualifying": [s for s in sessions if s["type"] == "qualifying"],
        "race": [s for s in sessions if s["type"] == "race"],
    }

    errors: list[str] = []
    for kind in ("qualifying", "race"):
        found = len(buckets[kind])
        want = len(expected[kind])
        if found != want:
            names = ", ".join(f.name for f, _ in buckets[kind]) or "none"
            errors.append(
                f"expected {want} {kind} file(s) but found {found} ({names})"
            )

    file_map: dict[str, Path] = {}
    data_map: dict[str, dict] = {}
    if not errors:
        for kind in ("qualifying", "race"):
            for session, (f, data) in zip(expected[kind], buckets[kind]):
                file_map[session["id"]] = f
                data_map[session["id"]] = data

    dropped = [f for f, _ in buckets["practice"]] + [f for f, _ in buckets["unknown"]]
    return file_map, data_map, dropped, errors


def _resolve_grid(
    session: dict,
    qual_outputs: dict[str, dict],
    race_outputs: dict[str, dict],
) -> list[str] | None:
    grid_from = session.get("grid_from", "")

    if grid_from.startswith("reverse_"):
        source_race_id = grid_from.replace("reverse_", "")
        source_race = race_outputs.get(source_race_id)
        if source_race and source_race["result"]:
            return [r.get("driverKey", r["driver"]) for r in reversed(source_race["result"])]
        return None

    if grid_from in qual_outputs:
        return qual_outputs[grid_from]["grid"]

    return None


if __name__ == "__main__":
    main()
