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
    """2009: Button's title, and the eight-scorer system's own numbers.

    Asserting only that a 'standings' key exists let every scoring bug in the
    audit through. 2009 is a good anchor because the era is fully modelled --
    no fastest-lap point, no sprints -- so the totals must be the real ones.
    """
    resp = client.post(
        '/api/calculate-standings',
        json={"season_year": 2009, "points_system": [10, 8, 6, 5, 4, 3, 2, 1]},
    )
    assert resp.status_code == 200
    data = resp.json()

    standings = data['standings']
    assert standings, "2009 returned an empty championship"
    assert [row['Position'] for row in standings[:3]] == [1, 2, 3]

    champion = standings[0]
    assert champion['surname'] == 'Button'
    assert champion['adjusted_points'] == 95.0
    assert champion['constructor_name'] == 'Brawn'
    assert standings[1]['surname'] == 'Vettel'
    assert standings[1]['adjusted_points'] == 84.0
    assert standings[2]['surname'] == 'Barrichello'
    assert standings[2]['adjusted_points'] == 77.0
    # Malaysia was stopped at 31 laps and paid half points, which is the only
    # reason anyone in 2009 finished on a half.
    webber = next(row for row in standings if row['surname'] == 'Webber')
    assert webber['adjusted_points'] == 69.5

    assert data['points_system_name'] == 'Eight-scorer era (2003-2009)'


def test_standings_are_ordered_by_points_descending():
    resp = client.post('/api/calculate-standings', json={"season_year": 1995})
    totals = [row['adjusted_points'] for row in resp.json()['standings']]
    assert totals == sorted(totals, reverse=True)


def test_a_retirement_inside_the_top_ten_does_not_score(monkeypatch):
    """End-to-end version of the headline finding, on the season that has it.

    1957 Monaco classified a car P7 in positionOrder with an engine failure.
    Under the old positionOrder scoring that entry collected points; the
    championship totals below are what the FIA actually awarded, minus the
    1950s fastest-lap point this app does not model.
    """
    resp = client.post(
        '/api/calculate-standings',
        json={"season_year": 1957, "points_system": [8, 6, 4, 3, 2]},
    )
    assert resp.status_code == 200
    standings = resp.json()['standings']
    assert standings[0]['surname'] == 'Fangio'
    # The real check: 8 races with 8+6+4+3+2 = 23 points on offer each, so the
    # season total is exactly 184 and not a point more. Paying retirements, or
    # paying both halves of a shared drive in full, breaks this immediately.
    assert sum(row['adjusted_points'] for row in standings) == 8 * 23


def test_dnfs_plot_behind_the_last_finisher_of_their_own_race():
    """The timeline chart used a fixed 20 for DNFs.

    Grids ran to 26 cars in the 1950s, so a retirement was drawn *ahead* of
    genuinely classified finishers. The fallback is now one place behind that
    race's own entrant count.
    """
    import json

    import main

    for season, minimum in ((1955, 22), (2021, 20)):
        frame = main.build_enriched_results(season=season)
        chart = json.loads(main.create_race_results_timeline_chart(frame, season))
        plotted = [y for trace in chart['data'] for y in trace['y']]
        assert max(plotted) > minimum, (
            f"{season} plots nothing below P{minimum}; DNFs are being drawn "
            "among the classified finishers"
        )


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


# ============================================================================
# Head-to-head response cache
# ============================================================================

def _sample_payload(marker):
    """A payload shaped like a real head-to-head response."""
    return {
        'driver1_stats': {'avg_finish': marker, 'radar_scores': {}},
        'driver2_stats': {'avg_finish': 0, 'radar_scores': {}},
        'race_by_race': [],
        'cumulative_chart': None,
    }


def _clear_h2h_rows(key):
    import main
    from db import HeadToHeadCache, SessionLocal

    with SessionLocal() as db:
        db.query(HeadToHeadCache).filter_by(
            driver1_id=key[0], driver2_id=key[1], season=key[2], mode=key[3]
        ).delete()
        db.commit()
    if main.REDIS_CLIENT:
        main.REDIS_CLIENT.delete(f"h2h:test:{key[0]}:{key[1]}")


def test_h2h_cache_round_trips():
    import main

    key = (999001, 999002, 1999, 'season')
    _clear_h2h_rows(key)
    try:
        assert main.h2h_cache_get('h2h:test:999001:999002', *key) is None
        main.h2h_cache_put('h2h:test:999001:999002', *key, _sample_payload(1))
        assert main.h2h_cache_get('h2h:test:999001:999002', *key)['driver1_stats']['avg_finish'] == 1
    finally:
        _clear_h2h_rows(key)


