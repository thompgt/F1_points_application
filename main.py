from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.requests import Request
from pydantic import BaseModel
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import json
from typing import List, Optional
import warnings
from functools import lru_cache
from functools import lru_cache

warnings.filterwarnings("ignore")

app = FastAPI(title="F1 Points Calculator", version="1.0.0")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Default modern points system
DEFAULT_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

class StandingsRequest(BaseModel):
    season_year: int
    points_system: Optional[List[int]] = None
    selected_driver_ids: Optional[List[int]] = None
    selected_driver_ids: Optional[List[int]] = None

@lru_cache(maxsize=1)
@lru_cache(maxsize=1)
def load_data():
    """Load all necessary CSV files"""
    results = pd.read_csv('results.csv')
    races = pd.read_csv('races.csv')
    drivers = pd.read_csv('drivers.csv')
    seasons = pd.read_csv('seasons.csv')
    constructors = pd.read_csv('constructors.csv')
    return results, races, drivers, seasons, constructors

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
    standings = standings.sort_values(by='adjusted_points', ascending=False).reset_index(drop=True)
    standings.index += 1
    standings.reset_index(inplace=True)
    standings.rename(columns={'index': 'Position'}, inplace=True)
    return standings

def create_cumulative_points_chart(adjusted_results_with_races, season_year, points_system_name, selected_driver_ids: Optional[List[int]] = None):
    """Create a cumulative points chart using Plotly"""
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year]
    
    if season_results.empty:
        return None
    
    # Sort by race order and build race_number
    race_number_col = 'round' if 'round' in season_results.columns else None
    if race_number_col is not None:
        season_results['race_number'] = season_results[race_number_col]
        season_results = season_results.sort_values(by=['race_number', 'positionOrder'])
    else:
        season_results = season_results.sort_values(by=['year', 'raceId', 'positionOrder'])
        season_results['race_number'] = season_results.groupby('year')['raceId'].rank(method='dense').astype(int)
    
    # Calculate cumulative points
    season_results['cumulative_points'] = season_results.groupby(['surname', 'forename'])['adjusted_points'].cumsum()
    
    # Determine which drivers to include
    if selected_driver_ids:
        season_results_filtered = season_results[season_results['driverId'].isin(selected_driver_ids)]
    else:
        top_10_drivers = (
            season_results.groupby(['driverId', 'surname', 'forename'], as_index=False)['adjusted_points']
            .sum()
            .sort_values(by='adjusted_points', ascending=False)
            .head(10)
        )
        season_results_filtered = season_results[
            season_results['driverId'].isin(top_10_drivers['driverId'])
        ]
    
    # Create the plot
    title_suffix = 'Selected Drivers' if selected_driver_ids else 'Top Drivers'
    fig = px.line(
        season_results_filtered,
        x='race_number',
        y='cumulative_points',
        color='surname',
        title=f'Cumulative Points for {title_suffix} in {season_year} Season ({points_system_name})',
        labels={'race_number': 'Race Number', 'cumulative_points': 'Cumulative Points', 'surname': 'Driver'},
        markers=True
    )
    
    fig.update_layout(
        height=600,
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02)
    )
    
    return fig.to_json()

def create_points_distribution_chart(standings, season_year, points_system_name):
    """Create a points distribution chart"""
    if standings.empty:
        return None
    
    fig = px.bar(
        standings.head(15),
        x='surname',
        y='adjusted_points',
        title=f'Points Distribution for {season_year} Season ({points_system_name})',
        labels={'surname': 'Driver', 'adjusted_points': 'Total Points'},
        color='adjusted_points',
        color_continuous_scale='viridis'
    )
    
    fig.update_layout(
        height=500,
        xaxis_tickangle=-45
    )
    
    return fig.to_json()

def create_constructors_cumulative_chart(adjusted_results_with_races, season_year, points_system_name):
    """Create constructors cumulative points chart over the season."""
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year].copy()
    if season_results.empty or 'constructor_name' not in season_results.columns:
        return None

    # Race number for x-axis
    race_number_col = 'round' if 'round' in season_results.columns else None
    if race_number_col is not None:
        season_results['race_number'] = season_results[race_number_col]
    else:
        season_results['race_number'] = season_results.groupby('year')['raceId'].rank(method='dense').astype(int)

    # Sum adjusted points per constructor per race
    per_race = season_results.groupby(['race_number', 'constructor_name'], as_index=False)['adjusted_points'].sum()
    per_race = per_race.sort_values(by=['constructor_name', 'race_number'])
    per_race['cumulative_points'] = per_race.groupby('constructor_name')['adjusted_points'].cumsum()

    fig = px.line(
        per_race,
        x='race_number',
        y='cumulative_points',
        color='constructor_name',
        title=f'Constructors Cumulative Points in {season_year} Season ({points_system_name})',
        labels={'race_number': 'Race Number', 'cumulative_points': 'Cumulative Points', 'constructor_name': 'Constructor'},
        markers=True
    )
    fig.update_layout(height=600)
    return fig.to_json()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/seasons")
async def get_seasons():
    """Get all available seasons"""
    try:
        _, _, _, seasons, _ = load_data()
        return {"seasons": seasons['year'].tolist()}
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
        
        results, races, drivers, _, constructors = load_data()
        
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
        cumulative_chart = create_cumulative_points_chart(
            adjusted_results_with_races, request.season_year, points_system_name, request.selected_driver_ids
        )
        distribution_chart = create_points_distribution_chart(standings, request.season_year, points_system_name)

        # Constructors cumulative chart
        constructors_cumulative_chart = create_constructors_cumulative_chart(adjusted_results_with_races, request.season_year, points_system_name)
        
        return {
            "standings": standings.to_dict('records'),
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

@app.get("/api/drivers")
async def get_drivers(season: Optional[int] = None):
    """Get drivers list, optionally for a specific season."""
    results, races, drivers, _ = load_data()
    if season is not None:
        race_ids = set(races.loc[races['year'] == season, 'raceId'].tolist())
        driver_ids = results.loc[results['raceId'].isin(race_ids), 'driverId'].unique().tolist()
        df = drivers[drivers['driverId'].isin(driver_ids)].copy()
    else:
        df = drivers.copy()
    df = df.sort_values(by=['surname', 'forename'])
    return {"drivers": df[['driverId', 'forename', 'surname']].to_dict('records')}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
