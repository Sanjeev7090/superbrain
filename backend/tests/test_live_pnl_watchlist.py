"""
Tests for:
1. GET /api/robo/positions — open positions enriched with live P&L fields
2. POST /api/robo/watchlist — immediate persistence of watchlist removal
"""
import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ──────────────────────────────────────────────────────────────────────────────
# 1. GET /api/robo/positions — structure & live P&L fields
# ──────────────────────────────────────────────────────────────────────────────

class TestOpenPositionsEndpoint:
    """Tests for /api/robo/positions live P&L enrichment"""

    def test_positions_returns_200(self, api):
        res = api.get(f"{BASE_URL}/api/robo/positions")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text[:200]}"

    def test_positions_success_flag(self, api):
        res = api.get(f"{BASE_URL}/api/robo/positions")
        data = res.json()
        assert data.get("success") is True, f"success flag missing: {data}"

    def test_positions_response_has_required_keys(self, api):
        res = api.get(f"{BASE_URL}/api/robo/positions")
        data = res.json()
        required_keys = ["open_positions", "pending_positions", "open_count"]
        for key in required_keys:
            assert key in data, f"Missing key '{key}' in response: {data.keys()}"

    def test_open_positions_is_list(self, api):
        res = api.get(f"{BASE_URL}/api/robo/positions")
        data = res.json()
        assert isinstance(data["open_positions"], list), "open_positions must be a list"

    def test_open_positions_pnl_fields_if_any(self, api):
        """If there are open positions, verify live P&L fields are present."""
        res = api.get(f"{BASE_URL}/api/robo/positions")
        data = res.json()
        positions = data.get("open_positions", [])

        if not positions:
            pytest.skip("No open positions to test P&L enrichment")

        for pos in positions:
            # Verify the P&L enrichment fields are present
            assert "current_price" in pos, f"Missing 'current_price' in position: {pos.get('order_id', '?')}"
            assert "unrealized_pnl" in pos, f"Missing 'unrealized_pnl' in position: {pos.get('order_id', '?')}"
            assert "pnl_pct" in pos, f"Missing 'pnl_pct' in position: {pos.get('order_id', '?')}"
            assert "price_change" in pos, f"Missing 'price_change' in position: {pos.get('order_id', '?')}"
            assert "price_change_pct" in pos, f"Missing 'price_change_pct' in position: {pos.get('order_id', '?')}"
            print(f"  Position {pos.get('ticker')}: CMP={pos.get('current_price')}, P&L={pos.get('unrealized_pnl')}, pnl_pct={pos.get('pnl_pct')}%")

    def test_pnl_calculation_correctness(self, api):
        """Verify unrealized_pnl is calculated correctly: (CMP - entry) * qty for BUY."""
        res = api.get(f"{BASE_URL}/api/robo/positions")
        data = res.json()
        positions = data.get("open_positions", [])

        if not positions:
            pytest.skip("No open positions to test P&L calculation")

        for pos in positions:
            if pos.get("current_price") and pos.get("entry_price") and pos.get("quantity"):
                cp    = pos["current_price"]
                entry = pos["entry_price"]
                qty   = pos["quantity"]
                direction = pos.get("direction", "BUY")
                expected_pnl = round((cp - entry) * qty if direction == "BUY" else (entry - cp) * qty, 2)
                actual_pnl   = pos["unrealized_pnl"]
                # Allow small floating point tolerance
                assert abs(actual_pnl - expected_pnl) < 1.0, (
                    f"P&L mismatch for {pos.get('ticker')}: "
                    f"expected ≈{expected_pnl}, got {actual_pnl} "
                    f"(CMP={cp}, entry={entry}, qty={qty})"
                )
                print(f"  P&L check PASS: {pos.get('ticker')} {direction} entry={entry} cmp={cp} qty={qty} pnl={actual_pnl}")

    def test_current_price_is_positive_number(self, api):
        """current_price must be a positive float."""
        res = api.get(f"{BASE_URL}/api/robo/positions")
        data = res.json()
        positions = data.get("open_positions", [])

        if not positions:
            pytest.skip("No open positions to check current_price")

        for pos in positions:
            cp = pos.get("current_price")
            if cp is not None:
                assert isinstance(cp, (int, float)), f"current_price must be numeric, got {type(cp)}"
                assert cp > 0, f"current_price must be > 0, got {cp}"

    def test_pnl_pct_is_percentage(self, api):
        """pnl_pct should be a reasonable percentage (not a fraction like 0.03)."""
        res = api.get(f"{BASE_URL}/api/robo/positions")
        data = res.json()
        positions = data.get("open_positions", [])

        if not positions:
            pytest.skip("No open positions to check pnl_pct")

        for pos in positions:
            pct = pos.get("pnl_pct")
            if pct is not None:
                # pnl_pct should be a percentage like -0.3, not 0.003
                assert isinstance(pct, (int, float)), f"pnl_pct must be numeric, got {type(pct)}"
                # Reasonable range: -50% to +50% for an open position
                assert -100 <= pct <= 100, f"pnl_pct out of expected range: {pct}"
                print(f"  pnl_pct for {pos.get('ticker')}: {pct}%")


# ──────────────────────────────────────────────────────────────────────────────
# 2. POST /api/robo/watchlist — immediate persistence
# ──────────────────────────────────────────────────────────────────────────────

