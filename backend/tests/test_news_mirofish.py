"""Backend tests for News and MiroFish Swarm Intelligence endpoints"""
import os
import pytest
import requests
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://kronos-order-flow.preview.emergentagent.com').rstrip('/')


@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _make_bars(n=60, start=2900.0):
    """Generate synthetic OHLCV bars for testing."""
    bars = []
    price = start
    for i in range(n):
        open_p = price
        high = price * 1.01
        low = price * 0.99
        close = price * (1.002 if i % 3 else 0.998)
        bars.append({
            "time": f"2025-01-{(i % 28) + 1:02d}",
            "open": round(open_p, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": 1000000 + i * 1000,
        })
        price = close
    return bars


# ----------------- NEWS endpoint -----------------

class TestNewsEndpoint:
    def test_get_news_reliance(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/news/RELIANCE.NS", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ticker"] == "RELIANCE.NS"
        assert "news" in data and isinstance(data["news"], list)
        assert "count" in data
        # If news returned, validate schema
        if data["count"] > 0:
            item = data["news"][0]
            for key in ["title", "summary", "source", "url", "published", "image"]:
                assert key in item, f"Missing key {key}"
            assert isinstance(item["title"], str)

    def test_get_news_tcs(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/news/TCS.NS", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["ticker"] == "TCS.NS"
        assert isinstance(data["news"], list)

    def test_get_news_invalid_ticker(self, api_client):
        # Should not error, just return empty/few items
        r = api_client.get(f"{BASE_URL}/api/news/ZZZZINVALID.NS", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "news" in data
        assert isinstance(data["news"], list)


# ----------------- MiroFish Swarm Intelligence -----------------

class TestMiroFishAnalyze:
    def test_min_bars_required(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/mirofish/analyze", json={
            "ticker": "RELIANCE.NS",
            "bars": _make_bars(5),
            "timeframe": "1D",
        }, timeout=60)
        assert r.status_code == 400
        assert "10 bars" in r.json().get("detail", "")

    def test_mirofish_returns_swarm_consensus(self, api_client):
        bars = _make_bars(60)
        r = api_client.post(f"{BASE_URL}/api/mirofish/analyze", json={
            "ticker": "RELIANCE.NS",
            "bars": bars,
            "timeframe": "1D",
        }, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()

        # Top-level keys
        for key in [
            "status", "signal_type", "swarm_consensus", "consensus_score",
            "direction", "entry_price", "stop_loss", "targets", "risk_reward",
            "news_sentiment", "news_summary", "agents", "confidence", "recommendation"
        ]:
            assert key in data, f"Missing key {key}"

        # signal_type validation
        assert data["signal_type"] in ["BUY", "SELL", "WAIT"]
        assert data["swarm_consensus"] in ["BULLISH", "BEARISH", "NEUTRAL"]
        assert data["news_sentiment"] in ["POSITIVE", "NEGATIVE", "NEUTRAL"]

        # Agents = 5
        assert isinstance(data["agents"], list)
        assert len(data["agents"]) == 5, f"Expected 5 agents, got {len(data['agents'])}"

        expected_roles = {"momentum", "news_sentiment", "contrarian", "risk_mgmt", "pattern"}
        actual_roles = {a["role"] for a in data["agents"]}
        assert expected_roles.issubset(actual_roles), f"Missing roles: {expected_roles - actual_roles}"

        # Each agent has required fields
        for a in data["agents"]:
            assert a["verdict"] in ["BUY", "SELL", "HOLD"]
            assert isinstance(a["agent_name"], str) and a["agent_name"]
            assert isinstance(a["reasoning"], str)
            assert 0 <= a["confidence"] <= 100

        # Targets is a list
        assert isinstance(data["targets"], list)

        # Numeric fields
        assert isinstance(data["consensus_score"], (int, float))
        assert isinstance(data["confidence"], int)
