"""
Monte Carlo Simulation Engine for Trading Strategy Validation
=============================================================
Realistic Monte Carlo with slippage, commission, skip-rate, and drawdown analysis.
"""
import numpy as np
import pandas as pd
from typing import List, Dict, Any
import json
from datetime import datetime


def realistic_monte_carlo_simulation(
    trades: List[Dict],           # List of trade dicts: [{'return': 0.023, 'pnl': 2300, ...}]
    initial_capital: float = 100000,
    simulations: int = 2000,
    slippage: float = 0.0008,     # 0.08%
    commission: float = 0.0005,   # Brokerage + taxes
    skip_rate: float = 0.08,      # 8% trades miss (real execution issues)
    random_seed: int = 42
) -> Dict:
    """
    Realistic Monte Carlo for trading strategy validation
    """
    np.random.seed(random_seed)

    if not trades:
        return {"error": "No trades provided"}

    # Convert to numpy for speed
    returns = np.array([t.get('return', 0) for t in trades])
    pnls = np.array([t.get('pnl', 0) for t in trades])

    results = []
    equity_curves = []

    for sim in range(simulations):
        # 1. Shuffle trades (order randomization)
        idx = np.random.permutation(len(trades))
        sim_returns = returns[idx]
        sim_pnls = pnls[idx]

        # 2. Randomly skip some trades
        skip_mask = np.random.rand(len(trades)) < skip_rate
        sim_returns = sim_returns[~skip_mask]
        sim_pnls = sim_pnls[~skip_mask]

        # 3. Apply costs
        sim_returns = sim_returns * (1 - slippage - commission)

        # Build equity curve
        equity = np.cumprod(1 + sim_returns) * initial_capital
        equity = np.insert(equity, 0, initial_capital)  # starting point

        final_equity = equity[-1]
        total_return = (final_equity / initial_capital - 1) * 100

        # Max Drawdown
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        max_dd = drawdown.min() * 100

        results.append({
            "simulation": sim + 1,
            "final_return": round(total_return, 2),
            "max_drawdown": round(max_dd, 2),
            "final_equity": round(final_equity, 2),
            "trades_executed": len(sim_returns)
        })

        if sim < 50:  # Save few curves for visualization
            equity_curves.append([round(float(v), 2) for v in equity.tolist()])

    # Summary Statistics
    df = pd.DataFrame(results)

    summary = {
        "initial_capital": initial_capital,
        "simulations": simulations,
        "mean_return": round(df['final_return'].mean(), 2),
        "median_return": round(df['final_return'].median(), 2),
        "win_probability": round((df['final_return'] > 0).mean() * 100, 2),
        "worst_return": round(df['final_return'].min(), 2),
        "best_return": round(df['final_return'].max(), 2),
        "avg_max_drawdown": round(df['max_drawdown'].mean(), 2),
        "worst_drawdown": round(df['max_drawdown'].min(), 2),
        "risk_of_ruin": round((df['max_drawdown'] < -50).mean() * 100, 2),  # >50% DD
        "percentile_5": round(np.percentile(df['final_return'], 5), 2),
        "percentile_95": round(np.percentile(df['final_return'], 95), 2),
        "timestamp": datetime.now().isoformat()
    }

    return {
        "summary": summary,
        "all_simulations": results[:100],  # Top 100 for frontend
        "sample_equity_curves": equity_curves
    }


def calculate_max_drawdown(equity: np.ndarray) -> float:
    """Helper function to calculate max drawdown from equity curve."""
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    return drawdown.min() * 100


def build_return_histogram(all_simulations: List[Dict], bins: int = 20) -> List[Dict]:
    """Build histogram data for return distribution chart."""
    returns = [s["final_return"] for s in all_simulations]
    counts, edges = np.histogram(returns, bins=bins)
    return [
        {
            "range":     f"{float(edges[i]):.1f}% to {float(edges[i+1]):.1f}%",
            "midpoint":  round(float((edges[i] + edges[i+1]) / 2), 2),
            "count":     int(counts[i]),
            "positive":  bool(edges[i] >= 0),
        }
        for i in range(len(counts))
    ]
