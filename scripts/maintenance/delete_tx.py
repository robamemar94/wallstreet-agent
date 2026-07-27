import re

with open('utils/portfolio_manager.py', 'r') as f:
    content = f.read()

# Add delete manual transaction function
new_func = """
def delete_manual_transaction(date_str, tx_type, ticker, shares, price):
    txs = load_manual_transactions()
    # Find and remove exact match
    for i, tx in enumerate(txs):
        if (tx['date'] == date_str and 
            tx['type'] == tx_type and 
            tx['ticker'] == ticker.upper() and 
            abs(tx['shares'] - float(shares)) < 0.001 and 
            abs(tx['price'] - float(price)) < 0.001):
            del txs[i]
            break
            
    with open(MANUAL_TX_FILE, "w", encoding="utf-8") as f:
        json.dump(txs, f, indent=4)
        
    rebuild_portfolio()
"""

content = content.replace("def rebuild_portfolio():", new_func + "\ndef rebuild_portfolio():")

with open('utils/portfolio_manager.py', 'w') as f:
    f.write(content)
print("Updated portfolio_manager.py")
