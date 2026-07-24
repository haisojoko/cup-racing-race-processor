"""Starting-grid inference for races.

The AC result files don't record the grid, so it is inferred by comparing each
race's lap-1 crossing order against candidate orders (a preceding qualifying
grid, the previous race's finish, and that finish reversed). Kendall's tau
scores each candidate; the best one wins and its confidence is recorded so
downstream stats can be nulled when the grid is a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from .processor import INVALID_LAP_TIME, DriverRegistry, build_registry

MIN_COMMON = 3
TIE_MARGIN = 0.05


@dataclass
class GridDecision:
    grid: list[str]
    source: str  # qualifying | previous-race | reversed-previous | first-lap-inferred | unknown
    confidence: str  # high | medium | low | unknown
    score: float | None

    def as_meta(self) -> dict:
        return {
            "gridSource": self.source,
            "gridConfidence": self.confidence,
            "gridScore": round(self.score, 3) if self.score is not None else None,
        }


def _valid(t) -> bool:
    return isinstance(t, (int, float)) and 0 < t < INVALID_LAP_TIME


def lap1_crossing_order(race_data: dict, registry: DriverRegistry) -> list[str]:
    """Order drivers by the timestamp of their earliest lap.

    Includes invalid laps: a driver who crashed on lap 1 still crossed the
    start line and belongs in the observed order.
    """
    earliest: dict[str, int] = {}
    for lap in (race_data.get("Laps") or []):
        label = registry.existing_label_for_entry(lap)
        if not label:
            continue
        ts = lap.get("Timestamp", 0)
        if not isinstance(ts, (int, float)):
            continue
        if label not in earliest or ts < earliest[label]:
            earliest[label] = ts
    return [label for label, _ in sorted(earliest.items(), key=lambda kv: kv[1])]


def kendall_tau(order_a: list[str], order_b: list[str]) -> float | None:
    """Kendall tau correlation over drivers common to both orders.

    Returns a value in [-1, 1]; +1 identical, -1 reversed. None if fewer than
    two common drivers (no pairs to compare).
    """
    common = [d for d in order_a if d in order_b]
    if len(common) < 2:
        return None
    rank_b = {d: i for i, d in enumerate(order_b)}
    rank_a = {d: i for i, d in enumerate(order_a)}
    concordant = 0
    discordant = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            x, y = common[i], common[j]
            a_order = rank_a[x] - rank_a[y]
            b_order = rank_b[x] - rank_b[y]
            if a_order * b_order > 0:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total


def _confidence(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


CLEAR_MARGIN = 0.15  # standard-vs-reverse must win by this to be trusted at low tau


def infer_grid(
    race_data: dict,
    *,
    fresh_quali_grid: list[str] | None = None,
    previous_finish: list[str] | None = None,
    registry: DriverRegistry | None = None,
    forced_reverse: bool = False,
    qualifying_grid: list[str] | None = None,  # back-compat alias
) -> GridDecision:
    """Infer a race's starting grid.

    ``fresh_quali_grid``: the grid of a qualifying session that ran immediately
    before this race (after any previous race). In this league a fresh
    qualifying deterministically sets the next race's grid, so it is trusted
    directly — lap-1 crossing order is only a noisy sanity signal.

    ``previous_finish``: finish order of the preceding race, used to decide
    standard vs reverse grid when no fresh qualifying precedes this race.

    ``forced_reverse``: the league's known format says this race is a reverse
    grid (e.g. R2/R4 of a reverse-grid season). The grid is then the previous
    finish reversed, taken on trust rather than guessed — so it no longer
    degrades to a low-confidence lap-1 fallback. If the previous finish is
    unavailable (missing data), we fall through to normal inference.
    """
    if fresh_quali_grid is None:
        fresh_quali_grid = qualifying_grid
    if registry is None:
        registry = build_registry(race_data)
    observed = lap1_crossing_order(race_data, registry)

    # Known reverse-grid race: reverse the (reliable) previous finish directly.
    if forced_reverse and previous_finish and len(previous_finish) >= MIN_COMMON:
        reversed_finish = list(reversed(previous_finish))
        tau = kendall_tau(observed, reversed_finish)
        # Trust the format; lap-1 order only nuances confidence (post-reverse
        # the fast cars scythe forward, so agreement is naturally imperfect).
        confidence = "high" if (tau is None or tau >= 0.2) else "medium"
        grid = _grid_from_candidate(reversed_finish, observed)
        return GridDecision(grid, "reversed-previous", confidence, tau)

    # A qualifying immediately before the race sets the grid — trust it.
    if fresh_quali_grid:
        tau = kendall_tau(observed, fresh_quali_grid)
        # High confidence unless lap-1 order actively contradicts it.
        confidence = "high" if (tau is None or tau >= 0.3) else "medium"
        grid = _grid_from_candidate(fresh_quali_grid, observed)
        return GridDecision(grid, "qualifying", confidence, tau)

    # No fresh qualifying: decide standard vs reverse from the previous race.
    if previous_finish and len(previous_finish) >= MIN_COMMON:
        reversed_finish = list(reversed(previous_finish))
        tau_std = kendall_tau(observed, previous_finish)
        tau_rev = kendall_tau(observed, reversed_finish)
        best = _pick_std_or_reverse(previous_finish, reversed_finish, tau_std, tau_rev, observed)
        if best is not None:
            return best

    if observed:
        return GridDecision(observed, "first-lap-inferred", "low", None)
    return GridDecision([], "unknown", "unknown", None)


def _pick_std_or_reverse(
    previous_finish: list[str],
    reversed_finish: list[str],
    tau_std: float | None,
    tau_rev: float | None,
    observed: list[str],
) -> GridDecision | None:
    if tau_std is None and tau_rev is None:
        return None
    tau_std = -2.0 if tau_std is None else tau_std
    tau_rev = -2.0 if tau_rev is None else tau_rev

    if tau_rev > tau_std:
        source, order, tau, other = "reversed-previous", reversed_finish, tau_rev, tau_std
    else:
        source, order, tau, other = "previous-race", previous_finish, tau_std, tau_rev

    if tau >= 0.5:
        confidence = _confidence(tau)
    elif tau >= 0.2 and (tau - other) >= CLEAR_MARGIN:
        # Modest absolute agreement, but a clear winner over the alternative.
        confidence = "medium"
    else:
        return None  # ambiguous → caller falls back to first-lap-inferred

    return GridDecision(_grid_from_candidate(order, observed), source, confidence, tau)


def _grid_from_candidate(order: list[str], observed: list[str]) -> list[str]:
    """Grid = candidate order, with any race-present drivers missing from the
    candidate appended in observed order."""
    grid = list(order)
    present = set(order)
    for d in observed:
        if d not in present:
            grid.append(d)
            present.add(d)
    return grid
