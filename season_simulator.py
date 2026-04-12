"""
Season Simulator - Uses RAG, Ollama, and web scraping to generate season summaries.
"""
import os
import io
import requests
from bs4 import BeautifulSoup
import wikipediaapi
import chromadb
from typing import List, Dict, Optional
from PIL import Image
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, PageBreak, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
import plotly.graph_objects as go
from datetime import datetime
import json

# Initialize ChromaDB client
chroma_client = chromadb.Client()

class SeasonSimulator:
    def __init__(self, ollama_base_url: str, ollama_model: str):
        """Initialize the season simulator with Ollama settings."""
        self.ollama_base_url = ollama_base_url.rstrip('/')
        self.ollama_model = ollama_model
        self.wiki = wikipediaapi.Wikipedia(
            language='en',
            user_agent='F1PointsCalculator/1.0'
        )
        
        # Initialize or get ChromaDB collection
        try:
            self.collection = chroma_client.get_collection("f1_seasons")
        except:
            self.collection = chroma_client.create_collection(
                name="f1_seasons",
                metadata={"description": "F1 season summaries from Wikipedia"}
            )
    
    def fetch_wikipedia_season_data(self, season_year: int) -> Optional[str]:
        """Fetch F1 season data from Wikipedia"""
        try:
            # Try different Wikipedia page formats
            possible_titles = [
                f"{season_year} Formula One World Championship",
                f"{season_year} Formula One season",
                f"{season_year} F1 season"
            ]
            
            for title in possible_titles:
                page = self.wiki.page(title)
                if page.exists():
                    # Extract relevant sections
                    summary = page.summary
                    text_content = page.text
                    
                    # Store in ChromaDB
                    self.collection.add(
                        documents=[summary],
                        metadatas=[{"year": season_year, "source": "wikipedia"}],
                        ids=[f"season_{season_year}"]
                    )
                    
                    return text_content
            
            return None
        except Exception as e:
            print(f"Error fetching Wikipedia data: {e}")
            return None
    
    def query_season_context(self, season_year: int, query: str = "") -> str:
        """Query ChromaDB for season context"""
        try:
            # First, ensure data exists
            if self.collection.count() == 0 or not self._season_exists(season_year):
                self.fetch_wikipedia_season_data(season_year)
            
            # Query the collection
            results = self.collection.query(
                query_texts=[query if query else f"Formula One {season_year} season summary"],
                n_results=1,
                where={"year": season_year}
            )
            
            if results and results['documents']:
                return results['documents'][0][0]
            
            return f"Limited information available for {season_year} F1 season."
        except Exception as e:
            print(f"Error querying season context: {e}")
            return f"Error retrieving context for {season_year} season."
    
    def _season_exists(self, season_year: int) -> bool:
        """Check if season data exists in ChromaDB"""
        try:
            results = self.collection.get(ids=[f"season_{season_year}"])
            return len(results['ids']) > 0
        except:
            return False
    
    def scrape_season_images(self, season_year: int, max_images: int = 5) -> List[str]:
        """Scrape F1 season images from web"""
        image_urls = []
        try:
            # Search for F1 season images (using a safe, public source)
            search_url = f"https://en.wikipedia.org/wiki/{season_year}_Formula_One_World_Championship"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(search_url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Find images in the article
                images = soup.find_all('img', limit=max_images * 2)
                
                for img in images:
                    src = img.get('src')
                    if src and ('upload.wikimedia.org' in src or src.startswith('//')):
                        # Make sure URL is absolute
                        if src.startswith('//'):
                            src = 'https:' + src
                        
                        # Filter for reasonable size images
                        if any(dim in src for dim in ['220px', '250px', '300px', '400px', '500px']):
                            image_urls.append(src)
                            if len(image_urls) >= max_images:
                                break
            
            return image_urls[:max_images]
        except Exception as e:
            print(f"Error scraping images: {e}")
            return []
    
    def download_image(self, url: str) -> Optional[Image.Image]:
        """Download and return PIL Image"""
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return Image.open(io.BytesIO(response.content))
            return None
        except Exception as e:
            print(f"Error downloading image: {e}")
            return None
    
    def generate_season_summary(self,
                               season_year: int, 
                               standings_data: Dict,
                               points_system: str,
                               wikipedia_context: str) -> str:
        """Generate AI summary using Ollama."""
        try:
            # Prepare standings text
            top_drivers = standings_data['standings'][:10]
            standings_text = "\n".join([
                f"{i+1}. {d['forename']} {d['surname']} ({d.get('constructor_name', 'Unknown')}) - {d['adjusted_points']} points"
                for i, d in enumerate(top_drivers)
            ])
            
            prompt = f"""You are an expert F1 analyst. Generate a comprehensive season summary for the {season_year} Formula One season.

Points System Used: {points_system}

Top 10 Driver Standings:
{standings_text}

Wikipedia Context:
{wikipedia_context[:2000]}

Please provide:
1. A brief overview of the season (2-3 paragraphs)
2. Key highlights and memorable moments
3. Championship battle analysis
4. Notable performances and surprises
5. How the points system affected the standings

Write in an engaging, informative style suitable for a professional report."""

            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=180
            )
            if response.status_code != 200:
                raise RuntimeError(f"Ollama returned status {response.status_code}: {response.text[:300]}")

            data = response.json()
            text = data.get('response', '').strip()
            if not text:
                raise RuntimeError("Ollama returned empty response")
            return text
        except Exception as e:
            print(f"Error generating summary: {e}")
            return f"Error generating AI summary. Basic info: {season_year} F1 season with {len(standings_data['standings'])} drivers."
    
    def create_pdf_report(self,
                         season_year: int,
                         standings_data: Dict,
                         points_system: str,
                         ai_summary: str,
                         image_urls: List[str],
                         chart_json_strings: Dict,
                         output_path: str):
        """Generate PDF report with standings, charts, images, and AI summary"""
        try:
            doc = SimpleDocTemplate(output_path, pagesize=letter,
                                   topMargin=0.5*inch, bottomMargin=0.5*inch)
            
            story = []
            styles = getSampleStyleSheet()
            
            # Custom styles
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=24,
                textColor=colors.HexColor('#e10600'),
                spaceAfter=30,
                alignment=TA_CENTER,
                fontName='Helvetica-Bold'
            )
            
            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=16,
                textColor=colors.HexColor('#0066cc'),
                spaceAfter=12,
                spaceBefore=12,
                fontName='Helvetica-Bold'
            )
            
            body_style = ParagraphStyle(
                'CustomBody',
                parent=styles['BodyText'],
                fontSize=11,
                alignment=TA_JUSTIFY,
                spaceAfter=12
            )
            
            # Title Page
            story.append(Spacer(1, 0.5*inch))
            story.append(Paragraph(f"{season_year} Formula One Season", title_style))
            story.append(Paragraph(f"Analyzed with {points_system} Points System", styles['Heading3']))
            story.append(Spacer(1, 0.3*inch))
            story.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
            story.append(PageBreak())
            
            # AI Summary
            story.append(Paragraph("Season Summary", heading_style))
            # Split AI summary into paragraphs
            for para in ai_summary.split('\n\n'):
                if para.strip():
                    story.append(Paragraph(para.strip(), body_style))
            story.append(Spacer(1, 0.3*inch))
            
            # Driver Standings Table
            story.append(Paragraph("Final Driver Standings", heading_style))
            
            # Prepare table data
            table_data = [['Pos', 'Driver', 'Team', 'Points']]
            for i, driver in enumerate(standings_data['standings'][:15], 1):
                table_data.append([
                    str(i),
                    f"{driver['forename']} {driver['surname']}",
                    driver.get('constructor_name', 'N/A'),
                    f"{driver['adjusted_points']:.0f}"
                ])
            
            # Create table
            table = Table(table_data, colWidths=[0.7*inch, 2.5*inch, 2*inch, 1*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e10600')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
            ]))
            
            story.append(table)
            story.append(PageBreak())
            
            # Add Charts
            story.append(Paragraph("Performance Charts", heading_style))
            
            # Save charts as images and add to PDF
            if chart_json_strings.get('cumulative_chart'):
                try:
                    fig = go.Figure(json.loads(chart_json_strings['cumulative_chart']))
                    img_bytes = fig.to_image(format="png", width=700, height=500)
                    img = RLImage(io.BytesIO(img_bytes), width=6*inch, height=4*inch)
                    story.append(Paragraph("Cumulative Points Progress", styles['Heading3']))
                    story.append(img)
                    story.append(Spacer(1, 0.2*inch))
                except Exception as e:
                    print(f"Error adding cumulative chart: {e}")
            
            if chart_json_strings.get('distribution_chart'):
                try:
                    fig = go.Figure(json.loads(chart_json_strings['distribution_chart']))
                    img_bytes = fig.to_image(format="png", width=700, height=500)
                    img = RLImage(io.BytesIO(img_bytes), width=6*inch, height=4*inch)
                    story.append(Paragraph("Points Distribution", styles['Heading3']))
                    story.append(img)
                    story.append(Spacer(1, 0.2*inch))
                except Exception as e:
                    print(f"Error adding distribution chart: {e}")
            
            story.append(PageBreak())
            
            # Add scraped images
            if image_urls:
                story.append(Paragraph("Season Gallery", heading_style))
                for i, url in enumerate(image_urls):
                    img = self.download_image(url)
                    if img:
                        # Save to bytes
                        img_byte_arr = io.BytesIO()
                        img.save(img_byte_arr, format='PNG')
                        img_byte_arr.seek(0)
                        
                        # Add to PDF
                        try:
                            rl_img = RLImage(img_byte_arr, width=5*inch, height=3*inch)
                            story.append(rl_img)
                            story.append(Spacer(1, 0.3*inch))
                        except:
                            pass
            
            # Build PDF
            doc.build(story)
            return True
            
        except Exception as e:
            print(f"Error creating PDF: {e}")
            return False


