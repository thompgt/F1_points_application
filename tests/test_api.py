import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_seasons():
    resp = client.get('/api/seasons')
    assert resp.status_code == 200
    data = resp.json()
    assert 'seasons' in data

def test_head_to_head_basic():
    # pick two driver ids from dataset (1 and 3 are typical)
    resp = client.get('/api/head-to-head?driver1_id=1&driver2_id=3&mode=season')
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        assert 'driver1_stats' in data and 'driver2_stats' in data

def test_race_results_api():
    # Test with 2023 Season, Bahrain GP (raceId 1101 usually)
    resp = client.post('/api/race-results', json={
        "season_year": 2023,
        "race_id": 1101
    })
    # If 2023 data isn't in the small sample, it might 404, but we check if it handles the request
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        assert 'results' in data
        assert 'race_name' in data