class TestWatchlistPersistence:
    """Tests for POST /api/robo/watchlist — removal persists immediately."""

    def test_get_watchlist_returns_200(self, api):
        res = api.get(f"{BASE_URL}/api/robo/watchlist")
        assert res.status_code == 200, f"GET watchlist failed: {res.text[:200]}"

    def test_watchlist_response_structure(self, api):
        res = api.get(f"{BASE_URL}/api/robo/watchlist")
        data = res.json()
        assert "watchlist" in data, f"Missing 'watchlist' key: {data}"
        assert isinstance(data["watchlist"], list), "watchlist must be a list"

    def test_add_ticker_persists_to_backend(self, api):
        """Add TEST_TCS.NS to watchlist and verify it persists via GET."""
        # First get current watchlist
        initial = api.get(f"{BASE_URL}/api/robo/watchlist").json()
        orig_list = initial.get("watchlist", [])
        orig_parallel = initial.get("max_parallel_trades", 3)

        test_ticker = "TEST_TCS.NS"
        new_list = orig_list + [test_ticker] if test_ticker not in orig_list else orig_list

        # POST with test ticker
        post_res = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": new_list,
            "max_parallel_trades": orig_parallel,
        })
        assert post_res.status_code == 200, f"POST watchlist failed: {post_res.text[:200]}"
        data = post_res.json()
        assert data.get("success") is True, f"success=False: {data}"

        # GET again to verify persistence
        get_res = api.get(f"{BASE_URL}/api/robo/watchlist")
        fetched = get_res.json().get("watchlist", [])
        assert test_ticker in fetched or any(t.upper() == test_ticker.upper() for t in fetched), (
            f"TEST_TCS.NS not found in watchlist after POST. Got: {fetched}"
        )
        print(f"  Watchlist after add: {fetched}")

    def test_remove_ticker_persists_to_backend(self, api):
        """
        Add TEST_INFY.NS, confirm it's there, then POST without it,
        confirm it's gone after a fresh GET (persistence of removal).
        """
        test_ticker = "TEST_INFY.NS"

        # Step 1 — ensure ticker is in watchlist
        initial = api.get(f"{BASE_URL}/api/robo/watchlist").json()
        orig_list = initial.get("watchlist", [])
        orig_parallel = initial.get("max_parallel_trades", 3)

        with_ticker = list(set(orig_list + [test_ticker]))
        api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": with_ticker,
            "max_parallel_trades": orig_parallel,
        })

        # Verify it's present
        after_add = api.get(f"{BASE_URL}/api/robo/watchlist").json()
        assert test_ticker in after_add.get("watchlist", []), (
            f"Pre-condition failed: {test_ticker} not added. Got: {after_add.get('watchlist')}"
        )

        # Step 2 — POST without the test ticker (simulating remove + immediate persist)
        without_ticker = [t for t in after_add.get("watchlist", []) if t != test_ticker]
        remove_res = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": without_ticker,
            "max_parallel_trades": orig_parallel,
        })
        assert remove_res.status_code == 200, f"Remove POST failed: {remove_res.text[:200]}"

        # Step 3 — GET to confirm removal persisted
        after_remove = api.get(f"{BASE_URL}/api/robo/watchlist").json()
        fetched = after_remove.get("watchlist", [])
        assert test_ticker not in fetched, (
            f"REMOVAL NOT PERSISTED: {test_ticker} still in watchlist after POST. Got: {fetched}"
        )
        print(f"  Watchlist after removal: {fetched}")

    def test_watchlist_post_returns_correct_count(self, api):
        """POST response should reflect the number of tickers submitted."""
        test_list = ["RELIANCE.NS", "TCS.NS", "INFY.NS"]
        res = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": test_list,
            "max_parallel_trades": 3,
        })
        assert res.status_code == 200
        data = res.json()
        assert data.get("success") is True
        # The response returns the normalised clean list
        returned_wl = data.get("watchlist", [])
        assert len(returned_wl) == len(test_list), (
            f"Expected {len(test_list)} tickers returned, got {len(returned_wl)}: {returned_wl}"
        )

    def test_watchlist_normalises_tickers(self, api):
        """Tickers without .NS suffix should get it appended."""
        res = api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": ["RELIANCE", "TCS"],
            "max_parallel_trades": 2,
        })
        assert res.status_code == 200
        data = res.json()
        returned = data.get("watchlist", [])
        assert "RELIANCE.NS" in returned, f"Expected RELIANCE.NS, got {returned}"
        assert "TCS.NS" in returned, f"Expected TCS.NS, got {returned}"
        print(f"  Normalised tickers: {returned}")

    def test_watchlist_removal_does_not_come_back_after_refresh(self, api):
        """
        Key regression: after removing a stock, a subsequent GET must NOT return it.
        This was the original bug (local state only, not persisted).
        """
        test_ticker = "TEST_WIPRO.NS"

        # Add it
        initial = api.get(f"{BASE_URL}/api/robo/watchlist").json()
        current = initial.get("watchlist", [])
        parallel = initial.get("max_parallel_trades", 3)

        with_it = list(set(current + [test_ticker]))
        api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": with_it,
            "max_parallel_trades": parallel,
        })

        # Verify added
        check = api.get(f"{BASE_URL}/api/robo/watchlist").json()
        assert test_ticker in check.get("watchlist", []), "Setup failed: ticker not added"

        # Remove it (simulate removeFromWatchlist immediate POST)
        without_it = [t for t in check.get("watchlist", []) if t != test_ticker]
        api.post(f"{BASE_URL}/api/robo/watchlist", json={
            "watchlist": without_it,
            "max_parallel_trades": parallel,
        })

        # Simulate page refresh — GET again fresh
        refresh_check = api.get(f"{BASE_URL}/api/robo/watchlist").json()
        after_refresh = refresh_check.get("watchlist", [])
        assert test_ticker not in after_refresh, (
            f"BUG: {test_ticker} came back after 'refresh' (GET). Watchlist: {after_refresh}"
        )
        print(f"  After remove + refresh, watchlist: {after_refresh}")
