import os
from dotenv import load_dotenv

# Load environment variables before importing local modules (e.g. db.py) that
# read them at import time -- otherwise a .env-defined DATABASE_URL etc. is
# silently ignored.
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import plotly.express as px
from plotly.utils import PlotlyJSONEncoder
# Optional fastf1 support for qualifying lap time gaps
try:
    import fastf1
    FASTF1_AVAILABLE = True
except Exception:
    FASTF1_AVAILABLE = False
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import warnings
from functools import lru_cache
from season_simulator import simulate_season
from db import init_db, HeadToHeadCache, SessionLocal, engine

# Import new modules for validation, middleware, and health checks
from validators import (
    StandingsRequest,
    SimulateSeasonRequest,
    RaceResultsRequest
)
from middleware import add_middleware_stack, get_logger
from health import router as health_router
from prometheus_fastapi_instrumentator import Instrumentator
import metrics
import scoring

# Optional Redis
try:
    import redis
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

# initialize DB
init_db()

REDIS_CLIENT = None
if REDIS_AVAILABLE:
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    try:
        REDIS_CLIENT = redis.Redis.from_url(redis_url, decode_responses=True)
        REDIS_CLIENT.ping()
    except Exception:
        REDIS_CLIENT = None

warnings.filterwarnings("ignore")

# Get logger instance
logger = get_logger()

# App configuration
API_VERSION = os.getenv("API_VERSION", "1.0.0")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
ENABLE_RATE_LIMITING = os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true"
# fastf1 fetches qualifying telemetry over the network per driver pair per
# race -- opt-in only, since it can make /api/head-to-head slow/flaky in
# production if left on by default just because the package is installed.
ENABLE_FASTF1_QUALI_GAP = os.getenv("ENABLE_FASTF1_QUALI_GAP", "false").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
# Wildcard origins + credentials is invalid per the CORS spec (and a security
# smell) -- only allow credentialed requests when explicit origins are set.
CORS_ALLOW_CREDENTIALS = "*" not in CORS_ORIGINS

app = FastAPI(
    title="F1 Points Calculator",
    description="Advanced Racing Analytics & Head-to-Head Comparisons API",
    version=API_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Add custom middleware stack (error handling, logging, rate limiting, security)
add_middleware_stack(app, {
    'enable_rate_limiting': ENABLE_RATE_LIMITING,
    'requests_per_minute': int(os.getenv('RATE_LIMIT_PER_MINUTE', '60')),
    'requests_per_hour': int(os.getenv('RATE_LIMIT_PER_HOUR', '1000')),
    'burst_limit': int(os.getenv('RATE_LIMIT_BURST', '10')),
    # Empty by default: X-Forwarded-For is ignored unless the immediate peer is
    # a proxy you have named here. Set it to your load balancer's address range
    # when deploying behind one, or per-client limits collapse into one bucket.
    'trusted_proxies': os.getenv('TRUSTED_PROXIES', ''),
})

# Include health check routes
app.include_router(health_router)

# Prometheus metrics. instrument() adds per-request counters/histograms labelled by
# *route template* (/api/race/{race_id}, not /api/race/1052) -- concrete ids would
# mint a new time series per race. expose() serves the text exposition at /metrics,
# which is why health.py no longer defines a route of that name.
#
# Grouping status codes would collapse 404 and 422 into "4xx", but the error-rate
# panel wants to tell "season not in the dataset" apart from "bad request body".
Instrumentator(
    should_group_status_codes=False,
    excluded_handlers=["/metrics"],  # don't let the scrape inflate its own numbers
).instrument(app).expose(app, include_in_schema=False)

# Mount static files and templates
#app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
DEFAULT_POINTS = scoring.DEFAULT_POINTS

# What "no points_system supplied" means. Not just the array: the modern era also
# pays a point for the fastest lap, so the default has to carry that modifier or
# every 2019-2024 season comes out short.
DEFAULT_RULES = scoring.resolve_points_system("modern")
FIXED_OLLAMA_MODEL = "llama3.1:8b"

# Note: Request models (StandingsRequest, SimulateSeasonRequest, etc.) are now in validators.py

# The six datasets every endpoint is built on. Table name == CSV basename.
DATA_TABLES = ('results', 'races', 'drivers', 'seasons', 'constructors', 'driver_standings')


def _load_from_database():
    """Read the F1 datasets out of the configured database (MySQL by default).

    Raises if any table is missing or empty so the caller can fall back to CSVs.
    """
    frames = []
    with engine.connect() as conn:
        for table in DATA_TABLES:
            df = pd.read_sql_table(table, conn)
            if df.empty:
                raise ValueError(f"table '{table}' is empty -- run scripts/seed_mysql.py")
            frames.append(df)
    return frames


def _load_from_csv():
    """Read the F1 datasets from the seed CSVs in the repo."""
    # Kaggle's exports use a literal \N for missing values.
    return [pd.read_csv(f'{table}.csv', na_values=['\\N']) for table in DATA_TABLES]


@lru_cache(maxsize=1)
def _load_data_cached():
    """Load all necessary F1 datasets.

    The database is the point of retrieval; the CSVs are only the seed data and
    stay in place as a fallback so the app still boots if MySQL is down or has
    not been seeded yet (see scripts/seed_mysql.py).
    """
    try:
        results, races, drivers, seasons, constructors, driver_standings = _load_from_database()
        logger.info(f"Loaded F1 datasets from {engine.dialect.name} database")
    except Exception as exc:
        logger.warning(f"Database load failed ({exc}); falling back to seed CSV files")
        results, races, drivers, seasons, constructors, driver_standings = _load_from_csv()
    return results, races, drivers, seasons, constructors, driver_standings


def load_data():
    """Cache-aware wrapper around the dataset load, for metrics.

    Whether the lru_cache served a call is invisible from the outside, so read
    it off cache_info(): a miss means the process actually re-read every table
    (results.csv is ~26k rows, and a cold load shows up in latency). The
    duration gauge records only misses -- a hit is a dict lookup and averaging
    it in would hide what the cold path costs.
    """
    misses_before = _load_data_cached.cache_info().misses
    started = time.perf_counter()
    frames = _load_data_cached()
    elapsed = time.perf_counter() - started
    was_hit = _load_data_cached.cache_info().misses == misses_before
    metrics.record_data_load(hit=was_hit, duration_seconds=elapsed)
    return frames


# Callers (and tests) that reach for load_data.cache_clear() still find it.
load_data.cache_clear = _load_data_cached.cache_clear
load_data.cache_info = _load_data_cached.cache_info

# The scoring rules themselves live in scoring.py, where they can be tested
# without booting the app -- see tests/test_points.py. Re-exported under the
# names the rest of this module already used.
adjust_points = scoring.adjust_points
calculate_standings = scoring.calculate_standings


@lru_cache(maxsize=16)
def _build_enriched_results(rules, season):
    """Score the results and join on driver, constructor and race metadata.

    Four endpoints used to carry a near-verbatim copy of this block, each one
    scoring and merging all 26k rows on every request only to filter to a single
    season immediately afterwards. Filtering first is the whole optimisation:
    a season is ~400 rows, so the three merges go from 26k-row joins to 400-row
    ones.

    Cached on (rules, season). ``rules`` is either a frozen ScoringRules or a
    tuple of points, both hashable; ``season`` of None means the full dataset,
    which the head-to-head cumulative chart still needs.
    """
    results, races, drivers, _, constructors, _ = load_data()

    if season is not None:
        season_race_ids = races.loc[races['year'] == season, 'raceId']
        results = results[results['raceId'].isin(season_race_ids)]

    adjusted = adjust_points(results, rules, races=races)

    adjusted = pd.merge(
        adjusted,
        drivers[['driverId', 'surname', 'forename']],
        on='driverId'
    )
    adjusted = pd.merge(
        adjusted,
        constructors[['constructorId', 'name']].rename(columns={'name': 'constructor_name'}),
        on='constructorId'
    )

    race_cols = ['raceId', 'year', 'name']
    if 'round' in races.columns:
        race_cols.append('round')

    return pd.merge(adjusted, races[race_cols], on='raceId')


def build_enriched_results(points_system=None, season=None):
    """Cache-fronted wrapper: normalises the points argument and hands back a copy.

    The copy matters -- callers slice, assign and sort the frame they get, and a
    mutation would otherwise be visible to every later request that hit the same
    cache entry.
    """
    if points_system is None:
        rules = DEFAULT_RULES
    elif isinstance(points_system, scoring.ScoringRules):
        rules = points_system
    else:
        rules = tuple(points_system)
    return _build_enriched_results(rules, season).copy()


def create_title_fight_chart(adjusted_results_with_races, season_year, points_system_name):
    """Create a Title Fight chart showing drivers within 10% of the champion, or the runner-up if none qualify."""
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year]
    if season_results.empty:
        return None
    standings = season_results.groupby(['driverId', 'surname', 'forename'], as_index=False)['adjusted_points'].sum()
    standings = standings.sort_values(by='adjusted_points', ascending=False).reset_index(drop=True)
    champion_points = standings.iloc[0]['adjusted_points']
    within_10pct = standings[standings['adjusted_points'] >= 0.9 * champion_points]
    if len(within_10pct) > 1:
        title_fight_driver_ids = within_10pct['driverId'].tolist()
    else:
        title_fight_driver_ids = standings.iloc[:2]['driverId'].tolist()

    # Prepare cumulative points for qualifying drivers
    race_number_col = 'round' if 'round' in season_results.columns else None
    if race_number_col is not None:
        season_results['race_number'] = season_results[race_number_col]
        season_results = season_results.sort_values(by=['race_number', 'positionOrder'])
    else:
        season_results = season_results.sort_values(by=['year', 'raceId', 'positionOrder'])
        season_results['race_number'] = season_results.groupby('year')['raceId'].rank(method='dense').astype(int)

    season_results['driver_label'] = season_results.apply(lambda row: f"{row['forename'][0]}. {row['surname']}", axis=1)
    season_results['cumulative_points'] = season_results.groupby(['driver_label'])['adjusted_points'].cumsum()
    season_results_filtered = season_results[season_results['driverId'].isin(title_fight_driver_ids)]

    # Build serializable traces (plain Python lists) to avoid binary-packed arrays
    traces = []
    # Sort drivers by final cumulative points descending so legend is ordered by points
    driver_order = (
        season_results_filtered.groupby('driver_label')['cumulative_points']
        .max()
        .sort_values(ascending=False)
        .index
        .tolist()
    )
    for driver_label in driver_order:
        grp = season_results_filtered[season_results_filtered['driver_label'] == driver_label]
        x = grp['race_number'].astype(int).tolist()
        y = grp['cumulative_points'].astype(float).tolist()
        traces.append({
            'x': x,
            'y': y,
            'mode': 'lines+markers',
            'name': driver_label,
            'type': 'scatter',
            'marker': {'symbol': 'circle'},
            'line': {'dash': 'solid'}
        })

    layout = {
        'title': f'Title Fight: Cumulative Points for Top Contenders in {season_year} ({points_system_name})',
        'height': 400,
        'showlegend': True,
        'legend': dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
        'xaxis': {'title': 'Race Number'},
        'yaxis': {'title': 'Cumulative Points'},
    }
    return json.dumps({'data': traces, 'layout': layout}, cls=PlotlyJSONEncoder)

