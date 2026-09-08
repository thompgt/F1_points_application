"""FastMCP Server for Formula 1 Points & Historical Analysis.

Provides Model Context Protocol (MCP) tools, resources, and prompts to enable
AI assistants (Claude Desktop, Cursor, Antigravity, etc.) to query F1 race data,
recalculate championship outcomes under historical or custom scoring rules,
and analyze driver head-to-head records.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import pandas as pd

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "F1 Historical Points Engine",
    instructions=(
        "F1 Points & Championship Analytics engine. Allows recalculating F1 World Championships "
        "under custom or historical points systems (1950-present), inspecting race classifications, "
        "and evaluating teammate head-to-head battles."
    ),
)

import gcs_storage
import scoring
from main import (
    build_enriched_results,
    calculate_standings,
    load_data,
)



# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _find_driver(driver_query: Union[str, int], drivers_df: pd.DataFrame) -> pd.Series:
    """Resolve a driver query (ID, surname, or full name) to a driver row."""
    # Check if query is directly an integer driverId
    if isinstance(driver_query, int) or (isinstance(driver_query, str) and driver_query.strip().isdigit()):
        d_id = int(driver_query)
        match = drivers_df[drivers_df["driverId"] == d_id]
        if not match.empty:
            return match.iloc[0]

    query_str = str(driver_query).strip().lower()

    # 1. Exact match on driverRef (e.g., 'hamilton', 'max_verstappen')
    if "driverRef" in drivers_df.columns:
        match = drivers_df[drivers_df["driverRef"].str.lower() == query_str]
        if not match.empty:
            return match.iloc[0]

    # 2. Exact match on surname
    match = drivers_df[drivers_df["surname"].str.lower() == query_str]
    if not match.empty:
        return match.iloc[0]

    # 3. Full name match: 'forename surname'
    full_names = (drivers_df["forename"] + " " + drivers_df["surname"]).str.lower()
    match = drivers_df[full_names == query_str]
    if not match.empty:
        return match.iloc[0]

    # 4. Substring match on surname or full name
    sub_match = drivers_df[
        drivers_df["surname"].str.lower().str.contains(query_str, na=False)
        | full_names.str.contains(query_str, na=False)
    ]
    if not sub_match.empty:
        return sub_match.iloc[0]

    raise ValueError(
        f"Driver '{driver_query}' not found. Please provide a valid surname, full name, or driverId."
    )


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def calculate_championship_standings(
    season_year: int,
    points_system: Optional[List[int]] = None,
    top_n: int = 15,
) -> List[Dict[str, Any]]:
    """Recalculate Formula 1 World Championship standings for any season (1950 to present).

    Applies official or custom points systems, including historical half-points rules
    and official FIA countback tie-breaking.

    Args:
        season_year: The championship season (e.g., 2021, 2007, 1988, 1976).
        points_system: Optional custom points list for top finishers (e.g., [10, 6, 4, 3, 2, 1]
                       for 1991-2002, [25, 18, 15, 12, 10, 8, 6, 4, 2, 1] for modern).
                       If omitted, the modern 2010+ system is applied.
        top_n: Maximum number of classified drivers to return (default 15).
    """
    applied_points = scoring.DEFAULT_POINTS if points_system is None else points_system

    enriched = build_enriched_results(points_system=applied_points, season=season_year)
    if enriched.empty:
        return []

    standings_df = calculate_standings(enriched, season_year)
    if standings_df.empty:
        return []

    # Map primary constructor for each driver
    season_rows = enriched[enriched["year"] == season_year]
    if not season_rows.empty and "constructor_name" in season_rows.columns:
        mode_constructors = (
            season_rows.groupby(["surname", "forename", "constructor_name"], as_index=False)["raceId"]
            .count()
            .sort_values(["surname", "forename", "raceId"], ascending=[True, True, False])
            .drop_duplicates(subset=["surname", "forename"], keep="first")
        )
        standings_df = pd.merge(
            standings_df,
            mode_constructors[["surname", "forename", "constructor_name"]],
            on=["surname", "forename"],
            how="left",
        )

    # Calculate wins per driver
    wins_per_driver = (
        season_rows[season_rows["positionOrder"] == 1]
        .groupby(["surname", "forename"], as_index=False)["raceId"]
        .count()
        .rename(columns={"raceId": "wins"})
    )
    standings_df = pd.merge(standings_df, wins_per_driver, on=["surname", "forename"], how="left")
    standings_df["wins"] = standings_df["wins"].fillna(0).astype(int)

    results: List[Dict[str, Any]] = []
    for _, row in standings_df.head(top_n).iterrows():
        results.append({
            "position": int(row["Position"]),
            "driver": f"{row['forename']} {row['surname']}",
            "constructor": str(row.get("constructor_name", "Unknown")),
            "points": float(row["adjusted_points"]),
            "wins": int(row["wins"]),
        })

    return results


@mcp.tool()
def get_driver_head_to_head(
    driver1: str,
    driver2: str,
    season: Optional[int] = None,
) -> Dict[str, Any]:
    """Compare two F1 drivers head-to-head across a season or shared career races.

    Accepts driver surnames (e.g. 'Hamilton', 'Verstappen', 'Alonso'), full names, or driver IDs.

    Args:
        driver1: Name or ID of the first driver (e.g. 'Hamilton').
        driver2: Name or ID of the second driver (e.g. 'Rosberg').
        season: Optional season year (e.g. 2016). If omitted, compares all shared races.
    """
    _, races, drivers, _, _, _ = load_data()

    d1_row = _find_driver(driver1, drivers)
    d2_row = _find_driver(driver2, drivers)

    d1_id = int(d1_row["driverId"])
    d2_id = int(d2_row["driverId"])

    if d1_id == d2_id:
        raise ValueError("Cannot compare a driver with themselves.")

    # Enriched results with points
    df = build_enriched_results(season=int(season) if season is not None else None)

    d1_data = df[df["driverId"] == d1_id].copy()
    d2_data = df[df["driverId"] == d2_id].copy()

    def _stats(d: pd.DataFrame) -> Dict[str, Any]:
        wins = int((d["positionOrder"] == 1).sum()) if "positionOrder" in d.columns else 0
        podiums = int((d["positionOrder"] <= 3).sum()) if "positionOrder" in d.columns else 0
        poles = int((d["grid"] == 1).sum()) if "grid" in d.columns else 0
        points = float(d["adjusted_points"].sum()) if "adjusted_points" in d.columns else 0.0

        # Average finish (classified or non-null)
        avg_finish = None
        if not d.empty and "positionOrder" in d.columns:
            finishes = d["positionOrder"].dropna()
            if not finishes.empty:
                avg_finish = round(float(finishes.mean()), 2)

        # Average grid
        avg_grid = None
        if not d.empty and "grid" in d.columns:
            grids = d["grid"].replace(0, pd.NA).dropna().astype(float)
            if not grids.empty:
                avg_grid = round(float(grids.mean()), 2)

        return {
            "races": len(d),
            "points": points,
            "wins": wins,
            "podiums": podiums,
            "poles": poles,
            "avg_finish": avg_finish,
            "avg_grid": avg_grid,
        }

    # Head-to-head shared races
    shared_races = set(d1_data["raceId"]).intersection(set(d2_data["raceId"]))
    d1_ahead = 0
    d2_ahead = 0
    d1_qualy_ahead = 0
    d2_qualy_ahead = 0

    for r_id in shared_races:
        r1 = d1_data[d1_data["raceId"] == r_id].iloc[0]
        r2 = d2_data[d2_data["raceId"] == r_id].iloc[0]

        p1 = r1.get("positionOrder", 9999)
        p2 = r2.get("positionOrder", 9999)
        if p1 < p2:
            d1_ahead += 1
        elif p2 < p1:
            d2_ahead += 1

        g1 = r1.get("grid", 0)
        g2 = r2.get("grid", 0)
        if g1 > 0 and g2 > 0:
            if g1 < g2:
                d1_qualy_ahead += 1
            elif g2 < g1:
                d2_qualy_ahead += 1

    d1_name = f"{d1_row['forename']} {d1_row['surname']}"
    d2_name = f"{d2_row['forename']} {d2_row['surname']}"

    return {
        "season": season,
        "shared_races_count": len(shared_races),
        "head_to_head": {
            "races_finished_ahead": {d1_name: d1_ahead, d2_name: d2_ahead},
            "qualifying_ahead": {d1_name: d1_qualy_ahead, d2_name: d2_qualy_ahead},
        },
        "driver1": {"name": d1_name, "id": d1_id, **_stats(d1_data)},
        "driver2": {"name": d2_name, "id": d2_id, **_stats(d2_data)},
    }


@mcp.tool()
def get_race_results(
    season_year: int,
    round_or_name: Union[int, str],
) -> Dict[str, Any]:
    """Get official classification and results for a specific Formula 1 Grand Prix.

    Args:
        season_year: Championship season year (e.g. 2021).
        round_or_name: The race round number (e.g. 1 or 22) or race name/circuit (e.g. 'Monaco', 'Abu Dhabi').
    """
    results, races, drivers, _, constructors, _ = load_data()

    season_races = races[races["year"] == season_year]
    if season_races.empty:
        raise ValueError(f"No races found for season {season_year}.")

    # Match race
    matched_race = None
    if isinstance(round_or_name, int) or (isinstance(round_or_name, str) and round_or_name.strip().isdigit()):
        rnd = int(round_or_name)
        match = season_races[season_races["round"] == rnd]
        if not match.empty:
            matched_race = match.iloc[0]
    else:
        query = str(round_or_name).strip().lower()
        match = season_races[season_races["name"].str.lower().str.contains(query, na=False)]
        if not match.empty:
            matched_race = match.iloc[0]

    if matched_race is None:
        raise ValueError(
            f"Race '{round_or_name}' not found in season {season_year}. "
            f"Available rounds: {season_races['round'].tolist()}."
        )

    race_id = int(matched_race["raceId"])
    race_results = results[results["raceId"] == race_id].copy()
    if race_results.empty:
        return {"race": matched_race["name"], "round": int(matched_race["round"]), "results": []}

    # Merge drivers and constructors
    race_results = pd.merge(
        race_results,
        drivers[["driverId", "forename", "surname", "code"]],
        on="driverId",
        how="left",
    )
    race_results = pd.merge(
        race_results,
        constructors[["constructorId", "name"]].rename(columns={"name": "constructor"}),
        on="constructorId",
        how="left",
    )
    race_results = race_results.sort_values("positionOrder")

    classification = []
    for _, row in race_results.iterrows():
        pos_str = str(row.get("positionText", row.get("positionOrder", "DNF")))
        classification.append({
            "position": pos_str,
            "driver": f"{row['forename']} {row['surname']}",
            "code": str(row.get("code", "")),
            "constructor": str(row.get("constructor", "Unknown")),
            "grid": int(row["grid"]) if pd.notna(row.get("grid")) else None,
            "laps": int(row["laps"]) if pd.notna(row.get("laps")) else 0,
            "time_or_status": str(row.get("time", "") if pd.notna(row.get("time")) else row.get("statusId", "")),
            "points": float(row["points"]) if pd.notna(row.get("points")) else 0.0,
        })

    return {
        "race_name": matched_race["name"],
        "year": int(matched_race["year"]),
        "round": int(matched_race["round"]),
        "date": str(matched_race.get("date", "")),
        "results": classification,
    }


@mcp.tool()
def list_points_systems() -> List[Dict[str, Any]]:
    """List historical Formula 1 points systems available for recalculations.

    Returns the points allocation per finishing place and era rules.
    """
    systems = [
        {
            "id": "1950",
            "name": "1950-1959",
            "points": [8, 6, 4, 3, 2],
            "description": "Top 5 scored, plus 1 point for fastest lap (often shared).",
        },
        {
            "id": "1960",
            "name": "1960",
            "points": [8, 6, 4, 3, 2, 1],
            "description": "Top 6 scored, 6th place introduced, fastest lap point dropped.",
        },
        {
            "id": "1961",
            "name": "1961-1990",
            "points": [9, 6, 4, 3, 2, 1],
            "description": "Classic system: 9 points for win, top 6 scored.",
        },
        {
            "id": "1991",
            "name": "1991-2002",
            "points": [10, 6, 4, 3, 2, 1],
            "description": "10 points for win, Senna/Schumacher era.",
        },
        {
            "id": "2003",
            "name": "2003-2009",
            "points": [10, 8, 6, 5, 4, 3, 2, 1],
            "description": "Top 8 scored, narrower gap between P1 and P2.",
        },
        {
            "id": "2010",
            "name": "2010-2018",
            "points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
            "description": "Modern system: 25 points for win, top 10 scored.",
        },
        {
            "id": "2019",
            "name": "2019-2024",
            "points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
            "description": "Modern system + 1 point for fastest lap if finishing in top 10.",
        },
        {
            "id": "2025",
            "name": "2025+",
            "points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
            "description": "Fastest lap point removed from 2025 onward.",
        },
    ]
    return systems


@mcp.tool()
def get_season_races(season_year: int) -> List[Dict[str, Any]]:
    """Get the full race calendar and round schedule for any season."""
    _, races, _, _, _, _ = load_data()
    season_races = races[races["year"] == season_year].sort_values("round")
    if season_races.empty:
        return []

    calendar = []
    for _, r in season_races.iterrows():
        calendar.append({
            "round": int(r["round"]),
            "race_id": int(r["raceId"]),
            "name": r["name"],
            "date": str(r.get("date", "")),
            "circuit_id": int(r["circuitId"]) if pd.notna(r.get("circuitId")) else None,
        })
    return calendar


@mcp.tool()
def get_season_report_url(
    season: int,
    points_system_name: str = "Modern",
) -> Dict[str, Any]:
    """Retrieve or generate a download URL for a pre-generated F1 season simulation report.

    If Google Cloud Storage (GCS) is enabled, returns a time-limited Signed URL.
    Otherwise, returns local availability and file details.

    Args:
        season: Championship year (e.g. 2021, 2012, 1994).
        points_system_name: Name of the points scoring system (e.g. 'Modern', '1991-2002', '1981-1990').
    """
    blob_name = gcs_storage.get_report_blob_name(season, points_system_name)
    filename = Path(blob_name).name

    if gcs_storage.is_gcs_enabled():
        exists = gcs_storage.report_exists(blob_name)
        if not exists:
            return {
                "available": False,
                "season": season,
                "points_system": points_system_name,
                "filename": filename,
                "message": f"Report '{filename}' has not been generated or cached yet. Run season simulation via API first.",
            }
        signed_url = gcs_storage.generate_report_signed_url(blob_name)
        return {
            "available": True,
            "season": season,
            "points_system": points_system_name,
            "filename": filename,
            "download_url": signed_url,
            "storage_type": "gcs",
            "expires_in_minutes": gcs_storage.get_signed_url_expiration_minutes(),
        }

    # Fallback to local exports
    local_path = Path("exports") / filename
    if local_path.exists():
        return {
            "available": True,
            "season": season,
            "points_system": points_system_name,
            "filename": filename,
            "local_path": str(local_path),
            "storage_type": "local",
            "size_bytes": local_path.stat().st_size,
        }

    return {
        "available": False,
        "season": season,
        "points_system": points_system_name,
        "filename": filename,
        "message": f"Report '{filename}' not found in local exports or GCS. Run season simulation to generate.",
    }


@mcp.tool()
def list_available_season_reports() -> List[Dict[str, Any]]:
    """List all available pre-generated championship simulation PDF reports (GCS or local)."""
    if gcs_storage.is_gcs_enabled():
        return gcs_storage.list_stored_reports()

    reports = []
    exports_dir = Path("exports")
    if exports_dir.exists():
        for p in exports_dir.glob("*.pdf"):
            reports.append({
                "filename": p.name,
                "blob_name": f"season_reports/{p.name}",
                "size_bytes": p.stat().st_size,
                "storage_type": "local",
            })
    return reports


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource("f1://seasons")
def list_available_seasons() -> str:
    """List all Formula 1 World Championship seasons available in the dataset."""
    _, races, _, _, _, _ = load_data()
    seasons = sorted(races["year"].unique().tolist(), reverse=True)
    return json.dumps({
        "total_seasons": len(seasons),
        "earliest": min(seasons),
        "latest": max(seasons),
        "seasons": seasons,
    }, indent=2)


@mcp.resource("f1://points-systems")
def resource_points_systems() -> str:
    """List historical scoring systems as a JSON resource."""
    return json.dumps(list_points_systems(), indent=2)


@mcp.resource("f1://reports")
def resource_season_reports() -> str:
    """List all available season simulation PDF reports as a JSON resource."""
    reports = list_available_season_reports()
    storage_type = "gcs" if gcs_storage.is_gcs_enabled() else "local"
    return json.dumps({
        "storage_type": storage_type,
        "total_reports": len(reports),
        "reports": reports,
    }, indent=2)


# ---------------------------------------------------------------------------
# MCP Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def compare_championship_eras(season: int, alternate_system_name: str) -> str:
    """Prompt template to compare an F1 championship outcome under alternate points rules."""
    return (
        f"Analyze the outcome of the {season} Formula One World Championship.\n\n"
        f"1. Use the `calculate_championship_standings` tool to recalculate the {season} standings "
        f"under the '{alternate_system_name}' points system.\n"
        f"2. Compare the recalculated champion and top 3 against what historically occurred.\n"
        f"3. Explain which drivers gain or lose the most positions/points under this scoring system, "
        f"highlighting win value, consistency rewards, or reliability impacts.\n"
        f"4. Provide detailed race context for any pivotal Grands Prix that swung the championship."
    )


# ---------------------------------------------------------------------------
# CLI stdio Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Runs the MCP server in standard I/O mode for Claude Desktop / Cursor / Antigravity
    mcp.run()
