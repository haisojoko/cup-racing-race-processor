"""Post-round recap: per-driver encouraging weekend cards from the dataset.

Reads the additive dataset the processor already writes (no extra data, no
external deps) and, for one venue weekend, finds the single most meaningful
*true* thing each driver did — then two supporting facts. Output is clipped,
factual Markdown, ready to paste into a Discord DM.

Design rules (see the league's recap notes):

- **Positive-only by construction.** We only ever create an anecdote for a good
  fact. There is no code path that says "you lost places" or "you were slowest".
- **Self- or cohort-relative, never field-relative.** A driver is measured
  against their own history or their midfield peers, never against the alien at
  the front — otherwise the dominant driver sweeps every superlative and the
  midfield feels worse. Field-wide "crowns" are cohort-scoped (the pack, i.e.
  drivers who finished outside the overall podium) and so never land on the
  runaway leader.
- **One headline + two supports.** Each card leads with the highest-impact fact,
  then two facts from *different* categories. Everything else goes under "More".
- **Honest.** Every line traces to a dataset field. Places-gained is skipped when
  the grid could not be trusted (`positionChanges is null`). Consistency is
  measured on sanitised laps. `contacts` counts collisions, not fault, so a high
  count is never framed as a negative — only a clean sheet is mentioned.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

_SEASON_RE = re.compile(r"^S(\d+)([a-z]?)$", re.IGNORECASE)


def _season_sort_key(name: str) -> tuple[int, str]:
    m = _SEASON_RE.match(name)
    return (int(m.group(1)), m.group(2).lower()) if m else (9999, name)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _load_seasons(dataset_dir: Path) -> dict[str, dict]:
    seasons_dir = dataset_dir / "seasons"
    out: dict[str, dict] = {}
    for p in sorted(seasons_dir.glob("*.json"), key=lambda p: _season_sort_key(p.stem)):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
    return out


def _ordered_season_ids(seasons: dict[str, dict]) -> list[str]:
    return sorted(seasons, key=_season_sort_key)


def _races_in_order(event: dict) -> list[tuple[str, dict]]:
    """Event races as (key, race) in chronological order (keys are '1','2',...)."""
    races = event.get("races") or {}
    return sorted(races.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def _fmt_laptime(ms: Optional[int]) -> str:
    if not ms:
        return "—"
    s = ms / 1000.0
    return f"{int(s // 60)}:{s % 60:06.3f}"


def _fmt_gap(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    return f"{ms / 1000.0:.3f}s"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "round"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _eff_pos(entry: dict) -> Optional[int]:
    """Class position when present (multi-class), else overall position."""
    cp = entry.get("classPosition")
    return cp if cp is not None else entry.get("position")


# --------------------------------------------------------------------------- #
# Career / season index (built once, read per driver)
# --------------------------------------------------------------------------- #

@dataclass
class RaceRow:
    season: str
    order: tuple[int, str]       # season sort key
    venue_order: int
    venue: str
    race_key: str
    pos: Optional[int]           # overall
    eff: Optional[int]           # class-aware finishing position
    best_lap: Optional[int]


def _build_history(seasons: dict[str, dict]) -> dict[str, list[RaceRow]]:
    """driver label -> chronological list of every race row they appear in."""
    hist: dict[str, list[RaceRow]] = {}
    for sid in _ordered_season_ids(seasons):
        sdata = seasons[sid]
        key = _season_sort_key(sid)
        for ev in sdata.get("events", []):
            vo = ev.get("venueOrder", 0)
            venue = ev.get("venue", "")
            for rk, race in _races_in_order(ev):
                for e in race.get("result") or []:
                    label = e.get("driver")
                    if not label:
                        continue
                    hist.setdefault(label, []).append(RaceRow(
                        season=sid, order=key, venue_order=vo, venue=venue,
                        race_key=rk, pos=e.get("position"), eff=_eff_pos(e),
                        best_lap=e.get("bestLapMs"),
                    ))
    for rows in hist.values():
        rows.sort(key=lambda r: (r.order, r.venue_order, int(r.race_key) if r.race_key.isdigit() else 999))
    return hist


def _driver_strength(hist: dict[str, list[RaceRow]]) -> dict[str, float]:
    """Mean class-aware finish across all history — lower = stronger. Used to
    judge the quality of an overtake ('you passed a front-runner')."""
    out: dict[str, float] = {}
    for label, rows in hist.items():
        effs = [r.eff for r in rows if r.eff]
        if effs:
            out[label] = sum(effs) / len(effs)
    return out


# --------------------------------------------------------------------------- #
# Per-weekend aggregation
# --------------------------------------------------------------------------- #

@dataclass
class Weekend:
    driver: str
    team: Optional[str] = None
    finishes: list[int] = field(default_factory=list)
    best_finish: Optional[int] = None
    starts: list[int] = field(default_factory=list)
    net_gain: Optional[int] = None            # None when never trustworthy
    overtakes: int = 0
    overtakes_suffered: int = 0
    passes_held: list[tuple[str, str]] = field(default_factory=list)  # (passed, race_key), finished ahead
    contacts: int = 0
    cuts: int = 0
    best_lap: Optional[int] = None
    laps: int = 0
    cv: Optional[float] = None                # sanitised lap-time coefficient of variation (%)
    lap_sd_ms: Optional[float] = None         # the same consistency in absolute terms (std-dev, ms)
    per_race_best: list[tuple[str, Optional[int]]] = field(default_factory=list)  # (race_key, bestLapMs)
    nearest: Optional[tuple[str, float, str, bool]] = None  # (rival, gap_ms, race_key, rival_ahead)


def _lap_spread(all_laps: list[int]) -> Optional[tuple[float, float]]:
    """Sanitised lap-time consistency as (coefficient of variation %, std-dev ms).

    Drops the out-lap and anything over 120% of the median (incidents, traffic)
    before measuring — same idea as the dataset's ``pace`` field. Returns both
    the percentage (comparable across tracks) and the absolute spread in ms, so
    a card can show "3.9% (±0.42s)".
    """
    laps = [x for x in all_laps if x]
    if len(laps) < 5:
        return None
    med = statistics.median(laps)
    kept = [x for i, x in enumerate(laps) if i != 0 and x <= 1.2 * med]
    if len(kept) < 5:
        kept = laps
    mean = statistics.mean(kept)
    if not mean:
        return None
    sd = statistics.pstdev(kept)
    return sd / mean * 100, sd


def _mean_per_race_spread(per_race_laps: list[list[int]]) -> Optional[tuple[float, float]]:
    """Consistency as the mean of each race's spread (the track-agnostic measure
    cri uses). Pooling laps across separate races would inflate the spread with
    between-race differences (fuel, leading vs chasing), so we average per race."""
    cvs, sds = [], []
    for laps in per_race_laps:
        spread = _lap_spread(laps)
        if spread:
            cvs.append(spread[0])
            sds.append(spread[1])
    if not cvs:
        return None
    return sum(cvs) / len(cvs), sum(sds) / len(sds)


def _nearest_rival(race: dict, label: str) -> Optional[tuple[str, float, bool]]:
    """Closest classified finisher to `label` on the same lap count.

    Returns (rival, gap_ms, rival_ahead) — ``rival_ahead`` is True when the rival
    finished in front (so the driver was *behind* them), False when the driver
    held the rival off. Direction matters: never tell someone they finished
    "behind" a driver they actually beat.
    """
    result = [e for e in (race.get("result") or []) if e.get("position")]
    result.sort(key=lambda e: e["position"])
    idx = next((i for i, e in enumerate(result) if e.get("driver") == label), None)
    if idx is None:
        return None
    me = result[idx]
    best: Optional[tuple[str, float, bool]] = None
    for j in (idx - 1, idx + 1):
        if 0 <= j < len(result):
            other = result[j]
            if (other.get("laps") == me.get("laps")
                    and other.get("totalTimeMs") and me.get("totalTimeMs")):
                gap = abs(other["totalTimeMs"] - me["totalTimeMs"])
                if best is None or gap < best[1]:
                    best = (other["driver"], gap, j < idx)  # j<idx → rival ahead
    return best


def _aggregate_weekend(event: dict) -> dict[str, Weekend]:
    races = _races_in_order(event)
    W: dict[str, Weekend] = {}

    def w(label: str) -> Weekend:
        if label not in W:
            W[label] = Weekend(driver=label)
        return W[label]

    per_race_laps: dict[str, list[list[int]]] = {}
    net_seen: dict[str, bool] = {}

    for rk, race in races:
        result = race.get("result") or []
        finish_pos = {e["driver"]: e.get("position") for e in result if e.get("driver")}
        drivers_meta = race.get("drivers") or {}
        grid = race.get("grid") or []
        pc = race.get("positionChanges")
        overtakes = race.get("overtakes") or []
        contacts = race.get("contacts") or []
        cuts = race.get("cuts") or {}
        laps_map = race.get("laps") or {}

        for e in result:
            label = e.get("driver")
            if not label:
                continue
            ww = w(label)
            ep = _eff_pos(e)          # class position on multi-class seasons, else overall
            if ep:
                ww.finishes.append(ep)
            if e.get("bestLapMs"):
                ww.best_lap = e["bestLapMs"] if ww.best_lap is None else min(ww.best_lap, e["bestLapMs"])
            ww.per_race_best.append((rk, e.get("bestLapMs")))
            if drivers_meta.get(label, {}).get("team"):
                ww.team = drivers_meta[label]["team"]

        for i, label in enumerate(grid):
            w(label).starts.append(i + 1)

        if pc:
            for label, ch in pc.items():
                if ch and ch.get("net") is not None:
                    ww = w(label)
                    ww.net_gain = (ww.net_gain or 0) + ch["net"]
                    net_seen[label] = True

        for o in overtakes:
            drv, passed = o.get("driver"), o.get("passed")
            if drv:
                w(drv).overtakes += 1
                # a pass "held" if the passer finished ahead of the passed in this race
                if (passed and finish_pos.get(drv) and finish_pos.get(passed)
                        and finish_pos[drv] < finish_pos[passed]):
                    w(drv).passes_held.append((passed, rk))
            if passed:
                w(passed).overtakes_suffered += 1

        for c in contacts:
            for k in ("driver1", "driver2"):
                if c.get(k):
                    w(c[k]).contacts += 1

        for label, arr in cuts.items():
            w(label).cuts += sum(x for x in arr if x)

        for label, arr in laps_map.items():
            per_race_laps.setdefault(label, []).append([x for x in arr if x])

        # nearest rival: keep the tightest across the weekend
        for label in finish_pos:
            near = _nearest_rival(race, label)
            if near:
                ww = w(label)
                if ww.nearest is None or near[1] < ww.nearest[1]:
                    ww.nearest = (near[0], near[1], rk, near[2])

    for label, ww in W.items():
        ww.net_gain = ww.net_gain if net_seen.get(label) else None
        ww.best_finish = min(ww.finishes) if ww.finishes else None
        races_laps = per_race_laps.get(label, [])
        ww.laps = sum(len(r) for r in races_laps)
        spread = _mean_per_race_spread(races_laps)
        ww.cv, ww.lap_sd_ms = spread if spread else (None, None)
    return W


# --------------------------------------------------------------------------- #
# Anecdotes
# --------------------------------------------------------------------------- #

@dataclass
class Anecdote:
    kind: str
    category: str   # progress | competence | relatedness | milestone | distinction | floor
    impact: float
    text: str


def _career_before(rows: list[RaceRow], target: tuple[int, str], target_vo: int) -> list[RaceRow]:
    return [r for r in rows if (r.order, r.venue_order) < (target, target_vo)]


def _season_before(rows: list[RaceRow], sid: str, target_vo: int) -> list[RaceRow]:
    return [r for r in rows if r.season == sid and r.venue_order < target_vo]


def _cohort(W: dict[str, Weekend]) -> set[str]:
    """The pack: everyone whose weekend-best finish is outside the overall top 3."""
    return {d for d, ww in W.items() if (ww.best_finish or 99) > 3}


def build_anecdotes(
    label: str,
    ww: Weekend,
    hist: list[RaceRow],
    strength: dict[str, float],
    W: dict[str, Weekend],
    cohort: set[str],
    sid: str,
    target_order: tuple[int, str],
    target_vo: int,
    venue: str,
    field_size: int,
) -> list[Anecdote]:
    out: list[Anecdote] = []
    best = ww.best_finish
    career = _career_before(hist, target_order, target_vo)
    season = _season_before(hist, sid, target_vo)
    prior_effs = [r.eff for r in career if r.eff]
    prior_best = min(prior_effs) if prior_effs else None

    # --- Milestones (tier 1-2) ---------------------------------------------- #
    if best is not None:
        # a win/sweep is the story of the weekend — it outranks places-gained,
        # so a reverse-grid winner never headlines "recovered from the back".
        wins = ww.finishes.count(1)
        if wins == len(ww.finishes) and wins > 1:
            out.append(Anecdote("sweep", "milestone", 96,
                                f"Clean sweep — won all {wins} races at {venue}."))
        elif wins > 1:
            out.append(Anecdote("wins", "milestone", 91, f"{wins} race wins at {venue}."))
        elif wins == 1:
            out.append(Anecdote("win", "milestone", 91, f"Race win — P1 at {venue}."))
        # career firsts — pick the strongest threshold newly reached
        thresholds = [(1, "win", 100), (3, "podium", 92), (5, "top-5", 86), (10, "top-10", 74)]
        for thr, name, impact in thresholds:
            if best <= thr and not any(e <= thr for e in prior_effs):
                out.append(Anecdote("career_first", "milestone", impact,
                                    f"Career-first {name} — P{best}."))
                break
        # career-best finish (strict improvement, and not already covered by a first)
        if prior_best is not None and best < prior_best:
            out.append(Anecdote("career_best", "milestone", 88,
                                f"Career-best finish: P{best} (previous best P{prior_best})."))
        # season-best-so-far
        season_effs = [r.eff for r in season if r.eff]
        if season_effs and best < min(season_effs):
            out.append(Anecdote("season_best", "milestone", 78,
                                f"Best result of {sid} so far — P{best}."))

    # --- Progress ----------------------------------------------------------- #
    avg_start = statistics.mean(ww.starts) if ww.starts else None
    if ww.net_gain and ww.net_gain > 0 and (avg_start is None or avg_start > field_size * 0.33):
        out.append(Anecdote("places_gained", "progress", 55 + ww.net_gain * 2,
                            f"Net +{ww.net_gain} places across the weekend."))
    # found time through the day
    reals = [(rk, ms) for rk, ms in ww.per_race_best if ms]
    if len(reals) >= 2 and reals[0][1] - reals[-1][1] >= 300:
        out.append(Anecdote("built_in", "progress", 47,
                            f"Found time through the day: best lap {_fmt_laptime(reals[0][1])} "
                            f"→ {_fmt_laptime(reals[-1][1])}."))
    # NOTE: no "personal-best lap at this venue" anecdote. The car changes every
    # season, so lap times aren't comparable across a driver's history — a faster
    # car in a later season would read as a false "personal best". Within-weekend
    # lap improvement ("found time", same car/track) is the honest version above.

    # --- Competence --------------------------------------------------------- #
    if ww.passes_held:
        # the biggest scalp held to the flag
        def scalp(p: tuple[str, str]) -> float:
            return -strength.get(p[0], 99)  # stronger rival (lower avg) = better scalp
        passed, rk = max(ww.passes_held, key=scalp)
        out.append(Anecdote("signature_pass", "competence", 62,
                            f"Passed {passed} in race {rk} and finished ahead."))
    if ww.overtakes >= 3:
        out.append(Anecdote("overtakes", "competence", 50 + ww.overtakes * 1.2,
                            f"{ww.overtakes} on-track passes across the weekend."))
    if ww.contacts == 0 and ww.cuts <= 2 and ww.laps >= 5:
        out.append(Anecdote("clean", "competence", 45,
                            "Clean weekend — no contact, no cut corners."))
    if ww.cv is not None and ww.cv <= 1.5:
        sd_s = (ww.lap_sd_ms or 0) / 1000.0
        out.append(Anecdote("consistency", "competence", 48,
                            f"Metronomic pace — lap-time spread {ww.cv:.1f}% (±{sd_s:.2f}s)."))

    # --- Relatedness -------------------------------------------------------- #
    if ww.nearest:
        rival, gap, rk, rival_ahead = ww.nearest
        tight = max(0.0, 30.0 - min(gap / 1000.0, 3.0) * 10.0)
        if gap <= 500:
            text = (f"Photo finish: {_fmt_gap(gap)} behind {rival} at the line (race {rk})."
                    if rival_ahead else
                    f"Held off {rival} by {_fmt_gap(gap)} at the line (race {rk}).")
            out.append(Anecdote("photo_finish", "relatedness", 60 + tight, text))
        else:
            out.append(Anecdote("close_battle", "relatedness", 34 + tight,
                                f"Closest scrap: {_fmt_gap(gap)} to {rival} (race {rk})."))
    if ww.team:
        mates = [d for d, o in W.items() if o.team == ww.team]
        if len(mates) >= 2 and best is not None and best == min((W[d].best_finish or 99) for d in mates):
            out.append(Anecdote("team_top", "relatedness", 42,
                                f"Top finisher for {ww.team} this round."))

    # --- Distinction (cohort-scoped crowns) --------------------------------- #
    if label in cohort:
        pack_ot = {d: W[d].overtakes for d in cohort}
        if ww.overtakes >= 3 and ww.overtakes == max(pack_ot.values()):
            out.append(Anecdote("pack_overtakes", "distinction", 52,
                                f"Most on-track passes in the midfield ({ww.overtakes})."))
        pack_net = {d: (W[d].net_gain or 0) for d in cohort}
        if (ww.net_gain or 0) > 0 and ww.net_gain == max(pack_net.values()):
            out.append(Anecdote("pack_mover", "distinction", 53,
                                f"Biggest climber in the midfield (+{ww.net_gain})."))

    # --- Floor (always resolvable) ------------------------------------------ #
    tenure = len(career) + len(ww.finishes)
    if tenure and (tenure % 25 == 0 or tenure in (10, 50, 100, 150, 200)):
        out.append(Anecdote("tenure", "floor", 40, f"Your {_ordinal(tenure)} Cup Racing start."))
    elif ww.laps:
        out.append(Anecdote("laps", "floor", 15,
                            f"{ww.laps} racing laps banked — {_ordinal(tenure)} career start."))
    return out


def _rank(anecdotes: list[Anecdote]) -> tuple[Optional[Anecdote], list[Anecdote], list[Anecdote]]:
    """headline, supports (<=2, distinct categories), more."""
    ordered = sorted(anecdotes, key=lambda a: a.impact, reverse=True)
    if not ordered:
        return None, [], []
    headline = ordered[0]
    supports: list[Anecdote] = []
    used_cat = {headline.category}
    rest: list[Anecdote] = []
    for a in ordered[1:]:
        if len(supports) < 2 and a.category not in used_cat:
            supports.append(a)
            used_cat.add(a.category)
        else:
            rest.append(a)
    return headline, supports, rest


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _render_card(driver: str, sid: str, venue: str, date: str,
                 headline: Anecdote, supports: list[Anecdote], more: list[Anecdote]) -> str:
    lines = [f"# {driver} — {sid} · {venue} · {date}", "", f"**{headline.text}**", ""]
    for s in supports:
        lines.append(f"- {s.text}")
    if more:
        lines += ["", "**More**", *[f"- {m.text}" for m in more]]
    return "\n".join(lines) + "\n"


def _render_everyone(sid: str, venue: str, date: str,
                     cards: list[tuple[str, Anecdote, list[Anecdote]]]) -> str:
    lines = [f"# {sid} · {venue} · {date} — Weekend cards", ""]
    for driver, headline, supports in cards:
        lines.append(f"## {driver}")
        lines.append(f"**{headline.text}**")
        for s in supports:
            lines.append(f"- {s.text}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _render_midfield(sid: str, venue: str, date: str,
                     W: dict[str, Weekend], cohort: set[str],
                     firsts: list[tuple[str, Anecdote]]) -> str:
    # Closest battles — one row per pair (dedupe A-vs-B / B-vs-A), genuinely tight only.
    seen: set[frozenset] = set()
    battles: list[tuple[str, str, float, str]] = []
    for d in cohort:
        n = W[d].nearest
        if not n:
            continue
        rival, gap, rk, _ahead = n
        key = frozenset((d, rival))
        if gap <= 3000 and key not in seen:
            seen.add(key)
            battles.append((d, rival, gap, rk))
    battles.sort(key=lambda b: b[2])
    battles = battles[:5]

    movers = sorted(((d, W[d].net_gain) for d in cohort if W[d].net_gain and W[d].net_gain > 0),
                    key=lambda x: -x[1])[:5]
    passers = sorted(((d, W[d].overtakes) for d in cohort if W[d].overtakes >= 3),
                     key=lambda x: -x[1])[:5]

    lines = [f"# {sid} · {venue} · {date} — Midfield briefing", "",
             "Stories from outside the front three. Ready hooks for pundits.", ""]

    def section(head: str, items: list, fmt) -> None:
        if not items:
            return
        lines.append(f"## {head}")
        lines.extend(fmt(it) for it in items)
        lines.append("")

    section("Closest battles", battles,
            lambda b: f"- {b[0]} vs {b[1]}: {_fmt_gap(b[2])} at the flag (race {b[3]}).")
    section("Biggest climbers", movers, lambda m: f"- {m[0]}: +{m[1]} places on the weekend.")
    section("Most passes", passers, lambda p: f"- {p[0]}: {p[1]} on-track passes.")
    section("Milestones", firsts, lambda f: f"- {f[0]}: {f[1].text}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _pick_event(seasons: dict[str, dict], season: Optional[str],
                round_no: Optional[int]) -> tuple[str, dict]:
    if season is None:
        candidates = [s for s in _ordered_season_ids(seasons) if seasons[s].get("events")]
        if not candidates:
            raise ValueError("no seasons with events in the dataset")
        season = candidates[-1]
    if season not in seasons:
        raise ValueError(f"season {season} not found in dataset")
    events = seasons[season].get("events") or []
    if not events:
        raise ValueError(f"season {season} has no events")
    if round_no is None:
        event = max(events, key=lambda e: e.get("venueOrder", 0))
    else:
        event = next((e for e in events if e.get("venueOrder") == round_no), None)
        if event is None:
            raise ValueError(f"{season} has no round {round_no}")
    return season, event


def list_rounds(dataset_dir: Path, season: Optional[str] = None) -> tuple[str, list[int]]:
    """Resolve the season (default: the latest with races) and its round numbers."""
    seasons = _load_seasons(dataset_dir)
    if not seasons:
        raise ValueError(f"no season files under {dataset_dir / 'seasons'}")
    sid, _event = _pick_event(seasons, season, None)
    events = seasons[sid].get("events") or []
    return sid, sorted(e.get("venueOrder", 0) for e in events)


def generate(dataset_dir: Path, out_dir: Path, *, season: Optional[str] = None,
             round_no: Optional[int] = None, only_driver: Optional[str] = None,
             midfield_only: bool = False) -> dict[str, Any]:
    """Build recap cards for one venue weekend. Returns a small summary dict."""
    seasons = _load_seasons(dataset_dir)
    if not seasons:
        raise ValueError(f"no season files under {dataset_dir / 'seasons'}")
    sid, event = _pick_event(seasons, season, round_no)
    venue = event.get("venue", "Round")
    date = event.get("date", "")
    target_order = _season_sort_key(sid)
    target_vo = event.get("venueOrder", 0)

    hist = _build_history(seasons)
    strength = _driver_strength(hist)
    W = _aggregate_weekend(event)
    cohort = _cohort(W)
    field_size = len(W)

    out_root = out_dir / sid / _slug(venue)
    out_root.mkdir(parents=True, exist_ok=True)

    everyone: list[tuple[str, Anecdote, list[Anecdote]]] = []
    firsts: list[tuple[str, Anecdote]] = []
    summary_rows: list[tuple[str, str]] = []

    drivers = sorted(W, key=lambda d: (W[d].best_finish or 99))
    for label in drivers:
        if only_driver and label.lower() != only_driver.lower():
            continue
        ww = W[label]
        anecdotes = build_anecdotes(label, ww, hist.get(label, []), strength, W, cohort,
                                    sid, target_order, target_vo, venue, field_size)
        headline, supports, more = _rank(anecdotes)
        if headline is None:
            continue
        for a in anecdotes:
            if a.kind in ("career_first", "career_best"):
                firsts.append((label, a))
                break
        if not midfield_only:
            (out_root / f"{_slug(label)}.md").write_text(
                _render_card(label, sid, venue, date, headline, supports, more), encoding="utf-8")
        everyone.append((label, headline, supports))
        summary_rows.append((label, headline.text))

    if not midfield_only and not only_driver:
        (out_root / "_everyone.md").write_text(
            _render_everyone(sid, venue, date, everyone), encoding="utf-8")
    if not only_driver:
        (out_root / "_midfield.md").write_text(
            _render_midfield(sid, venue, date, W, cohort, firsts), encoding="utf-8")

    return {"season": sid, "venue": venue, "date": date, "out": out_root,
            "drivers": len(everyone), "summary": summary_rows}
