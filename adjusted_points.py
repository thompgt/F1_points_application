import pandas as pd
import warnings
import seaborn as sns
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

# Define the modern points system
MODERN_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

def adjust_points(results_df):
    """
    Adjust the points in the results DataFrame to the modern points system.
    """
    adjusted_results = results_df.copy()
    adjusted_results['adjusted_points'] = 0

    for i, points in enumerate(MODERN_POINTS, start=1):
        adjusted_results.loc[adjusted_results['positionOrder'] == i, 'adjusted_points'] = points

    return adjusted_results

def calculate_standings(adjusted_results_with_races, season_year):
    """
    Calculate the standings for a given season.
    """
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year]
    standings = season_results.groupby(['surname', 'forename'], as_index=False)['adjusted_points'].sum()
    standings = standings.sort_values(by='adjusted_points', ascending=False).reset_index(drop=True)
    standings.index += 1
    standings.reset_index(inplace=True)
    standings.rename(columns={'index': 'Position'}, inplace=True)
    return standings

def plot_cumulative_points(adjusted_results_with_races, season_year):
    """
    Plot cumulative points for the top 10 drivers over the season.
    """
    # Filter results for the given season
    season_results = adjusted_results_with_races[adjusted_results_with_races['year'] == season_year]

    # Sort by race order to ensure cumulative points are calculated correctly
    season_results = season_results.sort_values(by=['raceId', 'positionOrder'])

    # Calculate cumulative points for each driver
    season_results['cumulative_points'] = season_results.groupby(['surname', 'forename'])['adjusted_points'].cumsum()

    # Get the top 10 drivers based on total points
    top_10_drivers = (
        season_results.groupby(['surname', 'forename'], as_index=False)['adjusted_points']
        .sum()
        .sort_values(by='adjusted_points', ascending=False)
        .head(10)
    )

    # Filter the season results to include only the top 10 drivers
    season_results_top_10 = season_results[
        season_results['surname'].isin(top_10_drivers['surname'])
    ]

    # Plot cumulative points using Seaborn
    plt.figure(figsize=(12, 6))
    sns.lineplot(
        data=season_results_top_10,
        x='raceId',
        y='cumulative_points',
        hue='surname',
        marker='o'
    )
    plt.title(f'Cumulative Points for Top 10 Drivers in {season_year} Season', fontsize=16)
    plt.xlabel('Race ID', fontsize=12)
    plt.ylabel('Cumulative Points', fontsize=12)
    plt.legend(title='Driver', bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    plt.show()
    
def main():
    # Load the necessary CSV files
    results = pd.read_csv('results.csv')
    races = pd.read_csv('races.csv')
    driver_info = pd.read_csv('drivers.csv')

    # Adjust points in the results DataFrame
    adjusted_results = adjust_points(results)

    # Merge adjusted results with driver information for better readability
    adjusted_results_with_drivers = pd.merge(
        adjusted_results,
        driver_info[['driverId', 'surname', 'forename']],
        on='driverId'
    )

    # Merge with race information to include race details
    adjusted_results_with_races = pd.merge(
        adjusted_results_with_drivers,
        races[['raceId', 'year', 'name']],
        on='raceId'
    )

    # Calculate standings for a specific season (e.g., 2009)
    season_year = 2024
    standings = calculate_standings(adjusted_results_with_races, season_year)

    # Display the standings
    print(f"Standings for the {season_year} season:")
    print(standings.to_string(index=False))

    # Plot cumulative points for the season
    plot_cumulative_points(adjusted_results_with_races, season_year)

if __name__ == "__main__":
    main()