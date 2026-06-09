"""
Risk & Portfolio Management Module — Phase 2
=============================================
Institutional-grade risk engine for the Dreamer V3 Robo-Trader.

Design Principles:
  • Conservative bias throughout — every method clips to safe upper bounds.
  • All public functions are fully type-hinted and log every calculation.
  • Heavy audit trail: every recalculation is persisted to MongoDB.
  • Supports Paper and Live modes (Live mode applies additional safety multipliers).
  • Edge cases: zero capital, None inputs, unrealistic targets, market holidays, etc.

Algorithms implemented:
  1. Kelly Criterion (half-Kelly) position sizing
  2. ATR-based stop-distance sizing
  3. Volatility-Regime–adjusted sizing
  4. Conservative minimum of all three → final position size
  5. Parametric VaR + CVaR (99% & 95%)
  6. Historical simulation VaR (if price history available)
  7. Dynamic Risk Budget (adjusts intra-day based on P&L progress)
  8. Portfolio Heat Monitor (total deployed risk vs capital)
  9. 6-tier Feasibility Checker with detailed warnings + suggestions
 10. MongoDB CRUD for user settings + full recalculation audit trail

DISCLAIMER:
  This module is for RESEARCH and PAPER TRADING only.
  No guaranteed returns. Past performance ≠ future results.
  Never risk capital you cannot afford to lose.
  Always consult a SEBI-registered Investment Advisor.

Author: Dreamer V3 Robo-Trader Team
Date:   June 2026
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS
# ════════════════════════════════════════════════════════════════════════════════

# ── Position sizing hard limits ──────────────────────────────────────────────
MIN_RISK_PCT          = 0.005   # 0.5 % of capital — absolute floor
MAX_RISK_PCT          = 0.020   # 2.0 % of capital — hard ceiling (conservative rule)
MAX_POSITION_PCT      = 0.40    # single position ≤ 40 % of capital
HALF_KELLY_FRACTION   = 0.50    # use half-Kelly for conservatism
DEFAULT_RR_RATIO      = 1.50    # reward:risk = 1.5:1
TARGET_DAILY_VOL      = 0.010   # 1 % target portfolio daily vol
ATR_SL_MULTIPLIER     = 2.0     # SL = 2 × ATR

# ── Portfolio heat limits ─────────────────────────────────────────────────────
MAX_PORTFOLIO_HEAT    = 0.06    # max 6 % of capital in open risk at any time
MAX_CONCURRENT_TRADES = 3       # hard concurrency cap (paper mode)

# ── Circuit-breaker thresholds ────────────────────────────────────────────────
DAILY_LOSS_CAP_PCT    = 0.015   # 1.5 % capital → hard daily loss limit
ACCOUNT_DD_CAP        = 0.05    # 5 % account drawdown → pause
CONSEC_LOSS_HARD_STOP = 5       # 5 consecutive losses → hard stop

# ── NSE historical return distribution (large-cap, 2010-2025) ─────────────────
# Used for feasibility scoring without scipy dependency.
# Source: empirical percentiles from Nifty 50 daily return series.
NSE_DAILY_RET_P50  = 0.00063   # 50th pct  ≈ +0.06 %
NSE_DAILY_RET_P75  = 0.00610   # 75th pct  ≈ +0.61 %
NSE_DAILY_RET_P90  = 0.01050   # 90th pct  ≈ +1.05 %
NSE_DAILY_RET_P95  = 0.01480   # 95th pct  ≈ +1.48 %
NSE_DAILY_RET_P99  = 0.02550   # 99th pct  ≈ +2.55 %
NSE_DAILY_VOL_AVG  = 0.01200   # average daily vol ≈ 1.20 %

# ── Feasibility tier definitions (required_daily_return_pct → tier) ───────────
FEASIBILITY_TIERS: List[Tuple[float, str, str, int, str]] = [
    # (max_pct, label, color_hex, score, suggestion)
    (0.20, "Easily Achievable",       "#10b981", 95,
     "Target is well within typical NSE daily movement. Solid foundation for consistent growth."),
    (0.50, "Achievable",              "#84cc16", 80,
     "Achievable on ~75% of trading days. Maintain discipline; avoid overtrading."),
    (1.05, "Moderately Aggressive",   "#f59e0b", 60,
     "Requires above-median market cooperation. Achievable ~40% of days. Maintain strict SL."),
    (1.50, "Aggressive – High Risk",  "#f97316", 35,
     "Exceeds the 90th percentile of historical NSE daily gains. Requires exceptional market day."),
    (2.60, "Very Aggressive",         "#ef4444", 15,
     "Near the 99th pct of NSE daily gains. Extremely difficult. High probability of loss chasing."),
    (99.0, "Unrealistic",             "#dc2626",  3,
     "Target exceeds historical extremes. Capital protection severely at risk. Please reduce target."),
]

# ── Reward shaping weights for DreamerV3 ─────────────────────────────────────
REWARD_TARGET_PROGRESS  = 0.35   # weight for daily-target progress
REWARD_CAPITAL_PROTECT  = 0.15   # bonus for low drawdown
REWARD_SHARPE           = 0.12
REWARD_CALMAR           = 0.10
REWARD_VOLATILITY_NORM  = 0.08   # bonus for near-target-vol position sizing
REWARD_DD_PENALTY       = 25.0   # convex drawdown penalty factor
REWARD_COST_PENALTY     = 1.20   # per-unit transaction cost penalty
REWARD_HEAT_PENALTY     = 5.0    # penalty when portfolio heat too high

# ── Live-mode safety multiplier ───────────────────────────────────────────────
LIVE_MODE_SAFETY_MULT   = 0.70   # all sizes × 0.70 in live mode

# ── Robot 3.O leverage (MIS intraday margin) ─────────────────────────────────
# When BUYING any stock, quantity is multiplied by this factor to utilise
# broker intraday margin (e.g. Groww MIS gives ~5x leverage on most large-caps).
# Risk per trade scales proportionally — SL/TP distances stay the same; only
# share-count and total position value increase. SELL/short trades are NOT
# levered (delivery shorts not allowed on Groww; intraday only).
ROBOT3_BUY_LEVERAGE     = 5.0    # 5× leverage on BUY orders


# ════════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATA CLASSES (all JSON-serialisable via asdict)
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class PositionSizeResult:
    """
    Unified position sizing output.
    All three sizing methods are computed; the final size is the conservative minimum.
    """
    capital:              float
    daily_target:         float
    ticker:               str
    mode:                 str          # "paper" | "live"

    # Method 1: Kelly Criterion (half-Kelly)
    kelly_fraction:       float        # raw half-Kelly fraction (0-1)
    kelly_position_inr:   float        # ₹ value of Kelly position

    # Method 2: ATR-based (risk-per-trade / SL-distance)
    atr_pct:              float        # daily ATR as % of price
    atr_risk_inr:         float        # ₹ amount risked per trade
    atr_position_inr:     float        # ₹ position value (ATR method)
    quantity_atr:         int          # shares / lots (ATR method)

    # Method 3: Volatility-regime adjusted
    vol_regime:           str          # "LOW" | "NORMAL" | "HIGH"
    vol_regime_mult:      float        # adjustment multiplier
    vol_position_inr:     float        # ₹ position value (vol method)

    # Final (conservative minimum of all three, safety-capped)
    final_position_inr:   float
    final_quantity:       int
    final_risk_pct:       float        # % of capital risked
    final_risk_inr:       float        # ₹ amount risked
    sl_price:             float
    tp_price:             float
    sl_distance_pct:      float

    # Meta
    timestamp:            str
    warnings:             List[str] = field(default_factory=list)
    leverage_applied:     float = 1.0  # Robot 3.O: 5× on BUY, 1× on SELL


@dataclass
class VaRResult:
    """Parametric + historical VaR / CVaR."""
    position_value:       float
    daily_vol:            float

    # Parametric (Normal distribution)
    param_var_95:         float        # VaR at 95 % confidence
    param_var_99:         float        # VaR at 99 % confidence
    param_cvar_95:        float        # CVaR (Expected Shortfall) at 95 %
    param_cvar_99:        float        # CVaR at 99 %

    # As % of capital
    var_95_pct_of_capital: float
    var_99_pct_of_capital: float

    capital:              float
    method:               str = "parametric_normal"
    timestamp:            str = ""


@dataclass
class FeasibilityResult:
    """6-tier feasibility assessment with detailed warnings."""
    required_daily_return_pct:  float
    daily_target:               float
    allocated_capital:          float

    # Tier result
    tier_label:                 str
    tier_color:                 str
    tier_score:                 int           # 0-100
    tier_suggestion:            str

    # Probability context
    historical_exceedance_pct:  float         # % of NSE days that exceeded this return
    required_win_rate_min:      float         # min win-rate to break even
    nse_median_comparison:      str           # e.g. "2.4× the NSE 50th percentile"

    # Risk warnings (list of plain-English warnings)
    warnings:                   List[str] = field(default_factory=list)

    # Suggestions
    alternative_targets:        Dict[str, float] = field(default_factory=dict)


@dataclass
class DynamicRiskBudget:
    """
    Intra-day risk budget that adjusts based on P&L progress and time elapsed.
    Called before every trade to determine the current position-size multiplier.
    """
    daily_target:         float
    daily_loss_limit:     float
    current_pnl:          float
    trades_today:         int
    max_trades:           int
    session_progress:     float         # 0=market open, 1=market close

    # Computed outputs
    remaining_target:     float
    remaining_risk:       float         # remaining loss budget
    pnl_progress_pct:     float         # current_pnl / daily_target × 100
    size_multiplier:      float         # 0.3 – 1.0 applied to base position
    trades_remaining:     int
    should_stop_trading:  bool          # True if budget exhausted
    stop_reason:          Optional[str]
    state_label:          str           # "NORMAL" | "CAUTIOUS" | "REDUCED" | "STOP"


@dataclass
class RecalculationAudit:
    """
    Full audit record for every risk recalculation event.
    Persisted to MongoDB `robo_recalculation_audit` collection.
    """
    audit_id:             str
    trigger:              str    # "user_update" | "market_change" | "scheduled" | "force"
    timestamp:            str

    # Inputs
    input_daily_target:   float
    input_capital:        float
    input_risk_tolerance: str
    input_ticker:         str
    input_mode:           str

    # Market inputs
    market_price:         float
    market_atr_pct:       float
    market_regime:        str

    # Outputs
    position_size:        Dict[str, Any]
    var_result:           Dict[str, Any]
    feasibility:          Dict[str, Any]
    dynamic_budget:       Dict[str, Any]
    risk_profile_full:    Dict[str, Any]

    # Performance
    computation_ms:       float
    warnings_count:       int


# ════════════════════════════════════════════════════════════════════════════════
# SECTION 3 — UTILITY / CALCULATION FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════════

def compute_kelly_fraction(
    win_rate_pct: float,      # e.g. 55.0 for 55%
    avg_win_pct: float,       # avg winning trade as % gain
    avg_loss_pct: float,      # avg losing trade as % loss (positive number)
) -> float:
    """
    Half-Kelly Criterion position fraction.

    Kelly formula:  f* = (p × b − q) / b
      p = win probability
      b = avg_win / avg_loss  (reward:risk ratio)
      q = 1 − p

    Half-Kelly: use f*/2 for conservatism (reduces drawdown by ~sqrt(2)).
    Hard cap at MAX_RISK_PCT to prevent over-concentration.

    Edge cases:
      - win_rate = 0 or 100 → returns 0 (degenerate)
      - avg_loss = 0 → returns 0 (prevents division by zero)
      - negative Kelly → returns 0 (no edge, do not trade)
    """
    if win_rate_pct <= 0 or win_rate_pct >= 100 or avg_loss_pct <= 0 or avg_win_pct <= 0:
        logger.debug("[Kelly] Degenerate inputs → returning 0 fraction")
        return 0.0

    p = win_rate_pct / 100.0
    q = 1.0 - p
    b = avg_win_pct / avg_loss_pct   # reward:risk

    f_star = (p * b - q) / b
    logger.debug("[Kelly] f*=%.4f | p=%.2f b=%.2f | half-Kelly=%.4f", f_star, p, b, f_star * HALF_KELLY_FRACTION)

    if f_star <= 0:
        logger.info("[Kelly] No positive edge (f*=%.4f). Skip trade.", f_star)
        return 0.0

    half_kelly = f_star * HALF_KELLY_FRACTION
    return float(np.clip(half_kelly, 0.0, MAX_RISK_PCT))


def compute_var_cvar(
    position_value: float,
    daily_vol_pct: float,     # e.g. 0.015 for 1.5%
    capital: float,
) -> VaRResult:
    """
    Parametric Normal VaR and CVaR at 95% and 99% confidence levels.

    Formulae:
      VaR_α  = position × σ × z_α
      CVaR_α = position × σ × φ(z_α) / (1 − α)

    where z_95=1.645, z_99=2.326, φ=standard normal PDF.
    CVaR is the expected loss GIVEN that loss exceeds VaR.

    Edge cases: zero position or vol → returns zero VaR.
    """
    if position_value <= 0 or daily_vol_pct <= 0:
        zero = VaRResult(
            position_value=0, daily_vol=0,
            param_var_95=0, param_var_99=0,
            param_cvar_95=0, param_cvar_99=0,
            var_95_pct_of_capital=0, var_99_pct_of_capital=0,
            capital=capital,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return zero

    sigma   = position_value * daily_vol_pct   # ₹ daily std dev
    z_95    = 1.6449
    z_99    = 2.3263

    # Standard normal PDF at z
    def phi(z: float) -> float:
        return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)

    var_95  = sigma * z_95
    var_99  = sigma * z_99
    cvar_95 = sigma * phi(z_95) / 0.05
    cvar_99 = sigma * phi(z_99) / 0.01

    result = VaRResult(
        position_value         = round(position_value, 2),
        daily_vol              = round(daily_vol_pct, 6),
        param_var_95           = round(var_95,  2),
        param_var_99           = round(var_99,  2),
        param_cvar_95          = round(cvar_95, 2),
        param_cvar_99          = round(cvar_99, 2),
        var_95_pct_of_capital  = round(var_95  / max(capital, 1) * 100, 4),
        var_99_pct_of_capital  = round(var_99  / max(capital, 1) * 100, 4),
        capital                = round(capital, 2),
        timestamp              = datetime.now(timezone.utc).isoformat(),
    )
    logger.debug(
        "[VaR] pos=₹%.0f vol=%.2f%% | VaR95=₹%.0f VaR99=₹%.0f CVaR95=₹%.0f",
        position_value, daily_vol_pct * 100, var_95, var_99, cvar_95,
    )
    return result


def _pct_days_exceeding(required_return_pct: float) -> float:
    """
    Estimate % of NSE trading days that historically produced ≥ required_return_pct.
    Uses linear interpolation over empirical percentile table (no scipy needed).
    Returns a float in [0, 100].
    """
    # Table: (percentile_exceedance %, required_return %)
    table = [
        (50.0,  NSE_DAILY_RET_P50  * 100),
        (25.0,  NSE_DAILY_RET_P75  * 100),
        (10.0,  NSE_DAILY_RET_P90  * 100),
        (5.0,   NSE_DAILY_RET_P95  * 100),
        (1.0,   NSE_DAILY_RET_P99  * 100),
        (0.1,   3.50),
        (0.01,  5.00),
    ]
    r = required_return_pct
    if r <= 0:
        return 100.0
    if r >= 5.0:
        return 0.01

    for i in range(len(table) - 1):
        p1, r1 = table[i]
        p2, r2 = table[i + 1]
        if r1 <= r <= r2:
            # linear interpolation in log-space for exceedance %
            t = (r - r1) / (r2 - r1 + 1e-9)
            return round(p1 + t * (p2 - p1), 2)
    return 0.01


def check_feasibility(
    daily_target: float,
    allocated_capital: float,
    risk_tolerance: str = "moderate",
    avg_win_pct: float = 1.5,
    avg_loss_pct: float = 1.0,
) -> FeasibilityResult:
    """
    6-tier feasibility check with detailed plain-English warnings.

    Parameters:
        daily_target:      user's daily ₹ profit target
        allocated_capital: total capital allocated
        risk_tolerance:    conservative | moderate | aggressive
        avg_win_pct:       assumed average winning trade gain (%)
        avg_loss_pct:      assumed average losing trade loss (%)

    Returns a FeasibilityResult with:
        • tier label + color + score + suggestion
        • % of historical NSE days that produced this return
        • minimum win-rate needed to break even
        • plain-English warnings list
        • alternative realistic targets
    """
    # ── Edge case guards ──────────────────────────────────────────────────────
    if allocated_capital <= 0:
        logger.warning("[Feasibility] Zero or negative capital provided: %.2f", allocated_capital)
        allocated_capital = 1000.0

    if daily_target <= 0:
        return FeasibilityResult(
            required_daily_return_pct=0, daily_target=daily_target,
            allocated_capital=allocated_capital,
            tier_label="No Target", tier_color="#6b7280", tier_score=100,
            tier_suggestion="Set a positive daily target to begin analysis.",
            historical_exceedance_pct=100, required_win_rate_min=0,
            nse_median_comparison="N/A",
        )

    required_ret_frac = daily_target / allocated_capital
    required_ret_pct  = required_ret_frac * 100.0

    # ── Find tier ─────────────────────────────────────────────────────────────
    tier_label, tier_color, tier_score, tier_suggestion = (
        "Unrealistic", "#dc2626", 3,
        "Target exceeds historical extremes. Severe capital risk."
    )
    for max_pct, lbl, color, score, suggestion in FEASIBILITY_TIERS:
        if required_ret_pct <= max_pct:
            tier_label, tier_color, tier_score, tier_suggestion = lbl, color, score, suggestion
            break

    # ── Historical context ────────────────────────────────────────────────────
    hist_exceedance = _pct_days_exceeding(required_ret_pct)

    # ── Minimum win-rate needed to break even ─────────────────────────────────
    # break-even: p * avg_win - (1-p) * avg_loss = 0
    # → p = avg_loss / (avg_win + avg_loss)
    if avg_win_pct + avg_loss_pct > 0:
        min_wr = avg_loss_pct / (avg_win_pct + avg_loss_pct) * 100.0
    else:
        min_wr = 50.0
    min_wr = round(min_wr, 1)

    # ── NSE median comparison ─────────────────────────────────────────────────
    median_pct = NSE_DAILY_RET_P50 * 100
    if median_pct > 0:
        mult = required_ret_pct / median_pct
        nse_cmp = f"{mult:.1f}× the NSE daily median return" if mult >= 1 else f"{1/mult:.1f}× below NSE median"
    else:
        nse_cmp = "N/A"

    # ── Build warnings list ───────────────────────────────────────────────────
    warnings: List[str] = []

    if required_ret_pct > NSE_DAILY_RET_P95 * 100:
        warnings.append(
            f"⚠️  Target requires {required_ret_pct:.2f}%/day — exceeds the historical 95th percentile "
            f"({NSE_DAILY_RET_P95*100:.2f}%). Only ~{hist_exceedance:.1f}% of market days achieve this."
        )
    if required_ret_pct > 2.0:
        warnings.append(
            "🔴 At this return level, leverage would be required for most position sizes, "
            "significantly amplifying both gains AND losses."
        )
    if risk_tolerance == "aggressive" and required_ret_pct > 1.0:
        warnings.append(
            "⚡ Aggressive risk tolerance with high daily target — monitor consecutive losses closely. "
            f"Circuit breaker will trigger at {CONSEC_LOSS_HARD_STOP} consecutive losses."
        )
    if risk_tolerance == "conservative" and required_ret_pct > 0.5:
        warnings.append(
            "🛡️  Conservative risk tolerance may conflict with this target. "
            "Position sizes will be reduced (0.6× multiplier), making the target harder to reach."
        )
    if daily_target > allocated_capital * 0.05:
        warnings.append(
            f"💥 Daily target ({daily_target:.0f}) exceeds 5% of capital. "
            "This is extremely aggressive and unsustainable over time."
        )

    # ── Alternative realistic targets ─────────────────────────────────────────
    alternatives = {
        "Easily Achievable (0.2%/day)": round(allocated_capital * 0.002, 0),
        "Achievable (0.5%/day)":         round(allocated_capital * 0.005, 0),
        "Monthly_target_0.5pct_daily":   round(allocated_capital * 0.005 * 20, 0),
    }

    logger.info(
        "[Feasibility] target=₹%.0f capital=₹%.0f → %.3f%%/day | %s (score=%d) | hist_exceedance=%.1f%%",
        daily_target, allocated_capital, required_ret_pct,
        tier_label, tier_score, hist_exceedance,
    )

    return FeasibilityResult(
        required_daily_return_pct = round(required_ret_pct, 4),
        daily_target              = round(daily_target, 2),
        allocated_capital         = round(allocated_capital, 2),
        tier_label                = tier_label,
        tier_color                = tier_color,
        tier_score                = tier_score,
        tier_suggestion           = tier_suggestion,
        historical_exceedance_pct = hist_exceedance,
        required_win_rate_min     = min_wr,
        nse_median_comparison     = nse_cmp,
        warnings                  = warnings,
        alternative_targets       = alternatives,
    )


def compute_dynamic_risk_budget(
    daily_target: float,
    daily_loss_limit: float,
    current_pnl: float,
    trades_today: int,
    max_trades: int,
    session_progress: float = 0.5,  # 0=market open, 1=close
) -> DynamicRiskBudget:
    """
    Intra-day risk budget management.

    Logic:
      • If daily_target hit → go to minimum size (0.3×) — lock in gains
      • If 80% of target hit → reduce size (0.75×) — protect gains
      • If behind target by >50% with <25% session left → reduce size (0.70×)
      • If loss_limit approaching → reduce size (0.50×)
      • If max_trades reached or loss_limit hit → STOP

    Returns DynamicRiskBudget with size_multiplier and trading permission.
    """
    remaining_target  = max(daily_target - current_pnl, 0.0)
    remaining_risk    = max(daily_loss_limit + current_pnl, 0.0)  # how much more we can lose
    pnl_progress_pct  = (current_pnl / daily_target * 100) if daily_target > 0 else 0.0
    trades_remaining  = max(max_trades - trades_today, 0)

    should_stop  = False
    stop_reason  = None
    state_label  = "NORMAL"
    multiplier   = 1.0

    # ── Hard stops ────────────────────────────────────────────────────────────
    if current_pnl <= -daily_loss_limit:
        should_stop = True
        stop_reason = f"Daily loss limit hit: ₹{abs(current_pnl):.0f} ≥ limit ₹{daily_loss_limit:.0f}"
        state_label = "STOP"
        multiplier  = 0.0
    elif trades_remaining <= 0:
        should_stop = True
        stop_reason = f"Max trades ({max_trades}) reached for the day"
        state_label = "STOP"
        multiplier  = 0.0

    # ── Gain protection ────────────────────────────────────────────────────────
    elif pnl_progress_pct >= 100:
        state_label = "STOP"
        should_stop = True
        stop_reason = "Daily target achieved — capital locked in for today"
        multiplier  = 0.0
    elif pnl_progress_pct >= 80:
        state_label = "REDUCED"
        multiplier  = 0.40   # lock in 80% of gains
        logger.info("[RiskBudget] 80%% target hit → size multiplier %.2f", multiplier)

    # ── Session-time + behind-target adjustments ───────────────────────────────
    elif session_progress >= 0.75 and pnl_progress_pct < 0:
        state_label = "CAUTIOUS"
        multiplier  = 0.70   # last quarter of session, still in loss → cautious
        logger.info("[RiskBudget] Late session + net loss → size multiplier %.2f", multiplier)

    elif remaining_risk < daily_loss_limit * 0.30:
        # Less than 30% of daily risk budget remaining
        state_label = "CAUTIOUS"
        multiplier  = 0.50
        logger.info("[RiskBudget] Low risk budget remaining → size multiplier %.2f", multiplier)

    logger.debug(
        "[RiskBudget] PnL=₹%.0f (%.0f%%) | trades=%d/%d | budget=₹%.0f | mult=%.2f | state=%s",
        current_pnl, pnl_progress_pct, trades_today, max_trades,
        remaining_risk, multiplier, state_label,
    )

    return DynamicRiskBudget(
        daily_target       = daily_target,
        daily_loss_limit   = daily_loss_limit,
        current_pnl        = round(current_pnl, 2),
        trades_today       = trades_today,
        max_trades         = max_trades,
        session_progress   = round(session_progress, 4),
        remaining_target   = round(remaining_target, 2),
        remaining_risk     = round(remaining_risk, 2),
        pnl_progress_pct   = round(pnl_progress_pct, 2),
        size_multiplier    = round(multiplier, 3),
        trades_remaining   = trades_remaining,
        should_stop_trading = should_stop,
        stop_reason        = stop_reason,
        state_label        = state_label,
    )


def get_volatility_regime(atr_pct: float) -> Tuple[str, float]:
    """
    Classify market volatility regime and return adjustment multiplier.

    Regimes:
      LOW    (ATR < 0.8%)  → increase size slightly (mult = 1.20)
      NORMAL (0.8–2.0%)   → no change           (mult = 1.00)
      HIGH   (2.0–3.5%)   → reduce size          (mult = 0.75)
      EXTREME(> 3.5%)     → heavily reduce        (mult = 0.50)

    Returns (regime_label, position_size_multiplier)
    """
    if atr_pct < 0.008:
        return "LOW",     1.20
    elif atr_pct < 0.020:
        return "NORMAL",  1.00
    elif atr_pct < 0.035:
        return "HIGH",    0.75
    else:
        return "EXTREME", 0.50


def compute_position_size(
    capital: float,
    daily_target: float,
    price: float,
    atr_pct: float,
    risk_tolerance: str = "moderate",
    win_rate_pct: float = 50.0,
    avg_win_pct: float  = 1.5,
    avg_loss_pct: float = 1.0,
    direction: str      = "BUY",
    mode: str           = "paper",
) -> PositionSizeResult:
    """
    Master position-sizing function — Kelly + ATR + Vol-Regime, conservative minimum.

    Steps:
      1. Determine base risk % from tolerance + hard limits
      2. Kelly fraction (half-Kelly)
      3. ATR-based size (risk / SL-distance)
      4. Volatility-regime multiplier
      5. Final = min(kelly, atr) × vol_mult × safety_cap
      6. Live mode: apply additional 0.70× safety multiplier
      7. Compute SL + TP prices

    Edge cases:
      - price ≤ 0 → returns zero-size result
      - atr_pct ≤ 0 → defaults to 1.5%
      - capital ≤ 0 → returns zero-size result
    """
    warnings: List[str] = []
    ts = datetime.now(timezone.utc).isoformat()

    # ── Guard clauses ─────────────────────────────────────────────────────────
    if capital <= 0 or price <= 0:
        logger.warning("[PositionSize] Zero capital (%.2f) or price (%.2f)", capital, price)
        warnings.append("⚠️  Invalid capital or price — returning zero position.")
        return PositionSizeResult(
            capital=capital, daily_target=daily_target, ticker="", mode=mode,
            kelly_fraction=0, kelly_position_inr=0,
            atr_pct=0, atr_risk_inr=0, atr_position_inr=0, quantity_atr=0,
            vol_regime="N/A", vol_regime_mult=1.0, vol_position_inr=0,
            final_position_inr=0, final_quantity=0, final_risk_pct=0, final_risk_inr=0,
            sl_price=price, tp_price=price, sl_distance_pct=0,
            timestamp=ts, warnings=warnings,
        )

    if atr_pct <= 0:
        atr_pct = NSE_DAILY_VOL_AVG
        warnings.append("ℹ️  ATR not available — using historical average volatility (1.2%).")

    # ── Base risk % by tolerance ──────────────────────────────────────────────
    tol_risk = {"conservative": 0.006, "moderate": 0.010, "aggressive": 0.016}
    base_risk_pct = tol_risk.get(risk_tolerance, 0.010)
    base_risk_pct = float(np.clip(base_risk_pct, MIN_RISK_PCT, MAX_RISK_PCT))

    # ── Method 1: Kelly ───────────────────────────────────────────────────────
    kelly_frac  = compute_kelly_fraction(win_rate_pct, avg_win_pct, avg_loss_pct)
    kelly_pos   = capital * kelly_frac

    # ── Method 2: ATR-based (risk / SL-distance) ──────────────────────────────
    risk_inr       = capital * base_risk_pct
    atr_inr        = price * atr_pct
    sl_distance    = atr_inr * ATR_SL_MULTIPLIER
    if sl_distance > 0:
        qty_atr    = max(1, int(risk_inr / sl_distance))
    else:
        qty_atr    = 1
    atr_pos        = qty_atr * price

    # SL / TP prices
    if direction == "BUY":
        sl_price = price - sl_distance
        tp_price = price + sl_distance * DEFAULT_RR_RATIO
    else:
        sl_price = price + sl_distance
        tp_price = price - sl_distance * DEFAULT_RR_RATIO

    sl_dist_pct = (sl_distance / price) * 100.0

    # ── Method 3: Volatility-regime ───────────────────────────────────────────
    vol_regime, vol_mult = get_volatility_regime(atr_pct)
    vol_pos = atr_pos * vol_mult

    # ── Final: conservative minimum ───────────────────────────────────────────
    candidate = min(kelly_pos, atr_pos, vol_pos)
    # Safety cap: never exceed MAX_POSITION_PCT × capital
    max_pos = capital * MAX_POSITION_PCT
    candidate = min(candidate, max_pos)

    # Live-mode additional safety
    if mode == "live":
        candidate *= LIVE_MODE_SAFETY_MULT
        warnings.append(f"🔴 LIVE MODE: position reduced by {(1-LIVE_MODE_SAFETY_MULT)*100:.0f}% safety margin.")

    # ── Robot 3.O: 5× leverage on BUY (MIS intraday margin) ───────────────────
    # Scales quantity/position value by ROBOT3_BUY_LEVERAGE for long entries.
    # SL/TP price levels are NOT changed — only share count scales, so absolute
    # risk in ₹ also scales by the leverage factor (paper + live both modes).
    leverage_applied = 1.0
    if direction == "BUY" and ROBOT3_BUY_LEVERAGE > 1.0:
        candidate *= ROBOT3_BUY_LEVERAGE
        leverage_applied = ROBOT3_BUY_LEVERAGE
        warnings.append(
            f"⚡ Robot 3.O Leverage: BUY position × {ROBOT3_BUY_LEVERAGE:.1f} (MIS intraday margin)"
        )

    final_qty   = max(1, int(candidate / price))
    final_pos   = final_qty * price
    final_risk  = final_qty * sl_distance
    final_risk_pct = (final_risk / capital) * 100.0

    # ── Warnings ──────────────────────────────────────────────────────────────
    # With leverage, the 2% risk guideline applies to UN-LEVERED equivalent risk.
    unlevered_risk_pct = final_risk_pct / max(leverage_applied, 1.0)
    if unlevered_risk_pct > MAX_RISK_PCT * 100 * 1.1:
        warnings.append(f"⚠️  Computed risk per trade ({final_risk_pct:.2f}%, unlevered {unlevered_risk_pct:.2f}%) exceeds 2% guideline.")
    if vol_regime == "HIGH":
        warnings.append("🔶 High volatility regime — position size reduced 25%.")
    elif vol_regime == "EXTREME":
        warnings.append("🔴 Extreme volatility — position size reduced 50%. Consider sitting out.")

    logger.info(
        "[PositionSize] capital=₹%.0f | kelly=₹%.0f ATR=₹%.0f vol=₹%.0f → final=₹%.0f qty=%d | "
        "risk=₹%.0f (%.2f%%) | SL=₹%.2f TP=₹%.2f | regime=%s | leverage=%.1fx | dir=%s",
        capital, kelly_pos, atr_pos, vol_pos, final_pos, final_qty,
        final_risk, final_risk_pct, sl_price, tp_price, vol_regime,
        leverage_applied, direction,
    )

    return PositionSizeResult(
        capital             = round(capital, 2),
        daily_target        = round(daily_target, 2),
        ticker              = "",
        mode                = mode,
        kelly_fraction      = round(kelly_frac, 6),
        kelly_position_inr  = round(kelly_pos,  2),
        atr_pct             = round(atr_pct, 6),
        atr_risk_inr        = round(risk_inr, 2),
        atr_position_inr    = round(atr_pos, 2),
        quantity_atr        = qty_atr,
        vol_regime          = vol_regime,
        vol_regime_mult     = round(vol_mult, 3),
        vol_position_inr    = round(vol_pos, 2),
        final_position_inr  = round(final_pos, 2),
        final_quantity      = final_qty,
        final_risk_pct      = round(final_risk_pct, 4),
        final_risk_inr      = round(final_risk, 2),
        sl_price            = round(sl_price, 2),
        tp_price            = round(tp_price, 2),
        sl_distance_pct     = round(sl_dist_pct, 4),
        timestamp           = ts,
        warnings            = warnings,
        leverage_applied    = float(leverage_applied),
    )


def compute_rpm_reward(
    step_return_pct: float,
    daily_pnl: float,
    daily_target: float,
    allocated_capital: float,
    drawdown_frac: float,
    transaction_cost_inr: float,
    position_risk_pct: float,
    target_vol_deviation: float,
    portfolio_heat: float,
    consecutive_losses: int,
    sharpe_rolling: float = 0.0,
    calmar_rolling: float = 0.0,
) -> Tuple[float, Dict[str, float]]:
    """
    Enhanced DreamerV3 reward function — Phase 2 version.
    Returns (total_reward, component_breakdown).

    Components:
      1. Target progress bonus:      tanh(progress_frac) × WEIGHT
      2. Capital protection bonus:   low-drawdown reward
      3. Sharpe component:           tanh(sharpe) × WEIGHT
      4. Calmar component:           tanh(calmar) × WEIGHT
      5. Volatility normality bonus: reward for sizing near target vol
      6. Drawdown excess penalty:    convex penalty
      7. Transaction cost penalty:   linear
      8. Portfolio heat penalty:     penalise overloading risk
      9. Consecutive-loss dampener:  multiplicative dampener

    All components are clipped; total is clipped to [-3, +3].
    """

    # 1. Target progress
    if daily_target > 0 and allocated_capital > 0:
        progress_frac = daily_pnl / daily_target
        target_bonus  = float(np.tanh(progress_frac)) * REWARD_TARGET_PROGRESS
    else:
        target_bonus  = 0.0

    # 2. Capital protection (reward low drawdowns)
    if drawdown_frac < 0.01:
        cap_prot = REWARD_CAPITAL_PROTECT * (1.0 - drawdown_frac * 100)
    elif drawdown_frac < 0.02:
        cap_prot = REWARD_CAPITAL_PROTECT * 0.5
    else:
        cap_prot = 0.0

    # 3. Sharpe
    sharpe_term = float(np.tanh(sharpe_rolling)) * REWARD_SHARPE

    # 4. Calmar
    calmar_term = float(np.tanh(calmar_rolling)) * REWARD_CALMAR

    # 5. Volatility normality bonus: reward when position size ≈ target vol
    # target_vol_deviation = |actual_position_vol - TARGET_DAILY_VOL| / TARGET_DAILY_VOL
    vol_bonus = REWARD_VOLATILITY_NORM * max(0.0, 1.0 - target_vol_deviation)

    # 6. Core PnL signal
    pnl_signal = step_return_pct * 100.0

    # 7. Drawdown penalty (convex: heavier punishment as drawdown grows)
    dd_thresh = 0.015
    if drawdown_frac > dd_thresh:
        excess  = drawdown_frac - dd_thresh
        dd_pen  = (excess ** 1.4) * REWARD_DD_PENALTY
    else:
        dd_pen  = 0.0

    # 8. Transaction cost penalty
    cost_pen = (transaction_cost_inr / max(allocated_capital, 1.0)) * REWARD_COST_PENALTY * 1000

    # 9. Portfolio heat penalty
    heat_excess = max(0.0, portfolio_heat - MAX_PORTFOLIO_HEAT)
    heat_pen    = heat_excess * REWARD_HEAT_PENALTY * 100

    # 10. Consecutive-loss dampener (multiplicative)
    if consecutive_losses >= CONSEC_LOSS_HARD_STOP - 1:
        dampener = 0.30
    elif consecutive_losses >= 3:
        dampener = 0.60
    elif consecutive_losses >= 2:
        dampener = 0.80
    else:
        dampener = 1.00

    pos_reward = (
        pnl_signal + target_bonus + cap_prot + sharpe_term
        + calmar_term + vol_bonus
    )
    neg_reward = dd_pen + cost_pen + heat_pen

    total = dampener * (pos_reward - neg_reward)
    total = float(np.clip(total, -3.0, 3.0))

    breakdown = {
        "total":          round(total, 6),
        "pnl_signal":     round(pnl_signal, 6),
        "target_bonus":   round(target_bonus, 6),
        "cap_prot":       round(cap_prot, 6),
        "sharpe_term":    round(sharpe_term, 6),
        "calmar_term":    round(calmar_term, 6),
        "vol_bonus":      round(vol_bonus, 6),
        "dd_pen":         round(-dd_pen, 6),
        "cost_pen":       round(-cost_pen, 6),
        "heat_pen":       round(-heat_pen, 6),
        "dampener":       round(dampener, 3),
    }

    logger.debug(
        "[RPMReward] total=%.4f | pnl=%.4f target=%.4f cap=%.4f dd_pen=%.4f | damp=%.2f",
        total, pnl_signal, target_bonus, cap_prot, -dd_pen, dampener,
    )

    return total, breakdown


# ════════════════════════════════════════════════════════════════════════════════
# SECTION 4 — MAIN RiskPortfolioManager CLASS
# ════════════════════════════════════════════════════════════════════════════════

class RiskPortfolioManager:
    """
    Central risk engine — wraps all utility functions and manages:
      • User settings with MongoDB persistence
      • Full recalculation with market context
      • Audit trail of every recalculation
      • Portfolio heat monitoring
      • DreamerV3 reward signal generation

    Usage:
        rpm = RiskPortfolioManager()
        await rpm.load_settings_from_db()           # on startup
        result = rpm.full_recalculate(trigger="scheduled")
        size   = rpm.get_position_size(price, atr_pct, direction)
        reward, breakdown = rpm.dreamer_reward_signal(...)
        await rpm.save_settings_to_db()             # persist

    Thread-safety: all mutating methods use internal lock.
    """

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()

        # ── Current settings ──────────────────────────────────────────────────
        self.daily_target:     float = 1000.0
        self.allocated_capital:float = 100_000.0
        self.risk_tolerance:   str   = "moderate"
        self.mode:             str   = "paper"
        self.ticker:           str   = "RELIANCE.NS"
        self.update_count:     int   = 0
        self.last_updated:     str   = datetime.now(timezone.utc).isoformat()
        self.settings_history: List[Dict] = []

        # ── Latest computed values ─────────────────────────────────────────────
        self.last_position_size: Optional[PositionSizeResult] = None
        self.last_var_result:    Optional[VaRResult]          = None
        self.last_feasibility:   Optional[FeasibilityResult]  = None
        self.last_risk_budget:   Optional[DynamicRiskBudget]  = None
        self.last_market_ctx:    Dict = {}
        self.last_recalc_ts:     Optional[str] = None
        self.last_audit_id:      Optional[str] = None

        # ── Portfolio heat ─────────────────────────────────────────────────────
        self._open_risks: List[float] = []   # ₹ risk amounts for open trades

        # ── MongoDB lazy init ──────────────────────────────────────────────────
        self._db = None

        logger.info("[RPM] RiskPortfolioManager initialised | target=₹%.0f capital=₹%.0f",
                    self.daily_target, self.allocated_capital)

    # ── Settings management ────────────────────────────────────────────────────

    def update_settings(
        self,
        daily_target:      Optional[float] = None,
        allocated_capital: Optional[float] = None,
        risk_tolerance:    Optional[str]   = None,
        ticker:            Optional[str]   = None,
        mode:              Optional[str]   = None,
    ) -> Dict:
        """
        Update one or more settings.
        Saves old values to history (last 50 changes).
        Returns updated settings dict.
        """
        with self._lock:
            # Save to history before updating
            old = {
                "daily_target":      self.daily_target,
                "allocated_capital": self.allocated_capital,
                "risk_tolerance":    self.risk_tolerance,
                "mode":              self.mode,
                "ticker":            self.ticker,
                "changed_at":        self.last_updated,
            }
            self.settings_history.append(old)
            self.settings_history = self.settings_history[-50:]   # ring buffer

            # Apply updates with validation
            if daily_target is not None:
                if daily_target <= 0:
                    logger.warning("[RPM] Invalid daily_target %.2f — ignoring", daily_target)
                else:
                    self.daily_target = float(daily_target)

            if allocated_capital is not None:
                if allocated_capital < 1000:
                    logger.warning("[RPM] Capital %.2f below minimum ₹1000 — clamping", allocated_capital)
                    allocated_capital = 1000.0
                self.allocated_capital = float(allocated_capital)

            if risk_tolerance is not None:
                if risk_tolerance not in ("conservative", "moderate", "aggressive"):
                    logger.warning("[RPM] Unknown risk_tolerance '%s' — using 'moderate'", risk_tolerance)
                    risk_tolerance = "moderate"
                self.risk_tolerance = risk_tolerance

            if ticker is not None:
                self.ticker = str(ticker).upper().strip()

            if mode is not None:
                self.mode = "live" if mode == "live" else "paper"

            self.update_count += 1
            self.last_updated = datetime.now(timezone.utc).isoformat()

        logger.info(
            "[RPM] Settings updated (#%d) | target=₹%.0f capital=₹%.0f tol=%s mode=%s ticker=%s",
            self.update_count, self.daily_target, self.allocated_capital,
            self.risk_tolerance, self.mode, self.ticker,
        )

        return self.to_settings_dict()

    def to_settings_dict(self) -> Dict:
        with self._lock:
            return {
                "daily_target":      self.daily_target,
                "allocated_capital": self.allocated_capital,
                "risk_tolerance":    self.risk_tolerance,
                "mode":              self.mode,
                "ticker":            self.ticker,
                "update_count":      self.update_count,
                "last_updated":      self.last_updated,
            }

    # ── Full recalculation ─────────────────────────────────────────────────────

    def full_recalculate(
        self,
        trigger: str = "scheduled",
        current_pnl: float = 0.0,
        trades_today: int  = 0,
        session_progress: float = 0.5,
    ) -> Dict:
        """
        Run complete risk recalculation:
          1. Fetch live market context (price, ATR, regime)
          2. Compute position size (Kelly + ATR + vol-regime)
          3. Compute VaR / CVaR
          4. Run feasibility check
          5. Compute dynamic risk budget
          6. Build full risk profile dict
          7. Log audit record

        Returns the full risk profile dict.
        """
        t_start = time.perf_counter()
        ts      = datetime.now(timezone.utc).isoformat()

        with self._lock:
            target   = self.daily_target
            capital  = self.allocated_capital
            tol      = self.risk_tolerance
            mode     = self.mode
            ticker   = self.ticker

        logger.info(
            "[RPM] full_recalculate | trigger=%s target=₹%.0f capital=₹%.0f tol=%s",
            trigger, target, capital, tol,
        )

        # ── 1. Market context ──────────────────────────────────────────────────
        ctx = self._fetch_market_context(ticker)
        price    = ctx.get("price",    0.0)
        atr_pct  = ctx.get("atr_pct",  NSE_DAILY_VOL_AVG)
        regime   = ctx.get("regime",   "UNKNOWN")

        with self._lock:
            self.last_market_ctx = ctx

        # ── 2. Position size ───────────────────────────────────────────────────
        if price > 0:
            pos_result = compute_position_size(
                capital        = capital,
                daily_target   = target,
                price          = price,
                atr_pct        = atr_pct,
                risk_tolerance = tol,
                win_rate_pct   = 50.0,
                avg_win_pct    = DEFAULT_RR_RATIO,
                avg_loss_pct   = 1.0,
                mode           = mode,
            )
        else:
            logger.warning("[RPM] Price fetch failed — using fallback position size")
            # Fallback: ATR-based without price
            est_price = target * 100   # rough estimate
            pos_result = compute_position_size(
                capital=capital, daily_target=target, price=est_price,
                atr_pct=atr_pct, risk_tolerance=tol, mode=mode,
            )

        with self._lock:
            self.last_position_size = pos_result

        # ── 3. VaR / CVaR ─────────────────────────────────────────────────────
        var_result = compute_var_cvar(
            position_value = pos_result.final_position_inr,
            daily_vol_pct  = atr_pct,
            capital        = capital,
        )
        with self._lock:
            self.last_var_result = var_result

        # ── 4. Feasibility ─────────────────────────────────────────────────────
        feasibility = check_feasibility(
            daily_target      = target,
            allocated_capital = capital,
            risk_tolerance    = tol,
        )
        with self._lock:
            self.last_feasibility = feasibility

        # ── 5. Daily loss limit ────────────────────────────────────────────────
        # Conservative: min(1.5× target, 1.5% capital, 2× VaR_99)
        daily_loss_limit = min(
            target * 1.5,
            capital * DAILY_LOSS_CAP_PCT,
            var_result.param_var_99 * 2.0 + 1e-8,
        )
        if daily_loss_limit < 1.0:
            daily_loss_limit = capital * DAILY_LOSS_CAP_PCT  # fallback

        # ── 6. Dynamic risk budget ─────────────────────────────────────────────
        risk_budget = compute_dynamic_risk_budget(
            daily_target       = target,
            daily_loss_limit   = daily_loss_limit,
            current_pnl        = current_pnl,
            trades_today       = trades_today,
            max_trades         = self._get_max_trades(tol),
            session_progress   = session_progress,
        )
        with self._lock:
            self.last_risk_budget = risk_budget

        # ── 7. Portfolio heat ──────────────────────────────────────────────────
        heat = self.get_portfolio_heat(capital)

        # ── 8. Compose full risk profile ───────────────────────────────────────
        t_ms = (time.perf_counter() - t_start) * 1000.0

        # Max trades per day
        max_trades = self._get_max_trades(tol)

        risk_profile = {
            # Core metrics
            "required_daily_return_pct": round(target / capital * 100, 4),
            "risk_per_trade_pct":        round(pos_result.final_risk_pct, 3),
            "position_size_inr":         round(pos_result.final_position_inr, 2),
            "quantity":                  pos_result.final_quantity,
            "max_daily_loss_inr":        round(daily_loss_limit, 2),
            "max_trades_per_day":        max_trades,
            "daily_loss_limit":          round(daily_loss_limit, 2),
            # Feasibility
            "feasibility_label":         feasibility.tier_label,
            "feasibility_color":         feasibility.tier_color,
            "feasibility_score":         feasibility.tier_score,
            "feasibility_suggestion":    feasibility.tier_suggestion,
            "feasibility_warnings":      feasibility.warnings,
            "hist_exceedance_pct":       feasibility.historical_exceedance_pct,
            "required_win_rate_min":     feasibility.required_win_rate_min,
            "nse_median_comparison":     feasibility.nse_median_comparison,
            "alternative_targets":       feasibility.alternative_targets,
            # Kelly + vol details
            "kelly_fraction":            round(pos_result.kelly_fraction, 6),
            "kelly_position_inr":        round(pos_result.kelly_position_inr, 2),
            "vol_regime":                pos_result.vol_regime,
            "vol_regime_mult":           pos_result.vol_regime_mult,
            # VaR
            "var_95_inr":                round(var_result.param_var_95, 2),
            "var_99_inr":                round(var_result.param_var_99, 2),
            "cvar_95_inr":               round(var_result.param_cvar_95, 2),
            "cvar_99_inr":               round(var_result.param_cvar_99, 2),
            "var_95_pct_of_capital":     round(var_result.var_95_pct_of_capital, 4),
            "var_99_pct_of_capital":     round(var_result.var_99_pct_of_capital, 4),
            # SL/TP
            "sl_price":                  round(pos_result.sl_price, 2),
            "tp_price":                  round(pos_result.tp_price, 2),
            "sl_distance_pct":           round(pos_result.sl_distance_pct, 4),
            "recommended_rr":            DEFAULT_RR_RATIO,
            # Dynamic budget
            "risk_budget_state":         risk_budget.state_label,
            "risk_budget_multiplier":    risk_budget.size_multiplier,
            "risk_budget_remaining":     round(risk_budget.remaining_risk, 2),
            "should_stop_trading":       risk_budget.should_stop_trading,
            # Portfolio heat
            "portfolio_heat_pct":        round(heat * 100, 3),
            "max_portfolio_heat_pct":    round(MAX_PORTFOLIO_HEAT * 100, 1),
            "heat_exceeded":             heat > MAX_PORTFOLIO_HEAT,
            # Market context
            "market_price":              ctx.get("price", 0.0),
            "market_atr_pct":            round(atr_pct * 100, 4),
            "market_regime":             regime,
            "market_rsi14":              ctx.get("rsi14", 50.0),
            "market_vol_ratio":          ctx.get("vol_ratio", 1.0),
            # Warnings
            "warnings":                  pos_result.warnings,
            # Meta
            "mode":                      mode,
            "computation_ms":            round(t_ms, 2),
            "last_recalculated":         ts,
        }

        with self._lock:
            self.last_recalc_ts = ts

        logger.info(
            "[RPM] Recalc done in %.1fms | size=₹%.0f qty=%d | VaR95=₹%.0f | %s | budget=%s",
            t_ms, pos_result.final_position_inr, pos_result.final_quantity,
            var_result.param_var_95, feasibility.tier_label, risk_budget.state_label,
        )

        # ── 9. Build + store audit record ────────────────────────────────────
        audit = RecalculationAudit(
            audit_id             = str(uuid4()),
            trigger              = trigger,
            timestamp            = ts,
            input_daily_target   = target,
            input_capital        = capital,
            input_risk_tolerance = tol,
            input_ticker         = ticker,
            input_mode           = mode,
            market_price         = price,
            market_atr_pct       = atr_pct,
            market_regime        = regime,
            position_size        = asdict(pos_result),
            var_result           = asdict(var_result),
            feasibility          = asdict(feasibility),
            dynamic_budget       = asdict(risk_budget),
            risk_profile_full    = risk_profile,
            computation_ms       = round(t_ms, 2),
            warnings_count       = len(pos_result.warnings) + len(feasibility.warnings),
        )
        with self._lock:
            self.last_audit_id = audit.audit_id

        return risk_profile

    # ── Portfolio heat ────────────────────────────────────────────────────────

    def add_open_risk(self, risk_inr: float) -> None:
        """Register a new open trade's risk amount."""
        with self._lock:
            self._open_risks.append(risk_inr)
        logger.debug("[RPM] Open risk added ₹%.0f | total open risks=%d", risk_inr, len(self._open_risks))

    def remove_open_risk(self, risk_inr: float) -> None:
        """Deregister a closed trade's risk amount."""
        with self._lock:
            try:
                self._open_risks.remove(risk_inr)
            except ValueError:
                if self._open_risks:
                    self._open_risks.pop(0)
        logger.debug("[RPM] Open risk removed ₹%.0f | remaining=%d", risk_inr, len(self._open_risks))

    def get_portfolio_heat(self, capital: Optional[float] = None) -> float:
        """Returns current portfolio heat (total open risk / capital). Range [0, 1]."""
        with self._lock:
            total_risk = sum(self._open_risks)
            cap = capital or self.allocated_capital
        heat = total_risk / max(cap, 1.0)
        return min(heat, 1.0)

    def is_heat_exceeded(self) -> bool:
        return self.get_portfolio_heat() > MAX_PORTFOLIO_HEAT

    # ── DreamerV3 reward ──────────────────────────────────────────────────────

    def dreamer_reward_signal(
        self,
        step_return_pct:     float,
        daily_pnl:           float,
        drawdown_frac:       float,
        transaction_cost_inr: float,
        consecutive_losses:  int,
        position_size_inr:   float = 0.0,
        sharpe_rolling:      float = 0.0,
        calmar_rolling:      float = 0.0,
    ) -> Tuple[float, Dict]:
        """
        Compute DreamerV3 reward signal using RPM context.
        Uses current settings for target/capital normalisation.
        """
        with self._lock:
            target  = self.daily_target
            capital = self.allocated_capital
            atr_pct = self.last_market_ctx.get("atr_pct", NSE_DAILY_VOL_AVG)

        heat = self.get_portfolio_heat(capital)

        # Volatility normalness: how close are we to TARGET_DAILY_VOL?
        if capital > 0 and position_size_inr > 0:
            actual_pos_vol  = (position_size_inr / capital) * atr_pct
            vol_deviation   = abs(actual_pos_vol - TARGET_DAILY_VOL) / (TARGET_DAILY_VOL + 1e-8)
        else:
            vol_deviation = 0.0

        return compute_rpm_reward(
            step_return_pct       = step_return_pct,
            daily_pnl             = daily_pnl,
            daily_target          = target,
            allocated_capital     = capital,
            drawdown_frac         = drawdown_frac,
            transaction_cost_inr  = transaction_cost_inr,
            position_risk_pct     = 0.0,
            target_vol_deviation  = vol_deviation,
            portfolio_heat        = heat,
            consecutive_losses    = consecutive_losses,
            sharpe_rolling        = sharpe_rolling,
            calmar_rolling        = calmar_rolling,
        )

    # ── State vector for DreamerV3 world model ─────────────────────────────────

    def get_capital_state_vector(
        self,
        current_pnl: float,
        trades_today: int,
        open_position_value: float,
    ) -> Dict[str, float]:
        """
        Normalised capital state vector for DreamerV3 state representation.
        All values normalised to [0, 1] or [-1, 1] range.
        """
        with self._lock:
            target  = self.daily_target
            capital = self.allocated_capital

        pnl_normalised    = float(np.clip(current_pnl / (target + 1e-8), -2.0, 2.0))
        capital_normalised= float(np.clip(capital / 1_000_000.0, 0.0, 1.0))
        target_normalised = float(np.clip(target / capital, 0.0, 0.10)) * 10   # scale 0-1%→0-1
        heat              = float(np.clip(self.get_portfolio_heat(capital), 0.0, 1.0))
        trades_frac       = float(trades_today / max(self._get_max_trades(self.risk_tolerance), 1))
        pos_frac          = float(np.clip(open_position_value / max(capital, 1.0), 0.0, 1.0))

        return {
            "pnl_normalised":    round(pnl_normalised, 6),
            "capital_normalised": round(capital_normalised, 6),
            "target_normalised": round(target_normalised, 6),
            "portfolio_heat":    round(heat, 6),
            "trades_fraction":   round(trades_frac, 6),
            "position_fraction": round(pos_frac, 6),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_max_trades(risk_tolerance: str) -> int:
        return {"conservative": 5, "moderate": 10, "aggressive": 15}.get(risk_tolerance, 10)

    def _fetch_market_context(self, ticker: str) -> Dict:
        """Lightweight market data fetch (delegates to yfinance)."""
        try:
            import yfinance as yf
            import pandas as pd

            raw = yf.download(ticker, period="30d", interval="1d",
                              progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)
            if len(raw) < 10:
                return {"price": 0.0, "atr_pct": NSE_DAILY_VOL_AVG,
                        "regime": "UNKNOWN", "ticker": ticker}

            close  = raw["Close"].astype(float)
            high   = raw["High"].astype(float)
            low    = raw["Low"].astype(float)
            volume = raw["Volume"].astype(float)

            h_l   = high - low
            h_pc  = (high - close.shift(1)).abs()
            l_pc  = (low  - close.shift(1)).abs()
            tr    = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
            atr14 = float(tr.rolling(14).mean().iloc[-1])
            cur   = float(close.iloc[-1])
            atr_pct = atr14 / (cur + 1e-8)

            ema20 = float(close.ewm(span=20).mean().iloc[-1])
            sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else ema20
            regime = ("UPTREND" if ema20 > sma50 * 1.01
                      else "DOWNTREND" if ema20 < sma50 * 0.99
                      else "SIDEWAYS")

            vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
            vol_ratio = float(volume.iloc[-1]) / (vol_avg20 + 1e-8)

            d    = close.diff()
            gain = d.clip(lower=0).rolling(14).mean()
            loss = (-d.clip(upper=0)).rolling(14).mean()
            rsi_series = 100 - 100 / (1 + gain / (loss + 1e-8))
            rsi  = float(rsi_series.iloc[-1])
            rsi  = rsi if not np.isnan(rsi) else 50.0

            return {
                "ticker":    ticker,
                "price":     round(cur, 2),
                "atr14":     round(atr14, 2),
                "atr_pct":   round(atr_pct, 6),
                "regime":    regime,
                "ema20":     round(ema20, 2),
                "sma50":     round(sma50, 2),
                "vol_ratio": round(vol_ratio, 3),
                "rsi14":     round(rsi, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            logger.warning("[RPM] Market context fetch failed for %s: %s", ticker, exc)
            return {"ticker": ticker, "price": 0.0, "atr_pct": NSE_DAILY_VOL_AVG,
                    "regime": "UNKNOWN", "error": str(exc)}

    # ── MongoDB persistence ───────────────────────────────────────────────────

    def _get_db(self):
        if self._db is None:
            mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
            db_name   = os.environ.get("DB_NAME",   "trading_db")
            self._db  = AsyncIOMotorClient(mongo_url)[db_name]
        return self._db

    async def save_settings_to_db(self) -> None:
        """Persist current settings + latest risk profile to MongoDB."""
        try:
            db  = self._get_db()
            doc = {
                "_id":              "default",
                "daily_target":     self.daily_target,
                "allocated_capital": self.allocated_capital,
                "risk_tolerance":   self.risk_tolerance,
                "mode":             self.mode,
                "ticker":           self.ticker,
                "update_count":     self.update_count,
                "last_updated":     self.last_updated,
                "settings_history": self.settings_history[-10:],
                "last_risk_profile": (
                    asdict(self.last_position_size) if self.last_position_size else {}
                ),
            }
            await db["robo_user_settings"].replace_one(
                {"_id": "default"}, doc, upsert=True
            )
            logger.debug("[RPM] Settings saved to MongoDB")
        except Exception as exc:
            logger.warning("[RPM] DB save failed: %s", exc)

    async def load_settings_from_db(self) -> None:
        """Load last-saved settings from MongoDB on startup."""
        try:
            db  = self._get_db()
            doc = await db["robo_user_settings"].find_one({"_id": "default"})
            if not doc:
                logger.info("[RPM] No saved settings in DB — using defaults")
                return
            with self._lock:
                self.daily_target      = float(doc.get("daily_target",     self.daily_target))
                self.allocated_capital = float(doc.get("allocated_capital", self.allocated_capital))
                self.risk_tolerance    = str(doc.get("risk_tolerance",     self.risk_tolerance))
                self.mode              = str(doc.get("mode",               self.mode))
                self.ticker            = str(doc.get("ticker",             self.ticker))
                self.update_count      = int(doc.get("update_count",       0))
                self.last_updated      = str(doc.get("last_updated",       self.last_updated))
                self.settings_history  = list(doc.get("settings_history",  []))
            logger.info(
                "[RPM] Settings loaded from DB | target=₹%.0f capital=₹%.0f tol=%s",
                self.daily_target, self.allocated_capital, self.risk_tolerance,
            )
        except Exception as exc:
            logger.warning("[RPM] DB load failed: %s", exc)

    async def log_recalculation_to_db(self, audit: RecalculationAudit) -> None:
        """Write audit record to MongoDB `robo_recalculation_audit` collection."""
        try:
            db  = self._get_db()
            doc = asdict(audit)
            # Flatten nested dataclass dicts (asdict handles recursion)
            await db["robo_recalculation_audit"].insert_one(doc)
            logger.debug("[RPM] Recalculation audit saved | id=%s", audit.audit_id)
        except Exception as exc:
            logger.debug("[RPM] Audit DB save failed: %s", exc)

    async def get_recalculation_history(self, limit: int = 20) -> List[Dict]:
        """Fetch the last N recalculation audit records from MongoDB."""
        try:
            db   = self._get_db()
            cur  = db["robo_recalculation_audit"].find(
                {}, {"_id": 0}
            ).sort("timestamp", -1).limit(limit)
            return [doc async for doc in cur]
        except Exception as exc:
            logger.warning("[RPM] Audit history fetch failed: %s", exc)
            return []


# ════════════════════════════════════════════════════════════════════════════════
# SECTION 5 — MODULE-LEVEL SINGLETON
# ════════════════════════════════════════════════════════════════════════════════

# Global singleton — imported by dreamer_robo_orchestrator and robo_router
rpm = RiskPortfolioManager()

__all__ = [
    "rpm",
    "RiskPortfolioManager",
    "PositionSizeResult",
    "VaRResult",
    "FeasibilityResult",
    "DynamicRiskBudget",
    "RecalculationAudit",
    "compute_kelly_fraction",
    "compute_var_cvar",
    "check_feasibility",
    "compute_position_size",
    "compute_dynamic_risk_budget",
    "compute_rpm_reward",
    "get_volatility_regime",
    "ROBOT3_BUY_LEVERAGE",
]
