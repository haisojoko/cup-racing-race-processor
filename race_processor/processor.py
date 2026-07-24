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


# Two ways to canonicalise a driver's name to their Cup Racing name:
#   _GUID_NAME_MAP  — Steam GUID -> canonical name (preferred; survives Steam
#                     display-name changes). From config.json "driverNames".
#   _ALIAS_LOOKUP   — display name -> canonical name (fallback, case-insensitive,
#                     for entries with no GUID). From config.json "driverAliases".
# GUID mapping wins when both could apply.
_GUID_NAME_MAP: dict[str, str] = {}
_ALIAS_LOOKUP: dict[str, str] = {}
# GUIDs shared by several people (a guest machine that cycled display names).
# For these the "one GUID = one person" rule is wrong, so identity falls back to
# the display name and the GUID->name map is ignored. From config.json sharedGuids.
_SHARED_GUIDS: set[str] = set()
# {guid: {season_id: name}} for a guest slot reused by a different person each
# season. Requires _CURRENT_SEASON to be set (the ingest layer sets it per
# season). A guid with guest names is always treated as shared.
_GUEST_SLOT_NAMES: dict[str, dict[str, str]] = {}
_CURRENT_SEASON: str = ""
INVALID_LAP_TIME = 999_000_000


def configure_aliases(mapping: dict[str, str]) -> None:
    """Install the display-name alias table (config.json driverAliases)."""
    global _ALIAS_LOOKUP
    _ALIAS_LOOKUP = {k.lower().strip(): v for k, v in (mapping or {}).items()}


def configure_name_map(mapping: dict[str, str]) -> None:
    """Install the GUID -> canonical name table (config.json driverNames)."""
    global _GUID_NAME_MAP
    _GUID_NAME_MAP = {str(k).strip(): v for k, v in (mapping or {}).items() if str(k).strip()}


def configure_shared_guids(guids) -> None:
    """Install the set of shared/guest GUIDs (config.json sharedGuids)."""
    global _SHARED_GUIDS
    _SHARED_GUIDS = {str(g).strip() for g in (guids or ()) if str(g).strip()}


def configure_guest_slot_names(mapping) -> None:
    """Install the {guid: {season: name}} guest-slot table (guestSlotNames)."""
    global _GUEST_SLOT_NAMES
    _GUEST_SLOT_NAMES = {
        str(g).strip(): {str(s).strip(): v for s, v in (m or {}).items()}
        for g, m in (mapping or {}).items() if str(g).strip()
    }


def set_current_season(season_id: str) -> None:
    """Tell the resolver which season is being processed (for guest slots)."""
    global _CURRENT_SEASON
    _CURRENT_SEASON = season_id or ""


def _guid_is_shared(guid: str) -> bool:
    """A GUID that is not one person: an explicit shared guid or a guest slot."""
    return bool(guid) and (guid in _SHARED_GUIDS or guid in _GUEST_SLOT_NAMES)


def resolve_driver_name(raw_name: str) -> str:
    """Apply display-name alias mapping to a raw driver name."""
    clean = raw_name.strip()
    if not clean:
        return clean
    return _ALIAS_LOOKUP.get(clean.lower(), clean)


