"""
Tests for Robot 3.0 — Multi-stock parallel trading and Kronos stale data fix.
Covers:
  - GET /api/robo/watchlist
  - POST /api/robo/watchlist
  - GET /api/robo/settings (watchlist + max_parallel_trades in preferences)
  - POST /api/robo/settings (accepts watchlist + max_parallel_trades)
  - Execution engine max positions enforcement
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ─── GET /api/robo/watchlist ──────────────────────────────────────────────────
class TestGetWatchlist:
    """GET /api/robo/watchlist — returns watchlist array and max_parallel_trades"""

    def test_watchlist_returns_200(self, api):
        r = api.get(f"{BASE_URL}/api/robo/watchlist")
        assert r.status_code == 200

    def test_watchlist_response_has_success_true(self, api):
        r = api.get(f"{BASE_URL}/api/robo/watchlist")
        data = r.json()
        assert data.get("success") is True

    def test_watchlist_contains_watchlist_array(self, api):
        r = api.get(f"{BASE_URL}/api/robo/watchlist")
        data = r.json()
        assert "watchlist" in data
        assert isinstance(data["watchlist"], list)

    def test_watchlist_contains_max_parallel_trades(self, api):
        r = api.get(f"{BASE_URL}/api/robo/watchlist")
        data = r.json()
        assert "max_parallel_trades" in data
        assert isinstance(data["max_parallel_trades"], int)

    def test_watchlist_max_parallel_trades_in_range(self, api):
        r = api.get(f"{BASE_URL}/api/robo/watchlist")
        data = r.json()
        assert 1 <= data["max_parallel_trades"] <= 5

    def test_watchlist_contains_primary_ticker(self, api):
        r = api.get(f"{BASE_URL}/api/robo/watchlist")
        data = r.json()
        assert "primary_ticker" in data
        assert isinstance(data["primary_ticker"], str)


# ─── POST /api/robo/watchlist ─────────────────────────────────────────────────
class TestPostWatchlist:
    """POST /api/robo/watchlist — updates watchlist with tickers and max_parallel_trades"""

    def test_post_watchlist_3_tickers_returns_200(self, api):
        payload = {
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
            "max_parallel_trades": 3
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        assert r.status_code == 200

    def test_post_watchlist_updates_tickers(self, api):
        payload = {
            "watchlist": ["HDFC.NS", "WIPRO.NS", "HDFCBANK.NS"],
            "max_parallel_trades": 3
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        data = r.json()
        assert data.get("success") is True
        # Verify watchlist has the tickers
        assert "watchlist" in data
        assert isinstance(data["watchlist"], list)
        # Backend appends .NS so check with normalization
        for ticker in ["HDFC.NS", "WIPRO.NS", "HDFCBANK.NS"]:
            assert ticker in data["watchlist"], f"{ticker} not in watchlist {data['watchlist']}"

    def test_post_watchlist_auto_appends_ns_suffix(self, api):
        """Tickers without .NS should get .NS appended automatically"""
        payload = {
            "watchlist": ["RELIANCE", "TCS", "INFY"],
            "max_parallel_trades": 3
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        data = r.json()
        assert data.get("success") is True
        for ticker in ["RELIANCE.NS", "TCS.NS", "INFY.NS"]:
            assert ticker in data["watchlist"], f"{ticker} not found after .NS auto-append"

    def test_post_watchlist_max_parallel_3(self, api):
        payload = {
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
            "max_parallel_trades": 3
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        data = r.json()
        assert data.get("max_parallel_trades") == 3

    def test_post_watchlist_max_parallel_4(self, api):
        """max_parallel_trades=4 should be accepted (within 1–5 range)"""
        payload = {
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS"],
            "max_parallel_trades": 4
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        assert data.get("max_parallel_trades") == 4

    def test_post_watchlist_max_parallel_5(self, api):
        """max_parallel_trades=5 should be accepted (maximum limit)"""
        payload = {
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "WIPRO.NS"],
            "max_parallel_trades": 5
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        assert data.get("max_parallel_trades") == 5

    def test_post_watchlist_max_parallel_1(self, api):
        """max_parallel_trades=1 should be accepted (minimum limit)"""
        payload = {
            "watchlist": ["RELIANCE.NS"],
            "max_parallel_trades": 1
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data.get("max_parallel_trades") == 1

    def test_post_watchlist_max_parallel_over_5_rejected(self, api):
        """max_parallel_trades=6 should be rejected (exceeds max)"""
        payload = {
            "watchlist": ["RELIANCE.NS"],
            "max_parallel_trades": 6
        }
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json=payload)
        assert r.status_code in (400, 422), f"Expected 400/422, got {r.status_code}"

    def test_post_watchlist_get_reflects_update(self, api):
        """After POST, GET should reflect the updated watchlist"""
        tickers = ["BAJAJFINANCE.NS", "ICICIBANK.NS", "SBIN.NS"]
        post_r = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": tickers,
            "max_parallel_trades": 3
        })
        assert post_r.status_code == 200

        get_r = api.get(f"{BASE_URL}/api/robo/watchlist")
        get_data = get_r.json()
        for t in tickers:
            assert t in get_data["watchlist"], f"{t} not reflected in GET after POST"

    def test_post_watchlist_message_field(self, api):
        """Response should contain a message field"""
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
            "max_parallel_trades": 3
        })
        data = r.json()
        assert "message" in data


# ─── GET /api/robo/settings ───────────────────────────────────────────────────
class TestGetSettings:
    """GET /api/robo/settings — preferences object must include watchlist and max_parallel_trades"""

    def test_settings_returns_200(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        assert r.status_code == 200

    def test_settings_has_preferences_key(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        data = r.json()
        assert "preferences" in data

    def test_settings_preferences_has_watchlist(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        prefs = r.json().get("preferences", {})
        assert "watchlist" in prefs, "preferences.watchlist is missing from GET /settings"
        assert isinstance(prefs["watchlist"], list)

    def test_settings_preferences_has_max_parallel_trades(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        prefs = r.json().get("preferences", {})
        assert "max_parallel_trades" in prefs, "preferences.max_parallel_trades is missing from GET /settings"
        assert isinstance(prefs["max_parallel_trades"], int)

    def test_settings_watchlist_reflects_post_watchlist(self, api):
        """After POST /watchlist, GET /settings should reflect same watchlist"""
        tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS"]
        api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": tickers,
            "max_parallel_trades": 3
        })
        r = api.get(f"{BASE_URL}/api/robo/settings")
        prefs = r.json().get("preferences", {})
        for t in tickers:
            assert t in prefs["watchlist"], f"{t} not in preferences.watchlist after POST /watchlist"

    def test_settings_max_parallel_reflects_post_watchlist(self, api):
        """After POST /watchlist with max=4, GET /settings preferences.max_parallel_trades should be 4"""
        api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "WIPRO.NS"],
            "max_parallel_trades": 4
        })
        r = api.get(f"{BASE_URL}/api/robo/settings")
        prefs = r.json().get("preferences", {})
        assert prefs["max_parallel_trades"] == 4

    def test_settings_has_risk_profile(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        data = r.json()
        assert "risk_profile" in data

    def test_settings_has_capital_state_vector(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        data = r.json()
        assert "capital_state_vector" in data


# ─── POST /api/robo/settings ──────────────────────────────────────────────────
class TestPostSettings:
    """POST /api/robo/settings — accepts watchlist and max_parallel_trades fields"""

    def test_post_settings_returns_200(self, api):
        r = api.post(f"{BASE_URL}/api/robo/settings", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000
        })
        assert r.status_code == 200

    def test_post_settings_with_watchlist_returns_200(self, api):
        r = api.post(f"{BASE_URL}/api/robo/settings", json={
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
            "max_parallel_trades": 3
        })
        assert r.status_code == 200

    def test_post_settings_watchlist_persisted_in_preferences(self, api):
        """After POST /settings with watchlist, preferences.watchlist should reflect it"""
        tickers_to_set = ["HDFCBANK.NS", "KOTAKBANK.NS"]
        r = api.post(f"{BASE_URL}/api/robo/settings", json={
            "watchlist": tickers_to_set,
            "max_parallel_trades": 2
        })
        assert r.status_code == 200
        data = r.json()
        # Check returned preferences
        prefs = data.get("preferences", {})
        for t in tickers_to_set:
            assert t in prefs.get("watchlist", []), f"{t} not in returned preferences.watchlist"

    def test_post_settings_max_parallel_persisted(self, api):
        """After POST /settings with max_parallel_trades=4, preferences should reflect it"""
        r = api.post(f"{BASE_URL}/api/robo/settings", json={
            "max_parallel_trades": 4
        })
        assert r.status_code == 200
        data = r.json()
        prefs = data.get("preferences", {})
        assert prefs.get("max_parallel_trades") == 4

    def test_post_settings_has_success_true(self, api):
        r = api.post(f"{BASE_URL}/api/robo/settings", json={
            "daily_profit_target": 1000
        })
        data = r.json()
        assert data.get("success") is True


# ─── Execution Engine max positions via POST /watchlist ───────────────────────
class TestExecutionEngineMaxPositions:
    """
    POST /api/robo/watchlist with max_parallel_trades=4 should configure
    the execution engine to allow up to 4 concurrent positions.
    Verified via GET /api/robo/loop-status exec_stats.
    """

    def test_set_max_parallel_4_accepted(self, api):
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "WIPRO.NS"],
            "max_parallel_trades": 4
        })
        assert r.status_code == 200
        assert r.json().get("max_parallel_trades") == 4

    def test_loop_status_has_exec_stats(self, api):
        """Loop status should have exec_stats with open_positions count"""
        r = api.get(f"{BASE_URL}/api/robo/loop-status")
        assert r.status_code == 200
        data = r.json()
        # exec_stats may be present
        if data.get("exec_stats"):
            es = data["exec_stats"]
            assert "open_positions" in es
            assert "mode" in es

    def test_positions_endpoint_after_max_4_config(self, api):
        """GET /positions should still work after configuring max 4"""
        api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "WIPRO.NS"],
            "max_parallel_trades": 4
        })
        r = api.get(f"{BASE_URL}/api/robo/positions")
        assert r.status_code == 200
        data = r.json()
        assert "open_positions" in data

    def test_settings_max_parallel_is_synced_after_watchlist_update(self, api):
        """After POST /watchlist with max=4, GET /settings must reflect max=4"""
        api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "WIPRO.NS"],
            "max_parallel_trades": 4
        })
        settings = api.get(f"{BASE_URL}/api/robo/settings").json()
        assert settings["preferences"]["max_parallel_trades"] == 4

    def test_reset_max_parallel_to_3(self, api):
        """Cleanup: reset to 3 parallel trades"""
        import time
        time.sleep(2)  # avoid rate-limit after rapid test sequence
        r = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
            "max_parallel_trades": 3
        })
        assert r.status_code in (200, 429), f"Unexpected status {r.status_code}"
        if r.status_code == 200:
            assert r.json().get("max_parallel_trades") == 3
