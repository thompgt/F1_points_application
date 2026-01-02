from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
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
import os
from dotenv import load_dotenv
from season_simulator import simulate_season

# Load environment variables
load_dotenv()

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

class SimulateSeasonRequest(BaseModel):
    season_year: int
    points_system: Optional[List[int]] = None
    gemini_api_key: Optional[str] = None

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
    standings = standings.sort_values(by='adjusted_points', ascending=False).reset_index(drop=True)
    standings.index += 1
    standings.reset_index(inplace=True)
    standings.rename(columns={'index': 'Position'}, inplace=True)
    return standings

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
    
    return fig.to_json()

def create_points_distribution_chart(standings, season_year, points_system_name):
    """Create a points distribution chart showing top drivers' total points"""
    if standings.empty:
        return None
    
    # Take top 15 drivers and create full name label
    top_standings = standings.head(15).copy()
    top_standings['driver_name'] = top_standings['forename'] + ' ' + top_standings['surname']
    
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
    
    return fig.to_json()

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
    return fig.to_json()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/seasons")
async def get_seasons():
    """Get all available seasons"""
    try:
        _, _, _, seasons, _, _ = load_data()
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

@app.get("/api/head-to-head")
async def get_head_to_head_stats(driver1_id: int, driver2_id: int, season: int):
    """Get detailed head-to-head statistics for two drivers in a specific season."""
    try:
        results, races, drivers, _, constructors, driver_standings = load_data()
        
        # Get season races
        season_races = races[races['year'] == season]
        if season_races.empty:
            raise HTTPException(status_code=404, detail=f"No races found for season {season}")
        
        race_ids = season_races['raceId'].tolist()
        
        # Get driver info
        driver1_info = drivers[drivers['driverId'] == driver1_id].iloc[0] if not drivers[drivers['driverId'] == driver1_id].empty else None
        driver2_info = drivers[drivers['driverId'] == driver2_id].iloc[0] if not drivers[drivers['driverId'] == driver2_id].empty else None
        
        if not driver1_info or not driver2_info:
            raise HTTPException(status_code=404, detail="One or both drivers not found")
        
        # Get results for both drivers in the season
        season_results = results[results['raceId'].isin(race_ids)]
        driver1_results = season_results[season_results['driverId'] == driver1_id].copy()
        driver2_results = season_results[season_results['driverId'] == driver2_id].copy()
        
        # Merge with constructor info
        driver1_results = pd.merge(driver1_results, constructors[['constructorId', 'name']], on='constructorId', how='left')
        driver2_results = pd.merge(driver2_results, constructors[['constructorId', 'name']], on='constructorId', how='left')
        
        # Merge with race info
        driver1_results = pd.merge(driver1_results, races[['raceId', 'name', 'round']], on='raceId', how='left')
        driver2_results = pd.merge(driver2_results, races[['raceId', 'name', 'round']], on='raceId', how='left')
        
        # Calculate statistics
        def calculate_driver_stats(driver_results, driver_info):
            if driver_results.empty:
                return {
                    'driver_name': f"{driver_info['forename']} {driver_info['surname']}",
                    'wins': 0,
                    'poles': 0,
                    'podiums': 0,
                    'points_finishes': 0,
                    'dnfs': 0,
                    'total_points': 0,
                    'best_finish': None,
                    'avg_finish': None,
                    'constructor': None,
                    'races_entered': 0
                }
            
            # Basic stats
            wins = len(driver_results[driver_results['positionOrder'] == 1])
            poles = len(driver_results[driver_results['grid'] == 1])
            podiums = len(driver_results[driver_results['positionOrder'].isin([1, 2, 3])])
            points_finishes = len(driver_results[driver_results['points'] > 0])
            dnfs = len(driver_results[driver_results['positionText'].str.contains('R|D|W|E', na=False)])
            total_points = driver_results['points'].sum()
            races_entered = len(driver_results)
            
            # Best and average finish (excluding DNFs)
            finished_races = driver_results[driver_results['positionOrder'].notna()]
            best_finish = finished_races['positionOrder'].min() if not finished_races.empty else None
            avg_finish = finished_races['positionOrder'].mean() if not finished_races.empty else None
            
            # Most common constructor
            constructor = driver_results['name'].mode().iloc[0] if not driver_results['name'].mode().empty else None
            
            return {
                'driver_name': f"{driver_info['forename']} {driver_info['surname']}",
                'wins': int(wins),
                'poles': int(poles),
                'podiums': int(podiums),
                'points_finishes': int(points_finishes),
                'dnfs': int(dnfs),
                'total_points': float(total_points),
                'best_finish': int(best_finish) if best_finish else None,
                'avg_finish': round(avg_finish, 1) if avg_finish else None,
                'constructor': constructor,
                'races_entered': int(races_entered)
            }
        
        driver1_stats = calculate_driver_stats(driver1_results, driver1_info)
        driver2_stats = calculate_driver_stats(driver2_results, driver2_info)
        
        # Head-to-head comparisons
        h2h_comparisons = []
        for _, race in season_races.iterrows():
            race_id = race['raceId']
            d1_race = driver1_results[driver1_results['raceId'] == race_id]
            d2_race = driver2_results[driver2_results['raceId'] == race_id]
            
            if not d1_race.empty and not d2_race.empty:
                d1_pos = d1_race.iloc[0]['positionOrder'] if pd.notna(d1_race.iloc[0]['positionOrder']) else None
                d2_pos = d2_race.iloc[0]['positionOrder'] if pd.notna(d2_race.iloc[0]['positionOrder']) else None
                
                if d1_pos is not None and d2_pos is not None:
                    winner = driver1_info['surname'] if d1_pos < d2_pos else driver2_info['surname']
                    margin = abs(d1_pos - d2_pos)
                else:
                    winner = "Both DNF" if d1_pos is None and d2_pos is None else "One DNF"
                    margin = None
                
                h2h_comparisons.append({
                    'race_name': race['name'],
                    'round': int(race['round']),
                    'driver1_position': int(d1_pos) if d1_pos else None,
                    'driver2_position': int(d2_pos) if d2_pos else None,
                    'winner': winner,
                    'margin': margin
                })
        
        # Calculate head-to-head record
        h2h_wins_d1 = len([c for c in h2h_comparisons if c['winner'] == driver1_info['surname']])
        h2h_wins_d2 = len([c for c in h2h_comparisons if c['winner'] == driver2_info['surname']])
        h2h_ties = len([c for c in h2h_comparisons if c['winner'] == "Both DNF"])
        
        return {
            'season': season,
            'driver1_stats': driver1_stats,
            'driver2_stats': driver2_stats,
            'head_to_head_record': {
                'driver1_wins': h2h_wins_d1,
                'driver2_wins': h2h_wins_d2,
                'ties': h2h_ties,
                'total_races': len(h2h_comparisons)
            },
            'race_by_race': h2h_comparisons
        }
        
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
