"""
DeltaDash Analysis Scoreboard
Multi-timeframe technical scoring engine for NSE stocks + indices.

Columns: Name | Total | Oly (Daily) | I-125 | I-75 | I-25 | I-15 | I-5 | CurrRate | RsChg | %Chg | Oly14ATR
Each timeframe score: 0-50 (5 indicators × 10 pts each)
Total max: 300 (6 timeframes × 50)
"""

import math
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

import yfinance as yf
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deltadash", tags=["deltadash"])

# ── Universe ──────────────────────────────────────────────────────
INDICES = [
    {"name": "NIFTY",      "ticker": "^NSEI",    "display": "NIFTY 50"},
    {"name": "BANKNIFTY",  "ticker": "^NSEBANK",  "display": "BANK NIFTY"},
    {"name": "MIDCPNIFTY", "ticker": "^CNXMID",   "display": "MIDCP NIFTY"},
    {"name": "FINNIFTY",   "ticker": "^CNXFIN",   "display": "FIN NIFTY"},
]

STOCKS_UNIVERSE = [
    {"name": "LT",          "ticker": "LT.NS"},
    {"name": "ICICIBANK",   "ticker": "ICICIBANK.NS"},
    {"name": "RELIANCE",    "ticker": "RELIANCE.NS"},
    {"name": "HDFCBANK",    "ticker": "HDFCBANK.NS"},
    {"name": "INFY",        "ticker": "INFY.NS"},
    {"name": "WIPRO",       "ticker": "WIPRO.NS"},
    {"name": "TCS",         "ticker": "TCS.NS"},
    {"name": "AXISBANK",    "ticker": "AXISBANK.NS"},
    {"name": "SBIN",        "ticker": "SBIN.NS"},
    {"name": "KOTAKBANK",   "ticker": "KOTAKBANK.NS"},
    {"name": "HINDUNILVR",  "ticker": "HINDUNILVR.NS"},
    {"name": "BAJFINANCE",  "ticker": "BAJFINANCE.NS"},
    {"name": "MARUTI",      "ticker": "MARUTI.NS"},
    {"name": "EICHERMOT",   "ticker": "EICHERMOT.NS"},
    {"name": "APOLLOHOSP",  "ticker": "APOLLOHOSP.NS"},
    {"name": "SUNPHARMA",   "ticker": "SUNPHARMA.NS"},
    {"name": "TATAMOTORS",  "ticker": "TATAMOTORS.NS"},
    {"name": "ADANIPORTS",  "ticker": "ADANIPORTS.NS"},
    {"name": "DRREDDY",     "ticker": "DRREDDY.NS"},
    {"name": "CIPLA",       "ticker": "CIPLA.NS"},
    {"name": "PIDILITIND",  "ticker": "PIDILITIND.NS"},
    {"name": "POLYCAB",     "ticker": "POLYCAB.NS"},
    {"name": "MUTHOOTFIN",  "ticker": "MUTHOOTFIN.NS"},
    {"name": "TORNTPHARM",  "ticker": "TORNTPHARM.NS"},
    {"name": "IDFCFIRSTB",  "ticker": "IDFCFIRSTB.NS"},
    {"name": "INDUSINDBK",  "ticker": "INDUSINDBK.NS"},
    {"name": "M&M",         "ticker": "M&M.NS"},
    {"name": "HEROMOTOCO",  "ticker": "HEROMOTOCO.NS"},
    {"name": "ITC",         "ticker": "ITC.NS"},
    {"name": "BHARTIARTL",  "ticker": "BHARTIARTL.NS"},
    {"name": "HCLTECH",     "ticker": "HCLTECH.NS"},
    {"name": "TECHM",       "ticker": "TECHM.NS"},
    {"name": "TITAN",       "ticker": "TITAN.NS"},
    {"name": "NTPC",        "ticker": "NTPC.NS"},
    {"name": "POWERGRID",   "ticker": "POWERGRID.NS"},
    {"name": "ONGC",        "ticker": "ONGC.NS"},
    {"name": "JSWSTEEL",    "ticker": "JSWSTEEL.NS"},
    {"name": "TATASTEEL",   "ticker": "TATASTEEL.NS"},
    {"name": "DIVISLAB",    "ticker": "DIVISLAB.NS"},
    {"name": "BAJAJ-AUTO",  "ticker": "BAJAJ-AUTO.NS"},
]

