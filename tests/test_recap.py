"""Tests for the post-round recap card generator."""

import json

from race_processor import recap


def _race(order_times: list[tuple[str, int]], laps: int = 10) -> dict:
    """A race from (driver, totalTimeMs) pairs in finishing order."""
    result, grid, laps_map, drivers = [], [], {}, {}
    for pos, (label, total) in enumerate(order_times, 1):
        result.append({"driver": label, "driverKey": label, "position": pos,
                       "totalTimeMs": total, "laps": laps, "bestLapMs": 60_000})
        grid.append(label)
        laps_map[label] = [60_000] * laps
        drivers[label] = {"driver": label, "car": "F1"}
    return {"grid": grid, "gridConfidence": "high", "positionChanges": None,
            "drivers": drivers, "result": result, "overtakes": [], "contacts": [],
            "cuts": {}, "laps": laps_map}


def _season() -> dict:
    # Alpha wins both races (a sweep). Bravo holds Charlie off by 0.059s in race 2.
    r1 = _race([("Alpha", 100_000), ("Bravo", 105_000), ("Charlie", 106_000)])
    r2 = _race([("Alpha", 100_000), ("Bravo", 105_000), ("Charlie", 105_059)])
    return {"schemaVersion": 2, "season": "S23", "events": [
        {"eventId": "e1", "venue": "Testville", "venueOrder": 1,
         "date": "2026-01-01", "races": {"1": r1, "2": r2}}]}


def _write_dataset(tmp_path, season_data, sid="S23"):
    (tmp_path / "seasons").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seasons" / f"{sid}.json").write_text(
        json.dumps(season_data), encoding="utf-8")
    return tmp_path


def test_generate_writes_a_card_per_driver(tmp_path):
    ds = _write_dataset(tmp_path, _season())
    out = tmp_path / "out"
    info = recap.generate(ds, out, season="S23")

    assert info["drivers"] == 3
    folder = out / "S23" / "testville"
    assert (folder / "_everyone.md").exists()
    assert (folder / "_midfield.md").exists()
    for driver in ("alpha", "bravo", "charlie"):
        assert (folder / f"{driver}.md").exists()


def test_winner_headlines_the_sweep_not_places_gained(tmp_path):
    ds = _write_dataset(tmp_path, _season())
    out = tmp_path / "out"
    recap.generate(ds, out, season="S23")
    assert "Clean sweep" in (out / "S23" / "testville" / "alpha.md").read_text()


def test_photo_finish_is_direction_aware(tmp_path):
    ds = _write_dataset(tmp_path, _season())
    out = tmp_path / "out"
    recap.generate(ds, out, season="S23")
    folder = out / "S23" / "testville"

    charlie = (folder / "charlie.md").read_text()
    assert "Photo finish: 0.059s behind Bravo" in charlie   # Charlie lost the scrap

    bravo = (folder / "bravo.md").read_text()
    assert "Held off Charlie by 0.059s" in bravo             # Bravo won it — never "behind"


def test_every_participant_gets_a_headline(tmp_path):
    """The floor guarantee: nobody is left without a positive line."""
    ds = _write_dataset(tmp_path, _season())
    info = recap.generate(ds, tmp_path / "out", season="S23")
    assert info["summary"]
    assert all(headline.strip() for _, headline in info["summary"])


def _mc_race(rows, laps_by_driver=None):
    """A multi-class race from (driver, class, overall_pos, class_pos, total_ms)."""
    result, drivers, laps_map = [], {}, {}
    for driver, cls, pos, cpos, total in rows:
        result.append({"driver": driver, "driverKey": driver, "position": pos,
                       "classPosition": cpos, "totalTimeMs": total, "laps": 8, "bestLapMs": 60_000})
        drivers[driver] = {"driver": driver, "car": "c", "class": cls}
        laps_map[driver] = (laps_by_driver or {}).get(driver, [60_000] * 8)
    grid = [r[0] for r in sorted(rows, key=lambda r: r[2])]
    return {"grid": grid, "gridConfidence": "high", "positionChanges": None,
            "drivers": drivers, "result": result, "overtakes": [], "contacts": [],
            "cuts": {}, "laps": laps_map}


def _mc_season():
    # Ada sweeps GT3; Bree is the GT3 runner-up in a photo finish; Cid (Street)
    # finishes overall BETWEEN them, to test that "nearest rival" stays in-class.
    rows = [("Ada", "GT3", 1, 1, 100_000),
            ("Cid", "Street", 2, 1, 100_400),
            ("Bree", "GT3", 3, 2, 100_450)]
    varied = {"Cid": [60_000, 66_000, 61_000, 70_000, 60_500, 64_000, 60_000, 63_000]}
    r1 = _mc_race(rows, varied)
    r2 = _mc_race(rows, varied)
    return {"schemaVersion": 2, "season": "S14",
            "classes": {"championship": "split", "order": ["GT3", "Street"]},
            "events": [{"eventId": "e1", "venue": "Testring", "venueOrder": 1,
                        "date": "2026-01-01", "races": {"1": r1, "2": r2}}]}


def test_multiclass_fidelity(tmp_path):
    ds = _write_dataset(tmp_path, _mc_season(), sid="S14")
    out = tmp_path / "out"
    recap.generate(ds, out, season="S14")
    folder = out / "S14" / "testring"

    ada = (folder / "ada.md").read_text()
    assert "GT3" in ada                       # class-aware win text
    assert "Career-first" not in ada          # no fabricated career milestones from incomplete data
    # nearest rival is the same-class car (Bree), not the overall-adjacent Street car (Cid)
    assert "Bree" in ada and "Cid" not in ada

    # consistency is shown for every driver, even a non-metronomic one, as a data line
    assert "Lap consistency:" in (folder / "cid.md").read_text()
