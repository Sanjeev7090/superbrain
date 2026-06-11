"""
Hybrid Super Brain Router
==========================
REST API surface for the HybridSuperBrain.

Endpoints:
  POST /api/hybrid-brain/decide       — get a fresh decision for a symbol
  GET  /api/hybrid-brain/state        — current fear, PnL, target, config
  POST /api/hybrid-brain/update-pnl   — push today's PnL %
  POST /api/hybrid-brain/reset-daily  — start a new trading day
  GET  /api/hybrid-brain/audit        — recent decision history
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import logging

from .hybrid_super_brain import hybrid_brain

router = APIRouter(prefix="/api/hybrid-brain", tags=["hybrid-brain"])
log = logging.getLogger("hybrid_brain_router")


# ─── Pydantic models ──────────────────────────────────────────────────────────
class DecideRequest(BaseModel):
    symbol: str = "NIFTY"
    news: str = ""
    dreamer_confidence: Optional[float] = Field(None, ge=0, le=100)
    market_data: Dict[str, Any] = Field(default_factory=dict)
    # market_data accepts: momentum_strength, volatility_index, atr_pct,
    # volume_thrust, change_pct, dreamer_conf


class PnLUpdate(BaseModel):
    pnl_pct: float = Field(..., description="Today's PnL as fraction (0.005 = 0.5%)")


# ─── Helpers ──────────────────────────────────────────────────────────────────
async def _auto_market_data(symbol: str) -> Dict[str, Any]:
    """Auto-fetch market context using existing helpers; graceful fallbacks."""
    md: Dict[str, Any] = {}

    # India VIX (vol index) - reuse helper from server.py if loaded
    try:
        from server import _fetch_live_india_vix
        md["volatility_index"] = float(_fetch_live_india_vix() or 0.015)
    except Exception:
        md["volatility_index"] = 0.015

    # yfinance momentum / change_pct
    try:
        import yfinance as _yf
        ticker_map = {
            "NIFTY":      "^NSEI",
            "BANKNIFTY":  "^NSEBANK",
            "FINNIFTY":   "NIFTY_FIN_SERVICE.NS",
            "SENSEX":     "^BSESN",
            "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
        }
        yt = ticker_map.get(symbol.upper(), symbol)
        hist = _yf.Ticker(yt).history(period="22d", interval="1d")
        if len(hist) >= 5:
            closes = hist["Close"].astype(float)
            vols   = hist["Volume"].astype(float)
            r5  = float((closes.iloc[-1] / closes.iloc[-5] - 1.0))   # 5-day return
            r20 = float((closes.iloc[-1] / closes.iloc[0]   - 1.0))  # ~1-month return
            md["change_pct"] = float((closes.iloc[-1] / closes.iloc[-2] - 1.0) * 100)
            md["momentum_strength"] = max(0.0, min(1.0, 0.5 + 5.0 * r5 + 1.5 * r20))
            avg_vol_20 = float(vols.tail(20).mean()) if len(vols) >= 20 else float(vols.mean())
            md["volume_thrust"] = float(vols.iloc[-1] / avg_vol_20) if avg_vol_20 > 0 else 1.0
            # ATR % proxy: rolling stdev of daily returns
            ret = closes.pct_change().dropna().tail(14)
            md["atr_pct"] = float(ret.std()) if len(ret) else 0.015
    except Exception as e:
        log.debug(f"yfinance auto fetch failed for {symbol}: {e}")

    return md


async def _auto_news(symbol: str) -> str:
    """Pull simple news headlines if mirofish/news helpers exist."""
    try:
        from server import _fetch_mirofish_news  # if it exists
        items = _fetch_mirofish_news(symbol) or []
        return " | ".join((it.get("title") or "") for it in items[:5])
    except Exception:
        return ""


# ─── Endpoints ────────────────────────────────────────────────────────────────
@router.post("/decide")
async def decide(req: DecideRequest):
    """Get a hybrid decision. If market_data is sparse, auto-enrich from live sources."""
    md = dict(req.market_data or {})

    # Auto-enrich missing keys
    if not any(k in md for k in ("momentum_strength", "change_pct", "volatility_index")):
        auto = await _auto_market_data(req.symbol)
        for k, v in auto.items():
            md.setdefault(k, v)

    news = req.news or await _auto_news(req.symbol)

    try:
        decision = await hybrid_brain.think_and_decide(
            market_data=md,
            news=news,
            symbol=req.symbol.upper(),
            dreamer_confidence=req.dreamer_confidence,
        )
        return decision
    except Exception as e:
        log.exception("decide failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/state")
async def state():
    """Current fear/PnL/target/config snapshot."""
    await hybrid_brain.survival.load()
    s = hybrid_brain.survival
    return {
        "daily_target_pct": s.daily_target * 100,
        "current_pnl_pct":  hybrid_brain.current_pnl_pct * 100,
        "fear_level":       round(s.fear_level, 3),
        "consecutive_fail": s.consecutive_fail,
        "last_pnl_pct":     s.last_pnl_pct * 100,
        "grace_days":       s.grace_days,
        "config":           hybrid_brain.config,
        "as_of":            datetime.now(timezone.utc).isoformat(),
    }


@router.post("/update-pnl")
async def update_pnl(body: PnLUpdate):
    await hybrid_brain.update_daily_pnl(body.pnl_pct)
    return await state()


@router.post("/reset-daily")
async def reset_daily():
    """
    Manual brain reset — fully clears fear level (0.0), consecutive fails, and PnL tracker.
    Use this when you want to give the brain a fresh start after a rough period.
    Automatic overnight reset decays fear by 0.35; this endpoint resets it to zero.
    """
    await hybrid_brain.reset_for_new_day(manual=True)
    # Also clear cached decisions so next decide() uses fresh state
    hybrid_brain._decision_cache.clear()
    return await state()


@router.get("/audit")
async def audit(limit: int = Query(50, ge=1, le=200)):
    rows = await hybrid_brain.get_recent_audit(limit=limit)
    return {"count": len(rows), "decisions": rows}