# ── Cache ─────────────────────────────────────────────────────────
_CACHE: Dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 300  # 5 minutes


# ── Technical Utilities ───────────────────────────────────────────

def _safe(v) -> float:
    try:
        f = float(v)
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return 0.0


def _ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k = 2.0 / (period + 1)
    val = sum(closes[:period]) / period
    for c in closes[period:]:
        val = c * k + val * (1 - k)
    return val


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1 + rs), 1)


def _macd(closes: list):
    """Returns (macd_line, signal_line, histogram) for last bar."""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_val = ema12 - ema26
    # Approximate signal as EMA9 of last N macd values
    macd_series = []
    for i in range(max(0, len(closes) - 40), len(closes)):
        e12 = _ema(closes[:i + 1], 12)
        e26 = _ema(closes[:i + 1], 26)
        macd_series.append(e12 - e26)
    signal = _ema(macd_series, 9) if len(macd_series) >= 9 else (macd_series[-1] if macd_series else 0.0)
    hist = macd_val - signal
    prev_hist = (macd_series[-2] - signal) if len(macd_series) >= 2 else hist
    return macd_val, signal, hist, prev_hist


def _atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if not trs:
        return 0.0
    return sum(trs[-period:]) / min(len(trs), period)


def _score_tf(bars: list) -> int:
    """
    Score one timeframe. bars = list of dicts {open, high, low, close, volume}.
    Returns int 0-50.
    """
    if len(bars) < 15:
        return 0

    closes  = [_safe(b["close"])  for b in bars]
    highs   = [_safe(b["high"])   for b in bars]
    lows    = [_safe(b["low"])    for b in bars]
    volumes = [_safe(b.get("volume", 0)) for b in bars]

    score = 0

    # 1. RSI (0-10)
    rsi = _rsi(closes, 14)
    if rsi >= 60:   score += 10
    elif rsi >= 52: score += 7
    elif rsi >= 45: score += 4
    elif rsi >= 38: score += 1

    # 2. MACD (0-10)
    try:
        _, _, hist, prev_hist = _macd(closes)
        if hist > 0 and hist > prev_hist: score += 10
        elif hist > 0:                    score += 7
        elif hist > prev_hist:            score += 3
    except Exception:
        pass

    # 3. EMA trend alignment (0-10)
    if len(closes) >= 50:
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        c = closes[-1]
        if c > ema20 > ema50:   score += 10
        elif c > ema20:         score += 6
        elif c > ema50:         score += 3

    # 4. Price momentum — 5-bar return (0-10)
    n5 = min(5, len(closes) - 1)
    if n5 > 0:
        ret5 = (closes[-1] - closes[-1 - n5]) / (closes[-1 - n5] + 1e-9)
        if ret5 >  0.015:  score += 10
        elif ret5 > 0.005: score += 7
        elif ret5 > 0.001: score += 4
        elif ret5 > 0.0:   score += 2

    # 5. Volume vs 20-bar avg (0-10)  — skip if no volume data (e.g. indices)
    non_zero_vols = [v for v in volumes[-20:] if v > 0]
    if len(non_zero_vols) >= 5:
        avg_vol  = sum(non_zero_vols) / len(non_zero_vols)
        last_vol = volumes[-1] if volumes[-1] > 0 else (non_zero_vols[-1] if non_zero_vols else 0)
        if avg_vol > 0:
            ratio = last_vol / avg_vol
            if ratio >= 1.5:   score += 10
            elif ratio >= 1.1: score += 7
            elif ratio >= 0.8: score += 4
    else:
        # No volume data (index) — add neutral 5 points so max possible stays balanced
        score += 5

    return min(50, score)


def _score_oly(daily_bars: list) -> int:
    """
    Oly = Daily-frame technical score (0-50).
    Uses same 5-indicator framework but on daily data.
    """
    return _score_tf(daily_bars)


def _atr14_daily(daily_bars: list) -> float:
    if len(daily_bars) < 2:
        return 0.0
    highs  = [_safe(b["high"])  for b in daily_bars]
    lows   = [_safe(b["low"])   for b in daily_bars]
    closes = [_safe(b["close"]) for b in daily_bars]
    return round(_atr(highs, lows, closes, 14), 2)


