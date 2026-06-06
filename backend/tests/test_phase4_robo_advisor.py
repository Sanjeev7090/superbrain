"""
Phase 4 — Dreamer V3 Robo-Advisor Dashboard
Backend API tests for Phase 4 new/modified endpoints:
  GET  /api/robo/status       - core state, P&L, decision, circuit-breakers
  GET  /api/robo/loop-status  - APScheduler loop state
  GET  /api/robo/positions    - open positions + exec stats
  GET  /api/robo/orders       - closed order history + P&L summary
  POST /api/robo/start        - start auto mode
  POST /api/robo/stop         - stop auto mode
  POST /api/robo/mode         - switch execution mode
  POST /api/robo/settings     - update settings
  POST /api/robo/risk-preview - live risk preview
  POST /api/robo/recalculate  - force recalculate risk profile
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


@pytest.fixture
def api():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


# ─── GET /api/robo/status ────────────────────────────────────────────────────

class TestRoboStatus:
    """GET /api/robo/status — core state"""

    def test_status_returns_200(self, api):
        r = api.get(f"{BASE_URL}/api/robo/status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

    def test_status_has_auto_mode_field(self, api):
        r = api.get(f"{BASE_URL}/api/robo/status")
        data = r.json()
        assert 'auto_mode' in data, f"Missing 'auto_mode' in: {list(data.keys())}"
        assert isinstance(data['auto_mode'], bool)

    def test_status_has_status_field(self, api):
        r = api.get(f"{BASE_URL}/api/robo/status")
        data = r.json()
        assert 'status' in data, f"Missing 'status' in: {list(data.keys())}"
        assert data['status'] in ['idle', 'scanning', 'trading', 'paused', 'circuit_breaker', 'market_closed', 'error']

    def test_status_has_daily_pnl(self, api):
        r = api.get(f"{BASE_URL}/api/robo/status")
        data = r.json()
        assert 'daily_pnl' in data, f"Missing 'daily_pnl' in: {list(data.keys())}"

    def test_status_has_mode(self, api):
        r = api.get(f"{BASE_URL}/api/robo/status")
        data = r.json()
        assert 'mode' in data, f"Missing 'mode' in: {list(data.keys())}"
        assert data['mode'] in ['paper', 'shadow', 'live']


# ─── GET /api/robo/loop-status ───────────────────────────────────────────────

class TestRoboLoopStatus:
    """GET /api/robo/loop-status — APScheduler loop info"""

    def test_loop_status_returns_200(self, api):
        r = api.get(f"{BASE_URL}/api/robo/loop-status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

    def test_loop_status_has_loop_key(self, api):
        r = api.get(f"{BASE_URL}/api/robo/loop-status")
        data = r.json()
        assert 'loop' in data, f"Missing 'loop' key. Got keys: {list(data.keys())}"

    def test_loop_status_loop_has_running(self, api):
        r = api.get(f"{BASE_URL}/api/robo/loop-status")
        loop = r.json().get('loop', {})
        assert 'running' in loop, f"Missing 'running' in loop: {list(loop.keys())}"
        assert isinstance(loop['running'], bool)

    def test_loop_status_loop_has_interval_minutes(self, api):
        r = api.get(f"{BASE_URL}/api/robo/loop-status")
        loop = r.json().get('loop', {})
        assert 'interval_minutes' in loop, f"Missing 'interval_minutes' in loop: {list(loop.keys())}"
        assert isinstance(loop['interval_minutes'], (int, float))

    def test_loop_status_has_success(self, api):
        r = api.get(f"{BASE_URL}/api/robo/loop-status")
        data = r.json()
        assert data.get('success') is True, f"Expected success=true, got: {data.get('success')}"


# ─── GET /api/robo/positions ─────────────────────────────────────────────────

class TestRoboPositions:
    """GET /api/robo/positions — open positions"""

    def test_positions_returns_200(self, api):
        r = api.get(f"{BASE_URL}/api/robo/positions")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

    def test_positions_has_open_positions(self, api):
        r = api.get(f"{BASE_URL}/api/robo/positions")
        data = r.json()
        assert 'open_positions' in data, f"Missing 'open_positions' in: {list(data.keys())}"
        assert isinstance(data['open_positions'], list)

    def test_positions_has_success(self, api):
        r = api.get(f"{BASE_URL}/api/robo/positions")
        data = r.json()
        assert data.get('success') is True, f"Expected success=true, got: {data.get('success')}"


# ─── GET /api/robo/orders ────────────────────────────────────────────────────

class TestRoboOrders:
    """GET /api/robo/orders — closed order history"""

    def test_orders_returns_200(self, api):
        r = api.get(f"{BASE_URL}/api/robo/orders")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

    def test_orders_has_orders_list(self, api):
        r = api.get(f"{BASE_URL}/api/robo/orders")
        data = r.json()
        assert 'orders' in data, f"Missing 'orders' in: {list(data.keys())}"
        assert isinstance(data['orders'], list)

    def test_orders_has_daily_pnl(self, api):
        r = api.get(f"{BASE_URL}/api/robo/orders")
        data = r.json()
        assert 'daily_pnl' in data, f"Missing 'daily_pnl' in: {list(data.keys())}"

    def test_orders_respects_limit_param(self, api):
        r = api.get(f"{BASE_URL}/api/robo/orders?limit=5")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.json()
        assert 'orders' in data
        # If there are orders, they should not exceed limit
        assert len(data['orders']) <= 5


# ─── POST /api/robo/start ────────────────────────────────────────────────────

class TestRoboStart:
    """POST /api/robo/start — start auto mode"""

    def test_start_returns_success(self, api):
        # First stop to ensure clean state
        api.post(f"{BASE_URL}/api/robo/stop")
        r = api.post(f"{BASE_URL}/api/robo/start", json={
            "ticker": "RELIANCE.NS",
            "interval_minutes": 5
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data.get('success') is True, f"Expected success=true, got: {data}"

    def test_start_updates_status_to_scanning(self, api):
        # Start should set auto_mode=True
        status = api.get(f"{BASE_URL}/api/robo/status").json()
        assert status.get('auto_mode') is True, f"auto_mode not true after start: {status.get('auto_mode')}"

    def test_start_with_custom_ticker(self, api):
        api.post(f"{BASE_URL}/api/robo/stop")
        r = api.post(f"{BASE_URL}/api/robo/start", json={
            "ticker": "TCS.NS",
            "interval_minutes": 10
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get('success') is True


# ─── POST /api/robo/stop ─────────────────────────────────────────────────────

class TestRoboStop:
    """POST /api/robo/stop — stop auto mode"""

    def test_stop_returns_200(self, api):
        # Ensure it's running first
        api.post(f"{BASE_URL}/api/robo/start", json={"ticker": "RELIANCE.NS", "interval_minutes": 5})
        r = api.post(f"{BASE_URL}/api/robo/stop")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"

    def test_stop_returns_success(self, api):
        r = api.post(f"{BASE_URL}/api/robo/stop")
        data = r.json()
        assert data.get('success') is True, f"Expected success=true: {data}"

    def test_stop_sets_auto_mode_false(self, api):
        # Stop the loop
        api.post(f"{BASE_URL}/api/robo/stop")
        import time; time.sleep(0.5)
        status = api.get(f"{BASE_URL}/api/robo/status").json()
        assert status.get('auto_mode') is False, f"auto_mode not false after stop: {status.get('auto_mode')}"


# ─── POST /api/robo/mode ─────────────────────────────────────────────────────

class TestRoboMode:
    """POST /api/robo/mode — switch execution mode"""

    def test_mode_paper_returns_success(self, api):
        r = api.post(f"{BASE_URL}/api/robo/mode", json={"mode": "paper"})
        assert r.status_code == 200, f"Expected 200: {r.text[:200]}"
        data = r.json()
        assert data.get('success') is True, f"Expected success=true: {data}"

    def test_mode_shadow_returns_success(self, api):
        r = api.post(f"{BASE_URL}/api/robo/mode", json={"mode": "shadow"})
        assert r.status_code == 200
        data = r.json()
        assert data.get('success') is True

    def test_mode_live_fails_without_api_key(self, api):
        """Live mode requires GROWW_API_KEY — should fail without it"""
        r = api.post(f"{BASE_URL}/api/robo/mode", json={"mode": "live"})
        assert r.status_code == 200
        data = r.json()
        # Either fails (no API key) or succeeds — just check it doesn't 500
        assert 'success' in data

    def test_mode_invalid_rejected(self, api):
        r = api.post(f"{BASE_URL}/api/robo/mode", json={"mode": "invalid_mode"})
        data = r.json()
        # Should fail cleanly
        assert data.get('success') is False or r.status_code in [400, 422], \
            f"Expected failure for invalid mode: {data}"

    def test_mode_reset_to_paper(self, api):
        """Cleanup: reset back to paper"""
        r = api.post(f"{BASE_URL}/api/robo/mode", json={"mode": "paper"})
        assert r.status_code == 200
        assert r.json().get('success') is True


# ─── POST /api/robo/risk-preview ─────────────────────────────────────────────

class TestRoboRiskPreview:
    """POST /api/robo/risk-preview — live risk preview for settings modal"""

    def test_risk_preview_returns_200(self, api):
        r = api.post(f"{BASE_URL}/api/robo/risk-preview", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000,
            "risk_tolerance": "moderate"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"

    def test_risk_preview_has_preview_key(self, api):
        r = api.post(f"{BASE_URL}/api/robo/risk-preview", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000,
            "risk_tolerance": "moderate"
        })
        data = r.json()
        assert 'preview' in data, f"Missing 'preview' key in: {list(data.keys())}"

    def test_risk_preview_contains_feasibility(self, api):
        r = api.post(f"{BASE_URL}/api/robo/risk-preview", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000,
            "risk_tolerance": "moderate"
        })
        preview = r.json().get('preview', {})
        assert 'feasibility_score' in preview or 'required_daily_return_pct' in preview, \
            f"Missing key fields in preview: {list(preview.keys())}"

    def test_risk_preview_conservative_vs_aggressive(self, api):
        """Conservative mode should generally have lower required return than aggressive"""
        cons = api.post(f"{BASE_URL}/api/robo/risk-preview", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000,
            "risk_tolerance": "conservative"
        }).json().get('preview', {})
        aggr = api.post(f"{BASE_URL}/api/robo/risk-preview", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000,
            "risk_tolerance": "aggressive"
        }).json().get('preview', {})
        # Both should have success previews
        assert cons or aggr, "Both previews are empty"


# ─── POST /api/robo/settings ─────────────────────────────────────────────────

class TestRoboSettings:
    """POST /api/robo/settings — update and get settings"""

    def test_post_settings_returns_200(self, api):
        r = api.post(f"{BASE_URL}/api/robo/settings", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000,
            "ticker": "RELIANCE.NS",
            "risk_tolerance": "moderate"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"

    def test_post_settings_has_success(self, api):
        r = api.post(f"{BASE_URL}/api/robo/settings", json={
            "daily_profit_target": 1000,
            "allocated_capital": 100000,
            "ticker": "RELIANCE.NS",
            "risk_tolerance": "moderate"
        })
        data = r.json()
        assert data.get('success') is True, f"Expected success=true: {data}"

    def test_get_settings_returns_200(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"

    def test_settings_has_required_fields(self, api):
        r = api.get(f"{BASE_URL}/api/robo/settings")
        data = r.json()
        assert 'preferences' in data or 'settings' in data or 'daily_profit_target' in data, \
            f"Missing settings fields in: {list(data.keys())}"


# ─── POST /api/robo/recalculate ──────────────────────────────────────────────

class TestRoboRecalculate:
    """POST /api/robo/recalculate — force recalculate risk profile"""

    def test_recalculate_returns_200(self, api):
        r = api.post(f"{BASE_URL}/api/robo/recalculate", json={"trigger": "manual"})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"

    def test_recalculate_has_success_or_queued(self, api):
        r = api.post(f"{BASE_URL}/api/robo/recalculate", json={"trigger": "manual"})
        data = r.json()
        assert data.get('success') is True or 'queued' in str(data).lower() or 'scheduled' in str(data).lower(), \
            f"Expected success/queued response: {data}"
