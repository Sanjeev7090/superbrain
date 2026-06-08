"""
Advanced Trading API Router — DreamerV3 Phase 2.

Endpoints:
  POST /api/advanced/portfolio/optimize        — Mean-Variance / Black-Litterman
  GET  /api/advanced/portfolio/frontier        — Efficient frontier
  POST /api/advanced/portfolio/kelly           — Kelly per-asset
  GET  /api/advanced/portfolio/cvar            — CVaR analysis
  POST /api/advanced/portfolio/hedge-suggest   — Options overlay suggestions
  POST /api/advanced/portfolio/smart-route     — Smart order routing
  GET  /api/advanced/risk/circuit-status       — Circuit breaker state
  POST /api/advanced/risk/kill-switch          — Activate/deactivate kill switch
  POST /api/advanced/risk/reset-circuit        — Reset circuit breaker
  GET  /api/advanced/risk/approvals            — Pending human approvals
  POST /api/advanced/risk/approve/{id}         — Approve/reject trade
  GET  /api/advanced/sentiment/news            — News sentiment for ticker
  GET  /api/advanced/sentiment/market          — Market-wide sentiment
  GET  /api/advanced/observability/metrics     — Trade metrics + equity curve
  GET  /api/advanced/observability/alerts      — Anomaly alerts
  GET  /api/advanced/observability/prometheus  — Prometheus text metrics
  POST /api/advanced/dreamer/continuous-toggle — Toggle continuous training
  GET  /api/advanced/dreamer/per-stats         — PER buffer stats
  POST /api/advanced/dreamer/record-trade      — Record trade for observability
"""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

advanced_router = APIRouter(prefix="/api/advanced", tags=["Advanced Trading"])


# ─── Request/Response models ──────────────────────────────────────────────────

class PortfolioRequest(BaseModel):
    tickers: List[str]
    method:  str = "mv"          # mv | bl
    views:   Optional[Dict[str, float]] = None
    period:  str = "1y"


class HedgeRequest(BaseModel):
    ticker:        str
    current_price: float
    position_size: float = 0.10
    volatility:    float = 0.20
    view:          str   = "neutral"


class SmartRouteRequest(BaseModel):
    ticker:      str
    direction:   str
    quantity:    float
    avg_volume:  float = 100_000
    volatility:  float = 0.20
    urgency:     float = 0.5


class KillSwitchRequest(BaseModel):
    action: str   # activate | deactivate
    reason: str   = "Manual override"


class ApprovalRequest(BaseModel):
    approved: bool
    comment:  str = ""


class RecordTradeRequest(BaseModel):
    pnl_pct:    float
    direction:  str   = "BUY"
    ticker:     str   = "RELIANCE.NS"
    capital_at_risk: float = 0.0


class ContinuousToggleRequest(BaseModel):
    enabled: bool
    ticker:  str = "RELIANCE.NS"


# ─── Portfolio endpoints ──────────────────────────────────────────────────────

@advanced_router.post("/portfolio/optimize")
async def portfolio_optimize(req: PortfolioRequest):
    try:
        from rl_agent.portfolio_optimizer import optimize_portfolio
        return optimize_portfolio(req.tickers, req.method, req.views, req.period)
    except Exception as exc:
        logger.exception("Portfolio optimize error")
        return {"error": str(exc)}


@advanced_router.post("/portfolio/frontier")
async def efficient_frontier_ep(req: PortfolioRequest):
    try:
        from rl_agent.portfolio_optimizer import _get_returns, efficient_frontier
        returns = _get_returns(req.tickers, req.period)
        if returns.empty:
            return {"error": "Insufficient data"}
        return {"frontier": efficient_frontier(returns, n_points=30)}
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/portfolio/kelly")
async def kelly_criterion(req: PortfolioRequest):
    try:
        from rl_agent.portfolio_optimizer import _get_returns, kelly_per_asset
        returns = _get_returns(req.tickers, req.period)
        if returns.empty:
            return {"error": "Insufficient data"}
        return {"kelly": kelly_per_asset(returns)}
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/portfolio/cvar")
async def cvar_analysis(req: PortfolioRequest):
    try:
        from rl_agent.portfolio_optimizer import _get_returns
        from rl_agent.risk_reward import compute_cvar
        import numpy as np
        returns = _get_returns(req.tickers, req.period)
        if returns.empty:
            return {"error": "Insufficient data"}
        result = {}
        for col in returns.columns:
            r = returns[col].dropna().values
            result[col] = compute_cvar(r)
        # Portfolio-level CVaR (equal weight)
        w = np.ones(len(returns.columns)) / len(returns.columns)
        port_ret = returns @ w
        result["portfolio"] = compute_cvar(port_ret.values)
        return result
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/portfolio/correlation")
async def correlation_hedge(req: PortfolioRequest):
    try:
        from rl_agent.portfolio_optimizer import _get_returns, correlation_hedge
        returns = _get_returns(req.tickers, req.period)
        if returns.empty or len(req.tickers) < 2:
            return {"error": "Need ≥2 tickers"}
        corr = returns.corr().round(4).to_dict()
        hedge = correlation_hedge(returns, req.tickers[0])
        return {"correlation_matrix": corr, "hedge_suggestion": hedge}
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/portfolio/hedge-suggest")
async def hedge_suggest(req: HedgeRequest):
    try:
        from rl_agent.portfolio_optimizer import options_overlay_suggest
        return options_overlay_suggest(
            req.ticker, req.current_price, req.position_size,
            req.volatility, req.view,
        )
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/portfolio/smart-route")
async def smart_route(req: SmartRouteRequest):
    try:
        from rl_agent.portfolio_optimizer import smart_order_route
        return smart_order_route(
            req.ticker, req.direction, req.quantity,
            req.avg_volume, req.volatility, req.urgency,
        )
    except Exception as exc:
        return {"error": str(exc)}


