"""
Portfolio Optimizer — Multi-Asset Intelligence Engine.

Features:
  1. Mean-Variance Optimization (Markowitz)
  2. Black-Litterman (views blend with market equilibrium)
  3. Dynamic Kelly Criterion per-asset
  4. CVaR / Expected Shortfall
  5. Efficient Frontier computation
  6. Sector Rotation signal
  7. Correlation hedging
  8. Tail-Risk Hedging: options overlay suggestions (protective puts / collars)
  9. Smart Order Routing score
"""

import logging
import time
from typing import Dict, List, Optional

import numpy as np
import scipy.optimize as sco
import yfinance as yf
import pandas as pd

from .risk_reward import compute_cvar, dynamic_kelly

logger = logging.getLogger(__name__)

RISK_FREE = 0.065       # India risk-free rate ~6.5% annual
TRADING_DAYS = 252

# ─── Data fetching ────────────────────────────────────────────────────────────

_PRICE_CACHE: Dict[str, tuple] = {}   # ticker → (df, timestamp)
_CACHE_TTL = 300                       # 5-min cache


def _get_returns(tickers: List[str], period: str = "1y") -> pd.DataFrame:
    """Download closing prices and compute daily returns."""
    now = time.time()
    all_prices = {}
    to_fetch = []
    for t in tickers:
        if t in _PRICE_CACHE and (now - _PRICE_CACHE[t][1]) < _CACHE_TTL:
            all_prices[t] = _PRICE_CACHE[t][0]
        else:
            to_fetch.append(t)

    if to_fetch:
        try:
            raw = yf.download(to_fetch, period=period, interval="1d",
                              progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                closes = raw["Close"]
            else:
                closes = raw[["Close"]]
                closes.columns = to_fetch[:1]

            for t in to_fetch:
                if t in closes.columns:
                    s = closes[t].dropna()
                    all_prices[t] = s
                    _PRICE_CACHE[t] = (s, now)
        except Exception as exc:
            logger.warning("Price fetch error: %s", exc)

    if not all_prices:
        return pd.DataFrame()

    price_df = pd.DataFrame(all_prices).dropna()
    if len(price_df) < 30:
        return pd.DataFrame()

    returns = price_df.pct_change().dropna()
    return returns


# ─── 1. Mean-Variance Optimization ───────────────────────────────────────────

def mean_variance_optimize(
    returns: pd.DataFrame,
    target_return: Optional[float] = None,
    risk_aversion: float = 3.0,
) -> dict:
    """
    Max w'μ - (λ/2)*w'Σw  s.t. sum(w)=1, w≥0.

    If target_return is given → minimize variance subject to return constraint.
    """
    mu    = returns.mean().values * TRADING_DAYS
    Sigma = returns.cov().values  * TRADING_DAYS
    n     = len(mu)

    def neg_sharpe(w):
        ret  = w @ mu
        vol  = np.sqrt(w @ Sigma @ w)
        return -((ret - RISK_FREE) / (vol + 1e-9))

    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    if target_return is not None:
        constraints.append({
            "type": "eq",
            "fun": lambda w: w @ mu - target_return
        })

    bounds = [(0.0, 0.40)] * n    # max 40% per asset

    w0     = np.ones(n) / n       # equal-weight start
    result = sco.minimize(
        neg_sharpe, w0, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-9},
    )

    w_opt = result.x if result.success else w0
    w_opt = np.clip(w_opt, 0, 1)
    w_opt /= w_opt.sum()

    port_ret = float(w_opt @ mu)
    port_vol = float(np.sqrt(w_opt @ Sigma @ w_opt))

    return {
        "weights":       {t: round(float(w), 4) for t, w in zip(returns.columns, w_opt)},
        "expected_return": round(port_ret, 4),
        "volatility":    round(port_vol, 4),
        "sharpe":        round((port_ret - RISK_FREE) / (port_vol + 1e-9), 4),
        "success":       bool(result.success),
    }


