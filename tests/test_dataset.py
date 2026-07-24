"""Tests for dataset assembly, venueOrder, index, and publish guards."""

import json
from datetime import datetime, timezone
from pathlib import Path

from race_processor.dataset import (
    assemble_season,
    build_index,
    publish,
    write_json_if_changed,
)


def _event(event_id, date, venue="V"):
    return {
        "eventId": event_id, "signature": "x", "venue": venue, "date": date,
        "track": "imola", "trackConfig": "",
        "qualifying": {}, "races": {"1": {"drivers": {"A": {}}}},
        "practice": [], "provenance": {"sourceFiles": [], "droppedFiles": [], "notes": []},
    }


def test_venue_order_recomputed_by_date():
    events = [_event("later", "2026-05-10"), _event("earlier", "2026-01-01")]
    season = assemble_season("S1", events, [])
    ordered = season["events"]
    assert ordered[0]["eventId"] == "earlier"
    assert ordered[0]["venueOrder"] == 1
    assert ordered[1]["venueOrder"] == 2


def test_write_json_if_changed_skips_identical_content(tmp_path):
    path = tmp_path / "s.json"
    data = assemble_season("S1", [_event("e", "2026-01-01")], [])
    assert write_json_if_changed(path, data) is True
    # Re-assemble (new lastUpdated) but same content → no rewrite.
    data2 = assemble_season("S1", [_event("e", "2026-01-01")], [])
    assert write_json_if_changed(path, data2) is False


def test_build_index_summarizes_seasons(tmp_path):
    seasons_dir = tmp_path / "seasons"
    seasons_dir.mkdir()
    season = assemble_season("S1", [_event("e", "2026-01-01")], [])
    (seasons_dir / "S1.json").write_text(json.dumps(season), encoding="utf-8")

    index = build_index(tmp_path)
    assert "S1" in index["seasons"]
    entry = index["seasons"]["S1"]
    assert entry["file"] == "seasons/S1.json"
    assert entry["events"][0]["races"] == 1
    assert "A" in entry["drivers"]


def test_index_surfaces_dropped_and_notes(tmp_path):
    seasons_dir = tmp_path / "seasons"
    seasons_dir.mkdir()
    ev = _event("e", "2026-01-01")
    ev["provenance"]["droppedFiles"] = [{"file": "dup.json", "reason": "byte-identical"}]
    ev["provenance"]["notes"] = ["car 5 had multiple GUIDs"]
    season = assemble_season("S1", [ev], [{"file": "bad.json", "reason": "unreadable"}])
    (seasons_dir / "S1.json").write_text(json.dumps(season), encoding="utf-8")

    index = build_index(tmp_path)
    quality = index["seasons"]["S1"]["dataQuality"]
    assert any("dup.json" in q for q in quality)
    assert any("multiple GUIDs" in q for q in quality)
    assert any("bad.json" in q for q in quality)


def test_publish_refuses_foreign_nonempty_dir(tmp_path):
    dataset = tmp_path / "dataset"
    (dataset / "seasons").mkdir(parents=True)
    (dataset / "index.json").write_text("{}", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "important.txt").write_text("do not delete", encoding="utf-8")  # no index.json

    warnings: list[str] = []
    written = publish(dataset, [dest], warn=warnings.append)
    assert written == []
    assert (dest / "important.txt").exists()  # untouched
    assert warnings


def test_publish_mirrors_into_owned_dir(tmp_path):
    dataset = tmp_path / "dataset"
    (dataset / "seasons").mkdir(parents=True)
    (dataset / "index.json").write_text("{}", encoding="utf-8")
    (dataset / "seasons" / "S1.json").write_text("{}", encoding="utf-8")

    dest = tmp_path / "dest"
    written = publish(dataset, [dest], warn=lambda *_: None)
    assert written == [dest]
    assert (dest / "index.json").exists()
    assert (dest / "seasons" / "S1.json").exists()
