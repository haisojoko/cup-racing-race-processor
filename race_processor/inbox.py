"""Drop-zone scanning, classification, and dedup.

Reads a season folder full of raw AC server result JSONs, records each file's
hash / timestamp / session type, and removes byte-identical duplicates and
aborted-restart files before the sessions are grouped into events.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INVALID_LAP_TIME = 999_000_000


@dataclass
class SessionFile:
    """One raw result file plus everything derived from it."""

    path: Path
    sha256: str
    timestamp: datetime
    timestamp_inferred: bool
    session_type: str  # qualifying | race | practice | unrecognized | unreadable
    data: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def track_key(self) -> str:
        return f"{self.data.get('TrackName', '')}|{self.data.get('TrackConfig', '')}"


@dataclass
class ScanResult:
    sessions: list[SessionFile]  # kept, timestamp-sorted
    dropped: list[dict[str, Any]]  # dedup decisions
    unprocessed: list[dict[str, Any]]  # unreadable / unrecognized


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_filename_timestamp(name: str) -> datetime | None:
    """Parse ``YYYY_M_D_H_M_TYPE.json`` (components are not zero-padded)."""
    stem = name[:-5] if name.lower().endswith(".json") else name
    parts = stem.split("_")
    if len(parts) < 5:
        return None
    try:
        y, mo, d, h, mi = (int(parts[i]) for i in range(5))
        return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


def classify(data: dict) -> str:
    type_value = str(data.get("Type", "")).strip().upper()
    if type_value.startswith("QUAL"):
        return "qualifying"
    if type_value.startswith("RACE"):
        return "race"
    if type_value.startswith("PRAC"):
        return "practice"
    return "unrecognized"


def _valid_lap_count(data: dict) -> int:
    return sum(
        1
        for lap in (data.get("Laps") or [])
        if isinstance(lap.get("LapTime"), (int, float)) and 0 < lap["LapTime"] < INVALID_LAP_TIME
    )


def _max_laps_by_driver(data: dict) -> int:
    counts: dict[Any, int] = {}
    for lap in (data.get("Laps") or []):
        t = lap.get("LapTime")
        if isinstance(t, (int, float)) and 0 < t < INVALID_LAP_TIME:
            key = lap.get("DriverGuid") or lap.get("DriverName")
            counts[key] = counts.get(key, 0) + 1
    return max(counts.values(), default=0)


def _is_finished_race(data: dict) -> bool:
    """A race whose leader completed the scheduled distance is never a restart."""
    race_laps = data.get("RaceLaps", 0)
    return bool(race_laps) and _max_laps_by_driver(data) >= race_laps


def scan_season(folder: Path, restart_window_minutes: float = 30.0) -> ScanResult:
    """Scan one season folder into kept sessions + drop/unprocessed records."""
    dropped: list[dict[str, Any]] = []
    unprocessed: list[dict[str, Any]] = []
    parsed: list[SessionFile] = []

    for path in sorted(folder.glob("*.json")):
        digest = sha256_of(path)
        ts = parse_filename_timestamp(path.name)
        inferred = ts is None
        if ts is None:
            ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            unprocessed.append({"file": path.name, "reason": "unreadable", "detail": str(exc)})
            continue

        if not isinstance(data, dict) or "Type" not in data or "Laps" not in data:
            unprocessed.append({
                "file": path.name,
                "reason": "unrecognized",
                "detail": "missing Type/Laps — not an AC result file",
            })
            continue

        parsed.append(SessionFile(
            path=path,
            sha256=digest,
            timestamp=ts,
            timestamp_inferred=inferred,
            session_type=classify(data),
            data=data,
            detail="timestamp inferred from file mtime" if inferred else "",
        ))

    parsed.sort(key=lambda s: (s.timestamp, s.name))

    kept = _dedup_identical(parsed, dropped)
    kept = _dedup_restarts(kept, dropped, restart_window_minutes)
    return ScanResult(sessions=kept, dropped=dropped, unprocessed=unprocessed)


def _dedup_identical(sessions: list[SessionFile], dropped: list[dict]) -> list[SessionFile]:
    """Rule A: byte-identical files — keep the earliest, drop the rest."""
    seen: dict[str, SessionFile] = {}
    kept: list[SessionFile] = []
    for s in sessions:
        first = seen.get(s.sha256)
        if first is None:
            seen[s.sha256] = s
            kept.append(s)
        else:
            dropped.append({
                "file": s.name,
                "sha256": s.sha256,
                "reason": "byte-identical",
                "detail": f"identical to {first.name}",
                "trackKey": s.track_key,
                "date": s.timestamp.strftime("%Y-%m-%d"),
            })
    return kept


def _dedup_restarts(
    sessions: list[SessionFile], dropped: list[dict], window_minutes: float
) -> list[SessionFile]:
    """Rule B: aborted restarts — an earlier same-track/type session with far
    fewer laps than a sibling within the window is dropped. Applied
    left-to-right so a chain of restarts collapses to the final run.
    """
    kept: list[SessionFile] = []
    for s in sessions:
        replaced = False
        for i, later in enumerate(sessions):
            if later is s:
                continue
            if later.session_type != s.session_type or later.track_key != s.track_key:
                continue
            if later.timestamp < s.timestamp:
                continue
            if (later.timestamp - s.timestamp).total_seconds() > window_minutes * 60:
                continue
            if _is_finished_race(s.data):
                continue
            s_laps = _valid_lap_count(s.data)
            later_laps = _valid_lap_count(later.data)
            if later_laps > 0 and s_laps < 0.5 * later_laps:
                dropped.append({
                    "file": s.name,
                    "sha256": s.sha256,
                    "reason": "aborted-restart",
                    "detail": (
                        f"{s_laps} valid laps vs {later_laps} in {later.name} "
                        f"{int((later.timestamp - s.timestamp).total_seconds() / 60)} min later"
                    ),
                    "trackKey": s.track_key,
                    "date": s.timestamp.strftime("%Y-%m-%d"),
                })
                replaced = True
                break
        if not replaced:
            kept.append(s)
    return kept