# ─── 2. Black-Litterman ───────────────────────────────────────────────────────

def black_litterman(
    returns:     pd.DataFrame,
    views:       Optional[Dict[str, float]] = None,  # ticker → expected annual return view
    view_confidence: float = 0.5,
) -> dict:
    """
    Black-Litterman posterior mean vector.

    views: {ticker: expected_return}  e.g. {"RELIANCE.NS": 0.18}
    view_confidence: 0-1 (sigma_view = (1-conf)/conf * sigma_prior)
    """
    mu_eq  = returns.mean().values * TRADING_DAYS
    Sigma  = returns.cov().values  * TRADING_DAYS
    n      = len(mu_eq)
    tau    = 0.05

    if views:
        tickers = list(returns.columns)
        P_rows, q_vals = [], []
        for tk, ret_view in views.items():
            if tk in tickers:
                row = np.zeros(n)
                row[tickers.index(tk)] = 1.0
                P_rows.append(row)
                q_vals.append(ret_view)

        if P_rows:
            P  = np.array(P_rows)
            q  = np.array(q_vals)
            k  = len(q)
            sigma_v = ((1 - view_confidence) / (view_confidence + 1e-9)) * 0.02
            Omega  = np.diag(np.full(k, sigma_v))

            # BL formula: μ_post = [(τΣ)⁻¹ + P'Ω⁻¹P]⁻¹ × [(τΣ)⁻¹π + P'Ω⁻¹q]
            tauSigma_inv = np.linalg.inv(tau * Sigma + 1e-8 * np.eye(n))
            Omega_inv    = np.linalg.inv(Omega)
            left_inv     = np.linalg.inv(tauSigma_inv + P.T @ Omega_inv @ P)
            right        = tauSigma_inv @ mu_eq + P.T @ Omega_inv @ q
            mu_post      = left_inv @ right
        else:
            mu_post = mu_eq
    else:
        mu_post = mu_eq

    # Optimize with BL posterior means
    def neg_sharpe_bl(w):
        ret = w @ mu_post
        vol = np.sqrt(w @ Sigma @ w)
        return -((ret - RISK_FREE) / (vol + 1e-9))

    result = sco.minimize(
        neg_sharpe_bl,
        np.ones(n) / n,
        method="SLSQP",
        bounds=[(0.0, 0.40)] * n,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
        options={"maxiter": 1000},
    )
    w_opt = result.x if result.success else np.ones(n) / n
    w_opt = np.clip(w_opt, 0, 1)
    w_opt /= w_opt.sum()

    return {
        "weights":    {t: round(float(w), 4) for t, w in zip(returns.columns, w_opt)},
        "mu_prior":   {t: round(float(m), 4) for t, m in zip(returns.columns, mu_eq)},
        "mu_posterior": {t: round(float(m), 4) for t, m in zip(returns.columns, mu_post)},
        "sharpe":     round(
            (w_opt @ mu_post - RISK_FREE) / (np.sqrt(w_opt @ Sigma @ w_opt) + 1e-9), 4
        ),
    }


# ─── 3. Efficient Frontier ────────────────────────────────────────────────────

def efficient_frontier(returns: pd.DataFrame, n_points: int = 30) -> List[dict]:
    """Compute n_points on the efficient frontier."""
    mu    = returns.mean().values * TRADING_DAYS
    Sigma = returns.cov().values  * TRADING_DAYS
    n     = len(mu)

    min_ret = float(mu.min()) * 1.1
    max_ret = float(mu.max()) * 0.9
    targets = np.linspace(min_ret, max_ret, n_points)

    frontier = []
    for target in targets:
        try:
            def port_vol(w):
                return np.sqrt(w @ Sigma @ w)

            result = sco.minimize(
                port_vol,
                np.ones(n) / n,
                method="SLSQP",
                bounds=[(0.0, 1.0)] * n,
                constraints=[
                    {"type": "eq", "fun": lambda w: w.sum() - 1},
                    {"type": "ineq", "fun": lambda w: w @ mu - target},
                ],
                options={"maxiter": 500},
            )
            if result.success:
                w  = result.x
                vl = float(np.sqrt(w @ Sigma @ w))
                rt = float(w @ mu)
                frontier.append({
                    "volatility": round(vl, 4),
                    "return":     round(rt, 4),
                    "sharpe":     round((rt - RISK_FREE) / (vl + 1e-9), 4),
                })
        except Exception:
            continue

    return frontier


