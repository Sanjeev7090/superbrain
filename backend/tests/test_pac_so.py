"""PAC + S&O Matrix backend tests"""
import os
import requests
import math
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://kronos-responsive-ui.preview.emergentagent.com").rstrip("/")


def _gen_bars(n=60, start=100.0, trend=0.5, vol=1.0):
    """Generate synthetic OHLCV bars with mild uptrend."""
    bars = []
    price = start
    for i in range(n):
        # add a small wave + trend
        wave = math.sin(i / 4.0) * vol
        open_p = price
        close_p = price + trend + wave
        high_p = max(open_p, close_p) + abs(vol) * 0.5
        low_p = min(open_p, close_p) - abs(vol) * 0.5
        bars.append({
            "open": round(open_p, 2),
            "high": round(high_p, 2),
            "low": round(low_p, 2),
            "close": round(close_p, 2),
            "volume": 100000 + i * 100,
            "time": 1700000000 + i * 900,
        })
        price = close_p
    return bars


@pytest.fixture
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ============== PAC+S&O Analyze endpoint ==============
class TestPACSOAnalyze:
    def test_analyze_with_sufficient_bars(self, api):
        bars = _gen_bars(60)
        r = api.post(f"{BASE_URL}/api/pac-so/analyze",
                     json={"ticker": "TEST.NS", "bars": bars, "timeframe": "15M"},
                     timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        # Structure validations
        assert "status" in data
        assert "signal_type" in data
        assert data["signal_type"] in ("BUY", "SELL", "WAIT")
        assert "structure_bias" in data
        assert data["structure_bias"] in ("BULLISH", "BEARISH", "NEUTRAL")
        assert "premium_discount" in data
        assert "confluence_score" in data
        assert isinstance(data["confluence_score"], int)
        assert 0 <= data["confluence_score"] <= 100
        assert "modules" in data
        assert isinstance(data["modules"], list)
        # Expect 3 modules
        assert len(data["modules"]) == 3, f"Expected 3 modules, got {len(data['modules'])}"
        module_names = [m["module"] for m in data["modules"]]
        # Module names should include PAC, S&O, Oscillator concepts
        assert any("PAC" in n or "Price Action" in n for n in module_names)
        assert any("S&O" in n or "Signal" in n or "Overlay" in n for n in module_names)
        assert any("Oscillator" in n for n in module_names)
        for m in data["modules"]:
            assert "status" in m
            assert m["status"] in ("PASS", "PARTIAL", "FAIL")
            assert "sub_signals" in m
            assert isinstance(m["sub_signals"], list)
        # Booleans present
        for k in ("bos_detected", "choch_detected", "choch_plus", "liquidity_swept"):
            assert k in data
            assert isinstance(data[k], bool)
        # Confidence + recommendation
        assert "confidence" in data and isinstance(data["confidence"], int)
        assert "recommendation" in data and isinstance(data["recommendation"], str)

    def test_analyze_insufficient_data(self, api):
        bars = _gen_bars(10)
        r = api.post(f"{BASE_URL}/api/pac-so/analyze",
                     json={"ticker": "TEST.NS", "bars": bars, "timeframe": "15M"},
                     timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "INSUFFICIENT_DATA"
        assert data["signal_type"] == "WAIT"
        assert data["confluence_score"] == 0
        assert data["modules"] == []
        assert "30 bars" in data["recommendation"]

    def test_analyze_signal_when_2_modules_pass(self, api):
        """If signal_type != WAIT, then at least 2 modules should PASS (or confluence high)."""
        bars = _gen_bars(80, trend=1.0)
        r = api.post(f"{BASE_URL}/api/pac-so/analyze",
                     json={"ticker": "TEST.NS", "bars": bars}, timeout=30)
        assert r.status_code == 200
        data = r.json()
        if data["signal_type"] in ("BUY", "SELL"):
            pass_count = sum(1 for m in data["modules"] if m["status"] == "PASS")
            assert pass_count >= 2, f"Signal fired but only {pass_count} modules PASS"
            # Entry/SL/TP must be present
            assert data.get("entry_price") is not None
            assert data.get("stop_loss") is not None
            assert data.get("tp1") is not None


# ============== Auto Scanner integration ==============
class TestAutoScanWithPACSO:
    def test_auto_scan_includes_pac_so(self, api):
        r = api.get(f"{BASE_URL}/api/auto-scan/RELIANCE.NS", timeout=90)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "signals" in data
        signals = data["signals"]
        assert isinstance(signals, list)
        # Verify PAC+S&O strategy tag exists (only if signal fired) - but at minimum, check shape
        strategies = [s.get("strategy", "") for s in signals]
        # PAC+S&O may or may not fire; if it fires, prefix should be PAC+S&O
        pac_signals = [s for s in signals if "PAC+S&O" in s.get("strategy", "")]
        for ps in pac_signals:
            assert ps["direction"] in ("BUY", "SELL")
            assert "entry" in ps
            assert "stoploss" in ps
            assert "targets" in ps
            assert isinstance(ps["targets"], list) and len(ps["targets"]) >= 1
            assert "confidence" in ps
        # Other strategies should still be present alongside
        # (SMC/AMDS/MiroFish may fire depending on market - just ensure response not broken)
        print(f"Auto-scan strategies found: {strategies}")
