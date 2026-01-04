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

def test_race_api():
    # attempt to find a race id from seasons endpoint
    resp = client.get('/api/seasons')
    if resp.status_code != 200:
        pytest.skip('seasons not available')
    # pick a plausible race id
    resp2 = client.get('/api/race/1')
    assert resp2.status_code in (200, 404)
