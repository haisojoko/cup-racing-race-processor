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


def _rich_race(rows):
    """A race carrying positions/sectors/laps so the new stats can fire.

    rows: (driver, finish_pos, positions[list], laps[list], sectors[list[list]]).
    """
    result, grid, laps_map, drivers, positions, sectors = [], [], {}, {}, {}, {}
    for driver, pos, postrace, laps, secs in rows:
        result.append({"driver": driver, "driverKey": driver, "position": pos,
                       "totalTimeMs": sum(laps), "laps": len(laps),
                       "bestLapMs": min(laps)})
        grid.append(driver)
        laps_map[driver] = laps
        positions[driver] = postrace
        sectors[driver] = secs
        drivers[driver] = {"driver": driver, "car": "F1"}
    return {"grid": grid, "gridConfidence": "high", "positionChanges": None,
            "drivers": drivers, "result": result, "overtakes": [], "contacts": [],
            "cuts": {}, "laps": laps_map, "positions": positions, "sectors": sectors}


def _rich_season():
    # Winner up front; Dan is a midfielder (P5) who charges late, sets his best
    # lap on the final lap, strings a tight streak, and owns the pack's fastest
    # lap + Sector 1. Eve is a slower pack car for the crowns to beat.
    fast = [80_000, 80_100, 80_050, 80_000, 79_900, 79_800]   # improving, best last
    steady = [80_060, 80_050, 80_055, 80_050, 80_040]         # tight streak, best last
    slow = [90_000, 90_500, 90_200, 90_800, 90_100, 90_600]
    sec_fast = [[26_000, 27_000, 27_000]] * 6
    sec_slow = [[30_000, 30_000, 30_000]] * 6
    dan = ("Dan", 5, [8, 8, 7, 6, 5], steady, [[26_000, 27_000, 27_000]] * 5)
    r1 = _rich_race([
        ("Win", 1, [1, 1, 1, 1, 1, 1], fast, sec_fast),
        dan,
        ("Eve", 6, [6, 6, 6, 7, 7, 6], slow, sec_slow),
    ])
    return {"schemaVersion": 2, "season": "S23", "events": [
        {"eventId": "e1", "venue": "Testville", "venueOrder": 1,
         "date": "2026-01-01", "races": {"1": r1}}]}


def test_new_stats_fire_for_a_midfielder(tmp_path):
    ds = _write_dataset(tmp_path, _rich_season())
    out = tmp_path / "out"
    recap.generate(ds, out, season="S23")
    dan = (out / "S23" / "testville" / "dan.md").read_text()
    assert "Late-race charge" in dan
    assert "Pushing to the flag" in dan
    assert "laps inside" in dan
    assert "Fastest lap of anyone in the midfield" in dan
    assert "Fastest through Sector 1" in dan


def test_places_gained_suppressed_on_reverse_grid(tmp_path):
    # A race where a back-starter nets +4 places with a trusted grid.
    laps = [80_000] * 6
    race = _rich_race([
        ("Front", 3, [3, 3, 3, 3, 3, 3], laps, [[26_000]] * 6),
        ("Climber", 1, [5, 5, 4, 3, 2, 1], laps, [[26_000]] * 6),
    ])
    race["positionChanges"] = {"Climber": {"gained": 4, "lost": 0, "net": 4},
                               "Front": {"gained": 0, "lost": 0, "net": 0}}
    season = {"schemaVersion": 2, "season": "S23", "events": [
        {"eventId": "e1", "venue": "Testville", "venueOrder": 1,
         "date": "2026-01-01", "races": {"1": race}}]}
    ds = _write_dataset(tmp_path, season)

    # normal grid: places-gained is a valid story
    recap.generate(ds, tmp_path / "normal", season="S23")
    assert "Net +4 places" in (tmp_path / "normal" / "S23" / "testville" / "climber.md").read_text()

    # reverse grid: it's misleading, so it's dropped (late-race charge stands in)
    recap.generate(ds, tmp_path / "rev", season="S23", reverse_grid_seasons=["S23"])
    climber = (tmp_path / "rev" / "S23" / "testville" / "climber.md").read_text()
    assert "Net +" not in climber
    assert "Late-race charge" in climber
