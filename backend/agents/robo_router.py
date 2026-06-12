"""
FastAPI Router — Dreamer V3 Robo-Trader (Phase 3)
==================================================
Endpoints:
  GET  /api/robo/settings          — fetch current user settings + full RPM risk profile
  POST /api/robo/settings          — update daily_target / allocated_capital (auto-recalculates)
  POST /api/robo/recalculate       — force full recalculation with live market data + audit log
  GET  /api/robo/status            — full robo state (P&L, progress, decision, capital state)
  POST /api/robo/start             — start autonomous trading loop (APScheduler)
  POST /api/robo/stop              — stop auto mode
  POST /api/robo/reset-daily       — reset daily P&L counters
  GET  /api/robo/decision          — latest DreamerV3 decision with RPM-sized position
  GET  /api/robo/audit             — paper trade audit trail (closed trades)
  POST /api/robo/risk-preview      — what-if calculator (no save)
  GET  /api/robo/risk-report       — full RPM report: Kelly + VaR + Feasibility + Budget
  GET  /api/robo/recalc-history    — last N recalculation audit records from MongoDB
  GET  /api/robo/capital-state     — current DreamerV3 capital state vector

  ── Phase 3 ──────────────────────────────────────────────────────────────────
  POST /api/robo/mode              — switch execution mode: paper | live | shadow
  GET  /api/robo/positions         — currently open positions (all modes)
  GET  /api/robo/orders            — order history (last N closed orders)
  GET  /api/robo/loop-status       — TradingLoop scheduler state
  POST /api/robo/set-interval      — change scan interval (1–30 min)
  POST /api/robo/cancel-pending    — cancel PENDING live order before confirmation delay
  POST /api/robo/close-all         — emergency: close all open positions

DISCLAIMER: PAPER TRADING ONLY by default. No guaranteed returns.
"""

from __future__ import annotations

import asyncio
import logging
import logging
import math
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

log = logging.getLogger("robo_router")

from . import dreamer_robo_orchestrator as orch
from .risk_portfolio_manager import (
    rpm,
    check_feasibility,
    compute_position_size,
    compute_var_cvar,
    compute_dynamic_risk_budget,
    get_volatility_regime,
)

logger = logging.getLogger(__name__)

robo_router = APIRouter(prefix="/api/robo", tags=["Robo Trader — Phase 2"])

DISCLAIMER = (
    "⚠️  PAPER TRADING / RESEARCH ONLY. No guaranteed returns. "
    "Past performance ≠ future results. Consult a SEBI-registered advisor."
)


def _get_robo_db():
    """Get Motor DB handle for robo_orders — fresh client each time to avoid loop issues."""
    url  = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    name = os.environ.get("DB_NAME", "trading_db")
    return AsyncIOMotorClient(url)[name]


# ════════════════════════════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ════════════════════════════════════════════════════════════════════════════════

class SettingsRequest(BaseModel):
    daily_profit_target: Optional[float] = Field(
        None, gt=0, description="Daily profit target in ₹ (e.g. 500, 2000)"
    )
    allocated_capital: Optional[float] = Field(
        None, gt=1000, description="Allocated trading capital in ₹"
    )
    ticker: Optional[str] = Field(None, description="NSE/BSE ticker (e.g. RELIANCE.NS)")
    risk_tolerance: Optional[str] = Field(
        None, description="conservative | moderate | aggressive | danger"
    )
    mode: Optional[str] = Field(
        None, description="paper | live  (live applies 30% extra safety multiplier)"
    )
    watchlist: Optional[list] = Field(
        None, description="List of NSE/BSE tickers to scan in parallel (max 10)"
    )
    max_parallel_trades: Optional[int] = Field(
        None, ge=1, le=5, description="Maximum concurrent open positions (1–5)"
    )


class StartRequest(BaseModel):
    ticker:           Optional[str] = None
    interval_minutes: int           = 5    # scan interval: 1–30 min


class ModeRequest(BaseModel):
    mode: str = Field(..., description="paper | live | shadow")


class SetIntervalRequest(BaseModel):
    interval_minutes: int = Field(..., ge=1, le=30, description="Scan interval in minutes")


class CancelPendingRequest(BaseModel):
    order_id: str = Field(..., description="Order ID to cancel (PENDING only)")


class ManualTickerRequest(BaseModel):
    ticker: str = Field(..., description="NSE/BSE ticker (e.g. RELIANCE.NS)")


class ScanNowRequest(BaseModel):
    mode:     str   = Field("hybrid", description="auto | manual | hybrid")
    deep:     bool  = Field(False,    description="Use LangGraph deep analysis")
    ticker:   Optional[str] = Field(None, description="Single ticker to analyse (overrides mode)")


class RiskPreviewRequest(BaseModel):
    """What-if calculator — preview risk profile without saving."""
    daily_profit_target: float = Field(..., gt=0,    description="Daily target in ₹")
    allocated_capital:   float = Field(..., gt=1000, description="Capital in ₹")
    risk_tolerance:      str   = Field("moderate",  description="conservative|moderate|aggressive|danger")
    ticker:              str   = Field("RELIANCE.NS", description="NSE ticker for live ATR fetch")


class RecalculateRequest(BaseModel):
    trigger: str = Field("force", description="Label for audit trail (e.g. user_manual, scheduled)")


# ════════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════════

# ─── 1. GET /settings ─────────────────────────────────────────────────────────
@robo_router.get("/settings")
async def get_settings():
    """
    Get current user preferences and the latest computed RPM risk profile.

    Response includes:
      • preferences (daily_target, capital, tolerance, mode, ticker)
      • risk_profile (position sizing, VaR, feasibility, dynamic budget)
      • capital_state_vector (normalised values for DreamerV3)
    """
    state = orch.get_robo_state()

    # Use RPM's cached full profile if available, else fall back to state
    risk_profile = state.get("risk_profile", {})
    if not risk_profile.get("kelly_fraction"):
        # First call or stale: run a quick recalculation
        try:
            risk_profile = orch._recalculate_risk_full(trigger="on_demand")
            orch._upd(risk_profile=risk_profile)
        except Exception as e:
            logger.warning("[robo_router] On-demand recalc failed: %s", e)

    cap_state = rpm.get_capital_state_vector(
        current_pnl         = state.get("daily_pnl", 0.0),
        trades_today        = state.get("daily_trades", 0),
        open_position_value = (state.get("open_trade") or {}).get("position_value", 0.0),
    )
    return {
        "success": True,
        "preferences": {
            "daily_profit_target": state.get("daily_profit_target", orch.DEFAULT_DAILY_TARGET),
            "allocated_capital":   state.get("allocated_capital",   orch.DEFAULT_CAPITAL),
            "ticker":              state.get("ticker",              orch.DEFAULT_TICKER),
            "risk_tolerance":      state.get("risk_tolerance",      "moderate"),
            "mode":                state.get("mode",                "paper"),
            "auto_mode":           state.get("auto_mode",           False),
            "watchlist":           state.get("watchlist",           []),
            "max_parallel_trades": state.get("max_parallel_trades", 3),
        },
        "risk_profile":        risk_profile,
        "capital_state_vector": cap_state,
        "rpm_settings":        rpm.to_settings_dict(),
        "disclaimer":          DISCLAIMER,
    }


