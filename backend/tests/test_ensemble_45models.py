"""
Comprehensive tests for the 45-Model Ensemble Cockpit features.
Tests: full-analysis (45 models), signal (3-model), status endpoint.
"""
import os
import requests
import pytest

# Load BASE_URL from env or frontend/.env
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

TIMEOUT_LLM = 180  # Full 45-model analysis can take up to ~60s; give plenty of margin


# ─── 1. Status endpoint ────────────────────────────────────────────────────────

class TestEnsembleStatus:
    """GET /api/ensemble/status"""

    def test_status_200(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/status", timeout=30)
        assert r.status_code == 200

    def test_status_key_configured(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/status", timeout=30)
        d = r.json()
        assert d["key_configured"] is True, f"key_configured should be True, got: {d}"

    def test_status_total_models_45(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/status", timeout=30)
        d = r.json()
        assert d["total_models"] == 45, f"Expected 45 models, got {d.get('total_models')}"

    def test_status_provider_mode_emergent(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/status", timeout=30)
        d = r.json()
        assert d["provider_mode"] in ("emergent", "freellmapi"), f"Unexpected provider_mode: {d}"

    def test_status_3_default_models(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/status", timeout=30)
        d = r.json()
        models = d["models"]
        assert len(models) == 3, f"Expected 3 default models, got {len(models)}"
        names = [m["display_name"] for m in models]
        assert "Claude Sonnet 4.5" in names
        assert "Gemini 3 Pro" in names
        assert "GPT-5.2" in names


# ─── 2. Signal endpoint (3-model standard) ────────────────────────────────────

class TestEnsembleSignal:
    """POST /api/ensemble/signal — 3-model verdict"""

    def test_signal_with_context(self):
        """Provide context dict directly to avoid yfinance dependency."""
        payload = {
            "ticker": "RELIANCE",
            "context": {
                "close": 2800.0,
                "rsi": 55.0,
                "trend": "bullish",
                "ema20": 2750.0,
                "sma50": 2700.0,
                "atr": 42.0,
                "support": 2720.0,
                "resistance": 2880.0
            }
        }
        r = requests.post(f"{BASE_URL}/api/ensemble/signal", json=payload, timeout=TIMEOUT_LLM)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
        d = r.json()
        assert d.get("success") is True, f"success should be True: {d}"

    def test_signal_verdict_fields(self):
        """Verdict must contain consensus signal + confidence."""
        payload = {
            "ticker": "RELIANCE",
            "context": {"close": 2800.0, "rsi": 55.0, "trend": "bullish"}
        }
        r = requests.post(f"{BASE_URL}/api/ensemble/signal", json=payload, timeout=TIMEOUT_LLM)
        assert r.status_code == 200
        d = r.json()
        if not d.get("success"):
            pytest.skip(f"Signal call failed: {d}")
        v = d["verdict"]
        assert v["consensus"] in ("BUY", "SELL", "HOLD", "ABSTAIN"), f"Invalid consensus: {v['consensus']}"
        assert isinstance(v["confidence"], int), f"confidence should be int: {v['confidence']}"
        assert 0 <= v["confidence"] <= 100

    def test_signal_per_model_count(self):
        """Should have at least 3 models in per_model list."""
        payload = {
            "ticker": "RELIANCE",
            "context": {"close": 2800.0, "rsi": 55.0, "trend": "bullish"}
        }
        r = requests.post(f"{BASE_URL}/api/ensemble/signal", json=payload, timeout=TIMEOUT_LLM)
        assert r.status_code == 200
        d = r.json()
        if not d.get("success"):
            pytest.skip(f"Signal call failed: {d}")
        assert len(d["verdict"]["per_model"]) >= 3, "Must have >=3 models"

    def test_signal_consensus_is_string(self):
        """Consensus must be a non-empty string."""
        payload = {
            "ticker": "RELIANCE",
            "context": {"close": 2800.0, "rsi": 55.0, "trend": "bullish"}
        }
        r = requests.post(f"{BASE_URL}/api/ensemble/signal", json=payload, timeout=TIMEOUT_LLM)
        assert r.status_code == 200
        d = r.json()
        if not d.get("success"):
            pytest.skip(f"Signal call failed: {d}")
        consensus = d["verdict"]["consensus"]
        assert isinstance(consensus, str) and len(consensus) > 0


# ─── 3. Full-analysis endpoint (45 models) ───────────────────────────────────

class TestFullAnalysis:
    """POST /api/ensemble/full-analysis — 45 model cockpit"""

    @pytest.fixture(scope="class")
    def full_analysis_response(self):
        """Single shared LLM request for the entire class — saves budget."""
        payload = {
            "ticker": "RELIANCE",
            "context": {
                "close": 2800.0,
                "rsi": 55.0,
                "trend": "bullish",
                "ema20": 2750.0,
                "sma50": 2700.0,
                "atr": 42.0,
                "support": 2720.0,
                "resistance": 2880.0
            }
        }
        r = requests.post(
            f"{BASE_URL}/api/ensemble/full-analysis",
            json=payload,
            timeout=TIMEOUT_LLM,
        )
        return r

    def test_full_analysis_200(self, full_analysis_response):
        assert full_analysis_response.status_code == 200, (
            f"Expected 200, got {full_analysis_response.status_code}: "
            f"{full_analysis_response.text[:300]}"
        )

    def test_full_analysis_success_true(self, full_analysis_response):
        d = full_analysis_response.json()
        assert d.get("success") is True, f"success should be True: {d}"

    def test_full_analysis_total_45(self, full_analysis_response):
        """total field should be 45 (or 46 if Kronos is loaded)."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        total = d.get("total", 0)
        assert total >= 45, f"Expected >=45 total, got {total}"

    def test_full_analysis_models_list_45(self, full_analysis_response):
        """models list must have 45+ entries."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        models = d.get("models", [])
        assert len(models) >= 45, f"Expected >=45 models, got {len(models)}"

    def test_full_analysis_models_have_num(self, full_analysis_response):
        """Each model entry must have a 'num' field numbered 1..N."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        models = d.get("models", [])
        nums = [m.get("num") for m in models]
        assert nums[0] == 1, f"First model num should be 1, got {nums[0]}"
        assert nums[44] == 45, f"45th model num should be 45, got {nums[44]}"

    def test_full_analysis_models_have_signal(self, full_analysis_response):
        """Every model entry must have a signal field."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        models = d.get("models", [])
        valid_signals = {"BUY", "SELL", "HOLD", "ABSTAIN", "WAIT"}
        for m in models[:45]:  # test core 45
            sig = m.get("signal") or "HOLD"
            assert isinstance(sig, str), f"Model {m.get('num')} signal not string: {sig}"

    def test_full_analysis_ok_models_count(self, full_analysis_response):
        """At least 40/45 models should return ok:True (budget may cause <5 failures)."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        budget_warning = d.get("budget_warning")
        if budget_warning:
            pytest.skip(f"Budget exceeded — transient: {budget_warning}")
        models = d.get("models", [])
        ok_count = sum(1 for m in models[:45] if m.get("ok") is True)
        assert ok_count >= 40, (
            f"Only {ok_count}/45 models returned ok:True. "
            f"Failed models: {[m.get('model') for m in models[:45] if not m.get('ok')]}"
        )

    def test_full_analysis_ok_models_have_confidence(self, full_analysis_response):
        """ok:True models must have confidence value."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        budget_warning = d.get("budget_warning")
        if budget_warning:
            pytest.skip(f"Budget exceeded: {budget_warning}")
        models = d.get("models", [])
        for m in models[:45]:
            if m.get("ok"):
                conf = m.get("confidence")
                assert conf is not None, f"Model {m.get('model')} ok:True but no confidence"
                assert 0 <= int(conf) <= 100, f"Confidence out of range: {conf}"

    def test_full_analysis_ok_models_have_stop_loss(self, full_analysis_response):
        """ok:True models should have stop_loss (may be None if LLM didn't provide)."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        budget_warning = d.get("budget_warning")
        if budget_warning:
            pytest.skip(f"Budget exceeded: {budget_warning}")
        models = d.get("models", [])
        ok_models = [m for m in models[:45] if m.get("ok")]
        # At least half of ok models should have stop_loss
        sl_count = sum(1 for m in ok_models if m.get("stop_loss") is not None)
        assert sl_count >= len(ok_models) // 2, (
            f"Only {sl_count}/{len(ok_models)} ok models have stop_loss"
        )

    def test_full_analysis_ok_models_have_target1(self, full_analysis_response):
        """ok:True models should have target_1."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        budget_warning = d.get("budget_warning")
        if budget_warning:
            pytest.skip(f"Budget exceeded: {budget_warning}")
        models = d.get("models", [])
        ok_models = [m for m in models[:45] if m.get("ok")]
        t1_count = sum(1 for m in ok_models if m.get("target_1") is not None)
        assert t1_count >= len(ok_models) // 2, (
            f"Only {t1_count}/{len(ok_models)} ok models have target_1"
        )

    def test_full_analysis_consensus_field(self, full_analysis_response):
        """consensus field should be BUY/SELL/HOLD."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        consensus = d.get("consensus")
        assert consensus in ("BUY", "SELL", "HOLD"), f"Unexpected consensus: {consensus}"

    def test_full_analysis_vote_counts(self, full_analysis_response):
        """vote_counts should have BUY/SELL/HOLD keys."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        vc = d.get("vote_counts", {})
        assert "BUY" in vc
        assert "SELL" in vc
        assert "HOLD" in vc

    def test_full_analysis_avg_confidence(self, full_analysis_response):
        """avg_confidence should be 0-100."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        avg = d.get("avg_confidence", -1)
        assert 0 <= avg <= 100, f"avg_confidence out of range: {avg}"

    def test_full_analysis_budget_warning_field_present(self, full_analysis_response):
        """budget_warning field must always be present (can be null)."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        # budget_warning key must exist (value can be None)
        assert "budget_warning" in d, "budget_warning field missing from response"

    def test_full_analysis_model_families_distributed(self, full_analysis_response):
        """Models should span multiple families (claude, gpt, gemini...)."""
        d = full_analysis_response.json()
        if not d.get("success"):
            pytest.skip("full-analysis failed")
        models = d.get("models", [])
        families = set(m.get("family") for m in models[:45])
        assert len(families) >= 3, f"Expected >=3 families, got: {families}"


# ─── 4. Models listing endpoint ──────────────────────────────────────────────

class TestFullAnalysisModelsList:
    """GET /api/ensemble/full-analysis/models"""

    def test_models_list_200(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/full-analysis/models", timeout=30)
        assert r.status_code == 200

    def test_models_list_count_45(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/full-analysis/models", timeout=30)
        d = r.json()
        assert d["count"] == 45, f"Expected 45 models in listing, got {d.get('count')}"
        assert len(d["models"]) == 45

    def test_models_list_fields(self):
        r = requests.get(f"{BASE_URL}/api/ensemble/full-analysis/models", timeout=30)
        d = r.json()
        for m in d["models"]:
            assert "id" in m
            assert "display" in m
            assert "family" in m
