# backend/agents/universe_scanner.py
"""
Universe Scanner — Full NSE + F&O stock scan via HybridSuperBrain

Super-fast parallel scan:
  1. Batch OHLCV fetch from yfinance (all tickers in one call)
  2. Compute technical indicators in numpy/pandas
  3. Run SMC + Kronos + DeltaDash + Brain lite decision for each stock
  4. Return ranked picks (BUY / SELL by confidence)

Target: 200+ stocks < 15 seconds
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger("universe_scanner")

# ─── NSE Universe ─────────────────────────────────────────────────────────────
# Equity + F&O eligible stocks (200 most traded)
NSE_UNIVERSE: List[Dict] = [
    # ── Nifty 50 / F&O ────────────────────────────────────────────────────────
    {"t": "RELIANCE.NS",    "n": "Reliance",         "seg": "fo"},
    {"t": "TCS.NS",         "n": "TCS",              "seg": "fo"},
    {"t": "HDFCBANK.NS",    "n": "HDFC Bank",        "seg": "fo"},
    {"t": "ICICIBANK.NS",   "n": "ICICI Bank",       "seg": "fo"},
    {"t": "INFY.NS",        "n": "Infosys",          "seg": "fo"},
    {"t": "BHARTIARTL.NS",  "n": "Bharti Airtel",    "seg": "fo"},
    {"t": "ITC.NS",         "n": "ITC",              "seg": "fo"},
    {"t": "BAJFINANCE.NS",  "n": "Bajaj Finance",    "seg": "fo"},
    {"t": "LT.NS",          "n": "L&T",              "seg": "fo"},
    {"t": "KOTAKBANK.NS",   "n": "Kotak Bank",       "seg": "fo"},
    {"t": "AXISBANK.NS",    "n": "Axis Bank",        "seg": "fo"},
    {"t": "HCLTECH.NS",     "n": "HCL Tech",         "seg": "fo"},
    {"t": "WIPRO.NS",       "n": "Wipro",            "seg": "fo"},
    {"t": "MARUTI.NS",      "n": "Maruti",           "seg": "fo"},
    {"t": "SBIN.NS",        "n": "SBI",              "seg": "fo"},
    {"t": "TATAMOTORS.NS",  "n": "Tata Motors",      "seg": "fo"},
    {"t": "SUNPHARMA.NS",   "n": "Sun Pharma",       "seg": "fo"},
    {"t": "TITAN.NS",       "n": "Titan",            "seg": "fo"},
    {"t": "NTPC.NS",        "n": "NTPC",             "seg": "fo"},
    {"t": "ONGC.NS",        "n": "ONGC",             "seg": "fo"},
    {"t": "COALINDIA.NS",   "n": "Coal India",       "seg": "fo"},
    {"t": "TATASTEEL.NS",   "n": "Tata Steel",       "seg": "fo"},
    {"t": "DRREDDY.NS",     "n": "Dr. Reddy's",      "seg": "fo"},
    {"t": "CIPLA.NS",       "n": "Cipla",            "seg": "fo"},
    {"t": "BAJAJ-AUTO.NS",  "n": "Bajaj Auto",       "seg": "fo"},
    {"t": "EICHERMOT.NS",   "n": "Eicher Motors",    "seg": "fo"},
    {"t": "HEROMOTOCO.NS",  "n": "Hero MotoCorp",    "seg": "fo"},
    {"t": "HINDALCO.NS",    "n": "Hindalco",         "seg": "fo"},
    {"t": "ASIANPAINT.NS",  "n": "Asian Paints",     "seg": "fo"},
    {"t": "ULTRACEMCO.NS",  "n": "UltraTech Cement", "seg": "fo"},
    {"t": "GRASIM.NS",      "n": "Grasim",           "seg": "fo"},
    {"t": "INDUSINDBK.NS",  "n": "IndusInd Bank",    "seg": "fo"},
    {"t": "JSWSTEEL.NS",    "n": "JSW Steel",        "seg": "fo"},
    {"t": "POWERGRID.NS",   "n": "Power Grid",       "seg": "fo"},
    {"t": "BPCL.NS",        "n": "BPCL",             "seg": "fo"},
    {"t": "TATACONSUM.NS",  "n": "Tata Consumer",    "seg": "fo"},
    {"t": "NESTLEIND.NS",   "n": "Nestle India",     "seg": "fo"},
    {"t": "APOLLOHOSP.NS",  "n": "Apollo Hospitals", "seg": "fo"},
    {"t": "ADANIPORTS.NS",  "n": "Adani Ports",      "seg": "fo"},
    {"t": "TATAPOWER.NS",   "n": "Tata Power",       "seg": "fo"},
    {"t": "DIVISLAB.NS",    "n": "Divi's Labs",      "seg": "fo"},
    {"t": "BAJAJFINSV.NS",  "n": "Bajaj Finserv",    "seg": "fo"},
    {"t": "SBILIFE.NS",     "n": "SBI Life",         "seg": "fo"},
    {"t": "HDFCLIFE.NS",    "n": "HDFC Life",        "seg": "fo"},
    {"t": "ICICIPRULI.NS",  "n": "ICICI Prudential", "seg": "fo"},
    {"t": "ADANIENT.NS",    "n": "Adani Enterprises","seg": "fo"},
    # ── Bank Nifty ────────────────────────────────────────────────────────────
    {"t": "BANKBARODA.NS",  "n": "Bank of Baroda",   "seg": "banknifty"},
    {"t": "IDFCFIRSTB.NS",  "n": "IDFC First Bank",  "seg": "banknifty"},
    {"t": "FEDERALBNK.NS",  "n": "Federal Bank",     "seg": "banknifty"},
    {"t": "PNB.NS",         "n": "Punjab National",  "seg": "banknifty"},
    {"t": "CANARABANK.NS",  "n": "Canara Bank",      "seg": "banknifty"},
    # ── Nifty Next 50 ─────────────────────────────────────────────────────────
    {"t": "HAVELLS.NS",     "n": "Havells",          "seg": "equity"},
    {"t": "PIIND.NS",       "n": "PI Industries",    "seg": "equity"},
    {"t": "TORNTPHARM.NS",  "n": "Torrent Pharma",   "seg": "equity"},
    {"t": "MCDOWELL-N.NS",  "n": "United Spirits",   "seg": "equity"},
    {"t": "BOSCHLTD.NS",    "n": "Bosch",            "seg": "equity"},
    {"t": "CHOLAFIN.NS",    "n": "Cholamandalam",    "seg": "fo"},
    {"t": "SBICARD.NS",     "n": "SBI Card",         "seg": "fo"},
    {"t": "NAUKRI.NS",      "n": "Info Edge",        "seg": "fo"},
    {"t": "DMART.NS",       "n": "Avenue Supermart", "seg": "fo"},
    {"t": "PAGEIND.NS",     "n": "Page Industries",  "seg": "equity"},
    {"t": "MUTHOOTFIN.NS",  "n": "Muthoot Finance",  "seg": "fo"},
    {"t": "PIDILITIND.NS",  "n": "Pidilite",         "seg": "fo"},
    {"t": "MARICO.NS",      "n": "Marico",           "seg": "fo"},
    {"t": "GODREJCP.NS",    "n": "Godrej Consumer",  "seg": "fo"},
    {"t": "BRITANNIA.NS",   "n": "Britannia",        "seg": "fo"},
    {"t": "DABUR.NS",       "n": "Dabur India",      "seg": "fo"},
    {"t": "COLPAL.NS",      "n": "Colgate-Palmolive","seg": "fo"},
    {"t": "BERGEPAINT.NS",  "n": "Berger Paints",    "seg": "fo"},
    {"t": "AMBUJACEM.NS",   "n": "Ambuja Cement",    "seg": "fo"},
    {"t": "ACC.NS",         "n": "ACC",              "seg": "fo"},
    {"t": "SHREECEM.NS",    "n": "Shree Cement",     "seg": "fo"},
    # ── Mid Cap F&O ───────────────────────────────────────────────────────────
    {"t": "MOTHERSON.NS",   "n": "Motherson Sumi",   "seg": "fo"},
    {"t": "BALKRISIND.NS",  "n": "Balkrishna Ind",   "seg": "fo"},
    {"t": "PERSISTENT.NS",  "n": "Persistent Sys",   "seg": "fo"},
    {"t": "COFORGE.NS",     "n": "Coforge",          "seg": "fo"},
    {"t": "MPHASIS.NS",     "n": "Mphasis",          "seg": "fo"},
    {"t": "LTIM.NS",        "n": "LTI Mindtree",     "seg": "fo"},
    {"t": "OFSS.NS",        "n": "Oracle Fin Svcs",  "seg": "fo"},
    {"t": "TATACOMM.NS",    "n": "Tata Comm",        "seg": "fo"},
    {"t": "INDUSTOWER.NS",  "n": "Indus Towers",     "seg": "fo"},
    {"t": "ZOMATO.NS",      "n": "Zomato",           "seg": "fo"},
    {"t": "PAYTM.NS",       "n": "Paytm",            "seg": "equity"},
    {"t": "NYKAA.NS",       "n": "Nykaa",            "seg": "equity"},
    {"t": "POLICYBZR.NS",   "n": "PB Fintech",       "seg": "equity"},
    {"t": "IRCTC.NS",       "n": "IRCTC",            "seg": "fo"},
    {"t": "IRFC.NS",        "n": "IRFC",             "seg": "fo"},
    {"t": "RVNL.NS",        "n": "RVNL",             "seg": "equity"},
    {"t": "CONCOR.NS",      "n": "Container Corp",   "seg": "fo"},
    {"t": "GAIL.NS",        "n": "GAIL",             "seg": "fo"},
    {"t": "IOC.NS",         "n": "Indian Oil",       "seg": "fo"},
    {"t": "HINDPETRO.NS",   "n": "HPCL",             "seg": "fo"},
    {"t": "TECHM.NS",       "n": "Tech Mahindra",    "seg": "fo"},
    {"t": "ZYDUSLIFE.NS",   "n": "Zydus Life",       "seg": "fo"},
    {"t": "LUPIN.NS",       "n": "Lupin",            "seg": "fo"},
    {"t": "AUROPHARMA.NS",  "n": "Aurobindo Pharma", "seg": "fo"},
    {"t": "BIOCON.NS",      "n": "Biocon",           "seg": "fo"},
    {"t": "TORNTPOWER.NS",  "n": "Torrent Power",    "seg": "fo"},
    {"t": "ADANIGREEN.NS",  "n": "Adani Green",      "seg": "fo"},
    {"t": "ADANIPOWER.NS",  "n": "Adani Power",      "seg": "fo"},
    {"t": "VEDL.NS",        "n": "Vedanta",          "seg": "fo"},
    {"t": "NMDC.NS",        "n": "NMDC",             "seg": "fo"},
    {"t": "SAIL.NS",        "n": "SAIL",             "seg": "fo"},
    {"t": "JINDALSTEL.NS",  "n": "Jindal Steel",     "seg": "fo"},
    {"t": "NATIONALUM.NS",  "n": "National Aluminium","seg": "fo"},
    {"t": "GMRINFRA.NS",    "n": "GMR Infrastructure","seg": "equity"},
    {"t": "AIAENG.NS",      "n": "AIA Engineering",  "seg": "fo"},
    {"t": "BHEL.NS",        "n": "BHEL",             "seg": "fo"},
    {"t": "BEL.NS",         "n": "BEL",              "seg": "fo"},
    {"t": "HAL.NS",         "n": "HAL",              "seg": "fo"},
    {"t": "CGPOWER.NS",     "n": "CG Power",         "seg": "fo"},
    {"t": "VOLTAS.NS",      "n": "Voltas",           "seg": "fo"},
    {"t": "WHIRLPOOL.NS",   "n": "Whirlpool",        "seg": "equity"},
    {"t": "DIXON.NS",       "n": "Dixon Tech",       "seg": "fo"},
    {"t": "KAYNES.NS",      "n": "Kaynes Tech",      "seg": "equity"},
    {"t": "APLAPOLLO.NS",   "n": "APL Apollo",       "seg": "fo"},
    {"t": "TATACHEM.NS",    "n": "Tata Chemicals",   "seg": "fo"},
    {"t": "UPL.NS",         "n": "UPL",              "seg": "fo"},
    {"t": "DEEPAKNTR.NS",   "n": "Deepak Nitrite",   "seg": "fo"},
    {"t": "NAVINFLUOR.NS",  "n": "Navin Fluorine",   "seg": "fo"},
    {"t": "SRF.NS",         "n": "SRF",              "seg": "fo"},
    {"t": "CASTROLIND.NS",  "n": "Castrol",          "seg": "fo"},
    {"t": "INDIAMART.NS",   "n": "IndiaMART",        "seg": "fo"},
    {"t": "ANGELONE.NS",    "n": "Angel One",        "seg": "fo"},
    {"t": "BSE.NS",         "n": "BSE",              "seg": "equity"},
    {"t": "MCX.NS",         "n": "MCX",              "seg": "fo"},
    {"t": "CDSL.NS",        "n": "CDSL",             "seg": "fo"},
    {"t": "CAMS.NS",        "n": "CAMS",             "seg": "fo"},
    {"t": "ABCAPITAL.NS",   "n": "Aditya Birla Cap", "seg": "fo"},
    {"t": "LICHSGFIN.NS",   "n": "LIC Housing",      "seg": "fo"},
    {"t": "M&MFIN.NS",      "n": "M&M Financial",    "seg": "fo"},
    {"t": "SHRIRAMFIN.NS",  "n": "Shriram Finance",  "seg": "fo"},
    {"t": "HDFCAMC.NS",     "n": "HDFC AMC",         "seg": "fo"},
    {"t": "NIPPONLIFE.NS",  "n": "Nippon Life AMC",  "seg": "fo"},
    {"t": "UTIAMC.NS",      "n": "UTI AMC",          "seg": "equity"},
    {"t": "M&M.NS",         "n": "Mahindra",         "seg": "fo"},
    {"t": "ASHOKLEY.NS",    "n": "Ashok Leyland",    "seg": "fo"},
    {"t": "SONACOMS.NS",    "n": "Sona BLW",         "seg": "fo"},
    {"t": "MINDAIND.NS",    "n": "UNO Minda",        "seg": "fo"},
    {"t": "TVSMOTOR.NS",    "n": "TVS Motor",        "seg": "fo"},
    {"t": "EXIDEIND.NS",    "n": "Exide Industries", "seg": "fo"},
    {"t": "AMARA.NS",       "n": "Amara Raja Energy","seg": "equity"},
    {"t": "RADICO.NS",      "n": "Radico Khaitan",   "seg": "fo"},
    {"t": "UNITDSPR.NS",    "n": "United Breweries", "seg": "equity"},
    {"t": "JUBLFOOD.NS",    "n": "Jubilant Foodworks","seg": "fo"},
    {"t": "DEVYANI.NS",     "n": "Devyani Int",      "seg": "equity"},
    {"t": "DELHIVERY.NS",   "n": "Delhivery",        "seg": "equity"},
    {"t": "CARTRADE.NS",    "n": "CarTrade Tech",    "seg": "equity"},
    {"t": "EASEMYTRIP.NS",  "n": "EaseMyTrip",       "seg": "equity"},
    {"t": "INDIGOPNTS.NS",  "n": "Indigo Paints",    "seg": "equity"},
    {"t": "LATENTVIEW.NS",  "n": "LatentView",       "seg": "equity"},
    {"t": "TATAELXSI.NS",   "n": "Tata Elxsi",       "seg": "fo"},
    {"t": "CYIENT.NS",      "n": "Cyient",           "seg": "fo"},
    {"t": "LTTS.NS",        "n": "L&T Technology",   "seg": "fo"},
    {"t": "KPITTECH.NS",    "n": "KPIT Tech",        "seg": "fo"},
    {"t": "HAPPSTMNDS.NS",  "n": "Happiest Minds",   "seg": "fo"},
    {"t": "TANLA.NS",       "n": "Tanla Platforms",  "seg": "fo"},
    {"t": "MAPMYINDIA.NS",  "n": "MapMyIndia",       "seg": "equity"},
    {"t": "GUJGASLTD.NS",   "n": "Gujarat Gas",      "seg": "fo"},
    {"t": "IGL.NS",         "n": "Indraprastha Gas", "seg": "fo"},
    {"t": "MGL.NS",         "n": "Mahanagar Gas",    "seg": "fo"},
    {"t": "PETRONET.NS",    "n": "Petronet LNG",     "seg": "fo"},
    {"t": "BANKBARODA.NS",  "n": "Bank of Baroda",   "seg": "fo"},
    {"t": "UNIONBANK.NS",   "n": "Union Bank",       "seg": "fo"},
    {"t": "BANKINDIA.NS",   "n": "Bank of India",    "seg": "fo"},
    {"t": "INDIANB.NS",     "n": "Indian Bank",      "seg": "fo"},
    {"t": "CENTRALBK.NS",   "n": "Central Bank",     "seg": "equity"},
    {"t": "RBLBANK.NS",     "n": "RBL Bank",         "seg": "fo"},
    {"t": "KARURVYSYA.NS",  "n": "Karur Vysya Bank", "seg": "equity"},
    {"t": "AUBANK.NS",      "n": "AU Small Finance",  "seg": "fo"},
    {"t": "EQUITASBNK.NS",  "n": "Equitas Bank",     "seg": "equity"},
    {"t": "UTKARSHBNK.NS",  "n": "Utkarsh Bank",     "seg": "equity"},
    {"t": "FINPIPE.NS",     "n": "Finolex Cables",   "seg": "fo"},
    {"t": "POLYCAB.NS",     "n": "Polycab India",    "seg": "fo"},
    {"t": "KEI.NS",         "n": "KEI Industries",   "seg": "fo"},
    {"t": "THERMAX.NS",     "n": "Thermax",          "seg": "fo"},
    {"t": "SIEMENS.NS",     "n": "Siemens India",    "seg": "fo"},
    {"t": "ABB.NS",         "n": "ABB India",        "seg": "fo"},
    {"t": "SCHNEIDER.NS",   "n": "Schneider Elec",   "seg": "equity"},
]

# Deduplicate
_seen = set()
_dedup = []
for s in NSE_UNIVERSE:
    if s["t"] not in _seen:
        _seen.add(s["t"])
        _dedup.append(s)
NSE_UNIVERSE = _dedup


# ─── Indicators ─────────────────────────────────────────────────────────────

def _compute_fast_indicators(close, high, low, volume) -> Dict[str, Any]:
    """Compute all indicators from numpy arrays. Returns dict."""
    n = len(close)
    if n < 15:
        return {}

    # RSI-14
    delta = np.diff(close)
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    avg_g = np.convolve(gain, np.ones(14)/14, mode='full')[13:n-1]
    avg_l = np.convolve(loss, np.ones(14)/14, mode='full')[13:n-1]
    rsi_val = 100.0 - 100.0 / (1.0 + avg_g[-1] / (avg_l[-1] + 1e-8)) if len(avg_g) > 0 else 50.0

    # ATR-14
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:]  - close[:-1]),
    ])
    atr14   = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))
    cur     = float(close[-1])
    atr_pct = atr14 / (cur + 1e-8)

    # EMA20 / SMA50 — regime
    ema20 = float(close[-1])
    alpha = 2.0 / 21.0
    for c in close[-min(40, n):]:
        ema20 = alpha * c + (1 - alpha) * ema20
    sma50 = float(np.mean(close[-50:])) if n >= 50 else float(np.mean(close))

    regime = ("UPTREND"   if ema20 > sma50 * 1.01
              else "DOWNTREND" if ema20 < sma50 * 0.99
              else "SIDEWAYS")

    # Volume thrust
    vol_avg = float(np.mean(volume[-20:])) if n >= 20 else float(np.mean(volume))
    vol_ratio = float(volume[-1]) / (vol_avg + 1e-8)

    # Momentum (5-day % change)
    mom5 = float((close[-1] - close[-6]) / (close[-6] + 1e-8) * 100) if n >= 6 else 0.0

    # SMC: BOS detection
    recent_hi = float(np.max(high[-5:]))
    recent_lo = float(np.min(low[-5:]))
    prev_hi   = float(np.max(high[-10:-5])) if n >= 10 else recent_hi
    prev_lo   = float(np.min(low[-10:-5]))  if n >= 10 else recent_lo
    bos_bull  = cur > prev_hi
    bos_bear  = cur < prev_lo

    # FVG check (3-candle gap)
    fvg = False
    if n >= 3:
        for i in range(max(0, n - 8), n - 2):
            if low[i+2] > high[i]:
                fvg = True
                break

    return {
        "price":     round(cur, 2),
        "rsi14":     round(float(rsi_val), 1),
        "atr14":     round(atr14, 2),
        "atr_pct":   round(atr_pct, 5),
        "regime":    regime,
        "ema20":     round(ema20, 2),
        "sma50":     round(sma50, 2),
        "vol_ratio": round(vol_ratio, 2),
        "mom5":      round(mom5, 2),
        "bos_bull":  bos_bull,
        "bos_bear":  bos_bear,
        "fvg":       fvg,
        "ob":        bos_bull or bos_bear,
    }


def _score_stock(ticker: str, name: str, seg: str, ind: Dict) -> Optional[Dict]:
    """Score a single stock and return result dict or None."""
    if not ind or ind.get("price", 0) <= 0:
        return None

    rsi    = ind.get("rsi14",   50.0)
    regime = ind.get("regime",  "SIDEWAYS")
    atr_p  = ind.get("atr_pct", 0.015)
    vol    = ind.get("vol_ratio", 1.0)
    mom5   = ind.get("mom5",    0.0)
    bos_b  = ind.get("bos_bull", False)
    bos_e  = ind.get("bos_bear", False)
    fvg    = ind.get("fvg",     False)
    ob     = ind.get("ob",      False)
    price  = ind.get("price",   0.0)
    atr14  = ind.get("atr14",   price * 0.015)

    # ── Bullish score (0–100) ─────────────────────────────────────────────────
    buy_score = 50.0
    if regime == "UPTREND":
        buy_score += 15.0
    elif regime == "DOWNTREND":
        buy_score -= 12.0

    if 40 < rsi < 65:
        buy_score += 10.0    # RSI sweet spot
    elif rsi < 30:
        buy_score += 8.0     # oversold bounce
    elif rsi > 75:
        buy_score -= 15.0    # overbought

    if vol > 1.8:
        buy_score += 8.0
    elif vol < 0.5:
        buy_score -= 5.0

    if mom5 > 1.5:
        buy_score += 8.0
    elif mom5 < -2.0:
        buy_score -= 8.0

    if bos_b:
        buy_score += 12.0
    if fvg:
        buy_score += 6.0
    if ob:
        buy_score += 4.0

    if atr_p > 0.055:
        buy_score -= 10.0    # extreme volatility

    # Kronos time advantage (IST = UTC+5:30)
    hour_ist = (datetime.utcnow().hour * 60 + datetime.utcnow().minute + 330) // 60 % 24
    if hour_ist < 9 or hour_ist >= 16:
        buy_score -= 10.0   # light penalty outside market, don't kill signal
    elif 14 <= hour_ist <= 15:
        buy_score += 5.0    # power hour bonus

    buy_score = max(20.0, min(97.0, buy_score))

    # ── Bearish score ────────────────────────────────────────────────────────
    sell_score = 100.0 - buy_score
    sell_score += (15.0 if bos_e else 0.0) + (6.0 if rsi > 70 else 0.0)
    sell_score = max(20.0, min(97.0, sell_score))

    # ── Action ───────────────────────────────────────────────────────────────
    if buy_score > 65.0:
        action, conf = "BUY",  round(buy_score, 1)
    elif sell_score > 65.0:
        action, conf = "SELL", round(sell_score, 1)
    else:
        action, conf = "HOLD", round(max(buy_score, sell_score), 1)

    if action == "HOLD":
        return None  # filter out HOLDs for clean results

    # SL / TP
    if action == "BUY":
        sl = round(price - atr14 * 2.0, 2)
        tp = round(price + atr14 * 3.0, 2)
    else:
        sl = round(price + atr14 * 2.0, 2)
        tp = round(price - atr14 * 3.0, 2)

    smc_sig = "bullish" if bos_b or fvg else ("bearish" if bos_e else "neutral")
    hour_ist = (datetime.utcnow().hour * 60 + datetime.utcnow().minute + 330) // 60 % 24
    if 9 <= hour_ist <= 10:
        kronos_p = "opening_auction"
    elif 10 <= hour_ist <= 14:
        kronos_p = "trending"
    elif 14 <= hour_ist <= 15:
        kronos_p = "power_hour"
    else:
        kronos_p = "closed"

    return {
        "ticker":       ticker,
        "name":         name,
        "segment":      seg,
        "action":       action,
        "confidence":   conf,
        "price":        price,
        "sl_price":     max(0.01, sl),
        "tp_price":     max(0.01, tp),
        "atr14":        round(atr14, 2),
        "rsi14":        round(rsi, 1),
        "regime":       regime,
        "vol_ratio":    round(vol, 2),
        "mom5":         round(mom5, 2),
        "smc_signal":   smc_sig,
        "kronos_phase": kronos_p,
        "bos":          bos_b or bos_e,
        "fvg":          fvg,
        "rr_ratio":     round(abs(tp - price) / (abs(price - sl) + 1e-8), 2),
    }


def _batch_fetch_indicators(tickers: List[str]) -> Dict[str, Any]:
    """
    Batch-fetch OHLCV for all tickers in one yfinance call.
    Returns {ticker: indicators_dict}
    """
    import yfinance as yf
    import pandas as pd

    try:
        raw = yf.download(
            tickers,
            period="60d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )
    except Exception as e:
        logger.warning("[Scanner] batch yf download failed: %s", e)
        return {}

    results: Dict[str, Any] = {}

    if isinstance(raw.columns, pd.MultiIndex):
        top_level = raw.columns.get_level_values(0).unique().tolist()

        for tick in tickers:
            if tick not in top_level:
                continue
            try:
                # New yfinance: (ticker, field)
                c  = raw[tick]["Close"].dropna().values.astype(float)
                h  = raw[tick]["High"].dropna().values.astype(float)
                lo = raw[tick]["Low"].dropna().values.astype(float)
                v  = raw[tick]["Volume"].dropna().values.astype(float)
                n  = min(len(c), len(h), len(lo), len(v))
                if n < 15:
                    continue
                results[tick] = _compute_fast_indicators(c[:n], h[:n], lo[:n], v[:n])
            except Exception:
                try:
                    # Fallback: old yfinance (field, ticker)
                    c  = raw["Close"][tick].dropna().values.astype(float)
                    h  = raw["High"][tick].dropna().values.astype(float)
                    lo = raw["Low"][tick].dropna().values.astype(float)
                    v  = raw["Volume"][tick].dropna().values.astype(float)
                    n  = min(len(c), len(h), len(lo), len(v))
                    if n >= 15:
                        results[tick] = _compute_fast_indicators(c[:n], h[:n], lo[:n], v[:n])
                except Exception:
                    pass
    else:
        # Single ticker fallback
        if len(tickers) == 1:
            tick = tickers[0]
            try:
                c  = raw["Close"].dropna().values.astype(float)
                h  = raw["High"].dropna().values.astype(float)
                lo = raw["Low"].dropna().values.astype(float)
                v  = raw["Volume"].dropna().values.astype(float)
                n  = min(len(c), len(h), len(lo), len(v))
                if n >= 15:
                    results[tick] = _compute_fast_indicators(c[:n], h[:n], lo[:n], v[:n])
            except Exception:
                pass

    return results


class UniverseScanner:
    """Fast NSE universe scanner — 200+ stocks in < 15 seconds."""

    def __init__(self):
        self._last_results: List[Dict] = []
        self._last_scan_ts: float = 0.0
        self._is_scanning:  bool  = False
        self._scan_progress: Dict = {"scanned": 0, "total": 0, "status": "idle"}

    def get_status(self) -> Dict:
        return {
            "is_scanning":   self._is_scanning,
            "last_scan_ts":  self._last_scan_ts,
            "last_scan_ago": round(time.time() - self._last_scan_ts, 0) if self._last_scan_ts else None,
            "result_count":  len(self._last_results),
            "progress":      self._scan_progress,
        }

    def get_last_results(self) -> List[Dict]:
        return self._last_results

    async def scan(
        self,
        segment: str = "all",     # "all" | "fo" | "equity" | "banknifty"
        min_confidence: float = 60.0,
        top_n: int = 30,
    ) -> Dict[str, Any]:
        """
        Main scan entry point.
        Returns {"buys": [...], "sells": [...], "timestamp": ..., "scan_time_sec": ...}
        """
        if self._is_scanning:
            return {
                "status": "already_scanning",
                "progress": self._scan_progress,
                "results": self._last_results,
            }

        self._is_scanning = True
        t0 = time.time()

        try:
            # Filter universe by segment
            universe = NSE_UNIVERSE
            if segment != "all":
                universe = [s for s in NSE_UNIVERSE if s["seg"] == segment]

            self._scan_progress = {"scanned": 0, "total": len(universe), "status": "fetching"}
            logger.info("[Scanner] Starting scan: %d stocks (segment=%s)", len(universe), segment)

            # ── Step 1: Batch fetch OHLCV ─────────────────────────────────────
            tickers = [s["t"] for s in universe]
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=4) as pool:
                # Fetch in 2 batches of 100 for reliability
                mid   = len(tickers) // 2
                batch1 = tickers[:mid]
                batch2 = tickers[mid:]

                f1 = loop.run_in_executor(pool, _batch_fetch_indicators, batch1)
                f2 = loop.run_in_executor(pool, _batch_fetch_indicators, batch2)
                ind1, ind2 = await asyncio.gather(f1, f2)

            indicators = {**ind1, **ind2}
            self._scan_progress["status"] = "scoring"
            self._scan_progress["scanned"] = len(indicators)

            # ── Step 2: Score each stock ──────────────────────────────────────
            results: List[Dict] = []
            for stock in universe:
                t = stock["t"]
                ind = indicators.get(t, {})
                result = _score_stock(t, stock["n"], stock["seg"], ind)
                if result and result["confidence"] >= min_confidence:
                    results.append(result)

            # Sort
            results.sort(key=lambda x: x["confidence"], reverse=True)

            buys  = [r for r in results if r["action"] == "BUY"][:top_n]
            sells = [r for r in results if r["action"] == "SELL"][:top_n]

            scan_time = round(time.time() - t0, 2)
            self._last_results = results
            self._last_scan_ts = time.time()
            self._scan_progress = {
                "scanned": len(indicators),
                "total":   len(universe),
                "status":  "done",
            }

            logger.info(
                "[Scanner] Done: %d stocks → %d BUY + %d SELL in %.1fs",
                len(indicators), len(buys), len(sells), scan_time,
            )

            return {
                "status":        "ok",
                "buys":          buys,
                "sells":         sells,
                "total_scanned": len(indicators),
                "total_hits":    len(results),
                "scan_time_sec": scan_time,
                "segment":       segment,
                "timestamp":     datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error("[Scanner] scan failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}
        finally:
            self._is_scanning = False


# Singleton
universe_scanner = UniverseScanner()
