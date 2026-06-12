"""
Shared intraday plan calculator for all Top Trader concept scanners.
"""
import pandas as pd
from typing import Dict


def make_intraday_plan(df: pd.DataFrame, direction: str = "BUY") -> Dict:
    """
    Build a tight intraday Entry / SL / T1 / T2 plan using ATR.

    direction: "BUY" or "SELL"
    Returns dict with entry, sl, t1, t2, risk_per_share, rr_ratio, exit_by.
    """
    try:
        closes = df['close'].astype(float)
        highs  = df['high'].astype(float)
        lows   = df['low'].astype(float)

        current = float(closes.iloc[-1])

        # ── ATR(5) for intraday SL sizing ──────────────────────────────────
        atr_vals = []
        for i in range(1, min(6, len(df))):
            tr = max(
                float(highs.iloc[-i]) - float(lows.iloc[-i]),
                abs(float(highs.iloc[-i]) - float(closes.iloc[-i - 1])),
                abs(float(lows.iloc[-i])  - float(closes.iloc[-i - 1])),
            )
            atr_vals.append(tr)
        atr = sum(atr_vals) / len(atr_vals) if atr_vals else current * 0.005

        # Previous day reference levels
        prev_low  = float(lows.iloc[-2])  if len(lows)  >= 2 else current * 0.995
        prev_high = float(highs.iloc[-2]) if len(highs) >= 2 else current * 1.005

        if direction == "BUY":
            entry = round(current * 1.0015, 2)          # 0.15% confirmation buffer
            sl    = round(max(prev_low - current * 0.001,
                              entry - atr * 0.75), 2)   # tighter of: prev-day low OR ATR-based
            sl    = min(sl, entry * 0.994)               # hard cap: max 0.6% SL
            risk  = round(entry - sl, 2)
            t1    = round(entry + risk * 2, 2)           # 1:2
            t2    = round(entry + risk * 3, 2)           # 1:3
        else:   # SELL / SHORT
            entry = round(current * 0.9985, 2)
            sl    = round(min(prev_high + current * 0.001,
                              entry + atr * 0.75), 2)
            sl    = max(sl, entry * 1.006)
            risk  = round(sl - entry, 2)
            t1    = round(entry - risk * 2, 2)
            t2    = round(entry - risk * 3, 2)

        risk = max(risk, 0.01)  # guard against zero

        return {
            "direction":      direction,
            "entry":          entry,
            "sl":             sl,
            "t1":             t1,
            "t2":             t2,
            "risk_per_share": risk,
            "rr_ratio":       "1:2 / 1:3",
            "exit_by":        "3:15 PM IST",
            "trade_type":     "INTRADAY",
        }
    except Exception:
        return {
            "direction":      direction,
            "entry":          None,
            "sl":             None,
            "t1":             None,
            "t2":             None,
            "risk_per_share": None,
            "rr_ratio":       "1:2 / 1:3",
            "exit_by":        "3:15 PM IST",
            "trade_type":     "INTRADAY",
        }
