import sqlite3
import json
import os

db_path = "data/alpha_flow.db.corrupted_backup"
if not os.path.exists(db_path):
    print("No backup database found!")
    exit(1)

# Connect to the corrupted database in read-only mode using a URI
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
cursor = conn.cursor()

print("Attempting to salvage assets table...")

salvaged_assets = {}
failed_count = 0

try:
    cursor.execute("SELECT ticker, company_name, is_favorite, data FROM assets;")
    
    # We fetch row by row to catch errors on a per-row basis
    while True:
        try:
            row = cursor.fetchone()
            if row is None:
                break
                
            ticker, company_name, is_favorite, data_json = row
            try:
                parsed_data = json.loads(data_json) if data_json else {}
                salvaged_assets[ticker] = {
                    "company_name": company_name,
                    "is_favorite": bool(is_favorite),
                    "data": parsed_data
                }
                print(f"✅ Salvaged {ticker} (Favorite: {bool(is_favorite)})")
            except Exception as e_json:
                # If JSON parsing fails, we still keep the ticker, company name, and favorite status!
                salvaged_assets[ticker] = {
                    "company_name": company_name,
                    "is_favorite": bool(is_favorite),
                    "data": {}
                }
                print(f"⚠️ Salvaged {ticker} without full JSON data (Favorite: {bool(is_favorite)}): {e_json}")
        except sqlite3.DatabaseError as e_row:
            failed_count += 1
            print(f"❌ Failed to fetch a row due to database corruption: {e_row}")
            # In SQLite, if fetchone() fails with DatabaseError, the cursor state is often broken.
            # But we can try to continue or we can handle it.
            continue
except Exception as e:
    print(f"Critical query error: {e}")

conn.close()

print(f"\nSummary: Salvaged {len(salvaged_assets)} assets. Encountered {failed_count} row errors.")

# Save the salvaged assets as clean JSON files inside data/
if salvaged_assets:
    os.makedirs("data/salvaged_assets", exist_ok=True)
    # Let's count how many favorites we recovered
    favs = [t for t, a in salvaged_assets.items() if a["is_favorite"]]
    print(f"Recovered {len(favs)} favorites: {favs}")
    
    # Write them back to data/
    for ticker, info in salvaged_assets.items():
        # Reconstruct the complete asset JSON
        data = info["data"]
        # Ensure company_name is there
        if "company_name" not in data:
            data["company_name"] = info["company_name"]
            
        # We also need to save the favorite flag in some way or we can write a recovery script to apply them to the new DB!
        # Let's save a list of favorite tickers to data/favorites.json!
        pass
        
    # Write favorites list
    with open("data/favorites.json", "w") as f:
        json.dump(favs, f, indent=4)
        print("Saved favorites list to data/favorites.json")
        
    # Overwrite the files in data/ with salvaged data if they contain full reports!
    overwritten_count = 0
    for ticker, info in salvaged_assets.items():
        data = info["data"]
        if data and "company_name" in data and ("fin" in data or "cap" in data or "moat" in data or "verdict" in data):
            dest_path = f"data/{ticker}.json"
            with open(dest_path, "w", encoding="utf-8") as out:
                json.dump(data, out, indent=4, ensure_ascii=False)
            overwritten_count += 1
            
    print(f"Overwrote {overwritten_count} JSON files in data/ with complete salvaged assets.")