def load_server_result(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_driver_name(entry: dict) -> str:
    """Resolve a driver's canonical name from any AC result structure.

    GUID mapping takes precedence over the raw display name, then display-name
    aliases, so a person maps to one league name even if their Steam name
    differs across sessions.
    """
    guid = extract_driver_guid(entry)
    # A guest slot's occupant is decided by which season we're processing.
    if guid and _CURRENT_SEASON:
        season_map = _GUEST_SLOT_NAMES.get(guid)
        if season_map and _CURRENT_SEASON in season_map:
            return season_map[_CURRENT_SEASON]
    if guid and guid in _GUID_NAME_MAP and not _guid_is_shared(guid):
        return _GUID_NAME_MAP[guid]
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


def extract_skin(entry: dict) -> str:
    value = entry.get("Skin")
    return value.strip() if isinstance(value, str) else ""


def extract_team(entry: dict) -> str:
    """Team lives on the Driver sub-object in Cars[] entries."""
    driver = entry.get("Driver")
    if isinstance(driver, dict):
        team = driver.get("Team", "")
        if isinstance(team, str):
            return team.strip()
    team = entry.get("Team")
    return team.strip() if isinstance(team, str) else ""


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
        # A Steam GUID uniquely identifies a person, so it alone is the key —
        # one driver stays one identity even if they occupy two car slots in a
        # session (join, leave, rejoin in a different car). The car id is only a
        # tiebreaker when no GUID is present.
        if self.guid and not _guid_is_shared(self.guid):
            return f"guid:{self.guid}"
        # A shared/guest GUID is not one person: split it by resolved name (a
        # display name, or the season's guest-slot name) so each occupant becomes
        # its own driver.
        if self.guid:
            return f"shared:{self.guid}|name:{self.name.casefold()}"
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
        meta = {
            "driver": ident.name,
            "guid": ident.guid,
            "carId": ident.car_id,
            "car": ident.car_model,
        }
        skin = extract_skin(entry)
        team = extract_team(entry)
        if skin:
            meta["skin"] = skin
        if team:
            meta["team"] = team
        self.meta_by_label[label] = meta
        self.labels.append(label)
        return label

    def enrich_from(self, entry: dict) -> None:
        """Fill in skin/team on an already-registered identity.

        Used for the Cars[] pass: it adds authoritative metadata but never
        creates a new driver (a Cars entry with no laps/result is a no-show).
        """
        ident = extract_driver_identity(entry)
        label = self.by_identity_key.get(ident.identity_key)
        if not label:
            return
        meta = self.meta_by_label[label]
        skin = extract_skin(entry)
        team = extract_team(entry)
        if skin and "skin" not in meta:
            meta["skin"] = skin
        if team and "team" not in meta:
            meta["team"] = team

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


def _as_list(value: Any) -> list:
    """Coerce a possibly-null AC field to a list.

    Real result files sometimes carry ``"Events": null`` / ``"Laps": null``
    rather than an empty array, so ``dict.get(key, [])`` is not enough.
    """
    return value if isinstance(value, list) else []


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
    return _first_lap_grid(laps, registry)


def _first_lap_grid(laps: list, registry: DriverRegistry) -> list[str]:
    """Order drivers by the timestamp of their first valid lap."""
    first_laps: list[tuple[int, str]] = []
    seen: set[str] = set()

    for lap in laps:
        if not _valid_lap_time(lap.get("LapTime", 0)):
            continue
        label = registry.existing_label_for_entry(lap)
        if not label or label in seen:
            continue
        seen.add(label)
        first_laps.append((lap.get("Timestamp", 0), label))

    first_laps.sort(key=lambda x: x[0])
    return [label for _, label in first_laps]


# ---------------------------------------------------------------------------
# Qualifying processing
# ---------------------------------------------------------------------------

def process_qualifying(qual_data: dict) -> dict[str, Any]:
    """Process a qualifying session into structured output."""
    results = _as_list(qual_data.get("Result"))
    laps = _as_list(qual_data.get("Laps"))
    registry = _build_driver_registry(results, laps, _as_list(qual_data.get("Cars")))
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

def process_race(
    race_data: dict,
    grid: list[str] | None = None,
    grid_meta: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Process a single race result JSON into structured output.

    ``grid_meta`` (gridSource / gridConfidence / gridScore) is embedded in the
    output. When grid confidence is low or unknown, positionChanges is nulled
    because it would be derived from an unreliable starting order.
    """
    laps_raw = _as_list(race_data.get("Laps"))
    events_raw = _as_list(race_data.get("Events"))
    results_raw = _as_list(race_data.get("Result"))

    registry = _build_driver_registry(results_raw, laps_raw, _as_list(race_data.get("Cars")), notes)

    grid_provided = grid is not None
    if grid is None:
        grid = _first_lap_grid(laps_raw, registry)

    drivers = _collect_drivers(results_raw, laps_raw, grid, registry)
    laps_by_driver, sectors_by_driver, cuts_by_driver, tyres_by_driver = _build_lap_tables(
        laps_raw, drivers, registry
    )
    positions = _compute_positions(laps_by_driver, drivers)
    position_details = _compute_position_details(laps_by_driver, drivers)
    overtakes = _compute_overtakes(laps_by_driver, drivers)
    contacts = _map_contacts_to_laps(events_raw, laps_raw, registry)
    pace = _compute_pace(laps_by_driver)
    result = _build_result(results_raw, laps_raw, registry)
    driver_meta = _build_driver_metadata(drivers, registry)

    if grid_meta is not None:
        meta = grid_meta
    elif grid_provided:
        meta = {"gridSource": "provided", "gridConfidence": "high", "gridScore": None}
    else:
        meta = {"gridSource": "first-lap-inferred", "gridConfidence": "low", "gridScore": None}
    confidence = meta.get("gridConfidence", "low")
    if confidence in ("high", "medium"):
        pos_changes: dict[str, Any] | None = _compute_position_changes(positions, grid)
    else:
        pos_changes = None

    return {
        "grid": grid,
        "gridSource": meta.get("gridSource"),
        "gridConfidence": confidence,
        "gridScore": meta.get("gridScore"),
        "drivers": driver_meta,
        "laps": laps_by_driver,
        "sectors": sectors_by_driver,
        "cuts": cuts_by_driver,
        "tyres": tyres_by_driver,
        "positions": positions,
        "positionDetails": position_details,
        "positionChanges": pos_changes,
        "overtakes": overtakes,
        "result": result,
        "contacts": contacts,
        "pace": pace,
    }


def build_registry(session_data: dict, notes: list[str] | None = None) -> DriverRegistry:
    """Public helper: build a DriverRegistry from a raw session JSON.

    Seeds from the authoritative Cars[] map first (named entries only), then
    laps and results. Used by grid inference and the ingest layer so every
    consumer shares the same driver labels.
    """
    return _build_driver_registry(
        _as_list(session_data.get("Result")),
        _as_list(session_data.get("Laps")),
        _as_list(session_data.get("Cars")),
        notes,
    )


def _build_driver_registry(
    results: list,
    laps: list,
    cars: list | None = None,
    notes: list[str] | None = None,
) -> DriverRegistry:
    registry = DriverRegistry()

    # Register from laps first so the car a driver actually drove is canonical.
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

    # Cars[] is authoritative for skin/team but must not invent drivers who
    # never participated, so it only enriches existing identities. Multi-GUID
    # slots are flagged as possible driver swaps.
    for car in cars or []:
        if not extract_driver_name(car):
            continue
        if notes is not None:
            guids = _nonempty_guids(car)
            if len(guids) > 1:
                notes.append(
                    f"car {extract_car_id(car)} had multiple GUIDs "
                    f"({', '.join(guids)}) — possible driver swap"
                )
        registry.enrich_from(car)
    return registry


def _nonempty_guids(car: dict) -> list[str]:
    driver = car.get("Driver")
    if not isinstance(driver, dict):
        return []
    guids = driver.get("GuidsList")
    if not isinstance(guids, list):
        return []
    return [str(g).strip() for g in guids if str(g).strip()]


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


def _build_lap_tables(
    laps: list, drivers: list[str], registry: DriverRegistry
) -> tuple[
    dict[str, list[int]],
    dict[str, list[list[int | None]]],
    dict[str, list[int]],
    dict[str, list[str]],
]:
    """Build lap times, sectors, cuts, and tyre compounds in one pass.

    All four tables are built from the same valid-lap filter and iteration
    order so that index N in every table refers to the same lap.
    Invalid sector values are nulled out but their position is preserved.
    """
    laps_by: dict[str, list[int]] = {d: [] for d in drivers}
    sectors_by: dict[str, list[list[int | None]]] = {d: [] for d in drivers}
    cuts_by: dict[str, list[int]] = {d: [] for d in drivers}
    tyres_by: dict[str, list[str]] = {d: [] for d in drivers}

    for lap in laps:
        label = registry.existing_label_for_entry(lap)
        time_ms = lap.get("LapTime", 0)
        if label not in laps_by or not _valid_lap_time(time_ms):
            continue
        laps_by[label].append(time_ms)
        raw_sectors = lap.get("Sectors") or []
        sectors_by[label].append([s if _valid_lap_time(s) else None for s in raw_sectors])
        cuts_by[label].append(_safe_int(lap.get("Cuts")))
        tyres_by[label].append(str(lap.get("Tyre", "") or "").strip())

    active = [d for d, times in laps_by.items() if times]
    return (
        {d: laps_by[d] for d in active},
        {d: sectors_by[d] for d in active},
        {d: cuts_by[d] for d in active},
        {d: tyres_by[d] for d in active},
    )


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


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
    grid: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Tally places gained/lost across the race.

    When a starting grid is supplied, the launch (grid → lap 1) counts as a
    position change. The grid is densified over only the drivers who have
    lap data, so a place is not credited just because a DNS driver vanished.
    """
    grid_pos: dict[str, int] = {}
    if grid:
        dense = [d for d in grid if d in positions]
        grid_pos = {driver: i + 1 for i, driver in enumerate(dense)}

    changes: dict[str, dict[str, int]] = {}
    for driver, pos_list in positions.items():
        seq = [grid_pos[driver], *pos_list] if driver in grid_pos else pos_list
        gained = 0
        lost = 0
        for i in range(1, len(seq)):
            diff = seq[i - 1] - seq[i]
            if diff > 0:
                gained += diff
            elif diff < 0:
                lost += abs(diff)
        changes[driver] = {"gained": gained, "lost": lost, "net": gained - lost}
    return changes


def _compute_overtakes(
    laps_by_driver: dict[str, list[int]], drivers: list[str]
) -> list[dict[str, Any]]:
    """Derive discrete on-track passes from consecutive leader-lap snapshots.

    For each lap transition n -> n+1, any driver A who was behind driver B at
    lap n and ahead of B at lap n+1 records a pass of B. Only counts snapshots
    where both drivers are ``classified`` on both laps, which suppresses pit /
    DNF artifacts. Lapped-traffic passes are not distinguished (documented in
    SCHEMA.md). ``positionsGained`` is A's net places gained over that lap.
    """
    snapshots = _position_snapshots(laps_by_driver, drivers)
    overtakes: list[dict[str, Any]] = []

    for i in range(len(snapshots) - 1):
        before = {r["driver"]: r for r in snapshots[i]}
        after = {r["driver"]: r for r in snapshots[i + 1]}
        leader_lap = snapshots[i + 1][0]["leaderLap"] if snapshots[i + 1] else i + 2

        for driver, a_after in after.items():
            a_before = before.get(driver)
            if a_before is None:
                continue
            if a_before["status"] != "classified" or a_after["status"] != "classified":
                continue
            gained = a_before["position"] - a_after["position"]
            if gained <= 0:
                continue
            for other, b_after in after.items():
                if other == driver:
                    continue
                b_before = before.get(other)
                if b_before is None:
                    continue
                if b_before["status"] != "classified" or b_after["status"] != "classified":
                    continue
                # A was behind B, now ahead of B → A passed B.
                if a_before["position"] > b_before["position"] and a_after["position"] < b_after["position"]:
                    overtakes.append({
                        "lap": leader_lap,
                        "driver": driver,
                        "passed": other,
                        "positionsGained": gained,
                    })

    return overtakes


# ---------------------------------------------------------------------------
# Contact mapping
# ---------------------------------------------------------------------------

def _map_contacts_to_laps(
    events: list, laps: list, registry: DriverRegistry
) -> list[dict[str, Any]]:
    """Map collision events to lap numbers using timestamps.

    Driver attribution and lap boundaries both flow through the registry so
    that contacts use the same driver labels (e.g. "Alex (car 2)") as the
    rest of the output, even when two drivers share a display name.
    """
    lap_boundaries = _build_lap_boundaries(laps, registry)

    contacts: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("Type", "")
        if event_type != "COLLISION_WITH_CAR":
            continue

        driver1 = _resolve_event_label(registry, _event_party_entry(event, "driver"))
        driver2 = _resolve_event_label(registry, _event_party_entry(event, "other"))
        impact = event.get("ImpactSpeed", 0.0)

        if not driver1 or not driver2:
            continue

        lap_num, lap_confidence = _estimate_lap_for_contact(event, driver1, lap_boundaries)

        contact = {
            "lap": lap_num,
            "lapConfidence": lap_confidence,
            "driver1": driver1,
            "driver2": driver2,
            "impactSpeed": round(impact, 1),
        }
        world = _world_position(event)
        if world is not None:
            contact["worldPosition"] = world
        contacts.append(contact)

    return contacts


def _world_position(event: dict) -> dict[str, float] | None:
    """Extract collision location (x, z ground plane) for incident mapping."""
    pos = event.get("WorldPosition")
    if not isinstance(pos, dict):
        return None
    x = pos.get("X")
    z = pos.get("Z")
    if not isinstance(x, (int, float)) or not isinstance(z, (int, float)):
        return None
    return {"x": round(float(x), 1), "z": round(float(z), 1)}


def _event_party_entry(event: dict, which: str) -> dict:
    """Build a registry-compatible entry for one side of a collision event."""
    if which == "driver":
        party = event.get("Driver", {})
        car_id = event.get("CarId")
    else:
        party = event.get("OtherDriver", {})
        car_id = event.get("OtherCarId")

    if isinstance(party, dict):
        name = party.get("Name", "")
        guid = party.get("Guid", "")
    elif isinstance(party, str):
        name = party
        guid = ""
    else:
        name = ""
        guid = ""

    return {"DriverName": name, "DriverGuid": guid, "CarId": car_id}


def _resolve_event_label(registry: DriverRegistry, party_entry: dict) -> str:
    """Resolve a collision party to a registry label, falling back to name."""
    name = extract_driver_name(party_entry)
    if not name:
        return ""

    label = registry.existing_label_for_entry(party_entry)
    if label:
        return label

    candidates = registry.labels_for_grid_value(name)
    if len(candidates) == 1:
        return candidates[0]

    car_id = extract_car_id(party_entry)
    if car_id is not None:
        for candidate in candidates:
            if registry.meta_by_label.get(candidate, {}).get("carId") == car_id:
                return candidate

    return name


def _build_lap_boundaries(
    laps: list, registry: DriverRegistry
) -> dict[str, list[tuple[int, int]]]:
    """Build per-driver list of (lap_start_ts, lap_end_ts) from lap records."""
    by_driver: dict[str, list[dict]] = {}
    for lap in laps:
        label = registry.existing_label_for_entry(lap)
        if not label:
            continue
        by_driver.setdefault(label, []).append(lap)

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

    # A driver who used two car slots has two result entries under one label.
    # Keep the entry that represents their real finish (a valid total time wins,
    # then a valid best lap), and remember where it sat in the finishing order.
    best: dict[str, tuple[tuple[int, int], int, dict]] = {}
    for idx, entry in enumerate(results):
        label = registry.existing_label_for_entry(entry)
        if not label:
            continue
        has_laps = lap_counts.get(label, 0) > 0
        if not _result_entry_has_evidence(entry, has_laps):
            continue
        score = _result_evidence_score(entry)
        current = best.get(label)
        if current is None or score > current[0]:
            best[label] = (score, idx, entry)

    output: list[dict[str, Any]] = []
    for label, (_score, _idx, entry) in sorted(best.items(), key=lambda kv: kv[1][1]):
        best_lap = entry.get("BestLap", 0)
        meta = registry.meta_by_label.get(label, {})
        output.append({
            "driver": meta.get("driver", label),
            "driverKey": label,
            "guid": meta.get("guid", ""),
            "carId": meta.get("carId"),
            "car": extract_car_model(entry) or meta.get("car", ""),
            "position": len(output) + 1,
            "totalTimeMs": entry.get("TotalTime", 0),
            "bestLapMs": best_lap if _valid_lap_time(best_lap) else 0,
            "laps": lap_counts.get(label, 0),
            "ballast": entry.get("BallastKG", 0),
            "restrictor": entry.get("Restrictor", 0),
        })
    return output


def _result_evidence_score(entry: dict) -> tuple[int, int]:
    """Rank a result entry's completeness: real total time first, then best lap."""
    total_time = entry.get("TotalTime", 0)
    best_lap = entry.get("BestLap", 0)
    has_total = 1 if isinstance(total_time, (int, float)) and total_time > 0 else 0
    has_best = 1 if _valid_lap_time(best_lap) else 0
    return (has_total, has_best)


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
# Practice processing
# ---------------------------------------------------------------------------

def process_practice(practice_data: dict) -> dict[str, Any]:
    """Lightweight practice summary: participants, best laps, lap counts."""
    results = _as_list(practice_data.get("Result"))
    laps = _as_list(practice_data.get("Laps"))
    registry = _build_driver_registry(results, laps, _as_list(practice_data.get("Cars")))

    best_laps: dict[str, int] = {}
    lap_counts: dict[str, int] = {}
    for lap in laps:
        label = registry.existing_label_for_entry(lap)
        lap_time = lap.get("LapTime", 0)
        if not label or not _valid_lap_time(lap_time):
            continue
        lap_counts[label] = lap_counts.get(label, 0) + 1
        if label not in best_laps or lap_time < best_laps[label]:
            best_laps[label] = lap_time

    participants = list(lap_counts)
    return {
        "participants": participants,
        "drivers": _build_driver_metadata(participants, registry),
        "bestLaps": best_laps,
        "lapCounts": lap_counts,
    }