def create_cumulative_points_chart(adjusted_results_with_races, season_year, points_system_name, selected_driver_ids: Optional[List[int]] = None):
    """Create a cumulative points chart using Plotly"""
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year].copy()
    
    if season_results.empty:
        return None
    
    # Use 'round' column if available, otherwise create race numbers from raceId
    if 'round' in season_results.columns:
        season_results = season_results.sort_values(by=['round', 'driverId'])
        season_results['race_number'] = season_results['round']
    else:
        # Create consistent race numbering
        race_order = season_results[['raceId']].drop_duplicates().sort_values('raceId').reset_index(drop=True)
        race_order['race_number'] = race_order.index + 1
        season_results = pd.merge(season_results, race_order, on='raceId')
        season_results = season_results.sort_values(by=['race_number', 'driverId'])
    
    # Calculate cumulative points for each driver
    season_results = season_results.sort_values(['driverId', 'race_number'])
    season_results['cumulative_points'] = season_results.groupby('driverId')['adjusted_points'].cumsum()
    
    # Determine which drivers to include
    if selected_driver_ids:
        top_drivers_list = selected_driver_ids
    else:
        # Get top 10 drivers by total points
        driver_totals = (
            season_results.groupby('driverId')['adjusted_points']
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )
        top_drivers_list = driver_totals.index.tolist()
    
    # Filter for selected drivers
    season_results_filtered = season_results[season_results['driverId'].isin(top_drivers_list)].copy()
    
    # Create driver label for legend
    season_results_filtered['driver_label'] = season_results_filtered['forename'] + ' ' + season_results_filtered['surname']
    
    # Sort by race number for proper line plotting
    season_results_filtered = season_results_filtered.sort_values(['driver_label', 'race_number'])
    
    # Create the plot
    fig = px.line(
        season_results_filtered,
        x='race_number',
        y='cumulative_points',
        color='driver_label',
        title=f'Cumulative Points - {season_year} Season ({points_system_name})',
        labels={'race_number': 'Race Number', 'cumulative_points': 'Cumulative Points', 'driver_label': 'Driver'},
        markers=True
    )
    fig.update_layout(
        height=600,
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
        xaxis_title='Race Number',
        yaxis_title='Cumulative Points',
        template='plotly_white'
    )
    
    # Build serializable traces (plain Python lists) to avoid binary-packed arrays
    traces = []
    # Sort drivers by final cumulative points descending so legend is ordered by points
    driver_order = (
        season_results_filtered.groupby('driver_label')['cumulative_points']
        .max()
        .sort_values(ascending=False)
        .index
        .tolist()
    )
    for driver_label in driver_order:
        grp = season_results_filtered[season_results_filtered['driver_label'] == driver_label]
        x = grp['race_number'].astype(int).tolist()
        y = grp['cumulative_points'].astype(float).tolist()
        traces.append({
            'x': x,
            'y': y,
            'mode': 'lines+markers',
            'name': driver_label,
            'type': 'scatter',
            'marker': {'symbol': 'circle'},
            'line': {'dash': 'solid'}
        })

    layout = fig.to_dict().get('layout', {})
    return json.dumps({'data': traces, 'layout': layout}, cls=PlotlyJSONEncoder)

