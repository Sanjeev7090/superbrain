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

import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, Field

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
        None, description="conservative | moderate | aggressive"
    )
    mode: Optional[str] = Field(
        None, description="paper | live  (live applies 30% extra safety multiplier)"
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
    risk_tolerance:      str   = Field("moderate",  description="conservative|moderate|aggressive")
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

    interval_minutes: 1–30 min. Default 5 min.
    DISCLAIMER: Starts in Paper Trading mode unless mode was changed via /api/robo/mode
    """
    return orch.start_auto_mode(
        ticker           = req.ticker,
        interval_minutes = req.interval_minutes,
    )


# ─── 6. POST /stop ────────────────────────────────────────────────────────────
@robo_router.post("/stop")
async def stop_auto_mode():
    """Stop the autonomous trading loop."""
    return orch.stop_auto_mode()


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
async def get_audit_trail(limit: int = Query(50, ge=1, le=100)):
    """Get paper trade audit trail (last N closed trades) with summary statistics."""
    trades    = orch.get_audit_trail(limit=limit)
    total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)
    wins      = sum(1 for t in trades if (t.get("pnl") or 0) >= 0)
    losses    = len(trades) - wins
    return {
        "success":    True,
        "trades":     trades,
        "count":      len(trades),
        "total_pnl":  round(total_pnl, 2),
        "win_count":  wins,
        "loss_count": losses,
        "win_rate":   round(wins / max(len(trades), 1) * 100, 1),
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


# ─── 15. GET /positions ───────────────────────────────────────────────────────
@robo_router.get("/positions")
async def get_open_positions():
    """
    Currently open positions across all modes (paper / live / shadow).
    Includes pending live orders awaiting confirmation delay.
    """
    try:
        from .execution_engine import engine
        stats = engine.get_daily_stats()
        return {
            "success":             True,
            "mode":                engine.mode,
            "open_positions":      stats["open_positions_list"],
            "pending_positions":   stats["pending_list"],
            "shadow_signals":      stats["shadow_list"],
            "open_count":          stats["open_positions"],
            "pending_count":       stats["pending_positions"],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "open_positions": []}


# ─── 16. GET /orders ──────────────────────────────────────────────────────────
@robo_router.get("/orders")
async def get_order_history(limit: int = Query(50, ge=1, le=200)):
    """
    Full order history (closed orders) for today's session.
    Includes P&L, brokerage, exit reason, DreamerV3 signal, risk profile snapshot.
    """
    try:
        from .execution_engine import engine
        history = engine.get_order_history(limit=limit)
        stats   = engine.get_daily_stats()
        total_pnl  = sum((o.get("pnl") or 0) for o in history)
        total_net  = sum((o.get("net_pnl") or 0) for o in history)
        wins       = sum(1 for o in history if (o.get("pnl") or 0) > 0)
        losses     = len(history) - wins
        return {
            "success":        True,
            "mode":           engine.mode,
            "orders":         history,
            "count":          len(history),
            "daily_pnl":      round(stats["daily_pnl"], 2),
            "daily_net_pnl":  round(stats["daily_net_pnl"], 2),
            "daily_brokerage": round(stats["daily_brokerage"], 2),
            "wins":           wins,
            "losses":         losses,
            "win_rate":       round(wins / max(len(history), 1) * 100, 1),
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
    In live mode, this will place market sell/buy orders on Groww.
    Use in emergencies — rapid market movement, unexpected circuit breakers, etc.
    """
    try:
        from .execution_engine import engine
        from .dreamer_robo_orchestrator import _fetch_live_price, _prefs as prefs
        live_price = _fetch_live_price(prefs.ticker) or 0.0
        prices = {prefs.ticker: live_price}
        closed = engine.close_all_positions(prices, reason="EMERGENCY_CLOSE")
        orch._upd(open_trade=None, status="paused")
        return {
            "success":        True,
            "closed_count":   len(closed),
            "closed_orders":  closed,
            "message":        f"Closed {len(closed)} position(s) at ₹{live_price:.2f}",
            "disclaimer":     DISCLAIMER,
        }
    except Exception as exc:
        logger.exception("[robo_router] emergency close-all failed")
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

