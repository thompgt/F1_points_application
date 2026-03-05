from fastapi import FastAPI, HTTPException, Query
from fastapi import Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.utils import PlotlyJSONEncoder
# Optional fastf1 support for qualifying lap time gaps
try:
    import fastf1
    FASTF1_AVAILABLE = True
except Exception:
    FASTF1_AVAILABLE = False
from plotly.subplots import make_subplots
import json
import plotly.io as pio
from typing import List, Optional
import warnings
from functools import lru_cache
import os
from dotenv import load_dotenv
from season_simulator import simulate_season
from db import init_db, HeadToHeadCache, SessionLocal
import sqlalchemy

# Import new modules for validation, middleware, and health checks
from validators import (
    StandingsRequest,
    SimulateSeasonRequest,
    RaceResultsRequest,
    HeadToHeadRequest,
    sanitize_string,
    InputValidator
)
from middleware import add_middleware_stack, get_logger
from health import router as health_router

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

# Load environment variables
load_dotenv()

warnings.filterwarnings("ignore")

# Get logger instance
logger = get_logger()

# App configuration
API_VERSION = os.getenv("API_VERSION", "1.0.0")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
ENABLE_RATE_LIMITING = os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

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
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Add custom middleware stack (error handling, logging, rate limiting, security)
add_middleware_stack(app, {
    'enable_rate_limiting': ENABLE_RATE_LIMITING,
    'requests_per_minute': int(os.getenv('RATE_LIMIT_PER_MINUTE', '60')),
    'requests_per_hour': int(os.getenv('RATE_LIMIT_PER_HOUR', '1000')),
    'burst_limit': int(os.getenv('RATE_LIMIT_BURST', '10'))
})

# Include health check routes
app.include_router(health_router)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
DEFAULT_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

# Note: Request models (StandingsRequest, SimulateSeasonRequest, etc.) are now in validators.py

@lru_cache(maxsize=1)
def load_data():
    """Load all necessary CSV files"""
    results = pd.read_csv('results.csv')
    races = pd.read_csv('races.csv')
    drivers = pd.read_csv('drivers.csv')
    seasons = pd.read_csv('seasons.csv')
    constructors = pd.read_csv('constructors.csv')
    driver_standings = pd.read_csv('driver_standings.csv')
    return results, races, drivers, seasons, constructors, driver_standings

def adjust_points(results_df, points_system):
    """Adjust the points in the results DataFrame to the specified points system"""
    adjusted_results = results_df.copy()
    adjusted_results['adjusted_points'] = 0

    for i, points in enumerate(points_system, start=1):
        adjusted_results.loc[adjusted_results['positionOrder'] == i, 'adjusted_points'] = points

    return adjusted_results

def calculate_standings(adjusted_results_with_races, season_year):
    """Calculate the standings for a given season"""
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year]
    
    if season_results.empty:
        return pd.DataFrame()
    
    standings = season_results.groupby(['surname', 'forename'], as_index=False)['adjusted_points'].sum()
    standings['driver_label'] = standings.apply(lambda row: f"{row['forename'][0]}. {row['surname']}", axis=1)
    standings = standings.sort_values(by='adjusted_points', ascending=False).reset_index(drop=True)
    standings.index += 1
    standings.reset_index(inplace=True)
    standings.rename(columns={'index': 'Position'}, inplace=True)
    return standings


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
    title_suffix = 'Selected Drivers' if selected_driver_ids else 'Top 10 Drivers'
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
        y='positionOrder',
        color='driver_label',
        title=f'Race Results Timeline - {season_year} Season',
        labels={'race_number': 'Race Number', 'positionOrder': 'Finishing Position', 'driver_label': 'Driver'},
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
        y = grp['positionOrder'].fillna(20).astype(int).tolist()  # Use 20 for DNF
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
    return templates.TemplateResponse("index.html", {"request": request})


@app.get('/head-to-head', response_class=HTMLResponse)
async def head_to_head(request: Request):
    """Serve the head-to-head comparison page"""
    return templates.TemplateResponse('head_to_head.html', {"request": request})


