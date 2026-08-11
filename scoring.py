"""Championship scoring rules, in one place.

Everything that turns a results frame into points and a standings table lives
here rather than in ``main.py`` so that it can be exercised without booting
FastAPI, loading 26k rows, or touching a database -- see ``tests/test_points.py``.

The rules modelled here, and the ones deliberately not modelled, are documented
in the "Scoring model and known limitations" section of the README. Two things
are worth stating up front because they are the difference between this module
and a naive ``positionOrder`` lookup:

* **Only classified finishers score.** Ergast gives *every* entrant a
  ``positionOrder`` -- it is a sort key for the results table, not a finishing
  position. Retirements, accidents, disqualifications and non-starters all get
  one. In the shipped dataset 338 rows have ``positionOrder <= 10`` while
  ``position`` is null (1957 Monaco, P7 with an engine failure, is a good
  example). ``positionText`` is the field that distinguishes them: it holds the
  finishing position as a numeral for classified runners and a letter code
  otherwise (``R`` retired, ``D`` disqualified, ``E`` excluded, ``W``
  withdrawn, ``F`` failed to qualify, ``N`` not classified).

* **Shared drives split the points.** In the 1950s a driver could hand his car
  to a team-mate mid-race and both were classified in the same position, so a
  race can have several rows with the same ``positionOrder`` (the 1955
  Argentine GP has three at P2). The FIA split that position's points between
  them; awarding each the full amount double-counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

import pandas as pd

# The system in force since 2010. Kept as the module-level default because the
# API treats "no points_system supplied" as "score it the modern way".
DEFAULT_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

# positionText is numeric exactly when the entrant was classified.
_NUMERIC_POSITION = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Half points
# ---------------------------------------------------------------------------
# Half points have been awarded six times, always because a race was stopped
# before 75% of the scheduled distance and could not be restarted. The dataset
# has no scheduled-lap-count column, so the rule cannot be derived from the
# data; there is no heuristic over ``laps`` that separates a short race from a
# long one without it. Six races is a short enough list to state explicitly,
# and being explicit is also honest about the fact that this is a lookup rather
# than a modelled rule.
HALF_POINTS_RACES = {
    (1975, "Spanish Grand Prix"),
    (1975, "Austrian Grand Prix"),
    (1984, "Monaco Grand Prix"),
    (1991, "Australian Grand Prix"),
    (2009, "Malaysian Grand Prix"),
    (2021, "Belgian Grand Prix"),
}


@dataclass(frozen=True)
class ScoringRules:
    """The modifiers that sit on top of a bare points-per-position array.

    A points array alone does not describe a season: 2010 and 2019 pay the same
    25/18/15 but only one of them has a fastest-lap point, and that single point
    is enough to move a championship (2021 was decided by 8).
    """

    points: tuple = tuple(DEFAULT_POINTS)
    # 2019-2024 (and 1950-1959, under a different rule this module does not
    # model): one point for the fastest lap of the race.
    fastest_lap_point: bool = False
    # Since 2019 the point is only paid if the driver also finished in the top
    # ten; before 2019 the fastest lap paid regardless of finishing position.
    fastest_lap_requires_top_ten: bool = True
    # Sprint race payouts, longest-standing form first. Empty means the system
    # has no sprint. Applied only if a sprint results frame is supplied.
    sprint_points: tuple = ()
    # Whether to halve the six races in HALF_POINTS_RACES.
    apply_half_points: bool = True

    def points_for(self, position: int) -> float:
        if 1 <= position <= len(self.points):
            return float(self.points[position - 1])
        return 0.0


# ---------------------------------------------------------------------------
# Named systems
# ---------------------------------------------------------------------------
# Each entry states the exact seasons it was in force and the modifiers that
# went with it, so that "Modern" cannot quietly mean two different things. The
# 2003-2009 system was missing entirely before -- seven seasons users could not
# select.
NAMED_POINTS_SYSTEMS: dict[str, dict] = {
    "modern": {
        "name": "Modern with fastest lap (2019-2024)",
        "points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
        "years": "2019-2024",
        "modifiers": "One point for the fastest lap if classified in the top ten. "
                     "Sprint races pay 8-7-6-5-4-3-2-1 from 2022 (2021 paid 3-2-1) "
                     "but no sprint dataset ships with this repo.",
        "rules": ScoringRules(
            points=(25, 18, 15, 12, 10, 8, 6, 4, 2, 1),
            fastest_lap_point=True,
            fastest_lap_requires_top_ten=True,
            sprint_points=(8, 7, 6, 5, 4, 3, 2, 1),
        ),
    },
    "modern_no_fl": {
        "name": "Modern (2010-2018)",
        "points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
        "years": "2010-2018",
        "modifiers": "No fastest-lap point. The 2014 season finale (Abu Dhabi) paid "
                     "double points; that one-off is not modelled here.",
        "rules": ScoringRules(points=(25, 18, 15, 12, 10, 8, 6, 4, 2, 1)),
    },
    "points_2003": {
        "name": "Eight-scorer era (2003-2009)",
        "points": [10, 8, 6, 5, 4, 3, 2, 1],
        "years": "2003-2009",
        "modifiers": "First system to pay down to eighth place. No fastest-lap point.",
        "rules": ScoringRules(points=(10, 8, 6, 5, 4, 3, 2, 1)),
    },
    "classic": {
        "name": "Classic (1991-2002)",
        "points": [10, 6, 4, 3, 2, 1],
        "years": "1991-2002",
        "modifiers": "Every round counted; the dropped-scores rule ended after 1990.",
        "rules": ScoringRules(points=(10, 6, 4, 3, 2, 1)),
    },
    "pre_1991": {
        "name": "Pre-1991 (1961-1990)",
        "points": [9, 6, 4, 3, 2, 1],
        "years": "1961-1990",
        "modifiers": "Only a driver's best N results counted towards the title, and N "
                     "varied year to year. That dropped-scores rule is NOT modelled: "
                     "every round is counted here.",
        "rules": ScoringRules(points=(9, 6, 4, 3, 2, 1)),
    },
}


def resolve_points_system(key: Optional[str]) -> Optional[ScoringRules]:
    """Look up a named system's rules, or None if the key is unknown."""
    entry = NAMED_POINTS_SYSTEMS.get(key or "")
    return entry["rules"] if entry else None


