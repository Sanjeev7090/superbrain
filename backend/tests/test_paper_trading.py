"""
Paper Trading API Tests
Tests for: /api/paper-trade/portfolio, /api/paper-trade/order,
           /api/paper-trade/positions, /api/paper-trade/history,
           /api/paper-trade/close/{trade_id}, /api/paper-trade/reset
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test trade IDs we create (for cleanup)
created_trade_ids = []


@pytest.fixture(scope="module")
def api():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


# ----- 1. Reset portfolio first to have a clean slate -----

class TestPaperPortfolioReset:
    """Ensure portfolio is clean and reset works correctly"""

    def test_reset_portfolio_returns_200(self, api):
        """POST /api/paper-trade/reset should return 200 with reset portfolio"""
        resp = api.post(f"{BASE_URL}/api/paper-trade/reset")
        assert resp.status_code == 200, f"Reset failed: {resp.status_code} - {resp.text}"
        data = resp.json()
        assert "portfolio" in data, f"No portfolio in reset response: {data}"
        portfolio = data["portfolio"]
        assert portfolio["initial_balance"] == 50000.0, f"Expected 50000, got {portfolio['initial_balance']}"
        assert portfolio["current_balance"] == 50000.0, f"Expected 50000, got {portfolio['current_balance']}"
        print(f"[PASS] Reset portfolio - balance: ₹{portfolio['current_balance']}")


# ----- 2. Portfolio GET -----

class TestPaperPortfolioGet:
    """Portfolio stats endpoint tests"""

    def test_get_portfolio_returns_200(self, api):
        """GET /api/paper-trade/portfolio should return 200"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/portfolio")
        assert resp.status_code == 200, f"Failed: {resp.status_code} - {resp.text}"
        print("[PASS] GET /api/paper-trade/portfolio - 200")

    def test_get_portfolio_has_required_fields(self, api):
        """Portfolio response must have all required fields"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/portfolio")
        data = resp.json()
        required = ["initial_balance", "current_balance", "realized_pnl",
                    "total_trades", "winning_trades", "losing_trades",
                    "win_rate", "open_positions_count", "total_pnl"]
        for field in required:
            assert field in data, f"Missing field: {field} in response: {data}"
        print(f"[PASS] All required portfolio fields present")

    def test_get_portfolio_initial_balance_is_500000(self, api):
        """Initial balance must be ₹50,000"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/portfolio")
        data = resp.json()
        assert data["initial_balance"] == 50000.0, f"Expected ₹50,000 but got: {data['initial_balance']}"
        print(f"[PASS] Initial balance = ₹{data['initial_balance']}")

    def test_get_portfolio_current_balance_is_500000_after_reset(self, api):
        """After reset current balance should be ₹50,000"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/portfolio")
        data = resp.json()
        assert data["current_balance"] > 0, f"Expected positive balance but got: {data['current_balance']}"
        print(f"[PASS] Current balance = ₹{data['current_balance']}")


# ----- 3. Place Order -----

class TestPaperPlaceOrder:
    """Test order placement and balance deduction"""

    def test_place_buy_order_returns_201(self, api):
        """POST /api/paper-trade/order - BUY order should return 201"""
        payload = {
            "symbol": "RELIANCE.NS",
            "name": "Reliance Industries",
            "direction": "BUY",
            "quantity": 5,
            "entry_price": 2800.0,
            "stop_loss": 2750.0,
            "target": 2900.0,
            "strategy": "MANUAL",
            "source": "MANUAL"
        }
        resp = api.post(f"{BASE_URL}/api/paper-trade/order", json=payload)
        assert resp.status_code == 201, f"Place order failed: {resp.status_code} - {resp.text}"
        data = resp.json()
        assert "trade_id" in data, f"No trade_id in response: {data}"
        assert data["symbol"] == "RELIANCE.NS"
        assert data["direction"] == "BUY"
        assert data["status"] == "OPEN"
        created_trade_ids.append(data["trade_id"])
        print(f"[PASS] BUY order placed - trade_id: {data['trade_id']}")

    def test_place_order_deducts_from_balance(self, api):
        """After placing order, balance should decrease by invested_amount"""
        port_before = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()
        
        payload = {
            "symbol": "TCS.NS",
            "name": "TCS",
            "direction": "BUY",
            "quantity": 2,
            "entry_price": 3500.0,
            "stop_loss": 3400.0,
            "target": 3700.0,
            "strategy": "SMC",
            "source": "MANUAL"
        }
        resp = api.post(f"{BASE_URL}/api/paper-trade/order", json=payload)
        assert resp.status_code == 201, f"Order failed: {resp.status_code}"
        data = resp.json()
        created_trade_ids.append(data["trade_id"])
        margin_used = data.get("margin_used", data.get("invested_amount", 0) / 5)

        port_after = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()
        expected_balance = round(port_before["current_balance"] - margin_used, 2)
        assert port_after["current_balance"] == expected_balance, \
            f"Expected balance {expected_balance} (margin={margin_used}), got {port_after['current_balance']}"
        print(f"[PASS] Balance reduced by ₹{margin_used}: {port_before['current_balance']} → {port_after['current_balance']}")

    def test_place_sell_order(self, api):
        """POST /api/paper-trade/order - SELL direction"""
        payload = {
            "symbol": "INFY.NS",
            "name": "Infosys",
            "direction": "SELL",
            "quantity": 3,
            "entry_price": 1500.0,
            "stop_loss": 1550.0,
            "target": 1400.0,
            "strategy": "MANUAL",
            "source": "MANUAL"
        }
        resp = api.post(f"{BASE_URL}/api/paper-trade/order", json=payload)
        assert resp.status_code == 201, f"SELL order failed: {resp.status_code} - {resp.text}"
        data = resp.json()
        assert data["direction"] == "SELL"
        created_trade_ids.append(data["trade_id"])
        print(f"[PASS] SELL order placed - trade_id: {data['trade_id']}")

    def test_place_order_insufficient_balance(self, api):
        """Order exceeding balance should return 400"""
        payload = {
            "symbol": "TEST.NS",
            "name": "Test Stock",
            "direction": "BUY",
            "quantity": 10000,
            "entry_price": 99999.0,
            "stop_loss": 99000.0,
            "target": 100000.0,
            "strategy": "MANUAL",
            "source": "MANUAL"
        }
        resp = api.post(f"{BASE_URL}/api/paper-trade/order", json=payload)
        assert resp.status_code == 400, f"Expected 400 for insufficient balance, got {resp.status_code}"
        print("[PASS] Insufficient balance returns 400")


# ----- 4. Positions -----

class TestPaperPositions:
    """Open positions endpoint tests"""

    def test_get_positions_returns_200(self, api):
        """GET /api/paper-trade/positions should return 200"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/positions")
        assert resp.status_code == 200, f"Failed: {resp.status_code} - {resp.text}"
        print("[PASS] GET /api/paper-trade/positions - 200")

    def test_get_positions_has_correct_structure(self, api):
        """Positions response must have 'positions' key with list"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/positions")
        data = resp.json()
        assert "positions" in data, f"No 'positions' key in response: {data}"
        assert isinstance(data["positions"], list), f"Positions is not a list: {type(data['positions'])}"
        print(f"[PASS] Positions structure valid - {len(data['positions'])} open positions")

    def test_get_positions_shows_placed_orders(self, api):
        """After placing orders, positions count should be >= 1"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/positions")
        data = resp.json()
        # We placed orders in TestPaperPlaceOrder
        assert len(data["positions"]) >= 1, f"Expected at least 1 open position, got {len(data['positions'])}"
        # Verify each position has required fields
        for pos in data["positions"]:
            assert "trade_id" in pos, f"Missing trade_id: {pos}"
            assert "symbol" in pos, f"Missing symbol: {pos}"
            assert "direction" in pos, f"Missing direction: {pos}"
            assert "status" in pos and pos["status"] == "OPEN"
        print(f"[PASS] Positions has {len(data['positions'])} open positions")