# ─── 2. POST /settings ────────────────────────────────────────────────────────
@robo_router.post("/settings")
async def update_settings(req: SettingsRequest, bg: BackgroundTasks):
    """
    Update daily_target and/or allocated_capital.

    Immediately triggers full RPM recalculation:
      • Kelly + ATR + vol-regime position sizing
      • Parametric VaR / CVaR (95% + 99%)
      • 6-tier feasibility check with warnings
      • Dynamic risk budget state
      • Portfolio heat check

    Safe to call at any time — even while auto mode is running.
    Changes take effect on the next trade decision.
    """
    result = orch.update_user_preferences(
        daily_profit_target = req.daily_profit_target,
        allocated_capital   = req.allocated_capital,
        ticker              = req.ticker,
        risk_tolerance      = req.risk_tolerance,
        watchlist           = req.watchlist,
        max_parallel_trades = req.max_parallel_trades,
    )
    # Persist settings + audit record in background
    bg.add_task(orch.save_preferences_to_db)
    bg.add_task(rpm.save_settings_to_db)

    # Attach capital state vector to response
    result["capital_state_vector"] = rpm.get_capital_state_vector(
        current_pnl=0.0, trades_today=0, open_position_value=0.0
    )
    result["disclaimer"] = DISCLAIMER
    return result


# ─── 3. POST /recalculate ─────────────────────────────────────────────────────
@robo_router.post("/recalculate")
async def force_recalculate(req: RecalculateRequest, bg: BackgroundTasks):
    """
    Force a full risk recalculation with fresh live market data.

    Use this when:
      • Market has moved significantly
      • Volatility regime has changed (earnings, RBI policy, etc.)
      • You want to see updated VaR after a position change
      • Before starting a new trading session

    Returns the complete RPM risk profile + audit record ID.
    Recalculation audit is logged to MongoDB asynchronously.
    """
    state = orch.get_robo_state()

    risk_profile = orch._recalculate_risk_full(
        trigger      = req.trigger,
        current_pnl  = state.get("daily_pnl", 0.0),
        trades_today = state.get("daily_trades", 0),
    )

    # Update state
    orch._upd(risk_profile=risk_profile)

    # Background: persist audit to DB
    bg.add_task(rpm.save_settings_to_db)

    cap_state = rpm.get_capital_state_vector(
        current_pnl         = state.get("daily_pnl", 0.0),
        trades_today        = state.get("daily_trades", 0),
        open_position_value = (state.get("open_trade") or {}).get("position_value", 0.0),
    )

    return {
        "success":             True,
        "risk_profile":        risk_profile,
        "capital_state_vector": cap_state,
        "market_context":      rpm.last_market_ctx,
        "audit_id":            rpm.last_audit_id,
        "computation_ms":      risk_profile.get("computation_ms"),
        "warnings":            risk_profile.get("warnings", []),
        "feasibility_warnings": risk_profile.get("feasibility_warnings", []),
        "disclaimer":          DISCLAIMER,
    }


# ─── 4. GET /status ───────────────────────────────────────────────────────────
@robo_router.get("/status")
async def get_status():
    """
    Full robo state: daily P&L progress, open trade, DreamerV3 decision,
    capital state vector, portfolio heat, and risk budget state.
    Also includes Hybrid Brain active status (brain_active, brain_action, brain_fear).
    """
    state = orch.get_robo_state()
    s = dict(state)
    s["audit_trail"] = []   # use /audit endpoint for trades

    # Attach live RPM metrics
    s["portfolio_heat_pct"]  = round(rpm.get_portfolio_heat(state.get("allocated_capital")) * 100, 3)
    s["heat_exceeded"]       = rpm.is_heat_exceeded()
    s["capital_state_vector"] = rpm.get_capital_state_vector(
        current_pnl         = state.get("daily_pnl", 0.0),
        trades_today        = state.get("daily_trades", 0),
        open_position_value = (state.get("open_trade") or {}).get("position_value", 0.0),
    )
    if rpm.last_risk_budget:
        s["risk_budget"] = asdict(rpm.last_risk_budget)

    # Attach Hybrid Brain status (set by trading loop each cycle)
    s["brain_active"]     = state.get("brain_active", False)
    s["brain_action"]     = state.get("brain_action", "HOLD")
    s["brain_confidence"] = state.get("brain_confidence", 0.0)
    s["brain_fear"]       = state.get("brain_fear", 0.0)
    s["brain_regime"]     = state.get("brain_regime", "")

    # Attach live DreamerV3 training stats
    try:
        from rl_agent.dreamer_trainer import get_state as _rl_state
        rs = _rl_state()
        s["live_training"] = {
            "exp_count":    rs.get("live_exp_count", 0),
            "train_steps":  rs.get("live_train_steps", 0),
            "wm_loss":      rs.get("live_wm_loss", 0.0),
            "tickers":      rs.get("live_tickers", []),
            "dreamer_status": rs.get("status", "idle"),
        }
    except Exception:
        s["live_training"] = {"exp_count": 0, "train_steps": 0, "wm_loss": 0.0, "tickers": [], "dreamer_status": "idle"}

    # Attach Robot 3.0 Layer Evolution state (all layers trained by live loop)
    try:
        from agents.layer_evolution import layer_evolution
        s["layer_evolution"] = layer_evolution.get_full_state()
    except Exception:
        s["layer_evolution"] = {"enabled": False}

    # Attach watchlist observations (per-ticker signal + trade plan)
    s["watchlist_observations"] = state.get("watchlist_observations", {})

    return {"success": True, **s}


