import os

DATA_DIR = os.getenv("DATA_DIR", "data")
CSV_FILE = os.getenv("CSV_FILE", "Transactions.csv")
MANUAL_TX_FILE = os.path.join(DATA_DIR, "manual_transactions.json")
DIV_CACHE_FILE = os.path.join(DATA_DIR, "historical_dividends.json")
SPLIT_CACHE_FILE = os.path.join(DATA_DIR, "historical_splits.json")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
CLOSED_PORTFOLIO_FILE = os.path.join(DATA_DIR, "closed_portfolio.json")
LEDGER_FILE = os.path.join(DATA_DIR, "ledger.json")

# Asset mapping (ISIN to Ticker)
ISIN_TO_TICKER = {
    'US90353T1007': 'UBER', 'US6701002056': 'NVO', 'SE0012673267': 'EVO.ST',
    'US67066G1040': 'NVDA', 'US91324P1021': 'UNH', 'US7731211089': 'RKLB',
    'US7731221062': 'RKLB', 'US58733R1023': 'MELI', 'AU0000185993': 'IREN',
    'US6837121036': 'OPEN', 'US4330001060': 'HIMS', 'US17253J1060': 'CIFR',
    'ES0184262212': 'VIS.MC', 'US00217D1000': 'ASTS', 'US8200144058': 'SBET',
    'US01609W1027': 'BABA', 'US5002551043': 'KSS', 'US02079K3059': 'GOOGL',
    'ES0130670112': 'ELE.MC', 'US88557W1018': 'QFIN', 'ES0177542018': 'IAG.MC',
    'FR0000121014': 'MC.PA', 'ES0684262928': 'VIS.MC', 'ES0105122024': 'MVC.MC',
    'US6541061031': 'NKE', 'ES0183746314': 'VID.MC', 'ES0684262910': 'VIS.MC',
    'US1912161007': 'KO', 'US30303M1027': 'META', 'IE00B4ND3602': 'IGLN.L',
    'ES0148396007': 'ITX.MC'
}
