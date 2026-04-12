# F1 Points Calculator

A full-stack web application that allows users to calculate Formula 1 driver standings using different points systems. Built with FastAPI, Plotly, and modern web technologies.

## Features

- **Season Selection**: Choose from any F1 season (1950-2024)
- **Multiple Points Systems**: 
  - Modern (2010-2024): 25, 18, 15, 12, 10, 8, 6, 4, 2, 1
  - Classic (1991-2002): 10, 6, 4, 3, 2, 1
  - Pre-1991: 9, 6, 4, 3, 2, 1
  - Custom: Define your own points system
- **Interactive Visualizations**: 
  - Cumulative points chart showing how drivers' points evolved throughout the season
  - Points distribution bar chart for the top 15 drivers
- **AI-Powered Season Simulation** (NEW!):
   - Generate comprehensive season reports with local Ollama
  - RAG (Retrieval Augmented Generation) using Wikipedia data
  - Web scraping for season images with Beautiful Soup
  - Export detailed PDF reports with charts, images, and AI analysis
- **Modern UI**: Responsive design with Bootstrap and custom styling
- **Real-time Calculations**: Fast API responses with Plotly charts

## Installation

1. **Clone or download the project files**

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up Ollama (for AI Season Simulation)**:
    - Model used by this app is fixed to: `llama3.1:8b`
    - Local install option:
       - Install Ollama from https://ollama.com/download
       - Pull model: `ollama pull llama3.1:8b`
       - Verify server: `curl http://localhost:11434/api/tags`
    - Docker option:
       - Run: `docker compose -f docker-compose.ollama.yml up -d`
       - Pull model: `docker exec -it ollama ollama pull llama3.1:8b`
       - Verify server: `curl http://localhost:11434/api/tags`
    - Optional env config:
       - `OLLAMA_BASE_URL=http://localhost:11434`

4. **Ensure you have the required CSV files**:
   - `results.csv` - Race results data
   - `races.csv` - Race information
   - `drivers.csv` - Driver information
   - `seasons.csv` - Available seasons

## Usage

1. **Start the application**:
   ```bash
   python main.py
   ```

2. **Open your web browser** and navigate to:
   ```
   http://localhost:8000
   ```

3. **Select a season** from the dropdown menu

4. **Choose a points system**:
   - Modern (default): Current F1 points system
   - Classic: Points system used from 1991-2002
   - Pre-1991: Points system used before 1991
   - Custom: Enter your own points (e.g., "10, 8, 6, 4, 3, 2, 1")

5. **Click "Calculate Standings"** to see the results

6. **Generate AI Season Report** (Optional):
   - Click "Simulate Season" button
   - No model selection is required (app uses `llama3.1:8b`)
   - Wait 30-60 seconds for the AI to generate a comprehensive report
   - PDF will download automatically with:
     - AI-generated season summary and analysis
     - Historical context from Wikipedia (RAG)
     - All standings and statistics
     - Interactive charts
     - Season images from web scraping

## API Endpoints

- `GET /` - Main application page
- `GET /api/seasons` - Get all available seasons
- `POST /api/calculate-standings` - Calculate standings for a season with specified points system
- `GET /api/points-systems` - Get predefined points systems
- `POST /api/simulate-season` - Generate AI-powered season simulation PDF (uses Ollama)

## Example API Usage

```python
import requests

# Get available seasons
seasons = requests.get("http://localhost:8000/api/seasons").json()

# Calculate standings for 2023 with modern points
response = requests.post("http://localhost:8000/api/calculate-standings", 
                        json={"season_year": 2023})

# Calculate standings with custom points
response = requests.post("http://localhost:8000/api/calculate-standings", 
                        json={
                            "season_year": 2023,
                            "points_system": [10, 8, 6, 4, 3, 2, 1]
                        })
```

## Data Sources

The application uses historical F1 data from CSV files containing:
- Race results and positions
- Driver information
- Race details and seasons
- Circuit information

## Technical Stack

- **Backend**: FastAPI (Python)
- **Frontend**: HTML5, CSS3, JavaScript
- **Styling**: Bootstrap 5, Custom CSS
- **Visualizations**: Plotly.js
- **Data Processing**: Pandas
- **AI/ML**: Ollama, ChromaDB (Vector Database), RAG
- **Web Scraping**: Beautiful Soup, Requests, Wikipedia API
- **PDF Generation**: ReportLab, Kaleido
- **Icons**: Font Awesome

## Customization

### Adding New Points Systems

To add new predefined points systems, modify the `get_points_systems()` function in `main.py`:

```python
@app.get("/api/points-systems")
async def get_points_systems():
    return {
        "points_systems": {
            "modern": {"name": "Modern (2010-2024)", "points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]},
            "classic": {"name": "Classic (1991-2002)", "points": [10, 6, 4, 3, 2, 1]},
            "pre_1991": {"name": "Pre-1991", "points": [9, 6, 4, 3, 2, 1]},
            "your_system": {"name": "Your System", "points": [15, 12, 10, 8, 6, 4, 2, 1]},
            "custom": {"name": "Custom", "points": []}
        }
    }
```

### Modifying Visualizations

The charts are created using Plotly. You can modify the chart functions in `main.py`:
- `create_cumulative_points_chart()` - Cumulative points over the season
- `create_points_distribution_chart()` - Final points distribution

## Troubleshooting

1. **Port already in use**: Change the port in `main.py`:
   ```python
   uvicorn.run(app, host="0.0.0.0", port=8001)
   ```

2. **Missing CSV files**: Ensure all required CSV files are in the project directory

3. **Dependencies issues**: Try updating pip and reinstalling requirements:
   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

## License

This project is open source and available under the MIT License.

## Contributing

Feel free to submit issues, feature requests, or pull requests to improve the application.
