import os
import json
import logging
import requests
from datetime import datetime

DATA_DIR = "data"

class AlphaVantageFetcher:
    """
    Handles multi-endpoint data retrieval from Alpha Vantage to build
    a comprehensive financial dataset, with local JSON caching for speed.
    No pandas used for maximum efficiency.
    """

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ALPHAVANTAGE_API_KEY", "demo")

    def _get_cache_path(self, ticker: str):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
        return os.path.join(DATA_DIR, f"{ticker.upper()}_financials.json")

    def _fetch_json(self, function: str, ticker: str):
        """Internal helper to manage API requests."""
        params = {
            "function": function,
            "symbol": ticker.upper(),
            "apikey": self.api_key,
        }
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            data = response.json()

            # Alpha Vantage error handling
            if "Note" in data or "Information" in data:
                logging.warning("API Limit reached (Standard: 25 calls/day). Please wait.")
                return None
            if "ErrorMessage" in data or not data:
                logging.error(f"Error fetching {function} for {ticker}: {data.get('ErrorMessage')}")
                return None

            return data
        except Exception as e:
            logging.error(f"Request error for {function}: {e}")
            return None

    def get_financials(self, ticker: str, max_years=15) -> dict:
        """
        Retrieves Income Statement, Cash Flow, and Earnings.
        Merges them and calculates key metrics like FCF.
        Caches to JSON to avoid redundant API calls within the same year.
        """
        cache_path = self._get_cache_path(ticker)
        current_year = datetime.now().year

        # Check cache
        if os.path.exists(cache_path):
            mod_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
            if mod_time.year == current_year:
                print(f"--- Loading fast JSON cached financials for {ticker} ---")
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    # Limit to max_years if cache has more
                    for k in cached_data:
                        cached_data[k] = cached_data[k][-max_years:]
                    return cached_data
                except Exception as e:
                    print(f"Error reading cache: {e}")

        print(f"--- Fetching full financials for {ticker} from Alpha Vantage ---")

        # 1. Retrieve Data from 3 different endpoints
        income_json = self._fetch_json("INCOME_STATEMENT", ticker)
        cash_json = self._fetch_json("CASH_FLOW", ticker)
        earnings_json = self._fetch_json("EARNINGS", ticker)

        if not income_json or not cash_json or not earnings_json:
            return None

        # Extract annual reports
        income_reports = income_json.get("annualReports", [])
        cash_reports = cash_json.get("annualReports", [])
        earnings_reports = earnings_json.get("annualEarnings", [])

        if not income_reports:
            return None

        # Build lookup dictionaries keyed by year
        cash_map = {r.get('fiscalDateEnding', '')[:4]: r for r in cash_reports}
        earn_map = {r.get('fiscalDateEnding', '')[:4]: r for r in earnings_reports}

        years = []
        rev = []
        ni = []
        eps = []
        fcf = []

        # Process in chronological order (Alpha Vantage returns newest first, so we reverse it)
        for inc in reversed(income_reports):
            date = inc.get('fiscalDateEnding', '')
            if not date: continue
            year = date[:4]
            years.append(year)

            def sfloat(val):
                try:
                    return float(val) if val and val != "None" else 0.0
                except:
                    return 0.0

            rev.append(sfloat(inc.get('totalRevenue', 0)))
            ni.append(sfloat(inc.get('netIncome', 0)))

            # Cash Flow
            cf_rpt = cash_map.get(year, {})
            op_cf = sfloat(cf_rpt.get('operatingCashflow', 0))
            capex = sfloat(cf_rpt.get('capitalExpenditures', 0))
            # FCF = Operating Cash Flow - CapEx
            fcf.append(op_cf - capex)

            # Earnings
            earn_rpt = earn_map.get(year, {})
            eps.append(sfloat(earn_rpt.get('reportedEPS', 0)))

        final_data = {
            'years': years,
            'revenue': rev,
            'net_income': ni,
            'eps': eps,
            'fcf': fcf
        }

        # Cache the result
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(final_data, f)
        except Exception as e:
            print(f"Error saving cache: {e}")

        # Limit return to max_years
        for k in final_data:
            final_data[k] = final_data[k][-max_years:]

        return final_data