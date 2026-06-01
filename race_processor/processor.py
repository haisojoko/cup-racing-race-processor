"""Core processing logic for AC server result JSONs.

Transforms raw AC dedicated-server result files into structured race
detail records suitable for the Cup Racing Data SPA and cup-racing-insights.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Map Steam/AC display names to canonical Cup Racing names.
# Add entries here when a driver's in-game name differs from
# the name used in the league spreadsheet.
# Keys are matched case-insensitively.
DRIVER_ALIASES: dict[str, str] = {
    # "SteamDisplayName": "Spreadsheet Name",
}

_ALIAS_LOOKUP: dict[str, str] | None = None
INVALID_LAP_TIME = 999_000_000


def _get_alias_lookup() -> dict[str, str]:
    global _ALIAS_LOOKUP
    if _ALIAS_LOOKUP is None:
        _ALIAS_LOOKUP = {k.lower().strip(): v for k, v in DRIVER_ALIASES.items()}
    return _ALIAS_LOOKUP


def resolve_driver_name(raw_name: str) -> str:
    """Apply alias mapping to a raw driver name."""
    clean = raw_name.strip()
    if not clean:
        return clean
    return _get_alias_lookup().get(clean.lower(), clean)


def load_server_result(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def extract_driver_name(entry: dict) -> str:
    """Pull the driver name from various AC result structures and resolve aliases."""
    if isinstance(entry.get("Driver"), dict):
        raw = entry["Driver"].get("Name", "").strip()
    elif isinstance(entry.get("DriverName"), str):
        raw = entry["DriverName"].strip()
    else:
        raw = entry.get("Driver", "").strip()
    return resolve_driver_name(raw)


def extract_car_model(entry: dict) -> str:
    if isinstance(entry.get("CarModel"), str):
        return entry["CarModel"].strip()
    return entry.get("Model", "").strip()


def extract_driver_guid(entry: dict) -> str:
    """Pull a Steam GUID from result, lap, car, or event structures."""
    if isinstance(entry.get("DriverGuid"), str):
        return entry["DriverGuid"].strip()
    if isinstance(entry.get("Driver"), dict):
        guid = entry["Driver"].get("Guid", "")
        if isinstance(guid, str):
            return guid.strip()
    return ""


def extract_car_id(entry: dict) -> int | None:
    car_id = entry.get("CarId")
    if isinstance(car_id, int):
        return car_id
    if isinstance(car_id, str) and car_id.strip().lstrip("-").isdigit():
        return int(car_id)
    return None


@dataclass(frozen=True)
class DriverIdentity:
    name: str
    guid: str = ""
    car_id: int | None = None
    car_model: str = ""

    @property
    def identity_key(self) -> str:
        if self.guid and self.car_id is not None:
            return f"guid:{self.guid}|car:{self.car_id}"
        if self.guid and self.car_model:
            return f"guid:{self.guid}|model:{self.car_model}"
        if self.guid:
            return f"guid:{self.guid}"
        if self.car_id is not None:
            return f"name:{self.name.casefold()}|car:{self.car_id}"
        if self.car_model:
            return f"name:{self.name.casefold()}|model:{self.car_model}"
        return f"name:{self.name.casefold()}"


@dataclass
class DriverRegistry:
    by_identity_key: dict[str, str] = field(default_factory=dict)
    meta_by_label: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)

    def register_entry(self, entry: dict) -> str:
        ident = extract_driver_identity(entry)
        if not ident.name:
            return ""

        existing = self.by_identity_key.get(ident.identity_key)
        if existing:
            return existing

        label = self._unique_label(ident)
        self.by_identity_key[ident.identity_key] = label
        self.meta_by_label[label] = {
            "driver": ident.name,
            "guid": ident.guid,
            "carId": ident.car_id,
            "car": ident.car_model,
        }
        self.labels.append(label)
        return label

    def label_for_entry(self, entry: dict) -> str:
        ident = extract_driver_identity(entry)
        if not ident.name:
            return ""
        return self.by_identity_key.get(ident.identity_key, self.register_entry(entry))

    def existing_label_for_entry(self, entry: dict) -> str:
        ident = extract_driver_identity(entry)
        if not ident.name:
            return ""
        return self.by_identity_key.get(ident.identity_key, "")

    def labels_for_grid_value(self, value: str) -> list[str]:
        if value in self.meta_by_label:
            return [value]
        return [
            label
            for label in self.labels
            if self.meta_by_label[label]["driver"] == value
        ]

    def _unique_label(self, ident: DriverIdentity) -> str:
        existing = set(self.meta_by_label)
        if ident.name not in existing:
            return ident.name

        suffixes = []
        if ident.car_id is not None:
            suffixes.append(f"car {ident.car_id}")
        if ident.car_model:
            suffixes.append(ident.car_model)
        if ident.guid:
            suffixes.append(ident.guid[-6:])

        for suffix in suffixes:
            candidate = f"{ident.name} ({suffix})"
            if candidate not in existing:
                return candidate

        i = 2
        while True:
            candidate = f"{ident.name} ({i})"
            if candidate not in existing:
                return candidate
            i += 1


def extract_driver_identity(entry: dict) -> DriverIdentity:
    return DriverIdentity(
        name=extract_driver_name(entry),
        guid=extract_driver_guid(entry),
        car_id=extract_car_id(entry),
        car_model=extract_car_model(entry),
    )


def _valid_lap_time(value: Any) -> bool:
    return isinstance(value, (int, float)) and 0 < value < INVALID_LAP_TIME


# ---------------------------------------------------------------------------
# Grid reconstruction
# ---------------------------------------------------------------------------

def build_grid_from_qualifying(qual_data: dict) -> list[str]:
    """Build starting grid from a qualifying result JSON.

    AC qualifying results have Result[] sorted by best lap time.
    """
    results = qual_data.get("Result", [])
    laps = qual_data.get("Laps", [])
    registry = _build_driver_registry(results, laps)
    return _build_qualifying_grid(results, registry)


def _build_qualifying_grid(results: list, registry: DriverRegistry) -> list[str]:
    grid = []
    for entry in results:
        name = registry.existing_label_for_entry(entry)
        best = entry.get("BestLap", 0)
        if name and _valid_lap_time(best):
            grid.append((best, name))

    grid.sort(key=lambda x: x[0])
    return [name for _, name in grid]


def build_grid_from_first_lap(race_data: dict) -> list[str]:
    """Infer starting grid from first-lap completion order.

    Not perfect (fast starters on lap 1 != grid order) but the best
    we can do without qualifying data.
    """
    laps = race_data.get("Laps", [])
    registry = _build_driver_registry([], laps)
    first_laps: list[tuple[int, str]] = []
    seen: set[str] = set()

    for lap in laps:
        name = registry.existing_label_for_entry(lap)
        if not name or name in seen:
            continue
        seen.add(name)
        ts = lap.get("Timestamp", 0)
        first_laps.append((ts, name))

    first_laps.sort(key=lambda x: x[0])
    return [name for _, name in first_laps]


# ---------------------------------------------------------------------------
# Qualifying processing
# ---------------------------------------------------------------------------

def process_qualifying(qual_data: dict) -> dict[str, Any]:
    """Process a qualifying session into structured output."""
    results = qual_data.get("Result", [])
    laps = qual_data.get("Laps", [])
    registry = _build_driver_registry(results, laps)
    grid = _build_qualifying_grid(results, registry)

    times_by_driver: dict[str, dict[str, Any]] = {}
    for lap in laps:
        name = registry.existing_label_for_entry(lap)
        if not name:
            continue
        lap_time = lap.get("LapTime", 0)
        if not _valid_lap_time(lap_time):
            continue

        if name not in times_by_driver:
            times_by_driver[name] = {"bestMs": lap_time, "laps": []}
        times_by_driver[name]["laps"].append(lap_time)
        if lap_time < times_by_driver[name]["bestMs"]:
            times_by_driver[name]["bestMs"] = lap_time

    drivers = _collect_drivers(results, laps, grid, registry)
    return {
        "grid": grid,
        "drivers": _build_driver_metadata(drivers, registry),
        "times": times_by_driver,
    }


# ---------------------------------------------------------------------------
# Race processing
# ---------------------------------------------------------------------------

def process_race(race_data: dict, grid: list[str] | None = None) -> dict[str, Any]:
    """Process a single race result JSON into structured output."""
    laps_raw = race_data.get("Laps", [])
    events_raw = race_data.get("Events", [])
    results_raw = race_data.get("Result", [])

    if grid is None:
        grid = build_grid_from_first_lap(race_data)

    registry = _build_driver_registry(results_raw, laps_raw)
    drivers = _collect_drivers(results_raw, laps_raw, grid, registry)
    laps_by_driver = _build_laps_by_driver(laps_raw, drivers, registry)
    sectors_by_driver = _build_sectors_by_driver(laps_raw, drivers, registry)
    positions = _compute_positions(laps_by_driver, drivers)
    position_details = _compute_position_details(laps_by_driver, drivers)
    pos_changes = _compute_position_changes(positions)
    contacts = _map_contacts_to_laps(events_raw, laps_raw, drivers)
    pace = _compute_pace(laps_by_driver)
    result = _build_result(results_raw, laps_raw, registry)
    driver_meta = _build_driver_metadata(drivers, registry)

    return {
        "grid": grid,
        "drivers": driver_meta,
        "laps": laps_by_driver,
        "sectors": sectors_by_driver,
        "positions": positions,
        "positionDetails": position_details,
        "positionChanges": pos_changes,
        "result": result,
        "contacts": contacts,
        "pace": pace,
    }


def _build_driver_registry(results: list, laps: list) -> DriverRegistry:
    registry = DriverRegistry()
    for lap in laps:
        if _valid_lap_time(lap.get("LapTime", 0)):
            registry.register_entry(lap)
    lap_keys = {
        extract_driver_identity(lap).identity_key
        for lap in laps
        if extract_driver_name(lap) and _valid_lap_time(lap.get("LapTime", 0))
    }
    for entry in results:
        if _result_entry_has_evidence(entry, extract_driver_identity(entry).identity_key in lap_keys):
            registry.register_entry(entry)
    return registry


def _collect_drivers(
    results: list, laps: list, grid: list, registry: DriverRegistry
) -> list[str]:
    """Get ordered driver list from all available sources."""
    seen: set[str] = set()
    ordered: list[str] = []

    for grid_value in grid:
        for label in registry.labels_for_grid_value(grid_value):
            if label and label not in seen:
                seen.add(label)
                ordered.append(label)
        if grid_value and not registry.labels_for_grid_value(grid_value) and grid_value not in seen:
            seen.add(grid_value)
            ordered.append(grid_value)

    for entry in results:
        label = registry.existing_label_for_entry(entry)
        if label and label not in seen:
            seen.add(label)
            ordered.append(label)

    for lap in laps:
        label = registry.existing_label_for_entry(lap)
        if label and label not in seen:
            seen.add(label)
            ordered.append(label)

    return ordered


def _build_driver_metadata(
    drivers: list[str], registry: DriverRegistry
) -> dict[str, dict[str, Any]]:
    return {
        label: registry.meta_by_label.get(
            label,
            {"driver": label, "guid": "", "carId": None, "car": ""},
        )
        for label in drivers
    }


def _build_laps_by_driver(
    laps: list, drivers: list[str], registry: DriverRegistry
) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {d: [] for d in drivers}
    for lap in laps:
        name = registry.existing_label_for_entry(lap)
        time_ms = lap.get("LapTime", 0)
        if name in result and _valid_lap_time(time_ms):
            result[name].append(time_ms)
    return {d: times for d, times in result.items() if times}


def _build_sectors_by_driver(
    laps: list, drivers: list[str], registry: DriverRegistry
) -> dict[str, list[list[int]]]:
    result: dict[str, list[list[int]]] = {d: [] for d in drivers}
    for lap in laps:
        name = registry.existing_label_for_entry(lap)
        sectors = lap.get("Sectors", [])
        if name in result and sectors and any(_valid_lap_time(s) for s in sectors):
            result[name].append(sectors)
    return {d: secs for d, secs in result.items() if secs}


def _compute_positions(
    laps_by_driver: dict[str, list[int]], drivers: list[str]
) -> dict[str, list[int]]:
    """Compute position after each leader lap.

    Drivers who stop completing laps remain in later snapshots and are ranked
    behind drivers with more completed laps.
    """
    positions: dict[str, list[int]] = {d: [] for d in _position_driver_order(laps_by_driver, drivers)}
    for snapshot in _position_snapshots(laps_by_driver, drivers):
        for row in snapshot:
            positions[row["driver"]].append(row["position"])
    return positions


def _compute_position_details(
    laps_by_driver: dict[str, list[int]], drivers: list[str]
) -> dict[str, list[dict[str, Any]]]:
    details: dict[str, list[dict[str, Any]]] = {
        d: [] for d in _position_driver_order(laps_by_driver, drivers)
    }
    for snapshot in _position_snapshots(laps_by_driver, drivers):
        for row in snapshot:
            driver = row["driver"]
            details[driver].append({
                "lap": row["leaderLap"],
                "position": row["position"],
                "lapsCompleted": row["lapsCompleted"],
                "status": row["status"],
            })
    return details


def _position_driver_order(
    laps_by_driver: dict[str, list[int]], drivers: list[str]
) -> list[str]:
    ordered = [d for d in drivers if d in laps_by_driver]
    for driver in laps_by_driver:
        if driver not in ordered:
            ordered.append(driver)
    return ordered


def _position_snapshots(
    laps_by_driver: dict[str, list[int]], drivers: list[str]
) -> list[list[dict[str, Any]]]:
    if not laps_by_driver:
        return []

    ordered = _position_driver_order(laps_by_driver, drivers)
    order_index = {driver: i for i, driver in enumerate(ordered)}
    max_laps = max(len(times) for times in laps_by_driver.values())
    snapshots: list[list[dict[str, Any]]] = []

    for lap_idx in range(max_laps):
        ranked = []
        leader_lap = lap_idx + 1
        for driver in ordered:
            times = laps_by_driver[driver]
            completed = min(len(times), leader_lap)
            total = sum(times[:completed])
            status = "classified" if completed == leader_lap else "dnf_or_lapped"
            ranked.append({
                "driver": driver,
                "leaderLap": leader_lap,
                "lapsCompleted": completed,
                "totalTimeMs": total,
                "status": status,
            })

        ranked.sort(
            key=lambda row: (
                -row["lapsCompleted"],
                row["totalTimeMs"],
                order_index[row["driver"]],
            )
        )
        for pos_zero, row in enumerate(ranked):
            row["position"] = pos_zero + 1
        snapshots.append(ranked)

    return snapshots


def _compute_position_changes(
    positions: dict[str, list[int]],
) -> dict[str, dict[str, int]]:
    changes: dict[str, dict[str, int]] = {}
    for driver, pos_list in positions.items():
        gained = 0
        lost = 0
        for i in range(1, len(pos_list)):
            diff = pos_list[i - 1] - pos_list[i]
            if diff > 0:
                gained += diff
            elif diff < 0:
                lost += abs(diff)
        changes[driver] = {"gained": gained, "lost": lost, "net": gained - lost}
    return changes


# ---------------------------------------------------------------------------
# Contact mapping
# ---------------------------------------------------------------------------

def _map_contacts_to_laps(
    events: list, laps: list, drivers: list[str]
) -> list[dict[str, Any]]:
    """Map collision events to lap numbers using timestamps."""
    lap_boundaries = _build_lap_boundaries(laps)

    contacts: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("Type", "")
        if event_type != "COLLISION_WITH_CAR":
            continue

        driver1 = _extract_event_driver(event, "Driver")
        driver2 = _extract_event_driver(event, "OtherDriver")
        impact = event.get("ImpactSpeed", 0.0)

        if not driver1 or not driver2:
            continue

        lap_num, lap_confidence = _estimate_lap_for_contact(event, driver1, lap_boundaries)

        contacts.append({
            "lap": lap_num,
            "lapConfidence": lap_confidence,
            "driver1": driver1,
            "driver2": driver2,
            "impactSpeed": round(impact, 1),
        })

    return contacts


def _extract_event_driver(event: dict, key: str) -> str:
    val = event.get(key, {})
    if isinstance(val, dict):
        raw = val.get("Name", "").strip()
    elif isinstance(val, str):
        raw = val.strip()
    else:
        return ""
    return resolve_driver_name(raw)


def _build_lap_boundaries(laps: list) -> dict[str, list[tuple[int, int]]]:
    """Build per-driver list of (lap_start_ts, lap_end_ts) from lap records."""
    by_driver: dict[str, list[dict]] = {}
    for lap in laps:
        name = extract_driver_name(lap)
        if not name:
            continue
        by_driver.setdefault(name, []).append(lap)

    boundaries: dict[str, list[tuple[int, int]]] = {}
    for driver, driver_laps in by_driver.items():
        driver_laps.sort(key=lambda x: x.get("Timestamp", 0))
        bounds: list[tuple[int, int]] = []
        for lap_entry in driver_laps:
            end_ts = lap_entry.get("Timestamp", 0)
            lap_time = lap_entry.get("LapTime", 0)
            start_ts = end_ts - lap_time if lap_time > 0 else end_ts
            bounds.append((start_ts, end_ts))
        boundaries[driver] = bounds

    return boundaries


def _estimate_lap_for_contact(
    event: dict, driver: str, boundaries: dict[str, list[tuple[int, int]]]
) -> tuple[int | None, str]:
    """Best-effort lap number for a contact event.

    AC collision events don't have a direct lap field, so we use the
    RelPosition or infer from the closest lap boundary by timestamp.
    Since collision events don't always carry reliable timestamps,
    we return None if we can't determine the lap.
    """
    driver_bounds = boundaries.get(driver, [])
    if not driver_bounds:
        return None, "unknown"

    event_ts = event.get("Timestamp")
    if event_ts is not None and event_ts > 0:
        for i, (start, end) in enumerate(driver_bounds):
            if start <= event_ts <= end:
                return i + 1, "timestamp"
        closest_lap = min(
            range(len(driver_bounds)),
            key=lambda i: min(abs(driver_bounds[i][0] - event_ts), abs(driver_bounds[i][1] - event_ts)),
        )
        return closest_lap + 1, "timestamp-nearest"

    return None, "unknown"


# ---------------------------------------------------------------------------
# Pace computation
# ---------------------------------------------------------------------------

def _compute_pace(laps_by_driver: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    pace: dict[str, dict[str, Any]] = {}
    for driver, times in laps_by_driver.items():
        clean = _clean_laps(times)
        if not clean:
            continue
        pace[driver] = {
            "avgMs": round(statistics.mean(clean)),
            "medianMs": round(statistics.median(clean)),
            "bestMs": min(clean),
            "lapsUsed": len(clean),
        }
    return pace


def _clean_laps(times: list[int]) -> list[int]:
    """Exclude lap 1 and outliers (>120% of median) for pace calculation."""
    if len(times) <= 1:
        return []

    without_lap1 = times[1:]
    if not without_lap1:
        return []

    med = statistics.median(without_lap1)
    threshold = med * 1.2
    return [t for t in without_lap1 if t <= threshold]


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def _build_result(
    results: list, laps: list, registry: DriverRegistry
) -> list[dict[str, Any]]:
    lap_counts = _valid_lap_counts(laps, registry)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in results:
        label = registry.existing_label_for_entry(entry)
        if not label or label in seen:
            continue

        best_lap = entry.get("BestLap", 0)
        total_time = entry.get("TotalTime", 0)
        has_laps = lap_counts.get(label, 0) > 0
        if not _result_entry_has_evidence(entry, has_laps):
            continue

        meta = registry.meta_by_label.get(label, {})
        output.append({
            "driver": meta.get("driver", label),
            "driverKey": label,
            "guid": meta.get("guid", ""),
            "carId": meta.get("carId"),
            "car": extract_car_model(entry) or meta.get("car", ""),
            "position": len(output) + 1,
            "totalTimeMs": total_time,
            "bestLapMs": best_lap if _valid_lap_time(best_lap) else 0,
            "laps": lap_counts.get(label, 0),
            "ballast": entry.get("BallastKG", 0),
            "restrictor": entry.get("Restrictor", 0),
        })
        seen.add(label)
    return output


def _valid_lap_counts(laps: list, registry: DriverRegistry) -> dict[str, int]:
    counts: dict[str, int] = {}
    for lap in laps:
        label = registry.existing_label_for_entry(lap)
        if label and _valid_lap_time(lap.get("LapTime", 0)):
            counts[label] = counts.get(label, 0) + 1
    return counts


def _result_entry_has_evidence(entry: dict, has_laps: bool) -> bool:
    best_lap = entry.get("BestLap", 0)
    total_time = entry.get("TotalTime", 0)
    has_result_time = isinstance(total_time, (int, float)) and total_time > 0
    return has_result_time or _valid_lap_time(best_lap) or has_laps


# ---------------------------------------------------------------------------
# Output merge
# ---------------------------------------------------------------------------

def merge_into_output(
    output_path: Path,
    season_id: str,
    venue_name: str,
    venue_order: int,
    qualifying: dict[str, Any] | None,
    races: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Read existing output JSON (if any), merge new venue data, write back."""
    if output_path.exists():
        with open(output_path) as f:
            data = json.load(f)
    else:
        data = {"version": 1, "lastUpdated": "", "seasons": {}}

    from datetime import datetime, timezone

    data["lastUpdated"] = datetime.now(timezone.utc).isoformat()

    if season_id not in data["seasons"]:
        data["seasons"][season_id] = {"venues": {}}

    venue_data: dict[str, Any] = {"venueOrder": venue_order, "races": races}
    if qualifying:
        venue_data["qualifying"] = _normalize_qualifying_output(qualifying)

    data["seasons"][season_id]["venues"][venue_name] = venue_data

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return data


def _normalize_qualifying_output(qualifying: dict[str, Any]) -> dict[str, Any]:
    if "grid" in qualifying or "times" in qualifying:
        return {"qual1": qualifying}
    return qualifying
