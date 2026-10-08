db_path = "data/alpha_flow.db"
with open(db_path, "rb") as f:
    raw_data = f.read()

page_size = 4096
num_pages = len(raw_data) // page_size

print(f"Total pages: {num_pages}")

target1 = b"full_audit_structure"
target2 = b"financials_hist_manual"

for p_idx in range(num_pages):
    offset = p_idx * page_size
    page = raw_data[offset:offset + page_size]
    
    if target1 in page or target2 in page:
        # Count printable ASCII in this page
        printable_count = sum(1 for b in page if 32 <= b <= 126 or b in [10, 13, 9])
        pct = (printable_count / page_size) * 100
        print(f"Page {p_idx:4d} contains target! Printable: {pct:5.1f}%")
        # Check first 200 bytes of text on this page
        preview = page[:300].decode('utf-8', errors='ignore').replace('\n', ' ')
        print(f"  Preview: {preview[:150]}...")