def create_points_distribution_chart(standings, season_year, points_system_name):
    """Create a points distribution chart showing top drivers' total points"""
    if standings.empty:
        return None
    
    # Take top 15 drivers and create full name label
    top_standings = standings.head(15).copy()
    top_standings['driver_name'] = top_standings['forename'] + ' ' + top_standings['surname']
    
    # Ensure bars are sorted by points descending
    top_standings = top_standings.sort_values(by='adjusted_points', ascending=False)
    fig = px.bar(
        top_standings,
        x='driver_name',
        y='adjusted_points',
        title=f'Final Points Distribution - {season_year} Season ({points_system_name})',
        labels={'driver_name': 'Driver', 'adjusted_points': 'Total Points'},
        color='adjusted_points',
        color_continuous_scale='Viridis',
        text='adjusted_points'
    )
    
    fig.update_traces(texttemplate='%{text:.1f}', textposition='outside')
    
    fig.update_layout(
        height=500,
        xaxis_tickangle=-45,
        showlegend=False,
        xaxis_title='Driver',
        yaxis_title='Total Points',
        template='plotly_white'
    )
    
    # Build serializable bar trace and layout
    x = top_standings['driver_name'].tolist()
    y = top_standings['adjusted_points'].astype(float).tolist()
    traces = [{
        'x': x,
        'y': y,
        'type': 'bar',
        'text': y
    }]
    layout = fig.to_dict().get('layout', {})
    return json.dumps({'data': traces, 'layout': layout}, cls=PlotlyJSONEncoder)

def create_constructors_cumulative_chart(adjusted_results_with_races, season_year, points_system_name):
    """Create constructors cumulative points chart over the season."""
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year].copy()
    if season_results.empty or 'constructor_name' not in season_results.columns:
        return None

    # Use 'round' column if available, otherwise create race numbers
    if 'round' in season_results.columns:
        season_results['race_number'] = season_results['round']
    else:
        # Create consistent race numbering
        race_order = season_results[['raceId']].drop_duplicates().sort_values('raceId').reset_index(drop=True)
        race_order['race_number'] = race_order.index + 1
        season_results = pd.merge(season_results, race_order, on='raceId')

    # Sum all drivers' points per constructor per race
    constructor_race_points = season_results.groupby(['race_number', 'constructor_name'], as_index=False)['adjusted_points'].sum()
    constructor_race_points = constructor_race_points.sort_values(by=['constructor_name', 'race_number'])
    
    # Calculate cumulative sum for each constructor
    constructor_race_points['cumulative_points'] = constructor_race_points.groupby('constructor_name')['adjusted_points'].cumsum()
    
    # Get top 10 constructors by final total points
    final_totals = constructor_race_points.groupby('constructor_name')['cumulative_points'].max().sort_values(ascending=False).head(10)
    top_constructors = final_totals.index.tolist()
    
    constructor_filtered = constructor_race_points[constructor_race_points['constructor_name'].isin(top_constructors)]

    fig = px.line(
        constructor_filtered,
        x='race_number',
        y='cumulative_points',
        color='constructor_name',
        title=f'Constructors Cumulative Points - {season_year} Season ({points_system_name})',
        labels={'race_number': 'Race Number', 'cumulative_points': 'Cumulative Points', 'constructor_name': 'Constructor'},
        markers=True
    )
    fig.update_layout(
        height=600,
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
        xaxis_title='Race Number',
        yaxis_title='Cumulative Points',
        template='plotly_white'
    )
    # Build serializable traces for constructors cumulative chart, ordered by final totals descending
    traces = []
    for constructor_name in final_totals.index.tolist():
        grp = constructor_filtered[constructor_filtered['constructor_name'] == constructor_name]
        x = grp['race_number'].astype(int).tolist()
        y = grp['cumulative_points'].astype(float).tolist()
        traces.append({
            'x': x,
            'y': y,
            'mode': 'lines+markers',
            'name': constructor_name,
            'type': 'scatter',
            'marker': {'symbol': 'circle'},
            'line': {'dash': 'solid'}
        })

    layout = fig.to_dict().get('layout', {})
    return json.dumps({'data': traces, 'layout': layout}, cls=PlotlyJSONEncoder)


