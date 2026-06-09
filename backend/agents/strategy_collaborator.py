"""
Strategy Collaborator — Multi-Agent Collaboration Engine
=========================================================
Orchestrates all strategy agents into a structured discussion round.
Each agent produces a standardised AgentSignal (signal + confidence + reasoning).
StrategyCollaborator blends all signals → feeds consensus to Dreamer V3.

Agents:
  1. ActiveStockScannerAgent  — NSE most-active + F&O volume screener
  2. BreakoutAgent15m         — 15-min Donchian-20 breakout + volume filter
  3. TechnicalCompositeAgent  — DEMON + SMC + GodzillaTTE composite
  4. KronosAgent              — Kronos AI price-forecast bridge
  5. MiroFishAgent            — lightweight rule-based (fast) / LangGraph (deep)

Dreamer V3 Integration:
  consensus vector → feeds as blending weight on top of DreamerV3 prediction.
  Final confidence = DreamerV3_conf × 0.60  +  agent_consensus_conf × 0.40

Monte Carlo:
  1000-path GBM simulation for each trade setup.
  Returns target-hit probability, SL-hit probability, expected P&L.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np

logger = logging.getLogger(__name__)

# ── NSE F&O + BankNifty + FinNifty universe ───────────────────────────────────
FNO_UNIVERSE: List[str] = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "ITC.NS",
    "LT.NS", "ASIANPAINT.NS", "AXISBANK.NS", "MARUTI.NS", "SUNPHARMA.NS",
    "BAJFINANCE.NS", "WIPRO.NS", "ONGC.NS", "NTPC.NS", "TATAMOTORS.NS",
    "TATASTEEL.NS", "HINDALCO.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "POWERGRID.NS", "JSWSTEEL.NS", "GRASIM.NS", "TECHM.NS", "DRREDDY.NS",
    "NESTLEIND.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS", "CIPLA.NS",
    "BANDHANBNK.NS", "FEDERALBNK.NS", "IDFCFIRSTB.NS", "INDUSINDBK.NS",
    "BANKBARODA.NS", "PNB.NS", "BAJAJFINSV.NS", "SBILIFE.NS", "HDFCLIFE.NS",
    "CHOLAFIN.NS", "ULTRACEMCO.NS", "DIVISLAB.NS", "APOLLOHOSP.NS",
    "TATACONSUM.NS", "BRITANNIA.NS", "EICHERMOT.NS", "SHREECEM.NS",
]

# ── Agent weights for consensus blending ──────────────────────────────────────
AGENT_WEIGHTS: Dict[str, float] = {
    "KronosAI":          0.22,   # highest — AI forecast model
    "Breakout15m":       0.18,   # strong momentum confirmation
    "TechComposite":     0.20,   # DEMON+SMC+GodzillaTTE
    "IntradayMomentum":  0.25,   # NEW — VWAP+ORB+velocity intraday engine
    "MiroFish":          0.10,   # LangGraph / rule-based
    "ActiveScanner":     0.05,   # volume/OI context
}


# ── Shared yfinance helper ────────────────────────────────────────────────────

def _download_ohlcv(ticker: str, period: str = "30d", interval: str = "1d"):
    """Download OHLCV via yfinance, normalise MultiIndex columns."""
    import pandas as pd
    import yfinance as yf
    raw = yf.download(ticker, period=period, interval=interval,
                      progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)
    return raw


def _compute_atr(high, low, close, period: int = 14) -> float:
    import pandas as pd
    h_l   = high - low
    h_pc  = (high - close.shift(1)).abs()
    l_pc  = (low  - close.shift(1)).abs()
    tr    = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    atr_s = tr.rolling(period).mean()
    v = atr_s.iloc[-1]
    return float(v) if not np.isnan(v) else float(close.iloc[-1] * 0.015)


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentSignal:
    """Standardised signal from any strategy agent."""
    agent_name:  str
    ticker:      str
    signal:      str          # BUY / SELL / HOLD
    confidence:  float        # 0–100
    reasoning:   str
    entry:       float = 0.0
    sl:          float = 0.0
    target:      float = 0.0
    timeframe:   str   = "1d"
    indicators:  Dict  = field(default_factory=dict)
    timestamp:   str   = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    error: Optional[str] = None

    @property
    def direction_score(self) -> float:
        """Signed confidence score (+ve BUY, -ve SELL, 0 HOLD)."""
        if self.signal == "BUY":
            return self.confidence
        if self.signal == "SELL":
            return -self.confidence
        return 0.0

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["direction_score"] = self.direction_score
        return d


@dataclass
class AgentDiscussion:
    """Complete multi-agent discussion for one ticker."""
    ticker:               str
    scan_id:              str
    agent_signals:        List[AgentSignal] = field(default_factory=list)
    consensus:            str   = "HOLD"
    consensus_confidence: float = 0.0
    weighted_score:       float = 0.0     # –100 … +100
    bull_count:           int   = 0
    bear_count:           int   = 0
    hold_count:           int   = 0
    # Dreamer V3 integration
    dreamer_input_features:    Dict  = field(default_factory=dict)
    dreamer_final_signal:      str   = "HOLD"
    dreamer_final_confidence:  float = 0.0
    dreamer_reasoning:         str   = ""
    # Monte Carlo
    monte_carlo:  Dict = field(default_factory=dict)
    # ── Intraday Intelligence (new fields) ────────────────────────────────
    win_probability:  float = 0.0   # MC target_hit_prob * 100 → 0..100
    conviction_score: float = 0.0   # % agents agreeing with consensus
    volume_ratio:     float = 1.0   # relative volume vs avg
    intraday_score:   float = 0.0   # composite ranking score
    agent_agreement_map: Dict = field(default_factory=dict)  # {agent: BUY/SELL/HOLD}
    # Meta
    timestamp:    str  = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    duration_ms:  float = 0.0
    mode:         str   = "fast"

    def to_dict(self) -> Dict:
        return {
            "ticker":               self.ticker,
            "scan_id":              self.scan_id,
            "agent_signals":        [s.to_dict() for s in self.agent_signals],
            "consensus":            self.consensus,
            "consensus_confidence": round(self.consensus_confidence, 1),
            "weighted_score":       round(self.weighted_score, 2),
            "bull_count":           self.bull_count,
            "bear_count":           self.bear_count,
            "hold_count":           self.hold_count,
            "dreamer_input_features":   self.dreamer_input_features,
            "dreamer_final_signal":      self.dreamer_final_signal,
            "dreamer_final_confidence":  round(self.dreamer_final_confidence, 1),
            "dreamer_reasoning":         self.dreamer_reasoning,
            "monte_carlo":           self.monte_carlo,
            "win_probability":       round(self.win_probability, 1),
            "conviction_score":      round(self.conviction_score, 1),
            "volume_ratio":          round(self.volume_ratio, 2),
            "intraday_score":        round(self.intraday_score, 2),
            "agent_agreement_map":   self.agent_agreement_map,
            "timestamp":             self.timestamp,
            "duration_ms":           round(self.duration_ms, 0),
            "mode":                  self.mode,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 1 — Active Stock Scanner
# ─────────────────────────────────────────────────────────────────────────────

class ActiveStockScannerAgent:
    """
    Scans NSE Most-Active endpoint + F&O universe for high-volume candidates.
    Returns a ranked list of tickers (not per-ticker BUY/SELL signals).
    When used in discussion, it contributes a volume-context signal for the
    specific ticker being analysed.
    """
    _cache:    Optional[List[Dict]] = None
    _cache_ts: float = 0.0
    CACHE_TTL: int   = 300  # 5 min

    def scan_universe(self, limit: int = 30) -> List[Dict]:
        """Return top `limit` active tickers with volume data."""
        if self._cache and (time.time() - self._cache_ts) < self.CACHE_TTL:
            return self._cache[:limit]

        results: List[Dict] = []

        # ── Try NSE most-active API ──
        results.extend(self._fetch_nse_most_active())

        # ── F&O quick scan ──
        results.extend(self._scan_fno_volume())

        # Dedup by ticker
        seen: set = set()
        deduped: List[Dict] = []
        for r in results:
            t = r.get("ticker", "")
            if t and t not in seen:
                seen.add(t)
                deduped.append(r)

        deduped.sort(key=lambda x: x.get("score", 0), reverse=True)
        self._cache    = deduped
        self._cache_ts = time.time()
        logger.info("[ActiveScanner] Scanned %d tickers", len(deduped))
        return deduped[:limit]

    def analyze(self, ticker: str) -> AgentSignal:
        """
        Volume-context signal for a specific ticker.
        BUY if top-10 active + vol_ratio > 1.5; HOLD otherwise.
        """
        try:
            active = self.scan_universe(30)
            active_tickers = [a["ticker"] for a in active[:10]]

            # Pull vol_ratio for this ticker
            vol_ratio = 1.0
            for a in active:
                if a.get("ticker") == ticker:
                    vol_ratio = a.get("vol_ratio", 1.0)
                    break

            if ticker in active_tickers and vol_ratio >= 1.5:
                signal = "BUY"
                conf   = min(80, 50 + int(vol_ratio * 10))
                reason = (
                    f"Top-10 most-active (vol_ratio {vol_ratio:.1f}×). "
                    "Unusual volume surge detected — smart money participation likely."
                )
            elif vol_ratio >= 1.3:
                signal = "HOLD"
                conf   = 40
                reason = f"Above-average volume ({vol_ratio:.1f}×) but not in top-10 active list."
            else:
                signal = "HOLD"
                conf   = 20
                reason = f"Normal volume ({vol_ratio:.1f}×). No unusual activity detected."

            return AgentSignal(
                agent_name="ActiveScanner",
                ticker=ticker,
                signal=signal,
                confidence=float(conf),
                reasoning=reason,
                indicators={"vol_ratio": round(vol_ratio, 2), "in_top10": ticker in active_tickers},
            )
        except Exception as exc:
            logger.debug("[ActiveScanner] analyze error: %s", exc)
            return AgentSignal(
                agent_name="ActiveScanner",
                ticker=ticker,
                signal="HOLD",
                confidence=0.0,
                reasoning="Scanner unavailable.",
                error=str(exc),
            )

    # ── private helpers ──────────────────────────────────────────────────────

    def _fetch_nse_most_active(self) -> List[Dict]:
        try:
            import requests
            base = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
            r = requests.get(f"{base}/api/nse/most-active?limit=25", timeout=8)
            if not r.ok:
                return []
            data  = r.json()
            out   = []
            for s in data.get("most_active", []):
                sym = (s.get("symbol") or s.get("ticker", "")).replace(".NS", "") + ".NS"
                vol = float(s.get("totalTradedVolume") or s.get("volume") or 0)
                out.append({
                    "ticker":    sym,
                    "vol_ratio": 2.0,  # most-active API implies above-average
                    "score":     vol / 1e6,
                    "source":    "nse_most_active",
                })
            return out
        except Exception:
            return []

    def _scan_fno_volume(self) -> List[Dict]:
        try:
            import random
            sample = random.sample(FNO_UNIVERSE, min(20, len(FNO_UNIVERSE)))
            import yfinance as yf
            raw = yf.download(sample, period="5d", interval="1d",
                              progress=False, auto_adjust=True)
            if raw.empty:
                return []
            close  = raw["Close"]
            volume = raw["Volume"]
            out    = []
            for t in sample:
                try:
                    c = close[t].dropna() if t in close.columns else None
                    v = volume[t].dropna() if t in volume.columns else None
                    if c is None or v is None or len(c) < 3:
                        continue
                    avg = float(v.rolling(5).mean().iloc[-1]) + 1e-8
                    vr  = float(v.iloc[-1]) / avg
                    chg = (float(c.iloc[-1]) - float(c.iloc[-2])) / (float(c.iloc[-2]) + 1e-8) * 100
                    out.append({
                        "ticker":    t,
                        "vol_ratio": round(vr, 2),
                        "price_change_pct": round(chg, 2),
                        "score":     round(vr * (1 + abs(chg) / 10), 3),
                        "source":    "fno_scan",
                    })
                except Exception:
                    pass
            return out
        except Exception as exc:
            logger.debug("[ActiveScanner] FNO scan error: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 2 — 15-Minute Breakout Agent
# ─────────────────────────────────────────────────────────────────────────────

class BreakoutAgent15m:
    """
    15-minute Donchian-20 breakout + volume filter + EMA20 alignment.
    BUY  : close > prev Donchian high, volume ≥ 1.3×, EMA20 rising.
    SELL : close < prev Donchian low,  volume ≥ 1.3×, EMA20 falling.
    """

    def analyze(self, ticker: str) -> AgentSignal:
        t0 = time.time()
        try:
            df = _download_ohlcv(ticker, period="5d", interval="15m")
            if df.empty or len(df) < 25:
                return self._hold(ticker, "Insufficient 15m data")

            close  = df["Close"].astype(float)
            high   = df["High"].astype(float)
            low    = df["Low"].astype(float)
            volume = df["Volume"].astype(float)

            don_high = high.rolling(20).max()
            don_low  = low.rolling(20).min()
            ema20    = close.ewm(span=20).mean()
            atr14    = _compute_atr(high, low, close, 14)

            cur_close   = float(close.iloc[-1])
            prev_dh     = float(don_high.iloc[-2])
            prev_dl     = float(don_low.iloc[-2])
            vol_avg20   = float(volume.rolling(20).mean().iloc[-1]) + 1e-8
            vol_ratio   = float(volume.iloc[-1]) / vol_avg20
            ema_rising  = float(ema20.iloc[-1]) > float(ema20.iloc[-5])

            # Contextual: how much above/below the channel?
            breakout_strength = 0.0
            if cur_close > prev_dh and prev_dh > 0:
                breakout_strength = (cur_close - prev_dh) / prev_dh * 100
            elif cur_close < prev_dl and prev_dl > 0:
                breakout_strength = (prev_dl - cur_close) / prev_dl * 100

            if cur_close > prev_dh and vol_ratio >= 1.3 and ema_rising:
                conf = min(92, 55 + int(vol_ratio * 8) + int(breakout_strength * 10))
                sl   = round(cur_close - atr14 * 1.5, 2)
                tgt  = round(cur_close + atr14 * 2.5, 2)
                return AgentSignal(
                    agent_name="Breakout15m",
                    ticker=ticker,
                    signal="BUY",
                    confidence=float(conf),
                    reasoning=(
                        f"15m Donchian-20 bullish breakout. Close {cur_close:.1f} > "
                        f"prev channel high {prev_dh:.1f} (+{breakout_strength:.2f}%). "
                        f"Volume {vol_ratio:.1f}× avg. EMA20 rising — momentum confirmed."
                    ),
                    entry=round(cur_close, 2),
                    sl=sl,
                    target=tgt,
                    timeframe="15m",
                    indicators={
                        "don_high": round(prev_dh, 2),
                        "don_low":  round(prev_dl, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "ema20":    round(float(ema20.iloc[-1]), 2),
                        "breakout_strength_pct": round(breakout_strength, 3),
                    },
                )

            if cur_close < prev_dl and vol_ratio >= 1.3 and not ema_rising:
                conf = min(88, 55 + int(vol_ratio * 8) + int(breakout_strength * 10))
                sl   = round(cur_close + atr14 * 1.5, 2)
                tgt  = round(cur_close - atr14 * 2.5, 2)
                return AgentSignal(
                    agent_name="Breakout15m",
                    ticker=ticker,
                    signal="SELL",
                    confidence=float(conf),
                    reasoning=(
                        f"15m Donchian-20 bearish breakdown. Close {cur_close:.1f} < "
                        f"prev channel low {prev_dl:.1f} (-{breakout_strength:.2f}%). "
                        f"Volume {vol_ratio:.1f}× avg. EMA20 falling — downtrend pressure."
                    ),
                    entry=round(cur_close, 2),
                    sl=sl,
                    target=tgt,
                    timeframe="15m",
                    indicators={
                        "don_high": round(prev_dh, 2),
                        "don_low":  round(prev_dl, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "ema20":    round(float(ema20.iloc[-1]), 2),
                        "breakout_strength_pct": round(breakout_strength, 3),
                    },
                )

            return self._hold(
                ticker,
                f"Price inside Donchian-20 channel (H:{prev_dh:.1f} – L:{prev_dl:.1f}). "
                f"No breakout. Vol ratio: {vol_ratio:.1f}×.",
                indicators={
                    "don_high":  round(prev_dh, 2),
                    "don_low":   round(prev_dl, 2),
                    "vol_ratio": round(vol_ratio, 2),
                },
            )

        except Exception as exc:
            logger.debug("[Breakout15m] error on %s: %s", ticker, exc)
            return AgentSignal(
                agent_name="Breakout15m",
                ticker=ticker,
                signal="HOLD",
                confidence=0.0,
                reasoning="15m data unavailable.",
                error=str(exc),
            )

    @staticmethod
    def _hold(ticker: str, reason: str, indicators: Dict = None) -> AgentSignal:
        return AgentSignal(
            agent_name="Breakout15m",
            ticker=ticker,
            signal="HOLD",
            confidence=25.0,
            reasoning=reason,
            indicators=indicators or {},
        )


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 3 — Technical Composite (DEMON + SMC + GodzillaTTE)
# ─────────────────────────────────────────────────────────────────────────────

class TechnicalCompositeAgent:
    """
    Composite of three strategy sub-signals on daily timeframe:
      • DEMON     : candle body position + momentum + volume
      • SMC       : order-block + fair-value-gap detection
      • GodzillaTTE: trend confluence + explosive volume
    Final signal = weighted majority vote.
    """

    def analyze(self, ticker: str) -> AgentSignal:
        try:
            df = _download_ohlcv(ticker, period="60d", interval="1d")
            if df.empty or len(df) < 20:
                return self._hold(ticker, "Insufficient daily data")

            o = df["Open"].astype(float)
            h = df["High"].astype(float)
            l = df["Low"].astype(float)
            c = df["Close"].astype(float)
            v = df["Volume"].astype(float)

            atr14  = _compute_atr(h, l, c, 14)
            ema20  = c.ewm(span=20).mean()
            sma50  = c.rolling(50).mean() if len(c) >= 50 else ema20
            rsi14  = self._rsi(c, 14)
            vavg20 = v.rolling(20).mean()
            macd_line, macd_sig = self._macd(c)

            cur_c  = float(c.iloc[-1])
            cur_o  = float(o.iloc[-1])
            cur_h  = float(h.iloc[-1])
            cur_l  = float(l.iloc[-1])
            cur_v  = float(v.iloc[-1])
            avg_v  = float(vavg20.iloc[-1]) + 1e-8
            vol_r  = cur_v / avg_v
            rsi    = float(rsi14.iloc[-1]) if not rsi14.empty else 50.0
            e20    = float(ema20.iloc[-1])
            s50    = float(sma50.iloc[-1])

            scores: List[Tuple[str, float, str]] = []  # (sub-agent, score, note)

            # ── DEMON ──────────────────────────────────────────────────────
            candle_range = cur_h - cur_l + 1e-8
            body         = abs(cur_c - cur_o)
            body_pct     = body / candle_range
            mid_range    = cur_l + candle_range * 0.5
            bull_candle  = cur_c > cur_o and cur_c > mid_range and body_pct > 0.5 and vol_r > 1.0
            bear_candle  = cur_c < cur_o and cur_c < mid_range and body_pct > 0.5 and vol_r > 1.0

            if bull_candle:
                demon_score = min(80, 50 + int(body_pct * 30) + int((vol_r - 1) * 10))
                demon_note  = f"DEMON bullish: body {body_pct:.0%}, mid-upper range, vol {vol_r:.1f}×"
            elif bear_candle:
                demon_score = -min(80, 50 + int(body_pct * 30) + int((vol_r - 1) * 10))
                demon_note  = f"DEMON bearish: body {body_pct:.0%}, mid-lower range, vol {vol_r:.1f}×"
            else:
                demon_score = 0
                demon_note  = f"DEMON neutral: small body ({body_pct:.0%}) or inside-bar"
            scores.append(("DEMON", demon_score, demon_note))

            # ── SMC (Smart Money Concepts) ─────────────────────────────────
            # Order Block: look for last large opposing candle before a ≥1R move
            smc_score = 0
            smc_note  = "SMC: no clear order block"
            if len(c) >= 6:
                # Bullish OB: last red candle before recent green ≥1R move
                recent_move = float(c.iloc[-1]) - float(c.iloc[-5])
                if recent_move > atr14:   # ≥1R move up
                    # look for last red candle in lookback
                    for i in range(-3, -6, -1):
                        if float(o.iloc[i]) > float(c.iloc[i]):  # red candle = OB
                            ob_level = float(c.iloc[i])
                            if cur_c > ob_level:
                                smc_score = 65
                                smc_note  = (
                                    f"SMC: price above bullish order block "
                                    f"({ob_level:.1f}). Institutional buying zone respected."
                                )
                            break
                elif recent_move < -atr14:  # ≥1R move down
                    for i in range(-3, -6, -1):
                        if float(c.iloc[i]) > float(o.iloc[i]):  # green candle = bearish OB
                            ob_level = float(o.iloc[i])
                            if cur_c < ob_level:
                                smc_score = -65
                                smc_note  = (
                                    f"SMC: price below bearish order block "
                                    f"({ob_level:.1f}). Institutional selling zone."
                                )
                            break

                # FVG (Fair Value Gap): 3-bar gap
                if len(c) >= 4:
                    prev_high  = float(h.iloc[-3])
                    prev2_low  = float(l.iloc[-2])
                    cur_low    = float(l.iloc[-1])
                    cur_high   = float(h.iloc[-1])
                    prev_low   = float(l.iloc[-3])
                    prev2_high = float(h.iloc[-2])

                    if prev_high < prev2_low:  # bullish FVG: gap between bar[-3] high and bar[-2] low
                        if smc_score >= 0:
                            smc_score = max(smc_score, 55)
                            smc_note += " | Bullish FVG present — magnet zone above."
                    elif prev_low > prev2_high:  # bearish FVG
                        if smc_score <= 0:
                            smc_score = min(smc_score, -55)
                            smc_note += " | Bearish FVG present — magnet zone below."

            scores.append(("SMC", smc_score, smc_note))

            # ── GodzillaTTE (Trend + Volume Explosion) ─────────────────────
            trend_up   = e20 > s50 * 1.005
            trend_down = e20 < s50 * 0.995
            macd_bull  = float(macd_line.iloc[-1]) > float(macd_sig.iloc[-1])
            macd_bear  = not macd_bull

            if trend_up and macd_bull and vol_r >= 1.3 and rsi > 45:
                gtz_score = min(75, 45 + int(vol_r * 8) + (10 if rsi > 55 else 0))
                gtz_note  = (
                    f"Godzilla: EMA20({e20:.0f}) > SMA50({s50:.0f}), "
                    f"MACD bullish cross, vol {vol_r:.1f}×, RSI {rsi:.0f}"
                )
            elif trend_down and macd_bear and vol_r >= 1.3 and rsi < 55:
                gtz_score = -min(75, 45 + int(vol_r * 8) + (10 if rsi < 45 else 0))
                gtz_note  = (
                    f"Godzilla: EMA20({e20:.0f}) < SMA50({s50:.0f}), "
                    f"MACD bearish, vol {vol_r:.1f}×, RSI {rsi:.0f}"
                )
            else:
                gtz_score = 0
                gtz_note  = (
                    f"Godzilla: mixed signals — trend={('up' if trend_up else 'down' if trend_down else 'flat')}, "
                    f"MACD={('bull' if macd_bull else 'bear')}, RSI={rsi:.0f}"
                )
            scores.append(("GodzillaTTE", gtz_score, gtz_note))

            # ── Composite vote ─────────────────────────────────────────────
            total   = sum(abs(s) for _, s, _ in scores) + 1e-8
            weights = {"DEMON": 0.35, "SMC": 0.35, "GodzillaTTE": 0.30}
            raw_score = sum(weights.get(name, 1/3) * score for name, score, _ in scores)

            if raw_score > 30:
                signal = "BUY"
                conf   = min(90, int(raw_score))
            elif raw_score < -30:
                signal = "SELL"
                conf   = min(90, int(abs(raw_score)))
            else:
                signal = "HOLD"
                conf   = max(20, 50 - int(abs(raw_score)))

            sl  = round(cur_c - atr14 * 2.0, 2) if signal == "BUY" else round(cur_c + atr14 * 2.0, 2) if signal == "SELL" else cur_c
            tgt = round(cur_c + atr14 * 3.0, 2) if signal == "BUY" else round(cur_c - atr14 * 3.0, 2) if signal == "SELL" else cur_c

            sub_notes = " | ".join(f"{n}: {note}" for n, _, note in scores)

            return AgentSignal(
                agent_name="TechComposite",
                ticker=ticker,
                signal=signal,
                confidence=float(conf),
                reasoning=f"Composite score {raw_score:+.0f}/100. {sub_notes}",
                entry=round(cur_c, 2),
                sl=sl,
                target=tgt,
                timeframe="1d",
                indicators={
                    "demon_score":  scores[0][1],
                    "smc_score":    scores[1][1],
                    "godzilla_score": scores[2][1],
                    "composite_score": round(raw_score, 1),
                    "rsi14":   round(rsi, 1),
                    "ema20":   round(e20, 2),
                    "sma50":   round(s50, 2),
                    "vol_ratio": round(vol_r, 2),
                },
            )

        except Exception as exc:
            logger.debug("[TechComposite] error on %s: %s", ticker, exc)
            return AgentSignal(
                agent_name="TechComposite",
                ticker=ticker,
                signal="HOLD",
                confidence=0.0,
                reasoning="Technical analysis unavailable.",
                error=str(exc),
            )

    @staticmethod
    def _rsi(close, period: int = 14):
        d    = close.diff()
        gain = d.clip(lower=0).rolling(period).mean()
        loss = (-d.clip(upper=0)).rolling(period).mean()
        return 100 - 100 / (1 + gain / (loss + 1e-8))

    @staticmethod
    def _macd(close, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = close.ewm(span=fast).mean()
        ema_slow = close.ewm(span=slow).mean()
        line     = ema_fast - ema_slow
        sig      = line.ewm(span=signal).mean()
        return line, sig

    @staticmethod
    def _hold(ticker: str, reason: str) -> AgentSignal:
        return AgentSignal(
            agent_name="TechComposite",
            ticker=ticker,
            signal="HOLD",
            confidence=20.0,
            reasoning=reason,
        )


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 4 — Kronos AI Agent
# ─────────────────────────────────────────────────────────────────────────────

class KronosAgent:
    """
    Bridges to the existing Kronos price-forecast model.
    Runs `kronos_router.get_kronos_signal()` in a fresh event-loop thread
    so it works from both sync and async callers.
    """

    def analyze(self, ticker: str) -> AgentSignal:
        try:
            result = self._run_kronos(ticker)
            if result is None:
                return AgentSignal(
                    agent_name="KronosAI",
                    ticker=ticker,
                    signal="HOLD",
                    confidence=30.0,
                    reasoning="Kronos model not loaded — start Kronos warmup first.",
                )

            raw_sig = result.get("signal", "WAIT")
            # Normalise: WAIT / NEUTRAL → HOLD
            if raw_sig in ("WAIT", "NEUTRAL", "NONE", ""):
                raw_sig = "HOLD"

            conf      = float(result.get("confidence", 50))
            entry     = float(result.get("entry_price", 0))
            sl        = float(result.get("stop_loss", 0))
            tgt       = float(result.get("target_1", 0))
            rationale = result.get("rationale", "")
            rr        = result.get("risk_reward", 0)

            reason = (
                f"Kronos AI forecast → {raw_sig} ({conf:.0f}% confidence). "
                f"Entry: ₹{entry:.0f}, SL: ₹{sl:.0f}, T1: ₹{tgt:.0f}, R:R {rr:.1f}. "
                f"{rationale}"
            )

            return AgentSignal(
                agent_name="KronosAI",
                ticker=ticker,
                signal=raw_sig,
                confidence=min(95.0, conf),
                reasoning=reason,
                entry=round(entry, 2),
                sl=round(sl, 2),
                target=round(tgt, 2),
                timeframe="1d",
                indicators={
                    "target_2":   float(result.get("target_2", 0)),
                    "target_3":   float(result.get("target_3", 0)),
                    "risk_reward": rr,
                },
            )
        except Exception as exc:
            logger.debug("[KronosAgent] error on %s: %s", ticker, exc)
            return AgentSignal(
                agent_name="KronosAI",
                ticker=ticker,
                signal="HOLD",
                confidence=0.0,
                reasoning="Kronos analysis failed.",
                error=str(exc),
            )

    @staticmethod
    def _run_kronos(ticker: str) -> Optional[Dict]:
        """Run async get_kronos_signal in a dedicated thread + event-loop."""
        import concurrent.futures

        def _worker():
            loop = asyncio.new_event_loop()
            try:
                import sys
                from pathlib import Path
                root = str(Path(__file__).parent.parent)
                if root not in sys.path:
                    sys.path.insert(0, root)
                from kronos_router import get_kronos_signal
                return loop.run_until_complete(get_kronos_signal(ticker))
            except Exception as e:
                logger.debug("[KronosAgent] _worker error: %s", e)
                return None
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_worker)
            try:
                return fut.result(timeout=20)
            except Exception:
                return None


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 5 — MiroFish Agent (fast / deep)
# ─────────────────────────────────────────────────────────────────────────────

class MiroFishAgent:
    """
    Fast mode  : rule-based multi-indicator reasoning (instant).
    Deep mode  : calls existing /api/mirofish/analyze endpoint for LangGraph AI.
                 ~10s latency per ticker — use only on-demand.
    """

    def analyze(self, ticker: str, deep: bool = False) -> AgentSignal:
        if deep:
            return self._deep_analyze(ticker)
        return self._fast_analyze(ticker)

    def _fast_analyze(self, ticker: str) -> AgentSignal:
        """Lightweight technical reasoning — RSI, MACD, Bollinger, EMA cross."""
        try:
            df = _download_ohlcv(ticker, period="30d", interval="1d")
            if df.empty or len(df) < 20:
                return self._hold(ticker, "Insufficient data for MiroFish fast-analysis")

            c   = df["Close"].astype(float)
            v   = df["Volume"].astype(float)
            atr = _compute_atr(df["High"].astype(float), df["Low"].astype(float), c, 14)

            rsi    = float(self._rsi(c, 14).iloc[-1]) if len(c) >= 15 else 50.0
            ema9   = float(c.ewm(span=9).mean().iloc[-1])
            ema21  = float(c.ewm(span=21).mean().iloc[-1])
            ema50  = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else ema21
            macd_l, macd_s = self._macd(c)
            macd_diff = float(macd_l.iloc[-1]) - float(macd_s.iloc[-1])

            # Bollinger Bands (20, 2σ)
            bb_mid  = float(c.rolling(20).mean().iloc[-1])
            bb_std  = float(c.rolling(20).std().iloc[-1]) + 1e-8
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std
            cur_c    = float(c.iloc[-1])
            vol_r    = float(v.iloc[-1]) / (float(v.rolling(20).mean().iloc[-1]) + 1e-8)

            bull_signals, bear_signals = [], []

            if ema9 > ema21:       bull_signals.append("EMA9>EMA21 (short-term bullish cross)")
            if ema21 > ema50:      bull_signals.append("EMA21>EMA50 (medium trend up)")
            if rsi > 50:           bull_signals.append(f"RSI {rsi:.0f} above mid-line")
            if rsi < 70:           bull_signals.append("RSI not overbought")
            if macd_diff > 0:      bull_signals.append("MACD histogram positive")
            if cur_c > bb_mid:     bull_signals.append("Price above BB midline")
            if vol_r > 1.2:        bull_signals.append(f"Volume surge {vol_r:.1f}×")

            if ema9 < ema21:       bear_signals.append("EMA9<EMA21 (short-term bearish cross)")
            if ema21 < ema50:      bear_signals.append("EMA21<EMA50 (medium trend down)")
            if rsi < 50:           bear_signals.append(f"RSI {rsi:.0f} below mid-line")
            if rsi > 70:           bear_signals.append("RSI overbought — reversal risk")
            if macd_diff < 0:      bear_signals.append("MACD histogram negative")
            if cur_c < bb_mid:     bear_signals.append("Price below BB midline")
            if cur_c < bb_lower:   bear_signals.append("Price below BB lower — oversold/breakdown")
            if cur_c > bb_upper:   bear_signals.append("Price above BB upper — overbought/breakout")

            bull_score = len(bull_signals) * 12
            bear_score = len(bear_signals) * 12
            net        = bull_score - bear_score

            if net >= 30:
                signal = "BUY"
                conf   = min(85, 40 + net)
                reason = (
                    f"MiroFish multi-indicator BUY: {len(bull_signals)} bullish signals "
                    f"({', '.join(bull_signals[:3])}). "
                    f"Net score: +{net}/100."
                )
            elif net <= -30:
                signal = "SELL"
                conf   = min(85, 40 + abs(net))
                reason = (
                    f"MiroFish multi-indicator SELL: {len(bear_signals)} bearish signals "
                    f"({', '.join(bear_signals[:3])}). "
                    f"Net score: {net}/100."
                )
            else:
                signal = "HOLD"
                conf   = 30.0
                reason = (
                    f"MiroFish mixed signals — bull:{len(bull_signals)} vs bear:{len(bear_signals)}. "
                    f"Net score: {net:+d}/100. Awaiting clearer setup."
                )

            sl  = round(cur_c - atr * 2.0, 2) if signal == "BUY" else round(cur_c + atr * 2.0, 2) if signal == "SELL" else cur_c
            tgt = round(cur_c + atr * 3.0, 2) if signal == "BUY" else round(cur_c - atr * 3.0, 2) if signal == "SELL" else cur_c

            return AgentSignal(
                agent_name="MiroFish",
                ticker=ticker,
                signal=signal,
                confidence=float(conf),
                reasoning=reason,
                entry=round(cur_c, 2),
                sl=sl,
                target=tgt,
                timeframe="1d",
                indicators={
                    "rsi14":     round(rsi, 1),
                    "ema9":      round(ema9, 2),
                    "ema21":     round(ema21, 2),
                    "ema50":     round(ema50, 2),
                    "macd_diff": round(macd_diff, 4),
                    "bb_upper":  round(bb_upper, 2),
                    "bb_lower":  round(bb_lower, 2),
                    "vol_ratio": round(vol_r, 2),
                    "net_score": net,
                },
            )
        except Exception as exc:
            logger.debug("[MiroFish] fast error: %s", exc)
            return self._hold(ticker, "MiroFish fast analysis failed.", str(exc))

    def _deep_analyze(self, ticker: str) -> AgentSignal:
        """Call existing MiroFish LangGraph endpoint. ~10s, on-demand only."""
        try:
            import requests
            base = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
            r = requests.post(
                f"{base}/api/mirofish/analyze",
                json={"ticker": ticker, "timeframe": "15m"},
                timeout=25,
            )
            if not r.ok:
                return self._fast_analyze(ticker)

            data       = r.json()
            suggestion = data.get("suggestion", "HOLD").upper()
            if "BUY" in suggestion:
                sig, conf = "BUY", 75.0
            elif "SELL" in suggestion:
                sig, conf = "SELL", 70.0
            else:
                sig, conf = "HOLD", 35.0

            summary = data.get("swarm_summary", data.get("summary", "No summary"))
            return AgentSignal(
                agent_name="MiroFish",
                ticker=ticker,
                signal=sig,
                confidence=conf,
                reasoning=f"[DEEP LangGraph] {summary[:300]}",
                timeframe="15m",
                indicators={"deep_mode": True, "swarm_agents": len(data.get("agent_outputs", []))},
            )
        except Exception as exc:
            logger.debug("[MiroFish] deep error: %s", exc)
            return self._fast_analyze(ticker)  # fallback to fast

    @staticmethod
    def _rsi(close, period=14):
        d    = close.diff()
        gain = d.clip(lower=0).rolling(period).mean()
        loss = (-d.clip(upper=0)).rolling(period).mean()
        return 100 - 100 / (1 + gain / (loss + 1e-8))

    @staticmethod
    def _macd(close, fast=12, slow=26, signal=9):
        line = close.ewm(span=fast).mean() - close.ewm(span=slow).mean()
        return line, line.ewm(span=signal).mean()

    @staticmethod
    def _hold(ticker: str, reason: str, error: str = None) -> AgentSignal:
        return AgentSignal(
            agent_name="MiroFish",
            ticker=ticker,
            signal="HOLD",
            confidence=0.0,
            reasoning=reason,
            error=error,
        )


# ─────────────────────────────────────────────────────────────────────────────
# AGENT 6 — Intraday Momentum Agent (VWAP + ORB + Velocity)
# ─────────────────────────────────────────────────────────────────────────────

class IntradayMomentumAgent:
    """
    Intraday-specific momentum agent using 5m + 15m data.
    Detects:
      - VWAP deviation (price above/below VWAP = smart money direction)
      - Price velocity (5-bar rate of change = momentum strength)
      - Volume surge (current vs session avg = participation)
      - Opening Range Breakout (first 30min breakout = intraday direction)
      - RSI-15m (overbought/oversold on 15m)
    Signal: BUY when 2+ conditions align bullishly with volume confirmation.
    """

    def analyze(self, ticker: str) -> AgentSignal:
        try:
            df5  = _download_ohlcv(ticker, period="3d", interval="5m")
            df15 = _download_ohlcv(ticker, period="5d", interval="15m")

            if df5.empty or len(df5) < 10:
                return self._hold(ticker, "Insufficient 5m intraday data")

            c5 = df5["Close"].astype(float)
            h5 = df5["High"].astype(float)
            l5 = df5["Low"].astype(float)
            v5 = df5["Volume"].astype(float)

            # ── VWAP (session) ──────────────────────────────────────────────
            typical = (h5 + l5 + c5) / 3
            vwap    = (typical * v5).cumsum() / (v5.cumsum() + 1e-8)
            cur_c    = float(c5.iloc[-1])
            cur_vwap = float(vwap.iloc[-1])
            vwap_dev = (cur_c - cur_vwap) / (cur_vwap + 1e-8) * 100

            # ── Price Velocity (5-bar ROC) ──────────────────────────────────
            velocity = 0.0
            if len(c5) >= 6:
                velocity = (float(c5.iloc[-1]) - float(c5.iloc[-6])) / (float(c5.iloc[-6]) + 1e-8) * 100

            # ── Volume Surge ────────────────────────────────────────────────
            vol_avg   = float(v5.rolling(20).mean().iloc[-1]) + 1e-8
            vol_ratio = float(v5.iloc[-1]) / vol_avg

            # ── Opening Range Breakout (first 30min = 6 × 5m candles) ──────
            session_candles = df5.tail(78)  # ~6.5hr session
            orb_breakout_up = orb_breakout_down = False
            orb_high = orb_low = cur_c
            if len(session_candles) >= 6:
                orb_high = float(session_candles["High"].iloc[:6].max())
                orb_low  = float(session_candles["Low"].iloc[:6].min())
                orb_breakout_up   = cur_c > orb_high * 1.001
                orb_breakout_down = cur_c < orb_low * 0.999

            # ── RSI-15m ──────────────────────────────────────────────────────
            rsi15 = 50.0
            if not df15.empty and len(df15) >= 15:
                c15   = df15["Close"].astype(float)
                d     = c15.diff()
                g     = d.clip(lower=0).rolling(14).mean()
                lo    = (-d.clip(upper=0)).rolling(14).mean()
                v     = 100 - 100 / (1 + g / (lo + 1e-8))
                rsi15 = float(v.iloc[-1]) if not np.isnan(float(v.iloc[-1])) else 50.0

            # ── Scoring ─────────────────────────────────────────────────────
            bull_score, bear_score = 0, 0
            bull_notes, bear_notes = [], []

            if vwap_dev > 0.25:
                bull_score += 25; bull_notes.append(f"Above VWAP +{vwap_dev:.2f}%")
            elif vwap_dev < -0.25:
                bear_score += 25; bear_notes.append(f"Below VWAP {vwap_dev:.2f}%")

            if velocity > 0.3:
                bull_score += 20; bull_notes.append(f"Velocity +{velocity:.2f}%")
            elif velocity < -0.3:
                bear_score += 20; bear_notes.append(f"Velocity {velocity:.2f}%")

            if vol_ratio >= 1.5:
                vol_bonus = min(25, int(vol_ratio * 8))
                if bull_score >= bear_score:
                    bull_score += vol_bonus; bull_notes.append(f"Vol {vol_ratio:.1f}× surge")
                else:
                    bear_score += vol_bonus; bear_notes.append(f"Vol {vol_ratio:.1f}× surge")

            if orb_breakout_up:
                bull_score += 30; bull_notes.append(f"ORB breakout >₹{orb_high:.1f}")
            elif orb_breakout_down:
                bear_score += 30; bear_notes.append(f"ORB breakdown <₹{orb_low:.1f}")

            if 55 < rsi15 < 75:
                bull_score += 15; bull_notes.append(f"RSI15m {rsi15:.0f} momentum")
            elif 25 < rsi15 < 45:
                bear_score += 15; bear_notes.append(f"RSI15m {rsi15:.0f} oversold bounce")

            atr14 = _compute_atr(h5, l5, c5, 14) if len(c5) >= 15 else cur_c * 0.01
            indicators = {
                "vwap": round(cur_vwap, 2),
                "vwap_dev_pct": round(vwap_dev, 3),
                "velocity_5bar": round(velocity, 3),
                "vol_ratio": round(vol_ratio, 2),
                "rsi15m": round(rsi15, 1),
                "orb_high": round(orb_high, 2),
                "orb_low": round(orb_low, 2),
            }

            if bull_score >= 40 and bull_score > bear_score:
                conf = min(90, 35 + bull_score)
                return AgentSignal(
                    agent_name="IntradayMomentum", ticker=ticker, signal="BUY",
                    confidence=float(conf),
                    reasoning=f"Intraday BUY: {', '.join(bull_notes)}. Score {bull_score}/100.",
                    entry=round(cur_c, 2),
                    sl=round(cur_c - atr14 * 1.5, 2),
                    target=round(cur_c + atr14 * 2.5, 2),
                    timeframe="5m", indicators=indicators,
                )

            if bear_score >= 40 and bear_score > bull_score:
                conf = min(90, 35 + bear_score)
                return AgentSignal(
                    agent_name="IntradayMomentum", ticker=ticker, signal="SELL",
                    confidence=float(conf),
                    reasoning=f"Intraday SELL: {', '.join(bear_notes)}. Score {bear_score}/100.",
                    entry=round(cur_c, 2),
                    sl=round(cur_c + atr14 * 1.5, 2),
                    target=round(cur_c - atr14 * 2.5, 2),
                    timeframe="5m", indicators=indicators,
                )

            return self._hold(
                ticker,
                f"Intraday mixed: VWAP {vwap_dev:+.2f}%, vel {velocity:+.2f}%, vol {vol_ratio:.1f}×. No clear setup.",
                indicators=indicators,
            )

        except Exception as exc:
            logger.debug("[IntradayMomentum] error on %s: %s", ticker, exc)
            return AgentSignal(
                agent_name="IntradayMomentum", ticker=ticker, signal="HOLD",
                confidence=0.0, reasoning="Intraday analysis unavailable.", error=str(exc),
            )

    @staticmethod
    def _hold(ticker: str, reason: str, indicators: Dict = None) -> AgentSignal:
        return AgentSignal(
            agent_name="IntradayMomentum", ticker=ticker, signal="HOLD",
            confidence=25.0, reasoning=reason, indicators=indicators or {},
        )


# ─────────────────────────────────────────────────────────────────────────────
# MONTE CARLO SCENARIO ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MonteCarloScenarioEngine:
    """
    1000-path Geometric Brownian Motion simulation.
    Given entry, SL, target → probability of each outcome + expected P&L.
    """

    def run(
        self,
        entry:     float,
        sl:        float,
        target:    float,
        capital:   float   = 100_000.0,
        ticker:    str     = "",
        n:         int     = 1000,
        bars:      int     = 26,            # ~6.5hr session in 15-min bars
    ) -> Dict:
        if entry <= 0 or sl <= 0 or target <= 0:
            return {"error": "Invalid price inputs"}

        # Risk per trade: 1% of capital
        sl_dist = abs(entry - sl)
        if sl_dist < 1e-4:
            return {"error": "SL too close to entry"}

        risk_inr  = capital * 0.01
        quantity  = max(1, int(risk_inr / sl_dist))
        direction = 1 if target > entry else -1   # BUY or SELL

        # Use ATR-implied vol if prices available, else estimate from R-distance
        r_distance = sl_dist / entry
        daily_vol  = max(r_distance * 1.5, 0.008)   # daily σ
        bar_vol    = daily_vol / np.sqrt(26)         # per 15-min bar

        target_hits, sl_hits, neutral = 0, 0, 0
        final_prices: List[float] = []

        rng = np.random.default_rng()
        for _ in range(n):
            price = entry
            hit   = None
            for _ in range(bars):
                ret   = rng.normal(0, bar_vol)
                price = price * (1 + ret)
                # Check exit conditions (direction-aware)
                if direction == 1:   # BUY
                    if price >= target:
                        hit = "TARGET"; break
                    if price <= sl:
                        hit = "SL"; break
                else:                # SELL
                    if price <= target:
                        hit = "TARGET"; break
                    if price >= sl:
                        hit = "SL"; break

            final_prices.append(price)
            if hit == "TARGET":  target_hits += 1
            elif hit == "SL":    sl_hits += 1
            else:                neutral  += 1

        fp = np.array(final_prices)
        pnl_per_share = (fp - entry) * direction  # +ve = profit
        pnl_inr       = pnl_per_share * quantity

        return {
            "target_hit_prob":        round(target_hits / n, 3),
            "sl_hit_prob":            round(sl_hits / n, 3),
            "neutral_prob":           round(neutral / n, 3),
            "expected_pnl_inr":       round(float(np.mean(pnl_inr)), 2),
            "median_pnl_inr":         round(float(np.median(pnl_inr)), 2),
            "pnl_p5":                 round(float(np.percentile(pnl_inr, 5)), 2),
            "pnl_p95":                round(float(np.percentile(pnl_inr, 95)), 2),
            "max_adverse_excursion":  round(float(np.min(pnl_inr)), 2),
            "max_favorable_excursion": round(float(np.max(pnl_inr)), 2),
            "quantity":               quantity,
            "risk_per_trade_inr":     round(quantity * sl_dist, 2),
            "paths_analyzed":         n,
            "expected_rr":            round(abs(target - entry) / sl_dist, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY COLLABORATOR — Master Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class StrategyCollaborator:
    """
    Runs all strategy agents in parallel for a ticker,
    builds consensus, constructs Dreamer V3 input features,
    and optionally runs Monte Carlo scenario analysis.
    """

    def __init__(self):
        self.active_scanner     = ActiveStockScannerAgent()
        self.breakout15m        = BreakoutAgent15m()
        self.tech_composite     = TechnicalCompositeAgent()
        self.kronos             = KronosAgent()
        self.mirofish           = MiroFishAgent()
        self.intraday_momentum  = IntradayMomentumAgent()   # NEW
        self.monte_carlo_eng    = MonteCarloScenarioEngine()

        self._lock            = threading.Lock()
        self._discussion_cache: Dict[str, AgentDiscussion] = {}
        self._cache_ttl       = 120   # 2 minutes (reduced for fresher intraday data)

    # ── Core: run full discussion for one ticker ───────────────────────────

    def run_discussion(
        self,
        ticker:  str,
        capital: float = 100_000.0,
        risk_tolerance: str = "moderate",
        deep:    bool   = False,
    ) -> AgentDiscussion:
        """
        Parallel agent analysis → consensus → Dreamer V3 features → Monte Carlo.
        Fast mode: ~2–4s (all agents in parallel, no LLM call)
        Deep mode: ~12–15s (MiroFish calls LangGraph)
        """
        scan_id = str(uuid4())[:8].upper()
        t0      = time.time()

        # Check cache
        cache_key = f"{ticker}:{'deep' if deep else 'fast'}"
        with self._lock:
            cached = self._discussion_cache.get(cache_key)
        if cached:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(
                cached.timestamp.replace("Z", "+00:00")
            )).total_seconds()
            if age < self._cache_ttl:
                logger.info("[Collaborator] Cache hit for %s (age %.0fs)", ticker, age)
                return cached

        logger.info("[Collaborator] Starting discussion for %s (deep=%s)", ticker, deep)

        # ── Run agents in parallel ────────────────────────────────────────
        agent_tasks = {
            "active_scanner":   lambda: self.active_scanner.analyze(ticker),
            "breakout15m":      lambda: self.breakout15m.analyze(ticker),
            "tech_composite":   lambda: self.tech_composite.analyze(ticker),
            "kronos":           lambda: self.kronos.analyze(ticker),
            "mirofish":         lambda: self.mirofish.analyze(ticker, deep=deep),
            "intraday_momentum": lambda: self.intraday_momentum.analyze(ticker),
        }

        signals: List[AgentSignal] = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(fn): name for name, fn in agent_tasks.items()}
            for fut in as_completed(futures, timeout=35):
                try:
                    sig = fut.result()
                    signals.append(sig)
                except Exception as exc:
                    name = futures[fut]
                    logger.warning("[Collaborator] Agent %s failed: %s", name, exc)

        # ── Build consensus ────────────────────────────────────────────────
        discussion = self._build_consensus(ticker, scan_id, signals, capital, deep, t0)

        # ── Record for Adaptive Learning (price validated after 30 min) ───
        try:
            from .adaptive_learner import learner as _learner
            entry_price = 0.0
            kronos_sig  = "HOLD"
            kronos_conf = 50.0
            for s in signals:
                if s.entry > 0 and entry_price == 0:
                    entry_price = s.entry
                if s.agent_name == "KronosAI":
                    kronos_sig  = s.signal
                    kronos_conf = s.confidence
            _learner.record_prediction(
                ticker        = ticker,
                entry_price   = entry_price,
                consensus     = discussion.consensus,
                agent_signals = discussion.agent_agreement_map,
                kronos_signal = kronos_sig,
                kronos_conf   = kronos_conf,
            )
        except Exception as _le:
            logger.debug("[Collaborator] Learner record: %s", _le)

        # Cache result
        with self._lock:
            self._discussion_cache[cache_key] = discussion

        logger.info(
            "[Collaborator] Discussion done | %s | %s %.0f%% | agents=%d | %.0fms",
            ticker, discussion.consensus, discussion.consensus_confidence,
            len(signals), discussion.duration_ms,
        )
        return discussion

    # ── Hybrid scan: rank multiple tickers ────────────────────────────────

    def scan_and_rank(
        self,
        manual_tickers: List[str] = None,
        mode:           str       = "hybrid",   # auto | manual | hybrid
        capital:        float     = 100_000.0,
        top_n:          int       = 5,
    ) -> List[AgentDiscussion]:
        """
        Scan universe and return top-N tickers by agent consensus strength.
        auto  : NSE most-active + F&O
        manual: only manual_tickers
        hybrid: both combined
        Enhanced: filters HOLDs, sorts by intraday_score, runs MC for all picks.
        """
        tickers: List[str] = []

        if mode in ("auto", "hybrid"):
            active = self.active_scanner.scan_universe(25)  # increased to 25
            tickers.extend([a["ticker"] for a in active])

        if mode in ("manual", "hybrid") and manual_tickers:
            for t in manual_tickers:
                if t and t not in tickers:
                    tickers.append(t)

        if not tickers:
            tickers = ["RELIANCE.NS", "TCS.NS", "SBIN.NS", "HDFCBANK.NS", "ICICIBANK.NS"]

        # Limit to top 20 to balance coverage vs speed
        tickers = list(dict.fromkeys(tickers))[:20]

        results: List[AgentDiscussion] = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {
                ex.submit(self.run_discussion, t, capital): t
                for t in tickers
            }
            for fut in as_completed(futures, timeout=90):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.warning("[Collaborator] scan_and_rank ticker failed: %s", exc)

        # Sort by intraday_score (composite: confidence + win_prob + volume)
        results.sort(key=lambda d: d.intraday_score, reverse=True)

        # Prefer non-HOLD results; fall back to all if none
        non_hold = [d for d in results if d.consensus != "HOLD"]
        final = non_hold[:top_n] if non_hold else results[:top_n]

        logger.info(
            "[Collaborator] scan_and_rank done | total=%d, non_hold=%d, returning=%d",
            len(results), len(non_hold), len(final),
        )
        return final

    def get_active_stocks(self, limit: int = 25) -> List[Dict]:
        """Return most-active stock universe (cached)."""
        return self.active_scanner.scan_universe(limit)

    # ── Private: build consensus + Dreamer V3 features ────────────────────

    def _build_consensus(
        self,
        ticker:   str,
        scan_id:  str,
        signals:  List[AgentSignal],
        capital:  float,
        deep:     bool,
        t0:       float,
    ) -> AgentDiscussion:

        bull_count = sum(1 for s in signals if s.signal == "BUY")
        bear_count = sum(1 for s in signals if s.signal == "SELL")
        hold_count = sum(1 for s in signals if s.signal == "HOLD")
        total_agents = len(signals) or 1

        # ── Weighted score using DYNAMIC weights from AdaptiveLearner ─────
        try:
            from .adaptive_learner import learner as _learner
            _dyn_w = _learner.get_dynamic_weights()
        except Exception:
            _dyn_w = dict(AGENT_WEIGHTS)

        total_weight = sum(_dyn_w.get(s.agent_name, 0.15) for s in signals) + 1e-8
        weighted_score = sum(
            _dyn_w.get(s.agent_name, 0.15) * s.direction_score
            for s in signals
        ) / total_weight

        # Consensus signal — thresholds lowered to ±15 so mild directional bias triggers entries.
        # (Earlier ±25 was too conservative in quiet/sideways markets where most agents return HOLD.)
        if weighted_score > 15:
            consensus     = "BUY"
            consensus_conf = min(95.0, 40 + abs(weighted_score))
        elif weighted_score < -15:
            consensus     = "SELL"
            consensus_conf = min(95.0, 40 + abs(weighted_score))
        else:
            consensus     = "HOLD"
            consensus_conf = max(20.0, 40 - abs(weighted_score))

        # ── Conviction Score: % agents agreeing with consensus ────────────
        if consensus == "BUY":
            agreeing = bull_count
        elif consensus == "SELL":
            agreeing = bear_count
        else:
            agreeing = hold_count
        conviction_score = (agreeing / total_agents) * 100.0

        # ── Agent Agreement Map (for UI cross-talk viz) ───────────────────
        agent_agreement_map = {s.agent_name: s.signal for s in signals}

        # ── Volume Ratio (from IntradayMomentum or ActiveScanner) ─────────
        volume_ratio = 1.0
        for s in signals:
            if s.agent_name == "IntradayMomentum" and s.indicators.get("vol_ratio"):
                volume_ratio = float(s.indicators["vol_ratio"])
                break
            if s.agent_name == "ActiveScanner" and s.indicators.get("vol_ratio"):
                volume_ratio = float(s.indicators["vol_ratio"])

        # ── Dreamer V3 input features ──────────────────────────────────────
        agent_map = {s.agent_name: s for s in signals}
        _hold_sig = AgentSignal("", "", "HOLD", 0, "")
        dreamer_features = {
            "consensus_score":        round(weighted_score / 100.0, 4),
            "bull_fraction":          round(bull_count / total_agents, 3),
            "bear_fraction":          round(bear_count / total_agents, 3),
            "conviction_score":       round(conviction_score / 100.0, 3),
            "volume_ratio":           round(volume_ratio, 3),
            "kronos_direction":       agent_map.get("KronosAI", _hold_sig).direction_score / 100.0,
            "breakout15m_signal":     agent_map.get("Breakout15m", _hold_sig).direction_score / 100.0,
            "tech_composite_score":   agent_map.get("TechComposite", _hold_sig).direction_score / 100.0,
            "intraday_momentum":      agent_map.get("IntradayMomentum", _hold_sig).direction_score / 100.0,
            "agent_agreement_rate":   round(max(bull_count, bear_count, hold_count) / total_agents, 3),
            # Adaptive learning context
            "dynamic_weights_active": True,
        }

        # ── Dreamer V3 final signal ─────────────────────────────────────────
        dreamer_final  = consensus
        dreamer_conf   = consensus_conf

        # Include adaptive learning context in reasoning
        try:
            from .adaptive_learner import learner as _learn
            ls = _learn.get_state()
            best_a  = ls.get("best_agent", "KronosAI")
            best_a_acc = round(ls.get("accuracy_scores", {}).get(best_a, 50.0), 1)
            learn_iter = ls.get("learning_iterations", 0)
            learn_note = (
                f" [Learning iter {learn_iter}: best agent={best_a} acc={best_a_acc}%]"
                if learn_iter > 0 else ""
            )
        except Exception:
            learn_note = ""

        dreamer_reason = (
            f"{bull_count} bull · {bear_count} bear · {hold_count} hold · "
            f"conviction {conviction_score:.0f}%. "
            f"Weighted score: {weighted_score:+.1f}/100. Confidence: {consensus_conf:.0f}%."
            f"{learn_note}"
        )

        # ── Monte Carlo (run for best non-HOLD signal) ─────────────────────
        mc_result: Dict = {}
        best_sig = max(
            (s for s in signals if s.signal != "HOLD" and s.entry > 0 and s.sl > 0 and s.target > 0),
            key=lambda s: s.confidence,
            default=None,
        )
        if best_sig:
            mc_result = self.monte_carlo_eng.run(
                entry   = best_sig.entry,
                sl      = best_sig.sl,
                target  = best_sig.target,
                capital = capital,
                ticker  = ticker,
                n       = 1000,
            )

        # ── Win Probability from MC ─────────────────────────────────────────
        win_probability = round(mc_result.get("target_hit_prob", 0) * 100, 1)

        # ── Intraday Composite Score (for ranking across stocks) ────────────
        # Combines: confidence, win_prob, volume, conviction — all 0-100 scaled
        vol_boost      = min(2.0, volume_ratio) / 2.0  # 0..1
        win_boost      = win_probability / 100.0        # 0..1
        conv_boost     = conviction_score / 100.0       # 0..1
        conf_boost     = consensus_conf / 100.0         # 0..1
        if consensus == "HOLD":
            intraday_score = 0.0
        else:
            intraday_score = round(
                (conf_boost * 0.35 + win_boost * 0.30 + conv_boost * 0.20 + vol_boost * 0.15)
                * 100.0, 2
            )

        return AgentDiscussion(
            ticker               = ticker,
            scan_id              = scan_id,
            agent_signals        = signals,
            consensus            = consensus,
            consensus_confidence = consensus_conf,
            weighted_score       = weighted_score,
            bull_count           = bull_count,
            bear_count           = bear_count,
            hold_count           = hold_count,
            dreamer_input_features   = dreamer_features,
            dreamer_final_signal     = dreamer_final,
            dreamer_final_confidence = dreamer_conf,
            dreamer_reasoning        = dreamer_reason,
            monte_carlo              = mc_result,
            win_probability          = win_probability,
            conviction_score         = conviction_score,
            volume_ratio             = volume_ratio,
            intraday_score           = intraday_score,
            agent_agreement_map      = agent_agreement_map,
            duration_ms              = (time.time() - t0) * 1000,
            mode                     = "deep" if deep else "fast",
        )


# ── Module-level singleton ────────────────────────────────────────────────────
collaborator = StrategyCollaborator()

__all__ = [
    "StrategyCollaborator",
    "AgentDiscussion",
    "AgentSignal",
    "MonteCarloScenarioEngine",
    "ActiveStockScannerAgent",
    "BreakoutAgent15m",
    "TechnicalCompositeAgent",
    "KronosAgent",
    "MiroFishAgent",
    "IntradayMomentumAgent",
    "FNO_UNIVERSE",
    "collaborator",
]
