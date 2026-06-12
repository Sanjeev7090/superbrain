"""
Jesse Livermore Pivotal Point Breakout Scanner
For NSE Indian Market
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class LivermorePivotalScanner:
    """
    Jesse Livermore Pivotal Point Breakout Strategy
    - Focus on price action, pivotal points, and trend continuation
    """

    def __init__(self):
        self.buffer_points       = 0.02   # 2% buffer for breakout confirmation
        self.volume_multiplier   = 1.4
        self.min_lookback        = 100

    def analyze_stock(self, df: pd.DataFrame, symbol: str,
                      market_df: Optional[pd.DataFrame] = None) -> Dict:
        try:
            if len(df) < self.min_lookback:
                return self._no_match_response(symbol, "Insufficient historical data")

            df = df.copy().sort_index()

            current_price  = float(df['close'].iloc[-1])
            current_volume = float(df['volume'].iloc[-1])
            avg_volume     = float(df['volume'].rolling(20).mean().iloc[-1])

            pivots   = self._detect_pivotal_points(df)
            trend    = self._determine_trend(df)
            breakout = self._check_pivotal_breakout(df, pivots, trend, current_volume, avg_volume)

            confluence_score = self._calculate_score(breakout, trend, pivots)
            reason           = self._generate_reason(trend, breakout, pivots)

            last_pivots = [
                {
                    "type":  str(p["type"]),
                    "price": round(float(p["price"]), 2),
                    "index": int(p["index"]),
                }
                for p in pivots[-3:]
            ]

            from agents.intraday_utils import make_intraday_plan
            direction = "BUY" if trend == "UPTREND" else ("SELL" if trend == "DOWNTREND" else "BUY")
            return {
                "symbol":          symbol,
                "is_match":        bool(confluence_score >= 72),
                "confluence_score": round(float(confluence_score), 2),
                "stage":           "LIVERMORE_BREAKOUT" if breakout["is_valid"] else "CONSOLIDATION",
                "trend":           trend,
                "pivotal_points":  last_pivots,
                "current_price":   round(current_price, 2),
                "entry_price":     round(float(breakout["entry"]), 2) if breakout["is_valid"] else None,
                "stop_loss":       round(float(breakout["stop_loss"]), 2) if breakout["is_valid"] else None,
                "target":          round(current_price * 1.20, 2),
                "intraday_plan":   make_intraday_plan(df, direction),
                "reason":          reason,
                "strength_signals": self._get_signals(trend, breakout),
                "timestamp":       datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error in Livermore analysis for {symbol}: {e}")
            return self._no_match_response(symbol, f"Analysis error: {str(e)}")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _detect_pivotal_points(self, df: pd.DataFrame, window: int = 5) -> List[Dict]:
        pivots = []
        for i in range(window, len(df) - window):
            if df['high'].iloc[i] == df['high'].iloc[i - window: i + window + 1].max():
                pivots.append({"type": "high", "price": float(df['high'].iloc[i]), "index": i})
            elif df['low'].iloc[i] == df['low'].iloc[i - window: i + window + 1].min():
                pivots.append({"type": "low", "price": float(df['low'].iloc[i]), "index": i})
        return pivots

    def _determine_trend(self, df: pd.DataFrame) -> str:
        sma50  = float(df['close'].rolling(50).mean().iloc[-1])
        sma200 = float(df['close'].rolling(200).mean().iloc[-1])
        close  = float(df['close'].iloc[-1])
        if close > sma50 > sma200:
            return "UPTREND"
        if close < sma50 < sma200:
            return "DOWNTREND"
        return "SIDEWAYS"

    def _check_pivotal_breakout(self, df: pd.DataFrame, pivots: List,
                                trend: str, current_vol: float, avg_vol: float) -> Dict:
        if not pivots:
            return {"is_valid": False, "entry": 0.0, "stop_loss": 0.0}

        recent_pivot = pivots[-1]
        buffer       = float(recent_pivot["price"]) * self.buffer_points
        close        = float(df["close"].iloc[-1])

        if trend == "UPTREND" and recent_pivot["type"] == "high":
            breakout_price = float(recent_pivot["price"]) + buffer
            if close > breakout_price and current_vol > avg_vol * self.volume_multiplier:
                return {
                    "is_valid":  True,
                    "entry":     breakout_price,
                    "stop_loss": float(recent_pivot["price"]) * 0.97,
                    "type":      "upside_breakout",
                }

        elif trend == "DOWNTREND" and recent_pivot["type"] == "low":
            breakout_price = float(recent_pivot["price"]) - buffer
            if close < breakout_price and current_vol > avg_vol * self.volume_multiplier:
                return {
                    "is_valid":  True,
                    "entry":     breakout_price,
                    "stop_loss": float(recent_pivot["price"]) * 1.03,
                    "type":      "downside_breakout",
                }

        return {"is_valid": False, "entry": 0.0, "stop_loss": 0.0}

    def _calculate_score(self, breakout: Dict, trend: str, pivots: List) -> float:
        score = 50.0
        if breakout["is_valid"]:
            score += 30
        if trend == "UPTREND":
            score += 15
        score += min(len(pivots) * 2, 10)
        return min(95.0, score)

    def _generate_reason(self, trend: str, breakout: Dict, pivots: List) -> str:
        if breakout["is_valid"]:
            return (
                f"Strong {trend} with Pivotal Point breakout on increased volume. "
                f"{len(pivots)} pivots identified."
            )
        return f"In {trend} phase. Waiting for pivotal point breakout confirmation."

    def _get_signals(self, trend: str, breakout: Dict) -> List[str]:
        signals = [f"Trend: {trend}"]
        if breakout["is_valid"]:
            signals.append("Pivotal Breakout Confirmed")
        return signals

    def _no_match_response(self, symbol: str, reason: str) -> Dict:
        return {
            "symbol":           symbol,
            "is_match":         False,
            "confluence_score": 0.0,
            "stage":            "NO_MATCH",
            "trend":            "UNKNOWN",
            "pivotal_points":   [],
            "current_price":    None,
            "entry_price":      None,
            "stop_loss":        None,
            "target":           None,
            "reason":           reason,
            "strength_signals": [],
            "timestamp":        datetime.now().isoformat(),
        }
