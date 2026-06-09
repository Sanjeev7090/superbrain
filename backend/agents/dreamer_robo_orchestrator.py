"""
Dreamer V3 Robo-Trader Orchestrator
====================================
Institutional-grade autonomous trading engine built on the existing DreamerV3 world model.

Architecture (Hierarchical Multi-Agent):
  1. Perception Layer     : Market Intelligence (multi-timeframe OI + order flow + regime)
  2. DreamerV3 Core       : World Model + Actor-Critic (existing dreamer_trainer)
  3. Risk & Portfolio Agent: Dynamic position sizing (VaR/CVaR, Calmar, Sharpe)
  4. Meta-Orchestrator    : Combines DreamerV3 + user-defined targets → paper execution
  5. Execution Engine     : Phase 3 — paper / live / shadow order management
  6. Trading Loop         : Phase 3 — APScheduler scan-and-execute loop

User-Controlled Settings (editable at any time):
  - daily_profit_target  (e.g., ₹500, ₹2000)
  - allocated_capital    (e.g., ₹50,000, ₹1,00,000)

System auto-recalculates:
  - Required daily return %
  - Risk per trade (0.5-2% of capital)
  - Position size (ATR-volatility targeted)
  - Feasibility tier (Easily Achievable / Achievable / Moderate / Aggressive / Unrealistic)
  - VaR 1-day 95% confidence

Phase 3 Additions:
  - ExecutionEngine: paper/live/shadow order lifecycle
  - TradingLoop: APScheduler-powered periodic scan cycle
  - Groww API integration for live mode
  - Full trade audit with execution metadata

Safety Circuit Breakers:
  - Max daily loss limit: 1.5× daily target or 2% capital (whichever is smaller)
  - Account drawdown > 5% → pause auto mode
  - Consecutive losses > 3 → reduce position size by 50%

DISCLAIMER: This system is for PAPER TRADING / RESEARCH ONLY.
No guaranteed returns. Past performance ≠ future results.
Always consult a SEBI-registered advisor before live trading.
"""

import asyncio
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, date, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

# ─── Phase 2: Import Risk Portfolio Manager ───────────────────────────────────
from .risk_portfolio_manager import (
    rpm as _rpm,
    compute_rpm_reward,
    compute_dynamic_risk_budget,
    check_feasibility,
    RiskPortfolioManager,
)

logger = logging.getLogger(__name__)

# ─── MongoDB setup (uses same env vars as server.py) ─────────────────────────
_mongo_client: Optional[AsyncIOMotorClient] = None
_db = None


def _get_db():
    global _mongo_client, _db
    if _db is None:
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        db_name   = os.environ.get("DB_NAME",   "trading_db")
        _mongo_client = AsyncIOMotorClient(mongo_url)
        _db = _mongo_client[db_name]
    return _db


# ─── Constants ────────────────────────────────────────────────────────────────
DEFAULT_DAILY_TARGET   = 1000.0   # ₹1,000
DEFAULT_CAPITAL        = 100_000.0  # ₹1,00,000
DEFAULT_TICKER         = "RELIANCE.NS"

# Feasibility thresholds (daily return %)
FEASIBILITY_TIERS = [
    (0.20, "Easily Achievable",       "#10b981", 95),
    (0.50, "Achievable",              "#84cc16", 80),
    (1.00, "Moderately Aggressive",   "#f59e0b", 60),
    (2.00, "Aggressive – High Risk",  "#f97316", 35),
    (5.00, "Very Aggressive",         "#ef4444", 15),
    (99.0, "Unrealistic",             "#dc2626",  5),
]

# Reward shaping weights
TARGET_PROGRESS_WEIGHT = 0.30    # daily-target progress bonus
SHARPE_WEIGHT          = 0.15
CALMAR_WEIGHT          = 0.10
CAPITAL_PROT_BONUS     = 0.10    # reward for keeping drawdown low
DD_PENALTY_FACTOR      = 20.0    # convex drawdown penalty
COST_WEIGHT            = 1.0

# Position sizing
BASE_RISK_PER_TRADE    = 0.01    # 1% of capital
MIN_RISK_PER_TRADE     = 0.005   # 0.5%
MAX_RISK_PER_TRADE     = 0.02    # 2%
ATR_MULTIPLIER_SL      = 2.0     # stop-loss = 2 × ATR

# Circuit breakers
MAX_DAILY_LOSS_FACTOR  = 1.5     # max loss = 1.5 × daily_target
ACCOUNT_DD_CIRCUIT     = 0.05    # 5% account drawdown → pause
CONSEC_LOSS_THRESHOLD  = 3       # 3 consecutive losses → halve size
MAX_TRADES_PER_DAY     = 10      # hard limit


# ─── Data Models ─────────────────────────────────────────────────────────────

