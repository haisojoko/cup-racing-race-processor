"""Tests for the race processor core logic."""

import copy
import json
import tempfile
from pathlib import Path

import race_processor.processor as proc_module
from race_processor.processor import (
    extract_driver_name,
    resolve_driver_name,
    build_grid_from_qualifying,
    build_grid_from_first_lap,
    process_qualifying,
    process_race,
    merge_into_output,
    _clean_laps,
    _compute_positions,
    _compute_position_details,
    _compute_position_changes,
)

from .fixtures import QUALIFYING_RESULT, RACE_RESULT


# ---- Driver name extraction ----

def test_extract_driver_name_dict():
    assert extract_driver_name({"Driver": {"Name": "Josie"}}) == "Josie"

def test_extract_driver_name_string():
    assert extract_driver_name({"DriverName": "Toby"}) == "Toby"

def test_extract_driver_name_fallback():
    assert extract_driver_name({"Driver": "Lee"}) == "Lee"

def test_extract_driver_name_strips_whitespace():
    assert extract_driver_name({"DriverName": "  Josie  "}) == "Josie"


# ---- Grid reconstruction ----

def test_qualifying_grid_order():
    grid = build_grid_from_qualifying(QUALIFYING_RESULT)
    assert grid == ["Josie", "Toby", "Lee"]

def test_first_lap_grid_fallback():
    grid = build_grid_from_first_lap(RACE_RESULT)
    assert grid[0] == "Josie"
    assert len(grid) == 3


# ---- Qualifying processing ----

def test_process_qualifying():
    result = process_qualifying(QUALIFYING_RESULT)
    assert result["grid"] == ["Josie", "Toby", "Lee"]
    assert result["times"]["Josie"]["bestMs"] == 96800
    assert len(result["times"]["Josie"]["laps"]) == 2
    assert result["times"]["Toby"]["bestMs"] == 97100

def test_process_qualifying_duplicate_names_are_separate_identities():
    qual = {
        "TrackName": "imola",
        "TrackConfig": "",
        "Type": "QUALIFY",
        "Laps": [
            {"DriverName": "Alex", "DriverGuid": "guid-a", "CarId": 1, "CarModel": "car_a", "LapTime": 100000, "Sectors": [33000, 33000, 34000], "Timestamp": 100000},
            {"DriverName": "Alex", "DriverGuid": "guid-b", "CarId": 2, "CarModel": "car_b", "LapTime": 99000, "Sectors": [32000, 33000, 34000], "Timestamp": 99000},
        ],
        "Result": [
            {"DriverName": "Alex", "DriverGuid": "guid-b", "CarId": 2, "CarModel": "car_b", "BestLap": 99000, "TotalTime": 99000, "BallastKG": 0, "Restrictor": 0},
            {"DriverName": "Alex", "DriverGuid": "guid-a", "CarId": 1, "CarModel": "car_a", "BestLap": 100000, "TotalTime": 100000, "BallastKG": 0, "Restrictor": 0},
        ],
        "Events": [],
    }

    result = process_qualifying(qual)

    assert result["grid"] == ["Alex (car 2)", "Alex"]
    assert sorted(result["times"]) == ["Alex", "Alex (car 2)"]
    assert result["drivers"]["Alex"]["guid"] == "guid-a"
    assert result["drivers"]["Alex (car 2)"]["guid"] == "guid-b"


# ---- Race processing ----

def test_process_race_basic():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    assert result["grid"] == ["Josie", "Toby", "Lee"]
    assert "Josie" in result["laps"]
    assert len(result["laps"]["Josie"]) == 4

def test_process_race_positions():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    positions = result["positions"]
    assert positions["Josie"][0] == 1
    assert positions["Lee"][-1] == 3
    assert len(positions["Josie"]) == 4

def test_process_race_sectors():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    assert len(result["sectors"]["Josie"]) == 4
    assert len(result["sectors"]["Josie"][0]) == 3

def test_process_race_contacts():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    contacts = result["contacts"]
    assert len(contacts) == 1
    assert contacts[0]["lap"] == 4
    assert contacts[0]["lapConfidence"] == "timestamp"
    assert contacts[0]["driver1"] == "Josie"
    assert contacts[0]["driver2"] == "Toby"
    assert contacts[0]["impactSpeed"] == 14.2