def create_race_results_timeline_chart(adjusted_results_with_races, season_year, selected_driver_ids: Optional[List[int]] = None):
    """Create a race results timeline chart showing finishing positions across the season."""
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year].copy()
    
    if season_results.empty:
        return None
    
    # Where to plot a car that did not finish. A fixed 20 put DNFs *ahead* of
    # classified finishers in any season with a bigger grid -- 22 to 26 through
    # the 1990s, 30-odd in the 1950s. Use one place behind the last entrant of
    # that particular race instead, computed before the driver filter below so
    # it reflects the whole field rather than the drivers on screen.
    entrants_per_race = season_results.groupby('raceId')['driverId'].nunique()
    dnf_position = season_results['raceId'].map(entrants_per_race).fillna(20) + 1
    season_results['plot_position'] = (
        scoring.classified_position(season_results).fillna(dnf_position).astype(int)
    )

    # Use 'round' column if available
    if 'round' in season_results.columns:
        season_results['race_number'] = season_results['round']
    else:
        race_order = season_results[['raceId']].drop_duplicates().sort_values('raceId').reset_index(drop=True)
        race_order['race_number'] = race_order.index + 1
        season_results = pd.merge(season_results, race_order, on='raceId')

    # Create driver label
    season_results['driver_label'] = season_results['forename'] + ' ' + season_results['surname']
    
    # Filter by selected drivers or get top 10
    if selected_driver_ids:
        season_results = season_results[season_results['driverId'].isin(selected_driver_ids)]
    else:
        # Get top 10 by total points
        driver_totals = (
            season_results.groupby('driverId')['adjusted_points']
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )
        season_results = season_results[season_results['driverId'].isin(driver_totals.index)]
    
    # Sort by race number and position
    season_results = season_results.sort_values(['driver_label', 'race_number'])
    
    # Create line chart with positions (inverted Y-axis so 1st place is at top)
    fig = px.line(
        season_results,
        x='race_number',
        y='plot_position',
        color='driver_label',
        title=f'Race Results Timeline - {season_year} Season',
        labels={'race_number': 'Race Number', 'plot_position': 'Finishing Position', 'driver_label': 'Driver'},
        markers=True
    )
    
    fig.update_layout(
        height=500,
        yaxis=dict(autorange='reversed'),  # Invert so P1 is at top
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
        xaxis_title='Race Number',
        yaxis_title='Finishing Position',
        template='plotly_white'
    )
    
    # Build serializable traces
    traces = []
    for driver_label in season_results['driver_label'].unique():
        grp = season_results[season_results['driver_label'] == driver_label]
        x = grp['race_number'].astype(int).tolist()
        y = grp['plot_position'].astype(int).tolist()
        traces.append({
            'x': x,
            'y': y,
            'mode': 'lines+markers',
            'name': driver_label,
            'type': 'scatter',
            'marker': {'symbol': 'circle'},
            'line': {'dash': 'solid'}
        })
    
    layout = fig.to_dict().get('layout', {})
    return json.dumps({'data': traces, 'layout': layout}, cls=PlotlyJSONEncoder)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main page"""
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get('/head-to-head', response_class=HTMLResponse)
async def head_to_head(request: Request):
    """Serve the head-to-head comparison page"""
    return templates.TemplateResponse(request, 'head_to_head.html', {"request": request})


@app.get('/race-detail', response_class=HTMLResponse)
async def race_detail(request: Request):
    return templates.TemplateResponse(request, 'race_detail.html', {"request": request})

@app.get("/api/seasons")
async def get_seasons():
    """Get all available seasons"""
    try:
        _, _, _, seasons, _, _ = load_data()
        season_list = seasons['year'].tolist()
        try:
            logger.debug(f"Seasons loaded: min={seasons['year'].min()}, max={seasons['year'].max()}, count={len(season_list)}")
        except Exception:
            logger.debug(f"Seasons loaded count={len(season_list)}")
        return {"seasons": season_list}
    except Exception as e:
        logger.exception(f"Error loading seasons: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/calculate-standings")
async def calculate_standings_api(request: StandingsRequest):
    """Calculate standings for a given season with optional custom points system"""
    try:
        points_system = DEFAULT_POINTS if request.points_system is None else request.points_system

        # Everything from the points adjustment through the standings groupby is
        # the actual "calculate" work, timed as one unit and split by which points
        # system was applied -- a pre-1991 array touches 6 positions, the modern
        # one 10, and the charts downstream are excluded on purpose.
        with metrics.observe_points_calculation(request.points_system):
            adjusted_results_with_races = build_enriched_results(
                request.points_system, season=request.season_year
            )
            standings = calculate_standings(adjusted_results_with_races, request.season_year)

        # Determine primary constructor per driver in the selected season (mode by count of appearances)
        season_rows = adjusted_results_with_races[adjusted_results_with_races['year'] == request.season_year]
        if not season_rows.empty:
            constructor_mode = (
                season_rows
                .groupby(['surname', 'forename', 'constructor_name'], as_index=False)['raceId']
                .count()
                .sort_values(['surname', 'forename', 'raceId'], ascending=[True, True, False])
            )
            # Keep the first (most frequent) constructor per driver
            constructor_mode = constructor_mode.drop_duplicates(subset=['surname', 'forename'], keep='first')
            standings = pd.merge(
                standings,
                constructor_mode[['surname', 'forename', 'constructor_name']],
                on=['surname', 'forename'],
                how='left'
            )
        
        if standings.empty:
            raise HTTPException(status_code=404, detail=f"No data found for season {request.season_year}")
        
        # Create visualizations. The label is baked into every chart title and the
        # PDF, so it has to name what was actually applied -- comparing against the
        # resolved list made an explicitly-posted modern array read as "Custom".
        points_system_name = scoring.points_system_label(request.points_system)
        title_fight_chart = create_title_fight_chart(adjusted_results_with_races, request.season_year, points_system_name)
        cumulative_chart = create_cumulative_points_chart(
            adjusted_results_with_races, request.season_year, points_system_name, request.selected_driver_ids
        )
        distribution_chart = create_points_distribution_chart(standings, request.season_year, points_system_name)
        constructors_cumulative_chart = create_constructors_cumulative_chart(adjusted_results_with_races, request.season_year, points_system_name)
        return {
            "standings": standings.to_dict('records'),
            "title_fight_chart": title_fight_chart,
            "cumulative_chart": cumulative_chart,
            "distribution_chart": distribution_chart,
            "constructors_cumulative_chart": constructors_cumulative_chart,
            "points_system": points_system,
            "points_system_name": points_system_name
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error calculating standings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/points-systems")
async def get_points_systems():
    """The named points systems, each annotated with its exact range and modifiers.

    "Modern (2010-2024)" was one entry covering fifteen seasons that were not
    scored the same way: 2010-2018 had no fastest-lap point and 2019 onwards
    does. The eight-scorer system used from 2003 to 2009 was missing entirely,
    leaving seven seasons a user could not select at all.
    """
    return {
        "points_systems": {
            key: {
                "name": entry["name"],
                "points": entry["points"],
                "years": entry["years"],
                "modifiers": entry["modifiers"],
            }
            for key, entry in scoring.NAMED_POINTS_SYSTEMS.items()
        } | {
            "custom": {
                "name": "Custom",
                "points": [],
                "years": "n/a",
                "modifiers": "Positions only -- no fastest-lap point and no sprint points.",
            }
        }
    }

@app.get("/api/races")
async def get_races(
    season: int = Query(..., ge=1950, le=2030, description="Season year to get races for")
):
    """Get all races for a specific season."""
    try:
        from db import Race, store_races, SessionLocal
        _, races_csv, _, _, _, _ = load_data()
        # Try DB first. `with` rather than a bare close() so the connection goes
        # back to the pool even when the query raises -- under MySQL's pool a
        # leak per failed request exhausts it.
        with SessionLocal() as db:
            race_list = [
                {
                    "raceId": r.raceId,
                    "name": r.name,
                    "year": r.year,
                    "round": r.round,
                    "date": r.date,
                    "circuitId": r.circuitId
                } for r in db.query(Race).filter_by(year=season).order_by(Race.round).all()
            ]
        if race_list:
            return {"races": race_list}
        # Fallback to CSV
        season_races = races_csv[races_csv['year'] == season].copy()
        if season_races.empty:
            return {"races": []}
        if 'round' in season_races.columns:
            season_races = season_races.sort_values('round')
        race_list = []
        for _, race in season_races.iterrows():
            race_list.append({
                "raceId": int(race['raceId']),
                "name": race.get('name', ''),
                "year": season,
                "round": int(race['round']) if 'round' in race and pd.notna(race['round']) else None,
                "date": str(race.get('date', '')) if pd.notna(race.get('date')) else None,
                "circuitId": int(race['circuitId']) if 'circuitId' in race and pd.notna(race['circuitId']) else None
            })
        # Store in DB for future
        store_races(race_list)
        return {"races": race_list}
    except Exception as e:
        logger.exception(f"Error loading races: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/race-results")
async def get_race_results(request: RaceResultsRequest):
    """Get detailed results for a specific race."""
    try:
        results, races, drivers, _, constructors, _ = load_data()
        
        if request.race_id:
            race = races[races['raceId'] == request.race_id]
            if race.empty:
                raise HTTPException(status_code=404, detail=f"Race ID {request.race_id} not found")
            race_id = int(request.race_id)
        else:
            # Find the race by season and round number
            season_races = races[races['year'] == request.season_year]
            if season_races.empty:
                raise HTTPException(status_code=404, detail=f"No races found for season {request.season_year}")
            
            # Find race by round number
            race = season_races[season_races['round'] == request.race_number]
            if race.empty:
                raise HTTPException(status_code=404, detail=f"Race {request.race_number} not found in season {request.season_year}")
            
            race_id = int(race.iloc[0]['raceId'])
        
        race_results = results[results['raceId'] == race_id].copy()
        
        if race_results.empty:
            return {"results": [], "race_name": race.iloc[0].get('name', ''), "round": request.race_number}
        
        # Merge with driver and constructor info
        race_results = pd.merge(
            race_results,
            drivers[['driverId', 'forename', 'surname']],
            on='driverId',
            how='left'
        )
        race_results = pd.merge(
            race_results,
            constructors[['constructorId', 'name']].rename(columns={'name': 'constructor'}),
            on='constructorId',
            how='left'
        )
        
        # Sort by position
        race_results = race_results.sort_values('positionOrder')
        
        result_list = []
        for _, row in race_results.iterrows():
            # Format time
            final_time = None
            if 'time' in row and pd.notna(row['time']):
                final_time = row['time']
            elif 'milliseconds' in row and pd.notna(row['milliseconds']):
                ms = float(row['milliseconds'])
                final_time = f"{ms/1000:.3f}s"
            # Use the actual `position` column to determine finishing (DNF if NaN/null)
            pos_val = None
            if 'position' in row and pd.notna(row['position']):
                try:
                    pos_val = int(row['position'])
                except Exception:
                    pos_val = None

            result_list.append({
                "position": pos_val,
                "driver": f"{row.get('forename', '')} {row.get('surname', '')}",
                "forename": row.get('forename', ''),
                "surname": row.get('surname', ''),
                "constructor": row.get('constructor', ''),
                "constructor_name": row.get('constructor', ''),
                "points": float(row.get('points', 0)),
                "grid": int(row['grid']) if pd.notna(row.get('grid')) else None,
                "final_time": final_time,
                "status": row.get('status', '') if pd.notna(row.get('status')) else None,
                "laps": int(row['laps']) if pd.notna(row['laps']) else None
            })
        
        # Provide race metadata (name, round, date)
        race_row = race.iloc[0]
        race_round = None
        if 'round' in race_row and pd.notna(race_row.get('round')):
            try:
                race_round = int(race_row.get('round'))
            except Exception:
                race_round = None
        race_date = str(race_row.get('date')) if pd.notna(race_row.get('date')) else None

        return {
            "results": result_list,
            "race_name": race_row.get('name', ''),
            "round": race_round,
            "date": race_date,
            "race_id": int(race_row.get('raceId')) if 'raceId' in race_row and pd.notna(race_row.get('raceId')) else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error loading race results: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/drivers")
async def get_drivers(
    season: Optional[int] = Query(default=None, ge=1950, le=2030, description="Filter by season")
):
    """Get drivers list, optionally for a specific season."""
    try:
        results, races, drivers, _, _, _ = load_data()
        if season is not None:
            # Get race IDs for the specific season
            season_races = races[races['year'] == season]
            if season_races.empty:
                return {"drivers": []}
            
            race_ids = season_races['raceId'].tolist()
            # Get drivers who participated in any race of this season
            season_results = results[results['raceId'].isin(race_ids)]
            driver_ids = season_results['driverId'].unique().tolist()
            df = drivers[drivers['driverId'].isin(driver_ids)].copy()
        else:
            df = drivers.copy()
        
        df = df.sort_values(by=['surname', 'forename'])
        return {"drivers": df[['driverId', 'forename', 'surname']].to_dict('records')}
    except Exception as e:
        logger.exception(f"Error loading drivers: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# How long a cached head-to-head answer stays good. Redis always had an hour;
# the SQL rows had no expiry at all, so once the underlying data was re-seeded
# the SQL cache served the old answer forever.
H2H_CACHE_TTL_SECONDS = int(os.getenv('H2H_CACHE_TTL_SECONDS', str(60 * 60)))


def _payload_matches_current_schema(payload) -> bool:
    """Reject rows written before the current head-to-head response shape."""
    return (
        isinstance(payload, dict)
        and isinstance(payload.get('driver1_stats'), dict)
        and 'avg_finish' in payload['driver1_stats']
        and 'radar_scores' in payload['driver1_stats']
    )


def h2h_cache_get(cache_key, driver1_id, driver2_id, season, mode):
    """Read a cached head-to-head payload, or None.

    Redis first, then SQL. Both are best-effort -- a dead cache must not break
    the endpoint -- but "best-effort" used to mean five nested
    `except Exception: pass` blocks, so a broken backend was indistinguishable
    from a miss. Failures are now logged and counted.
    """
    if REDIS_CLIENT:
        try:
            cached = REDIS_CLIENT.get(cache_key)
            if cached:
                payload = json.loads(cached)
                if _payload_matches_current_schema(payload):
                    metrics.record_cache_event('redis', 'hit')
                    return payload
                metrics.record_cache_event('redis', 'stale')
            else:
                metrics.record_cache_event('redis', 'miss')
        except Exception as exc:
            metrics.record_cache_event('redis', 'error', exc)

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=H2H_CACHE_TTL_SECONDS)
        with SessionLocal() as db:
            row = (
                db.query(HeadToHeadCache)
                .filter_by(driver1_id=driver1_id, driver2_id=driver2_id, season=season, mode=mode)
                .order_by(HeadToHeadCache.created_at.desc())
                .first()
            )
            if row is None or not row.response_json:
                metrics.record_cache_event('sql', 'miss')
                return None

            # created_at comes back naive on SQLite and aware on MySQL; normalise
            # before comparing or this raises instead of expiring anything.
            created_at = row.created_at
            if created_at is not None and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at is None or created_at < cutoff:
                metrics.record_cache_event('sql', 'stale')
                return None

            payload = json.loads(row.response_json)
            if not _payload_matches_current_schema(payload):
                metrics.record_cache_event('sql', 'stale')
                return None
            metrics.record_cache_event('sql', 'hit')
            return payload
    except Exception as exc:
        metrics.record_cache_event('sql', 'error', exc)
    return None


def h2h_cache_put(cache_key, driver1_id, driver2_id, season, mode, response):
    """Write a head-to-head payload to both caches.

    The SQL side upserts on the key tuple. It used to INSERT on every miss and
    read back the newest row, so the table grew a row per request forever and
    nothing ever pruned it.
    """
    try:
        serialized = json.dumps(response)
    except Exception as exc:
        metrics.record_cache_event('sql', 'error', exc)
        return

    if REDIS_CLIENT:
        try:
            REDIS_CLIENT.set(cache_key, serialized, ex=H2H_CACHE_TTL_SECONDS)
            metrics.record_cache_event('redis', 'write')
        except Exception as exc:
            metrics.record_cache_event('redis', 'error', exc)

    try:
        with SessionLocal() as db:
            existing = (
                db.query(HeadToHeadCache)
                .filter_by(driver1_id=driver1_id, driver2_id=driver2_id, season=season, mode=mode)
                .order_by(HeadToHeadCache.created_at.desc())
                .all()
            )
            if existing:
                # Keep the newest row and refresh it in place; drop any duplicates
                # an earlier insert-per-miss build left behind.
                keeper, duplicates = existing[0], existing[1:]
                keeper.response_json = serialized
                keeper.created_at = datetime.now(timezone.utc)
                for duplicate in duplicates:
                    db.delete(duplicate)
            else:
                db.add(HeadToHeadCache(
                    driver1_id=driver1_id,
                    driver2_id=driver2_id,
                    season=season,
                    mode=mode,
                    response_json=serialized,
                    created_at=datetime.now(timezone.utc),
                ))
            db.commit()
            metrics.record_cache_event('sql', 'write')
    except Exception as exc:
        metrics.record_cache_event('sql', 'error', exc)


@app.get('/api/head-to-head')
async def api_head_to_head(driver1_id: int, driver2_id: int, season: Optional[int] = None, mode: Optional[str] = 'season'):
    """Return season head-to-head statistics for two drivers."""
    # This endpoint takes raw query params rather than the HeadToHeadRequest
    # model, so its "can't compare a driver with themselves" validation
    # never ran -- enforce it explicitly here instead.
    if driver1_id == driver2_id:
        raise HTTPException(status_code=422, detail="Cannot compare a driver with themselves")
    try:
        # Season-only mode (career comparisons disabled).
        mode = 'season'
        _, races, drivers, seasons, _, _ = load_data()

        # Scored and joined once, on the default modern rules, filtered to the
        # requested season inside the builder rather than after a 26k-row join.
        df = build_enriched_results(season=int(season) if season is not None else None)

        # Try cache (Redis first, then SQL)
        cache_key = f"h2h:v2:{driver1_id}:{driver2_id}:{season}:{mode}"
        cached_payload = h2h_cache_get(cache_key, driver1_id, driver2_id, season, mode)
        if cached_payload is not None:
            return cached_payload

        # Helper to compute stats for a driver
        def compute_stats(driver_id):
            d = df[df['driverId'] == int(driver_id)].copy()
            name_row = drivers[drivers['driverId'] == int(driver_id)]
            driver_name = ''
            if not name_row.empty:
                driver_name = f"{name_row.iloc[0]['forename']} {name_row.iloc[0]['surname']}"

            wins = int(d[d.get('positionOrder', pd.Series()).fillna(9999) == 1].shape[0])
            podiums = int(d[d.get('positionOrder', pd.Series()).fillna(9999) <= 3].shape[0])
            poles = int(d[d.get('grid', pd.Series()) == 1].shape[0]) if 'grid' in d.columns else 0
            total_points = float(d['adjusted_points'].sum()) if 'adjusted_points' in d.columns else 0.0

            # DNFs: prefer positionText semantics (non-numeric means non-classified/retired).
            dnfs = 0
            if 'positionText' in d.columns:
                try:
                    pos_text = d['positionText'].astype(str).str.strip()
                    # Numeric positionText values indicate classified finishers.
                    dnf_mask = ~pos_text.str.match(r'^\d+$')
                    dnfs = int(dnf_mask.sum())
                except Exception:
                    dnfs = 0
            elif 'positionOrder' in d.columns:
                dnfs = int(d['positionOrder'].isna().sum())

            # Grid stats (rounded to nearest tenth)
            avg_grid = None
            avg_quali = None
            if 'grid' in d.columns and not d['grid'].dropna().empty:
                grids = d['grid'].replace(0, pd.NA).dropna().astype(float)
                if not grids.empty:
                    avg_grid = round(float(grids.mean()), 1)
                    avg_quali = round(float(grids.mean()), 1)

            # Average finish and consistency metrics
            avg_finish = None
            finish_variance = None
            if 'positionOrder' in d.columns and not d['positionOrder'].dropna().empty:
                finishes = d['positionOrder'].dropna().astype(float)
                if not finishes.empty:
                    avg_finish = round(float(finishes.mean()), 2)
                    finish_variance = round(float(finishes.var(ddof=0)), 3)

            # Net positions gained (grid - finish); positive is better
            net_positions_gained = None
            try:
                nets = []
                for _, rr in d.iterrows():
                    g = rr.get('grid')
                    p = rr.get('positionOrder')
                    if pd.notna(g) and pd.notna(p):
                        g = float(g)
                        p = float(p)
                        if g > 0:
                            nets.append(g - p)
                if nets:
                    net_positions_gained = round(float(pd.Series(nets).mean()), 3)
            except Exception:
                net_positions_gained = None

            # Teammate comparison metrics
            teammate_race_count = 0
            outqualified_by_5 = 0
            outraced_by_5 = 0
            teammate_points_driver = 0.0
            teammate_points_peer = 0.0
            if 'constructorId' in d.columns and 'raceId' in d.columns:
                for _, row in d.iterrows():
                    race_id = row.get('raceId')
                    constructor = row.get('constructorId')
                    if pd.isna(race_id) or pd.isna(constructor):
                        continue
                    peers = df[(df['raceId'] == race_id) & (df['constructorId'] == constructor) & (df['driverId'] != driver_id)]
                    if peers.empty:
                        continue
                    peer = peers.iloc[0]
                    teammate_race_count += 1

                    g_self = row.get('grid')
                    g_peer = peer.get('grid')
                    if pd.notna(g_self) and pd.notna(g_peer) and float(g_self) > 0 and float(g_peer) > 0:
                        if (float(g_self) - float(g_peer)) >= 5:
                            outqualified_by_5 += 1

                    p_self = row.get('positionOrder')
                    p_peer = peer.get('positionOrder')
                    if pd.notna(p_self) and pd.notna(p_peer):
                        if (float(p_self) - float(p_peer)) >= 5:
                            outraced_by_5 += 1

                    try:
                        teammate_points_driver += float(row.get('adjusted_points') or 0.0)
                    except Exception:
                        pass
                    try:
                        teammate_points_peer += float(peer.get('adjusted_points') or 0.0)
                    except Exception:
                        pass

            teammate_points_pct = None
            denom = teammate_points_driver + teammate_points_peer
            if denom > 0:
                teammate_points_pct = round((teammate_points_driver / denom) * 100.0, 2)

            # Clutchness score requested by user
            race_count = int(len(d)) if len(d) else 0
            dnf_rate = (dnfs / race_count * 100.0) if race_count > 0 else 0.0
            teammate_rate_base = teammate_race_count if teammate_race_count > 0 else 1
            outqualified_by_5_rate = (outqualified_by_5 / teammate_rate_base) * 100.0
            outraced_by_5_rate = (outraced_by_5 / teammate_rate_base) * 100.0
            clutchness = round(max(0.0, 100.0 - (0.50 * dnf_rate + 0.25 * outqualified_by_5_rate + 0.25 * outraced_by_5_rate)), 2)

            # Average qualifying gap to teammate: try fastf1 (qualifying lap-time gap) otherwise fallback to grid difference
            avg_grid_gap = None
            if 'constructorId' in d.columns:
                gaps = []
                session_cache = {}
                for _, row in d.iterrows():
                    race_id = row.get('raceId')
                    constructor = row.get('constructorId')
                    if pd.isna(race_id) or pd.isna(constructor):
                        continue
                    peers = df[(df['raceId'] == race_id) & (df['constructorId'] == constructor) & (df['driverId'] != driver_id)]
                    if peers.empty:
                        continue
                    peer = peers.iloc[0]

                    qual_gap_found = False
                    if FASTF1_AVAILABLE and ENABLE_FASTF1_QUALI_GAP:
                        try:
                            race_row = races[races['raceId'] == race_id]
                            if race_row.empty:
                                raise Exception('no race row')
                            year = int(race_row.iloc[0]['year'])
                            round_no = int(race_row.iloc[0]['round']) if 'round' in race_row.columns and not pd.isna(race_row.iloc[0]['round']) else None
                            if round_no is None:
                                raise Exception('no round')
                            sess_key = f"{year}-{round_no}"
                            if sess_key not in session_cache:
                                for sname in ['Q', 'SQ', 'Qualifying']:
                                    try:
                                        session = fastf1.get_session(year, round_no, sname)
                                        session.load(laps=True, telemetry=False)
                                        session_cache[sess_key] = session
                                        break
                                    except Exception:
                                        continue
                            session = session_cache.get(sess_key)
                            if session is not None:
                                code1 = None
                                code2 = None
                                try:
                                    code1 = drivers.loc[drivers['driverId'] == int(driver_id), 'code'].values[0]
                                except Exception:
                                    code1 = None
                                try:
                                    code2 = drivers.loc[drivers['driverId'] == int(peer['driverId']), 'code'].values[0]
                                except Exception:
                                    code2 = None
                                if code1 and code2:
                                    laps1 = session.laps.pick_driver(code1)
                                    laps2 = session.laps.pick_driver(code2)
                                    if not laps1.empty and not laps2.empty:
                                        t1 = laps1['LapTime'].min()
                                        t2 = laps2['LapTime'].min()
                                        if pd.notna(t1) and pd.notna(t2):
                                            s1 = t1.total_seconds()
                                            s2 = t2.total_seconds()
                                            gaps.append(abs(s1 - s2))
                                            qual_gap_found = True
                        except Exception as exc:
                            # Falls through to the grid-position gap below, which
                            # is the intended degradation -- but a fastf1 cache
                            # miss, a network failure and a season with no
                            # telemetry all looked identical from outside.
                            logger.debug(
                                "fastf1 qualifying gap unavailable for race %s: %s: %s",
                                race_id, type(exc).__name__, exc,
                            )
                    if not qual_gap_found:
                        if pd.notna(row.get('grid')) and pd.notna(peer.get('grid')):
                            try:
                                gaps.append(abs(float(row.get('grid')) - float(peer.get('grid'))))
                            except Exception:
                                pass
                if gaps:
                    avg_grid_gap = float(pd.Series(gaps).mean())

            return {
                'driver_id': int(driver_id),
                'driver_name': driver_name,
                'wins': wins,
                'podiums': podiums,
                'poles': poles,
                'total_points': total_points,
                'dnfs': dnfs,
                'avg_grid': avg_grid,
                'avg_quali': avg_quali,
                'avg_finish': avg_finish,
                'net_positions_gained': net_positions_gained,
                'finish_variance': finish_variance,
                'teammate_points_pct': teammate_points_pct,
                'outqualified_by_5': outqualified_by_5,
                'outraced_by_5': outraced_by_5,
                'clutchness': clutchness,
                'avg_grid_gap_to_teammate': avg_grid_gap
            }

        driver1_stats = compute_stats(driver1_id)
        driver2_stats = compute_stats(driver2_id)

        def clamp01(v):
            return max(0.0, min(100.0, float(v)))

        def normalize_to_100(value, min_val, max_val, invert=False):
            if value is None:
                return 50.0
            val = float(value)
            if max_val <= min_val:
                return 50.0
            ratio = (val - float(min_val)) / (float(max_val) - float(min_val))
            ratio = max(0.0, min(1.0, ratio))
            score = (1.0 - ratio) * 100.0 if invert else ratio * 100.0
            return clamp01(score)

        def score_avg_position(v):
            # Lower average position is better (1 is best, 21 is worst/DNF bucket).
            return normalize_to_100(v, 1.0, 21.0, invert=True)

        def score_net_positions(v):
            # Typical range from -10 (bad) to +10 (great).
            return normalize_to_100(v, -10.0, 10.0, invert=False)

        def score_consistency(variance):
            # Same formula-based scaling as other metrics: min-max normalization to 0..100.
            # Lower variance is better; near-zero variance should map close to 100.
            return normalize_to_100(variance, 0.0, 25.0, invert=True)

        def build_radar_scores(stats):
            return {
                'Avg Finish (Race Pace)': round(score_avg_position(stats.get('avg_finish')), 2),
                'Avg Quali (Raw Pace)': round(score_avg_position(stats.get('avg_quali')), 2),
                'Net Positions Gained (Race Craft)': round(score_net_positions(stats.get('net_positions_gained')), 2),
                'Consistency (Low Variance)': round(score_consistency(stats.get('finish_variance')), 2),
                'Teammate Dominance (%)': round(clamp01(stats.get('teammate_points_pct') if stats.get('teammate_points_pct') is not None else 50.0), 2),
                'Clutchness': round(clamp01(stats.get('clutchness') if stats.get('clutchness') is not None else 50.0), 2)
            }

        driver1_stats['radar_scores'] = build_radar_scores(driver1_stats)
        driver2_stats['radar_scores'] = build_radar_scores(driver2_stats)

        # Race-by-race comparison
        race_list = []
        # consider races in the filtered df where either driver participated
        races_of_interest = df[df['driverId'].isin([int(driver1_id), int(driver2_id)])]['raceId'].unique().tolist()
        for rid in races_of_interest:
            race_row = races[races['raceId'] == rid]
            if race_row.empty:
                continue
            race_info = race_row.iloc[0]
            round_val = int(race_info['round']) if 'round' in race_info and not pd.isna(race_info['round']) else None
            race_name = race_info.get('name', '')
            row1 = df[(df['raceId'] == rid) & (df['driverId'] == int(driver1_id))]
            row2 = df[(df['raceId'] == rid) & (df['driverId'] == int(driver2_id))]
            pos1 = int(row1.iloc[0]['positionOrder']) if not row1.empty and not pd.isna(row1.iloc[0].get('positionOrder')) else None
            pos2 = int(row2.iloc[0]['positionOrder']) if not row2.empty and not pd.isna(row2.iloc[0].get('positionOrder')) else None
            constructor1 = row1.iloc[0].get('constructor_name') if not row1.empty else None
            constructor2 = row2.iloc[0].get('constructor_name') if not row2.empty else None
            grid1 = int(row1.iloc[0]['grid']) if not row1.empty and not pd.isna(row1.iloc[0].get('grid')) and float(row1.iloc[0].get('grid')) > 0 else None
            grid2 = int(row2.iloc[0]['grid']) if not row2.empty and not pd.isna(row2.iloc[0].get('grid')) and float(row2.iloc[0].get('grid')) > 0 else None

            # Determine race winner if both finished
            winner = None
            winner_driver = None
            if pos1 and pos2:
                if pos1 < pos2:
                    winner = driver1_stats['driver_name']
                    winner_driver = 'driver1'
                elif pos2 < pos1:
                    winner = driver2_stats['driver_name']
                    winner_driver = 'driver2'
                else:
                    winner = 'Tie'
                    winner_driver = 'tie'
            elif pos1 and not pos2:
                winner = driver1_stats['driver_name']
                winner_driver = 'driver1'
            elif pos2 and not pos1:
                winner = driver2_stats['driver_name']
                winner_driver = 'driver2'
            # Compute margin in seconds if finish time milliseconds available for both
            margin = None
            try:
                if not row1.empty and not row2.empty and 'milliseconds' in row1.columns and 'milliseconds' in row2.columns:
                    ms1 = row1.iloc[0].get('milliseconds')
                    ms2 = row2.iloc[0].get('milliseconds')
                    if pd.notna(ms1) and pd.notna(ms2):
                        margin = round(abs(float(ms1) - float(ms2)) / 1000.0, 3)
            except Exception:
                margin = None

            # Determine quali winner and margin in grid places
            quali_winner = None
            quali_winner_driver = None
            quali_margin = None
            if grid1 and grid2:
                if grid1 < grid2:
                    quali_winner = driver1_stats['driver_name']
                    quali_winner_driver = 'driver1'
                elif grid2 < grid1:
                    quali_winner = driver2_stats['driver_name']
                    quali_winner_driver = 'driver2'
                else:
                    quali_winner = 'Tie'
                    quali_winner_driver = 'tie'
                quali_margin = abs(grid1 - grid2)
            elif grid1 and not grid2:
                quali_winner = driver1_stats['driver_name']
                quali_winner_driver = 'driver1'
            elif grid2 and not grid1:
                quali_winner = driver2_stats['driver_name']
                quali_winner_driver = 'driver2'

            race_list.append({
                'round': round_val,
                'race_name': race_name,
                'driver1_race_position': pos1,
                'driver2_race_position': pos2,
                'driver1_quali_position': grid1,
                'driver2_quali_position': grid2,
                'driver1_position': pos1,
                'driver2_position': pos2,
                'driver1_constructor': constructor1,
                'driver2_constructor': constructor2,
                'winner': winner,
                'winner_driver': winner_driver,
                'winner_race': winner,
                'winner_driver_race': winner_driver,
                'margin': margin,
                'race_margin_seconds': margin,
                'winner_quali': quali_winner,
                'winner_driver_quali': quali_winner_driver,
                'quali_margin_positions': quali_margin
            })

        # Build cumulative chart JSON for the two drivers using existing function
        try:
            # `df` is already the scored, joined frame for this season.
            chart_year = int(season) if season else int(df['year'].min())
            cumulative_chart = create_cumulative_points_chart(
                df, chart_year, scoring.points_system_label(None), [int(driver1_id), int(driver2_id)]
            )
        except Exception:
            cumulative_chart = None

        response = {
            'driver1_stats': driver1_stats,
            'driver2_stats': driver2_stats,
            'race_by_race': race_list,
            'cumulative_chart': cumulative_chart
        }

        h2h_cache_put(cache_key, driver1_id, driver2_id, season, mode, response)
        return response
    except Exception as e:
        logger.exception(f"Error computing head-to-head stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get('/api/h2h-wikipedia')
async def api_h2h_wikipedia(driver1: int, driver2: int, season: Optional[int] = None):
    """Return a simple assembled summary for two drivers (offline fallback)."""
    try:
        _, _, drivers, seasons, _, _ = load_data()
        def brief(driver_id):
            row = drivers[drivers['driverId'] == int(driver_id)]
            if row.empty:
                return ''
            r = row.iloc[0]
            parts = [f"{r.get('forename','')} {r.get('surname','')}."]
            if 'dob' in r.index and not pd.isna(r['dob']):
                parts.append(f"Born {r['dob']}")
            if 'nationality' in r.index and not pd.isna(r['nationality']):
                parts.append(f"Nationality: {r['nationality']}")
            return ' '.join(parts)

        summary = f"{brief(driver1)}\n\n{brief(driver2)}\n\nNote: This is an offline summary. For richer summaries, enable external Wikipedia fetch." 
        return {'summary': summary}
    except Exception as e:
        logger.exception(f"Error building h2h summary: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get('/api/race/{race_id}')
async def api_race_detail(race_id: int):
    try:
        results, races, drivers, _, constructors, _ = load_data()
        race_row = races[races['raceId'] == race_id]
        if race_row.empty:
            raise HTTPException(status_code=404, detail='Race not found')
        race = race_row.iloc[0].to_dict()
        race_results = results[results['raceId'] == race_id].copy()
        # merge driver and constructor info
        race_results = pd.merge(race_results, drivers[['driverId','forename','surname']], on='driverId', how='left')
        race_results = pd.merge(race_results, constructors[['constructorId','name']].rename(columns={'name':'constructor_name'}), on='constructorId', how='left')
        # compute time in seconds if milliseconds present
        def time_seconds(row):
            try:
                ms = row.get('milliseconds')
                if pd.notna(ms):
                    return round(float(ms)/1000.0,3)
            except Exception:
                pass
            return None

        rows = []
        for _, r in race_results.sort_values('positionOrder').iterrows():
            rows.append({
                'driverId': int(r['driverId']),
                'forename': r.get('forename'),
                'surname': r.get('surname'),
                'constructor_name': r.get('constructor_name'),
                'positionOrder': int(r['positionOrder']) if pd.notna(r.get('positionOrder')) else None,
                'grid': int(r['grid']) if pd.notna(r.get('grid')) else None,
                'laps': int(r['laps']) if pd.notna(r.get('laps')) else None,
                'time_seconds': time_seconds(r)
            })

        return {'raceId': int(race_id), 'name': race.get('name'), 'round': race.get('round'), 'date': race.get('date'), 'results': rows}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error loading race detail: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/simulate-season")
async def simulate_season_endpoint(request: SimulateSeasonRequest):
    """
    Generate AI-powered season summary with RAG, web scraping, and PDF export
    """
    try:
        # Ollama settings (local model server)
        ollama_base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
        ollama_model = FIXED_OLLAMA_MODEL
        
        # Get points system
        points_system_name = scoring.points_system_label(request.points_system)

        with metrics.observe_points_calculation(request.points_system):
            adjusted_results_with_races = build_enriched_results(
                request.points_system, season=request.season_year
            )
            standings = calculate_standings(adjusted_results_with_races, request.season_year)
        
        # Determine primary constructor per driver
        season_rows = adjusted_results_with_races[adjusted_results_with_races['year'] == request.season_year]
        if not season_rows.empty:
            constructor_mode = (
                season_rows
                .groupby(['surname', 'forename', 'constructor_name'], as_index=False)['raceId']
                .count()
                .sort_values(['surname', 'forename', 'raceId'], ascending=[True, True, False])
            )
            constructor_mode = constructor_mode.drop_duplicates(subset=['surname', 'forename'], keep='first')
            standings = pd.merge(
                standings,
                constructor_mode[['surname', 'forename', 'constructor_name']],
                on=['surname', 'forename'],
                how='left'
            )
        
        if standings.empty:
            raise HTTPException(status_code=404, detail=f"No data found for season {request.season_year}")
        
        # Create charts
        cumulative_chart = create_cumulative_points_chart(
            adjusted_results_with_races, request.season_year, points_system_name
        )
        distribution_chart = create_points_distribution_chart(standings, request.season_year, points_system_name)
        constructors_chart = create_constructors_cumulative_chart(
            adjusted_results_with_races, request.season_year, points_system_name
        )
        
        # Prepare data for simulator
        standings_data = {
            'standings': standings.to_dict('records'),
            'season_year': request.season_year
        }
        
        chart_json_strings = {
            'cumulative_chart': cumulative_chart,
            'distribution_chart': distribution_chart,
            'constructors_chart': constructors_chart
        }
        
        # Generate PDF using the simulator
        pdf_path = simulate_season(
            season_year=request.season_year,
            standings_data=standings_data,
            points_system_name=points_system_name,
            chart_json_strings=chart_json_strings,
            ollama_base_url=ollama_base_url,
            ollama_model=ollama_model,
            output_dir="exports"
        )
        
        if pdf_path and os.path.exists(pdf_path):
            # Return the PDF file
            return FileResponse(
                pdf_path, 
                media_type='application/pdf',
                filename=os.path.basename(pdf_path)
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to generate PDF report")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error simulating season: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