class UserPreferences:
    """
    User-defined trading parameters — editable at any time via API.
    Stored in MongoDB `robo_user_preferences` collection (singleton doc id=default).
    """
    def __init__(
        self,
        daily_profit_target: float = DEFAULT_DAILY_TARGET,
        allocated_capital: float   = DEFAULT_CAPITAL,
        ticker: str                = DEFAULT_TICKER,
        risk_tolerance: str        = "moderate",  # conservative / moderate / aggressive
        auto_mode: bool            = False,
        watchlist: list            = None,
        max_parallel_trades: int   = 3,
    ):
        self.daily_profit_target = max(0.0, daily_profit_target)
        self.allocated_capital   = max(1000.0, allocated_capital)
        self.ticker              = ticker
        self.risk_tolerance      = risk_tolerance
        self.auto_mode           = auto_mode
        self.watchlist           = list(watchlist) if watchlist else []
        self.max_parallel_trades = max(1, min(5, int(max_parallel_trades)))

    def to_dict(self) -> Dict:
        return {
            "daily_profit_target": self.daily_profit_target,
            "allocated_capital":   self.allocated_capital,
            "ticker":              self.ticker,
            "risk_tolerance":      self.risk_tolerance,
            "auto_mode":           self.auto_mode,
            "watchlist":           self.watchlist,
            "max_parallel_trades": self.max_parallel_trades,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "UserPreferences":
        return cls(
            daily_profit_target = float(d.get("daily_profit_target", DEFAULT_DAILY_TARGET)),
            allocated_capital   = float(d.get("allocated_capital",   DEFAULT_CAPITAL)),
            ticker              = str(d.get("ticker", DEFAULT_TICKER)),
            risk_tolerance      = str(d.get("risk_tolerance", "moderate")),
            auto_mode           = bool(d.get("auto_mode", False)),
            watchlist           = list(d.get("watchlist", [])),
            max_parallel_trades = int(d.get("max_parallel_trades", 3)),
        )


class RiskProfile:
    """
    Dynamically computed risk profile based on user preferences + market conditions.
    Recalculated every time user updates settings or market volatility changes.
    """
    def __init__(self, prefs: UserPreferences, market_atr_pct: float = 0.015):
        cap    = prefs.allocated_capital
        target = prefs.daily_profit_target
        tol    = prefs.risk_tolerance

        # Required daily return to hit target
        self.required_daily_return_pct = (target / cap) * 100.0

        # Risk per trade adjustments by tolerance
        tol_mult = {"conservative": 0.6, "moderate": 1.0, "aggressive": 1.6}
        mult = tol_mult.get(tol, 1.0)

        self.risk_per_trade_pct = float(np.clip(
            BASE_RISK_PER_TRADE * mult * 100,
            MIN_RISK_PER_TRADE * 100,
            MAX_RISK_PER_TRADE * 100,
        ))

        # Position size in ₹ per trade (ATR-based)
        risk_amount = cap * (self.risk_per_trade_pct / 100.0)
        atr_abs = cap * market_atr_pct
        sl_distance = atr_abs * ATR_MULTIPLIER_SL
        self.position_size_inr = risk_amount / (market_atr_pct * ATR_MULTIPLIER_SL + 1e-8)
        self.position_size_inr = float(np.clip(self.position_size_inr, 0, cap * 0.40))

        # Max daily loss (circuit breaker level)
        self.max_daily_loss_inr = min(
            target * MAX_DAILY_LOSS_FACTOR,
            cap * 0.02,   # hard 2% capital limit
        )

        # Max trades per day (scales with risk tolerance)
        max_t_mult = {"conservative": 0.5, "moderate": 1.0, "aggressive": 1.5}
        self.max_trades_per_day = max(1, int(MAX_TRADES_PER_DAY * max_t_mult.get(tol, 1.0)))

        # VaR 1-day 95% (parametric normal, assuming market_atr_pct as daily σ)
        position_frac = self.position_size_inr / max(cap, 1.0)
        self.var_1day_95 = abs(cap * position_frac * market_atr_pct * 1.645)

        # Feasibility scoring
        r = self.required_daily_return_pct
        for thresh, label, color, score in FEASIBILITY_TIERS:
            if r <= thresh:
                self.feasibility_label = label
                self.feasibility_color = color
                self.feasibility_score = score
                break
        else:
            self.feasibility_label = "Unrealistic"
            self.feasibility_color = "#dc2626"
            self.feasibility_score = 0

        # Expected win-rate needed (assuming 1:1.5 RR)
        rr_ratio   = 1.5
        self.min_winrate_needed = 1.0 / (1.0 + rr_ratio) * 100  # ~40%
        self.recommended_rr = rr_ratio

    def to_dict(self) -> Dict:
        return {
            "required_daily_return_pct":  round(self.required_daily_return_pct, 4),
            "risk_per_trade_pct":         round(self.risk_per_trade_pct, 3),
            "position_size_inr":          round(self.position_size_inr, 2),
            "max_daily_loss_inr":         round(self.max_daily_loss_inr, 2),
            "max_trades_per_day":         self.max_trades_per_day,
            "var_1day_95":                round(self.var_1day_95, 2),
            "feasibility_label":          self.feasibility_label,
            "feasibility_color":          self.feasibility_color,
            "feasibility_score":          self.feasibility_score,
            "min_winrate_needed":         round(self.min_winrate_needed, 1),
            "recommended_rr":             self.recommended_rr,
        }


# ─── Paper Trade Record ───────────────────────────────────────────────────────

class PaperTrade:
    """Represents a single paper trade decision and outcome."""
    def __init__(
        self,
        trade_id: str,
        ticker: str,
        direction: str,       # BUY / SELL / HOLD
        entry_price: float,
        quantity: int,
        position_value: float,
        sl_price: float,
        tp_price: float,
        confidence: float,
        dreamer_signal: float,
        strategy_weights: Dict,
        risk_profile_snapshot: Dict,
        daily_target: float,
        allocated_capital: float,
    ):
        self.trade_id      = trade_id
        self.ticker        = ticker
        self.direction     = direction
        self.entry_price   = entry_price
        self.quantity      = quantity
        self.position_value = position_value
        self.sl_price      = sl_price
        self.tp_price      = tp_price
        self.confidence    = confidence
        self.dreamer_signal = dreamer_signal
        self.strategy_weights = strategy_weights
        self.risk_profile_snapshot = risk_profile_snapshot
        self.daily_target  = daily_target
        self.allocated_capital = allocated_capital
        self.entry_time    = datetime.now(timezone.utc).isoformat()
        self.exit_time     = None
        self.exit_price    = None
        self.pnl           = None
        self.pnl_pct       = None
        self.exit_reason   = None   # SL / TP / MANUAL / CIRCUIT_BREAKER / EOD
        self.status        = "OPEN"  # OPEN / CLOSED

    def close(self, exit_price: float, exit_reason: str):
        self.exit_price  = exit_price
        self.exit_time   = datetime.now(timezone.utc).isoformat()
        self.exit_reason = exit_reason
        self.status      = "CLOSED"
        if self.direction == "BUY":
            self.pnl = (exit_price - self.entry_price) * self.quantity
        elif self.direction == "SELL":
            self.pnl = (self.entry_price - exit_price) * self.quantity
        else:
            self.pnl = 0.0
        self.pnl_pct = (self.pnl / max(self.position_value, 1e-8)) * 100

    def to_dict(self) -> Dict:
        return {
            "trade_id":              self.trade_id,
            "ticker":                self.ticker,
            "direction":             self.direction,
            "entry_price":           round(self.entry_price, 2),
            "quantity":              self.quantity,
            "position_value":        round(self.position_value, 2),
            "sl_price":              round(self.sl_price, 2),
            "tp_price":              round(self.tp_price, 2),
            "confidence":            round(self.confidence, 1),
            "dreamer_signal":        round(self.dreamer_signal, 4),
            "strategy_weights":      self.strategy_weights,
            "risk_profile_snapshot": self.risk_profile_snapshot,
            "daily_target":          self.daily_target,
            "allocated_capital":     self.allocated_capital,
            "entry_time":            self.entry_time,
            "exit_time":             self.exit_time,
            "exit_price":            round(self.exit_price, 2) if self.exit_price else None,
            "pnl":                   round(self.pnl, 2) if self.pnl is not None else None,
            "pnl_pct":               round(self.pnl_pct, 3) if self.pnl_pct is not None else None,
            "exit_reason":           self.exit_reason,
            "status":                self.status,
        }


# ─── Robo Orchestrator State ──────────────────────────────────────────────────

_lock  = threading.Lock()
_state: Dict = {
    # Settings
    "daily_profit_target":  DEFAULT_DAILY_TARGET,
    "allocated_capital":    DEFAULT_CAPITAL,
    "ticker":               DEFAULT_TICKER,
    "risk_tolerance":       "moderate",
    # Mode
    "auto_mode":            False,
    "status":               "idle",       # idle | scanning | trading | paused | circuit_breaker
    "mode":                 "paper",      # paper | live | shadow
    # Watchlist & parallel trading
    "watchlist":            [],
    "max_parallel_trades":  3,
    # Daily progress
    "daily_pnl":            0.0,
    "daily_trades":         0,
    "daily_target_pct":     0.0,          # daily_pnl / daily_profit_target * 100
    "daily_drawdown":       0.0,
    "circuit_breaker":      False,
    "circuit_reason":       None,
    # Current decision
    "current_decision":     None,         # latest DreamerV3 decision
    "current_position":     None,         # open paper position
    "open_trade":           None,         # PaperTrade dict
    # Dreamer V3 bridge state
    "dreamer_signal":       0.0,
    "dreamer_confidence":   0,
    "dreamer_weights":      {},
    "dreamer_wm_loss":      0.0,
    # Risk profile
    "risk_profile":         {},
    # Performance
    "total_paper_pnl":      0.0,
    "total_trades":         0,
    "win_trades":           0,
    "loss_trades":          0,
    "consecutive_losses":   0,
    "peak_capital":         DEFAULT_CAPITAL,
    "current_capital":      DEFAULT_CAPITAL,
    # Meta
    "last_decision_time":   None,
    "last_updated":         None,
    "error":                None,
    "uptime_start":         None,
    # Audit trail (in-memory ring, last 100 trades)
    "audit_trail":          [],
    # ── Multi-Agent Collaboration (Phase 4+) ──
    "agent_discussion":     None,         # latest AgentDiscussion dict
    "last_scan_time":       None,         # when last collaborative scan ran
    "scan_mode":            "auto",       # auto | manual | hybrid
    "manual_tickers":       [],           # user-added tickers
    "top_picks":            [],           # top stocks from last scan
}

_stop_evt   = threading.Event()
_robo_thread: Optional[threading.Thread] = None
_prefs      = UserPreferences()


def _upd(**kw):
    with _lock:
        _state.update(kw)
        _state["last_updated"] = datetime.now(timezone.utc).isoformat()


def get_robo_state() -> Dict:
    with _lock:
        return dict(_state)


# ─── Multi-Agent Collaboration ────────────────────────────────────────────────

def _run_collaborative_analysis(
    ticker:    str,
    capital:   float = None,
    risk_tol:  str   = None,
    deep:      bool  = False,
) -> Dict:
    """
    Run StrategyCollaborator for a ticker and store result in state.
    Returns the AgentDiscussion dict.
    Called from: auto-mode worker, manual scan trigger, decision engine.
    """
    try:
        from .strategy_collaborator import collaborator
        with _lock:
            cap = capital or _state.get("allocated_capital", DEFAULT_CAPITAL)
            tol = risk_tol or _state.get("risk_tolerance", "moderate")

        disc = collaborator.run_discussion(ticker, capital=cap, risk_tolerance=tol, deep=deep)
        disc_dict = disc.to_dict()
        _upd(
            agent_discussion = disc_dict,
            last_scan_time   = datetime.now(timezone.utc).isoformat(),
        )
        logger.info(
            "[Collab] Discussion done: %s | %s %.0f%% | bull=%d bear=%d",
            ticker, disc.consensus, disc.consensus_confidence,
            disc.bull_count, disc.bear_count,
        )
        return disc_dict
    except Exception as exc:
        logger.warning("[Collab] Discussion failed for %s: %s", ticker, exc)
        return {"error": str(exc), "ticker": ticker}


def trigger_scan(
    mode:            str   = "hybrid",
    deep:            bool  = False,
    manual_tickers:  List  = None,
    capital:         float = None,
) -> Dict:
    """
    Trigger an immediate collaborative scan across tickers.
    Returns: {top_picks: [...], scan_mode, timestamp}
    """
    try:
        from .strategy_collaborator import collaborator
        with _lock:
            cap    = capital or _state.get("allocated_capital", DEFAULT_CAPITAL)
            manual = manual_tickers or list(_state.get("manual_tickers", []))
            mode   = mode or _state.get("scan_mode", "hybrid")

        logger.info("[Collab] Scan triggered | mode=%s | manual=%s", mode, manual)
        discussions = collaborator.scan_and_rank(
            manual_tickers=manual, mode=mode, capital=cap, top_n=5
        )

        top_picks = []
        for d in discussions:
            top_picks.append({
                "ticker":         d.ticker,
                "consensus":      d.consensus,
                "confidence":     round(d.consensus_confidence, 1),
                "score":          round(d.weighted_score, 1),
                "bull":           d.bull_count,
                "bear":           d.bear_count,
                "win_probability": round(d.win_probability, 1),
                "conviction_score": round(d.conviction_score, 1),
                "volume_ratio":   round(d.volume_ratio, 2),
                "intraday_score": round(d.intraday_score, 2),
                "agent_agreement_map": d.agent_agreement_map,
                "mc_target_prob": d.monte_carlo.get("target_hit_prob", 0),
                "scan_id":        d.scan_id,
            })

        # Store best discussion in state
        if discussions:
            best = discussions[0]
            _upd(
                agent_discussion = best.to_dict(),
                top_picks        = top_picks,
                last_scan_time   = datetime.now(timezone.utc).isoformat(),
                scan_mode        = mode,
            )

        return {
            "success":    True,
            "top_picks":  top_picks,
            "scan_mode":  mode,
            "tickers_scanned": len(discussions),
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("[Collab] Scan error: %s", exc)
        return {"success": False, "error": str(exc)}


def add_manual_ticker(ticker: str) -> Dict:
    """Add a ticker to the manual watchlist."""
    ticker = ticker.strip().upper()
    if not ticker.endswith(".NS") and not ticker.endswith(".BO"):
        ticker = ticker + ".NS"
    with _lock:
        tickers = list(_state.get("manual_tickers", []))
        if ticker not in tickers:
            tickers.append(ticker)
            _state["manual_tickers"] = tickers
    logger.info("[Collab] Added manual ticker: %s", ticker)
    return {"success": True, "ticker": ticker, "manual_tickers": tickers}


def remove_manual_ticker(ticker: str) -> Dict:
    """Remove a ticker from the manual watchlist."""
    ticker = ticker.strip().upper()
    with _lock:
        tickers = [t for t in _state.get("manual_tickers", []) if t != ticker]
        _state["manual_tickers"] = tickers
    return {"success": True, "manual_tickers": tickers}


def get_manual_tickers() -> List[str]:
    with _lock:
        return list(_state.get("manual_tickers", []))


def get_agent_discussion() -> Optional[Dict]:
    with _lock:
        return dict(_state.get("agent_discussion") or {})


def get_top_picks() -> List[Dict]:
    with _lock:
        return list(_state.get("top_picks") or [])


# ─── Risk Recalculation — delegates to RPM ────────────────────────────────────

def _recalculate_risk(prefs: UserPreferences, market_atr_pct: float = 0.015) -> RiskProfile:
    """
    Recompute full risk profile whenever user changes settings or market vol changes.
    Phase 2: delegates to RiskPortfolioManager for Kelly + VaR + Feasibility.
    """
    return RiskProfile(prefs, market_atr_pct)


def _recalculate_risk_full(
    trigger: str = "scheduled",
    current_pnl: float = 0.0,
    trades_today: int = 0,
) -> Dict:
    """
    Phase 2 full recalculation via RPM — returns the complete risk profile dict
    (position size, VaR, feasibility, dynamic budget, portfolio heat).
    Syncs RPM settings from _prefs before calculating.
    """
    # Sync RPM with current prefs
    with _lock:
        prefs = _prefs

    _rpm.update_settings(
        daily_target      = prefs.daily_profit_target,
        allocated_capital = prefs.allocated_capital,
        risk_tolerance    = prefs.risk_tolerance,
        ticker            = prefs.ticker,
        mode              = _state.get("mode", "paper"),
    )

    # Compute session progress (NSE: 09:15 – 15:30 IST)
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    session_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    session_end   = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    total_secs = max((session_end - session_start).total_seconds(), 1)
    elapsed    = (now_ist - session_start).total_seconds()
    session_progress = float(np.clip(elapsed / total_secs, 0.0, 1.0))

    risk_profile = _rpm.full_recalculate(
        trigger          = trigger,
        current_pnl      = current_pnl,
        trades_today     = trades_today,
        session_progress = session_progress,
    )

    logger.info(
        "[Orchestrator] Risk recalculated | %s | size=₹%.0f | heat=%.1f%% | budget=%s",
        risk_profile.get("feasibility_label", "?"),
        risk_profile.get("position_size_inr", 0),
        risk_profile.get("portfolio_heat_pct", 0),
        risk_profile.get("risk_budget_state", "?"),
    )
    return risk_profile


def compute_robo_reward(
    step_return_pct: float,
    daily_pnl: float,
    daily_target: float,
    allocated_capital: float,
    drawdown: float,
    transaction_cost: float,
    consecutive_losses: int,
    sharpe_rolling: float = 0.0,
    calmar_rolling: float = 0.0,
    position_size_inr: float = 0.0,
) -> float:
    """
    Phase 2 reward function — delegates to RPM's enhanced compute_rpm_reward().
    Uses capital state from RPM for normalisation.

    Backwards-compatible signature (extra `position_size_inr` kwarg added).
    Returns scalar reward clipped to [-3, +3].
    """
    reward, breakdown = _rpm.dreamer_reward_signal(
        step_return_pct      = step_return_pct,
        daily_pnl            = daily_pnl,
        drawdown_frac        = drawdown,
        transaction_cost_inr = transaction_cost * allocated_capital,  # convert to ₹
        consecutive_losses   = consecutive_losses,
        position_size_inr    = position_size_inr,
        sharpe_rolling       = sharpe_rolling,
        calmar_rolling       = calmar_rolling,
    )
    logger.debug("[Reward] %.4f | breakdown=%s", reward, breakdown)
    return reward


# ─── Market Intelligence Layer ────────────────────────────────────────────────

def _fetch_live_price(ticker: str) -> Optional[float]:
    """Fetch current price via yfinance (1m candle)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.debug("Live price fetch failed for %s: %s", ticker, exc)
        return None


def _fetch_market_context(ticker: str) -> Dict:
    """
    Multi-timeframe market intelligence:
      - Current price
      - ATR (14, daily)
      - Regime (trend direction)
      - Volume ratio
    """
    try:
        import yfinance as yf
        import pandas as pd

        raw = yf.download(ticker, period="30d", interval="1d", progress=False, auto_adjust=True)
        # Handle MultiIndex columns from yfinance
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.droplevel(1)
        if len(raw) < 10:
            return {"error": "Insufficient data"}

        close  = raw["Close"].astype(float)
        high   = raw["High"].astype(float)
        low    = raw["Low"].astype(float)
        volume = raw["Volume"].astype(float)

        # ATR-14
        h_l    = high - low
        h_pc   = (high - close.shift(1)).abs()
        l_pc   = (low  - close.shift(1)).abs()
        tr     = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
        atr14  = float(tr.rolling(14).mean().iloc[-1])
        cur    = float(close.iloc[-1])
        atr_pct = atr14 / (cur + 1e-8)

        # Regime
        ema20 = float(close.ewm(span=20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else ema20
        regime = "UPTREND" if ema20 > sma50 * 1.01 else ("DOWNTREND" if ema20 < sma50 * 0.99 else "SIDEWAYS")

        # Volume ratio
        vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / (vol_avg20 + 1e-8)

        # RSI-14
        d     = close.diff()
        gain  = d.clip(lower=0).rolling(14).mean()
        loss  = (-d.clip(upper=0)).rolling(14).mean()
        rsi_series = 100 - 100 / (1 + gain / (loss + 1e-8))
        rsi   = float(rsi_series.iloc[-1])
        rsi   = rsi if not np.isnan(rsi) else 50.0

        return {
            "ticker":    ticker,
            "price":     round(cur, 2),
            "atr14":     round(atr14, 2),
            "atr_pct":   round(atr_pct, 5),
            "regime":    regime,
            "ema20":     round(ema20, 2),
            "sma50":     round(sma50, 2),
            "vol_ratio": round(vol_ratio, 3),
            "rsi14":     round(rsi, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.debug("Market context failed for %s: %s", ticker, exc)
        return {"ticker": ticker, "price": 0.0, "atr_pct": 0.015, "regime": "UNKNOWN", "error": str(exc)}


# ─── DreamerV3 Decision Bridge ────────────────────────────────────────────────

def _get_dreamer_decision(ticker: str, prefs: UserPreferences, risk: RiskProfile) -> Dict:
    """
    Bridge to existing DreamerV3 agent:
      1. Get DreamerV3 state (signal + weights + confidence)
      2. Apply user-target–aware position sizing
      3. Apply risk profile constraints
      4. Return actionable decision
    """
    try:
        import sys
        import importlib
        from pathlib import Path

        parent = str(Path(__file__).parent.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        # Import dreamer_trainer from rl_agent package
        rl = importlib.import_module("rl_agent.dreamer_trainer")

        state      = rl.get_state()
        dreamer_status = state.get("status", "idle")

        if dreamer_status not in ("running", "training", "paused"):
            return {
                "signal":      "HOLD",
                "confidence":  0,
                "direction":   0,
                "quantity":    0,
                "entry_price": 0.0,
                "sl_price":    0.0,
                "tp_price":    0.0,
                "position_value": 0.0,
                "dreamer_active": False,
                "message":     "DreamerV3 not active – start RL training first.",
                "strategy_weights": {},
                "wm_loss":     0.0,
            }

        # Use prediction from dreamer
        pred = rl.get_prediction(ticker)
        signal     = pred.get("signal", "HOLD")
        confidence = pred.get("confidence", 0)
        _trade_sig  = state.get("last_trade_signal", 0.0)
        weights    = pred.get("strategy_weights", {})
        wm_loss    = pred.get("wm_loss", 0.0)

        # Market context for position sizing
        ctx = _fetch_market_context(ticker)
        price     = ctx.get("price", 0.0)
        _atr_pct  = ctx.get("atr_pct", 0.015)
        atr14     = ctx.get("atr14", price * 0.015)
        regime    = ctx.get("regime", "UNKNOWN")
        rsi14     = ctx.get("rsi14", 50.0)

        # Risk-adjusted quantity based on user settings
        risk_amount_inr = prefs.allocated_capital * (risk.risk_per_trade_pct / 100.0)
        sl_distance_inr = atr14 * ATR_MULTIPLIER_SL

        if price > 0 and sl_distance_inr > 0:
            quantity = max(1, int(risk_amount_inr / sl_distance_inr))
        else:
            quantity = 1

        position_value = quantity * price if price > 0 else 0.0

        # SL / TP prices
        if signal == "BUY":
            sl_price = price - atr14 * ATR_MULTIPLIER_SL
            tp_price = price + atr14 * ATR_MULTIPLIER_SL * risk.recommended_rr
        elif signal == "SELL":
            sl_price = price + atr14 * ATR_MULTIPLIER_SL
            tp_price = price - atr14 * ATR_MULTIPLIER_SL * risk.recommended_rr
        else:
            sl_price = tp_price = price

        # Daily-target-aware confidence boost
        # If we're behind target, be slightly more aggressive with signals
        daily_pnl    = _state.get("daily_pnl", 0.0)
        target_gap   = prefs.daily_profit_target - daily_pnl
        behind_boost = 1.0
        if target_gap > 0 and prefs.daily_profit_target > 0:
            behind_frac  = min(target_gap / prefs.daily_profit_target, 1.0)
            behind_boost = 1.0 + behind_frac * 0.20  # up to 20% confidence boost

        effective_confidence = min(100, int(confidence * behind_boost))

        # ── Agent Consensus Blending ──────────────────────────────────────
        # Pull latest discussion from state (non-blocking)
        agent_consensus = {}
        blended_signal  = signal
        blended_conf    = effective_confidence
        agent_override  = False

        with _lock:
            disc = _state.get("agent_discussion") or {}

        if disc and disc.get("ticker") == ticker:
            c_signal = disc.get("consensus", "HOLD")
            c_conf   = float(disc.get("consensus_confidence", 0))
            c_score  = float(disc.get("weighted_score", 0))

            # Dynamic blend weights: if DreamerV3 is idle/HOLD, give agents full weight
            # (otherwise their 40% share alone rarely crosses the entry gate).
            dreamer_is_idle = (signal == "HOLD" or effective_confidence < 10)
            if dreamer_is_idle:
                w_dreamer, w_agents = 0.0, 1.0
            else:
                w_dreamer, w_agents = 0.60, 0.40

            dreamer_numeric = (1 if signal == "BUY" else -1 if signal == "SELL" else 0) * effective_confidence
            agent_numeric   = (1 if c_signal == "BUY" else -1 if c_signal == "SELL" else 0) * c_conf
            blended_numeric = dreamer_numeric * w_dreamer + agent_numeric * w_agents

            # Lowered gate from ±25 to ±15 to match collaborator's consensus threshold.
            if abs(blended_numeric) >= 15:
                blended_signal = "BUY" if blended_numeric > 0 else "SELL"
                blended_conf   = int(min(98, max(30, abs(blended_numeric))))
            else:
                blended_signal = "HOLD"
                blended_conf   = max(10, int(50 - abs(blended_numeric)))

            # Hard override: if agents STRONGLY disagree with DreamerV3 → HOLD
            if signal != "HOLD" and c_signal not in (signal, "HOLD") and c_conf > 60:
                blended_signal = "HOLD"
                blended_conf   = 30
                agent_override = True
                logger.info(
                    "[DreamerV3] Agent override: DV3=%s but agents=%s (conf=%.0f) → HOLD",
                    signal, c_signal, c_conf,
                )

            agent_consensus = {
                "agent_signal":    c_signal,
                "agent_conf":      round(c_conf, 1),
                "agent_score":     round(c_score, 1),
                "blended_signal":  blended_signal,
                "blended_conf":    blended_conf,
                "agent_override":  agent_override,
                "dreamer_only":    False,
            }
            logger.info(
                "[DreamerV3] Blended: DV3=%s(%d) + Agents=%s(%.0f) → %s(%d)",
                signal, effective_confidence, c_signal, c_conf,
                blended_signal, blended_conf,
            )
        else:
            # No collaborative analysis yet — run fast background analysis
            agent_consensus = {"dreamer_only": True}

        return {
            "signal":          blended_signal,
            "confidence":      blended_conf,
            "direction":       1 if blended_signal == "BUY" else (-1 if blended_signal == "SELL" else 0),
            "quantity":        quantity,
            "entry_price":     round(price, 2),
            "sl_price":        round(sl_price, 2),
            "tp_price":        round(tp_price, 2),
            "position_value":  round(position_value, 2),
            "dreamer_active":  True,
            "dreamer_status":  dreamer_status,
            "dreamer_raw_signal": signal,
            "dreamer_raw_conf":   effective_confidence,
            "wm_loss":         round(wm_loss, 6),
            "strategy_weights": weights,
            "market_context":  ctx,
            "regime":          regime,
            "rsi14":           rsi14,
            "risk_profile":    risk.to_dict(),
            "daily_target":    prefs.daily_profit_target,
            "daily_pnl":       daily_pnl,
            "target_gap":      round(target_gap, 2),
            "behind_boost":    round(behind_boost, 3),
            "agent_consensus": agent_consensus,
            "message": (
                f"Blended: DV3={signal}({effective_confidence}%) + "
                f"Agents={agent_consensus.get('agent_signal','—')}({agent_consensus.get('agent_conf',0):.0f}%) "
                f"→ {blended_signal}({blended_conf}%) | {regime} | RSI {rsi14:.0f}"
            ),
            "timestamp":       datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        logger.exception("Dreamer decision error")
        return {
            "signal":      "HOLD",
            "confidence":  0,
            "direction":   0,
            "quantity":    0,
            "entry_price": 0.0,
            "sl_price":    0.0,
            "tp_price":    0.0,
            "position_value": 0.0,
            "dreamer_active": False,
            "error":       str(exc),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }


# ─── Paper Trading Engine ─────────────────────────────────────────────────────

def _check_open_position(decision: Dict, current_price: float, prefs: UserPreferences) -> Optional[str]:
    """
    Check if open paper position should be closed:
      - SL hit → "SL"
      - TP hit → "TP"
      - Circuit breaker → "CIRCUIT_BREAKER"
      - Signal reversal → "SIGNAL_REVERSAL"
    """
    with _lock:
        open_trade_dict = _state.get("open_trade")
    if not open_trade_dict:
        return None

    direction = open_trade_dict.get("direction", "")
    sl_price  = open_trade_dict.get("sl_price", 0)
    tp_price  = open_trade_dict.get("tp_price", 0)
    status    = open_trade_dict.get("status", "CLOSED")
    if status != "OPEN":
        return None

    if direction == "BUY":
        if current_price <= sl_price:
            return "SL"
        if current_price >= tp_price:
            return "TP"
    elif direction == "SELL":
        if current_price >= sl_price:
            return "SL"
        if current_price <= tp_price:
            return "TP"

    # Signal reversal check
    new_sig = decision.get("signal", "HOLD")
    if direction == "BUY" and new_sig == "SELL":
        return "SIGNAL_REVERSAL"
    if direction == "SELL" and new_sig == "BUY":
        return "SIGNAL_REVERSAL"

    return None


def _open_paper_trade(decision: Dict, prefs: UserPreferences, risk: RiskProfile) -> Optional[Dict]:
    """Open a new paper trade based on DreamerV3 decision."""
    signal = decision.get("signal", "HOLD")
    if signal == "HOLD":
        return None

    with _lock:
        daily_trades  = _state.get("daily_trades", 0)
        circuit       = _state.get("circuit_breaker", False)
        existing_open = _state.get("open_trade")

    if circuit:
        return None
    if daily_trades >= risk.max_trades_per_day:
        return None
    if existing_open and existing_open.get("status") == "OPEN":
        return None
    if decision.get("confidence", 0) < 30:
        return None  # minimum confidence threshold

    trade = PaperTrade(
        trade_id            = str(uuid4())[:8].upper(),
        ticker              = prefs.ticker,
        direction           = signal,
        entry_price         = decision.get("entry_price", 0.0),
        quantity            = decision.get("quantity", 1),
        position_value      = decision.get("position_value", 0.0),
        sl_price            = decision.get("sl_price", 0.0),
        tp_price            = decision.get("tp_price", 0.0),
        confidence          = decision.get("confidence", 0),
        dreamer_signal      = decision.get("direction", 0.0),
        strategy_weights    = decision.get("strategy_weights", {}),
        risk_profile_snapshot = risk.to_dict(),
        daily_target        = prefs.daily_profit_target,
        allocated_capital   = prefs.allocated_capital,
    )
    return trade.to_dict()


def _close_paper_trade(trade_dict: Dict, exit_price: float, reason: str) -> Dict:
    """Close an open paper trade and compute P&L."""
    direction = trade_dict.get("direction", "BUY")
    qty       = trade_dict.get("quantity", 1)
    entry     = trade_dict.get("entry_price", exit_price)
    pos_val   = trade_dict.get("position_value", exit_price * qty)

    if direction == "BUY":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty

    pnl_pct = (pnl / max(pos_val, 1e-8)) * 100

    closed = dict(trade_dict)
    closed.update({
        "exit_price":  round(exit_price, 2),
        "exit_time":   datetime.now(timezone.utc).isoformat(),
        "exit_reason": reason,
        "pnl":         round(pnl, 2),
        "pnl_pct":     round(pnl_pct, 3),
        "status":      "CLOSED",
    })
    return closed


# ─── Circuit Breaker Checks ───────────────────────────────────────────────────

def _check_circuit_breakers(prefs: UserPreferences, risk: RiskProfile) -> Tuple[bool, Optional[str]]:
    """Returns (tripped, reason) — if tripped, auto mode should pause."""
    with _lock:
        daily_pnl     = _state.get("daily_pnl", 0.0)
        _daily_dd     = _state.get("daily_drawdown", 0.0)
        consec_losses = _state.get("consecutive_losses", 0)
        peak_cap      = _state.get("peak_capital", prefs.allocated_capital)
        cur_cap       = _state.get("current_capital", prefs.allocated_capital)

    # 1. Max daily loss
    if daily_pnl < -risk.max_daily_loss_inr:
        return True, f"Max daily loss hit: ₹{abs(daily_pnl):.0f} > limit ₹{risk.max_daily_loss_inr:.0f}"

    # 2. Account drawdown
    if peak_cap > 0:
        account_dd = (peak_cap - cur_cap) / peak_cap
        if account_dd >= ACCOUNT_DD_CIRCUIT:
            return True, f"Account drawdown {account_dd:.1%} hit circuit breaker ({ACCOUNT_DD_CIRCUIT:.0%} limit)"

    # 3. Consecutive losses
    if consec_losses >= CONSEC_LOSS_THRESHOLD + 2:  # extra cushion → hard stop
        return True, f"{consec_losses} consecutive losses — auto-paused for review"

    return False, None


# ─── Background Auto-Mode Worker ──────────────────────────────────────────────

def _robo_worker(prefs: UserPreferences):
    """
    Continuous background loop for auto mode:
      1. Fetch DreamerV3 decision every 60 seconds
      2. Check open position SL/TP
      3. Open new position if signal strong enough
      4. Update daily P&L and progress
      5. Check circuit breakers
    """
    _stop_evt.clear()
    _upd(
        status         = "scanning",
        auto_mode      = True,
        uptime_start   = datetime.now(timezone.utc).isoformat(),
        daily_pnl      = 0.0,
        daily_trades   = 0,
        daily_target_pct = 0.0,
        circuit_breaker = False,
        circuit_reason  = None,
        error           = None,
    )
    logger.info("[RoboOrchestrator] Auto-mode started | target=₹%.0f | capital=₹%.0f",
                prefs.daily_profit_target, prefs.allocated_capital)

    # Phase 2: full RPM recalculation on start
    risk_profile = _recalculate_risk_full(trigger="auto_start")
    risk = _recalculate_risk(prefs)   # backward-compat RiskProfile object
    _upd(risk_profile = risk_profile)

    iteration = 0

    while not _stop_evt.is_set():
        try:
            iteration += 1

            # ── 1. Get DreamerV3 decision ──
            decision = _get_dreamer_decision(prefs.ticker, prefs, risk)
            _upd(
                current_decision  = decision,
                dreamer_signal    = decision.get("direction", 0.0),
                dreamer_confidence = decision.get("confidence", 0),
                dreamer_weights   = decision.get("strategy_weights", {}),
                dreamer_wm_loss   = decision.get("wm_loss", 0.0),
                last_decision_time = datetime.now(timezone.utc).isoformat(),
            )

            # ── 2. Fetch live price ──
            live_price = _fetch_live_price(prefs.ticker)
            if live_price is None or live_price <= 0:
                live_price = decision.get("entry_price", 0.0)

            # ── 3. Check open position ──
            with _lock:
                open_trade = _state.get("open_trade")

            if open_trade and open_trade.get("status") == "OPEN":
                close_reason = _check_open_position(decision, live_price, prefs)
                if close_reason:
                    closed = _close_paper_trade(open_trade, live_price, close_reason)
                    pnl    = closed.get("pnl", 0.0)

                    with _lock:
                        _state["daily_pnl"]       += pnl
                        _state["total_paper_pnl"] += pnl
                        _state["total_trades"]    += 1
                        _state["current_capital"] += pnl
                        _state["peak_capital"]     = max(_state["peak_capital"], _state["current_capital"])

                        if pnl >= 0:
                            _state["win_trades"]         += 1
                            _state["consecutive_losses"]  = 0
                        else:
                            _state["loss_trades"]         += 1
                            _state["consecutive_losses"]  += 1

                        # Store in audit trail (ring buffer, last 100)
                        _state["audit_trail"] = ([closed] + _state["audit_trail"])[:100]
                        _state["open_trade"]   = None

                        dpnl    = _state["daily_pnl"]
                        dtarget = prefs.daily_profit_target
                        _state["daily_target_pct"] = (dpnl / dtarget * 100) if dtarget > 0 else 0.0

                    # ── Adaptive Learning: feed trade outcome to Robot 3.0 learner ──
                    try:
                        from .adaptive_learner import learner as _learner
                        with _lock:
                            disc = _state.get("agent_discussion") or {}
                        _learner.record_trade_outcome(
                            ticker        = prefs.ticker,
                            trade_signal  = open_trade.get("direction", "BUY"),
                            outcome       = "WIN" if pnl >= 0 else "LOSS",
                            agent_signals = disc.get("agent_agreement_map", {}),
                        )
                    except Exception as _le:
                        logger.debug("[Orchestrator] Learner feedback: %s", _le)

                    logger.info(
                        "[RoboOrchestrator] Trade CLOSED | %s | P&L: ₹%.2f | Reason: %s",
                        close_reason, pnl, close_reason
                    )

            # ── 4. Check circuit breakers ──
            tripped, reason = _check_circuit_breakers(prefs, risk)
            if tripped:
                _upd(
                    circuit_breaker = True,
                    circuit_reason  = reason,
                    status          = "circuit_breaker",
                    auto_mode       = False,
                )
                logger.warning("[RoboOrchestrator] CIRCUIT BREAKER: %s", reason)
                break

            # ── 5. Update daily drawdown ──
            with _lock:
                daily_pnl = _state["daily_pnl"]
                if daily_pnl < 0:
                    _state["daily_drawdown"] = abs(daily_pnl) / max(prefs.allocated_capital, 1.0)

            # ── 6. Open new trade if signal is strong ──
            with _lock:
                has_open = (_state.get("open_trade") and
                            _state["open_trade"].get("status") == "OPEN")

            if not has_open and decision.get("signal", "HOLD") != "HOLD":
                new_trade = _open_paper_trade(decision, prefs, risk)
                if new_trade:
                    with _lock:
                        _state["open_trade"]   = new_trade
                        _state["daily_trades"] += 1
                        _state["status"]        = "trading"
                    logger.info(
                        "[RoboOrchestrator] Trade OPENED | %s %s @ ₹%.2f | qty=%d | SL=₹%.2f | TP=₹%.2f",
                        new_trade["direction"], prefs.ticker,
                        new_trade["entry_price"], new_trade["quantity"],
                        new_trade["sl_price"],  new_trade["tp_price"],
                    )
                else:
                    _upd(status = "scanning")

            # ── 7. Recalculate risk every 10 iterations (market vol may change) ──
            if iteration % 10 == 0:
                with _lock:
                    daily_pnl_now  = _state.get("daily_pnl", 0.0)
                    daily_tr_now   = _state.get("daily_trades", 0)
                risk_profile = _recalculate_risk_full(
                    trigger      = "scheduled",
                    current_pnl  = daily_pnl_now,
                    trades_today = daily_tr_now,
                )
                risk = _recalculate_risk(prefs)  # keep backward-compat object
                _upd(risk_profile = risk_profile)

                # Update capital state vector for DreamerV3
                with _lock:
                    open_val = (_state.get("open_trade") or {}).get("position_value", 0.0)
                cap_state = _rpm.get_capital_state_vector(
                    current_pnl         = daily_pnl_now,
                    trades_today        = daily_tr_now,
                    open_position_value = open_val,
                )
                _upd(capital_state_vector = cap_state)

                # Check dynamic budget — stop if exhausted
                budget = _rpm.last_risk_budget
                if budget and budget.should_stop_trading:
                    _upd(
                        circuit_breaker = True,
                        circuit_reason  = f"[DynamicBudget] {budget.stop_reason}",
                        status          = "circuit_breaker",
                        auto_mode       = False,
                    )
                    logger.warning("[Orchestrator] Dynamic budget stop: %s", budget.stop_reason)
                    break

            # ── Sleep 60 seconds between iterations ──
            for _ in range(60):
                if _stop_evt.is_set():
                    break
                time.sleep(1)

        except Exception as exc:
            logger.exception("[RoboOrchestrator] Worker error")
            _upd(error = str(exc))
            time.sleep(30)  # back off on error

    _upd(status="paused", auto_mode=False)
    logger.info("[RoboOrchestrator] Auto-mode stopped.")


# ─── Public API ───────────────────────────────────────────────────────────────

def update_user_preferences(
    daily_profit_target: Optional[float] = None,
    allocated_capital: Optional[float]   = None,
    ticker: Optional[str]                = None,
    risk_tolerance: Optional[str]        = None,
    watchlist: Optional[List]            = None,
    max_parallel_trades: Optional[int]   = None,
) -> Dict:
    """
    Update user-defined settings and immediately recalculate risk profile.
    Phase 2: uses RPM for full Kelly + VaR + Feasibility recalculation.
    Can be called at any time (even during active auto mode).
    """
    global _prefs

    # Update prefs object
    if daily_profit_target is not None:
        _prefs.daily_profit_target = max(0.0, float(daily_profit_target))
    if allocated_capital is not None:
        _prefs.allocated_capital   = max(1000.0, float(allocated_capital))
    if ticker is not None:
        _prefs.ticker = str(ticker)
    if risk_tolerance is not None:
        _prefs.risk_tolerance = str(risk_tolerance)
    if watchlist is not None:
        _prefs.watchlist = [str(t).strip().upper() for t in watchlist]
    if max_parallel_trades is not None:
        _prefs.max_parallel_trades = max(1, min(5, int(max_parallel_trades)))
        # Sync execution engine max positions
        try:
            from .execution_engine import engine
            engine.set_max_positions(_prefs.max_parallel_trades)
        except Exception:
            pass

    # Phase 2: full RPM recalculation (fetches live market data for ATR)
    risk_profile = _recalculate_risk_full(trigger="user_update")

    _upd(
        daily_profit_target = _prefs.daily_profit_target,
        allocated_capital   = _prefs.allocated_capital,
        ticker              = _prefs.ticker,
        risk_tolerance      = _prefs.risk_tolerance,
        watchlist           = _prefs.watchlist,
        max_parallel_trades = _prefs.max_parallel_trades,
        risk_profile        = risk_profile,
        current_capital     = _prefs.allocated_capital,
        peak_capital        = _prefs.allocated_capital,
    )

    # Get simplified feasibility for the message
    feasibility_label = risk_profile.get("feasibility_label", "Unknown")
    req_ret   = risk_profile.get("required_daily_return_pct", 0)
    pos_size  = risk_profile.get("position_size_inr", 0)
    warnings  = risk_profile.get("feasibility_warnings", [])
    var95     = risk_profile.get("var_95_inr", 0)

    logger.info(
        "[Orchestrator] Prefs updated → target=₹%.0f | capital=₹%.0f | %s",
        _prefs.daily_profit_target, _prefs.allocated_capital, feasibility_label,
    )

    return {
        "success":       True,
        "preferences":   _prefs.to_dict(),
        "risk_profile":  risk_profile,
        "market_context": _rpm.last_market_ctx,
        "capital_state": _rpm.get_capital_state_vector(
            current_pnl         = _state.get("daily_pnl", 0.0),
            trades_today        = _state.get("daily_trades", 0),
            open_position_value = _state.get("open_trade", {}).get("position_value", 0.0) if _state.get("open_trade") else 0.0,
        ),
        "warnings": warnings,
        "message": (
            f"Settings updated. {feasibility_label} | "
            f"Daily return needed: {req_ret:.2f}% | "
            f"Position size: ₹{pos_size:,.0f} | "
            f"VaR 95%: ₹{var95:,.0f}"
        ),
    }


def start_auto_mode(
    ticker: Optional[str] = None,
    interval_minutes: int = 5,
) -> Dict:
    """
    Phase 3: Start autonomous trading loop via TradingLoop (APScheduler).
    Falls back to legacy thread-based worker if TradingLoop unavailable.

    Args:
        ticker:           Override ticker for this session.
        interval_minutes: Scan interval (1–30 min). Default 5.
    """
    global _robo_thread, _prefs

    if _state.get("auto_mode") and _state.get("status") not in ("paused", "idle", "circuit_breaker"):
        return {"success": False, "error": "Auto mode already running"}

    if ticker:
        _prefs.ticker = ticker

    # Sync execution engine mode with current orchestrator mode
    try:
        from .execution_engine import engine
        engine.set_mode(_state.get("mode", "paper"))
    except Exception as _e:
        logger.warning("[Orchestrator] ExecutionEngine sync failed: %s", _e)

    # Reset daily counters
    _upd(
        daily_pnl        = 0.0,
        daily_trades     = 0,
        daily_target_pct = 0.0,
        daily_drawdown   = 0.0,
        circuit_breaker  = False,
        circuit_reason   = None,
        error            = None,
        open_trade       = None,
        auto_mode        = True,
        uptime_start     = datetime.now(timezone.utc).isoformat(),
    )

    # Phase 3: Try TradingLoop first
    try:
        from .trading_loop import loop as _loop
        result = _loop.start(interval_minutes=interval_minutes)
        if result.get("success"):
            _upd(status="scanning")
            logger.info(
                "[Orchestrator] TradingLoop started | interval=%dmin | ticker=%s",
                interval_minutes, _prefs.ticker,
            )
            return {
                "success":          True,
                "interval_minutes": interval_minutes,
                "message": (
                    f"Auto mode started | Mode: {_state.get('mode','paper').upper()} | "
                    f"Scanning every {interval_minutes} min | "
                    f"Target: ₹{_prefs.daily_profit_target:.0f} | "
                    f"Capital: ₹{_prefs.allocated_capital:,.0f}"
                ),
                "disclaimer": (
                    "⚠️  PAPER TRADING — No real orders. No guaranteed returns."
                    if _state.get("mode") == "paper" else
                    "🔴 LIVE MODE ACTIVE — Real orders will be placed. Capital at risk."
                    if _state.get("mode") == "live" else
                    "👁  SHADOW MODE — Observing only. No orders placed."
                ),
            }
    except Exception as _loop_err:
        logger.warning(
            "[Orchestrator] TradingLoop failed (%s) — falling back to legacy worker", _loop_err
        )

    # Fallback: legacy thread-based worker
    _stop_evt.clear()
    _robo_thread = threading.Thread(
        target   = _robo_worker,
        args     = (_prefs,),
        daemon   = True,
        name     = "robo-orchestrator",
    )
    _robo_thread.start()

    return {
        "success": True,
        "message": (
            f"Auto mode started (PAPER/legacy) | Target: ₹{_prefs.daily_profit_target:.0f} | "
            f"Capital: ₹{_prefs.allocated_capital:,.0f}"
        ),
        "disclaimer": "PAPER TRADING ONLY. No real capital at risk. No guaranteed returns.",
    }


def stop_auto_mode() -> Dict:
    """Stop auto mode — stops TradingLoop if running, else legacy thread."""
    # Stop TradingLoop
    try:
        from .trading_loop import loop as _loop
        if _loop._running:
            _loop.stop()
    except Exception as _e:
        logger.debug("[Orchestrator] TradingLoop stop: %s", _e)

    # Stop legacy thread
    _stop_evt.set()
    _upd(auto_mode=False, status="paused")
    return {"success": True, "message": "Auto mode stopped"}


def reset_daily() -> Dict:
    """Reset daily P&L counters (call at market open each day)."""
    _upd(
        daily_pnl        = 0.0,
        daily_trades     = 0,
        daily_target_pct = 0.0,
        daily_drawdown   = 0.0,
        circuit_breaker  = False,
        circuit_reason   = None,
        open_trade       = None,
    )
    return {"success": True, "message": "Daily counters reset"}


def get_audit_trail(limit: int = 50) -> List[Dict]:
    with _lock:
        return list(_state.get("audit_trail", []))[:limit]


def get_latest_decision() -> Dict:
    with _lock:
        return dict(_state.get("current_decision") or {
            "signal": "HOLD",
            "confidence": 0,
            "message": "No decision yet — start auto mode.",
        })


# ─── Async DB Persistence (optional – called from endpoints) ──────────────────

async def save_preferences_to_db() -> None:
    """Persist current user preferences + risk profile to MongoDB."""
    try:
        db = _get_db()
        doc = {
            "_id":             "default",
            "preferences":     _prefs.to_dict(),
            "risk_profile":    _state.get("risk_profile", {}),
            "updated_at":      datetime.now(timezone.utc).isoformat(),
        }
        await db["robo_user_preferences"].replace_one(
            {"_id": "default"}, doc, upsert=True
        )
    except Exception as exc:
        logger.warning("Could not save preferences to DB: %s", exc)


async def load_preferences_from_db() -> None:
    """Load last-saved preferences from MongoDB on startup."""
    global _prefs
    try:
        db  = _get_db()
        doc = await db["robo_user_preferences"].find_one({"_id": "default"})
        if doc and "preferences" in doc:
            _prefs = UserPreferences.from_dict(doc["preferences"])
            risk   = _recalculate_risk(_prefs)
            _upd(
                daily_profit_target = _prefs.daily_profit_target,
                allocated_capital   = _prefs.allocated_capital,
                ticker              = _prefs.ticker,
                risk_tolerance      = _prefs.risk_tolerance,
                risk_profile        = risk.to_dict(),
                current_capital     = _prefs.allocated_capital,
                peak_capital        = _prefs.allocated_capital,
            )
            logger.info(
                "[RoboOrchestrator] Preferences loaded from DB: target=₹%.0f capital=₹%.0f",
                _prefs.daily_profit_target, _prefs.allocated_capital,
            )
    except Exception as exc:
        logger.warning("Could not load preferences from DB: %s", exc)


async def log_trade_to_db(trade: Dict) -> None:
    """Append closed paper trade to MongoDB audit collection."""
    try:
        db = _get_db()
        await db["robo_paper_trades"].insert_one(trade)
    except Exception as exc:
        logger.debug("Trade log DB error: %s", exc)
