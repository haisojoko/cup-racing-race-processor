"""End-to-end CLI tests over a temporary inbox."""

import json
from pathlib import Path

from race_processor.cli import _ensure_season_folders, _season_dirs, _season_sort_key, main

from .fixtures import QUALIFYING_RESULT, RACE_RESULT


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_repo(tmp_path: Path, publish_dest: Path | None = None) -> Path:
    """Build a config.json + inbox tree; return the config path."""
    cfg = {
        "inboxDir": "inbox",
        "datasetDir": "dataset",
        "publishDestinations": [str(publish_dest)] if publish_dest else [],
        "driverAliases": {},
        "trackDisplayNames": {"imola|": "Imola"},
        "eventGapHours": 4,
        "restartWindowMinutes": 30,
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp_path / "config.json"


def _seed_event(inbox_season: Path) -> None:
    inbox_season.mkdir(parents=True, exist_ok=True)
    _write(inbox_season / "2026_1_10_20_0_QUALIFY.json", QUALIFYING_RESULT)
    _write(inbox_season / "2026_1_10_20_20_RACE.json", RACE_RESULT)


def test_ingest_end_to_end(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")
    # a byte-identical duplicate race and a corrupt file
    _write(tmp_path / "inbox" / "S20" / "2026_1_10_20_21_RACE.json", RACE_RESULT)
    (tmp_path / "inbox" / "S20" / "broken.json").write_text("{not json", encoding="utf-8")

    main(["--config", str(cfg_path), "ingest", "--no-publish"])

    season = json.loads((tmp_path / "dataset" / "seasons" / "S20.json").read_text(encoding="utf-8"))
    assert season["season"] == "S20"
    assert len(season["events"]) == 1
    event = season["events"][0]
    assert event["venue"] == "Imola"
    assert event["venueOrder"] == 1
    # duplicate race dropped, so exactly one race remains
    assert len(event["races"]) == 1
    assert any(d["reason"] == "byte-identical" for d in event["provenance"]["droppedFiles"])
    # corrupt file surfaced, not silently dropped
    assert any(u["reason"] == "unreadable" for u in season["unprocessed"])

    index = json.loads((tmp_path / "dataset" / "index.json").read_text(encoding="utf-8"))
    assert "S20" in index["seasons"]
    assert index["seasons"]["S20"]["events"][0]["races"] == 1


def test_ingest_idempotent(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")

    main(["--config", str(cfg_path), "ingest", "--no-publish"])
    season_file = tmp_path / "dataset" / "seasons" / "S20.json"
    first = season_file.read_text(encoding="utf-8")

    main(["--config", str(cfg_path), "ingest", "--no-publish"])
    second = season_file.read_text(encoding="utf-8")

    assert first == second  # unchanged content means no rewrite (lastUpdated preserved)


def test_dry_run_writes_nothing(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")

    main(["--config", str(cfg_path), "ingest", "--dry-run"])

    assert not (tmp_path / "dataset").exists()


def test_publish_mirrors_dataset(tmp_path):
    dest = tmp_path / "consumer" / "cup-dataset"
    cfg_path = _make_repo(tmp_path, publish_dest=dest)
    _seed_event(tmp_path / "inbox" / "S20")

    main(["--config", str(cfg_path), "ingest"])

    assert (dest / "index.json").exists()
    assert (dest / "seasons" / "S20.json").exists()


def test_config_fingerprint_triggers_reprocess(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")
    main(["--config", str(cfg_path), "ingest", "--no-publish"])

    # Change an alias → fingerprint changes → event reprocesses with new label.
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["driverAliases"] = {"Josie": "JosieRenamed"}
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    main(["--config", str(cfg_path), "ingest", "--no-publish"])
    season = json.loads((tmp_path / "dataset" / "seasons" / "S20.json").read_text(encoding="utf-8"))
    race = season["events"][0]["races"]["1"]
    assert "JosieRenamed" in race["drivers"]


def test_rebuild_drops_stale_events(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")
    main(["--config", str(cfg_path), "ingest", "--no-publish"])

    for f in (tmp_path / "inbox" / "S20").glob("*.json"):
        f.unlink()
    main(["--config", str(cfg_path), "rebuild", "--no-publish"])

    season = json.loads((tmp_path / "dataset" / "seasons" / "S20.json").read_text(encoding="utf-8"))
    assert season["events"] == []


def test_split_season_folders_ingested(tmp_path):
    # S18a and S18b are independent seasons; both must produce their own file.
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S18a")
    _seed_event(tmp_path / "inbox" / "S18b")

    main(["--config", str(cfg_path), "ingest", "--no-publish"])

    assert (tmp_path / "dataset" / "seasons" / "S18a.json").exists()
    assert (tmp_path / "dataset" / "seasons" / "S18b.json").exists()

    index = json.loads((tmp_path / "dataset" / "index.json").read_text(encoding="utf-8"))
    assert "S18a" in index["seasons"] and "S18b" in index["seasons"]
    # S18 base is NOT auto-created when split folders exist.
    assert not (tmp_path / "inbox" / "S18").exists()


def test_ensure_season_folders_respects_splits(tmp_path):
    inbox = tmp_path / "inbox"
    # Pre-create split folders for 18 and 24 (the real + future cases).
    (inbox / "S18a").mkdir(parents=True)
    (inbox / "S24b").mkdir(parents=True)

    _ensure_season_folders(inbox)

    assert (inbox / "S1").exists()         # normal base seasons created
    assert (inbox / "S23").exists()
    assert not (inbox / "S18").exists()    # split present → base skipped
    assert not (inbox / "S24").exists()
    assert (inbox / "S18a").exists() and (inbox / "S24b").exists()


def test_season_sort_orders_splits_between_neighbours():
    names = ["S19", "S2", "S18b", "S18a", "S18", "S10", "S9"]
    ordered = sorted(names, key=_season_sort_key)
    assert ordered == ["S2", "S9", "S10", "S18", "S18a", "S18b", "S19"]


def test_driver_names_map_applied_in_ingest(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")
    # Give the fixture drivers GUIDs so the GUID map can target them.
    for name in ["2026_1_10_20_0_QUALIFY.json", "2026_1_10_20_20_RACE.json"]:
        p = tmp_path / "inbox" / "S20" / name
        data = json.loads(p.read_text(encoding="utf-8"))
        for coll in ("Laps", "Result"):
            for e in data.get(coll, []):
                if e.get("DriverName") == "Josie":
                    e["DriverGuid"] = "guid-josie"
        p.write_text(json.dumps(data), encoding="utf-8")

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["driverNames"] = {"guid-josie": "Josephine"}
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    main(["--config", str(cfg_path), "ingest", "--no-publish"])
    season = json.loads((tmp_path / "dataset" / "seasons" / "S20.json").read_text(encoding="utf-8"))
    race = season["events"][0]["races"]["1"]
    assert "Josephine" in race["drivers"]
    assert "Josie" not in race["drivers"]


def test_roster_writes_template(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")
    for name in ["2026_1_10_20_0_QUALIFY.json", "2026_1_10_20_20_RACE.json"]:
        p = tmp_path / "inbox" / "S20" / name
        data = json.loads(p.read_text(encoding="utf-8"))
        for coll in ("Laps", "Result"):
            for e in data.get(coll, []):
                e["DriverGuid"] = "guid-" + e["DriverName"].lower()
        p.write_text(json.dumps(data), encoding="utf-8")

    out = tmp_path / "roster.json"
    main(["--config", str(cfg_path), "roster", "-o", str(out)])

    roster = json.loads(out.read_text(encoding="utf-8"))
    guids = {r["guid"] for r in roster["drivers"]}
    assert "guid-josie" in guids
    josie = next(r for r in roster["drivers"] if r["guid"] == "guid-josie")
    assert josie["suggested"] == "Josie"
    assert "S20" in josie["seasons"]


def test_ingest_preserves_events_when_files_removed(tmp_path):
    cfg_path = _make_repo(tmp_path)
    _seed_event(tmp_path / "inbox" / "S20")
    main(["--config", str(cfg_path), "ingest", "--no-publish"])

    for f in (tmp_path / "inbox" / "S20").glob("*.json"):
        f.unlink()
    main(["--config", str(cfg_path), "ingest", "--no-publish"])

    season = json.loads((tmp_path / "dataset" / "seasons" / "S20.json").read_text(encoding="utf-8"))
    assert len(season["events"]) == 1  # ingest never deletes
