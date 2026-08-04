"""Car-class resolution for multi-class seasons.

Some seasons run two car classes in one field (S14 GT3 + Street, S18a/b and
S24a Hypercar + GT3). Which car belongs to which class is a league rule, not a
fact recoverable from the logs, so it is declared per season in config.json —
never hardcoded here. The car packs change between seasons, so a pattern that
holds for S18 says nothing about S26.

Class is resolved **once per driver per season**, from the car they used most.
The league's rule is that drivers do not switch cars mid-season, so this is
both correct and more robust than resolving per race: it survives the odd race
where the server logged a blank car model. Genuine exceptions get a byDriver
override; a driver whose cars straddle two classes is reported rather than
silently collapsed.

Championship mode is independent of class. S18a/b ran two classes under a
single WDC; S14 and S24a run a WDC per class. ``classPosition`` is emitted for
every multi-class season either way — it is useful for analysis even when the
championship is combined — but it is derived from on-track order only. The
hand-audited archive remains the truth source for standings.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

# A driver needs to appear in at least this share of their season's races for a
# straddling car history to be worth reporting. Below it, a one-off appearance
# in a borrowed car is noise rather than a config error.
_MIN_RACES_FOR_STRADDLE_WARNING = 2


@dataclass(frozen=True)
class SeasonClassSpec:
    """Declared class structure for one season."""

    championship: str = "combined"  # "combined" (one WDC) | "split" (WDC per class)
    order: tuple[str, ...] = ()  # display order, fastest class first
    by_car_model: dict[str, tuple[str, ...]] = field(default_factory=dict)
    by_driver: dict[str, str] = field(default_factory=dict)
    fallback: str = ""  # class for models matching no pattern

    @property
    def is_split(self) -> bool:
        return self.championship == "split"

    def class_names(self) -> list[str]:
        """Declared classes in display order, including any fallback."""
        names = list(self.order)
        for name in list(self.by_car_model) + ([self.fallback] if self.fallback else []):
            if name not in names:
                names.append(name)
        return names

    def match_model(self, car_model: str) -> str:
        """Class for a car model, or "" if nothing matches and no fallback."""
        model = (car_model or "").strip().casefold()
        if not model:
            return ""
        for name in self.class_names():
            for pattern in self.by_car_model.get(name, ()):
                if fnmatch.fnmatch(model, pattern.casefold()):
                    return name
        return self.fallback


def parse_spec(raw: Any) -> SeasonClassSpec:
    """Build a spec from its config.json form. Unknown shapes degrade to empty."""
    if not isinstance(raw, dict):
        return SeasonClassSpec()
    championship = str(raw.get("championship", "combined")).strip().casefold()
    if championship not in ("combined", "split"):
        championship = "combined"
    by_car_model = {
        str(name): tuple(str(p) for p in patterns)
        for name, patterns in (raw.get("byCarModel") or {}).items()
    }
    return SeasonClassSpec(
        championship=championship,
        order=tuple(str(x) for x in (raw.get("order") or ())),
        by_car_model=by_car_model,
        by_driver={str(d): str(c) for d, c in (raw.get("byDriver") or {}).items()},
        fallback=str(raw.get("fallback", "")).strip(),
    )


def _driver_car_counts(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """How many races each driver ran each car model, across the season."""
    counts: dict[str, dict[str, int]] = {}
    for event in events:
        for race in (event.get("races") or {}).values():
            for label, meta in (race.get("drivers") or {}).items():
                model = (meta.get("car") or "").strip()
                if not model:
                    continue
                counts.setdefault(label, {})
                counts[label][model] = counts[label].get(model, 0) + 1
    return counts


def resolve_driver_classes(
    spec: SeasonClassSpec,
    events: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Map each driver label to a class for the whole season.

    Returns (label -> class, warnings). A driver with no matching car and no
    fallback is left out of the map entirely rather than assigned a guess.
    """
    warnings: list[str] = []
    counts = _driver_car_counts(events)
    resolved: dict[str, str] = {}
    unmatched: dict[str, set[str]] = {}

    for label in sorted(counts):
        models = counts[label]
        override = spec.by_driver.get(label)
        if override:
            resolved[label] = override
            continue

        by_class: dict[str, int] = {}
        for model, n in models.items():
            name = spec.match_model(model)
            if not name:
                unmatched.setdefault(model, set()).add(label)
                continue
            by_class[name] = by_class.get(name, 0) + n
        if not by_class:
            continue

        # Most-raced class wins; declared order breaks ties deterministically.
        order = spec.class_names()
        winner = max(
            by_class,
            key=lambda c: (by_class[c], -(order.index(c) if c in order else len(order))),
        )
        resolved[label] = winner

        if len(by_class) > 1 and sum(by_class.values()) >= _MIN_RACES_FOR_STRADDLE_WARNING:
            detail = ", ".join(f"{c}x{n}" for c, n in sorted(by_class.items()))
            warnings.append(
                f"{label} raced more than one class ({detail}) — using {winner}. "
                f"Add a byDriver override if that is wrong."
            )

    for model, labels in sorted(unmatched.items()):
        who = ", ".join(sorted(labels))
        warnings.append(
            f"car model '{model}' matches no class pattern (drivers: {who}) — "
            f"add a pattern or a fallback class."
        )

    for label, name in spec.by_driver.items():
        if label not in counts:
            warnings.append(f"byDriver override for '{label}' but they raced no races this season.")
        elif name not in spec.class_names():
            warnings.append(f"byDriver override sends '{label}' to undeclared class '{name}'.")

    return resolved, warnings


def apply_classes(
    events: list[dict[str, Any]],
    driver_classes: dict[str, str],
) -> None:
    """Annotate events in place with per-driver class and class position.

    ``position`` is left alone — it stays the overall on-track order, which is
    what the lap and gap data describe. ``classPosition`` is added alongside.
    """
    for event in events:
        for session in list((event.get("races") or {}).values()) + \
                       list((event.get("qualifying") or {}).values()):
            for label, meta in (session.get("drivers") or {}).items():
                name = driver_classes.get(label)
                if name:
                    meta["class"] = name

        for race in (event.get("races") or {}).values():
            seen: dict[str, int] = {}
            for row in race.get("result") or []:
                name = driver_classes.get(row.get("driverKey"))
                if not name:
                    row.pop("classPosition", None)
                    continue
                seen[name] = seen.get(name, 0) + 1
                row["classPosition"] = seen[name]


def season_class_block(
    spec: SeasonClassSpec,
    driver_classes: dict[str, str],
) -> dict[str, Any] | None:
    """The season-level ``classes`` record, or None for a single-class season."""
    present = [c for c in spec.class_names() if c in set(driver_classes.values())]
    if len(present) < 2:
        return None
    return {
        "championship": spec.championship,
        "order": present,
        "drivers": dict(sorted(driver_classes.items())),
    }
