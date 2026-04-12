# Setup Instructions for AI Season Simulation Feature (Ollama + Docker)

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This will install all required packages including:
- `beautifulsoup4` - Web scraping
- `chromadb` - Vector database for RAG
- `wikipedia-api` - Wikipedia data fetching
- `reportlab` - PDF generation
- `Pillow` - Image processing
- `kaleido` - Plotly image export

### 2. Start Ollama with Docker

This app uses a fixed model: `llama3.1:8b`.

### 2A. Local Ollama Setup (No Docker)

1. Install Ollama: https://ollama.com/download
2. Pull the model:

```bash
ollama pull llama3.1:8b
```

3. Verify Ollama server is available:

```bash
curl http://localhost:11434/api/tags
```

### 2B. Docker Ollama Setup

```bash
docker compose -f docker-compose.ollama.yml up -d
```

Then pull a model (example):

```bash
docker exec -it ollama ollama pull llama3.1:8b
```

### 3. Configure Environment

Use environment variables (optional if using defaults):

```bash
OLLAMA_BASE_URL=http://localhost:11434
```

### 4. Run the Application

```bash
python main.py
```

Or with uvicorn:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Use the Simulate Season Feature

1. Open http://localhost:8000 in your browser
2. Select a season (e.g., 2021)
3. Choose a points system
4. Click **"Simulate Season"** button
5. No model selection is required (fixed to `llama3.1:8b`)
6. Wait 30-60 seconds for generation
7. PDF will download automatically!

## What Gets Generated

The PDF report includes:

### 📊 Comprehensive Analysis
- **AI-Generated Summary**: Ollama analyzes the season with context
- **Historical Context**: Wikipedia data via RAG (Retrieval Augmented Generation)
- **Championship Analysis**: AI insights on the title battle
- **Key Highlights**: Memorable moments and surprises

### 📈 Data & Charts
- **Driver Standings Table**: Top 15 drivers with points
- **Cumulative Points Chart**: Race-by-race progression
- **Points Distribution**: Visual comparison of driver performances
- **Constructor Analysis**: Team performance over the season

### 🖼️ Season Images
- Up to 5 images scraped from Wikipedia
- Historic race photos and championship moments
- High-quality images optimized for print

## How It Works

### RAG (Retrieval Augmented Generation)
1. **Fetch**: Scrapes F1 season data from Wikipedia
2. **Store**: Saves to ChromaDB vector database
3. **Retrieve**: Queries relevant context for the season
4. **Augment**: Combines with standings data
5. **Generate**: Ollama creates comprehensive analysis

### Web Scraping
- Uses Beautiful Soup to parse Wikipedia pages
- Finds relevant F1 season images
- Downloads and processes for PDF inclusion

### PDF Generation
- ReportLab creates professional layouts
- Plotly charts converted to high-res images
- Custom styling with F1 branding

## Troubleshooting

### "Import could not be resolved" errors
These are normal before installation. Run:
```bash
pip install -r requirements.txt
```

### ChromaDB errors
If you get ChromaDB errors, try:
```bash
pip install --upgrade chromadb
```

### Plotly image export issues
Ensure kaleido is installed:
```bash
pip install kaleido==0.2.1
```

### No images in PDF
This is normal for older seasons (pre-2000s) or if Wikipedia has limited content.

### Ollama errors
- **Connection refused**: Make sure Docker is running and Ollama container is up
- **Model not found**: Pull the model via `docker exec -it ollama ollama pull <model>`
- **Slow response**: First request is slower while model warms up

## Data Source Limits

### Wikipedia
- Be respectful with scraping
- Built-in delays and user agent
- Maximum 5 images per season

## Cost Estimation

### Local Setup Usage
- ✅ Ollama (local): No API key cost
- ✅ Wikipedia: FREE
- ✅ ChromaDB: FREE (local storage)

## Advanced Configuration

### Custom Output Directory
Edit in `season_simulator.py`:
```python
output_dir="exports"  # Change to your preferred path
```

### More Images
Edit in `season_simulator.py`:
```python
image_urls = simulator.scrape_season_images(season_year, max_images=10)
```

### Change Fixed AI Model (Optional)
Edit `FIXED_OLLAMA_MODEL` in `main.py`:
```python
FIXED_OLLAMA_MODEL = "llama3.1:8b"
```

## File Structure

```
F1_adjusted/
├── main.py                  # FastAPI backend with new endpoint
├── season_simulator.py      # NEW: AI simulation logic
├── requirements.txt         # Updated with new packages
├── docker-compose.ollama.yml  # Ollama container setup
├── .env                    # Optional Ollama config (gitignored)
├── .gitignore             # Ignores .env and exports/
├── exports/               # Generated PDFs stored here
│   └── README.md
└── templates/
    └── index.html         # Updated with Simulate button
```

## Security Notes

⚠️ **Never commit your .env file or secrets to version control!**

- `.env` is in `.gitignore`
- Model is fixed in backend code (no user override from UI)
- Consider using environment variables in production

## Support

If you encounter issues:
1. Check the console output for detailed error messages
2. Verify all dependencies are installed
3. Ensure Ollama is reachable and model is pulled
4. Check that CSV files are in the correct location

Enjoy generating AI-powered F1 season reports with local Ollama!