@app.get('/race-detail', response_class=HTMLResponse)
async def race_detail(request: Request):
    return templates.TemplateResponse('race_detail.html', {"request": request})

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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/calculate-standings")
async def calculate_standings_api(request: StandingsRequest):
    """Calculate standings for a given season with optional custom points system"""
    try:
        if request.points_system is None:
            points_system = DEFAULT_POINTS
        else:
            points_system = request.points_system
        
        results, races, drivers, _, constructors, _ = load_data()
        
        # Adjust points
        adjusted_results = adjust_points(results, points_system)
        
        # Merge with driver and race and constructor information
        adjusted_results_with_drivers = pd.merge(
            adjusted_results,
            drivers[['driverId', 'surname', 'forename']],
            on='driverId'
        )

        adjusted_results_with_constructors = pd.merge(
            adjusted_results_with_drivers,
            constructors[['constructorId', 'name']].rename(columns={'name': 'constructor_name'}),
            on='constructorId'
        )
        
        race_cols = ['raceId', 'year', 'name']
        if 'round' in races.columns:
            race_cols.append('round')

        adjusted_results_with_races = pd.merge(
            adjusted_results_with_constructors,
            races[race_cols],
            on='raceId'
        )
        
        # Calculate standings
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
        
        # Create visualizations
        points_system_name = "Custom" if points_system != DEFAULT_POINTS else "Modern"
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
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/points-systems")
async def get_points_systems():
    """Get predefined points systems"""
    return {
        "points_systems": {
            "modern": {"name": "Modern (2010-2024)", "points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]},
            "classic": {"name": "Classic (1991-2002)", "points": [10, 6, 4, 3, 2, 1]},
            "pre_1991": {"name": "Pre-1991", "points": [9, 6, 4, 3, 2, 1]},
            "custom": {"name": "Custom", "points": []}
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
        # Try DB first
        db = SessionLocal()
        db_races = db.query(Race).filter_by(round=season).all()
        db.close()
        if db_races:
            race_list = [
                {
                    "raceId": r.raceId,
                    "name": r.name,
                    "round": r.round,
                    "date": r.date,
                    "circuitId": r.circuitId
                } for r in db_races
            ]
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
                "round": int(race['round']) if 'round' in race and pd.notna(race['round']) else None,
                "date": str(race.get('date', '')) if pd.notna(race.get('date')) else None,
                "circuitId": int(race['circuitId']) if 'circuitId' in race and pd.notna(race['circuitId']) else None
            })
        # Store in DB for future
        store_races(race_list)
        return {"races": race_list}
    except Exception as e:
        logger.error(f"Error loading races: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        logger.error(f"Error loading race results: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/api/head-to-head')
async def api_head_to_head(driver1_id: int, driver2_id: int, season: Optional[int] = None, mode: Optional[str] = 'season'):
    """Return head-to-head statistics for two drivers. Mode: 'season' or 'career'"""
    try:
        results, races, drivers, seasons, constructors, _ = load_data()

        # Merge driver and race info
        df = results.copy()
        df = pd.merge(df, drivers[['driverId', 'forename', 'surname']], on='driverId', how='left')
        race_cols = ['raceId', 'year', 'name']
        if 'round' in races.columns:
            race_cols.append('round')
        df = pd.merge(df, races[race_cols], on='raceId', how='left')
        df = pd.merge(df, constructors[['constructorId', 'name']].rename(columns={'name': 'constructor_name'}), on='constructorId', how='left')

        # Adjust points (use default modern system)
        df = adjust_points(df, DEFAULT_POINTS)

        # Filter by mode
        if mode == 'season' and season is not None:
            df = df[df['year'] == int(season)].copy()

        # Try cache (Redis first, then SQLite)
        cache_key = f"h2h:{driver1_id}:{driver2_id}:{season}:{mode}"
        if REDIS_CLIENT:
            cached = REDIS_CLIENT.get(cache_key)
            if cached:
                try:
                    return json.loads(cached)
                except Exception:
                    pass
        # Try sqlite cache
        try:
            db = SessionLocal()
            q = db.query(HeadToHeadCache).filter_by(driver1_id=driver1_id, driver2_id=driver2_id, season=season, mode=mode).order_by(HeadToHeadCache.created_at.desc()).first()
            if q and q.response_json:
                return json.loads(q.response_json)
        except Exception:
            pass

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

            # DNFs: count if completed laps < 90% of race laps, fallback to positionOrder missing
            dnfs = 0
            if 'laps' in d.columns and 'raceId' in d.columns:
                for _, rr in d.iterrows():
                    try:
                        race_id = rr['raceId']
                        race_row = races[races['raceId'] == race_id]
                        if not race_row.empty and 'laps' in race_row.columns and not pd.isna(race_row.iloc[0]['laps']):
                            total_laps = float(race_row.iloc[0]['laps'])
                            driver_laps = float(rr.get('laps', 0) or 0)
                            if total_laps > 0 and driver_laps < 0.9 * total_laps:
                                dnfs += 1
                                continue
                    except Exception:
                        pass
                if dnfs == 0 and 'positionOrder' in d.columns:
                    dnfs = int(d['positionOrder'].isna().sum())
            elif 'positionOrder' in d.columns:
                dnfs = int(d['positionOrder'].isna().sum())
            elif 'positionText' in d.columns:
                dnfs = int(d['positionText'].isin(['R', 'D', 'DNF']).sum())

            # Grid stats (rounded to nearest tenth)
            avg_grid = None
            median_grid = None
            if 'grid' in d.columns and not d['grid'].dropna().empty:
                grids = d['grid'].replace(0, pd.NA).dropna().astype(float)
                if not grids.empty:
                    avg_grid = round(float(grids.mean()), 1)
                    median_grid = round(float(grids.median()), 1)

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
                    if FASTF1_AVAILABLE:
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
                        except Exception:
                            pass
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
                'median_grid': median_grid,
                'avg_grid_gap_to_teammate': avg_grid_gap
            }

        driver1_stats = compute_stats(driver1_id)
        driver2_stats = compute_stats(driver2_id)

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
            # Determine winner if both finished
            winner = None
            if pos1 and pos2:
                if pos1 < pos2:
                    winner = driver1_stats['driver_name']
                elif pos2 < pos1:
                    winner = driver2_stats['driver_name']
                else:
                    winner = 'Tie'
            elif pos1 and not pos2:
                winner = driver1_stats['driver_name']
            elif pos2 and not pos1:
                winner = driver2_stats['driver_name']
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

            race_list.append({
                'round': round_val,
                'race_name': race_name,
                'driver1_position': pos1,
                'driver2_position': pos2,
                'winner': winner,
                'margin': margin
            })

        # Build cumulative chart JSON for the two drivers using existing function
        try:
            # Need adjusted_results_with_races similar to calculate_standings_api
            adjusted_results = adjust_points(results, DEFAULT_POINTS)
            adjusted_results_with_drivers = pd.merge(
                adjusted_results,
                drivers[['driverId', 'surname', 'forename']],
                on='driverId'
            )
            adjusted_results_with_constructors = pd.merge(
                adjusted_results_with_drivers,
                constructors[['constructorId', 'name']].rename(columns={'name': 'constructor_name'}),
                on='constructorId'
            )
            race_cols = ['raceId', 'year', 'name']
            if 'round' in races.columns:
                race_cols.append('round')
            adjusted_results_with_races = pd.merge(
                adjusted_results_with_constructors,
                races[race_cols],
                on='raceId'
            )
            cumulative_chart = create_cumulative_points_chart(adjusted_results_with_races, int(season) if season else adjusted_results_with_races['year'].min(), 'Modern', [int(driver1_id), int(driver2_id)])
        except Exception:
            cumulative_chart = None

        response = {
            'driver1_stats': driver1_stats,
            'driver2_stats': driver2_stats,
            'race_by_race': race_list,
            'cumulative_chart': cumulative_chart
        }

        # Save to caches
        try:
            serialized = json.dumps(response)
            if REDIS_CLIENT:
                try:
                    REDIS_CLIENT.set(cache_key, serialized, ex=60*60)
                except Exception:
                    pass
            try:
                db = SessionLocal()
                entry = HeadToHeadCache(driver1_id=driver1_id, driver2_id=driver2_id, season=season, mode=mode, response_json=serialized)
                db.add(entry)
                db.commit()
                db.close()
            except Exception:
                pass
        except Exception:
            pass

        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/simulate-season")
async def simulate_season_endpoint(request: SimulateSeasonRequest):
    """
    Generate AI-powered season summary with RAG, web scraping, and PDF export
    """
    try:
        # Get Gemini API key from request or environment
        gemini_api_key = request.gemini_api_key or os.getenv('GEMINI_API_KEY')
        
        if not gemini_api_key:
            raise HTTPException(
                status_code=400, 
                detail="Gemini API key required. Set GEMINI_API_KEY environment variable or provide in request."
            )
        
        # Get points system
        if request.points_system is None:
            points_system = DEFAULT_POINTS
            points_system_name = "Modern"
        else:
            points_system = request.points_system
            points_system_name = "Custom"
        
        # Load data and calculate standings
        results, races, drivers, _, constructors, _ = load_data()
        adjusted_results = adjust_points(results, points_system)
        
        # Merge with driver and race and constructor information
        adjusted_results_with_drivers = pd.merge(
            adjusted_results,
            drivers[['driverId', 'surname', 'forename']],
            on='driverId'
        )
        
        adjusted_results_with_constructors = pd.merge(
            adjusted_results_with_drivers,
            constructors[['constructorId', 'name']].rename(columns={'name': 'constructor_name'}),
            on='constructorId'
        )
        
        race_cols = ['raceId', 'year', 'name']
        if 'round' in races.columns:
            race_cols.append('round')
        
        adjusted_results_with_races = pd.merge(
            adjusted_results_with_constructors,
            races[race_cols],
            on='raceId'
        )
        
        # Calculate standings
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
            gemini_api_key=gemini_api_key,
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
        raise HTTPException(status_code=500, detail=f"Error simulating season: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