def points_system_label(points_system: Optional[Sequence[float]]) -> str:
    """Name a points array the way a user would recognise it.

    ``None`` means the caller did not ask for anything in particular and got the
    default, so it is the default's name. Otherwise match the array against the
    named systems -- posting the exact modern array by hand used to come back as
    "Custom" purely because it was not the same Python object as the default.
    Trailing zeros are ignored, so an eleven-element array whose eleventh entry
    is 0 is still recognised as the ten-element system it is equivalent to.
    """
    if points_system is None:
        return NAMED_POINTS_SYSTEMS["modern"]["name"]

    trimmed = list(points_system)
    while trimmed and not trimmed[-1]:
        trimmed.pop()

    for entry in NAMED_POINTS_SYSTEMS.values():
        if trimmed == list(entry["points"]):
            return entry["name"]
    return "Custom"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def classified_position(results_df: pd.DataFrame) -> pd.Series:
    """Finishing position for classified entrants, NaN for everyone else.

    Reads ``positionText`` when present, because that is the only field that
    distinguishes a classified tenth place from a car that retired and happens
    to sort tenth. Frames without it (hand-built fixtures, some older exports)
    fall back to ``position``, which is null for non-finishers and therefore
    carries the same information, just less explicitly.
    """
    if "positionText" in results_df.columns:
        text = results_df["positionText"].astype("string").str.strip()
        numeric = text.str.match(_NUMERIC_POSITION, na=False)
        return pd.to_numeric(text.where(numeric), errors="coerce")
    if "position" in results_df.columns:
        return pd.to_numeric(results_df["position"], errors="coerce")
    raise KeyError("results frame has neither 'positionText' nor 'position'")


def _half_points_race_ids(races: Optional[pd.DataFrame]) -> set:
    if races is None or "year" not in races.columns or "name" not in races.columns:
        return set()
    key = list(zip(races["year"], races["name"]))
    mask = [k in HALF_POINTS_RACES for k in key]
    return set(races.loc[mask, "raceId"])


