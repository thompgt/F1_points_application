"""Unit tests for the What-If Scenario position promotion and scoring in ``scoring.py``."""

import pandas as pd

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

    # Verify races_summary contains pole_sitter, winner, p2, p3
    races_summary = data["races_summary"]
    assert len(races_summary) > 0
    first_race = races_summary[0]
    assert "pole_sitter" in first_race
    assert "original_pole_sitter" in first_race
    assert "what_if_winner" in first_race
    assert "original_winner" in first_race
    assert "what_if_p2" in first_race
    assert "original_p2" in first_race
    assert "what_if_p3" in first_race
    assert "original_p3" in first_race
    assert "winner_changed" in first_race
    assert "pole_changed" in first_race


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


def test_what_if_tab_html_rendered():
    """Verify that the What-If Scenario tab and its interactive elements are in index.html."""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "What-If Scenario Simulator" in html
    assert "whatIfSeasonSelect" in html
    assert "whatIfDriversDropdownBtn" in html
    assert "whatIfPointsSystemSelect" in html
    assert "whatIfStandingsTable" in html
    assert "whatIfBattlePlot" in html
    assert "whatIfRaceResultsTable" in html


def test_promote_positions_without_race_id_column():
    """Verify promote_positions works when raceId column is omitted."""
    frame = pd.DataFrame([
        {"driverId": 10, "positionText": "1", "positionOrder": 1, "rank": 0},
        {"driverId": 20, "positionText": "2", "positionOrder": 2, "rank": 0},
    ])
    frame["position"] = [1.0, 2.0]
    promoted = promote_positions(frame, excluded_driver_ids=[10])
    row_20 = promoted[promoted["driverId"] == 20].iloc[0]
    assert row_20["position"] == 1
    assert row_20["position_delta"] == 1


def test_promote_positions_all_drivers_excluded():
    """Verify behavior when every driver in a race is excluded."""
    race = make_race([
        (1, 1, "1", 1, 0),
        (1, 2, "2", 2, 0),
    ])
    promoted = promote_positions(race, excluded_driver_ids=[1, 2])
    assert promoted["is_excluded"].all()
    assert promoted["position"].isna().all()
    assert (promoted["positionText"] == "EXC").all()


def test_promote_positions_multi_race_independent():
    """Verify position promotions are independently calculated for each race."""
    multi_race = make_race([
        # Race 1
        (1, 100, "1", 1, 0),
        (1, 200, "2", 2, 0),
        # Race 2
        (2, 200, "1", 1, 0),
        (2, 100, "2", 2, 0),
    ])
    # Exclude driver 100 only
    promoted = promote_positions(multi_race, excluded_driver_ids=[100])

    r1_200 = promoted[(promoted["raceId"] == 1) & (promoted["driverId"] == 200)].iloc[0]
    assert r1_200["position"] == 1
    assert r1_200["position_delta"] == 1

    r2_200 = promoted[(promoted["raceId"] == 2) & (promoted["driverId"] == 200)].iloc[0]
    assert r2_200["position"] == 1
    assert r2_200["position_delta"] == 0  # was already P1


def test_fastest_lap_transfer_to_promoted_p10():
    """If P11 driver had fastest lap, promoting to P10 makes them eligible for FL bonus point."""
    rows = [(1, i, str(i), i, 0) for i in range(1, 11)]
    # Driver 11 is P11 with fastest lap (rank=1)
    rows.append((1, 11, "11", 11, 1))
    race = make_race(rows)

    # Exclude P1 (driver 1). Driver 11 promotes from P11 to P10.
    promoted = promote_positions(race, excluded_driver_ids=[1])
    rules = ScoringRules(points=(25, 18, 15, 12, 10, 8, 6, 4, 2, 1), fastest_lap_point=True)
    scored = adjust_points(promoted, rules)

    row_11 = scored[scored["driverId"] == 11].iloc[0]
    assert row_11["position"] == 10
    # 1 base point for P10 + 1 point for fastest lap = 2.0 points
    assert row_11["adjusted_points"] == 2.0


def test_countback_tie_breaker_with_promoted_results():
    """Verify calculate_standings breaks ties correctly using countback of finishes."""
    df = pd.DataFrame([
        # Driver 1: one P1 finish (25 pts)
        {"year": 2021, "driverId": 1, "driverRef": "d1", "forename": "Driver", "surname": "One", "raceId": 1, "position": 1, "positionOrder": 1, "adjusted_points": 25.0},
        # Driver 2: two P2 finishes (18 + 7 = 25 pts)
        {"year": 2021, "driverId": 2, "driverRef": "d2", "forename": "Driver", "surname": "Two", "raceId": 1, "position": 2, "positionOrder": 2, "adjusted_points": 18.0},
        {"year": 2021, "driverId": 2, "driverRef": "d2", "forename": "Driver", "surname": "Two", "raceId": 2, "position": 2, "positionOrder": 2, "adjusted_points": 7.0},
    ])
    standings = calculate_standings(df, season_year=2021)
    # Both have 25 points, but Driver 1 has a P1 finish vs 0 P1s for Driver 2
    assert standings.iloc[0]["surname"] == "One"
    assert standings.iloc[1]["surname"] == "Two"


import json
from main import create_what_if_battle_chart


def test_create_what_if_battle_chart_limits_to_top_10():
    """Verify that create_what_if_battle_chart produces traces for at most the top 10 drivers."""
    rows = []
    for d_id in range(1, 16):  # 15 drivers
        for r_num in [1, 2, 3]:
            rows.append({
                "raceId": r_num,
                "round": r_num,
                "year": 2021,
                "driverId": d_id,
                "forename": f"Driver{d_id}",
                "surname": f"Last{d_id}",
                "adjusted_points": float(16 - d_id),
            })
    df = pd.DataFrame(rows)
    top_10_ids = list(range(1, 11))

    chart_json = create_what_if_battle_chart(df, top_10_ids, 2021, "Modern")
    assert chart_json is not None
    chart_data = json.loads(chart_json)

    assert "data" in chart_data
    assert "layout" in chart_data
    assert len(chart_data["data"]) == 10
    assert chart_data["data"][0]["name"].startswith("P1:")


def test_create_what_if_battle_chart_empty_or_no_round():
    """Verify create_what_if_battle_chart handles empty data and falls back if round is missing."""
    empty_df = pd.DataFrame(columns=["raceId", "year", "driverId", "adjusted_points", "forename", "surname"])
    assert create_what_if_battle_chart(empty_df, [1, 2], 2021, "Modern") is None

    # Missing 'round' column, relies on raceId
    rows = [
        {"raceId": 101, "year": 2021, "driverId": 1, "forename": "A", "surname": "B", "adjusted_points": 25.0},
        {"raceId": 102, "year": 2021, "driverId": 1, "forename": "A", "surname": "B", "adjusted_points": 18.0},
    ]
    df = pd.DataFrame(rows)
    chart_json = create_what_if_battle_chart(df, [1], 2021, "Modern")
    assert chart_json is not None
    parsed = json.loads(chart_json)
    assert len(parsed["data"]) == 1
    assert parsed["data"][0]["x"] == [1, 2]
    assert parsed["data"][0]["y"] == [25.0, 43.0]


