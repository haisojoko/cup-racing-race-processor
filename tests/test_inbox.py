"""Tests for inbox scanning, filename parsing, and dedup."""

import json
from pathlib import Path

from race_processor.inbox import parse_filename_timestamp, scan_season


def _laps(n: int, track="imola", start_ts=100000) -> list[dict]:
    return [
        {"DriverName": f"D{i%3}", "DriverGuid": f"g{i%3}", "CarId": i % 3,
         "CarModel": "car", "LapTime": 90000 + i, "Sectors": [30000, 30000, 30000],
         "Timestamp": start_ts + i * 90000}
        for i in range(n)
    ]


def _session(track="imola", type_="RACE", n_laps=10, race_laps=0) -> dict:
    return {
        "TrackName": track, "TrackConfig": "", "Type": type_,
        "RaceLaps": race_laps, "Cars": [], "Laps": _laps(n_laps), "Result": [], "Events": [],
    }


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_parse_filename_timestamp_non_padded():
    ts = parse_filename_timestamp("2026_5_26_22_0_QUALIFY.json")
    assert ts is not None
    assert (ts.year, ts.month, ts.day, ts.hour, ts.minute) == (2026, 5, 26, 22, 0)


def test_parse_filename_timestamp_bad_returns_none():
    assert parse_filename_timestamp("garbage.json") is None


def test_byte_identical_dropped_keeps_earliest(tmp_path):
    data = _session()
    _write(tmp_path / "2026_1_1_20_0_RACE.json", data)
    _write(tmp_path / "2026_1_1_20_5_RACE.json", data)  # identical bytes
    scan = scan_season(tmp_path)
    assert len(scan.sessions) == 1
    assert scan.sessions[0].name == "2026_1_1_20_0_RACE.json"
    assert scan.dropped[0]["reason"] == "byte-identical"


def test_back_to_back_complete_races_both_kept(tmp_path):
    # Real-spacing regression: two full races ~11 min apart, similar lap counts.
    _write(tmp_path / "2026_1_1_21_35_RACE.json", _session(n_laps=114))
    _write(tmp_path / "2026_1_1_21_46_RACE.json", _session(n_laps=99))
    scan = scan_season(tmp_path)
    assert len(scan.sessions) == 2
    assert not scan.dropped


def test_aborted_restart_dropped(tmp_path):
    # A 4-lap aborted start right before a 99-lap race → dropped.
    _write(tmp_path / "2026_1_1_21_30_RACE.json", _session(n_laps=4))
    _write(tmp_path / "2026_1_1_21_40_RACE.json", _session(n_laps=99))
    scan = scan_season(tmp_path)
    assert len(scan.sessions) == 1
    assert scan.sessions[0].name == "2026_1_1_21_40_RACE.json"
    assert scan.dropped[0]["reason"] == "aborted-restart"


def test_finished_race_not_treated_as_restart(tmp_path):
    # Earlier race leader completed the distance → never a restart victim,
    # even if a later short session sits within the window.
    _write(tmp_path / "2026_1_1_21_30_RACE.json", _session(n_laps=60, race_laps=6))
    _write(tmp_path / "2026_1_1_21_45_RACE.json", _session(n_laps=4))
    scan = scan_season(tmp_path)
    names = {s.name for s in scan.sessions}
    assert "2026_1_1_21_30_RACE.json" in names


def test_unreadable_and_unrecognized_surfaced(tmp_path):
    (tmp_path / "2026_1_1_20_0_RACE.json").write_text("{bad json", encoding="utf-8")
    _write(tmp_path / "2026_1_1_20_5_RACE.json", {"foo": "bar"})  # no Type/Laps
    scan = scan_season(tmp_path)
    reasons = {u["reason"] for u in scan.unprocessed}
    assert reasons == {"unreadable", "unrecognized"}
    assert not scan.sessions