def adjust_points(
    results_df: pd.DataFrame,
    points_system: Sequence[float] | ScoringRules | None = None,
    races: Optional[pd.DataFrame] = None,
    sprint_results: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Return a copy of ``results_df`` with an ``adjusted_points`` column.

    ``points_system`` may be a bare points array (the historical signature, and
    what the API accepts) or a :class:`ScoringRules` carrying the modifiers as
    well. A bare array is taken literally: positions only, no fastest lap, no
    sprint -- because that is what a user typing "10,6,4,3,2,1" into the custom
    box is asking for.

    ``races`` is only needed for the half-points lookup and ``sprint_results``
    only for sprint payouts; both are optional and their absence simply means
    that modifier is not applied.
    """
    if points_system is None:
        rules = ScoringRules(points=tuple(DEFAULT_POINTS))
    elif isinstance(points_system, ScoringRules):
        rules = points_system
    else:
        rules = ScoringRules(points=tuple(points_system))

    adjusted = results_df.copy()
    position = classified_position(adjusted)

    # Base points for the finishing position. Unclassified rows keep 0.
    base = position.map(lambda p: rules.points_for(int(p)) if pd.notna(p) else 0.0)
    base = base.astype(float).fillna(0.0)

    # Shared drives: two or more entries classified in the same position of the
    # same race split that position's points, as the FIA did.
    if "raceId" in adjusted.columns:
        share_key = adjusted["raceId"].astype("string") + "|" + position.astype("string")
        shares = share_key.where(position.notna()).map(
            share_key.where(position.notna()).value_counts()
        )
        base = base / shares.fillna(1).astype(float)

    adjusted["adjusted_points"] = base

    # Fastest lap. Ergast records it as rank == 1; the column is only populated
    # from 2004 onwards, so earlier seasons simply never trigger this.
    if rules.fastest_lap_point and "rank" in adjusted.columns:
        rank = pd.to_numeric(adjusted["rank"], errors="coerce")
        eligible = (rank == 1) & position.notna()
        if rules.fastest_lap_requires_top_ten:
            eligible &= position <= 10
        adjusted.loc[eligible, "adjusted_points"] += 1.0

    # Sprint races. No sprint dataset ships with the repo (races.csv has
    # sprint_date for 18 rounds but there is no sprint_results table), so this
    # is a no-op unless a caller supplies one -- see scripts/fetch_data.py.
    if rules.sprint_points and sprint_results is not None and not sprint_results.empty:
        sprint_rules = ScoringRules(points=tuple(rules.sprint_points))
        scored_sprint = adjust_points(sprint_results, sprint_rules)
        totals = (
            scored_sprint.groupby(["raceId", "driverId"], as_index=False)["adjusted_points"]
            .sum()
            .rename(columns={"adjusted_points": "_sprint_points"})
        )
        adjusted = adjusted.merge(totals, on=["raceId", "driverId"], how="left")
        adjusted["adjusted_points"] += adjusted["_sprint_points"].fillna(0.0)
        adjusted = adjusted.drop(columns=["_sprint_points"])
        position = classified_position(adjusted)

    # Half points, for the six races that were stopped early.
    if rules.apply_half_points:
        halved = _half_points_race_ids(races)
        if halved and "raceId" in adjusted.columns:
            adjusted.loc[adjusted["raceId"].isin(halved), "adjusted_points"] *= 0.5

    return adjusted


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

# How deep the countback goes. Sorting on points alone leaves ties resolved by
# whatever order the groupby happened to emit, which under a six-position system
# is common enough to flip a champion.
_COUNTBACK_DEPTH = 10


def _countback_table(season_results: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Per-driver counts of first places, second places, ... up to P10.

    This is the FIA tie-break: most points, then most wins, then most seconds,
    and so on down the order.
    """
    position = classified_position(season_results)
    frame = season_results[group_cols].copy()
    frame["_pos"] = position
    out = frame[group_cols].drop_duplicates().reset_index(drop=True)
    for place in range(1, _COUNTBACK_DEPTH + 1):
        counts = (
            frame[frame["_pos"] == place]
            .groupby(group_cols, as_index=False)
            .size()
            .rename(columns={"size": f"_p{place}"})
        )
        out = out.merge(counts, on=group_cols, how="left")
        out[f"_p{place}"] = out[f"_p{place}"].fillna(0).astype(int)
    return out


def calculate_standings(adjusted_results_with_races: pd.DataFrame, season_year: int) -> pd.DataFrame:
    """Championship table for one season, tie-broken by FIA countback."""
    season_results = adjusted_results_with_races[
        adjusted_results_with_races["year"] == season_year
    ]
    if season_results.empty:
        return pd.DataFrame()

    group_cols = ["surname", "forename"]
    standings = season_results.groupby(group_cols, as_index=False)["adjusted_points"].sum()
    standings = standings.merge(
        _countback_table(season_results, group_cols), on=group_cols, how="left"
    )

    countback_cols = [f"_p{place}" for place in range(1, _COUNTBACK_DEPTH + 1)]
    standings = standings.sort_values(
        by=["adjusted_points", *countback_cols, "surname", "forename"],
        ascending=[False, *([False] * len(countback_cols)), True, True],
    ).reset_index(drop=True)
    standings = standings.drop(columns=countback_cols)

    standings["driver_label"] = standings.apply(
        lambda row: f"{row['forename'][0]}. {row['surname']}", axis=1
    )
    standings.index += 1
    standings.reset_index(inplace=True)
    standings.rename(columns={"index": "Position"}, inplace=True)
    return standings
