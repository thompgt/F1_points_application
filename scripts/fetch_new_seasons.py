#!/usr/bin/env python3
"""
Sync F1 season data from the Jolpica-F1 API into this project's CSV files.

Ergast (the original source for this dataset) shut down; Jolpica-F1
(https://api.jolpi.ca/ergast/f1/) is the free, schema-compatible community
successor and is used here instead. No API key required.

Rerunnable by design: races/results/standings that already exist in the CSVs
are skipped, so this can (and should) be run again later in an in-progress
season to pick up newly-completed rounds.

Usage:
    python scripts/fetch_new_seasons.py --years 2025 2026
    python scripts/fetch_new_seasons.py --years 2026 --dry-run
"""
import argparse
import os
import time

import pandas as pd
import requests

BASE_URL = "https://api.jolpi.ca/ergast/f1"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NULL = "\\N"  # matches this dataset's existing missing-value convention

FILES = {
    "races": "races.csv",
    "results": "results.csv",
    "drivers": "drivers.csv",
    "constructors": "constructors.csv",
    "circuits": "circuits.csv",
    "driver_standings": "driver_standings.csv",
    "constructor_standings": "constructor_standings.csv",
    "constructor_results": "constructor_results.csv",
    "seasons": "seasons.csv",
}


def csv_path(key):
    return os.path.join(ROOT, FILES[key])


def api_get(endpoint, **params):
    """GET a Jolpica-F1 (Ergast-compatible) endpoint, retrying transient failures."""
    url = f"{BASE_URL}/{endpoint}.json"
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()["MRData"]
        except requests.RequestException as e:
            last_error = e
            time.sleep(2)
    raise last_error


# Jolpica's live /status.json (136 rows) covers everything it has ingested itself,
# but omits a handful of pre-qualifying-era statuses that only appear in this
# dataset's older, Kaggle-sourced seasons. Confirmed by cross-referencing every
# unmapped statusId in results.csv against Ergast's original status.csv dump.
LEGACY_STATUS_OVERRIDES = {
    77: "107% Rule",
    81: "Did not qualify",
    97: "Did not prequalify",
}


def fetch_status_lookup():
    """Canonical statusId <-> status text table (paginated at 100/page)."""
    text_to_id = {}
    id_to_text = {}
    offset = 0
    while True:
        data = api_get("status", limit=100, offset=offset)
        rows = data["StatusTable"]["Status"]
        for row in rows:
            status_id = int(row["statusId"])
            text_to_id[row["status"]] = status_id
            id_to_text[status_id] = row["status"]
        offset += len(rows)
        if not rows or offset >= int(data["total"]):
            break

    for status_id, text in LEGACY_STATUS_OVERRIDES.items():
        id_to_text.setdefault(status_id, text)
        text_to_id.setdefault(text, status_id)
    return text_to_id, id_to_text


def strip_z(value):
    """Jolpica times are like '04:00:00Z'; existing CSVs store '04:00:00'."""
    if not value:
        return NULL
    return value[:-1] if value.endswith("Z") else value


class IdAllocator:
    """Hands out the next integer ID after the current max in a CSV column."""

    def __init__(self, series):
        self._next = (int(series.max()) + 1) if len(series) else 1

    def take(self):
        value = self._next
        self._next += 1
        return value


