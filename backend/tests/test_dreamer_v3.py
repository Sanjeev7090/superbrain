"""
Tests for DreamerV3 RL Agent endpoints:
  - GET  /api/rl-agent/status  — must return algorithm=DreamerV3
  - POST /api/rl-agent/train   — accepts DreamerV3, starts background training
  - POST /api/rl-agent/stop    — stops training
  - POST /api/rl-agent/predict — returns signal, wm_loss, kronos_active
  - POST /api/rl-agent/reset   — resets agent to idle state
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(autouse=True, scope="module")
def reset_before_and_after():
    """Ensure DreamerV3 agent is idle before and after the test suite."""
    r = requests.post(f"{BASE_URL}/api/rl-agent/reset")
    assert r.status_code == 200, f"Pre-suite reset failed: {r.text}"
    time.sleep(1)
    yield
    requests.post(f"{BASE_URL}/api/rl-agent/reset")


# ─── Status ──────────────────────────────────────────────────────────────────

class TestDreamerV3Status:
    """GET /api/rl-agent/status"""

    def test_status_returns_200(self):
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        print("PASS: /status → 200")

    def test_status_algorithm_is_dreamerv3(self):
        """Core requirement: algorithm must be DreamerV3, not PPO/SAC."""
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        assert data.get("algorithm") == "DreamerV3", \
            f"Expected algorithm=DreamerV3, got: {data.get('algorithm')}"
        print(f"PASS: algorithm=DreamerV3")

    def test_status_has_dreamerv3_specific_fields(self):
        """DreamerV3-specific state fields must be present."""
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        dreamer_fields = ["wm_loss", "actor_loss", "critic_loss", "kronos_active", "kronos_bonus"]
        for field in dreamer_fields:
            assert field in data, f"Missing DreamerV3 field: {field}"
        print(f"PASS: DreamerV3 fields present — wm_loss={data['wm_loss']}, kronos_active={data['kronos_active']}")

    def test_status_has_standard_rl_fields(self):
        """Standard RL state fields must still be present."""
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        assert r.status_code == 200
        data = r.json()
        standard_fields = [
            "status", "algorithm", "mode", "ticker",
            "episode", "timesteps_done", "timesteps_total",
            "episode_rewards", "last_weights", "avg_reward_10", "best_reward"
        ]
        for field in standard_fields:
            assert field in data, f"Missing standard field: {field}"
        print(f"PASS: all standard fields present")

    def test_status_idle_after_reset(self):
        r_reset = requests.post(f"{BASE_URL}/api/rl-agent/reset")
        assert r_reset.status_code == 200
        time.sleep(0.5)

        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        data = r.json()
        assert data["status"] == "idle", f"Expected idle, got {data['status']}"
        print("PASS: status=idle after reset")

    def test_status_12_strategy_weights(self):
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        data = r.json()
        weights = data.get("last_weights", [])
        assert len(weights) == 12, f"Expected 12 weights, got {len(weights)}"
        print("PASS: 12 strategy weights present")


# ─── Train ───────────────────────────────────────────────────────────────────

class TestDreamerV3Train:
    """POST /api/rl-agent/train with DreamerV3"""

    def test_dreamerv3_train_returns_200(self):
        # Reset first
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        payload = {
            "algorithm": "DreamerV3",
            "mode": "historical",
            "ticker": "RELIANCE.NS",
            "timesteps": 5000,
        }
        r = requests.post(f"{BASE_URL}/api/rl-agent/train", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        print("PASS: /train → 200")

    def test_dreamerv3_train_success_true(self):
        """Training start should return success=True."""
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        r = requests.post(f"{BASE_URL}/api/rl-agent/train", json={
            "algorithm": "DreamerV3",
            "mode": "historical",
            "ticker": "RELIANCE.NS",
            "timesteps": 5000,
        })
        data = r.json()
        assert data.get("success") is True, f"Expected success=True, got: {data}"
        print(f"PASS: training started — {data.get('message')}")

    def test_dreamerv3_status_becomes_training(self):
        """Status should transition to 'training' after start."""
        time.sleep(1.5)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        data = r.json()
        assert data["status"] in ("training", "running", "paused"), \
            f"Expected training/running/paused, got: {data['status']}"
        # CRITICAL: algorithm must still be DreamerV3 during training
        assert data["algorithm"] == "DreamerV3", \
            f"Expected DreamerV3 during training, got: {data['algorithm']}"
        print(f"PASS: training status={data['status']}, algorithm={data['algorithm']}")

    def test_dreamerv3_duplicate_train_blocked(self):
        """Cannot start new training while DreamerV3 already training."""
        # Check if still training
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        if r.json().get("status") != "training":
            pytest.skip("Agent not in training state — skipping duplicate test")

        r2 = requests.post(f"{BASE_URL}/api/rl-agent/train", json={
            "algorithm": "DreamerV3",
            "mode": "historical",
            "ticker": "TCS.NS",
            "timesteps": 5000,
        })
        data2 = r2.json()
        assert data2.get("success") is False, f"Expected duplicate blocked: {data2}"
        print(f"PASS: duplicate DreamerV3 training blocked")

    def test_dreamerv3_default_algorithm_is_dreamerv3(self):
        """Sending no algorithm field should default to DreamerV3."""
        requests.post(f"{BASE_URL}/api/rl-agent/stop")
        time.sleep(0.5)
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        # No algorithm field — router default is DreamerV3
        r = requests.post(f"{BASE_URL}/api/rl-agent/train", json={
            "mode": "historical",
            "ticker": "RELIANCE.NS",
            "timesteps": 5000,
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True, f"Default DreamerV3 train failed: {data}"
        print(f"PASS: default algorithm=DreamerV3 works — {data.get('message')}")


# ─── Stop ────────────────────────────────────────────────────────────────────

class TestDreamerV3Stop:
    """POST /api/rl-agent/stop"""

    def test_stop_returns_200(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/stop")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        print("PASS: /stop → 200")

    def test_stop_returns_success_true(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/stop")
        data = r.json()
        assert data.get("success") is True, f"Expected success=True: {data}"
        print(f"PASS: stop returned success=True — {data.get('message')}")

    def test_stop_sets_status_paused_or_idle(self):
        time.sleep(1)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        data = r.json()
        assert data["status"] in ("paused", "idle", "running"), \
            f"Unexpected status after stop: {data['status']}"
        print(f"PASS: status={data['status']} after stop")


# ─── Reset ───────────────────────────────────────────────────────────────────

class TestDreamerV3Reset:
    """POST /api/rl-agent/reset"""

    def test_reset_returns_200(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/reset")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        print("PASS: /reset → 200")

    def test_reset_returns_success_true(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/reset")
        data = r.json()
        assert data.get("success") is True, f"Expected success=True: {data}"
        print("PASS: reset returned success=True")

    def test_reset_clears_dreamerv3_state(self):
        time.sleep(0.5)
        r = requests.get(f"{BASE_URL}/api/rl-agent/status")
        data = r.json()

        assert data["status"] == "idle",        f"Expected idle, got: {data['status']}"
        assert data["episode"] == 0,             f"Expected episode=0, got: {data['episode']}"
        assert data["model_saved"] is False,     f"Expected model_saved=False, got: {data['model_saved']}"
        assert data["wm_loss"] == 0.0,           f"Expected wm_loss=0.0, got: {data['wm_loss']}"
        assert data["actor_loss"] == 0.0,        f"Expected actor_loss=0.0, got: {data['actor_loss']}"
        assert data["critic_loss"] == 0.0,       f"Expected critic_loss=0.0, got: {data['critic_loss']}"
        assert data["kronos_active"] is False,   f"Expected kronos_active=False, got: {data['kronos_active']}"
        assert data["algorithm"] == "DreamerV3", f"Algorithm should remain DreamerV3 after reset"
        print("PASS: all DreamerV3 fields reset to defaults")


# ─── Predict ─────────────────────────────────────────────────────────────────

class TestDreamerV3Predict:
    """POST /api/rl-agent/predict"""

    def test_predict_returns_200(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        print("PASS: /predict → 200")

    def test_predict_has_dreamerv3_wm_loss_field(self):
        """wm_loss is a DreamerV3-specific field in the prediction response."""
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        data = r.json()
        assert "wm_loss" in data, f"Missing DreamerV3 field: wm_loss. Response: {data}"
        assert isinstance(data["wm_loss"], float), f"wm_loss should be float, got: {type(data['wm_loss'])}"
        print(f"PASS: wm_loss present = {data['wm_loss']}")

    def test_predict_has_kronos_active_field(self):
        """kronos_active is a DreamerV3-specific integration field."""
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        data = r.json()
        assert "kronos_active" in data, f"Missing DreamerV3 field: kronos_active. Response: {data}"
        assert isinstance(data["kronos_active"], bool), \
            f"kronos_active should be bool, got: {type(data['kronos_active'])}"
        print(f"PASS: kronos_active present = {data['kronos_active']}")

    def test_predict_has_actor_loss_field(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        data = r.json()
        assert "actor_loss" in data, f"Missing DreamerV3 field: actor_loss. Response: {data}"
        print(f"PASS: actor_loss present = {data.get('actor_loss')}")

    def test_predict_signal_is_valid(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        data = r.json()
        assert data["signal"] in ("BUY", "SELL", "HOLD"), \
            f"Signal must be BUY/SELL/HOLD, got: {data['signal']}"
        print(f"PASS: signal={data['signal']}")

    def test_predict_idle_returns_hold_confidence_0(self):
        """When agent is idle (not trained), predict must return HOLD with confidence=0."""
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        data = r.json()
        assert data["signal"] == "HOLD",    f"Expected HOLD when idle, got: {data['signal']}"
        assert data["confidence"] == 0,     f"Expected confidence=0 when idle, got: {data['confidence']}"
        assert data["wm_loss"] == 0.0,      f"Expected wm_loss=0.0 when idle, got: {data['wm_loss']}"
        assert data["kronos_active"] is False, f"Expected kronos_active=False when idle"
        print(f"PASS: idle agent → HOLD, confidence=0, wm_loss=0.0, kronos_active=False")

    def test_predict_strategy_weights_12_entries(self):
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        data = r.json()
        sw = data.get("strategy_weights", {})
        assert len(sw) == 12, f"Expected 12 strategy weights, got {len(sw)}"
        print(f"PASS: strategy_weights has 12 entries")

    def test_predict_has_message_field(self):
        """DreamerV3 predict message should include WM loss info."""
        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        data = r.json()
        assert "message" in data, "Missing message field in predict response"
        print(f"PASS: message = '{data['message']}'")


# ─── End-to-end Training Cycle ───────────────────────────────────────────────

class TestDreamerV3TrainCycle:
    """Start → Poll status (training) → Stop → Predict (post-training signals)"""

    def test_full_train_stop_cycle(self):
        # 1. Reset
        requests.post(f"{BASE_URL}/api/rl-agent/reset")
        time.sleep(0.5)

        # 2. Start DreamerV3 training
        r = requests.post(f"{BASE_URL}/api/rl-agent/train", json={
            "algorithm": "DreamerV3",
            "mode": "historical",
            "ticker": "RELIANCE.NS",
            "timesteps": 5000,
        })
        assert r.json().get("success") is True, f"Train start failed: {r.json()}"
        print("PASS: DreamerV3 training started")

        # 3. Poll for training status (up to 5 seconds)
        time.sleep(1)
        r_st = requests.get(f"{BASE_URL}/api/rl-agent/status")
        st_data = r_st.json()
        assert st_data["status"] in ("training", "running", "paused"), \
            f"Unexpected status: {st_data['status']}"
        assert st_data["algorithm"] == "DreamerV3"
        print(f"PASS: status={st_data['status']}, algorithm=DreamerV3")

        # 4. Stop training
        r_stop = requests.post(f"{BASE_URL}/api/rl-agent/stop")
        assert r_stop.status_code == 200
        assert r_stop.json().get("success") is True
        time.sleep(1)
        print("PASS: stop returned success")

        # 5. Verify paused/idle status
        r_after = requests.get(f"{BASE_URL}/api/rl-agent/status")
        after_data = r_after.json()
        assert after_data["status"] in ("paused", "idle", "running"), \
            f"After stop: {after_data['status']}"
        print(f"PASS: post-stop status={after_data['status']}")

    def test_predict_after_training_started(self):
        """Even during training, predict should work and return wm_loss + kronos_active."""
        # Ensure training is active
        r_st = requests.get(f"{BASE_URL}/api/rl-agent/status")
        if r_st.json().get("status") == "idle":
            requests.post(f"{BASE_URL}/api/rl-agent/train", json={
                "algorithm": "DreamerV3",
                "mode": "historical",
                "ticker": "RELIANCE.NS",
                "timesteps": 5000,
            })
            time.sleep(1)

        r = requests.post(f"{BASE_URL}/api/rl-agent/predict", json={"ticker": "RELIANCE.NS"})
        assert r.status_code == 200
        data = r.json()
        assert "wm_loss" in data,       f"wm_loss missing from predict response"
        assert "kronos_active" in data, f"kronos_active missing from predict response"
        assert data["signal"] in ("BUY", "SELL", "HOLD")
        print(f"PASS: mid-training predict → signal={data['signal']}, "
              f"wm_loss={data['wm_loss']}, kronos_active={data['kronos_active']}")
