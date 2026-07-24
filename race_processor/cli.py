"""CLI entry point for the race processor.

Workflow: drop AC server result JSONs into inbox/<Season>/ folders, then run
``race-processor ingest``. It scans every season folder, groups sessions into
events, infers grids, writes an additive per-season dataset, and publishes it
to the configured consumer repos.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import config as config_mod
from . import dataset as ds
from . import processor
from .events import group_events
from .inbox import scan_season

SEASON_RANGE = range(1, 25)  # base seasons S1..S24 created up front
_SEASON_RE = re.compile(r"^S(\d+)([a-z]?)$", re.IGNORECASE)


def _ensure_season_folders(inbox_dir: Path) -> None:
    """Create base SN folders, but never alongside a split variant.

    If the user has made split folders for a season (e.g. S18a/S18b, or
    S24a/S24b), the plain SN folder is intentionally omitted — creating an
    empty SN next to the splits would just be clutter.
    """
    existing_numbers = set()
    if inbox_dir.exists():
        for d in inbox_dir.iterdir():
            m = _SEASON_RE.match(d.name)
            if d.is_dir() and m and m.group(2):  # a suffixed (split) folder
                existing_numbers.add(int(m.group(1)))
    for n in SEASON_RANGE:
        if n in existing_numbers:
            continue
        (inbox_dir / f"S{n}").mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="race-processor",
        description="Process Assetto Corsa race results into the Cup Racing dataset.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config.json")
    sub = parser.add_subparsers(dest="command")

    ing = sub.add_parser("ingest", help="Scan inbox, build dataset, publish (the main command)")
    ing.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    ing.add_argument("--no-publish", action="store_true", help="Build dataset but don't publish")

    reb = sub.add_parser("rebuild", help="Rebuild every season from scratch (drops stale events)")
    reb.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    reb.add_argument("--no-publish", action="store_true", help="Build dataset but don't publish")

    sub.add_parser("publish", help="Copy the dataset to configured destinations only")

    ros = sub.add_parser("roster", help="List every driver GUID + names seen (to fill in driverNames)")
    ros.add_argument("-o", "--output", type=Path, default=None,
                     help="Write a driver_roster.json template (default: print a summary)")

    args = parser.parse_args(argv)

    if args.command == "ingest":
        _run_build(args.config, dry_run=args.dry_run, no_publish=args.no_publish, rebuild=False)
    elif args.command == "rebuild":
        _run_build(args.config, dry_run=args.dry_run, no_publish=args.no_publish, rebuild=True)
    elif args.command == "publish":
        _run_publish(args.config)
    elif args.command == "roster":
        _run_roster(args.config, args.output)
    else:
        parser.print_help()


def _resolve_root(config_path: Path | None) -> Path:
    if config_path is not None:
        return config_path.parent.resolve()
    return Path.cwd()


def _load_config(config_path: Path | None):
    if config_path is not None:
        if not config_path.exists():
            config_mod.write_default_config(config_path)
            print(f"Created {config_path}")
        return config_mod.load_config(config_path)
    return config_mod.load_or_create_config(Path.cwd())


def _season_dirs(inbox_dir: Path) -> list[Path]:
    if not inbox_dir.exists():
        return []
    return sorted(
        (d for d in inbox_dir.iterdir() if d.is_dir() and _SEASON_RE.match(d.name)),
        key=lambda d: _season_sort_key(d.name),
    )


def _season_sort_key(name: str) -> tuple[int, str]:
    m = _SEASON_RE.match(name)
    return (int(m.group(1)), m.group(2).lower()) if m else (9999, name)


def _run_build(config_path: Path | None, *, dry_run: bool, no_publish: bool, rebuild: bool) -> None:
    cfg = _load_config(config_path)
    processor.configure_name_map(cfg.driver_names)
    processor.configure_aliases(cfg.driver_aliases)
    processor.configure_shared_guids(cfg.shared_guids)
    fingerprint = cfg.fingerprint()

    if not dry_run:
        _ensure_season_folders(cfg.inbox_dir)

    mode = "REBUILD" if rebuild else "INGEST"
    print(f"[{mode}] inbox={cfg.inbox_dir}  dataset={cfg.dataset_dir}")
    if dry_run:
        print("(dry run — nothing will be written)")

    season_ids = {d.name for d in _season_dirs(cfg.inbox_dir)}
    seasons_out_dir = cfg.dataset_dir / "seasons"
    if seasons_out_dir.exists():
        season_ids.update(p.stem for p in seasons_out_dir.glob("*.json"))

    any_written = False
    total_events = 0
    for season_id in sorted(season_ids, key=_season_sort_key):
        folder = cfg.inbox_dir / season_id
        scan = scan_season(folder, cfg.restart_window_minutes) if folder.exists() else _empty_scan()

        for drop in scan.dropped:
            print(f"  WARNING [{season_id}] dropped {drop['file']}: {drop['reason']} — {drop['detail']}")
        for un in scan.unprocessed:
            print(f"  WARNING [{season_id}] unprocessed {un['file']}: {un['reason']} — {un['detail']}")

        current_events = group_events(scan.sessions, cfg.event_gap_hours, cfg.track_display_names)
        existing = ds.load_season(cfg.dataset_dir, season_id)
        existing_by_id = {e["eventId"]: e for e in (existing or {}).get("events", [])}

        event_objs: list[dict] = []
        current_ids: set[str] = set()
        rebuilt = reused = 0
        for ev in current_events:
            current_ids.add(ev.event_id)
            sig = ds.event_signature(ev, fingerprint)
            old = existing_by_id.get(ev.event_id)
            if not rebuild and old is not None and old.get("signature") == sig:
                event_objs.append(old)
                reused += 1
            else:
                event_objs.append(ds.build_event(ev, fingerprint, scan.dropped))
                rebuilt += 1

        if not rebuild:
            # Ingest never deletes: keep on-disk events with no current inbox files.
            for eid, old in existing_by_id.items():
                if eid not in current_ids:
                    event_objs.append(old)

        if not event_objs and existing is None:
            continue

        total_events += len(event_objs)
        season_data = ds.assemble_season(season_id, event_objs, scan.unprocessed)
        verb = "would write" if dry_run else "wrote"
        for e in season_data["events"]:
            races = len(e.get("races", {}))
            quals = len(e.get("qualifying", {}))
            print(f"  [{season_id}] {e['venueOrder']}. {e['venue']} ({e['date']}) — "
                  f"{quals} qual, {races} race(s)")

        if not dry_run:
            wrote = ds.write_json_if_changed(ds.season_path(cfg.dataset_dir, season_id), season_data)
            any_written = any_written or wrote
            status = "updated" if wrote else "unchanged"
            print(f"  [{season_id}] {status}: {rebuilt} rebuilt, {reused} reused")
        else:
            print(f"  [{season_id}] {verb}: {rebuilt} to build, {reused} unchanged")

    if not dry_run:
        ds.sync_schema_doc(cfg.dataset_dir)
        index = ds.build_index(cfg.dataset_dir)
        idx_written = ds.write_json_if_changed(cfg.dataset_dir / "index.json", index)
        any_written = any_written or idx_written
        print(f"Index: {'updated' if idx_written else 'unchanged'} "
              f"({len(index['seasons'])} season(s), {total_events} event(s))")

        if not no_publish and cfg.publish_destinations:
            written = ds.publish(cfg.dataset_dir, cfg.publish_destinations)
            for dest in written:
                print(f"Published -> {dest}")
    else:
        print(f"Dry run complete: {total_events} event(s) across {len(season_ids)} season(s).")


def _run_publish(config_path: Path | None) -> None:
    cfg = _load_config(config_path)
    if not cfg.dataset_dir.exists():
        print(f"Error: dataset dir {cfg.dataset_dir} does not exist. Run ingest first.", file=sys.stderr)
        sys.exit(1)
    if not cfg.publish_destinations:
        print("No publishDestinations configured in config.json.", file=sys.stderr)
        sys.exit(1)
    written = ds.publish(cfg.dataset_dir, cfg.publish_destinations)
    for dest in written:
        print(f"Published -> {dest}")
    if not written:
        print("Nothing published (all destinations were skipped).")


def _run_roster(config_path: Path | None, output: Path | None) -> None:
    """Collect every driver GUID and the display names it has used.

    This is the starting point for filling in config.json "driverNames"
    (GUID -> canonical league name). Existing mappings are shown/pre-filled so
    re-running only surfaces the people still needing a name.
    """
    import collections
    import json as _json
    from .inbox import parse_filename_timestamp

    cfg = _load_config(config_path)
    seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    seasons: dict[str, set] = collections.defaultdict(set)

    for folder in _season_dirs(cfg.inbox_dir):
        for f in sorted(folder.glob("*.json")):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            for coll, name_key, guid_key in (
                ("Cars", None, None), ("Laps", "DriverName", "DriverGuid"),
                ("Result", "DriverName", "DriverGuid"),
            ):
                for entry in (data.get(coll) or []):
                    if coll == "Cars":
                        drv = entry.get("Driver") or {}
                        name, guid = drv.get("Name", ""), drv.get("Guid", "")
                    else:
                        name, guid = entry.get(name_key, ""), entry.get(guid_key, "")
                    guid = str(guid).strip()
                    name = str(name).strip()
                    if guid and name:
                        seen[guid][name] += 1
                        seasons[guid].add(folder.name)

    rows = []
    for guid, names in seen.items():
        primary = names.most_common(1)[0][0]
        rows.append({
            "guid": guid,
            "canonicalName": cfg.driver_names.get(guid, ""),
            "namesSeen": [n for n, _ in names.most_common()],
            "suggested": primary,
            "seasons": sorted(seasons[guid], key=_season_sort_key),
        })
    rows.sort(key=lambda r: (r["canonicalName"] == "", -sum(seen[r["guid"]].values())))

    mapped = sum(1 for r in rows if r["canonicalName"])
    print(f"{len(rows)} distinct driver GUIDs — {mapped} already mapped, {len(rows) - mapped} unmapped.")

    if output is None:
        for r in rows:
            status = r["canonicalName"] or "(unmapped)"
            alt = "" if len(r["namesSeen"]) == 1 else f"  aka {r['namesSeen'][1:]}"
            print(f"  {r['guid']}  {status:20} <- {r['suggested']}{alt}")
        print('\nRe-run with `-o driver_roster.json` to write an editable template, '
              'then paste GUID -> name pairs into config.json "driverNames".')
        return

    template = {
        "_help": "Fill canonicalName for each driver, then copy the guid->name "
                 "pairs into config.json \"driverNames\". Re-run ingest afterwards.",
        "drivers": rows,
    }
    output.write_text(_json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output} ({len(rows)} drivers).")


def _empty_scan():
    from .inbox import ScanResult
    return ScanResult(sessions=[], dropped=[], unprocessed=[])


if __name__ == "__main__":
    main()