# ─── 4. Kelly Criterion per-asset ─────────────────────────────────────────────

def kelly_per_asset(returns: pd.DataFrame) -> Dict[str, dict]:
    """Compute fractional Kelly sizing for each asset."""
    result = {}
    for col in returns.columns:
        r = returns[col].dropna().values
        if len(r) < 20:
            result[col] = {"kelly": 0.05, "win_rate": 0.5, "avg_win": 0.0, "avg_loss": 0.0}
            continue
        wins  = r[r > 0]
        losses = r[r < 0]
        win_rate = len(wins) / len(r)
        avg_win  = float(wins.mean())  if len(wins)  > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
        k = dynamic_kelly(win_rate, avg_win, avg_loss, fraction=0.25)
        result[col] = {
            "kelly":    round(k, 4),
            "win_rate": round(win_rate, 4),
            "avg_win":  round(avg_win, 6),
            "avg_loss": round(avg_loss, 6),
        }
    return result


# ─── 5. Correlation Hedging ───────────────────────────────────────────────────

def correlation_hedge(returns: pd.DataFrame, target_ticker: str) -> dict:
    """Find best hedge asset (lowest correlation to target)."""
    if target_ticker not in returns.columns or len(returns.columns) < 2:
        return {}

    corr = returns.corr()[target_ticker].drop(target_ticker)
    best_hedge  = corr.idxmin()
    hedge_corr  = float(corr.min())

    # Minimum-variance hedge ratio (beta)
    tgt = returns[target_ticker].values
    hdg = returns[best_hedge].values
    cov   = np.cov(tgt, hdg)
    hedge_ratio = float(cov[0, 1] / (cov[1, 1] + 1e-9))

    return {
        "target":       target_ticker,
        "best_hedge":   best_hedge,
        "correlation":  round(hedge_corr, 4),
        "hedge_ratio":  round(hedge_ratio, 4),
        "all_corr": {
            t: round(float(v), 4)
            for t, v in corr.sort_values().items()
        },
    }


# ─── 6. Options Overlay Suggestions ──────────────────────────────────────────

def options_overlay_suggest(
    ticker:       str,
    current_price: float,
    position_size: float,  # portfolio weight
    volatility:   float,   # annualized vol of the asset
    view:         str      = "neutral",  # bullish / neutral / bearish
) -> dict:
    """
    Suggest protective put / collar / covered call strategy.
    Theoretical pricing (simplified BSM approximation).
    """
    # Approx premium as vol × time_sqrt factor (simplified)
    t_30d = 30 / 365
    t_sq  = np.sqrt(t_30d)
    atm_put_premium_pct  = volatility * t_sq * 0.4   # approx 40% of vol for ATM put
    atm_call_premium_pct = volatility * t_sq * 0.38

    put_strike  = round(current_price * 0.95, 2)    # 5% OTM put
    call_strike = round(current_price * 1.05, 2)    # 5% OTM call

    put_cost_pct  = volatility * t_sq * 0.32
    call_recv_pct = volatility * t_sq * 0.30

    strategies = []

    if view in ("neutral", "bullish"):
        # Protective Put
        strategies.append({
            "name":         "Protective Put",
            "action":       "BUY PUT",
            "strike":       put_strike,
            "expiry":       "30D",
            "cost_pct":     round(put_cost_pct * 100, 2),
            "max_loss_pct": round(5 + put_cost_pct * 100, 2),
            "protection_below": round(put_strike, 2),
            "rationale":    "Downside protection while holding long position",
        })

    if view in ("neutral", "bearish"):
        # Covered Call
        strategies.append({
            "name":         "Covered Call",
            "action":       "SELL CALL",
            "strike":       call_strike,
            "expiry":       "30D",
            "income_pct":   round(call_recv_pct * 100, 2),
            "cap_above":    round(call_strike, 2),
            "rationale":    "Generate income; capped upside at call strike",
        })

    if view == "neutral":
        # Collar
        net_cost_pct = put_cost_pct - call_recv_pct
        strategies.append({
            "name":          "Collar",
            "action":        "BUY PUT + SELL CALL",
            "put_strike":    put_strike,
            "call_strike":   call_strike,
            "expiry":        "30D",
            "net_cost_pct":  round(net_cost_pct * 100, 2),
            "range":         f"{put_strike} – {call_strike}",
            "rationale":     "Low-cost downside protection with capped upside",
        })

    return {
        "ticker":         ticker,
        "current_price":  current_price,
        "implied_vol_pct": round(volatility * 100, 1),
        "position_weight": round(position_size * 100, 1),
        "view":           view,
        "strategies":     strategies,
    }


