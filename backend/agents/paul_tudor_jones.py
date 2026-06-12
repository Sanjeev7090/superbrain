"""
Paul Tudor Jones Macro + Trend Following Scanner
For NSE Indian Market
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class PaulTudorJonesScanner:
    """
    Paul Tudor Jones Macro + Trend Following Scanner
    Focus: 200-day MA Trend, Asymmetric Risk-Reward, Macro Proxy
    """

    def __init__(self):
        self.ma_long_period      = 200
        self.volume_multiplier   = 1.35
        self.min_risk_reward     = 3.0
        self.macro_sentiment_bias = 0.0

    def analyze_stock(self, df: pd.DataFrame, symbol: str,
                      market_df: Optional[pd.DataFrame] = None,
                      macro_data: Optional[Dict] = None) -> Dict:
        try:
            if len(df) < 250:
                return self._no_match_response(symbol, "Insufficient data for 200-day MA (need 250+ bars)")

            df = df.copy()
            df['ma200'] = df['close'].rolling(self.ma_long_period).mean()

            current_price  = float(df['close'].iloc[-1])
            current_volume = float(df['volume'].iloc[-1])
            avg_volume     = float(df['volume'].rolling(20).mean().iloc[-1])

            above_ma200     = bool(current_price > float(df['ma200'].iloc[-1]))
            trend_strength  = self._calculate_trend_strength(df)
            momentum_break  = self._check_momentum_break(df, above_ma200, current_volume, avg_volume)
            risk_reward     = self._calculate_risk_reward(df, above_ma200)
            macro_score     = self._macro_proxy(macro_data, market_df)

            ptj_score = self._calculate_ptj_score(
                above_ma200, trend_strength, momentum_break, risk_reward, macro_score
            )
            reason   = self._generate_reason(above_ma200, momentum_break, risk_reward, macro_score)
            is_match = bool(ptj_score >= 76)

            stop_loss_val = float(df['low'].rolling(20).min().iloc[-1]) * 0.96

            from agents.intraday_utils import make_intraday_plan
            direction = "BUY" if above_ma200 else "SELL"
            return {
                "symbol":           symbol,
                "is_match":         is_match,
                "confluence_score": round(float(ptj_score), 2),
                "strategy":         "PAUL_TUDOR_JONES",
                "current_price":    round(current_price, 2),
                "stage":            "PTJ_UPTREND" if (above_ma200 and is_match) else "PTJ_WATCH",
                "entry_price":      round(current_price * 1.005, 2) if momentum_break else None,
                "stop_loss":        round(stop_loss_val, 2),
                "target":           round(current_price * 1.25, 2),
                "risk_reward_ratio": round(float(risk_reward), 2),
                "above_ma200":      above_ma200,
                "trend_strength":   round(float(trend_strength), 1),
                "macro_score":      round(float(macro_score), 1),
                "intraday_plan":    make_intraday_plan(df, direction),
                "reason":           reason,
                "strength_signals": self._get_signals(above_ma200, momentum_break, risk_reward),
                "timestamp":        datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"PTJ Scanner Error for {symbol}: {e}")
            return self._no_match_response(symbol, f"Error: {str(e)}")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _calculate_trend_strength(self, df: pd.DataFrame) -> float:
        ma200    = float(df['ma200'].iloc[-1])
        price    = float(df['close'].iloc[-1])
        distance = (price - ma200) / ma200 * 100
        return min(95.0, max(40.0, 60.0 + distance * 2))

    def _check_momentum_break(self, df: pd.DataFrame, above_ma: bool,
                               current_vol: float, avg_vol: float) -> bool:
        if not above_ma:
            return False
        recent_high = float(df['high'].rolling(10).max().iloc[-1])
        return (float(df['close'].iloc[-1]) > recent_high and
                current_vol > avg_vol * self.volume_multiplier)

    def _calculate_risk_reward(self, df: pd.DataFrame, above_ma: bool) -> float:
        recent_low = float(df['low'].rolling(20).min().iloc[-1])
        current    = float(df['close'].iloc[-1])
        risk       = current - recent_low
        if risk <= 0:
            return 1.5
        target = current * 1.25 if above_ma else current * 0.75
        reward = abs(target - current)
        return float(reward / risk)

    def _macro_proxy(self, macro_data: Optional[Dict],
                     market_df: Optional[pd.DataFrame]) -> float:
        if not macro_data:
            if market_df is not None and len(market_df) > 50:
                ma200_val = float(market_df['close'].rolling(200).mean().iloc[-1])
                above = float(market_df['close'].iloc[-1]) > ma200_val
                return 85.0 if above else 45.0
            return 60.0
        return float(macro_data.get('macro_score', 60))

    def _calculate_ptj_score(self, above_ma: bool, trend_strength: float,
                              momentum: bool, rr: float, macro: float) -> float:
        score = 50.0
        if above_ma:
            score += 20
        score += (trend_strength - 60) * 0.6
        if momentum:
            score += 18
        if rr >= self.min_risk_reward:
            score += 15
        score += (macro - 60) * 0.4
        return min(97.0, max(40.0, score))

    def _generate_reason(self, above_ma: bool, momentum: bool,
                          rr: float, macro: float) -> str:
        parts = []
        if above_ma:
            parts.append("Above 200-day MA (PTJ Core Rule)")
        if momentum:
            parts.append("Momentum Breakout with Volume")
        if rr >= 3.0:
            parts.append(f"Strong {rr:.1f}:1 Risk-Reward")
        if macro > 70:
            parts.append("Positive Macro Environment")
        return " + ".join(parts) if parts else "Below 200-day MA — Stay Defensive"

    def _get_signals(self, above_ma: bool, momentum: bool, rr: float) -> List[str]:
        signals = []
        if above_ma:
            signals.append("200-day MA Bullish")
        if momentum:
            signals.append("Volume Confirmed Breakout")
        if rr >= 3.0:
            signals.append(f"Asymmetry {rr:.1f}:1")
        return signals

    def _no_match_response(self, symbol: str, reason: str) -> Dict:
        return {
            "symbol":           symbol,
            "is_match":         False,
            "confluence_score": 0.0,
            "strategy":         "PAUL_TUDOR_JONES",
            "stage":            "NO_MATCH",
            "current_price":    None,
            "entry_price":      None,
            "stop_loss":        None,
            "target":           None,
            "risk_reward_ratio": 0.0,
            "above_ma200":      False,
            "trend_strength":   0.0,
            "macro_score":      0.0,
            "reason":           reason,
            "strength_signals": [],
            "timestamp":        datetime.now().isoformat(),
        }
