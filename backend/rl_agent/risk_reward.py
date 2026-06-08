"""
Risk-Adjusted Reward Engine — replaces naive P&L reward in DreamerV3.

Computes composite reward:
  R = pnl_component
    + sharpe_bonus     (rolling 20-step Sharpe × 0.05)
    - cvar_penalty     (tail-risk exposure × 0.1)
    + kelly_alignment  (position size vs Kelly fraction alignment × 0.03)
    - drawdown_penalty (current drawdown × 0.05)
    + sortino_bonus    (downside-deviation adjusted return × 0.03)
"""

import numpy as np
from collections import deque
from typing import Deque

RISK_FREE = 0.065 / 252      # India: ~6.5% annual → daily
WINDOW    = 20               # rolling window for risk metrics
CVAR_PCT  = 0.05             # worst 5% of returns


class RiskAdjustedRewardEngine:
    """
    Stateful reward engine — maintains rolling return history per episode.
    Call `reset()` at episode start, `compute(...)` at each step.
    """

    def __init__(self):
        self._returns: Deque[float] = deque(maxlen=WINDOW * 5)  # longer for CVaR
        self._peak_equity = 1.0
        self._equity      = 1.0

    # ── per-episode reset ──────────────────────────────────────────────────────

    def reset(self):
        self._returns.clear()
        self._peak_equity = 1.0
        self._equity      = 1.0

    # ── main API ──────────────────────────────────────────────────────────────

    def compute(
        self,
        raw_pnl_pct  : float,   # step-level P&L as fraction of capital (e.g. 0.01 = +1%)
        position_size: float,   # current position fraction [0,1]
        kelly_fraction: float,  # suggested Kelly fraction [0,1]
    ) -> tuple:
        """
        Returns (risk_adjusted_reward, breakdown_dict).
        breakdown_dict has Sharpe, CVaR, Kelly alignment, drawdown components.
        """
        self._returns.append(raw_pnl_pct)

        # Update synthetic equity curve
        self._equity     *= (1 + raw_pnl_pct)
        self._peak_equity = max(self._peak_equity, self._equity)
        drawdown = (self._peak_equity - self._equity) / (self._peak_equity + 1e-9)

        # ── 1. P&L component ─────────────────────────────────────────────────
        pnl_comp = np.clip(raw_pnl_pct * 50, -2.0, 2.0)   # scale & clip

        # ── 2. Rolling Sharpe bonus ───────────────────────────────────────────
        sharpe_bonus = 0.0
        if len(self._returns) >= 5:
            r = np.array(list(self._returns)[-WINDOW:])
            excess = r - RISK_FREE
            if excess.std() > 1e-8:
                sharpe = excess.mean() / excess.std() * np.sqrt(252)
                sharpe_bonus = float(np.clip(sharpe, -3, 3)) * 0.05

        # ── 3. CVaR penalty ───────────────────────────────────────────────────
        cvar_penalty = 0.0
        if len(self._returns) >= 10:
            ret_arr = np.array(list(self._returns))
            var_threshold = np.percentile(ret_arr, CVAR_PCT * 100)
            tail_returns = ret_arr[ret_arr <= var_threshold]
            if len(tail_returns) > 0:
                cvar = float(tail_returns.mean())   # negative value
                cvar_penalty = abs(cvar) * 0.10     # penalty = 10% of CVaR magnitude

        # ── 4. Dynamic Kelly alignment ────────────────────────────────────────
        # Reward if position size ≈ Kelly fraction, penalise over-sizing
        kelly_align = 0.0
        if kelly_fraction > 0:
            diff = position_size - kelly_fraction
            kelly_align = -abs(diff) * 0.03          # penalise deviation from Kelly

        # ── 5. Drawdown penalty ───────────────────────────────────────────────
        dd_penalty = drawdown * 0.05

        # ── 6. Sortino bonus ──────────────────────────────────────────────────
        sortino_bonus = 0.0
        if len(self._returns) >= 5:
            r = np.array(list(self._returns)[-WINDOW:])
            excess   = r - RISK_FREE
            downside = excess[excess < 0]
            if len(downside) > 0 and downside.std() > 1e-8:
                sortino = excess.mean() / downside.std() * np.sqrt(252)
                sortino_bonus = float(np.clip(sortino, -2, 2)) * 0.03

        # ── Total ─────────────────────────────────────────────────────────────
        total = pnl_comp + sharpe_bonus - cvar_penalty + kelly_align - dd_penalty + sortino_bonus

        breakdown = {
            "pnl_comp":      round(float(pnl_comp), 6),
            "sharpe_bonus":  round(float(sharpe_bonus), 6),
            "cvar_penalty":  round(float(cvar_penalty), 6),
            "kelly_align":   round(float(kelly_align), 6),
            "dd_penalty":    round(float(dd_penalty), 6),
            "sortino_bonus": round(float(sortino_bonus), 6),
            "total":         round(float(total), 6),
            "drawdown":      round(float(drawdown), 6),
        }
        return float(total), breakdown


# ─── Dynamic Kelly Criterion ─────────────────────────────────────────────────

def dynamic_kelly(
    win_rate    : float,     # historical win rate [0,1]
    avg_win     : float,     # average winning trade return (positive)
    avg_loss    : float,     # average losing trade return (positive magnitude)
    fraction    : float = 0.25,  # fractional Kelly (0.25 = quarter Kelly)
    max_f       : float = 0.50,  # hard cap on position size
) -> float:
    """
    f* = W/L - (1-W)/W  — where W=win_rate, L=avg_loss/avg_win
    Returns fraction × f*, capped at max_f.
    """
    if avg_win <= 1e-9 or avg_loss <= 1e-9:
        return 0.05  # minimum sizing when history is thin
    b = avg_win / avg_loss          # payoff ratio
    f_star = (b * win_rate - (1 - win_rate)) / b
    f_star = max(f_star, 0.0)       # Kelly is ≥ 0 (never short the game)
    return min(fraction * f_star, max_f)


# ─── CVaR (Expected Shortfall) ────────────────────────────────────────────────

def compute_cvar(returns: np.ndarray, alpha: float = 0.05) -> dict:
    """
    Compute CVaR (Expected Shortfall) at confidence level (1-alpha).

    Returns dict with var, cvar, max_drawdown, volatility.
    """
    if len(returns) < 10:
        return {"var": 0.0, "cvar": 0.0, "max_drawdown": 0.0, "volatility": 0.0}

    r = np.array(returns, dtype=float)
    var  = float(np.percentile(r, alpha * 100))
    tail = r[r <= var]
    cvar = float(tail.mean()) if len(tail) > 0 else var

    # Max drawdown from cumulative returns
    cum = np.cumprod(1 + r)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / (peak + 1e-9)
    max_dd = float(dd.min())

    return {
        "var":          round(var, 6),
        "cvar":         round(cvar, 6),
        "max_drawdown": round(max_dd, 6),
        "volatility":   round(float(r.std() * np.sqrt(252)), 6),
        "sharpe":       round(float((r.mean() - RISK_FREE) / (r.std() + 1e-9) * np.sqrt(252)), 4),
        "sortino":      round(float(
            (r.mean() - RISK_FREE) / (r[r < 0].std() + 1e-9) * np.sqrt(252)
        ) if any(r < 0) else 0.0, 4),
        "win_rate":     round(float((r > 0).mean()), 4),
    }
