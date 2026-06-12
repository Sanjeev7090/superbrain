"""
Stock Finder SSE endpoint tests
Endpoint: GET /api/stock-finder/scan?cap={all|large|mid|small}
Streams Server-Sent Events with three event types: progress, result, done.
"""
import json
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://hybrid-brain-scan-1.preview.emergentagent.com").rstrip("/")
ENDPOINT = f"{BASE_URL}/api/stock-finder/scan"


def _consume_sse(cap: str, max_seconds: int = 180):
    """Open SSE stream, parse events, return list of dicts."""
    events = []
    with requests.get(f"{ENDPOINT}?cap={cap}", stream=True, timeout=max_seconds) as r:
        assert r.status_code == 200, f"Bad status: {r.status_code}"
        # Check content-type for SSE
        ctype = r.headers.get("content-type", "")
        assert "text/event-stream" in ctype, f"Wrong content-type: {ctype}"

        start = time.time()
        for raw_line in r.iter_lines(decode_unicode=True):
            if time.time() - start > max_seconds:
                break
            if not raw_line:
                continue
            if raw_line.startswith("data:"):
                payload = raw_line[5:].strip()
                try:
                    evt = json.loads(payload)
                except Exception as e:
                    pytest.fail(f"Event JSON parse failed: {payload[:120]} -> {e}")
                # NaN/Infinity check: json.loads accepts NaN by default; reject if any leaked
                assert "NaN" not in payload and "Infinity" not in payload, \
                    f"Floats not sanitized: {payload[:120]}"
                events.append(evt)
                if evt.get("type") == "done":
                    break
    return events


# ---------- cap=large (40 stocks, ~30-60s) ----------
class TestStockFinderLargeCap:
    """Tests for cap=large filter."""

    @classmethod
    def setup_class(cls):
        cls.events = _consume_sse("large", max_seconds=180)

    def test_received_events(self):
        assert len(self.events) > 0, "No SSE events received"

    def test_done_event_present(self):
        dones = [e for e in self.events if e.get("type") == "done"]
        assert len(dones) == 1, f"Expected exactly 1 done event, got {len(dones)}"

    def test_total_scanned_matches_large_universe(self):
        done = next(e for e in self.events if e.get("type") == "done")
        assert done["total_scanned"] == 40, f"Large cap should scan 40, got {done['total_scanned']}"

    def test_progress_events_count(self):
        progs = [e for e in self.events if e.get("type") == "progress"]
        assert len(progs) == 40, f"Expected 40 progress events, got {len(progs)}"

    def test_progress_event_fields(self):
        prog = next(e for e in self.events if e.get("type") == "progress")
        assert "current" in prog and "total" in prog and "symbol" in prog
        assert prog["total"] == 40

    def test_result_event_schema(self):
        results = [e for e in self.events if e.get("type") == "result"]
        if not results:
            pytest.skip("No result signals returned by live market — schema check skipped")
        r = results[0]
        for field in ("ticker", "name", "cap", "current_price", "signals",
                      "best_direction", "best_entry", "best_sl", "best_target",
                      "best_confidence", "strategies"):
            assert field in r, f"Missing field {field} in result: {r}"

    def test_best_direction_is_buy_or_sell_only(self):
        results = [e for e in self.events if e.get("type") == "result"]
        if not results:
            pytest.skip("No results to check direction")
        for r in results:
            assert r["best_direction"] in ("BUY", "SELL"), \
                f"Invalid direction {r['best_direction']} for {r['ticker']}"

    def test_result_cap_is_large(self):
        results = [e for e in self.events if e.get("type") == "result"]
        if not results:
            pytest.skip("No results to verify cap")
        for r in results:
            assert r["cap"] == "large", f"Expected cap=large, got {r['cap']} for {r['ticker']}"

    def test_done_total_found_matches_result_count(self):
        done = next(e for e in self.events if e.get("type") == "done")
        result_count = sum(1 for e in self.events if e.get("type") == "result")
        assert done["total_found"] == result_count


# ---------- cap=mid (39 stocks) ----------
class TestStockFinderMidCap:
    """Quick check that mid-cap filter slices universe correctly."""

    def test_mid_cap_total_scanned(self):
        events = _consume_sse("mid", max_seconds=180)
        done = next((e for e in events if e.get("type") == "done"), None)
        assert done is not None, "No done event received for mid cap"
        assert done["total_scanned"] == 39, f"Mid cap should scan 39, got {done['total_scanned']}"
        # Verify any returned results actually have cap=mid
        for e in events:
            if e.get("type") == "result":
                assert e["cap"] == "mid"


# ---------- cap validation ----------
class TestStockFinderResponseHeaders:
    def test_sse_headers(self):
        with requests.get(f"{ENDPOINT}?cap=large", stream=True, timeout=10) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers.get("content-type", "")
            assert "no-cache" in r.headers.get("cache-control", "").lower()