def test_h2h_cache_upserts_instead_of_growing():
    """The SQL cache used to INSERT a row per miss and read back the newest.

    Re-seeding the data then meant the table grew forever while serving the
    original answer, because nothing expired or replaced it.
    """
    import main
    from db import HeadToHeadCache, SessionLocal

    key = (999003, 999004, 1999, 'season')
    _clear_h2h_rows(key)
    try:
        for marker in range(1, 4):
            main.h2h_cache_put('h2h:test:999003:999004', *key, _sample_payload(marker))

        with SessionLocal() as db:
            rows = db.query(HeadToHeadCache).filter_by(
                driver1_id=key[0], driver2_id=key[1], season=key[2], mode=key[3]
            ).count()
        assert rows == 1, f"three writes left {rows} rows behind"

        served = main.h2h_cache_get('h2h:test:999003:999004', *key)
        assert served['driver1_stats']['avg_finish'] == 3, "cache served a stale payload"
    finally:
        _clear_h2h_rows(key)


def test_h2h_cache_expires_old_sql_rows(monkeypatch):
    """Redis rows had a 1h TTL; SQL rows had none at all."""
    import main

    key = (999005, 999006, 1999, 'season')
    _clear_h2h_rows(key)
    try:
        main.h2h_cache_put('h2h:test:999005:999006', *key, _sample_payload(1))
        assert main.h2h_cache_get('h2h:test:999005:999006', *key) is not None

        # Re-read with a TTL of zero: the row is now older than the cutoff.
        monkeypatch.setattr(main, 'H2H_CACHE_TTL_SECONDS', 0)
        if main.REDIS_CLIENT:
            main.REDIS_CLIENT.delete('h2h:test:999005:999006')
        assert main.h2h_cache_get('h2h:test:999005:999006', *key) is None
    finally:
        _clear_h2h_rows(key)


def test_h2h_cache_read_survives_a_broken_backend(monkeypatch):
    """A dead cache must degrade to a miss, not a 500 -- but must be counted."""
    import main

    class Broken:
        def get(self, *args, **kwargs):
            raise RuntimeError("redis is down")

        def set(self, *args, **kwargs):
            raise RuntimeError("redis is down")

        def delete(self, *args, **kwargs):
            return None

    monkeypatch.setattr(main, 'REDIS_CLIENT', Broken())
    before = main.metrics.h2h_cache_total.labels(backend='redis', outcome='error')._value.get()
    assert main.h2h_cache_get('h2h:test:broken', 999007, 999008, 1999, 'season') is None
    after = main.metrics.h2h_cache_total.labels(backend='redis', outcome='error')._value.get()
    assert after == before + 1, "a broken cache went unrecorded"


# ============================================================================
# Rate limiter client identification
# ============================================================================

def _limiter(trusted=None):
    from middleware import RateLimitMiddleware

    return RateLimitMiddleware(app=None, trusted_proxies=trusted)


class _FakeRequest:
    def __init__(self, peer, forwarded=None):
        self.client = type('C', (), {'host': peer})()
        self.headers = {'X-Forwarded-For': forwarded} if forwarded else {}


def test_forwarded_header_is_ignored_from_an_untrusted_peer():
    """Otherwise the limiter is decorative: one header buys a fresh bucket."""
    limiter = _limiter()
    request = _FakeRequest('203.0.113.9', forwarded='1.2.3.4')
    assert limiter._get_client_id(request) == '203.0.113.9'


def test_forwarded_header_is_honoured_behind_a_trusted_proxy():
    limiter = _limiter('10.0.0.0/8')
    request = _FakeRequest('10.1.2.3', forwarded='203.0.113.9')
    assert limiter._get_client_id(request) == '203.0.113.9'


def test_spoofed_entries_left_of_the_real_client_are_discarded():
    """A client can prepend anything; only the right-hand hops are trustworthy.

    Here the client claims to be 9.9.9.9, the real edge saw 203.0.113.9, and the
    internal proxy chain is ours. Walking from the right past the trusted hops
    lands on 203.0.113.9, not the value the client made up.
    """
    limiter = _limiter('10.0.0.0/8')
    request = _FakeRequest('10.1.2.3', forwarded='9.9.9.9, 203.0.113.9, 10.4.5.6')
    assert limiter._get_client_id(request) == '203.0.113.9'


def test_a_garbage_forwarded_value_does_not_mint_a_bucket():
    limiter = _limiter()
    request = _FakeRequest('203.0.113.9', forwarded='not-an-ip')
    assert limiter._get_client_id(request) == '203.0.113.9'


def test_unparseable_trusted_proxy_entries_are_dropped_not_trusted():
    limiter = _limiter('not-a-network, 10.0.0.0/8')
    assert limiter._peer_is_trusted_proxy('10.1.2.3') is True
    assert limiter._peer_is_trusted_proxy('203.0.113.9') is False
