"""Golden test against the real S22 Road Atlanta files shipped in the repo.

Validates the end-to-end pipeline on real data: the Road Atlanta round has
three races and the known qual-standard-reverse format is recovered (race 2 is
the reverse-grid race). Dedup behaviour is covered separately with controlled
fixtures in test_inbox.py.
"""

from pathlib import Path

import pytest

from race_processor.dataset import build_event, event_signature
from race_processor.events import group_events
from race_processor.inbox import scan_season

INBOX_S22 = Path(__file__).resolve().parent.parent / "inbox" / "S22"

pytestmark = pytest.mark.skipif(
    not INBOX_S22.exists() or not any(INBOX_S22.glob("*.json")),
    reason="real S22 inbox files not present",
)


ROAD_ATLANTA_TRACK = "jr_road_atlanta_2022"


def _build():
    """Build every S22 event, then return the Road Atlanta one plus the scan.

    S22 may contain other venues (the inbox grows over time), so this locates
    the Road Atlanta event rather than assuming it is the only one.
    """
    scan = scan_season(INBOX_S22)
    events = group_events(scan.sessions, gap_hours=4, track_display_names={
        "csp/2144/../jr_road_atlanta_2022|full": "Road Atlanta"
    })
    built = [build_event(ev, event_signature(ev, "fp"), scan.dropped) for ev in events]
    atlanta = next(e for e in built if ROAD_ATLANTA_TRACK in e["track"])
    return atlanta, scan


def test_s22_road_atlanta_three_races():
    event, _ = _build()
    assert event["venue"] == "Road Atlanta"
    assert len(event["races"]) == 3


def test_s22_reverse_grid_format_recovered():
    event, _ = _build()
    races = event["races"]
    # Q1 → R1 (standard) → R2 (reverse) → Q2 → R3 (standard)
    assert races["1"]["gridSource"] == "qualifying"
    assert races["2"]["gridSource"] == "reversed-previous"
    assert races["3"]["gridSource"] == "qualifying"
    # Standard-grid races off a fresh qualifying get position changes.
    assert races["1"]["positionChanges"] is not None
    assert races["3"]["positionChanges"] is not None


def test_s22_contacts_have_world_position():
    event, _ = _build()
    contacts = event["races"]["1"]["contacts"]
    assert contacts
    assert all("worldPosition" in c for c in contacts)
