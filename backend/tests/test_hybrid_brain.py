"""
Backend tests for Hybrid Super Brain endpoints and robo/audit with include_brain.
Tests:
  - GET /api/hybrid-brain/state
  - POST /api/hybrid-brain/decide
  - GET /api/hybrid-brain/audit
  - GET /api/robo/audit?include_brain=true
  - POST /api/hybrid-brain/reset-daily
"""
import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ── 1) GET /api/hybrid-brain/state ───────────────────────────────────────────
class TestHybridBrainState:
    """Tests for GET /api/hybrid-brain/state"""

    def test_state_returns_200(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/state")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_state_has_fear_level(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/state")
        data = r.json()
        assert "fear_level" in data, f"Missing fear_level in {data}"
        assert 0.0 <= data["fear_level"] <= 1.0, f"fear_level out of range: {data['fear_level']}"

    def test_state_has_daily_target_pct(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/state")
        data = r.json()
        assert "daily_target_pct" in data, f"Missing daily_target_pct in {data}"
        assert isinstance(data["daily_target_pct"], (int, float))

    def test_state_has_consecutive_fail(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/state")
        data = r.json()
        assert "consecutive_fail" in data, f"Missing consecutive_fail in {data}"
        assert isinstance(data["consecutive_fail"], int)

    def test_state_has_current_pnl_pct(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/state")
        data = r.json()
        assert "current_pnl_pct" in data, f"Missing current_pnl_pct in {data}"
        assert isinstance(data["current_pnl_pct"], (int, float))

    def test_state_has_last_pnl_pct_and_grace_days(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/state")
        data = r.json()
        assert "last_pnl_pct" in data, f"Missing last_pnl_pct in {data}"
        assert "grace_days" in data, f"Missing grace_days in {data}"


# ── 2) POST /api/hybrid-brain/decide ─────────────────────────────────────────
class TestHybridBrainDecide:
    """Tests for POST /api/hybrid-brain/decide"""

    def test_decide_returns_200(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_decide_returns_action(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        data = r.json()
        assert "action" in data, f"Missing action in {data}"
        assert data["action"] in ("BUY", "SELL", "HOLD"), f"Unexpected action: {data['action']}"

    def test_decide_returns_confidence(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        data = r.json()
        assert "confidence" in data, f"Missing confidence in {data}"
        assert 0 <= data["confidence"] <= 100, f"confidence out of range: {data['confidence']}"

    def test_decide_returns_psych(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        data = r.json()
        assert "psych" in data, f"Missing psych in {data}"
        psych = data["psych"]
        assert "fomo_score" in psych, f"Missing fomo_score in psych: {psych}"
        assert "regime" in psych, f"Missing regime in psych: {psych}"
        assert "hidden_value_gap" in psych, f"Missing hidden_value_gap in psych: {psych}"

    def test_decide_returns_components(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        data = r.json()
        assert "components" in data, f"Missing components in {data}"
        assert isinstance(data["components"], dict), f"components is not dict"

    def test_decide_returns_survival(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        data = r.json()
        assert "survival" in data, f"Missing survival in {data}"

    def test_decide_returns_reasoning(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        data = r.json()
        assert "reasoning" in data, f"Missing reasoning in {data}"
        assert isinstance(data["reasoning"], str) and len(data["reasoning"]) > 0

    def test_decide_with_market_data(self, client):
        """Test decide with explicit market_data"""
        payload = {
            "symbol": "BANKNIFTY",
            "market_data": {
                "momentum_strength": 0.7,
                "volatility_index": 0.018,
                "volume_thrust": 1.3,
                "change_pct": 1.2
            }
        }
        r = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data["action"] in ("BUY", "SELL", "HOLD")
        assert data["symbol"] == "BANKNIFTY"

    def test_decide_caching_returns_cached_flag(self, client):
        """Second call within 60s should return cached=True"""
        client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "TESTCACHE"})
        r2 = client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "TESTCACHE"})
        data = r2.json()
        assert "cached" in data, f"Missing cached field: {data}"


# ── 3) GET /api/hybrid-brain/audit ───────────────────────────────────────────
class TestHybridBrainAudit:
    """Tests for GET /api/hybrid-brain/audit"""

    def test_audit_returns_200(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/audit")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_audit_has_decisions_array(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/audit")
        data = r.json()
        assert "decisions" in data, f"Missing decisions in {data}"
        assert isinstance(data["decisions"], list), f"decisions is not a list"

    def test_audit_has_count_field(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/audit")
        data = r.json()
        assert "count" in data, f"Missing count in {data}"

    def test_audit_limit_param(self, client):
        r = client.get(f"{BASE_URL}/api/hybrid-brain/audit?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert len(data["decisions"]) <= 5, f"Limit not respected: got {len(data['decisions'])}"

    def test_audit_decision_fields_after_decide(self, client):
        """After calling decide, audit should contain at least one decision"""
        # First fire a decision to ensure audit has at least 1 record
        client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        r = client.get(f"{BASE_URL}/api/hybrid-brain/audit?limit=10")
        data = r.json()
        assert data["count"] > 0, "Audit should have at least 1 decision after calling /decide"
        if data["decisions"]:
            d = data["decisions"][0]
            assert "action" in d, f"Missing action in decision: {d}"
            assert "confidence" in d, f"Missing confidence in decision: {d}"
            assert "symbol" in d, f"Missing symbol in decision: {d}"
            assert "timestamp" in d, f"Missing timestamp in decision: {d}"


# ── 4) GET /api/robo/audit?include_brain=true ─────────────────────────────────
class TestRoboAuditWithBrain:
    """Tests for GET /api/robo/audit with include_brain=true"""

    def test_robo_audit_returns_200(self, client):
        r = client.get(f"{BASE_URL}/api/robo/audit?include_brain=true")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_robo_audit_has_trades(self, client):
        r = client.get(f"{BASE_URL}/api/robo/audit?include_brain=true")
        data = r.json()
        assert "trades" in data, f"Missing trades in {data}"
        assert isinstance(data["trades"], list)

    def test_robo_audit_has_brain_decisions(self, client):
        r = client.get(f"{BASE_URL}/api/robo/audit?include_brain=true")
        data = r.json()
        assert "brain_decisions" in data, f"Missing brain_decisions in {data}"
        assert isinstance(data["brain_decisions"], list)

    def test_robo_audit_has_brain_count(self, client):
        r = client.get(f"{BASE_URL}/api/robo/audit?include_brain=true")
        data = r.json()
        assert "brain_count" in data, f"Missing brain_count in {data}"

    def test_robo_audit_brain_decisions_have_entry_type(self, client):
        """Brain decisions should have _entry_type = brain_decision"""
        # Fire a decide to ensure there's at least 1
        client.post(f"{BASE_URL}/api/hybrid-brain/decide", json={"symbol": "NIFTY"})
        r = client.get(f"{BASE_URL}/api/robo/audit?include_brain=true")
        data = r.json()
        if data["brain_decisions"]:
            bd = data["brain_decisions"][0]
            assert bd.get("_entry_type") == "brain_decision", f"Wrong entry_type: {bd.get('_entry_type')}"

    def test_robo_audit_without_brain_flag(self, client):
        """Default include_brain=True should also work"""
        r = client.get(f"{BASE_URL}/api/robo/audit")
        assert r.status_code == 200
        data = r.json()
        assert "brain_decisions" in data


# ── 5) POST /api/hybrid-brain/reset-daily ────────────────────────────────────
class TestHybridBrainResetDaily:
    """Tests for POST /api/hybrid-brain/reset-daily"""

    def test_reset_daily_returns_200(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/reset-daily")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_reset_daily_returns_state(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/reset-daily")
        data = r.json()
        # Should return the new state
        assert "fear_level" in data, f"Missing fear_level after reset: {data}"
        assert "current_pnl_pct" in data, f"Missing current_pnl_pct after reset: {data}"

    def test_reset_daily_sets_pnl_to_zero(self, client):
        r = client.post(f"{BASE_URL}/api/hybrid-brain/reset-daily")
        data = r.json()
        assert data.get("current_pnl_pct") == 0.0, f"Expected current_pnl_pct=0 after reset, got: {data.get('current_pnl_pct')}"
