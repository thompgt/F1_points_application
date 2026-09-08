"""Unit tests for FastMCP server tools, resources, and endpoints."""

import json
import pytest
from fastapi.testclient import TestClient

import mcp_server
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_mcp_list_points_systems():
    """Verify list_points_systems returns all historical presets."""
    systems = mcp_server.list_points_systems()
    assert isinstance(systems, list)
    assert len(systems) >= 6
    ids = [s["id"] for s in systems]
    assert "1950" in ids
    assert "1991" in ids
    assert "2010" in ids
    assert "2025" in ids


def test_mcp_calculate_championship_standings_2021():
    """Verify 2021 championship recalculation produces expected top finishers."""
    standings = mcp_server.calculate_championship_standings(season_year=2021, top_n=5)
    assert len(standings) == 5
    p1 = standings[0]
    p2 = standings[1]

    assert p1["position"] == 1
    assert "Verstappen" in p1["driver"]
    assert p1["wins"] == 10
    assert p1["points"] == 383.5

    assert p2["position"] == 2
    assert "Hamilton" in p2["driver"]
    assert p2["wins"] == 8
    assert p2["points"] == 379.5


def test_mcp_calculate_championship_standings_custom_rules():
    """Verify calculating 2021 under 1991-2002 10-6-4-3-2-1 rules."""
    system_1991 = [10, 6, 4, 3, 2, 1]
    standings = mcp_server.calculate_championship_standings(
        season_year=2021,
        points_system=system_1991,
        top_n=3,
    )
    assert len(standings) == 3
    # Top two should still be classified with positive points
    assert standings[0]["points"] > 0
    assert standings[1]["points"] > 0
    assert standings[0]["points"] > standings[1]["points"]


def test_mcp_head_to_head_comparison():
    """Verify head-to-head comparison between Hamilton and Rosberg in 2016."""
    h2h = mcp_server.get_driver_head_to_head("Hamilton", "Rosberg", season=2016)
    assert h2h["season"] == 2016
    assert h2h["shared_races_count"] == 21

    # Check finished ahead metrics
    finished_ahead = h2h["head_to_head"]["races_finished_ahead"]
    assert "Lewis Hamilton" in finished_ahead
    assert "Nico Rosberg" in finished_ahead
    assert finished_ahead["Lewis Hamilton"] == 11
    assert finished_ahead["Nico Rosberg"] == 10

    # Check points
    assert h2h["driver1"]["points"] == 383.0
    assert h2h["driver2"]["points"] == 391.0


def test_mcp_head_to_head_validation():
    """Verify invalid comparisons raise errors."""
    with pytest.raises(ValueError, match="Cannot compare a driver with themselves"):
        mcp_server.get_driver_head_to_head("Hamilton", "Hamilton")

    with pytest.raises(ValueError, match="not found"):
        mcp_server.get_driver_head_to_head("NonExistentDriver12345", "Rosberg")


def test_mcp_get_race_results():
    """Verify race results lookup by round and by name."""
    # Lookup round 1 of 2021
    res_r1 = mcp_server.get_race_results(2021, 1)
    assert res_r1["year"] == 2021
    assert res_r1["round"] == 1
    assert len(res_r1["results"]) > 0
    winner = res_r1["results"][0]
    assert winner["position"] == "1"

    # Lookup by name: Abu Dhabi
    res_ad = mcp_server.get_race_results(2021, "Abu Dhabi")
    assert "Abu Dhabi" in res_ad["race_name"]
    assert len(res_ad["results"]) > 0
    assert "Verstappen" in res_ad["results"][0]["driver"]


def test_mcp_get_season_races():
    """Verify getting season race calendar."""
    calendar = mcp_server.get_season_races(2021)
    assert len(calendar) == 22
    assert calendar[0]["round"] == 1
    assert calendar[-1]["round"] == 22


def test_mcp_resources_and_prompts():
    """Verify resource definitions and prompt generation."""
    seasons_json = mcp_server.list_available_seasons()
    data = json.loads(seasons_json)
    assert "seasons" in data
    assert 2021 in data["seasons"]

    points_json = mcp_server.resource_points_systems()
    systems = json.loads(points_json)
    assert isinstance(systems, list)

    prompt = mcp_server.compare_championship_eras(2021, "1991-2002")
    assert "2021" in prompt
    assert "1991-2002" in prompt


def test_mcp_sse_mount(client):
    """Verify /mcp sub-application is mounted and handling requests."""
    # Check that /mcp is in app routes
    mcp_routes = [r for r in app.routes if getattr(r, "path", "") == "/mcp"]
    assert len(mcp_routes) == 1

    # Verify sub-app endpoints exist
    sub_routes = [r.path for r in mcp_routes[0].app.routes]
    assert "/sse" in sub_routes
    assert "/messages" in sub_routes

    # POST to messages without session should reach MCP handler (returning 400 Invalid Content-Type or session)
    response = client.post("/mcp/messages/")
    assert response.status_code == 400