# ─── 5. POST /start ───────────────────────────────────────────────────────────
@robo_router.post("/start")
async def start_auto_mode(req: StartRequest):
    """
    Phase 3: Start the autonomous trading loop (APScheduler).

    Before starting, system:
      1. Runs full RPM recalculation with live market data
      2. Checks feasibility — warns if target is aggressive
      3. Resets daily P&L counters
      4. Starts APScheduler loop (scans every interval_minutes)
      5. First scan runs 5 seconds after start
      6. Activates Hybrid Super Brain (warmup + initial decision in background)

    interval_minutes: 1–30 min. Default 5 min.
    DISCLAIMER: Starts in Paper Trading mode unless mode was changed via /api/robo/mode
    """
    result = orch.start_auto_mode(
        ticker           = req.ticker,
        interval_minutes = req.interval_minutes,
    )

    # ── Activate Hybrid Brain in background ──────────────────────────────────
    if result.get("success"):
        import asyncio

        async def _warmup_brain():
            try:
                from .hybrid_super_brain import hybrid_brain
                from .dreamer_robo_orchestrator import _prefs, _upd
                ticker = req.ticker or _prefs.ticker or "NIFTY"
                symbol = ticker.replace(".NS", "").replace(".BO", "").upper()

                # Load survival state from DB
                await hybrid_brain.survival.load()

                # Fire initial decision (populates cache + audit)
                decision = await hybrid_brain.think_and_decide(
                    market_data={},
                    news="",
                    symbol=symbol,
                )
                # ── Immediately reflect in robo state ────────────────────────
                _upd(
                    brain_active=True,
                    brain_action=decision.get("action", "HOLD"),
                    brain_confidence=decision.get("confidence", 0.0),
                    brain_fear=decision.get("survival", {}).get("fear", 0.0),
                    brain_regime=decision.get("psych", {}).get("regime", ""),
                    brain_last_decision=decision,
                )
                log.info(
                    "[AutoStart] Brain activated → %s @ %.1f%% conf | fear=%.2f",
                    decision.get("action"), decision.get("confidence", 0),
                    decision.get("survival", {}).get("fear", 0),
                )
            except Exception as e:
                log.warning("[AutoStart] Brain warmup failed (non-fatal): %s", e)

        asyncio.create_task(_warmup_brain())
        result["brain_activated"] = True

    return result


# ─── 6. POST /stop ────────────────────────────────────────────────────────────
@robo_router.post("/stop")
async def stop_auto_mode():
    """Stop the autonomous trading loop."""
    result = orch.stop_auto_mode()
    # Mark brain as standby (still loaded, just not actively cycling)
    try:
        from .dreamer_robo_orchestrator import _upd
        _upd(brain_active=False)
    except Exception:
        pass
    return result


# ─── 7. POST /reset-daily ─────────────────────────────────────────────────────
@robo_router.post("/reset-daily")
async def reset_daily():
    """Reset daily P&L counters. Call at the start of each NSE trading session."""
    return orch.reset_daily()


# ─── 8. GET /decision ─────────────────────────────────────────────────────────
@robo_router.get("/decision")
async def get_latest_decision():
    """
    Latest DreamerV3 trade decision including RPM-sized position parameters.
    Includes capital state vector so frontend can display normalised metrics.
    """
    state = orch.get_robo_state()
    dec   = orch.get_latest_decision()
    return {
        "success":             True,
        "decision":            dec,
        "capital_state_vector": rpm.get_capital_state_vector(
            current_pnl         = state.get("daily_pnl", 0.0),
            trades_today        = state.get("daily_trades", 0),
            open_position_value = (state.get("open_trade") or {}).get("position_value", 0.0),
        ),
    }


# ─── 9. GET /audit ────────────────────────────────────────────────────────────
@robo_router.get("/audit")
async def get_audit_trail(limit: int = Query(50, ge=1, le=100), include_brain: bool = Query(True)):
    """Get paper trade audit trail (last N closed trades) with summary statistics.
    
    If include_brain=True (default), also fetches recent Hybrid Brain decisions and 
    merges them as annotated entries in the timeline.
    """
    trades    = orch.get_audit_trail(limit=limit)
    total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)
    wins      = sum(1 for t in trades if (t.get("pnl") or 0) >= 0)
    losses    = len(trades) - wins

    # Fetch and mix hybrid brain decisions
    brain_decisions = []
    if include_brain:
        try:
            from .hybrid_super_brain import hybrid_brain
            raw = await hybrid_brain.get_recent_audit(limit=limit)
            for d in raw:
                brain_decisions.append({
                    **d,
                    "_entry_type": "brain_decision",
                    "direction":   d.get("action", "HOLD"),
                    "ticker":      d.get("symbol", "—"),
                    "pnl":         None,
                    "exit_reason": f"Brain:{d.get('action','?')} conf={d.get('confidence','?')}",
                })
        except Exception:
            pass

    return {
        "success":         True,
        "trades":          trades,
        "brain_decisions": brain_decisions,
        "count":           len(trades),
        "brain_count":     len(brain_decisions),
        "total_pnl":       round(total_pnl, 2),
        "win_count":       wins,
        "loss_count":      losses,
        "win_rate":        round(wins / max(len(trades), 1) * 100, 1),
    }


# ─── 10. POST /risk-preview ───────────────────────────────────────────────────
@robo_router.post("/risk-preview")
async def risk_preview(req: RiskPreviewRequest):
    """
    What-if risk calculator — full RPM analysis for arbitrary settings without saving.

    Returns:
      • position_size (Kelly + ATR + vol-regime)
      • VaR / CVaR (95% + 99%)
      • Feasibility (6-tier with warnings and NSE historical context)
      • Dynamic risk budget preview
      • Volatility regime classification
    """
    import time
    t0 = time.perf_counter()

    # Temporarily update RPM settings without saving
    orig_target  = rpm.daily_target
    orig_capital = rpm.allocated_capital
    orig_tol     = rpm.risk_tolerance
    orig_ticker  = rpm.ticker

    rpm.update_settings(
        daily_target      = req.daily_profit_target,
        allocated_capital = req.allocated_capital,
        risk_tolerance    = req.risk_tolerance,
        ticker            = req.ticker,
    )

    risk_profile = rpm.full_recalculate(trigger="preview")

    # Restore original settings
    rpm.update_settings(
        daily_target      = orig_target,
        allocated_capital = orig_capital,
        risk_tolerance    = orig_tol,
        ticker            = orig_ticker,
    )

    comp_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "success":         True,
        "preview":         risk_profile,
        "market_context":  rpm.last_market_ctx,
        "computation_ms":  comp_ms,
        "disclaimer":      DISCLAIMER,
    }


# ─── 11. GET /risk-report ─────────────────────────────────────────────────────
@robo_router.get("/risk-report")
async def get_risk_report():
    """
    Full RPM risk report for the current settings.

    Sections:
      • position_sizing: Kelly fraction, ATR method, vol-regime, final size
      • var_cvar: parametric VaR and CVaR at 95% and 99%
      • feasibility: 6-tier assessment with historical NSE context and warnings
      • dynamic_budget: intra-day risk budget state
      • portfolio_heat: total deployed risk vs capital
      • capital_state: normalised DreamerV3 state vector
      • market_context: live price, ATR, regime, RSI
    """
    state = orch.get_robo_state()

    pos  = rpm.last_position_size
    var  = rpm.last_var_result
    feas = rpm.last_feasibility
    budg = rpm.last_risk_budget
    heat = rpm.get_portfolio_heat(state.get("allocated_capital"))

    return {
        "success": True,
        "settings": rpm.to_settings_dict(),
        "position_sizing": asdict(pos)  if pos  else {},
        "var_cvar":        asdict(var)  if var  else {},
        "feasibility":     asdict(feas) if feas else {},
        "dynamic_budget":  asdict(budg) if budg else {},
        "portfolio_heat": {
            "heat_pct":         round(heat * 100, 3),
            "max_heat_pct":     6.0,
            "exceeded":         heat > 0.06,
            "open_risk_count":  len(rpm._open_risks),
        },
        "capital_state_vector": rpm.get_capital_state_vector(
            current_pnl         = state.get("daily_pnl", 0.0),
            trades_today        = state.get("daily_trades", 0),
            open_position_value = (state.get("open_trade") or {}).get("position_value", 0.0),
        ),
        "market_context":   rpm.last_market_ctx,
        "last_recalculated": rpm.last_recalc_ts,
        "disclaimer":        DISCLAIMER,
    }


