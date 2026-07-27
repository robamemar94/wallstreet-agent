import os
import requests
import csv
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

# Configuration
API_KEY = "demo" 
DATA_DIR = "data"
LOGOS_DIR = "logos_cache"
FONT_PATH = "/usr/share/fonts/truetype/fonts-yrsa-rasa/Yrsa-Bold.ttf"
OUTPUT_FILE = "earnings_calendar.png"

# Colors and dimensions
CANVAS_WIDTH = 1500
CANVAS_HEIGHT = 1000
BG_COLOR = (20, 20, 25) # Dark theme like the example
HEADER_BG = (35, 35, 45)
ACCENT_COLOR = (0, 150, 255)
TEXT_COLOR = (240, 240, 240)
SUBTEXT_COLOR = (180, 180, 180)
WHITE = (255, 255, 255)
COLUMN_WIDTH = CANVAS_WIDTH // 5
ROW_HEIGHT = CANVAS_HEIGHT // 2

def get_my_tickers():
    import storage
    tickers = storage.get_all_analyzed_tickers()
    return set(t.upper() for t in tickers)

def download_logos():
    # Deshabilitado por petición del usuario
    pass

def get_logo(ticker):
    # Deshabilitado por petición del usuario para evitar llamadas innecesarias
    return None

def fetch_earnings():
    print("Fetching earnings calendar from Alpha Vantage...")
    url = f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=3month&apikey={API_KEY}"
    try:
        resp = requests.get(url, timeout=10)
        decoded_content = resp.content.decode('utf-8')
        cr = csv.reader(decoded_content.splitlines(), delimiter=',')
        my_list = list(cr)
        return my_list[1:] # Skip header
    except Exception as e:
        print(f"Error fetching earnings: {e}")
        return []