# ─── Risk / Safety endpoints ──────────────────────────────────────────────────

@advanced_router.get("/risk/circuit-status")
async def circuit_status():
    try:
        from observability.metrics_engine import get_circuit_status
        return get_circuit_status()
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/risk/kill-switch")
async def kill_switch(req: KillSwitchRequest):
    try:
        from observability.metrics_engine import activate_kill_switch, deactivate_kill_switch
        if req.action == "activate":
            activate_kill_switch(req.reason)
            return {"status": "activated", "reason": req.reason}
        else:
            deactivate_kill_switch()
            return {"status": "deactivated"}
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/risk/reset-circuit")
async def reset_circuit():
    try:
        from observability.metrics_engine import reset_circuit as rc
        rc()
        return {"status": "reset", "message": "Circuit breaker reset to NORMAL"}
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.get("/risk/approvals")
async def get_approvals():
    try:
        from observability.metrics_engine import get_pending_approvals
        import observability.metrics_engine as me
        with me._lock:
            all_approvals = list(me._approval_queue)
        return {
            "pending":  [a for a in all_approvals if a["status"] == "PENDING"],
            "resolved": [a for a in all_approvals if a["status"] != "PENDING"],
        }
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.post("/risk/approve/{approval_id}")
async def resolve_approval(approval_id: str, req: ApprovalRequest):
    try:
        from observability.metrics_engine import resolve_approval as ra
        ok = ra(approval_id, req.approved, req.comment)
        return {"success": ok, "approval_id": approval_id, "status": "APPROVED" if req.approved else "REJECTED"}
    except Exception as exc:
        return {"error": str(exc)}


# ─── Sentiment endpoints ──────────────────────────────────────────────────────

@advanced_router.get("/sentiment/news")
async def news_sentiment(ticker: str = "RELIANCE.NS"):
    try:
        from data_providers.sentiment_provider import get_news_sentiment
        return get_news_sentiment(ticker)
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.get("/sentiment/market")
async def market_sentiment(tickers: str = "RELIANCE.NS,TCS.NS,INFY.NS,HDFCBANK.NS"):
    try:
        from data_providers.sentiment_provider import market_sentiment_aggregate
        ticker_list = [t.strip() for t in tickers.split(",")][:10]
        return market_sentiment_aggregate(ticker_list)
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.get("/sentiment/fear-greed")
async def fear_greed(
    pcr: float = 1.0,
    india_vix: float = 15.0,
    breadth: float = 0.5,
    sentiment_score: float = 0.0,
):
    try:
        from data_providers.sentiment_provider import fear_greed_index
        return fear_greed_index(pcr, india_vix, breadth, sentiment_score)
    except Exception as exc:
        return {"error": str(exc)}


# ─── Observability endpoints ──────────────────────────────────────────────────

@advanced_router.get("/observability/metrics")
async def trade_metrics():
    try:
        from observability.metrics_engine import get_metrics
        return get_metrics()
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.get("/observability/alerts")
async def anomaly_alerts(limit: int = 30):
    try:
        from observability.metrics_engine import get_alerts
        return {"alerts": get_alerts(limit)}
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.get("/observability/prometheus")
async def prometheus_export():
    from fastapi.responses import PlainTextResponse
    try:
        from observability.metrics_engine import prometheus_metrics
        return PlainTextResponse(prometheus_metrics(), media_type="text/plain")
    except Exception as exc:
        return PlainTextResponse(f"# error: {exc}\n", media_type="text/plain")


@advanced_router.post("/observability/record-trade")
async def record_trade(req: RecordTradeRequest):
    try:
        from observability.metrics_engine import record_trade as rt
        rt(req.pnl_pct, req.direction, req.ticker, req.capital_at_risk)
        return {"recorded": True, "pnl_pct": req.pnl_pct}
    except Exception as exc:
        return {"error": str(exc)}


# ─── DreamerV3 Continuous Training endpoints ──────────────────────────────────

@advanced_router.post("/dreamer/continuous-toggle")
async def continuous_toggle(req: ContinuousToggleRequest):
    try:
        from rl_agent import dreamer_trainer as dt
        if req.enabled:
            return dt.start_continuous(req.ticker)
        else:
            return dt.stop_continuous()
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.get("/dreamer/per-stats")
async def per_stats():
    try:
        from rl_agent import dreamer_trainer as dt
        return dt.get_per_stats()
    except Exception as exc:
        return {"error": str(exc)}


@advanced_router.get("/dreamer/risk-reward")
async def risk_reward_state():
    try:
        from rl_agent import dreamer_trainer as dt
        return dt.get_risk_reward_state()
    except Exception as exc:
        return {"error": str(exc)}