def _yf_bars(ticker: str, interval: str, period: str) -> list:
    """Fetch OHLCV from yfinance, return list of bar dicts."""
    try:
        df = yf.download(ticker, interval=interval, period=period,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        bars = []
        for ts, row in df.iterrows():
            bars.append({
                "open":   _safe(row.get("open",  0)),
                "high":   _safe(row.get("high",  0)),
                "low":    _safe(row.get("low",   0)),
                "close":  _safe(row.get("close", 0)),
                "volume": _safe(row.get("volume",0)),
            })
        return bars
    except Exception as e:
        logger.debug("yf_bars %s %s: %s", ticker, interval, e)
        return []


def _score_one(meta: dict) -> Optional[dict]:
    """Compute full DeltaDash row for one symbol. Returns None on error."""
    ticker = meta["ticker"]
    name   = meta["name"]
    try:
        # Fetch once per resolution needed
        bars_5m  = _yf_bars(ticker, "5m",  "5d")
        bars_15m = _yf_bars(ticker, "15m", "5d")
        bars_30m = _yf_bars(ticker, "30m", "5d")
        bars_60m = _yf_bars(ticker, "60m", "1mo")
        bars_90m = _yf_bars(ticker, "90m", "1mo")
        bars_1d  = _yf_bars(ticker, "1d",  "3mo")

        s5   = _score_tf(bars_5m)
        s15  = _score_tf(bars_15m)
        s25  = _score_tf(bars_30m)    # closest to I-25
        s75  = _score_tf(bars_60m)    # closest to I-75
        s125 = _score_tf(bars_90m)    # closest to I-125
        oly  = _score_oly(bars_1d)
        total = oly + s125 + s75 + s25 + s15 + s5

        # Current price, change
        curr_rate = 0.0
        rs_chg    = 0.0
        pct_chg   = 0.0
        if bars_1d and len(bars_1d) >= 2:
            curr_rate = round(bars_1d[-1]["close"], 2)
            prev      = bars_1d[-2]["close"]
            rs_chg    = round(curr_rate - prev, 2)
            pct_chg   = round((rs_chg / prev * 100) if prev else 0.0, 2)
        elif bars_5m:
            curr_rate = round(bars_5m[-1]["close"], 2)

        atr14 = _atr14_daily(bars_1d)

        return {
            "name":      name,
            "ticker":    ticker,
            "total":     total,
            "oly":       oly,
            "i125":      s125,
            "i75":       s75,
            "i25":       s25,
            "i15":       s15,
            "i5":        s5,
            "curr_rate": curr_rate,
            "rs_chg":    rs_chg,
            "pct_chg":   pct_chg,
            "atr14":     atr14,
        }
    except Exception as e:
        logger.warning("DeltaDash score_one %s: %s", ticker, e)
        return None


# ── API Endpoint ──────────────────────────────────────────────────

@router.get("/scoreboard")
async def get_scoreboard(refresh: bool = Query(False)):
    """
    Returns DeltaDash multi-timeframe scoreboard.
    Cached for 5 minutes. Pass ?refresh=true to force re-scan.
    """
    global _CACHE
    now = time.time()
    if not refresh and _CACHE["data"] and (now - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]

    all_metas = INDICES + STOCKS_UNIVERSE
    results_indices = []
    results_stocks  = []

    with ThreadPoolExecutor(max_workers=10) as exe:
        futures = {exe.submit(_score_one, m): m for m in all_metas}
        for fut in as_completed(futures):
            meta = futures[fut]
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                if meta in INDICES:
                    results_indices.append(row)
                else:
                    results_stocks.append(row)

    # Sort indices by original order
    idx_order = {m["name"]: i for i, m in enumerate(INDICES)}
    results_indices.sort(key=lambda r: idx_order.get(r["name"], 99))

    # Sort stocks by total score descending
    results_stocks.sort(key=lambda r: r["total"], reverse=True)

    payload = {
        "indices":      results_indices,
        "stocks":       results_stocks,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "universe_size": len(all_metas),
    }
    _CACHE["ts"]   = now
    _CACHE["data"] = payload
    return payload