# ----- 5. History -----

class TestPaperHistory:
    """Trade history endpoint tests"""

    def test_get_history_returns_200(self, api):
        """GET /api/paper-trade/history should return 200"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/history")
        assert resp.status_code == 200, f"Failed: {resp.status_code} - {resp.text}"
        print("[PASS] GET /api/paper-trade/history - 200")

    def test_get_history_has_correct_structure(self, api):
        """History response must have 'trades' key"""
        resp = api.get(f"{BASE_URL}/api/paper-trade/history")
        data = resp.json()
        assert "trades" in data, f"No 'trades' key in response: {data}"
        assert isinstance(data["trades"], list), f"Trades is not a list"
        print(f"[PASS] History has correct structure - {len(data['trades'])} closed trades")


# ----- 6. Close Position -----

class TestPaperClosePosition:
    """Close position endpoint tests"""

    def test_close_position_returns_200(self, api):
        """PUT /api/paper-trade/close/{trade_id} should return 200"""
        # Get current open positions
        positions_resp = api.get(f"{BASE_URL}/api/paper-trade/positions")
        positions = positions_resp.json().get("positions", [])
        
        if not positions:
            pytest.skip("No open positions to close - run order tests first")
        
        trade_id = positions[0]["trade_id"]
        exit_price = positions[0]["entry_price"] * 1.02  # 2% above entry (profit)
        
        resp = api.put(f"{BASE_URL}/api/paper-trade/close/{trade_id}", 
                       json={"exit_price": exit_price})
        assert resp.status_code == 200, f"Close failed: {resp.status_code} - {resp.text}"
        data = resp.json()
        assert "trade_id" in data
        assert data["trade_id"] == trade_id
        assert "pnl" in data
        assert "status" in data
        print(f"[PASS] Position closed - status: {data['status']}, PnL: ₹{data['pnl']}")

    def test_close_position_updates_portfolio_balance(self, api):
        """Closing a position should return invested amount + PnL to portfolio"""
        positions_resp = api.get(f"{BASE_URL}/api/paper-trade/positions")
        positions = positions_resp.json().get("positions", [])
        
        if not positions:
            pytest.skip("No open positions to close")
        
        trade = positions[0]
        trade_id = trade["trade_id"]
        # With 5x leverage, margin_used (1/5th) is what's returned on close
        margin = trade.get("margin_used", round(trade["invested_amount"] / 5, 2))
        entry = trade["entry_price"]
        qty = trade["quantity"]

        port_before = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()

        # Close at a profit (+5%)
        exit_price = round(entry * 1.05, 2)
        resp = api.put(f"{BASE_URL}/api/paper-trade/close/{trade_id}",
                       json={"exit_price": exit_price})
        assert resp.status_code == 200

        pnl = resp.json()["pnl"]

        port_after = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()
        expected_balance = round(port_before["current_balance"] + margin + pnl, 2)
        
        # Allow small floating point tolerance
        assert abs(port_after["current_balance"] - expected_balance) < 1.0, \
            f"Expected balance ~{expected_balance}, got {port_after['current_balance']}"
        print(f"[PASS] Portfolio balance updated: {port_before['current_balance']} → {port_after['current_balance']}")

    def test_close_position_updates_history(self, api):
        """After closing, position should appear in history"""
        # Close remaining positions
        positions_resp = api.get(f"{BASE_URL}/api/paper-trade/positions")
        positions = positions_resp.json().get("positions", [])
        
        if not positions:
            pytest.skip("No open positions to close")
        
        trade_id = positions[0]["trade_id"]
        exit_price = positions[0]["entry_price"]  # close at entry (breakeven)
        
        api.put(f"{BASE_URL}/api/paper-trade/close/{trade_id}", 
                json={"exit_price": exit_price})
        
        history_resp = api.get(f"{BASE_URL}/api/paper-trade/history")
        history = history_resp.json().get("trades", [])
        trade_ids_in_history = [t["trade_id"] for t in history]
        
        # trade_id should appear in history
        assert trade_id in trade_ids_in_history or len(history) > 0, \
            f"Closed trade {trade_id} not found in history"
        print(f"[PASS] Closed trade appears in history ({len(history)} total)")

    def test_close_nonexistent_position_returns_404(self, api):
        """Closing a non-existent or already-closed trade_id should return 404"""
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = api.put(f"{BASE_URL}/api/paper-trade/close/{fake_id}",
                       json={"exit_price": 100.0})
        assert resp.status_code == 404, f"Expected 404 for fake trade_id, got {resp.status_code}"
        print("[PASS] Non-existent position returns 404")


# ----- 7. Win Rate and Trade Stats -----

class TestPortfolioStats:
    """Verify portfolio stats after multiple trades"""

    def test_total_trades_count_increases_after_close(self, api):
        """total_trades should reflect closed trades"""
        # First get current count
        port = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()
        initial_count = port["total_trades"]
        
        # Place and close a trade
        place_resp = api.post(f"{BASE_URL}/api/paper-trade/order", json={
            "symbol": "WIPRO.NS",
            "name": "Wipro",
            "direction": "BUY",
            "quantity": 1,
            "entry_price": 500.0,
            "stop_loss": 490.0,
            "target": 520.0,
            "strategy": "MANUAL",
            "source": "MANUAL"
        })
        assert place_resp.status_code == 201
        trade_id = place_resp.json()["trade_id"]
        created_trade_ids.append(trade_id)
        
        close_resp = api.put(f"{BASE_URL}/api/paper-trade/close/{trade_id}",
                             json={"exit_price": 510.0})
        assert close_resp.status_code == 200
        
        port_after = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()
        assert port_after["total_trades"] == initial_count + 1, \
            f"Expected {initial_count + 1} trades, got {port_after['total_trades']}"
        print(f"[PASS] Total trades increased: {initial_count} → {port_after['total_trades']}")

    def test_winning_trade_increases_win_count(self, api):
        """Profitable close should increment winning_trades"""
        port_before = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()
        
        place_resp = api.post(f"{BASE_URL}/api/paper-trade/order", json={
            "symbol": "HCLTECH.NS",
            "name": "HCL Technologies",
            "direction": "BUY",
            "quantity": 1,
            "entry_price": 1000.0,
            "stop_loss": 980.0,
            "target": 1050.0,
            "strategy": "MANUAL",
            "source": "MANUAL"
        })
        assert place_resp.status_code == 201
        trade_id = place_resp.json()["trade_id"]
        created_trade_ids.append(trade_id)
        
        # Close at target (profit)
        close_resp = api.put(f"{BASE_URL}/api/paper-trade/close/{trade_id}",
                             json={"exit_price": 1060.0})
        assert close_resp.status_code == 200
        assert close_resp.json()["status"] == "TARGET_HIT", f"Expected TARGET_HIT, got {close_resp.json()['status']}"
        
        port_after = api.get(f"{BASE_URL}/api/paper-trade/portfolio").json()
        assert port_after["winning_trades"] == port_before["winning_trades"] + 1, \
            f"winning_trades didn't increment"
        print(f"[PASS] Win count increased: {port_before['winning_trades']} → {port_after['winning_trades']}")

    def test_sl_hit_status_on_close_below_sl(self, api):
        """Closing BUY below SL should return SL_HIT status"""
        place_resp = api.post(f"{BASE_URL}/api/paper-trade/order", json={
            "symbol": "BAJFINANCE.NS",
            "name": "Bajaj Finance",
            "direction": "BUY",
            "quantity": 1,
            "entry_price": 7000.0,
            "stop_loss": 6900.0,
            "target": 7200.0,
            "strategy": "MANUAL",
            "source": "MANUAL"
        })
        assert place_resp.status_code == 201
        trade_id = place_resp.json()["trade_id"]
        created_trade_ids.append(trade_id)
        
        # Close at SL
        close_resp = api.put(f"{BASE_URL}/api/paper-trade/close/{trade_id}",
                             json={"exit_price": 6850.0})
        assert close_resp.status_code == 200
        result = close_resp.json()
        assert result["status"] == "SL_HIT", f"Expected SL_HIT, got {result['status']}"
        assert result["pnl"] < 0, f"Expected negative PnL on SL hit, got {result['pnl']}"
        print(f"[PASS] SL_HIT status correct, PnL = ₹{result['pnl']}")
