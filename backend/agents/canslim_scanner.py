"""
William O'Neil CANSLIM Strategy Scanner
For NSE Indian Market
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class CANSLIMScanner:
    """
    William O'Neil CANSLIM Strategy Scanner
    Focused on Indian NSE Market
    """

    def __init__(self):
        self.rs_period               = 50
        self.volume_surge            = 1.5
        self.earnings_growth_threshold = 25

    def analyze_stock(self, df: pd.DataFrame, symbol: str,
                      market_df: Optional[pd.DataFrame] = None,
                      fundamentals: Optional[Dict] = None) -> Dict:
        try:
            if len(df) < 200:
                return self._no_match_response(symbol, "Insufficient data (need 200+ bars)")

            df = df.copy()
            current_price  = float(df['close'].iloc[-1])
            current_volume = float(df['volume'].iloc[-1])
            avg_volume     = float(df['volume'].rolling(20).mean().iloc[-1])

            earnings_score     = self._check_earnings(fundamentals)
            new_high           = self._is_new_high(df)
            volume_demand      = self._check_volume_demand(df, current_volume, avg_volume)
            relative_strength  = self._calculate_relative_strength(df, market_df)
            institutional_proxy = self._institutional_proxy(df)
            market_trend       = self._check_market_trend(market_df)

            canslim_score = self._calculate_canslim_score(
                earnings_score, new_high, volume_demand,
                relative_strength, institutional_proxy, market_trend
            )
            reason   = self._generate_reason(earnings_score, new_high, volume_demand,
                                              relative_strength, market_trend)
            is_match = bool(canslim_score >= 78)

            stop_loss_val = float(df['low'].rolling(10).min().iloc[-1]) * 0.97

            from agents.intraday_utils import make_intraday_plan
            return {
                "symbol":          symbol,
                "is_match":        is_match,
                "confluence_score": round(float(canslim_score), 2),
                "strategy":        "CANSLIM",
                "current_price":   round(current_price, 2),
                "stage":           "CANSLIM_LEADER" if is_match else "WEAK",
                "entry_price":     round(float(df['high'].iloc[-1]) * 1.005, 2) if new_high else None,
                "stop_loss":       round(stop_loss_val, 2),
                "target":          round(current_price * 1.25, 2),
                "intraday_plan":   make_intraday_plan(df, "BUY"),
                "reason":          reason,
                "canslim_breakdown": {
                    "C_A_Earnings":      int(earnings_score),
                    "N_NewHigh":         bool(new_high),
                    "S_Volume":          int(volume_demand),
                    "L_RelativeStrength": round(float(relative_strength), 1),
                    "I_Institutional":   int(institutional_proxy),
                    "M_MarketTrend":     str(market_trend),
                },
                "strength_signals": self._get_strong_signals(
                    earnings_score, new_high, volume_demand, relative_strength
                ),
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"CANSLIM Error for {symbol}: {e}")
            return self._no_match_response(symbol, f"Error: {str(e)}")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _check_earnings(self, fundamentals: Optional[Dict]) -> int:
        if not fundamentals:
            return 65
        qoq = fundamentals.get('qoq_earnings_growth', 0)
        yoy = fundamentals.get('yoy_earnings_growth', 0)
        if qoq > 40 or yoy > 50:
            return 95
        if qoq > 25 or yoy > 30:
            return 80
        return 50

    def _is_new_high(self, df: pd.DataFrame, period: int = 252) -> bool:
        rolling_high = float(df['close'].rolling(period).max().iloc[-1])
        return float(df['close'].iloc[-1]) >= rolling_high * 0.98

    def _check_volume_demand(self, df: pd.DataFrame,
                              current_vol: float, avg_vol: float) -> int:
        if current_vol > avg_vol * self.volume_surge:
            return 90
        if current_vol > avg_vol * 1.2:
            return 75
        return 55

    def _calculate_relative_strength(self, df: pd.DataFrame,
                                      market_df: Optional[pd.DataFrame]) -> float:
        if market_df is None or market_df.empty:
            return 70.0
        stock_ret  = float(df['close'].pct_change(60).iloc[-1])
        market_ret = float(market_df['close'].pct_change(60).iloc[-1])
        rs = 50.0 + (stock_ret - market_ret) * 150
        return float(max(30.0, min(98.0, rs)))

    def _institutional_proxy(self, df: pd.DataFrame) -> int:
        recent_vol = float(df['volume'].tail(20).mean())
        avg_vol    = float(df['volume'].mean())
        return 85 if recent_vol > avg_vol * 1.3 else 60

    def _check_market_trend(self, market_df: Optional[pd.DataFrame]) -> str:
        if market_df is None or len(market_df) < 50:
            return "NEUTRAL"
        sma50 = float(market_df['close'].rolling(50).mean().iloc[-1])
        return "UPTREND" if float(market_df['close'].iloc[-1]) > sma50 else "DOWNTREND"

    def _calculate_canslim_score(self, earnings: int, new_high: bool, volume: int,
                                  rs: float, inst: int, market: str) -> float:
        score = (earnings + volume + inst) / 3.0
        if new_high:
            score += 18
        if rs > 75:
            score += 15
        if market == "UPTREND":
            score += 12
        return min(97.0, score)

    def _generate_reason(self, earnings: int, new_high: bool, volume: int,
                          rs: float, market: str) -> str:
        parts = []
        if earnings >= 80:
            parts.append("Strong Earnings Growth")
        if new_high:
            parts.append("Trading at New Highs")
        if volume >= 75:
            parts.append("Strong Volume Demand")
        if rs > 75:
            parts.append(f"High Relative Strength ({rs:.1f})")
        if market == "UPTREND":
            parts.append("Bullish Market Trend")
        return " + ".join(parts) if parts else "CANSLIM criteria not fully met"

    def _get_strong_signals(self, earnings: int, new_high: bool,
                             volume: int, rs: float) -> List[str]:
        signals = []
        if earnings >= 80:
            signals.append("Strong C&A (Earnings)")
        if new_high:
            signals.append("N - New Highs")
        if volume >= 80:
            signals.append("S - Heavy Volume")
        if rs > 80:
            signals.append("L - Market Leader")
        return signals

    def _no_match_response(self, symbol: str, reason: str) -> Dict:
        return {
            "symbol":          symbol,
            "is_match":        False,
            "confluence_score": 0.0,
            "strategy":        "CANSLIM",
            "stage":           "NO_MATCH",
            "current_price":   None,
            "entry_price":     None,
            "stop_loss":       None,
            "target":          None,
            "reason":          reason,
            "canslim_breakdown": {},
            "strength_signals": [],
            "timestamp":       datetime.now().isoformat(),
        }