def generate_calendar():
    my_tickers = get_my_tickers()
    reports = fetch_earnings()
    download_logos()
    
    # Current date is 2026-04-14 (Tuesday)
    # Let's show the week of 2026-04-13 to 2026-04-17
    start_date = datetime(2026, 4, 13)
    end_date = start_date + timedelta(days=5)
    
    filtered = []
    for r in reports:
        # r: symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay
        date_str = r[2]
        try:
            r_date = datetime.strptime(date_str, "%Y-%m-%d")
            if start_date <= r_date < end_date:
                filtered.append(r)
        except:
            continue
            
    # Group by date and time
    calendar_data = {}
    for i in range(5):
        day = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        calendar_data[day] = {"pre-market": [], "post-market": [], "other": []}
        
    for r in filtered:
        day = r[2]
        time = r[6].lower() if r[6] else "other"
        if time not in ["pre-market", "post-market"]:
            time = "other"
        
        # Sort priority: if in my_tickers, put at front
        symbol = r[0]
        calendar_data[day][time].append(symbol)

    # Sort each list so my_tickers are first
    for day in calendar_data:
        for time in calendar_data[day]:
            calendar_data[day][time].sort(key=lambda x: x not in my_tickers)

    # Draw
    img = Image.new('RGB', (CANVAS_WIDTH, CANVAS_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    try:
        font_header = ImageFont.truetype(FONT_PATH, 36)
        font_date = ImageFont.truetype(FONT_PATH, 24)
        font_ticker = ImageFont.truetype(FONT_PATH, 18)
        font_title = ImageFont.truetype(FONT_PATH, 48)
    except:
        font_header = ImageFont.load_default()
        font_date = ImageFont.load_default()
        font_ticker = ImageFont.load_default()
        font_title = ImageFont.load_default()

    # Title
    draw.text((CANVAS_WIDTH // 2 - 200, 20), "EARNINGS CALENDAR", fill=ACCENT_COLOR, font=font_title)
    draw.text((CANVAS_WIDTH // 2 - 150, 75), f"Week of {start_date.strftime('%B %d, %Y')}", fill=SUBTEXT_COLOR, font=font_date)

    days_of_week = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
    
    content_y_start = 140
    
    for i in range(5):
        day_date = (start_date + timedelta(days=i))
        day_str = day_date.strftime("%Y-%m-%d")
        x_offset = i * COLUMN_WIDTH
        
        # Day Header
        draw.rectangle([x_offset + 5, content_y_start, x_offset + COLUMN_WIDTH - 5, content_y_start + 80], fill=HEADER_BG)
        draw.text((x_offset + 20, content_y_start + 10), days_of_week[i], fill=WHITE, font=font_header)
        draw.text((x_offset + 20, content_y_start + 45), day_date.strftime("%b %d"), fill=SUBTEXT_COLOR, font=font_date)
        
        # Before Open
        section_y = content_y_start + 100
        draw.text((x_offset + 20, section_y), "BEFORE OPEN", fill=ACCENT_COLOR, font=font_ticker)
        draw.line([x_offset + 20, section_y + 25, x_offset + COLUMN_WIDTH - 20, section_y + 25], fill=HEADER_BG)
        
        y_offset = section_y + 40
        for ticker in calendar_data[day_str]["pre-market"][:8]: # Show top 8
            logo = get_logo(ticker)
            if logo:
                # Create a small white background circle/rounded rect for the logo
                logo_size = 50
                bg_size = 60
                logo_bg = Image.new('RGBA', (bg_size, bg_size), (0,0,0,0))
                bg_draw = ImageDraw.Draw(logo_bg)
                bg_draw.ellipse([0, 0, bg_size, bg_size], fill=(255, 255, 255, 255))
                
                logo.thumbnail((logo_size, logo_size))
                # Center logo on bg
                lx = (bg_size - logo.width) // 2
                ly = (bg_size - logo.height) // 2
                logo_bg.paste(logo, (lx, ly), logo if logo.mode == 'RGBA' else None)
                
                img.paste(logo_bg, (x_offset + 20, y_offset), logo_bg)
                draw.text((x_offset + 90, y_offset + 20), ticker, fill=TEXT_COLOR, font=font_ticker)
                y_offset += 75
            else:
                # If no logo, just draw the text
                draw.text((x_offset + 25, y_offset + 20), ticker, fill=TEXT_COLOR, font=font_ticker)
                y_offset += 75
        
        # After Close
        section_y = content_y_start + 550
        draw.text((x_offset + 20, section_y), "AFTER CLOSE", fill=ACCENT_COLOR, font=font_ticker)
        draw.line([x_offset + 20, section_y + 25, x_offset + COLUMN_WIDTH - 20, section_y + 25], fill=HEADER_BG)
        
        y_offset = section_y + 40
        for ticker in calendar_data[day_str]["post-market"][:8]: # Show top 8
            logo = get_logo(ticker)
            if logo:
                logo_size = 50
                bg_size = 60
                logo_bg = Image.new('RGBA', (bg_size, bg_size), (0,0,0,0))
                bg_draw = ImageDraw.Draw(logo_bg)
                bg_draw.ellipse([0, 0, bg_size, bg_size], fill=(255, 255, 255, 255))
                
                logo.thumbnail((logo_size, logo_size))
                lx = (bg_size - logo.width) // 2
                ly = (bg_size - logo.height) // 2
                logo_bg.paste(logo, (lx, ly), logo if logo.mode == 'RGBA' else None)
                
                img.paste(logo_bg, (x_offset + 20, y_offset), logo_bg)
                draw.text((x_offset + 90, y_offset + 20), ticker, fill=TEXT_COLOR, font=font_ticker)
                y_offset += 75
            else:
                draw.text((x_offset + 25, y_offset + 20), ticker, fill=TEXT_COLOR, font=font_ticker)
                y_offset += 75

    # Footer
    footer_text = "Generated by Alpha-Flow Agent | Data from Alpha Vantage"
    draw.text((20, CANVAS_HEIGHT - 40), footer_text, fill=SUBTEXT_COLOR, font=font_ticker)

    # Draw vertical grid lines
    for i in range(1, 5):
        draw.line([i * COLUMN_WIDTH, content_y_start, i * COLUMN_WIDTH, CANVAS_HEIGHT - 50], fill=HEADER_BG, width=2)

    img.save(OUTPUT_FILE)
    print(f"Successfully generated {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_calendar()
