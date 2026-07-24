"""Tests for event grouping."""

from datetime import datetime, timezone

from race_processor.events import group_events
from race_processor.inbox import SessionFile


def _sf(name, track, ts, type_="race") -> SessionFile:
    return SessionFile(
        path=type("P", (), {"name": name})(),  # lightweight stand-in with .name
        sha256=name,
        timestamp=ts,
        timestamp_inferred=False,
        session_type=type_,
        data={"TrackName": track, "TrackConfig": ""},
    )


def _dt(h, mi=0, day=1):
    return datetime(2026, 1, day, h, mi, tzinfo=timezone.utc)


def test_track_change_splits_events():
    sessions = [
        _sf("a", "imola", _dt(20)),
        _sf("b", "spa", _dt(21)),
    ]
    events = group_events(sessions, gap_hours=4)
    assert len(events) == 2
    assert {e.venue for e in events} == {"Imola", "Spa"}


def test_large_gap_splits_events():
    sessions = [
        _sf("a", "imola", _dt(10)),
        _sf("b", "imola", _dt(20)),  # 10h later
    ]
    events = group_events(sessions, gap_hours=4)
    assert len(events) == 2


def test_midnight_crossing_kept_as_one_event():
    sessions = [
        _sf("a", "imola", datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)),
        _sf("b", "imola", datetime(2026, 1, 2, 0, 10, tzinfo=timezone.utc)),
    ]
    events = group_events(sessions, gap_hours=4)
    assert len(events) == 1


def test_event_id_collision_suffix():
    # Same track, same day, >4h apart → two events, second suffixed.
    sessions = [
        _sf("a", "imola", _dt(8)),
        _sf("b", "imola", _dt(20)),
    ]
    events = group_events(sessions, gap_hours=4)
    ids = [e.event_id for e in events]
    assert ids[0] == "2026-01-01-imola"
    assert ids[1] == "2026-01-01-imola-2"


def test_track_display_name_lookup():
    sessions = [_sf("a", "csp/2144/../jr_road_atlanta_2022", _dt(20))]
    events = group_events(sessions, gap_hours=4, track_display_names={
        "csp/2144/../jr_road_atlanta_2022|": "Road Atlanta"
    })
    assert events[0].venue == "Road Atlanta"


def test_sessions_bucketed_by_type():
    sessions = [
        _sf("q", "imola", _dt(20), type_="qualifying"),
        _sf("r1", "imola", _dt(20, 20), type_="race"),
        _sf("p", "imola", _dt(19), type_="practice"),
    ]
    events = group_events(sessions, gap_hours=4)
    assert len(events) == 1
    e = events[0]
    assert len(e.qualifying) == 1 and len(e.races) == 1 and len(e.practice) == 1
