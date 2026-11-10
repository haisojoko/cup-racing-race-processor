# Cup Racing wheel-to-wheel rules and penalties

The league's own rules, as given by the driver on 2026-11-10. **This text is the
authority** — it is reproduced verbatim below and should not be reworded or
"improved". Anything after it is method notes for reviewing contacts against it.

Official standings and penalties stay in the league's own records (see README);
this file exists so a contact review can be run back without re-asking for the
rules.

---

## The rules (verbatim)

```
WHEEL-TO-WHEEL RACING RULES
Leaving Space
Drivers must leave at least one car’s width on corner entry and exit if another car is alongside.
Definition of Alongside: The attacking car’s front axle must be in front of the defending car’s rear axle.
Prohibited Actions:
Squeezing or pushing another driver off the track.
Running a driver wide off the track without leaving space.
Attacking
Prohibited Overtakes:
Cutting the track - see track limits rules for definition of cutting the track
Cutting grass, even if allowed by the track’s detection system.
Using the pitlane to save time.
Reckless Attacks:
Diving too late, forcing an opponent off the track.
Diving too late, causing contact.
Reckless moves without being alongside before braking.
Blocking a car after understeering mid-corner.
Steward Discretion:
Stewards will assess whether moves were erratic, aggressive, or too late.
Defending
Permitted Moves:
One defensive move into a braking zone.
One defensive move to break a slipstream or choose a defensive line.
Defending the apex of a corner if the line was chosen before the attacking driver committed.
Prohibited Moves:
Reactive moves in the braking zone.
Erratic movement in the braking zone.
Brake-checking the attacking driver.
Blocking more than once on a straight.
Squeezing an attacker off track mid-corner.
Closing the door last second (as determined by stewards).
Re-Entering the Track
Drivers must re-enter the track safely:
Check for approaching cars visually and via the track map.
Re-enter parallel to the track, do not immediately cut to the center of the track.
Do not impede oncoming traffic.
Spinning Out
Drivers losing control should:
Hold brakes to stay predictable.
Attempt to regain control cautiously.
If recovery causes a collision, stewards may impose penalties.

PENALTIES
Point Deduction Scale:
1–3 points: Minor infractions (e.g., accidental track limit violation without significant gain, minor contact).
4–6 points: Major infractions (e.g., repeated track abuse or avoidable contact).
7–DSQ: Intentional misconduct (e.g., griefing, deliberate contact, intentional cutting).
Disqualification (DSQ):
Applied for repeated or egregious rule violations.
Appeals:
Penalties can be appealed, but stewards' decisions are final.
```

---

## Reviewing contacts against these rules

Worked through for all of S25 on 2026-11-10 (Chang and Sepang, 8 races, 24
incidents). The findings are not repeated here; this is how to get them again.

### Where the data is

| What | Where | Gap to be aware of |
|---|---|---|
| Who touched whom, impact speed, map position | the raw server JSON, `Events[]` with `Type == "COLLISION_WITH_CAR"` | **No lap and no timestamp.** Each touch is logged once per car, so expect reciprocal pairs |
| Contact point on each car | the same events, `RelPosition` | In that car's own frame: `+z` is its nose, `-z` its tail. Pairing the two logs says who ran into whom |
| Positions, throttle and brake per car per frame | the `.acreplay`, via `ac-replay-tower/tools/acreplay.py` | Throttle (byte 244) and brake (byte 245) are documented there but **not extracted** by `parse_replay` — read them yourself |
| Lap numbers | start/finish crossings, `ac-replay-tower/tools/laps._crossings` | — |
| Turn names | `ac-telemetry-lua/rss-super-formula-25/<track>/combo.json`, `turns` | Normalised lap positions; these are the names the driver's own notes use |
| Cuts / track limits | `Laps[].Cuts` in the server JSON | **Unpopulated league-wide** — the server is not writing it, so off-track abuse cannot be judged from the data |

### Turning the rules into numbers

- **Alongside** (front axle past the defender's rear axle) is a longitudinal
  centre-to-centre gap under one **wheelbase, 3.6 m** for the Supreme 25. A pure
  rear-end sits at about 4.6 m, so a car that rear-ends another was by
  definition never alongside and was never owed space.
- **One car's width** is **2.0 m** between centres, measured across the road.
- Compare the two cars **at the same point on the road**, not the same instant:
  a follower reaches a given point up to ~0.2 s later, so on a bend, comparing
  instants makes the road's curve read as a driver moving across. Longitudinal
  and lateral both come from the centreline (arc length and signed offset).
- **Brake-checking** and **reactive braking-zone moves** are answerable directly
  from the brake channel once it is extracted; "did the defender move" is the
  defender's own path across the road.

### Traps that cost time on the first run

- `Centerline.project`'s `around` and `window` are **array indices**, not metres.
  They only equal arc metres at the module's default 1 m spacing — build at any
  other step and every number is silently wrong.
- A centreline built as a closed loop joins its end back to its start, and the
  reference car does not cross the line at the same width on both laps, so that
  join is a lateral step of several metres. Projections either side of it read
  as a car jumping across the road. Build an **open one-lap line whose ends sit
  half a lap from the incident** instead.
- Validate the throttle/brake bytes before trusting them: brake over 50% should
  give roughly −10 to −16 m/s² and full throttle +3.8 to +4.3 m/s². Cars that
  never drove read as 0% throttle with the brake held.
- The server's `ImpactSpeed` disagrees with the measured closing speed in
  slow-queue contacts. Judge on measured speed.
- **Check the race had not already finished.** Compare the incident's time after
  the green with the winner's `totalTimeMs`. Cars park on the track after the
  flag, and two of S25's heaviest "impacts" were cars hitting a stationary one
  after everyone had finished.
- The final ~0.15 s of any trace is the impact, not driving.

### What the data cannot settle

- Off-track abuse: `Cuts` is empty across every race file.
- There is no steering channel, so "who moved" is the car's path, never the wheel.
- Car length and width are assumed from the Supreme 25. A verdict resting on
  1.9 m against 2.0 m is inside that tolerance and should be a warning, not a
  penalty.
- "Defenders may not move too late" and "closing the door last second" are
  reserved to steward discretion by the rules above. The data can say what
  happened and when; it cannot make that call.