def test_process_race_contact_without_timestamp_is_unknown_lap():
    race = copy.deepcopy(RACE_RESULT)
    del race["Events"][0]["Timestamp"]

    result = process_race(race, grid=["Josie", "Toby", "Lee"])
    contact = result["contacts"][0]

    assert contact["lap"] is None
    assert contact["lapConfidence"] == "unknown"

def test_process_race_env_collisions_excluded():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    for c in result["contacts"]:
        assert c["driver1"] != "Lee" or c["driver2"] != ""

def test_process_race_result():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    assert result["result"][0]["driver"] == "Josie"
    assert result["result"][0]["bestLapMs"] == 97100

def test_process_race_pace():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    pace = result["pace"]
    assert "Josie" in pace
    assert pace["Josie"]["bestMs"] > 0
    assert pace["Josie"]["lapsUsed"] <= 3

def test_process_race_position_changes():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    changes = result["positionChanges"]
    for driver in ["Josie", "Toby", "Lee"]:
        assert "gained" in changes[driver]
        assert "lost" in changes[driver]
        assert "net" in changes[driver]
        assert changes[driver]["net"] == changes[driver]["gained"] - changes[driver]["lost"]


# ---- Result filtering and driver identity ----

def test_process_race_skips_named_no_lap_placeholder():
    race = copy.deepcopy(RACE_RESULT)
    race["Result"].insert(0, {
        "DriverName": "Ghost",
        "DriverGuid": "ghost-guid",
        "CarId": 99,
        "CarModel": "tatuus_fa01",
        "BestLap": 999999999,
        "TotalTime": 0,
        "BallastKG": 0,
        "Restrictor": 0,
    })

    result = process_race(race, grid=["Josie", "Toby", "Lee"])

    assert [r["driver"] for r in result["result"]] == ["Josie", "Toby", "Lee"]
    assert "Ghost" not in result["drivers"]
    assert "Ghost" not in result["laps"]

def test_process_race_keeps_dnf_with_real_laps():
    race = copy.deepcopy(RACE_RESULT)
    race["Result"][1]["BestLap"] = 999999999
    race["Result"][1]["TotalTime"] = 0

    result = process_race(race, grid=["Josie", "Toby", "Lee"])
    toby = next(r for r in result["result"] if r["driver"] == "Toby")

    assert toby["bestLapMs"] == 0
    assert toby["totalTimeMs"] == 0
    assert toby["laps"] == 4

def test_process_race_duplicate_names_are_separate_identities():
    race = {
        "TrackName": "imola",
        "TrackConfig": "",
        "Type": "RACE",
        "Laps": [
            {"DriverName": "Alex", "DriverGuid": "guid-a", "CarId": 1, "CarModel": "car_a", "LapTime": 100000, "Sectors": [33000, 33000, 34000], "Timestamp": 100000},
            {"DriverName": "Alex", "DriverGuid": "guid-b", "CarId": 2, "CarModel": "car_b", "LapTime": 101000, "Sectors": [33000, 34000, 34000], "Timestamp": 101000},
        ],
        "Result": [
            {"DriverName": "Alex", "DriverGuid": "guid-a", "CarId": 1, "CarModel": "car_a", "BestLap": 100000, "TotalTime": 100000, "BallastKG": 0, "Restrictor": 0},
            {"DriverName": "Alex", "DriverGuid": "guid-b", "CarId": 2, "CarModel": "car_b", "BestLap": 101000, "TotalTime": 101000, "BallastKG": 0, "Restrictor": 0},
        ],
        "Events": [],
    }

    result = process_race(race)

    assert result["grid"] == ["Alex", "Alex (car 2)"]
    assert sorted(result["laps"]) == ["Alex", "Alex (car 2)"]
    assert result["result"][0]["driver"] == "Alex"
    assert result["result"][0]["driverKey"] == "Alex"
    assert result["result"][1]["driver"] == "Alex"
    assert result["result"][1]["driverKey"] == "Alex (car 2)"
    assert result["drivers"]["Alex"]["guid"] == "guid-a"
    assert result["drivers"]["Alex (car 2)"]["guid"] == "guid-b"

