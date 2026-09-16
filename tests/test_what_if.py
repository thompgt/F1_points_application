"""Unit tests for the What-If Scenario position promotion and scoring in ``scoring.py``."""

import pandas as pd
import pytest

import scoring
from scoring import (
    ScoringRules,
    adjust_points,
    calculate_standings,
    promote_positions,
)


def make_race(rows):
    """Build a results frame from (raceId, driverId, positionText, positionOrder, rank) tuples."""
    frame = pd.DataFrame(
        rows,
        columns=["raceId", "driverId", "positionText", "positionOrder", "rank"],
    )
    frame["position"] = pd.to_numeric(frame["positionText"], errors="coerce")
    return frame


def test_single_driver_exclusion_promotes_subsequent_finishers():
    """Excluding P1 should promote P2 to P1, P3 to P2, etc."""
    race = make_race([
        (1, 101, "1", 1, 0),  # Driver 101 finishes P1
        (1, 102, "2", 2, 0),  # Driver 102 finishes P2
        (1, 103, "3", 3, 0),  # Driver 103 finishes P3
        (1, 104, "4", 4, 0),  # Driver 104 finishes P4
    ])

    promoted = promote_positions(race, excluded_driver_ids=[101])

    # Check Driver 101 is excluded
    row_101 = promoted[promoted["driverId"] == 101].iloc[0]
    assert row_101["is_excluded"] is True or row_101["is_excluded"] == 1
    assert pd.isna(row_101["position"])
    assert row_101["positionText"] == "EXC"

    # Check Driver 102 promoted to P1 with +1 delta
    row_102 = promoted[promoted["driverId"] == 102].iloc[0]
    assert row_102["position"] == 1
    assert row_102["positionText"] == "1"
    assert row_102["position_delta"] == 1

    # Check Driver 103 promoted to P2 with +1 delta
    row_103 = promoted[promoted["driverId"] == 103].iloc[0]
    assert row_103["position"] == 2
    assert row_103["position_delta"] == 1


def test_multiple_drivers_excluded():
    """Excluding P1 and P2 should promote P3 to P1 with +2 delta."""
    race = make_race([
        (1, 1, "1", 1, 0),
        (1, 2, "2", 2, 0),
        (1, 3, "3", 3, 0),
        (1, 4, "4", 4, 0),
    ])

    promoted = promote_positions(race, excluded_driver_ids=[1, 2])

    row_3 = promoted[promoted["driverId"] == 3].iloc[0]
    assert row_3["position"] == 1
    assert row_3["positionText"] == "1"
    assert row_3["position_delta"] == 2

    row_4 = promoted[promoted["driverId"] == 4].iloc[0]
    assert row_4["position"] == 2
    assert row_4["positionText"] == "2"
    assert row_4["position_delta"] == 2


def test_retirements_are_not_promoted_into_points():
    """Cars that retired ('R') must remain unclassified and not receive points."""
    race = make_race([
        (1, 1, "1", 1, 0),
        (1, 2, "2", 2, 0),
        (1, 3, "R", 15, 0),  # DNF
        (1, 4, "3", 3, 0),
    ])

    promoted = promote_positions(race, excluded_driver_ids=[1])

    row_ret = promoted[promoted["driverId"] == 3].iloc[0]
    assert pd.isna(row_ret["position"])
    assert row_ret["positionText"] == "R"

    scored = adjust_points(promoted, [25, 18, 15, 12, 10])
    scored_ret = scored[scored["driverId"] == 3].iloc[0]
    assert scored_ret["adjusted_points"] == 0.0


def test_what_if_hybrid_scoring_points_awarded():
    """Verify points are awarded based on the promoted positions under custom or classic rules."""
    race = make_race([
        (1, 10, "1", 1, 0),
        (1, 20, "2", 2, 0),
        (1, 30, "3", 3, 0),
        (1, 40, "4", 4, 0),
        (1, 50, "5", 5, 0),
        (1, 60, "6", 6, 0),
    ])

    # Exclude 10 (P1) and 30 (P3).
    # Promoted order: 20 (P1), 40 (P2), 50 (P3), 60 (P4)
    promoted = promote_positions(race, excluded_driver_ids=[10, 30])
    scored_classic = adjust_points(promoted, [10, 6, 4, 3, 2, 1])

    pts = dict(zip(scored_classic["driverId"], scored_classic["adjusted_points"]))
    assert pts[10] == 0.0   # Excluded
    assert pts[30] == 0.0   # Excluded
    assert pts[20] == 10.0  # Promoted P1
    assert pts[40] == 6.0   # Promoted P2
    assert pts[50] == 4.0   # Promoted P3
    assert pts[60] == 3.0   # Promoted P4


def test_fastest_lap_on_excluded_driver_is_forfeited():
    """If the driver with fastest lap (rank=1) is excluded, they do not get the FL point."""
    race = make_race([
        (1, 1, "1", 1, 1),  # P1 has rank=1 (fastest lap)
        (1, 2, "2", 2, 2),
    ])

    promoted = promote_positions(race, excluded_driver_ids=[1])
    rules = ScoringRules(points=(25, 18, 15), fastest_lap_point=True)
    scored = adjust_points(promoted, rules)

    pts = dict(zip(scored["driverId"], scored["adjusted_points"]))
    assert pts[1] == 0.0
    assert pts[2] == 25.0  # Gets P1 base points, no FL point


from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_api_what_if_2021_exclude_hamilton_and_verstappen():
    """Excluding Verstappen (830) and Hamilton (1) from 2021 should make Bottas champion."""
    resp = client.post(
        "/api/what-if-standings",
        json={
            "season_year": 2021,
            "excluded_driver_ids": [1, 830]
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "what_if_standings" in data
    assert "races_summary" in data
    assert "race_results" in data
    assert "battle_chart" in data
    assert "excluded_drivers" in data

    standings = data["what_if_standings"]
    assert len(standings) > 0
    new_champion = standings[0]
    assert new_champion["surname"] == "Bottas"
    assert new_champion["Position"] == 1
    assert len(data["excluded_drivers"]) == 2


def test_api_what_if_validation_errors():
    """Verify input validation handles empty or invalid payloads."""
    # Empty excluded drivers list
    resp = client.post(
        "/api/what-if-standings",
        json={"season_year": 2021, "excluded_driver_ids": []}
    )
    assert resp.status_code == 422

    # Negative driver id
    resp = client.post(
        "/api/what-if-standings",
        json={"season_year": 2021, "excluded_driver_ids": [-5]}
    )
    assert resp.status_code == 422

