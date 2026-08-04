"""Configuration loading for the race processor.

A single ``config.json`` at the repo root holds every knob: where the drop
zone lives, where the dataset is written, where to publish it, driver aliases,
track display names, and the event-grouping thresholds. The file is created
with sensible defaults on first run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classes import SeasonClassSpec, parse_spec

CONFIG_FILENAME = "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "inboxDir": "inbox",
    "datasetDir": "dataset",
    "publishDestinations": [],
    "driverNames": {},
    "driverAliases": {},
    "sharedGuids": [],
    "guestSlotNames": {},
    "reverseGridSeasons": [],
    "seasonClasses": {},
    "trackDisplayNames": {},
    "eventGapHours": 4,
    "restartWindowMinutes": 30,
}

_KNOWN_KEYS = set(DEFAULT_CONFIG)


@dataclass(frozen=True)
class Config:
    """Resolved configuration. Paths are absolute."""

    root: Path
    inbox_dir: Path
    dataset_dir: Path
    publish_destinations: tuple[Path, ...]
    driver_names: dict[str, str] = field(default_factory=dict)
    driver_aliases: dict[str, str] = field(default_factory=dict)
    shared_guids: tuple[str, ...] = ()
    # {guid: {season_id: canonical name}} — for a guest slot reused by a
    # different person each season. Overrides the display name for that season.
    guest_slot_names: dict[str, dict[str, str]] = field(default_factory=dict)
    # Season IDs (e.g. "S19") that run a reverse-grid format: R1/R3 from
    # qualifying, R2/R4 the reverse of the preceding race's finish.
    reverse_grid_seasons: tuple[str, ...] = ()
    # {season_id: class spec} for multi-class seasons. Car-model patterns are
    # per season on purpose: the packs change, so a rule true for S18 says
    # nothing about a future season. See classes.py.
    season_classes: dict[str, SeasonClassSpec] = field(default_factory=dict)
    track_display_names: dict[str, str] = field(default_factory=dict)
    event_gap_hours: float = 4.0
    restart_window_minutes: float = 30.0

    def fingerprint(self) -> str:
        """Stable hash of the config values that affect dataset content.

        Used so that editing an alias or a track display name forces affected
        events to reprocess on the next ingest, even though their source files
        are unchanged.
        """
        payload = json.dumps(
            {
                "driverNames": self.driver_names,
                "driverAliases": self.driver_aliases,
                "sharedGuids": sorted(self.shared_guids),
                "guestSlotNames": self.guest_slot_names,
                "reverseGridSeasons": sorted(self.reverse_grid_seasons),
                "seasonClasses": {
                    sid: [
                        spec.championship,
                        list(spec.order),
                        sorted((k, list(v)) for k, v in spec.by_car_model.items()),
                        sorted(spec.by_driver.items()),
                        spec.fallback,
                    ]
                    for sid, spec in sorted(self.season_classes.items())
                },
                "trackDisplayNames": self.track_display_names,
                "eventGapHours": self.event_gap_hours,
                "restartWindowMinutes": self.restart_window_minutes,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def default_config_path(root: Path) -> Path:
    return root / CONFIG_FILENAME


def write_default_config(path: Path) -> None:
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")


def _strip_line_comments(text: str) -> str:
    """Permit whole-line ``//`` or ``#`` comments in config.json.

    JSON itself has no comments, so any line whose first non-space character
    begins one is dropped before parsing. Only *whole-line* comments are
    removed — a value that contains ``//`` or ``#`` is never touched, because a
    JSON string can't span a line break, so no value can start a line.
    """
    kept = [line for line in text.splitlines()
            if not line.lstrip().startswith(("//", "#"))]
    return "\n".join(kept)


def load_config(path: Path, *, warn=print) -> Config:
    """Load config from ``path``. Relative paths resolve against its directory.

    Whole-line ``//`` / ``#`` comments are allowed. Unknown top-level keys emit
    a warning (typo guard) but are ignored. Missing keys fall back to defaults.
    """
    root = path.parent.resolve()
    raw = json.loads(_strip_line_comments(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")

    for key in raw:
        if key not in _KNOWN_KEYS:
            warn(f"WARNING: unknown config key '{key}' in {path.name} (ignored)")

    merged = {**DEFAULT_CONFIG, **raw}

    def resolve(p: str) -> Path:
        candidate = Path(p)
        return candidate if candidate.is_absolute() else (root / candidate).resolve()

    return Config(
        root=root,
        inbox_dir=resolve(str(merged["inboxDir"])),
        dataset_dir=resolve(str(merged["datasetDir"])),
        publish_destinations=tuple(resolve(str(d)) for d in merged["publishDestinations"]),
        driver_names=dict(merged["driverNames"]),
        driver_aliases=dict(merged["driverAliases"]),
        shared_guids=tuple(str(g).strip() for g in merged["sharedGuids"] if str(g).strip()),
        guest_slot_names={
            str(g).strip(): {str(s).strip(): str(n) for s, n in (m or {}).items()}
            for g, m in dict(merged["guestSlotNames"]).items() if str(g).strip()
        },
        reverse_grid_seasons=tuple(str(x).strip() for x in merged["reverseGridSeasons"] if str(x).strip()),
        season_classes={
            str(sid).strip(): parse_spec(raw)
            for sid, raw in dict(merged["seasonClasses"]).items() if str(sid).strip()
        },
        track_display_names=dict(merged["trackDisplayNames"]),
        event_gap_hours=float(merged["eventGapHours"]),
        restart_window_minutes=float(merged["restartWindowMinutes"]),
    )


def load_or_create_config(root: Path, *, warn=print) -> Config:
    """Load config from ``root``; create it with defaults if absent."""
    path = default_config_path(root)
    if not path.exists():
        write_default_config(path)
        warn(f"Created {path.name} with defaults. Edit it to set publish destinations.")
    return load_config(path, warn=warn)