def test_placeholder_does_not_steal_base_driver_label():
    race = {
        "TrackName": "spa",
        "TrackConfig": "",
        "Type": "RACE",
        "Laps": [
            {"DriverName": "NoaH", "DriverGuid": "same-guid", "CarId": 22, "CarModel": "ks_lamborghini", "LapTime": 180000, "Sectors": [50000, 80000, 50000], "Timestamp": 180000},
            {"DriverName": "NoaH", "DriverGuid": "same-guid", "CarId": 22, "CarModel": "ks_lamborghini", "LapTime": 175000, "Sectors": [49000, 79000, 47000], "Timestamp": 355000},
        ],
        "Result": [
            {"DriverName": "NoaH", "DriverGuid": "same-guid", "CarId": 0, "CarModel": "mercedes_sls_gt3", "BestLap": 999999999, "TotalTime": 0, "BallastKG": 0, "Restrictor": 0},
            {"DriverName": "NoaH", "DriverGuid": "same-guid", "CarId": 22, "CarModel": "ks_lamborghini", "BestLap": 175000, "TotalTime": 355000, "BallastKG": 0, "Restrictor": 0},
        ],
        "Events": [],
    }

    result = process_race(race, grid=["NoaH"])

    assert list(result["laps"]) == ["NoaH"]
    assert result["result"][0]["driverKey"] == "NoaH"


# ---- Position computation ----

def test_compute_positions_simple():
    laps = {
        "A": [100, 100, 100],
        "B": [110, 90, 100],
    }
    positions = _compute_positions(laps, ["A", "B"])
    assert positions["A"][0] == 1
    assert positions["B"][0] == 2
    assert positions["A"][1] == 1
    assert positions["B"][2] == 2

def test_compute_positions_overtake():
    laps = {
        "A": [100, 120],
        "B": [110, 90],
    }
    positions = _compute_positions(laps, ["A", "B"])
    assert positions["A"][0] == 1
    assert positions["A"][1] == 2
    assert positions["B"][0] == 2
    assert positions["B"][1] == 1

def test_compute_positions_keeps_dnf_in_later_laps():
    laps = {
        "A": [100, 100, 100],
        "B": [90],
    }
    positions = _compute_positions(laps, ["A", "B"])
    details = _compute_position_details(laps, ["A", "B"])

    assert positions["B"] == [1, 2, 2]
    assert len(positions["B"]) == 3
    assert details["B"][-1]["lapsCompleted"] == 1
    assert details["B"][-1]["status"] == "dnf_or_lapped"

def test_compute_position_changes_net():
    positions = {"A": [1, 2, 1], "B": [2, 1, 2]}
    changes = _compute_position_changes(positions)
    assert changes["A"]["gained"] == 1
    assert changes["A"]["lost"] == 1
    assert changes["A"]["net"] == 0

def test_position_changes_count_start_move():
    # B starts P2 on the grid, leads every lap → gained the launch.
    positions = {"A": [2, 2], "B": [1, 1]}
    grid = ["A", "B"]
    changes = _compute_position_changes(positions, grid)
    assert changes["B"]["gained"] == 1
    assert changes["B"]["net"] == 1
    assert changes["A"]["lost"] == 1
    assert changes["A"]["net"] == -1

def test_position_changes_grid_densified_over_lap_drivers():
    # Grid has a DNS driver (X) ahead who never appears in positions.
    # A starting "behind" X should not be credited a phantom gain.
    positions = {"A": [1], "B": [2]}
    grid = ["X", "A", "B"]
    changes = _compute_position_changes(positions, grid)
    assert changes["A"]["net"] == 0
    assert changes["B"]["net"] == 0


# ---- Cuts, tyres, sectors ----

