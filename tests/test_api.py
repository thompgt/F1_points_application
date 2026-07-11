import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ============================================================================
# /api/seasons
# ============================================================================

def test_seasons():
    resp = client.get('/api/seasons')
    assert resp.status_code == 200
    data = resp.json()
    assert 'seasons' in data
    assert isinstance(data['seasons'], list)


# ============================================================================
# /api/points-systems
# ============================================================================

def test_points_systems():
    resp = client.get('/api/points-systems')
    assert resp.status_code == 200
    data = resp.json()
    assert 'points_systems' in data
    for key in ('modern', 'classic', 'pre_1991', 'custom'):
        assert key in data['points_systems']


# ============================================================================
# /api/calculate-standings
# ============================================================================

def test_calculate_standings_valid():
    resp = client.post('/api/calculate-standings', json={"season_year": 2009})
    assert resp.status_code == 200
    data = resp.json()
    assert 'standings' in data


def test_calculate_standings_no_data_returns_404():
    # 2029 passes Pydantic's range validation but has no underlying data.
    resp = client.post('/api/calculate-standings', json={"season_year": 2029})
    assert resp.status_code == 404


def test_calculate_standings_missing_season_year_returns_422():
    resp = client.post('/api/calculate-standings', json={})
    assert resp.status_code == 422


def test_calculate_standings_season_out_of_range_returns_422():
    resp = client.post('/api/calculate-standings', json={"season_year": 1800})
    assert resp.status_code == 422


# ============================================================================
# /api/races
# ============================================================================

def test_races_valid_season():
    resp = client.get('/api/races?season=2009')
    assert resp.status_code == 200
    data = resp.json()
    assert 'races' in data
    assert isinstance(data['races'], list)
    if data['races']:
        assert 'year' in data['races'][0]


def test_races_missing_season_returns_422():
    resp = client.get('/api/races')
    assert resp.status_code == 422


def test_races_season_out_of_range_returns_422():
    resp = client.get('/api/races?season=1800')
    assert resp.status_code == 422


# ============================================================================
# /api/race-results
# ============================================================================

def test_race_results_api():
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


def test_race_results_requires_race_number_or_id():
    resp = client.post('/api/race-results', json={"season_year": 2009})
    assert resp.status_code == 422


def test_race_results_unknown_race_id_returns_404():
    resp = client.post('/api/race-results', json={"season_year": 2009, "race_id": 999999})
    assert resp.status_code == 404


# ============================================================================
# /api/drivers
# ============================================================================

def test_drivers_all():
    resp = client.get('/api/drivers')
    assert resp.status_code == 200
    data = resp.json()
    assert 'drivers' in data
    assert isinstance(data['drivers'], list)
    assert len(data['drivers']) > 0


def test_drivers_filtered_by_season():
    resp = client.get('/api/drivers?season=2009')
    assert resp.status_code == 200
    data = resp.json()
    assert 'drivers' in data


# ============================================================================
# /api/head-to-head
# ============================================================================

def test_head_to_head_basic():
    # pick two driver ids from dataset (1 and 3 are typical)
    resp = client.get('/api/head-to-head?driver1_id=1&driver2_id=3&mode=season')
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        assert 'driver1_stats' in data and 'driver2_stats' in data


def test_head_to_head_same_driver_returns_422():
    resp = client.get('/api/head-to-head?driver1_id=1&driver2_id=1')
    assert resp.status_code == 422


# ============================================================================
# /api/h2h-wikipedia
# ============================================================================

def test_h2h_wikipedia():
    resp = client.get('/api/h2h-wikipedia?driver1=1&driver2=3')
    assert resp.status_code == 200
    data = resp.json()
    assert 'summary' in data


# ============================================================================
# /api/race/{race_id}
# ============================================================================

def test_race_detail_valid():
    resp = client.get('/api/race/1')
    assert resp.status_code == 200
    data = resp.json()
    assert data['raceId'] == 1
    assert 'results' in data


def test_race_detail_not_found():
    resp = client.get('/api/race/999999')
    assert resp.status_code == 404


# ============================================================================
# Health probes
# ============================================================================

def test_health():
    resp = client.get('/health')
    assert resp.status_code == 200
    data = resp.json()
    assert data['status'] == 'healthy'


def test_ready():
    resp = client.get('/ready')
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert data['status'] in ('ready', 'not_ready')
    assert 'checks' in data
    assert (resp.status_code == 200) == (data['status'] == 'ready')


def test_live():
    resp = client.get('/live')
    assert resp.status_code == 200
    data = resp.json()
    assert data['status'] == 'alive'


def test_health_detailed():
    resp = client.get('/health/detailed')
    assert resp.status_code == 200
    data = resp.json()
    assert 'database' in data and 'cache' in data and 'data_files' in data
