"""
Minervini VCP (Volatility Contraction Pattern) Scanner
For NSE Indian Market
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class MinerviniVCPScanner:
    """
    Mark Minervini Volatility Contraction Pattern (VCP) Scanner
    Focused on Indian NSE Market
    """

    def __init__(self):
        self.min_contractions = 3
        self.volume_surge_multiplier = 1.5
        self.relative_strength_period = 50

    def analyze_stock(self, df: pd.DataFrame, symbol: str,
                      market_df: Optional[pd.DataFrame] = None) -> Dict:
        try:
            if len(df) < 200:
                return self._no_match_response(symbol, "Insufficient data (need min 200 candles)")

            df = df.copy()

            # Calculate SMAs
            df['sma50']  = df['close'].rolling(window=50).mean()
            df['sma150'] = df['close'].rolling(window=150).mean()
            df['sma200'] = df['close'].rolling(window=200).mean()
            df['rolling_high_52w'] = df['close'].rolling(window=252).max()

            current_price  = float(df['close'].iloc[-1])
            current_volume = float(df['volume'].iloc[-1])
            avg_volume     = float(df['volume'].rolling(window=20).mean().iloc[-1])

            # 1. Trend Filter (Stage 2)
            if not self._is_in_uptrend(df):
                return self._no_match_response(symbol, "Not in Stage 2 Uptrend (below key SMAs)")

            if not self._near_52w_high(df):
                return self._no_match_response(symbol, "Not near 52-week high")

            # 2. Volatility Contraction Detection
            contractions = self._detect_contractions(df)

            if len(contractions) < self.min_contractions:
                return self._no_match_response(
                    symbol,
                    f"Only {len(contractions)} contractions detected. Need at least {self.min_contractions}"
                )

            # 3. Breakout Check
            is_breakout = self._check_breakout(df, contractions, current_volume, avg_volume)

            # 4. Relative Strength
            rel_strength = self._calculate_relative_strength(df, market_df)

            # Final Scoring
            confluence_score = self._calculate_confluence_score(contractions, is_breakout, rel_strength)
            reason           = self._generate_reason(contractions, is_breakout, rel_strength)

            return {
                "symbol":            symbol,
                "is_match":          bool(confluence_score >= 75),
                "confluence_score":  round(float(confluence_score), 2),
                "stage":             "VCP_BREAKOUT" if is_breakout else "VCP_CONTRACTION",
                "contraction_levels": [
                    {
                        "index":       int(c["index"]),
                        "range_pct":   float(c["range_pct"]),
                        "volume_dryup": bool(c["volume_dryup"]),
                        "tightness":   str(c["tightness"]),
                    }
                    for c in contractions
                ],
                "current_price": round(current_price, 2),
                "entry_price":   round(float(df['high'].iloc[-1]) * 1.01, 2) if is_breakout else None,
                "stop_loss":     round(float(df['low'].iloc[-5:].min()) * 0.98, 2),
                "target":        round(current_price * 1.15, 2),
                "reason":        reason,
                "strength_signals": self._get_strength_signals(contractions, is_breakout, rel_strength),
                "rel_strength":     round(float(rel_strength), 2),
                "timestamp":        datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return self._no_match_response(symbol, f"Error: {str(e)}")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _is_in_uptrend(self, df: pd.DataFrame) -> bool:
        latest = df.iloc[-1]
        return (latest['close'] > latest['sma50'] and
                latest['close'] > latest.get('sma150', 0) and
                latest['close'] > latest.get('sma200', 0))

    def _near_52w_high(self, df: pd.DataFrame, threshold: float = 0.85) -> bool:
        current  = float(df['close'].iloc[-1])
        high_52w = float(df['rolling_high_52w'].iloc[-1])
        return current >= high_52w * threshold

    def _detect_contractions(self, df: pd.DataFrame, lookback: int = 120) -> List[Dict]:
        recent = df.iloc[-lookback:]
        contractions = []

        for i in range(10, len(recent) - 5):
            window_high = float(recent['high'].iloc[max(0, i-10):i+10].max())
            window_low  = float(recent['low'].iloc[max(0, i-10):i+10].min())
            range_pct   = (window_high - window_low) / window_low * 100

            avg_vol  = float(recent['volume'].iloc[max(0, i-20):i].mean())
            curr_vol = float(recent['volume'].iloc[i])

            if range_pct < 25:
                contractions.append({
                    "index":       int(i),
                    "range_pct":   round(range_pct, 2),
                    "volume_dryup": curr_vol < avg_vol * 0.7,
                    "tightness":   "tight" if range_pct < 8 else "medium" if range_pct < 15 else "wide",
                })

        # Keep only progressively tighter ones
        filtered = []
        for i in range(1, len(contractions)):
            if contractions[i]['range_pct'] < contractions[i-1]['range_pct'] * 0.95:
                filtered.append(contractions[i])

        return filtered[-5:]

    def _check_breakout(self, df: pd.DataFrame, contractions: List,
                        current_vol: float, avg_vol: float) -> bool:
        if not contractions:
            return False
        recent_high = float(df['high'].iloc[-10:].max())
        return (float(df['close'].iloc[-1]) > recent_high and
                current_vol > avg_vol * self.volume_surge_multiplier)

    def _calculate_relative_strength(self, df: pd.DataFrame,
                                     market_df: Optional[pd.DataFrame]) -> float:
        if market_df is None or market_df.empty:
            return 70.0
        stock_return  = float(df['close'].pct_change(20).iloc[-1])
        market_return = float(market_df['close'].pct_change(20).iloc[-1])
        return 50.0 + (stock_return - market_return) * 100

    def _calculate_confluence_score(self, contractions: List,
                                    is_breakout: bool, rel_strength: float) -> float:
        score  = 60.0
        score += len(contractions) * 8
        if is_breakout:
            score += 25
        score += (rel_strength - 50) * 0.4
        return min(98.0, max(40.0, score))

    def _generate_reason(self, contractions: List,
                         is_breakout: bool, rel_strength: float) -> str:
        reason = f"Detected {len(contractions)} volatility contractions with progressive tightening."
        if is_breakout:
            reason += " Strong volume breakout observed."
        reason += f" Relative Strength: {rel_strength:.1f}."
        return reason

    def _get_strength_signals(self, contractions: List,
                              is_breakout: bool, rel_strength: float) -> List[str]:
        signals = []
        if len(contractions) >= 4:
            signals.append("Excellent Contraction Pattern")
        if is_breakout:
            signals.append("Volume Breakout Confirmed")
        if rel_strength > 70:
            signals.append("Strong Relative Strength")
        return signals

    def _no_match_response(self, symbol: str, reason: str) -> Dict:
        return {
            "symbol":            symbol,
            "is_match":          False,
            "confluence_score":  0.0,
            "stage":             "NO_MATCH",
            "contraction_levels": [],
            "current_price":     None,
            "entry_price":       None,
            "stop_loss":         None,
            "target":            None,
            "reason":            reason,
            "strength_signals":  [],
            "rel_strength":      0.0,
            "timestamp":         datetime.now().isoformat(),
        }
