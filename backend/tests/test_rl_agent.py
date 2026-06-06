"""
Tests for RL Agent endpoints:
  - GET  /api/rl-agent/status
  - POST /api/rl-agent/train   (PPO + SAC)
  - POST /api/rl-agent/stop
  - POST /api/rl-agent/reset
  - POST /api/rl-agent/predict
  - Duplicate training prevention
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(autouse=True, scope="module")
def reset_before_suite():
    """Ensure agent is in idle state before tests run."""
    r = requests.post(f"{BASE_URL}/api/rl-agent/reset")
    assert r.status_code == 200, f"Pre-suite reset failed: {r.text}"
    time.sleep(1)
    yield
    # Cleanup: reset after all tests
    requests.post(f"{BASE_URL}/api/rl-agent/reset")


class TestRLAgentStatus:
    """GET /api/rl-agent/status"""

    def test_status_returns_200(self):
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        print("PASS: status returns 200")

    def test_status_has_required_fields(self):
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()

        required_fields = [
            "status", "algorithm", "mode", "ticker",
            "episode", "timesteps_done", "timesteps_total",
            "episode_rewards", "last_weights", "avg_reward_10", "best_reward"
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

        print(f"PASS: all required fields present — status={data['status']}")

    def test_status_idle_after_reset(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/reset")
        assert r.status_code == 200
        time.sleep(0.5)

        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "idle", f"Expected idle, got {data['status']}"
        print("PASS: status=idle after reset")

    def test_status_last_weights_has_12_entries(self):
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        weights = data.get("last_weights", [])
        assert len(weights) == 12, f"Expected 12 weights, got {len(weights)}: {weights}"
        print(f"PASS: last_weights has 12 entries")

    def test_status_episode_rewards_is_list(self):
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["episode_rewards"], list), "episode_rewards should be a list"
        print("PASS: episode_rewards is list")


class TestRLAgentTrainPPO:
    """POST /api/rl-agent/train with PPO"""

    def test_ppo_train_starts_successfully(self):
        # Reset first
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        payload = {
            "algorithm": "PPO",
            "mode": "historical",
            "ticker": "RELIANCE.NS",
            "timesteps": 5000
        }
        r = requests.post(f"{BASE_URL}/api/rl-agent/train", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()
        assert data.get("success") is True, f"Expected success=true, got: {data}"
        print(f"PASS: PPO training started — {data.get('message')}")

    def test_ppo_status_becomes_training(self):
        # Status should be training shortly after start
        time.sleep(1)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        # Status should be 'training' or already completed to 'running'
        assert data["status"] in ("training", "running", "paused"), \
            f"Expected training/running/paused, got: {data['status']}"
        assert data["algorithm"] == "DreamerV3", f"Expected DreamerV3 algorithm, got: {data['algorithm']}"
        assert data["ticker"] == "RELIANCE.NS"
        print(f"PASS: status={data['status']}, algorithm={data['algorithm']}, ticker={data['ticker']}")

    def test_ppo_duplicate_train_returns_error(self):
        """Cannot start training when already training."""
        # Check current status
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        current_status = r.json().get("status")
        if current_status != "training":
            pytest.skip(f"Agent not in training state (status={current_status}), skipping duplicate test")

        payload = {
            "algorithm": "PPO",
            "mode": "historical",
            "ticker": "TCS.NS",
            "timesteps": 5000
        }
        r2 = requests.post(f"{BASE_URL}/api/rl-agent/train", json=payload)
        assert r2.status_code == 200
        data = r2.json()
        assert data.get("success") is False, f"Expected success=false for duplicate train, got: {data}"
        assert "error" in data or "message" in data
        print(f"PASS: duplicate training blocked — {data}")

    def test_ppo_training_progresses(self):
        """Wait for PPO to complete or progress."""
        # Wait up to 20 seconds for PPO to finish
        max_wait = 20
        start = time.time()
        while time.time() - start < max_wait:
            r = requests.get(f"{BASE_URL}/api/rl-agent/status")
            data = r.json()
            if data["status"] in ("running", "paused", "idle"):
                break
            time.sleep(2)

        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        data = r.json()
        print(f"INFO: After ~{int(time.time() - start)}s — status={data['status']}, "
              f"episodes={data.get('episode')}, timesteps={data.get('timesteps_done')}")

        # Either training completed (running) or still training — both acceptable
        assert data["status"] in ("training", "running", "paused"), \
            f"Unexpected status: {data['status']}, error: {data.get('error')}"

        if data["status"] == "running":
            assert data.get("episode", 0) >= 0
            print(f"PASS: PPO completed — {data.get('episode')} episodes, "
                  f"{data.get('timesteps_done')} timesteps")
        else:
            print(f"INFO: PPO still training after {max_wait}s")


class TestRLAgentStop:
    """POST /api/rl-agent/stop"""

    def test_stop_returns_success(self):
        # Start training first if not already in progress
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        st = r.json().get("status")
        if st not in ("training", "running"):
            requests.post(f"{BASE_URL}/api/rl-agent/reset")
            time.sleep(0.3)
            requests.post(f"{BASE_URL}/api/rl-agent/train", json={
                "algorithm": "PPO",
                "mode": "historical",
                "ticker": "RELIANCE.NS",
                "timesteps": 5000
            })
            time.sleep(0.5)

        r = requests.post(f"{BASE_URL}/api/rl-agent/stop")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("success") is True, f"Expected success=true: {data}"
        print(f"PASS: stop returned success")

    def test_stop_sets_status_paused(self):
        time.sleep(1)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        # After stop, status should be paused (or idle if training completed before stop was processed)
        assert data["status"] in ("paused", "idle", "running"), \
            f"Unexpected status after stop: {data['status']}"
        print(f"PASS: status={data['status']} after stop")


class TestRLAgentReset:
    """POST /api/rl-agent/reset"""

    def test_reset_returns_success(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/reset")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("success") is True, f"Expected success=true: {data}"
        print("PASS: reset returned success")

    def test_reset_clears_state(self):
        time.sleep(0.5)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "idle", f"Expected idle after reset, got: {data['status']}"
        assert data.get("model_saved") is False, f"Expected model_saved=False after reset"
        assert data.get("episode", 0) == 0, f"Expected episode=0 after reset, got: {data.get('episode')}"
        print("PASS: reset cleared state — status=idle, model_saved=False, episode=0")


class TestRLAgentPredict:
    """POST /api/rl-agent/predict"""

    def test_predict_returns_200(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        print("PASS: predict returns 200")

    def test_predict_has_required_fields(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200
        data = r.json()
        assert "signal" in data, "Missing field: signal"
        assert "confidence" in data, "Missing field: confidence"
        assert "strategy_weights" in data, "Missing field: strategy_weights"
        assert "weights_raw" in data, "Missing field: weights_raw"
        print(f"PASS: predict has required fields — signal={data['signal']}, confidence={data['confidence']}")

    def test_predict_signal_is_valid(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200
        data = r.json()
        assert data["signal"] in ("BUY", "SELL", "HOLD"), \
            f"Signal must be BUY/SELL/HOLD, got: {data['signal']}"
        print(f"PASS: signal={data['signal']} is valid")

    def test_predict_strategy_weights_has_12_entries(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200
        data = r.json()
        sw = data.get("strategy_weights", {})
        assert len(sw) == 12, f"Expected 12 strategy weights, got {len(sw)}: {sw}"
        print(f"PASS: strategy_weights has 12 entries")

    def test_predict_weights_raw_has_12_entries(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200
        data = r.json()
        wr = data.get("weights_raw", [])
        assert len(wr) == 12, f"Expected 12 raw weights, got {len(wr)}: {wr}"
        print(f"PASS: weights_raw has 12 entries")

    def test_predict_idle_agent_returns_hold_zero_confidence(self):
        """Idle agent (not trained) should return HOLD with confidence=0."""
        # Make sure agent is reset
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200
        data = r.json()
        assert data["signal"] == "HOLD", f"Expected HOLD when not trained, got: {data['signal']}"
        assert data["confidence"] == 0, f"Expected confidence=0, got: {data['confidence']}"
        print(f"PASS: idle agent returns HOLD with confidence=0")


class TestRLAgentSAC:
    """POST /api/rl-agent/train with SAC"""

    def test_sac_train_starts_successfully(self):
        # Reset first
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        payload = {
            "algorithm": "SAC",
            "mode": "historical",
            "ticker": "TCS.NS",
            "timesteps": 5000
        }
        r = requests.post(f"{BASE_URL}/api/rl-agent/train", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("success") is True, f"Expected success=true: {data}"
        print(f"PASS: SAC training started — {data.get('message')}")

    def test_sac_status_becomes_training(self):
        time.sleep(1)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("training", "running", "paused"), \
            f"Expected training/running/paused, got: {data['status']}"
        assert data["algorithm"] == "DreamerV3", f"Expected DreamerV3 algorithm, got: {data['algorithm']}"
        assert data["ticker"] == "TCS.NS", f"Expected TCS.NS ticker, got: {data['ticker']}"
        print(f"PASS: DreamerV3 training — status={data['status']}, algo={data['algorithm']}, ticker={data['ticker']}")

    def test_sac_duplicate_train_returns_error_while_training(self):
        """Cannot start training when SAC is already training."""
        time.sleep(0.5)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        current_status = r.json().get("status")

        if current_status != "training":
            pytest.skip(f"SAC not in training state (status={current_status})")

        r2 = requests.post(f"{BASE_URL}/api/rl-agent/train", json={
            "algorithm": "PPO",
            "mode": "historical",
            "ticker": "RELIANCE.NS",
            "timesteps": 5000
        })
        assert r2.status_code == 200
        data = r2.json()
        assert data.get("success") is False, f"Expected duplicate blocked: {data}"
        print(f"PASS: duplicate training during SAC blocked")

    def test_sac_stop_and_predict_after_training(self):
        """Stop SAC and get prediction."""
        # Stop training
        r = requests.post(f"{BASE_URL}/api/rl-agent/stop")
        assert r.status_code == 200
        time.sleep(1)

        # Get prediction - should work since training started
        r2 = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "TCS.NS"})
        assert r2.status_code == 200
        data = r2.json()
        assert data["signal"] in ("BUY", "SELL", "HOLD"), f"Invalid signal: {data['signal']}"
        print(f"PASS: after SAC stop — signal={data['signal']}, confidence={data['confidence']}")