# ─── 12. GET /recalc-history ──────────────────────────────────────────────────
@robo_router.get("/recalc-history")
async def get_recalculation_history(limit: int = Query(10, ge=1, le=50)):
    """
    Fetch the last N recalculation audit records from MongoDB.
    Each record contains full inputs, outputs, timing, and warnings count.
    """
    records = await rpm.get_recalculation_history(limit=limit)
    return {
        "success": True,
        "count":   len(records),
        "records": records,
    }


# ─── 13. GET /capital-state ───────────────────────────────────────────────────
@robo_router.get("/capital-state")
async def get_capital_state():
    """
    Get the current normalised capital state vector for DreamerV3 world model.
    All values in [0,1] or [-1,1] — ready to concat with market observation.
    """
    state = orch.get_robo_state()
    vec   = rpm.get_capital_state_vector(
        current_pnl         = state.get("daily_pnl", 0.0),
        trades_today        = state.get("daily_trades", 0),
        open_position_value = (state.get("open_trade") or {}).get("position_value", 0.0),
    )
    return {
        "success":          True,
        "capital_state":    vec,
        "raw_values": {
            "daily_pnl":       round(state.get("daily_pnl", 0.0), 2),
            "daily_target":    rpm.daily_target,
            "allocated_capital": rpm.allocated_capital,
            "portfolio_heat_pct": round(rpm.get_portfolio_heat() * 100, 3),
            "trades_today":    state.get("daily_trades", 0),
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
# PHASE 3 ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════════

# ─── 14. POST /mode ───────────────────────────────────────────────────────────
@robo_router.post("/mode")
async def set_mode(req: ModeRequest):
    """
    Switch execution mode.

    Modes:
      paper  : Simulate trades in memory. No real orders. Default.
      live   : Real orders via Groww API. Requires GROWW_API_KEY + GROWW_API_SECRET.
               Applies 30% size reduction + 30-second confirmation delay.
      shadow : Observe-only. Logs decisions but never executes. Useful for monitoring.

    WARNING: Switching to LIVE mode will place REAL orders on Groww.
    Ensure GROWW_API_KEY and GROWW_API_SECRET are set in backend/.env.
    The system DOES NOT validate Groww API health before mode switch.
    """
    try:
        from .execution_engine import engine
        result = engine.set_mode(req.mode)
        if result.get("success"):
            # Sync orchestrator state
            orch._upd(mode=req.mode)
            # Sync RPM mode
            rpm.update_settings(mode=req.mode)
        return {**result, "disclaimer": DISCLAIMER}
    except Exception as exc:
        logger.exception("[robo_router] mode switch failed")
        return {"success": False, "error": str(exc)}


# ─── Live price cache (ticker → {price, ts}) refreshed at most every 15s ─────
_lp_cache: dict = {}

def _get_cached_live_prices(tickers: list) -> dict:
    """Fetch live prices for a list of tickers; returns {ticker: price}. 15s cache."""
    import time
    now = time.time()
    result = {}
    stale = [t for t in tickers if (now - _lp_cache.get(t, {}).get("ts", 0)) > 15]

    if stale:
        try:
            from .dreamer_robo_orchestrator import _fetch_live_price
            for t in stale:
                p = _fetch_live_price(t)
                if p:
                    _lp_cache[t] = {"price": p, "ts": now}
        except Exception:
            pass

    for t in tickers:
        result[t] = _lp_cache.get(t, {}).get("price")
    return result


# ─── 15. GET /positions ───────────────────────────────────────────────────────
@robo_router.get("/positions")
async def get_open_positions():
    """
    Currently open positions — merges in-memory + DB (OPEN status) for persistence across restarts.
    Enriches each position with live current_price, unrealized_pnl, pnl_pct.
    """
    try:
        from .execution_engine import engine
        stats = engine.get_daily_stats()
        mem_positions = stats["open_positions_list"]
        mem_ids = {p.get("order_id") for p in mem_positions}

        # Load DB open positions not already in memory (from previous session)
        try:
            db = _get_robo_db()
            cursor = db["robo_orders"].find({"status": "OPEN"}, {"_id": 0}).sort("entry_time", -1).limit(50)
            db_open = [doc async for doc in cursor]
            extra_open = [p for p in db_open if p.get("order_id") not in mem_ids]
        except Exception:
            extra_open = []

        all_positions = mem_positions + extra_open

        # Enrich with live price + unrealized P&L
        unique_tickers = list({p.get("ticker") for p in all_positions if p.get("ticker")})
        if unique_tickers:
            import asyncio
            live_prices = await asyncio.get_event_loop().run_in_executor(
                None, _get_cached_live_prices, unique_tickers
            )
            for pos in all_positions:
                ticker = pos.get("ticker")
                cp = live_prices.get(ticker) if ticker else None
                entry = pos.get("entry_price") or 0
                qty   = pos.get("quantity") or 0
                direction = pos.get("direction", "BUY")
                if cp and entry and qty:
                    pnl = (cp - entry) * qty if direction == "BUY" else (entry - cp) * qty
                    invested = entry * qty or 1
                    pos["current_price"]   = round(cp, 2)
                    pos["unrealized_pnl"]  = round(pnl, 2)
                    pos["pnl_pct"]         = round(pnl / invested * 100, 2)
                    pos["price_change"]    = round(cp - entry, 2)
                    pos["price_change_pct"]= round((cp - entry) / entry * 100, 2) if entry else 0

        return {
            "success":           True,
            "mode":              engine.mode,
            "open_positions":    all_positions,
            "pending_positions": stats["pending_list"],
            "shadow_signals":    stats["shadow_list"],
            "open_count":        stats["open_positions"] + len(extra_open),
            "pending_count":     stats["pending_positions"],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "open_positions": []}


# ─── 16. GET /orders ──────────────────────────────────────────────────────────
@robo_router.get("/orders")
async def get_order_history(limit: int = Query(50, ge=1, le=200)):
    """
    Full order history (all orders) — from MongoDB for persistence across restarts.
    Merges DB records with any in-memory orders not yet persisted.
    """
    try:
        from .execution_engine import engine
        # Primary: DB history via fresh Motor client
        try:
            db = _get_robo_db()
            cursor = db["robo_orders"].find({}, {"_id": 0}).sort("entry_time", -1).limit(limit)
            db_orders = [doc async for doc in cursor]
        except Exception as db_exc:
            logger.warning("[robo/orders] DB fetch failed: %s", db_exc)
            db_orders = []

        # Secondary: merge in-memory orders not yet in DB
        db_ids = {o.get("order_id") for o in db_orders}
        mem_orders = engine.get_open_positions() + engine.get_order_history(limit=limit)
        extra = [o for o in mem_orders if o.get("order_id") not in db_ids]

        history = (extra + db_orders)[:limit]
        stats   = engine.get_daily_stats()
        wins    = sum(1 for o in history if (o.get("pnl") or 0) > 0)
        losses  = sum(1 for o in history if (o.get("pnl") or 0) < 0)
        return {
            "success":         True,
            "mode":            engine.mode,
            "orders":          history,
            "count":           len(history),
            "daily_pnl":       round(stats["daily_pnl"], 2),
            "daily_net_pnl":   round(stats["daily_net_pnl"], 2),
            "daily_brokerage": round(stats["daily_brokerage"], 2),
            "wins":            wins,
            "losses":          losses,
            "win_rate":        round(wins / max(len(history), 1) * 100, 1),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "orders": []}


# ─── 17. GET /loop-status ─────────────────────────────────────────────────────
@robo_router.get("/loop-status")
async def get_loop_status():
    """
    TradingLoop APScheduler state: running, interval, last/next cycle times,
    last cycle result, market open status.
    """
    try:
        from .trading_loop import loop
        loop_state = loop.get_status()
        from .execution_engine import engine
        exec_stats = engine.get_daily_stats()
        return {
            "success":    True,
            "loop":       loop_state,
            "exec_stats": exec_stats,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ─── 18. POST /set-interval ───────────────────────────────────────────────────
@robo_router.post("/set-interval")
async def set_scan_interval(req: SetIntervalRequest):
    """
    Change the scan interval on the fly.
    If loop is running, it will be restarted with the new interval.
    Minimum 1 min, maximum 30 min.
    """
    try:
        from .trading_loop import loop
        result = loop.set_interval(req.interval_minutes)
        return {**result, "message": f"Scan interval set to {req.interval_minutes} min"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ─── 19. POST /cancel-pending ─────────────────────────────────────────────────
@robo_router.post("/cancel-pending")
async def cancel_pending_order(req: CancelPendingRequest):
    """
    Cancel a PENDING order before the live confirmation delay expires.
    Only applicable in LIVE mode — paper/shadow orders are never PENDING.
    """
    try:
        from .execution_engine import engine
        return engine.cancel_pending(req.order_id)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ─── 20. POST /close-all ──────────────────────────────────────────────────────
@robo_router.post("/close-all")
async def emergency_close_all():
    """
    Emergency: close ALL open positions immediately.
    Closes both in-memory positions AND any DB-persisted OPEN positions
    (e.g. from a previous server session).
    """
    try:
        from .execution_engine import engine
        from .dreamer_robo_orchestrator import _fetch_live_price, _prefs as prefs
        import asyncio
        from datetime import datetime, timezone

        # ── 1. Collect all DB OPEN positions to get tickers for live prices ──
        db = _get_robo_db()
        cursor = db["robo_orders"].find({"status": "OPEN"}, {"_id": 0})
        db_open = [doc async for doc in cursor]

        # ── 2. Build price map (fetch live prices for all unique tickers) ────
        unique_tickers = list({p.get("ticker") for p in db_open if p.get("ticker")})
        mem_tickers    = [p.get("ticker") for p in engine.get_open_positions() if p.get("ticker")]
        all_tickers    = list(set(unique_tickers + mem_tickers))

        prices: dict = {}
        if all_tickers:
            prices = await asyncio.get_event_loop().run_in_executor(
                None, _get_cached_live_prices, all_tickers
            )
        # fallback: use entry_price for any ticker with no live price
        for pos in db_open:
            t = pos.get("ticker")
            if t and t not in prices:
                prices[t] = pos.get("entry_price", 0.0)

        # ── 3. Close in-memory positions ─────────────────────────────────────
        closed = engine.close_all_positions(prices, reason="EMERGENCY_CLOSE")
        mem_closed_ids = {o.get("order_id") for o in closed}

        # ── 4. Close DB positions not already in memory ───────────────────────
        db_closed_count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for pos in db_open:
            oid = pos.get("order_id")
            if oid in mem_closed_ids:
                continue  # already handled by engine
            ticker     = pos.get("ticker", "")
            exit_price = round(prices.get(ticker, pos.get("entry_price", 0.0)), 2)
            entry      = pos.get("entry_price", 0.0) or exit_price
            qty        = pos.get("quantity", 0) or 0
            direction  = pos.get("direction", "BUY")
            pnl        = round((exit_price - entry) * qty if direction == "BUY" else (entry - exit_price) * qty, 2)
            await db["robo_orders"].update_one(
                {"order_id": oid},
                {"$set": {
                    "status":      "CLOSED",
                    "exit_price":  exit_price,
                    "exit_time":   now_iso,
                    "exit_reason": "EMERGENCY_CLOSE",
                    "pnl":         pnl,
                    "net_pnl":     pnl,
                }},
            )
            db_closed_count += 1

        total_closed = len(closed) + db_closed_count
        orch._upd(open_trade=None, status="paused")
        return {
            "success":      True,
            "closed_count": total_closed,
            "closed_orders": closed,
            "message":      f"Closed {total_closed} position(s) (mem={len(closed)}, db={db_closed_count})",
            "disclaimer":   DISCLAIMER,
        }
    except Exception as exc:
        logger.exception("[robo_router] emergency close-all failed")
        return {"success": False, "error": str(exc)}


# ─── 20b. POST /close-position/{order_id} ────────────────────────────────────
@robo_router.post("/close-position/{order_id}")
async def close_single_position(order_id: str):
    """
    Close a single position by order_id.
    Handles both in-memory positions and DB-only (previous-session) positions.
    """
    try:
        from .execution_engine import engine
        import asyncio
        from datetime import datetime, timezone

        # ── Try in-memory close first ────────────────────────────────────────
        mem_pos = next(
            (p for p in engine.get_open_positions() if p.get("order_id") == order_id),
            None
        )
        if mem_pos:
            ticker = mem_pos.get("ticker", "")
            live_prices = await asyncio.get_event_loop().run_in_executor(
                None, _get_cached_live_prices, [ticker]
            ) if ticker else {}
            exit_price = live_prices.get(ticker) or mem_pos.get("entry_price", 0.0)
            result = engine.close_position(order_id, exit_price, reason="MANUAL_CLOSE")
            if result.get("success"):
                return {"success": True, "order": result["order"], "source": "memory"}
            return {"success": False, "error": result.get("error", "Close failed")}

        # ── Fallback: close in DB if not in memory ────────────────────────────
        db = _get_robo_db()
        pos = await db["robo_orders"].find_one({"order_id": order_id, "status": "OPEN"}, {"_id": 0})
        if not pos:
            raise HTTPException(status_code=404, detail=f"Position {order_id} not found or already closed")

        ticker     = pos.get("ticker", "")
        live_prices = await asyncio.get_event_loop().run_in_executor(
            None, _get_cached_live_prices, [ticker]
        ) if ticker else {}
        exit_price = round(live_prices.get(ticker) or pos.get("entry_price", 0.0), 2)
        entry      = pos.get("entry_price", 0.0) or exit_price
        qty        = pos.get("quantity", 0) or 0
        direction  = pos.get("direction", "BUY")
        pnl        = round((exit_price - entry) * qty if direction == "BUY" else (entry - exit_price) * qty, 2)
        now_iso    = datetime.now(timezone.utc).isoformat()

        await db["robo_orders"].update_one(
            {"order_id": order_id},
            {"$set": {
                "status":      "CLOSED",
                "exit_price":  exit_price,
                "exit_time":   now_iso,
                "exit_reason": "MANUAL_CLOSE",
                "pnl":         pnl,
                "net_pnl":     pnl,
            }},
        )
        return {
            "success":     True,
            "order_id":    order_id,
            "exit_price":  exit_price,
            "pnl":         pnl,
            "source":      "db",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[robo_router] close-position failed for %s", order_id)
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════════
# MULTI-AGENT COLLABORATION ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════════

@robo_router.get("/agent-discussion")
async def get_agent_discussion():
    """
    GET latest agent discussion (cached result from last scan).
    Returns the full multi-agent discussion including per-agent signals,
    consensus, Monte Carlo scenarios, and Dreamer V3 final reasoning.
    """
    disc = orch.get_agent_discussion()
    top  = orch.get_top_picks()
    return {
        "success":          True,
        "discussion":       disc if disc else None,
        "top_picks":        top,
        "last_scan_time":   orch.get_robo_state().get("last_scan_time"),
        "scan_mode":        orch.get_robo_state().get("scan_mode", "auto"),
        "disclaimer":       DISCLAIMER,
    }


@robo_router.post("/scan-now")
async def scan_now(req: ScanNowRequest, bg: BackgroundTasks):
    """
    POST trigger an immediate collaborative scan.
    If `ticker` is provided: analyse that single ticker deeply.
    Otherwise: scan in `mode` (auto/manual/hybrid).
    """
    if req.ticker:
        # Single ticker detailed analysis
        def _run():
            orch._run_collaborative_analysis(
                ticker   = req.ticker,
                deep     = req.deep,
            )
        bg.add_task(_run)
        return {
            "success": True,
            "message": f"Deep analysis started for {req.ticker}",
            "mode":    "single_ticker",
            "deep":    req.deep,
        }
    else:
        # Multi-ticker scan
        def _scan():
            orch.trigger_scan(
                mode   = req.mode,
                deep   = req.deep,
            )
        bg.add_task(_scan)
        return {
            "success": True,
            "message": f"Collaborative scan triggered in '{req.mode}' mode",
            "mode":    req.mode,
            "deep":    req.deep,
        }


@robo_router.get("/manual-stocks")
async def get_manual_stocks():
    """GET list of user-added manual tickers."""
    tickers = orch.get_manual_tickers()
    return {
        "success":        True,
        "manual_tickers": tickers,
        "count":          len(tickers),
    }


@robo_router.post("/manual-stocks/add")
async def add_manual_stock(req: ManualTickerRequest):
    """POST add a ticker to the manual watchlist."""
    result = orch.add_manual_ticker(req.ticker)
    return {**result, "disclaimer": DISCLAIMER}


@robo_router.post("/manual-stocks/remove")
async def remove_manual_stock(req: ManualTickerRequest):
    """POST remove a ticker from the manual watchlist."""
    result = orch.remove_manual_ticker(req.ticker)
    return result


@robo_router.get("/scenarios/{ticker}")
async def get_scenarios(ticker: str, capital: float = Query(100000.0)):
    """
    GET Monte Carlo scenario analysis for a ticker using current agent signals.
    Runs 1000-path GBM simulation → target-hit probability, expected P&L.
    """
    try:
        from .strategy_collaborator import MonteCarloScenarioEngine, _download_ohlcv
        import yfinance as yf
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        def _run_mc():
            try:
                df  = _download_ohlcv(ticker, period="30d", interval="1d")
                if df.empty:
                    return {"error": "No price data"}
                c   = df["Close"].astype(float)
                h   = df["High"].astype(float)
                lo  = df["Low"].astype(float)
                import pandas as pd
                h_l  = h - lo
                h_pc = (h - c.shift(1)).abs()
                l_pc = (lo - c.shift(1)).abs()
                tr   = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
                atr  = float(tr.rolling(14).mean().iloc[-1])
                price = float(c.iloc[-1])
                entry  = price
                sl     = round(price - atr * 2.0, 2)
                target = round(price + atr * 3.0, 2)

                eng = MonteCarloScenarioEngine()
                return {
                    "ticker":  ticker,
                    "entry":   round(entry, 2),
                    "sl":      sl,
                    "target":  target,
                    "atr14":   round(atr, 2),
                    **eng.run(entry=entry, sl=sl, target=target, capital=capital, ticker=ticker),
                }
            except Exception as e:
                return {"error": str(e)}

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as ex:
            result = await loop.run_in_executor(ex, _run_mc)

        return {"success": True, "scenarios": result, "disclaimer": DISCLAIMER}

    except Exception as exc:
        logger.exception("[robo_router] scenarios failed")
        return {"success": False, "error": str(exc)}


@robo_router.get("/active-stocks")
async def get_active_stocks(limit: int = Query(25, ge=5, le=50)):
    """
    GET most-active stocks from NSE + F&O universe.
    Returns tickers ranked by volume × momentum score.
    """
    try:
        from .strategy_collaborator import collaborator
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as ex:
            stocks = await loop.run_in_executor(
                ex, lambda: collaborator.get_active_stocks(limit)
            )
        return {
            "success":    True,
            "count":      len(stocks),
            "stocks":     stocks,
            "disclaimer": DISCLAIMER,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ─── 21. GET /learning-state ──────────────────────────────────────────────────
@robo_router.get("/learning-state")
async def get_learning_state():
    """
    GET Robot 3.0 Adaptive Learning Engine state.

    Returns current dynamic agent weights (evolved from price validation
    and trade outcomes), accuracy scores per agent, Kronos teacher
    alignment, and recent weight changes.

    This is the 'brain improvement log' — shows how Robot 3.0 has
    self-optimised since the last reset.
    """
    try:
        from .adaptive_learner import learner
        state = learner.get_state()
        return {
            "success":   True,
            "learning":  state,
            "disclaimer": DISCLAIMER,
        }
    except Exception as exc:
        logger.exception("[robo_router] learning-state failed")
        return {"success": False, "error": str(exc)}


# ─── 22. POST /learning-reset ─────────────────────────────────────────────────
@robo_router.post("/learning-reset")
async def reset_learning():
    """
    POST reset all adaptive weights back to base (factory defaults).
    Use when you want Robot 3.0 to start fresh.
    """
    try:
        from .adaptive_learner import learner
        learner.reset_to_base()
        return {
            "success": True,
            "message": "Adaptive learning reset to base weights.",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}



# ─── 23. GET /watchlist ───────────────────────────────────────────────────────
@robo_router.get("/watchlist")
async def get_watchlist():
    """Get current multi-stock watchlist and parallel trade settings."""
    state = orch.get_robo_state()
    return {
        "success":           True,
        "watchlist":         state.get("watchlist", []),
        "max_parallel_trades": state.get("max_parallel_trades", 3),
        "primary_ticker":    state.get("ticker", orch.DEFAULT_TICKER),
    }


class WatchlistRequest(BaseModel):
    watchlist:           list = Field(..., description="List of NSE/BSE tickers")
    max_parallel_trades: int  = Field(3, ge=1, le=5, description="Max concurrent positions (1–5)")


# ─── 24. POST /watchlist ──────────────────────────────────────────────────────
@robo_router.post("/watchlist")
async def update_watchlist(req: WatchlistRequest, bg: BackgroundTasks):
    """
    Update multi-stock watchlist and max parallel positions.

    Example:
        {
            "watchlist": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
            "max_parallel_trades": 3
        }

    Robot 3.0 will scan all tickers each cycle and open up to
    max_parallel_trades simultaneous positions when conditions are met.
    """
    # Normalise tickers
    clean = []
    for t in req.watchlist[:10]:   # max 10 watchlist tickers
        t = str(t).strip().upper()
        if t and not t.endswith(".NS") and not t.endswith(".BO"):
            t = t + ".NS"
        if t:
            clean.append(t)

    result = orch.update_user_preferences(
        watchlist           = clean,
        max_parallel_trades = req.max_parallel_trades,
    )
    bg.add_task(orch.save_preferences_to_db)
    return {
        "success":             True,
        "watchlist":           clean,
        "max_parallel_trades": req.max_parallel_trades,
        "message":             f"Watchlist updated: {len(clean)} stocks | Max parallel: {req.max_parallel_trades}",
    }


# ─── 25. GET /watchlist/discover ─────────────────────────────────────────────

# ── NSE F&O + Liquid universe for auto-discover ──────────────────────────────
_DISCOVER_UNIVERSE = [
    # Core NIFTY 50
    {"ticker": "RELIANCE.NS",   "name": "Reliance Industries",  "sector": "Energy"},
    {"ticker": "TCS.NS",        "name": "TCS",                  "sector": "IT"},
    {"ticker": "HDFCBANK.NS",   "name": "HDFC Bank",            "sector": "Banking"},
    {"ticker": "ICICIBANK.NS",  "name": "ICICI Bank",           "sector": "Banking"},
    {"ticker": "INFY.NS",       "name": "Infosys",              "sector": "IT"},
    {"ticker": "BHARTIARTL.NS", "name": "Bharti Airtel",        "sector": "Telecom"},
    {"ticker": "BAJFINANCE.NS", "name": "Bajaj Finance",        "sector": "NBFC"},
    {"ticker": "LT.NS",         "name": "L&T",                  "sector": "Infra"},
    {"ticker": "AXISBANK.NS",   "name": "Axis Bank",            "sector": "Banking"},
    {"ticker": "HCLTECH.NS",    "name": "HCL Technologies",     "sector": "IT"},
    {"ticker": "MARUTI.NS",     "name": "Maruti Suzuki",        "sector": "Auto"},
    {"ticker": "SBIN.NS",       "name": "SBI",                  "sector": "Banking"},
    {"ticker": "TATAMOTORS.NS", "name": "Tata Motors",          "sector": "Auto"},
    {"ticker": "SUNPHARMA.NS",  "name": "Sun Pharma",           "sector": "Pharma"},
    {"ticker": "TITAN.NS",      "name": "Titan Company",        "sector": "Consumer"},
    {"ticker": "WIPRO.NS",      "name": "Wipro",                "sector": "IT"},
    {"ticker": "NTPC.NS",       "name": "NTPC",                 "sector": "Power"},
    {"ticker": "TATASTEEL.NS",  "name": "Tata Steel",           "sector": "Metals"},
    {"ticker": "ADANIENT.NS",   "name": "Adani Enterprises",    "sector": "Conglom."},
    {"ticker": "JSWSTEEL.NS",   "name": "JSW Steel",            "sector": "Metals"},
    {"ticker": "KOTAKBANK.NS",  "name": "Kotak Bank",           "sector": "Banking"},
    {"ticker": "ITC.NS",        "name": "ITC",                  "sector": "FMCG"},
    {"ticker": "DRREDDY.NS",    "name": "Dr. Reddy's",          "sector": "Pharma"},
    {"ticker": "CIPLA.NS",      "name": "Cipla",                "sector": "Pharma"},
    # High-beta Mid-cap
    {"ticker": "PERSISTENT.NS", "name": "Persistent Systems",   "sector": "IT"},
    {"ticker": "COFORGE.NS",    "name": "Coforge",              "sector": "IT"},
    {"ticker": "LTIM.NS",       "name": "LTIMindtree",          "sector": "IT"},
    {"ticker": "TRENT.NS",      "name": "Trent",                "sector": "Retail"},
    {"ticker": "HAVELLS.NS",    "name": "Havells India",        "sector": "Electricals"},
    {"ticker": "TATAELXSI.NS",  "name": "Tata Elxsi",          "sector": "IT"},
    {"ticker": "KPITTECH.NS",   "name": "KPIT Technologies",    "sector": "IT"},
    {"ticker": "DLF.NS",        "name": "DLF",                  "sector": "Realty"},
    {"ticker": "TVSMOTOR.NS",   "name": "TVS Motor",            "sector": "Auto"},
    {"ticker": "BAJAJ-AUTO.NS", "name": "Bajaj Auto",           "sector": "Auto"},
    {"ticker": "M&M.NS",        "name": "M&M",                  "sector": "Auto"},
    {"ticker": "RVNL.NS",       "name": "Rail Vikas Nigam",     "sector": "Infra"},
    {"ticker": "ADANIPORTS.NS", "name": "Adani Ports",          "sector": "Ports"},
    {"ticker": "TATAPOWER.NS",  "name": "Tata Power",           "sector": "Power"},
    {"ticker": "BANKBARODA.NS", "name": "Bank of Baroda",       "sector": "Banking"},
    {"ticker": "INDUSINDBK.NS", "name": "IndusInd Bank",        "sector": "Banking"},
]

_discover_cache: dict = {"ts": 0.0, "data": None}   # TTL=5 min
_DISCOVER_SAMPLE = 28   # scan random 28 each call (fast)
_DISCOVER_TOP    = 8    # return top 8


def _momentum_score_stock(meta: dict) -> Optional[dict]:
    """
    Fetch 45d daily OHLCV for one ticker, compute momentum score.
    Returns scored dict or None if data insufficient.
    Score (0–100):
      • 40 pts: Price momentum  — 5d chg vs 20d avg daily chg
      • 30 pts: Volume spike    — last vol vs 10d avg vol
      • 20 pts: Trend           — EMA9 > EMA21 > EMA50
      • 10 pts: RSI sweet spot  — 45-70 = max 10 pts
    """
    import math
    import yfinance as yf

    def _safe(v):
        try:
            f = float(v)
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except Exception:
            return 0.0

    def _ema(closes, period):
        if len(closes) < period:
            return closes[-1] if closes else 0.0
        k = 2.0 / (period + 1)
        val = sum(closes[:period]) / period
        for c in closes[period:]:
            val = c * k + val * (1 - k)
        return val

    def _rsi(closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains = [max(closes[i] - closes[i-1], 0.0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0.0) for i in range(1, len(closes))]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - 100.0 / (1 + rs), 1)

    try:
        obj  = yf.Ticker(meta["ticker"])
        hist = obj.history(period="45d", interval="1d")
        if hist.empty or len(hist) < 20:
            return None

        closes  = [_safe(r["Close"])  for _, r in hist.iterrows()]
        volumes = [_safe(r["Volume"]) for _, r in hist.iterrows()]
        current = closes[-1]
        if current <= 0:
            return None

        # ── Momentum (40 pts) ─────────────────────────────────────────────────
        chg_5d  = (current - closes[-5]) / closes[-5] * 100 if len(closes) >= 5  else 0
        chg_20d = (current - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
        # Bullish momentum: +2% in 5d, +5% in 20d
        mom_score = min(40, max(0, chg_5d * 4 + chg_20d * 1.5))  # 0–40

        # ── Volume spike (30 pts) ─────────────────────────────────────────────
        avg_vol = sum(volumes[-11:-1]) / 10 if len(volumes) >= 11 else sum(volumes) / len(volumes)
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        vol_score = min(30, max(0, (vol_ratio - 1.0) * 20))       # >1.5× = 10 pts; >2.5× = 30 pts

        # ── Trend (20 pts) ────────────────────────────────────────────────────
        ema9  = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        ema50 = _ema(closes, min(50, len(closes)))
        trend_score = 0
        if ema9  > ema21: trend_score += 10
        if ema21 > ema50: trend_score += 10

        # ── RSI sweet spot 45-70 (10 pts) ─────────────────────────────────────
        rsi = _rsi(closes)
        if 45 <= rsi <= 70:
            rsi_score = 10 - abs(rsi - 57.5) / 57.5 * 10   # peak at RSI=57.5
        else:
            rsi_score = 0

        # ── Direction ─────────────────────────────────────────────────────────
        direction = "BUY" if chg_5d > 0 and ema9 > ema21 else ("SELL" if chg_5d < -1.0 else "HOLD")

        total_score = round(mom_score + vol_score + trend_score + rsi_score, 1)

        return {
            "ticker":      meta["ticker"],
            "name":        meta["name"],
            "sector":      meta.get("sector", ""),
            "price":       round(current, 2),
            "chg_5d":      round(chg_5d, 2),
            "chg_20d":     round(chg_20d, 2),
            "vol_ratio":   round(vol_ratio, 2),
            "rsi":         rsi,
            "ema9":        round(ema9, 2),
            "ema21":       round(ema21, 2),
            "direction":   direction,
            "score":       total_score,
            "score_breakdown": {
                "momentum": round(mom_score, 1),
                "volume":   round(vol_score, 1),
                "trend":    round(trend_score, 1),
                "rsi":      round(rsi_score, 1),
            },
        }
    except Exception:
        return None


@robo_router.get("/watchlist/discover")
async def auto_discover_watchlist(refresh: bool = False):
    """
    Auto-Discover top momentum stocks for the watchlist.

    Scores each stock (0–100) on:
      • Price momentum   (5d + 20d change)
      • Volume spike     (vs 10d avg)
      • Trend strength   (EMA9 > EMA21 > EMA50)
      • RSI sweet zone   (45–70)

    Cached for 5 minutes. Use ?refresh=true to force re-scan.
    Returns top 8 high-momentum BUY/SELL candidates.
    """
    global _discover_cache

    now = time.time()
    if not refresh and _discover_cache["data"] and (now - _discover_cache["ts"]) < 300:
        return {"success": True, "from_cache": True, **_discover_cache["data"]}

    # Pick random sample so every call discovers something new on refresh
    sample = random.sample(_DISCOVER_UNIVERSE, min(_DISCOVER_SAMPLE, len(_DISCOVER_UNIVERSE)))

    results = []
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_momentum_score_stock, m): m for m in sample}
        for fut in as_completed(futures):
            try:
                res = fut.result(timeout=15)
                if res:
                    results.append(res)
            except Exception:
                pass

    # Sort by score descending; prefer BUY over SELL over HOLD
    dir_order = {"BUY": 0, "SELL": 1, "HOLD": 2}
    results.sort(key=lambda x: (-x["score"], dir_order.get(x["direction"], 2)))

    top = results[:_DISCOVER_TOP]

    payload = {
        "candidates":   top,
        "total_scanned": len(results),
        "scan_pool":     len(sample),
        "cached_at":    now,
    }
    _discover_cache = {"ts": now, "data": payload}

    return {"success": True, "from_cache": False, **payload}



# ─── 24. GET /danger-scan ─────────────────────────────────────────────────────
@robo_router.get("/danger-scan")
async def danger_mode_scan(
    top: int = Query(5, ge=1, le=15, description="Top N F&O picks to return"),
    force: bool = Query(False, description="Force fresh scan (bypass 90s cache)"),
):
    """
    Danger Mode — F&O Universe Scanner.
    Scans all F&O underlyings (indices + 25 liquid stocks) ranked by:
      • Momentum score (5d return, volume spike, ATR, RSI sweet-zone)
      • PCR parity boost (Put-Call ratio directional signal)
    Used automatically when risk_tolerance = 'danger'. No direct equity trades.
    """
    try:
        from .danger_scanner import async_danger_scan
        results = await async_danger_scan(top_n=top, force=force)
        return {
            "success":    True,
            "top_picks":  results,
            "count":      len(results),
            "mode":       "danger",
            "note":       "Danger mode: F&O universe scan with PCR priority. No direct stock trades.",
            "scanned":    len(results),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Danger scan failed: {e}")
