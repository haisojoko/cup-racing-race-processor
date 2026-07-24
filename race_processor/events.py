"""Group deduped sessions into events (venue race-days).

An event is a run of sessions on the same track with no gap longer than
``eventGapHours``. Sessions are numbered within the event by kind: races
"1".."N" chronologically, qualifying qual1..M, practice as a list. No format
is assumed — whatever sessions exist are grouped as-is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .inbox import SessionFile


@dataclass
class Event:
    event_id: str
    venue: str
    date: str  # YYYY-MM-DD
    track: str
    track_config: str
    qualifying: list[SessionFile] = field(default_factory=list)
    races: list[SessionFile] = field(default_factory=list)
    practice: list[SessionFile] = field(default_factory=list)

    @property
    def sessions(self) -> list[SessionFile]:
        return [*self.qualifying, *self.races, *self.practice]


def _track_slug(track_name: str) -> str:
    segment = track_name.replace("\\", "/").rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", segment.lower()).strip("-")
    return slug or "track"


def _prettify(track_name: str) -> str:
    segment = track_name.replace("\\", "/").rstrip("/").split("/")[-1]
    words = re.split(r"[^a-z0-9]+", segment.lower())
    return " ".join(w.capitalize() for w in words if w) or segment


def group_events(
    sessions: list[SessionFile],
    gap_hours: float = 4.0,
    track_display_names: dict[str, str] | None = None,
) -> list[Event]:
    """Group timestamp-sorted sessions into events."""
    track_display_names = track_display_names or {}
    if not sessions:
        return []

    ordered = sorted(sessions, key=lambda s: (s.timestamp, s.name))
    groups: list[list[SessionFile]] = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        same_track = cur.track_key == prev.track_key
        within_gap = (cur.timestamp - prev.timestamp).total_seconds() <= gap_hours * 3600
        if same_track and within_gap:
            groups[-1].append(cur)
        else:
            groups.append([cur])

    events: list[Event] = []
    used_ids: dict[str, int] = {}
    for group in groups:
        first = group[0]
        track_name = str(first.data.get("TrackName", ""))
        track_config = str(first.data.get("TrackConfig", ""))
        date = first.timestamp.strftime("%Y-%m-%d")
        slug = _track_slug(track_name)

        base_id = f"{date}-{slug}"
        used_ids[base_id] = used_ids.get(base_id, 0) + 1
        event_id = base_id if used_ids[base_id] == 1 else f"{base_id}-{used_ids[base_id]}"

        venue = track_display_names.get(f"{track_name}|{track_config}") \
            or track_display_names.get(track_name) \
            or _prettify(track_name)

        event = Event(
            event_id=event_id,
            venue=venue,
            date=date,
            track=track_name,
            track_config=track_config,
        )
        for s in group:
            if s.session_type == "qualifying":
                event.qualifying.append(s)
            elif s.session_type == "race":
                event.races.append(s)
            elif s.session_type == "practice":
                event.practice.append(s)
        events.append(event)

    return events
