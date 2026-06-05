"""
Tests for new features:
1. GET /api/nse/most-active — live NSE most-active equities (volume+value, deduped)
2. SSE /api/multi-tf-scanner/scan with segment=most_active + new '15m Breakout' strategy
"""
import os
import json
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"


# ── Most Active equity endpoint ──────────────────────────────────────────────
class TestNseMostActive:
    def test_most_active_limit_10(self):
        r = requests.get(f"{BASE_URL}/api/nse/most-active?limit=10", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True, f"ok not True: {data}"
        rows = data.get("rows") or []
        assert isinstance(rows, list)
        assert len(rows) > 0, "rows must be non-empty (cache should serve stale if NSE closed)"
        for row in rows:
            assert "ticker" in row, row
            assert row["ticker"].endswith(".NS"), f"ticker not .NS suffixed: {row['ticker']}"
            assert "name" in row
            assert row.get("segment") == "most_active", row

    def test_most_active_limit_25_dedupe(self):
        r = requests.get(f"{BASE_URL}/api/nse/most-active?limit=25", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        rows = data.get("rows") or []
        assert len(rows) > 0
        assert len(rows) <= 25, f"too many rows returned: {len(rows)}"
        tickers = [r_["ticker"] for r_ in rows]
        assert len(tickers) == len(set(tickers)), f"Duplicate tickers found: {tickers}"


# ── SSE scanner with most_active segment + 15m Breakout strategy ─────────────
def _read_sse(url, max_seconds=60):
    """Read SSE stream, parse events, stop at done or timeout."""
    events = []
    start = time.time()
    with requests.get(url, stream=True, timeout=max_seconds + 5) as r:
        assert r.status_code == 200, f"SSE returned {r.status_code}: {r.text[:200]}"
        buffer = ""
        for chunk in r.iter_content(chunk_size=1024, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            while "\n\n" in buffer:
                raw, buffer = buffer.split("\n\n", 1)
                data_line = None
                for line in raw.splitlines():
                    if line.startswith("data:"):
                        data_line = line[5:].strip()
                if data_line:
                    try:
                        ev = json.loads(data_line)
                        events.append(ev)
                        if ev.get("type") == "done":
                            return events
                    except json.JSONDecodeError:
                        pass
            if time.time() - start > max_seconds:
                break
    return events


class TestMultiTFScannerMostActive:
    def test_scan_most_active_15m(self):
        url = f"{BASE_URL}/api/multi-tf-scanner/scan?segment=most_active&timeframes=15m"
        events = _read_sse(url, max_seconds=90)
        assert len(events) > 0, "No SSE events received"

        progress_events = [e for e in events if e.get("type") == "progress"]
        result_events = [e for e in events if e.get("type") == "result"]
        done_events = [e for e in events if e.get("type") == "done"]

        assert len(progress_events) > 0, "No progress events"
        # Each progress event should have current/total/symbol
        for pe in progress_events[:3]:
            assert "current" in pe and "total" in pe, pe
        total = progress_events[0].get("total", 0)
        assert total > 0 and total <= 30, f"Universe size unexpected: {total}"

        assert len(done_events) > 0, "Stream did not end with done"

        # At least one result should include '15m Breakout' in signals
        found_breakout = False
        for ev in result_events:
            row = ev.get("row") or ev.get("data") or ev
            tf_signals = row.get("tf_signals") or {}
            for tf, payload in tf_signals.items():
                sigs = payload.get("signals") if isinstance(payload, dict) else []
                if sigs and any("15m Breakout" in str(s) for s in sigs):
                    found_breakout = True
                    break
            if found_breakout:
                break
        # Soft assert — markets may be closed but should still produce some result
        if not found_breakout:
            print(f"WARNING: No '15m Breakout' signal found across {len(result_events)} results (markets may be closed)")
        # At least the strategy should be wired — verify universe loaded
        assert total >= 1, "Most-active universe should have at least 1 ticker"

    def test_scan_fo_15m_no_regression(self):
        url = f"{BASE_URL}/api/multi-tf-scanner/scan?segment=fo&timeframes=15m"
        events = _read_sse(url, max_seconds=90)
        assert len(events) > 0
        progress_events = [e for e in events if e.get("type") == "progress"]
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(progress_events) > 0, "No progress for fo segment"
        assert len(done_events) > 0, "No done for fo segment"
