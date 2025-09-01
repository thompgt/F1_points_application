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

warnings.filterwarnings("ignore")

    # ...existing code...

app = FastAPI(title="F1 Points Calculator", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
DEFAULT_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

class StandingsRequest(BaseModel):
    season_year: int
    points_system: Optional[List[int]] = None
    selected_driver_ids: Optional[List[int]] = None

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

    fig = px.line(
        season_results_filtered,
        x='race_number',
        y='cumulative_points',
        color='driver_label',
        title=f'Title Fight: Cumulative Points for Top Contenders in {season_year} ({points_system_name})',
        labels={'race_number': 'Race Number', 'cumulative_points': 'Cumulative Points', 'driver_label': 'Driver'},
        markers=True
    )
    fig.update_layout(
        height=400,
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02)
    )
    return fig.to_json()

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
    season_results['driver_label'] = season_results.apply(lambda row: f"{row['forename'][0]}. {row['surname']}", axis=1)
    season_results['cumulative_points'] = season_results.groupby(['driver_label'])['adjusted_points'].cumsum()
    # Determine which drivers to include
    if selected_driver_ids:
        season_results_filtered = season_results[season_results['driverId'].isin(selected_driver_ids)]
    else:
        top_12_drivers = (
            season_results.groupby(['driverId', 'surname', 'forename'], as_index=False)['adjusted_points']
            .sum()
            .sort_values(by='adjusted_points', ascending=False)
            .head(12)
        )
        season_results_filtered = season_results[
            season_results['driverId'].isin(top_12_drivers['driverId'])
        ]
    # Create the plot
    title_suffix = 'Selected Drivers' if selected_driver_ids else 'Top Drivers'
    fig = px.line(
        season_results_filtered,
        x='race_number',
        y='cumulative_points',
        color='driver_label',
        title=f'Cumulative Points for {title_suffix} in {season_year} Season ({points_system_name})',
        labels={'race_number': 'Race Number', 'cumulative_points': 'Cumulative Points', 'driver_label': 'Driver'},
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
        x='driver_label',
        y='adjusted_points',
        title=f'Points Distribution for {season_year} Season ({points_system_name})',
        labels={'driver_label': 'Driver', 'adjusted_points': 'Total Points'},
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
        title_fight_chart = create_title_fight_chart(adjusted_results_with_races, request.season_year, points_system_name)
        cumulative_chart = create_cumulative_points_chart(
            adjusted_results_with_races, request.season_year, points_system_name, request.selected_driver_ids
        )
        distribution_chart = create_points_distribution_chart(standings, request.season_year, points_system_name)

        # Constructors cumulative chart
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


@app.post("/api/head-to-head")
async def get_head_to_head_stats(selected_driver_ids: list, season: int):
    """Get head-to-head and filtered stats for selected drivers in a specific season."""
    try:
        results, races, drivers, _, constructors, driver_standings = load_data()
        season_races = races[races['year'] == season]
        if season_races.empty:
            raise HTTPException(status_code=404, detail=f"No races found for season {season}")
        race_ids = season_races['raceId'].tolist()

        # Get info and results for selected drivers
        selected_infos = []
        selected_stats = []
        selected_results = []
        for driver_id in selected_driver_ids:
            driver_df = drivers[drivers['driverId'] == driver_id]
            if driver_df.empty:
                continue
            driver_info = driver_df.iloc[0]
            season_results = results[results['raceId'].isin(race_ids)]
            driver_results = season_results[season_results['driverId'] == driver_id].copy()
            driver_results = pd.merge(driver_results, constructors[['constructorId', 'name']], on='constructorId', how='left')
            driver_results = pd.merge(driver_results, races[['raceId', 'name', 'round']], on='raceId', how='left')
            selected_infos.append(driver_info)
            selected_results.append(driver_results)
            # Stats
            wins = len(driver_results[driver_results['positionOrder'] == 1])
            poles = len(driver_results[driver_results['grid'] == 1])
            podiums = len(driver_results[driver_results['positionOrder'].isin([1, 2, 3])])
            points_finishes = len(driver_results[driver_results['points'] > 0])
            dnfs = len(driver_results[driver_results['positionText'].str.contains('R|D|W|E', na=False)])
            total_points = driver_results['points'].sum()
            races_entered = len(driver_results)
            finished_races = driver_results[driver_results['positionOrder'].notna()]
            best_finish = finished_races['positionOrder'].min() if not finished_races.empty else None
            avg_finish = finished_races['positionOrder'].mean() if not finished_races.empty else None
            constructor = driver_results['name'].mode().iloc[0] if not driver_results['name'].mode().empty else None
            selected_stats.append({
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
            })

        # Head-to-head only if two drivers selected
        h2h_comparisons = []
        h2h_record = None
        if len(selected_driver_ids) == 2:
            driver1_info = selected_infos[0]
            driver2_info = selected_infos[1]
            driver1_results = selected_results[0]
            driver2_results = selected_results[1]
            for _, race in season_races.iterrows():
                race_id = race['raceId']
                d1_race = driver1_results[driver1_results['raceId'] == race_id]
                d2_race = driver2_results[driver2_results['raceId'] == race_id]
                d1_pos = None
                d2_pos = None
                if not d1_race.empty:
                    val = d1_race['positionOrder'].iat[0]
                    d1_pos = int(val) if pd.notna(val) else None
                if not d2_race.empty:
                    val = d2_race['positionOrder'].iat[0]
                    d2_pos = int(val) if pd.notna(val) else None
                if d1_pos is not None and d2_pos is not None:
                    winner = str(driver1_info['surname']) if d1_pos < d2_pos else str(driver2_info['surname'])
                    margin = abs(d1_pos - d2_pos)
                elif d1_pos is None and d2_pos is None:
                    winner = "Both DNF"
                    margin = None
                else:
                    winner = "One DNF"
                    margin = None
                h2h_comparisons.append({
                    'race_name': race['name'],
                    'round': int(race['round']),
                    'driver1_position': d1_pos,
                    'driver2_position': d2_pos,
                    'winner': winner,
                    'margin': margin
                })
            driver1_surname = str(driver1_info['surname'])
            driver2_surname = str(driver2_info['surname'])
            h2h_wins_d1 = len([c for c in h2h_comparisons if c['winner'] == driver1_surname])
            h2h_wins_d2 = len([c for c in h2h_comparisons if c['winner'] == driver2_surname])
            h2h_ties = len([c for c in h2h_comparisons if c['winner'] == "Both DNF"])
            h2h_record = {
                'driver1_wins': h2h_wins_d1,
                'driver2_wins': h2h_wins_d2,
                'ties': h2h_ties,
                'total_races': len(h2h_comparisons)
            }

        return {
            'season': season,
            'selected_stats': selected_stats,
            'head_to_head_record': h2h_record,
            'race_by_race': h2h_comparisons
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