def test_cuts_and_tyres_captured():
    race = copy.deepcopy(RACE_RESULT)
    for lap in race["Laps"]:
        if lap["DriverName"] == "Josie":
            lap["Cuts"] = 1
            lap["Tyre"] = "SM"
    result = process_race(race, grid=["Josie", "Toby", "Lee"])
    assert result["cuts"]["Josie"] == [1, 1, 1, 1]
    assert result["tyres"]["Josie"] == ["SM", "SM", "SM", "SM"]

def test_lap_tables_are_index_aligned():
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    for driver, laps in result["laps"].items():
        assert len(result["sectors"][driver]) == len(laps)
        assert len(result["cuts"][driver]) == len(laps)
        assert len(result["tyres"][driver]) == len(laps)

def test_invalid_sectors_nulled_but_positions_preserved():
    race = copy.deepcopy(RACE_RESULT)
    race["Laps"][0]["Sectors"] = [31000, 0, 33000]  # middle sector un-timed
    result = process_race(race, grid=["Josie", "Toby", "Lee"])
    assert result["sectors"]["Josie"][0] == [31000, None, 33000]


# ---- Contact driver identity ----

def test_contacts_use_registry_labels_for_duplicate_names():
    race = {
        "Type": "RACE",
        "Laps": [
            {"DriverName": "Alex", "DriverGuid": "guid-a", "CarId": 1, "CarModel": "car_a", "LapTime": 100000, "Sectors": [33000, 33000, 34000], "Timestamp": 100000},
            {"DriverName": "Alex", "DriverGuid": "guid-b", "CarId": 2, "CarModel": "car_b", "LapTime": 101000, "Sectors": [33000, 34000, 34000], "Timestamp": 101000},
            {"DriverName": "Alex", "DriverGuid": "guid-a", "CarId": 1, "CarModel": "car_a", "LapTime": 99000, "Sectors": [33000, 33000, 33000], "Timestamp": 199000},
            {"DriverName": "Alex", "DriverGuid": "guid-b", "CarId": 2, "CarModel": "car_b", "LapTime": 99500, "Sectors": [33000, 33000, 33500], "Timestamp": 200500},
        ],
        "Result": [
            {"DriverName": "Alex", "DriverGuid": "guid-a", "CarId": 1, "CarModel": "car_a", "BestLap": 99000, "TotalTime": 199000, "BallastKG": 0, "Restrictor": 0},
            {"DriverName": "Alex", "DriverGuid": "guid-b", "CarId": 2, "CarModel": "car_b", "BestLap": 99500, "TotalTime": 200500, "BallastKG": 0, "Restrictor": 0},
        ],
        "Events": [
            {"Type": "COLLISION_WITH_CAR",
             "Driver": {"Name": "Alex", "Guid": "guid-a"}, "OtherDriver": {"Name": "Alex", "Guid": "guid-b"},
             "CarId": 1, "OtherCarId": 2, "ImpactSpeed": 20.0, "Timestamp": 150000},
        ],
    }
    result = process_race(race)
    contact = result["contacts"][0]
    assert contact["driver1"] == "Alex"
    assert contact["driver2"] == "Alex (car 2)"
    # Both labels are real keys in the rest of the output.
    assert contact["driver1"] in result["laps"]
    assert contact["driver2"] in result["laps"]

def test_contacts_still_resolve_unique_names_without_guid():
    # Standard fixture: events carry no guid/CarId — must still attribute.
    result = process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])
    contact = result["contacts"][0]
    assert contact["driver1"] == "Josie"
    assert contact["driver2"] == "Toby"
    assert contact["driver1"] in result["laps"]


# ---- Pace cleaning ----

def test_clean_laps_excludes_lap1():
    result = _clean_laps([120000, 97000, 97500, 98000])
    assert 120000 not in result
    assert len(result) == 3

def test_clean_laps_excludes_outliers():
    result = _clean_laps([100000, 97000, 97500, 200000])
    assert 200000 not in result

def test_clean_laps_single_lap():
    assert _clean_laps([97000]) == []


# ---- Output merge ----

def test_merge_creates_new_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "output.json"
        races = {"1": process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])}
        merge_into_output(out_path, "S20", "Imola", 1, None, races)

        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert data["version"] == 1
        assert "S20" in data["seasons"]
        assert "Imola" in data["seasons"]["S20"]["venues"]
        assert "1" in data["seasons"]["S20"]["venues"]["Imola"]["races"]