def simulate_season(season_year: int, 
                   standings_data: Dict,
                   points_system_name: str,
                   chart_json_strings: Dict,
                   ollama_base_url: str,
                   ollama_model: str,
                   output_dir: str = "exports") -> Optional[str]:
    """
    Main function to simulate season and generate PDF
    
    Args:
        season_year: The F1 season year
        standings_data: Dictionary containing standings information
        points_system_name: Name of the points system used
        chart_json_strings: Dictionary with chart JSONs
        ollama_base_url: Ollama HTTP base URL (e.g. http://localhost:11434)
        ollama_model: Ollama model name (e.g. llama3.1:8b)
        output_dir: Directory to save the PDF
    
    Returns:
        Path to generated PDF or None if failed
    """
    try:
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        # Initialize simulator
        simulator = SeasonSimulator(ollama_base_url, ollama_model)
        
        # Fetch Wikipedia context
        print(f"Fetching Wikipedia data for {season_year}...")
        wikipedia_context = simulator.query_season_context(season_year)
        
        # Generate AI summary
        print("Generating AI summary with Ollama...")
        ai_summary = simulator.generate_season_summary(
            season_year, 
            standings_data, 
            points_system_name,
            wikipedia_context
        )
        
        # Scrape images
        print("Scraping season images...")
        image_urls = simulator.scrape_season_images(season_year, max_images=3)
        
        # Generate PDF
        print("Creating PDF report...")
        output_filename = f"F1_Season_{season_year}_{points_system_name.replace(' ', '_')}.pdf"
        output_path = os.path.join(output_dir, output_filename)
        
        success = simulator.create_pdf_report(
            season_year,
            standings_data,
            points_system_name,
            ai_summary,
            image_urls,
            chart_json_strings,
            output_path
        )
        
        if success:
            print(f"PDF generated successfully: {output_path}")
            return output_path
        else:
            return None
            
    except Exception as e:
        print(f"Error in simulate_season: {e}")
        return None