# ─── 7. Smart Order Routing ───────────────────────────────────────────────────

def smart_order_route(
    ticker:       str,
    direction:    str,    # BUY / SELL
    quantity:     float,
    avg_volume:   float,
    volatility:   float,
    urgency:      float = 0.5,  # 0=passive, 1=aggressive
) -> dict:
    """
    TWAP/VWAP-based order fragmentation.
    Returns recommended execution schedule.
    """
    # Participation rate: 5-15% of avg volume
    part_rate = 0.05 + urgency * 0.10
    order_qty_per_slice = max(1, avg_volume * part_rate)
    n_slices = max(1, int(np.ceil(quantity / order_qty_per_slice)))

    # TWAP: equal time slices
    interval_mins = max(1, int(60 / n_slices))

    # Market impact estimate (simplified)
    impact_bps = 10 * np.sqrt(quantity / (avg_volume + 1)) * volatility * 100

    strategy = "AGGRESSIVE" if urgency > 0.7 else ("PASSIVE" if urgency < 0.3 else "TWAP")

    slices = [
        {
            "slice":    i + 1,
            "qty":      round(min(order_qty_per_slice, quantity - i * order_qty_per_slice)),
            "delay_min": i * interval_mins,
            "algo":      strategy,
        }
        for i in range(min(n_slices, 10))   # max 10 slices
    ]

    return {
        "ticker":          ticker,
        "direction":       direction,
        "total_qty":       quantity,
        "n_slices":        n_slices,
        "interval_mins":   interval_mins,
        "estimated_impact_bps": round(impact_bps, 2),
        "strategy":        strategy,
        "slices":          slices,
    }


# ─── Public API ───────────────────────────────────────────────────────────────

def optimize_portfolio(
    tickers: List[str],
    method:  str = "mv",   # mv | bl
    views:   Optional[Dict[str, float]] = None,
    period:  str = "1y",
) -> dict:
    """Master portfolio optimization entry point."""
    returns = _get_returns(tickers, period)
    if returns.empty or len(returns.columns) < 2:
        return {"error": "Insufficient price data"}

    if method == "bl":
        result = black_litterman(returns, views)
    else:
        result = mean_variance_optimize(returns)

    # Add per-asset Kelly
    result["kelly"]     = kelly_per_asset(returns)
    # Add correlation matrix
    corr = returns.corr().round(4)
    result["correlation"] = corr.to_dict()
    # CVaR per asset
    result["cvar"]      = {
        t: compute_cvar(returns[t].dropna().values)
        for t in returns.columns
    }
    # Efficient frontier sample
    result["frontier"]  = efficient_frontier(returns, n_points=20)

    return result
