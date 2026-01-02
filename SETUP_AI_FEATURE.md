# Setup Instructions for AI Season Simulation Feature

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This will install all required packages including:
- `google-generativeai` - Google Gemini AI API
- `beautifulsoup4` - Web scraping
- `chromadb` - Vector database for RAG
- `wikipedia-api` - Wikipedia data fetching
- `reportlab` - PDF generation
- `Pillow` - Image processing
- `kaleido` - Plotly image export

### 2. Get Gemini API Key

1. Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Sign in with your Google account
3. Click "Create API Key"
4. Copy the generated API key

### 3. Configure Environment

**Option A: Using .env file (Recommended)**
```bash
# Copy the example file
cp .env.example .env

# Edit .env and add your key
GEMINI_API_KEY=your_actual_api_key_here
```

**Option B: Enter at runtime**
- The application will prompt for the API key when you click "Simulate Season"

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
5. Enter your Gemini API key if prompted
6. Wait 30-60 seconds for generation
7. PDF will download automatically!

## What Gets Generated

The PDF report includes:

### 📊 Comprehensive Analysis
- **AI-Generated Summary**: Gemini analyzes the season with context
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
5. **Generate**: Gemini creates comprehensive analysis

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

### Gemini API errors
- **Invalid API key**: Check your key at AI Studio
- **Quota exceeded**: Free tier has limits, wait or upgrade
- **Rate limit**: Wait a few seconds between requests

## API Rate Limits

### Gemini API (Free Tier)
- 60 requests per minute
- 1,500 requests per day
- Season simulation uses 1 request

### Wikipedia
- Be respectful with scraping
- Built-in delays and user agent
- Maximum 5 images per season

## Cost Estimation

### Free Tier Usage
- ✅ Gemini: FREE (within limits)
- ✅ Wikipedia: FREE
- ✅ ChromaDB: FREE (local storage)

### If Upgrading
- Gemini Pro: $0.00025 per 1K characters
- Average season simulation: ~5K characters = $0.00125

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

### Different AI Model
Edit in `season_simulator.py`:
```python
self.model = genai.GenerativeModel('gemini-1.5-pro')  # More powerful
```

## File Structure

```
F1_adjusted/
├── main.py                  # FastAPI backend with new endpoint
├── season_simulator.py      # NEW: AI simulation logic
├── requirements.txt         # Updated with new packages
├── .env.example            # API key template
├── .env                    # Your actual API key (gitignored)
├── .gitignore             # Ignores .env and exports/
├── exports/               # Generated PDFs stored here
│   └── README.md
└── templates/
    └── index.html         # Updated with Simulate button
```

## Security Notes

⚠️ **Never commit your .env file or API keys to version control!**

- `.env` is in `.gitignore`
- API keys can also be entered at runtime
- Consider using environment variables in production

## Support

If you encounter issues:
1. Check the console output for detailed error messages
2. Verify all dependencies are installed
3. Ensure your Gemini API key is valid
4. Check that CSV files are in the correct location

Enjoy generating AI-powered F1 season reports! 🏎️✨
