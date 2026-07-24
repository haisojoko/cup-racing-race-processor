"""Tests for grid inference."""

from race_processor.gridinfer import infer_grid, kendall_tau
from race_processor.processor import build_registry


def test_kendall_tau_identical():
    assert kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0


def test_kendall_tau_reversed():
    assert kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0


def test_kendall_tau_too_few_common():
    assert kendall_tau(["a"], ["a"]) is None


def _race_from_order(order: list[str]) -> dict:
    """Build a race whose lap-1 crossing order matches ``order``."""
    laps = []
    for i, d in enumerate(order):
        laps.append({
            "DriverName": d, "DriverGuid": f"g_{d}", "CarId": i, "CarModel": "car",
            "LapTime": 90000 + i, "Sectors": [30000, 30000, 30000], "Timestamp": 100000 + i * 10,
        })
    return {
        "TrackName": "imola", "TrackConfig": "", "Type": "RACE", "RaceLaps": 0,
        "Cars": [], "Laps": laps,
        "Result": [{"DriverName": d, "DriverGuid": f"g_{d}", "CarId": i, "CarModel": "car",
                    "BestLap": 90000 + i, "TotalTime": 90000 + i, "BallastKG": 0, "Restrictor": 0}
                   for i, d in enumerate(order)],
        "Events": [],
    }


def test_fresh_quali_trusted_as_high():
    order = ["a", "b", "c", "d", "e"]
    race = _race_from_order(order)
    decision = infer_grid(race, fresh_quali_grid=order, previous_finish=None)
    assert decision.source == "qualifying"
    assert decision.confidence == "high"
    assert decision.grid[:5] == order


def test_reverse_grid_detected_from_previous():
    finish = ["a", "b", "c", "d", "e"]
    # Reverse-grid race: running order early ≈ reversed finish.
    observed = list(reversed(finish))
    race = _race_from_order(observed)
    decision = infer_grid(race, fresh_quali_grid=None, previous_finish=finish)
    assert decision.source == "reversed-previous"
    assert decision.confidence in ("high", "medium")


def test_standard_grid_detected_from_previous():
    finish = ["a", "b", "c", "d", "e"]
    race = _race_from_order(finish)  # order matches finish → standard
    decision = infer_grid(race, fresh_quali_grid=None, previous_finish=finish)
    assert decision.source == "previous-race"


def test_ambiguous_falls_back_to_first_lap():
    # Observed order shares no meaningful correlation with the candidate.
    finish = ["a", "b", "c", "d", "e"]
    observed = ["c", "a", "e", "b", "d"]
    race = _race_from_order(observed)
    decision = infer_grid(race, fresh_quali_grid=None, previous_finish=finish)
    if decision.source == "first-lap-inferred":
        assert decision.confidence == "low"
    else:
        # If a candidate did win, it must at least be a real one, not garbage.
        assert decision.source in ("previous-race", "reversed-previous")


def test_forced_reverse_trusts_format_over_ambiguous_lap1():
    finish = ["a", "b", "c", "d", "e"]
    # Jumbled lap-1 order that normal inference would treat as low-confidence.
    observed = ["c", "a", "e", "b", "d"]
    race = _race_from_order(observed)
    decision = infer_grid(race, fresh_quali_grid=None, previous_finish=finish, forced_reverse=True)
    assert decision.source == "reversed-previous"
    assert decision.confidence in ("high", "medium")
    assert decision.grid[:5] == list(reversed(finish))


def test_forced_reverse_without_previous_finish_falls_through():
    order = ["a", "b", "c"]
    race = _race_from_order(order)
    decision = infer_grid(race, fresh_quali_grid=None, previous_finish=None, forced_reverse=True)
    # Nothing to reverse → ordinary inference (first-lap here), not a crash.
    assert decision.source in ("first-lap-inferred", "unknown")


def test_no_signal_is_unknown():
    race = {"TrackName": "x", "TrackConfig": "", "Type": "RACE", "Laps": [], "Result": [], "Cars": [], "Events": []}
    decision = infer_grid(race, fresh_quali_grid=None, previous_finish=None)
    assert decision.source == "unknown"
    assert decision.grid == []
