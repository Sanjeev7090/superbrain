"""
NSE Tick Streamer
=================
FastAPI WebSocket-based real-time price streaming.

Architecture:
  - Single shared background task polls yfinance fast_info every 2 seconds
  - All connected WebSocket clients receive pushed tick data
  - Supports: NSE equities (.NS), NSE indices (NIFTY, BANKNIFTY, SENSEX)
  - No API key required — uses yfinance public data

Endpoint:  /api/ws/nse-tick
Subscribe: {"action":"subscribe","tickers":["RELIANCE.NS","^NSEI"]}
Tick msg:  {"type":"tick","ticker":"RELIANCE.NS","data":{price,change,change_pct,...}}
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger("tick_streamer")

# ── Index symbol map ──────────────────────────────────────────────────────────
INDEX_MAP: Dict[str, str] = {
    "NIFTY":        "^NSEI",
    "NIFTY50":      "^NSEI",
    "BANKNIFTY":    "^NSEBANK",
    "BANK NIFTY":   "^NSEBANK",
    "FINNIFTY":     "NIFTY_FIN_SERVICE.NS",
    "SENSEX":       "^BSESN",
    "MIDCPNIFTY":   "NIFTY_MID_SELECT.NS",
    "MIDCAP":       "NIFTY_MID_SELECT.NS",
}

POLL_INTERVAL   = 2.0    # seconds between price refreshes
MAX_WORKERS     = 8      # thread pool size for yfinance fetches
STALE_THRESHOLD = 30.0   # seconds before a tick is considered stale


def _normalize_ticker(raw: str) -> str:
    """Map user-friendly name to yfinance symbol."""
    key = raw.upper().replace(".NS", "").replace(".BO", "").strip()
    if key in INDEX_MAP:
        return INDEX_MAP[key]
    # Equity: ensure .NS suffix
    if not raw.endswith(".NS") and not raw.endswith(".BO") and not raw.startswith("^"):
        return raw.upper() + ".NS"
    return raw.upper()


def _fetch_fast(ticker_sym: str) -> Optional[Dict[str, Any]]:
    """Fetch live tick using yfinance fast_info. Returns None on error."""
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker_sym).fast_info
        lp = float(fi.last_price or 0)
        if lp <= 0:
            return None
        pc = float(fi.previous_close or lp)
        chg     = lp - pc
        chg_pct = (chg / pc * 100) if pc else 0.0
        return {
            "price":      round(lp, 2),
            "prev_close": round(pc, 2),
            "change":     round(chg, 2),
            "change_pct": round(chg_pct, 2),
            "high":       round(float(fi.day_high  or lp), 2),
            "low":        round(float(fi.day_low   or lp), 2),
            "volume":     int(fi.last_volume or 0),
            "open":       round(float(fi.open or lp), 2),
            "ts":         datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.debug("[TickStreamer] fast_info error %s: %s", ticker_sym, e)
        return None


class NSETickStreamer:
    """Shared singleton — one poll loop, N WebSocket clients."""

    def __init__(self):
        # ticker_sym → last tick dict
        self._cache:       Dict[str, Dict] = {}
        # ws → set of subscribed ticker_syms
        self._connections: Dict[WebSocket, Set[str]] = {}
        self._running      = False
        self._task:        Optional[asyncio.Task] = None
        self._pool         = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="tick_")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def startup(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[TickStreamer] Started (poll_interval=%.1fs)", POLL_INTERVAL)

    async def shutdown(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self._pool.shutdown(wait=False)

    # ── WebSocket helpers ─────────────────────────────────────────────────────

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections[ws] = set()
        logger.info("[TickStreamer] Client connected | total=%d", len(self._connections))

    async def disconnect(self, ws: WebSocket):
        self._connections.pop(ws, None)
        logger.info("[TickStreamer] Client disconnected | remaining=%d", len(self._connections))

    async def subscribe(self, ws: WebSocket, raw_tickers: List[str]):
        syms = [_normalize_ticker(t) for t in raw_tickers]
        for sym in syms:
            self._connections[ws].add(sym)
            # Immediately push cached data if available
            if sym in self._cache:
                try:
                    await ws.send_json({"type": "tick", "ticker": sym, "data": self._cache[sym]})
                except Exception:
                    pass
        await ws.send_json({
            "type":    "subscribed",
            "tickers": syms,
            "count":   len(syms),
        })

    async def unsubscribe(self, ws: WebSocket, raw_tickers: List[str]):
        syms = {_normalize_ticker(t) for t in raw_tickers}
        if ws in self._connections:
            self._connections[ws] -= syms

    def get_cached(self, raw_ticker: str) -> Optional[Dict]:
        return self._cache.get(_normalize_ticker(raw_ticker))

    # ── Background poll loop ──────────────────────────────────────────────────

    async def _poll_loop(self):
        while self._running:
            try:
                # Collect all subscribed symbols across all clients
                active: Set[str] = set()
                for subs in self._connections.values():
                    active.update(subs)

                if active:
                    await self._fetch_and_broadcast(list(active))
            except Exception as e:
                logger.warning("[TickStreamer] Poll error: %s", e)

            await asyncio.sleep(POLL_INTERVAL)

    async def _fetch_and_broadcast(self, symbols: List[str]):
        loop = asyncio.get_event_loop()

        # Fetch all tickers concurrently via thread pool
        futures = {sym: loop.run_in_executor(self._pool, _fetch_fast, sym) for sym in symbols}
        results: Dict[str, Optional[Dict]] = {}
        for sym, fut in futures.items():
            try:
                results[sym] = await fut
            except Exception:
                results[sym] = None

        # Enrich with direction (up/down/flat) and update cache
        for sym, data in results.items():
            if data is None:
                continue
            old_price = self._cache.get(sym, {}).get("price", data["price"])
            data["direction"] = (
                "up"   if data["price"] > old_price else
                "down" if data["price"] < old_price else
                "flat"
            )
            self._cache[sym] = data

        # Broadcast to each connection
        dead: List[WebSocket] = []
        for ws, subs in list(self._connections.items()):
            for sym in subs:
                if results.get(sym) is None:
                    continue
                try:
                    await ws.send_json({"type": "tick", "ticker": sym, "data": results[sym]})
                except Exception:
                    dead.append(ws)
                    break

        for ws in dead:
            await self.disconnect(ws)


# ── Singleton ─────────────────────────────────────────────────────────────────
nse_tick_streamer = NSETickStreamer()