def load_all():
    return {key: pd.read_csv(csv_path(key)) for key in FILES}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=int, nargs="+", default=[2025, 2026])
    parser.add_argument("--dry-run", action="store_true", help="Print planned insert counts without writing anything")
    args = parser.parse_args()

    tables = load_all()
    print("Fetching canonical status table...")
    status_lookup, status_id_to_text = fetch_status_lookup()

    # results.csv has only statusId with no lookup table anywhere in the repo,
    # so DNF reasons never rendered (main.py read a 'status' column that never
    # existed). Backfill real status text for every existing row here, using
    # the same canonical table new rows are populated from below.
    results_status_backfilled = False
    if "status" not in tables["results"].columns:
        backfilled = tables["results"]["statusId"].map(status_id_to_text)
        unmapped = backfilled.isna().sum()
        if unmapped:
            print(f"Warning: {unmapped} existing result rows have a statusId with no known status text")
        tables["results"]["status"] = backfilled.fillna("Unknown")
        results_status_backfilled = True
        print(f"Backfilling status text for {len(tables['results'])} existing results.csv rows")

    driver_ids = IdAllocator(tables["drivers"]["driverId"])
    constructor_ids = IdAllocator(tables["constructors"]["constructorId"])
    circuit_ids = IdAllocator(tables["circuits"]["circuitId"])
    race_ids = IdAllocator(tables["races"]["raceId"])
    result_ids = IdAllocator(tables["results"]["resultId"])
    driver_standings_ids = IdAllocator(tables["driver_standings"]["driverStandingsId"])
    constructor_standings_ids = IdAllocator(tables["constructor_standings"]["constructorStandingsId"])
    constructor_results_ids = IdAllocator(tables["constructor_results"]["constructorResultsId"])

    driver_ref_to_id = dict(zip(tables["drivers"]["driverRef"], tables["drivers"]["driverId"]))
    constructor_ref_to_id = dict(zip(tables["constructors"]["constructorRef"], tables["constructors"]["constructorId"]))
    circuit_ref_to_id = dict(zip(tables["circuits"]["circuitRef"], tables["circuits"]["circuitId"]))
    race_key_to_id = {(int(r.year), int(r.round)): int(r.raceId) for r in tables["races"].itertuples()}
    races_with_results = set(tables["results"]["raceId"].unique())
    races_with_driver_standings = set(tables["driver_standings"]["raceId"].unique())
    races_with_constructor_standings = set(tables["constructor_standings"]["raceId"].unique())
    existing_season_years = set(tables["seasons"]["year"])

    new_rows = {key: [] for key in FILES}

    def get_driver_id(driver):
        ref = driver["driverId"]
        if ref in driver_ref_to_id:
            return driver_ref_to_id[ref]
        new_id = driver_ids.take()
        driver_ref_to_id[ref] = new_id
        new_rows["drivers"].append({
            "driverId": new_id,
            "driverRef": ref,
            "number": driver.get("permanentNumber", NULL),
            "code": driver.get("code", NULL),
            "forename": driver.get("givenName", ""),
            "surname": driver.get("familyName", ""),
            "dob": driver.get("dateOfBirth", NULL),
            "nationality": driver.get("nationality", NULL),
            "url": driver.get("url", ""),
        })
        return new_id

    def get_constructor_id(constructor):
        ref = constructor["constructorId"]
        if ref in constructor_ref_to_id:
            return constructor_ref_to_id[ref]
        new_id = constructor_ids.take()
        constructor_ref_to_id[ref] = new_id
        new_rows["constructors"].append({
            "constructorId": new_id,
            "constructorRef": ref,
            "name": constructor.get("name", ""),
            "nationality": constructor.get("nationality", NULL),
            "url": constructor.get("url", ""),
        })
        return new_id

    def get_circuit_id(circuit):
        ref = circuit["circuitId"]
        if ref in circuit_ref_to_id:
            return circuit_ref_to_id[ref]
        new_id = circuit_ids.take()
        circuit_ref_to_id[ref] = new_id
        loc = circuit.get("Location", {})
        new_rows["circuits"].append({
            "circuitId": new_id,
            "circuitRef": ref,
            "name": circuit.get("circuitName", ""),
            "location": loc.get("locality", NULL),
            "country": loc.get("country", NULL),
            "lat": loc.get("lat", NULL),
            "lng": loc.get("long", NULL),
            "alt": NULL,  # not provided by Jolpica; historical rows only
            "url": circuit.get("url", ""),
        })
        return new_id

    for year in args.years:
        print(f"\n=== {year} season ===")
        schedule = api_get(str(year))["RaceTable"]["Races"]
        print(f"{len(schedule)} races on the calendar")

        if year not in existing_season_years:
            new_rows["seasons"].append({
                "year": year,
                "url": f"https://en.wikipedia.org/wiki/{year}_Formula_One_World_Championship",
            })
            existing_season_years.add(year)

        for race in schedule:
            season = int(race["season"])
            round_ = int(race["round"])
            circuit_id = get_circuit_id(race["Circuit"])

            if (season, round_) not in race_key_to_id:
                fp1 = race.get("FirstPractice", {})
                fp2 = race.get("SecondPractice", {})
                fp3 = race.get("ThirdPractice", {})
                quali = race.get("Qualifying", {})
                sprint = race.get("Sprint", {})
                new_race_id = race_ids.take()
                new_rows["races"].append({
                    "raceId": new_race_id,
                    "year": season,
                    "round": round_,
                    "circuitId": circuit_id,
                    "name": race["raceName"],
                    "date": race["date"],
                    "time": strip_z(race.get("time")),
                    "url": race.get("url", ""),
                    "fp1_date": fp1.get("date", NULL),
                    "fp1_time": strip_z(fp1.get("time")),
                    "fp2_date": fp2.get("date", NULL),
                    "fp2_time": strip_z(fp2.get("time")),
                    "fp3_date": fp3.get("date", NULL),
                    "fp3_time": strip_z(fp3.get("time")),
                    "quali_date": quali.get("date", NULL),
                    "quali_time": strip_z(quali.get("time")),
                    "sprint_date": sprint.get("date", NULL),
                    "sprint_time": strip_z(sprint.get("time")),
                })
                race_key_to_id[(season, round_)] = new_race_id

            race_id = race_key_to_id[(season, round_)]

            # Results (skipped for rounds that haven't happened yet)
            if race_id not in races_with_results:
                results = api_get(f"{season}/{round_}/results")["RaceTable"]["Races"]
                results = results[0]["Results"] if results else []
                if results:
                    print(f"  Round {round_} ({race['raceName']}): {len(results)} results")
                    constructor_points = {}
                    for order, result in enumerate(results, start=1):
                        driver_id = get_driver_id(result["Driver"])
                        constructor_id = get_constructor_id(result["Constructor"])
                        status_text = result.get("status", "Unknown")
                        status_id = status_lookup.get(status_text, NULL)
                        time_info = result.get("Time", {})
                        fastest = result.get("FastestLap", {})
                        fastest_time = fastest.get("Time", {})
                        new_rows["results"].append({
                            "resultId": result_ids.take(),
                            "raceId": race_id,
                            "driverId": driver_id,
                            "constructorId": constructor_id,
                            "number": result.get("number", NULL),
                            "grid": result.get("grid", NULL),
                            "position": result.get("position", NULL),
                            "positionText": result.get("positionText", NULL),
                            "positionOrder": order,
                            "points": result.get("points", 0),
                            "laps": result.get("laps", NULL),
                            "time": time_info.get("time") or NULL,
                            "milliseconds": time_info.get("millis") or NULL,
                            "fastestLap": fastest.get("lap", NULL),
                            "rank": fastest.get("rank", NULL),
                            "fastestLapTime": fastest_time.get("time", NULL),
                            "fastestLapSpeed": fastest.get("AverageSpeed", {}).get("speed", NULL),
                            "statusId": status_id,
                            "status": status_text,
                        })
                        constructor_points[constructor_id] = constructor_points.get(constructor_id, 0) + float(result.get("points", 0))
                    for constructor_id, points in constructor_points.items():
                        new_rows["constructor_results"].append({
                            "constructorResultsId": constructor_results_ids.take(),
                            "raceId": race_id,
                            "constructorId": constructor_id,
                            "points": points,
                            "status": NULL,
                        })
                    races_with_results.add(race_id)

                    if race_id not in races_with_driver_standings:
                        standings = api_get(f"{season}/{round_}/driverStandings")["StandingsTable"]["StandingsLists"]
                        for entry in (standings[0]["DriverStandings"] if standings else []):
                            new_rows["driver_standings"].append({
                                "driverStandingsId": driver_standings_ids.take(),
                                "raceId": race_id,
                                "driverId": get_driver_id(entry["Driver"]),
                                "points": entry.get("points", 0),
                                "position": entry.get("position", NULL),
                                "positionText": entry.get("positionText", NULL),
                                "wins": entry.get("wins", 0),
                            })
                        races_with_driver_standings.add(race_id)

                    if race_id not in races_with_constructor_standings:
                        standings = api_get(f"{season}/{round_}/constructorStandings")["StandingsTable"]["StandingsLists"]
                        for entry in (standings[0]["ConstructorStandings"] if standings else []):
                            new_rows["constructor_standings"].append({
                                "constructorStandingsId": constructor_standings_ids.take(),
                                "raceId": race_id,
                                "constructorId": get_constructor_id(entry["Constructor"]),
                                "points": entry.get("points", 0),
                                "position": entry.get("position", NULL),
                                "positionText": entry.get("positionText", NULL),
                                "wins": entry.get("wins", 0),
                            })
                        races_with_constructor_standings.add(race_id)
                else:
                    print(f"  Round {round_} ({race['raceName']}): not yet run, skipping")

    print("\n=== Planned inserts ===")
    for key in FILES:
        print(f"  {FILES[key]}: +{len(new_rows[key])} rows")
    if results_status_backfilled:
        print("  results.csv: backfilling 'status' text column for existing rows")

    if args.dry_run:
        print("\nDry run - nothing written.")
        return

    for key in FILES:
        if not new_rows[key] and not (key == "results" and results_status_backfilled):
            continue
        updated = pd.concat([tables[key], pd.DataFrame(new_rows[key])], ignore_index=True) if new_rows[key] else tables[key]
        updated.to_csv(csv_path(key), index=False)
        print(f"Wrote {FILES[key]} ({len(updated)} total rows)")


if __name__ == "__main__":
    main()
