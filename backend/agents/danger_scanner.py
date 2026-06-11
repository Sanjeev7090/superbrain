"""
Danger Mode Scanner
====================
Activated when risk_tolerance == "danger".

Scans the FULL F&O universe (indices + liquid stocks) and ranks picks by:
  1. Momentum score   (5d price return, volume spike, ATR%, RSI band)
  2. PCR parity boost (Put-Call parity mispricing → directional edge)
  3. OI change        (rising OI in direction = institutional conviction)

Returns the top N picks for the trading loop to use instead of the
configured watchlist. Direct equity trades are blocked; only F&O
underlyings are eligible.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("danger_scanner")

# ─── Full F&O Universe (indices first, then liquid stocks) ────────────────────
FNO_UNIVERSE: List[Dict] = [
    # ── Index underlyings (highest priority) ──────────────────────────────────
    {"ticker": "NIFTY",      "yf": "^NSEI",                  "type": "index", "sector": "Index"},
    {"ticker": "BANKNIFTY",  "yf": "^NSEBANK",               "type": "index", "sector": "Index"},
    {"ticker": "FINNIFTY",   "yf": "NIFTY_FIN_SERVICE.NS",   "type": "index", "sector": "Index"},
    {"ticker": "MIDCPNIFTY", "yf": "NIFTY_MID_SELECT.NS",    "type": "index", "sector": "Index"},
    {"ticker": "SENSEX",     "yf": "^BSESN",                 "type": "index", "sector": "Index"},
    # ── High-liquidity F&O stocks ─────────────────────────────────────────────
    {"ticker": "RELIANCE.NS",   "yf": "RELIANCE.NS",   "type": "stock", "sector": "Energy"},
    {"ticker": "HDFCBANK.NS",   "yf": "HDFCBANK.NS",   "type": "stock", "sector": "Banking"},
    {"ticker": "ICICIBANK.NS",  "yf": "ICICIBANK.NS",  "type": "stock", "sector": "Banking"},
    {"ticker": "SBIN.NS",       "yf": "SBIN.NS",       "type": "stock", "sector": "Banking"},
    {"ticker": "AXISBANK.NS",   "yf": "AXISBANK.NS",   "type": "stock", "sector": "Banking"},
    {"ticker": "KOTAKBANK.NS",  "yf": "KOTAKBANK.NS",  "type": "stock", "sector": "Banking"},
    {"ticker": "INDUSINDBK.NS", "yf": "INDUSINDBK.NS", "type": "stock", "sector": "Banking"},
    {"ticker": "INFY.NS",       "yf": "INFY.NS",       "type": "stock", "sector": "IT"},
    {"ticker": "TCS.NS",        "yf": "TCS.NS",        "type": "stock", "sector": "IT"},
    {"ticker": "HCLTECH.NS",    "yf": "HCLTECH.NS",    "type": "stock", "sector": "IT"},
    {"ticker": "WIPRO.NS",      "yf": "WIPRO.NS",      "type": "stock", "sector": "IT"},
    {"ticker": "BAJFINANCE.NS", "yf": "BAJFINANCE.NS", "type": "stock", "sector": "NBFC"},
    {"ticker": "LT.NS",         "yf": "LT.NS",         "type": "stock", "sector": "Infra"},
    {"ticker": "MARUTI.NS",     "yf": "MARUTI.NS",     "type": "stock", "sector": "Auto"},
    {"ticker": "TATAMOTORS.NS", "yf": "TATAMOTORS.NS", "type": "stock", "sector": "Auto"},
    {"ticker": "M&M.NS",        "yf": "M&M.NS",        "type": "stock", "sector": "Auto"},
    {"ticker": "TATASTEEL.NS",  "yf": "TATASTEEL.NS",  "type": "stock", "sector": "Metals"},
    {"ticker": "JSWSTEEL.NS",   "yf": "JSWSTEEL.NS",   "type": "stock", "sector": "Metals"},
    {"ticker": "SUNPHARMA.NS",  "yf": "SUNPHARMA.NS",  "type": "stock", "sector": "Pharma"},
    {"ticker": "DRREDDY.NS",    "yf": "DRREDDY.NS",    "type": "stock", "sector": "Pharma"},
    {"ticker": "CIPLA.NS",      "yf": "CIPLA.NS",      "type": "stock", "sector": "Pharma"},
    {"ticker": "ITC.NS",        "yf": "ITC.NS",        "type": "stock", "sector": "FMCG"},
    {"ticker": "BHARTIARTL.NS", "yf": "BHARTIARTL.NS", "type": "stock", "sector": "Telecom"},
    {"ticker": "NTPC.NS",       "yf": "NTPC.NS",       "type": "stock", "sector": "Power"},
    {"ticker": "ADANIPORTS.NS", "yf": "ADANIPORTS.NS", "type": "stock", "sector": "Ports"},
    {"ticker": "DLF.NS",        "yf": "DLF.NS",        "type": "stock", "sector": "Realty"},
    {"ticker": "TRENT.NS",      "yf": "TRENT.NS",      "type": "stock", "sector": "Retail"},
    {"ticker": "PERSISTENT.NS", "yf": "PERSISTENT.NS", "type": "stock", "sector": "IT"},
    {"ticker": "LTIM.NS",       "yf": "LTIM.NS",       "type": "stock", "sector": "IT"},
]

# PCR signal → directional weight
_PCR_SIGNAL_WEIGHT = {
    "STRONGLY_BULLISH": 22,
    "BULLISH":          14,
    "NEUTRAL":           0,
    "BEARISH":         -10,
    "STRONGLY_BEARISH": -18,
}

_SCAN_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 90.0   # 90s — danger mode rescans more frequently


def _score_one(meta: dict) -> Optional[dict]:
    """Compute momentum + vol score for one ticker. Returns None on failure."""
    try:
        import yfinance as yf
        import numpy as np

        yt = meta["yf"]
        hist = yf.Ticker(yt).history(period="30d", interval="1d", timeout=8)
        if len(hist) < 5:
            return None

        closes = hist["Close"].astype(float)
        vols   = hist["Volume"].astype(float)

        c_last  = float(closes.iloc[-1])
        c_prev  = float(closes.iloc[-2]) if len(closes) >= 2 else c_last
        c_5ago  = float(closes.iloc[-5]) if len(closes) >= 5 else c_last
        c_20ago = float(closes.iloc[-20]) if len(closes) >= 20 else c_last

        # 1. Momentum (5d & 20d)
        r5  = (c_last / c_5ago  - 1.0) * 100 if c_5ago  > 0 else 0.0
        r20 = (c_last / c_20ago - 1.0) * 100 if c_20ago > 0 else 0.0
        mom_score = min(40, max(0, (r5 * 3.0 + r20 * 0.8 + 5.0)))

        # 2. Volume spike (vs 10d avg)
        v_last   = float(vols.iloc[-1])
        v_10_avg = float(vols.tail(10).mean()) if len(vols) >= 10 else float(vols.mean()) or 1.0
        vol_ratio = v_last / v_10_avg if v_10_avg > 0 else 1.0
        vol_score = min(30, max(0, (vol_ratio - 1.0) * 20))

        # 3. ATR% (volatility reward in danger mode — more volatile = more edge)
        ret = closes.pct_change().dropna().tail(14)
        atr_pct = float(ret.std()) if len(ret) >= 3 else 0.015
        atr_score = min(20, atr_pct * 600)

        # 4. RSI-band: 55-70 zone = sweet spot for BUY
        delta = closes.diff()
        gain  = delta.clip(lower=0).tail(14).mean()
        loss  = (-delta.clip(upper=0)).tail(14).mean()
        rsi   = (100 - 100 / (1 + gain / loss)) if loss > 0 else 50.0
        rsi_score = 10.0 if 55 <= rsi <= 72 else 5.0 if 45 <= rsi < 55 else 0.0

        total = mom_score + vol_score + atr_score + rsi_score

        return {
            "ticker":    meta["ticker"],
            "yf":        meta["yf"],
            "type":      meta["type"],
            "sector":    meta["sector"],
            "price":     round(c_last, 2),
            "change_1d": round((c_last / c_prev - 1.0) * 100, 2) if c_prev else 0.0,
            "r5d":       round(r5, 2),
            "r20d":      round(r20, 2),
            "vol_ratio": round(vol_ratio, 2),
            "atr_pct":   round(atr_pct, 4),
            "rsi":       round(float(rsi), 1),
            "mom_score": round(mom_score, 1),
            "vol_score": round(vol_score, 1),
            "atr_score": round(atr_score, 1),
            "rsi_score": round(rsi_score, 1),
            "raw_score": round(total, 1),
            "pcr_boost": 0,    # filled later
            "final_score": round(total, 1),
            "pcr_signal":  "NEUTRAL",
            "pcr_conf":    50,
        }
    except Exception as e:
        logger.debug("[DangerScan] %s score failed: %s", meta.get("ticker"), e)
        return None


def _fetch_pcr_boosts() -> Dict[str, Tuple[int, str, int]]:
    """
    Returns {underlying → (boost_pts, signal_label, signal_confidence)}
    Covers NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY.
    """
    boosts: Dict[str, Tuple[int, str, int]] = {}
    try:
        import sys
        from pathlib import Path
        p = str(Path(__file__).parent.parent)
        if p not in sys.path:
            sys.path.insert(0, p)
        from server import _fetch_nse_option_chain, _extract_option_rows

        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            try:
                oi_data = _fetch_nse_option_chain(sym)
                if not oi_data or not oi_data.get("records", {}).get("data"):
                    continue
                opts, spot, expiry, _ = _extract_option_rows(oi_data, sym, nearest_only=True)
                total_call_oi = sum(float(o.get("oi", 0) or 0) for o in opts if o.get("type") == "CE")
                total_put_oi  = sum(float(o.get("oi", 0) or 0) for o in opts if o.get("type") == "PE")
                pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
                if pcr >= 1.3:
                    sig, conf = "STRONGLY_BULLISH", 92
                elif pcr >= 1.1:
                    sig, conf = "BULLISH", 75
                elif pcr >= 0.85:
                    sig, conf = "NEUTRAL", 50
                elif pcr >= 0.65:
                    sig, conf = "BEARISH", 30
                else:
                    sig, conf = "STRONGLY_BEARISH", 15
                boosts[sym] = (_PCR_SIGNAL_WEIGHT.get(sig, 0), sig, conf)
            except Exception as e:
                logger.debug("[DangerScan] PCR for %s failed: %s", sym, e)
    except Exception as e:
        logger.debug("[DangerScan] PCR import failed: %s", e)
    return boosts


def run_danger_scan(top_n: int = 5, force: bool = False) -> List[dict]:
    """
    Full Danger-mode F&O universe scan.
    Scores all tickers, applies PCR parity boost, returns top_n sorted picks.
    Results cached for 90s.
    """
    now = time.time()
    if not force and _SCAN_CACHE["data"] and (now - _SCAN_CACHE["ts"]) < _CACHE_TTL:
        return _SCAN_CACHE["data"][:top_n]

    t0 = time.time()
    results: List[dict] = []

    # 1. Score all tickers in parallel
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_score_one, m): m for m in FNO_UNIVERSE}
        for fut in futures:
            r = fut.result()
            if r:
                results.append(r)

    if not results:
        logger.warning("[DangerScan] No results scored!")
        return []

    # 2. Fetch PCR boosts for index underlyings
    pcr_boosts = _fetch_pcr_boosts()
    for row in results:
        base = row["ticker"].replace(".NS", "").replace(".BO", "").upper()
        if base in pcr_boosts:
            boost, sig, conf = pcr_boosts[base]
            row["pcr_boost"]   = boost
            row["pcr_signal"]  = sig
            row["pcr_conf"]    = conf
            row["final_score"] = round(row["raw_score"] + boost, 1)
        else:
            row["final_score"] = row["raw_score"]

    # 3. Sort by final score descending
    results.sort(key=lambda r: r["final_score"], reverse=True)

    elapsed = round(time.time() - t0, 2)
    logger.info(
        "[DangerScan] Scanned %d tickers in %.1fs → top pick: %s (%.1f)",
        len(results), elapsed, results[0]["ticker"] if results else "—", results[0]["final_score"] if results else 0,
    )

    _SCAN_CACHE["ts"]   = now
    _SCAN_CACHE["data"] = results

    return results[:top_n]


async def async_danger_scan(top_n: int = 5, force: bool = False) -> List[dict]:
    """Async wrapper — runs scan in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_danger_scan, top_n, force)
