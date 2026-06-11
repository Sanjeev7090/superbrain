"""
Robo Auto-Discover momentum scanner + watchlist + core robo endpoint tests.

Validates:
  • GET /api/robo/watchlist/discover - momentum stock scanner
  • GET /api/robo/watchlist/discover?refresh=true - cache bypass
  • GET /api/robo/settings, /status, /watchlist - core endpoints
  • POST /api/robo/watchlist - persistence
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://hybrid-brain-scan.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------------- Core robo endpoints ----------------
class TestRoboCoreEndpoints:
    def test_get_settings(self, client):
        r = client.get(f"{API}/robo/settings", timeout=30)
        assert r.status_code == 200, f"settings status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert isinstance(data, dict)
        # Look for some common settings fields
        assert "success" in data or "preferences" in data or "settings" in data or len(data) > 0

    def test_get_status(self, client):
        r = client.get(f"{API}/robo/status", timeout=30)
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert isinstance(data, dict)

    def test_get_watchlist(self, client):
        r = client.get(f"{API}/robo/watchlist", timeout=30)
        assert r.status_code == 200, f"watchlist status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert data.get("success") is True
        assert "watchlist" in data
        assert isinstance(data["watchlist"], list)
        assert "max_parallel_trades" in data
        assert isinstance(data["max_parallel_trades"], int)


# ---------------- Watchlist persistence ----------------
class TestWatchlistPersistence:
    def test_post_watchlist(self, client):
        payload = {"watchlist": ["RELIANCE.NS", "TCS.NS"], "max_parallel_trades": 3}
        r = client.post(f"{API}/robo/watchlist", json=payload, timeout=30)
        # rate limits may apply - retry once
        if r.status_code == 429:
            import time as _t; _t.sleep(2)
            r = client.post(f"{API}/robo/watchlist", json=payload, timeout=30)
        assert r.status_code == 200, f"POST watchlist status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert data.get("success") is True

    def test_get_after_post(self, client):
        r = client.get(f"{API}/robo/watchlist", timeout=30)
        assert r.status_code == 200
        data = r.json()
        wl = data.get("watchlist", [])
        # Should contain at least the tickers we posted
        assert "RELIANCE.NS" in wl or "TCS.NS" in wl, f"Watchlist did not persist tickers: {wl}"
        assert data.get("max_parallel_trades") == 3


# ---------------- Auto-Discover momentum scanner ----------------
class TestAutoDiscover:
    def test_discover_basic_structure(self, client):
        r = client.get(f"{API}/robo/watchlist/discover", timeout=120)
        assert r.status_code == 200, f"discover status={r.status_code} body={r.text[:300]}"
        data = r.json()
        assert data.get("success") is True
        assert "candidates" in data and isinstance(data["candidates"], list)
        assert "total_scanned" in data
        assert "scan_pool" in data
        assert isinstance(data["total_scanned"], int)
        assert isinstance(data["scan_pool"], int)

    def test_discover_candidate_fields(self, client):
        r = client.get(f"{API}/robo/watchlist/discover", timeout=120)
        assert r.status_code == 200
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            pytest.skip("No candidates returned (likely upstream price data unavailable).")
        for c in candidates[:3]:
            assert "ticker" in c, f"missing ticker in {c}"
            assert "score" in c, f"missing score in {c}"
            assert "direction" in c, f"missing direction in {c}"
            assert "price" in c, f"missing price in {c}"
            assert isinstance(c["score"], (int, float))
            assert 0 <= c["score"] <= 100, f"score out of range: {c['score']}"
            assert c["direction"] in ("BUY", "SELL", "HOLD"), f"invalid direction: {c['direction']}"
            # name and sector should be present
            assert "name" in c
            assert "sector" in c

    def test_discover_refresh_bypasses_cache(self, client):
        # First call to populate cache
        r1 = client.get(f"{API}/robo/watchlist/discover", timeout=120)
        assert r1.status_code == 200
        # Now refresh=true should bypass cache and return from_cache=False
        r2 = client.get(f"{API}/robo/watchlist/discover", params={"refresh": "true"}, timeout=180)
        assert r2.status_code == 200, f"refresh discover status={r2.status_code}"
        data = r2.json()
        assert data.get("success") is True
        assert data.get("from_cache") is False, f"Expected from_cache=False on refresh, got {data.get('from_cache')}"

    def test_discover_second_call_uses_cache(self, client):
        # Force a fresh scan first
        r1 = client.get(f"{API}/robo/watchlist/discover", params={"refresh": "true"}, timeout=180)
        assert r1.status_code == 200
        # Immediate second call (no refresh) should hit cache
        r2 = client.get(f"{API}/robo/watchlist/discover", timeout=30)
        assert r2.status_code == 200
        data = r2.json()
        assert data.get("from_cache") is True, f"Expected from_cache=True on second call, got {data.get('from_cache')}"