def test_merge_preserves_existing():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "output.json"
        races1 = {"1": process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])}
        merge_into_output(out_path, "S19", "Spa", 1, None, races1)

        races2 = {"1": process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])}
        merge_into_output(out_path, "S20", "Imola", 1, None, races2)

        data = json.loads(out_path.read_text())
        assert "S19" in data["seasons"]
        assert "S20" in data["seasons"]

def test_merge_with_qualifying():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "output.json"
        qual = process_qualifying(QUALIFYING_RESULT)
        races = {"1": process_race(RACE_RESULT, grid=qual["grid"])}
        merge_into_output(out_path, "S20", "Imola", 1, qual, races)

        data = json.loads(out_path.read_text())
        venue = data["seasons"]["S20"]["venues"]["Imola"]
        assert "qualifying" in venue
        assert venue["qualifying"]["qual1"]["grid"] == ["Josie", "Toby", "Lee"]

def test_merge_with_multiple_qualifying_sessions():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "output.json"
        qual1 = process_qualifying(QUALIFYING_RESULT)
        qual2 = process_qualifying({
            **QUALIFYING_RESULT,
            "Result": [
                {"DriverName": "Toby", "CarModel": "tatuus_fa01", "BestLap": 96000, "TotalTime": 195300, "BallastKG": 0, "Restrictor": 0},
                {"DriverName": "Josie", "CarModel": "tatuus_fa01", "BestLap": 97000, "TotalTime": 194300, "BallastKG": 0, "Restrictor": 0},
            ],
        })
        races = {"1": process_race(RACE_RESULT, grid=qual1["grid"])}
        merge_into_output(out_path, "S20", "Imola", 1, {"qual1": qual1, "qual2": qual2}, races)

        data = json.loads(out_path.read_text())
        qualifying = data["seasons"]["S20"]["venues"]["Imola"]["qualifying"]
        assert qualifying["qual1"]["grid"] == ["Josie", "Toby", "Lee"]
        assert qualifying["qual2"]["grid"] == ["Toby", "Josie"]

def test_merge_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "output.json"
        races = {"1": process_race(RACE_RESULT, grid=["Josie", "Toby", "Lee"])}
        merge_into_output(out_path, "S20", "Imola", 1, None, races)
        merge_into_output(out_path, "S20", "Imola", 1, None, races)

        data = json.loads(out_path.read_text())
        assert len(data["seasons"]["S20"]["venues"]) == 1


# ---- Driver aliases ----

def _set_aliases(aliases: dict[str, str]):
    """Helper to temporarily set aliases for testing."""
    proc_module.DRIVER_ALIASES.clear()
    proc_module.DRIVER_ALIASES.update(aliases)
    proc_module._ALIAS_LOOKUP = None

def _clear_aliases():
    proc_module.DRIVER_ALIASES.clear()
    proc_module._ALIAS_LOOKUP = None


def test_resolve_alias_basic():
    _set_aliases({"xX_SpeedDemon_Xx": "James"})
    assert resolve_driver_name("xX_SpeedDemon_Xx") == "James"
    _clear_aliases()

def test_resolve_alias_case_insensitive():
    _set_aliases({"speedking": "Toby"})
    assert resolve_driver_name("SpeedKing") == "Toby"
    assert resolve_driver_name("SPEEDKING") == "Toby"
    _clear_aliases()

def test_resolve_no_alias_passthrough():
    _clear_aliases()
    assert resolve_driver_name("Josie") == "Josie"

def test_alias_applies_in_extract():
    _set_aliases({"racerboy99": "Lee"})
    entry = {"DriverName": "racerboy99"}
    assert extract_driver_name(entry) == "Lee"
    _clear_aliases()

def test_alias_applies_in_full_race():
    _set_aliases({"Josie": "JosieRenamed"})
    result = process_race(RACE_RESULT, grid=None)
    assert "JosieRenamed" in result["laps"]
    assert "Josie" not in result["laps"]
    _clear_aliases()
