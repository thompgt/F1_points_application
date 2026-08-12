#!/usr/bin/env python3
"""Standalone recalculation: re-score a season and write adjusted_results.csv.

This script used to be a fork. It carried its own copy of the points array and
its own ``adjust_points`` / ``calculate_standings``, which meant it also carried
its own copy of every scoring bug -- awarding points off ``positionOrder`` so
retirements scored, paying both halves of a 1950s shared drive in full, and
resolving championship ties by whatever order the groupby emitted. It imported
seaborn and matplotlib, neither of which is in requirements.txt, so on a clean
install it crashed on import before any of that mattered.

It now imports the same :mod:`scoring` module the API uses, so there is exactly
one implementation of the rules and this cannot drift from it again. Charts use
plotly, which the app already depends on.

Usage::

    python adjusted_points.py                 # 2024 under modern points
    python adjusted_points.py --season 1988 --system pre_1991
    python adjusted_points.py --chart         # also write a cumulative-points HTML
"""

from __future__ import annotations

import argparse
import os
import warnings

import pandas as pd

import scoring

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_CSV = os.path.join(ROOT, "adjusted_results.csv")

# Kaggle's exports use a literal \N for missing values.
NA_VALUES = ["\\N"]


def load_frames():
    read = lambda name: pd.read_csv(os.path.join(ROOT, f"{name}.csv"), na_values=NA_VALUES)  # noqa: E731
    return read("results"), read("races"), read("drivers")


def build_adjusted(system_key: str) -> pd.DataFrame:
    """Score every result and join on driver and race metadata."""
    results, races, drivers = load_frames()

    rules = scoring.resolve_points_system(system_key)
    if rules is None:
        raise SystemExit(
            f"unknown points system {system_key!r}; choose one of: "
            + ", ".join(scoring.NAMED_POINTS_SYSTEMS)
        )

    adjusted = scoring.adjust_points(results, rules, races=races)
    adjusted = pd.merge(adjusted, drivers[["driverId", "surname", "forename"]], on="driverId")
    return pd.merge(adjusted, races[["raceId", "year", "name", "round"]], on="raceId")


def plot_cumulative_points(adjusted_results_with_races: pd.DataFrame, season_year: int) -> str:
    """Write an interactive cumulative-points chart for the season's top 10."""
    import plotly.express as px

    season = adjusted_results_with_races[
        adjusted_results_with_races["year"] == season_year
    ].sort_values(["round", "raceId"]).copy()
    season["driver"] = season["forename"] + " " + season["surname"]
    season["cumulative_points"] = season.groupby("driver")["adjusted_points"].cumsum()

    top_10 = (
        season.groupby("driver")["adjusted_points"].sum().sort_values(ascending=False).head(10).index
    )
    figure = px.line(
        season[season["driver"].isin(top_10)],
        x="round",
        y="cumulative_points",
        color="driver",
        markers=True,
        title=f"Cumulative points, {season_year}",
        labels={"round": "Round", "cumulative_points": "Cumulative points", "driver": "Driver"},
    )
    path = os.path.join(ROOT, f"cumulative_points_{season_year}.html")
    figure.write_html(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2024, help="season to print standings for")
    parser.add_argument(
        "--system",
        default="modern",
        choices=sorted(scoring.NAMED_POINTS_SYSTEMS),
        help="named points system to score under",
    )
    parser.add_argument("--chart", action="store_true", help="also write a cumulative-points chart")
    parser.add_argument("--no-write", action="store_true", help="skip writing adjusted_results.csv")
    args = parser.parse_args()

    adjusted = build_adjusted(args.system)

    if not args.no_write:
        adjusted.to_csv(OUTPUT_CSV, index=False)
        print(f"wrote {OUTPUT_CSV} ({len(adjusted):,} rows)")

    standings = scoring.calculate_standings(adjusted, args.season)
    if standings.empty:
        print(f"No data for the {args.season} season.")
        return 1

    label = scoring.NAMED_POINTS_SYSTEMS[args.system]["name"]
    print(f"\n{args.season} standings under {label}:")
    print(standings[["Position", "forename", "surname", "adjusted_points"]].to_string(index=False))

    if args.chart:
        print(f"\nwrote {plot_cumulative_points(adjusted, args.season)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
