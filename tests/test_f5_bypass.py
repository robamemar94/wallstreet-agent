import time
from fastapi.testclient import TestClient
from main import app
from app.interfaces.views.market_views import INDEX_CACHE
from app.interfaces.views.portfolio_views import GLOBAL_CACHE

def test_f5_bypass():
    client = TestClient(app)
    
    # 1. Clear caches initially
    INDEX_CACHE["prices_update"] = 0
    INDEX_CACHE["last_update"] = 0
    INDEX_CACHE["data"] = {}

    GLOBAL_CACHE["last_update"] = 0
    GLOBAL_CACHE["prices"] = {}

    # 2. Make a normal request to populate cache
    print("Making first request...")
    response = client.get("/")
    assert response.status_code == 200

    # Ensure price cache has been updated
    original_prices_update = INDEX_CACHE["prices_update"]
    assert original_prices_update > 0

    # 3. Add a test dummy to the cache to prove it is being used
    INDEX_CACHE["data"]["DUMMY_TICKER"] = {
        "price": 123.45,
        "prev": 123.45,
        "last_date": "2026-06-16"
    }

    # 4. Make a second normal request (should use price cache and preserve our dummy)
    print("Making second normal request...")
    response2 = client.get("/")
    assert response2.status_code == 200
    assert "DUMMY_TICKER" in INDEX_CACHE["data"]
    assert INDEX_CACHE["prices_update"] == original_prices_update

    # 5. Make a reload request (F5) with Cache-Control header
    # This resets prices_update to 0, triggering a price re-fetch
    print("Making reload (F5) request...")
    response3 = client.get("/", headers={"cache-control": "max-age=0"})
    assert response3.status_code == 200

    # After a reload, price cache is cleared, so DUMMY_TICKER disappears and prices_update is refreshed
    assert "DUMMY_TICKER" not in INDEX_CACHE["data"]
    assert INDEX_CACHE["prices_update"] > original_prices_update

    # 6. Test the Portfolio page cache bypass
    GLOBAL_CACHE["last_update"] = int(time.time())
    GLOBAL_CACHE["prices"]["DUMMY_PORTFOLIO"] = {"price": 99.9, "prev": 99.9}
    
    # Request portfolio page with Cache-Control F5 header
    print("Making portfolio reload (F5) request...")
    response4 = client.get("/portfolio", headers={"cache-control": "max-age=0"})
    assert response4.status_code == 200
    
    # GLOBAL_CACHE should be invalidated
    assert "DUMMY_PORTFOLIO" not in GLOBAL_CACHE["prices"]
    assert GLOBAL_CACHE["last_update"] != int(time.time()) - 1000  # should be updated to now

    print("All F5 cache bypass tests passed successfully!")

if __name__ == "__main__":
    test_f5_bypass()
