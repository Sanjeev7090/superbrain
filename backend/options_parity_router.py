"""
Put-Call Parity Router
=======================
- POST /api/options/put-call-parity   : Single-strike calculator + payoff chart data
- GET  /api/options/parity-scanner    : Scans entire F&O universe (NIFTY family + SENSEX),
                                        ranks strikes by parity mispricing, returns best arbitrage.

Theory: C + X·e^(-rT) = P + S  (no-dividend European parity)
  mispricing = (C + X·e^(-rT)) - (P + S)
  > 0  → Call overpriced / Put underpriced  → REVERSE CONVERSION
  < 0  → Call underpriced / Put overpriced  → CONVERSION
"""
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import math
import logging

router = APIRouter(prefix="/api/options", tags=["options-parity"])
log = logging.getLogger("options_parity")


# ─────────────────────── Models ─────────────────────────
class PutCallParityRequest(BaseModel):
    stock_price: float = Field(..., gt=0)
    strike: float = Field(..., gt=0)
    call_price: float = Field(..., ge=0)
    put_price: float = Field(..., ge=0)
    risk_free_rate: float = 0.065        # India 10Y G-Sec ~6.5%
    time_to_expiry: float = 30.0 / 365.0  # years
    ticker: str = "NIFTY"


# ─────────────────────── Helpers ────────────────────────
def _days_to_expiry(expiry_str: str) -> float:
    """Parse '28-May-2026' or similar → days from now (clamped ≥ 1)."""
    if not expiry_str:
        return 30.0
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(expiry_str, fmt)
            days = (dt - datetime.now()).days
            return float(max(1, days))
        except Exception:
            continue
    return 30.0


def _compute_parity(S: float, X: float, C: float, P: float, r: float, T: float) -> Dict[str, Any]:
    """Core parity check."""
    left  = C + X * math.exp(-r * T)
    right = P + S
    mispricing = left - right
    mispricing_pct = (mispricing / S * 100.0) if S else 0.0
    abs_pct = abs(mispricing_pct)

    if abs_pct < 0.05:
        signal = "FAIRLY_PRICED"
        action = "No arbitrage — parity holds within bid-ask spread"
    elif mispricing > 0:
        signal = "REVERSE_CONVERSION"
        action = "Short Call + Long Put + Long Stock + Short Bond (call overpriced / put cheap)"
    else:
        signal = "CONVERSION"
        action = "Long Call + Short Put + Short Stock + Long Bond (call cheap / put overpriced)"

    return {
        "left_side": round(left, 4),
        "right_side": round(right, 4),
        "mispricing": round(mispricing, 4),
        "mispricing_pct": round(mispricing_pct, 4),
        "arbitrage_opportunity": abs_pct > 0.10,   # > 10 bps of spot
        "signal": signal,
        "action": action,
    }


def _payoff_chart(S: float, X: float, C: float, P: float, r: float, T: float, n: int = 80) -> Dict[str, list]:
    """Generate payoff curves for Long Stock / Long Call / Long Put."""
    lo = S * 0.7
    hi = S * 1.3
    step = (hi - lo) / max(1, n - 1)
    stock_prices, long_stock, long_call, long_put = [], [], [], []
    for i in range(n):
        sp = lo + i * step
        stock_prices.append(round(sp, 2))
        long_stock.append(round(sp - S, 2))
        long_call.append(round(max(sp - X, 0) - C, 2))
        long_put.append(round(max(X - sp, 0) - P, 2))
    return {
        "stock_prices": stock_prices,
        "long_stock":   long_stock,
        "long_call":    long_call,
        "long_put":     long_put,
    }


# ─────────────────── 1) Single-strike endpoint ───────────────────
@router.post("/put-call-parity")
async def put_call_parity(data: PutCallParityRequest):
    """One-Click Put-Call Parity Calculator + Payoff Chart Data."""
    parity = _compute_parity(
        S=data.stock_price, X=data.strike,
        C=data.call_price, P=data.put_price,
        r=data.risk_free_rate, T=data.time_to_expiry,
    )
    chart = _payoff_chart(
        S=data.stock_price, X=data.strike,
        C=data.call_price, P=data.put_price,
        r=data.risk_free_rate, T=data.time_to_expiry,
    )
    return {
        "ticker": data.ticker,
        "parity_equation": "C + X·e^(-rT) = P + S",
        "current_values": {
            "stock":  data.stock_price,
            "strike": data.strike,
            "call":   data.call_price,
            "put":    data.put_price,
            "r":      data.risk_free_rate,
            "T_years": round(data.time_to_expiry, 4),
        },
        "parity_check": parity,
        "chart_data":   chart,
        "message":      "Put-Call Parity Analysis Ready",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────── 2) F&O scanner endpoint ───────────────────
@router.get("/parity-scanner")
async def parity_scanner(
    symbols: str = Query("NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX", description="Comma-separated F&O underlyings"),
    strikes_around_atm: int = Query(10, ge=2, le=30, description="± strikes around ATM to consider"),
    top: int = Query(15, ge=1, le=50),
    r: float = Query(0.065, description="Risk-free rate (default 6.5%)"),
):
    """
    Scan entire F&O universe and find the BEST Put-Call Parity arbitrage opportunities.

    For each underlying, pulls live option chain, pairs CE+PE at every near-ATM strike,
    computes parity mispricing, ranks by |mispricing %|.
    """
    # Lazy imports — fall back gracefully if helpers don't exist
    try:
        from server import (
            _fetch_nse_option_chain, _extract_option_rows,
            _fetch_sensex_live_options, _fetch_live_india_vix, _sensex_expiry_dates,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Option chain helpers unavailable: {e}")

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="No symbols provided")

    all_results: List[Dict[str, Any]] = []
    per_symbol: Dict[str, Dict[str, Any]] = {}

    for sym in syms:
        try:
            if sym == "SENSEX":
                import yfinance as _yf
                try:
                    hist = _yf.Ticker("^BSESN").history(period="2d", interval="1d")
                    spot = float(hist["Close"].iloc[-1]) if len(hist) else 80000.0
                except Exception:
                    spot = 80000.0
                sigma   = _fetch_live_india_vix()
                expiries = _sensex_expiry_dates(n_weeks=4)
                expiry_str = expiries[0] if expiries else ""
                opts = _fetch_sensex_live_options(spot, sigma, expiry_str)
                underlying, nearest_expiry = spot, expiry_str
            else:
                oi_data = _fetch_nse_option_chain(sym)
                if not oi_data or not oi_data.get("records", {}).get("data"):
                    per_symbol[sym] = {"error": "empty option chain"}
                    continue
                opts, underlying, nearest_expiry, _ = _extract_option_rows(oi_data, sym, nearest_only=True)

            if not opts or not underlying:
                per_symbol[sym] = {"error": "no options data"}
                continue

            # Group by strike → {strike: {CE, PE}}
            by_strike: Dict[float, Dict[str, Dict[str, Any]]] = {}
            for o in opts:
                k = float(o.get("strike", 0))
                if k <= 0:
                    continue
                t = o.get("type", "")
                if t not in ("CE", "PE"):
                    continue
                by_strike.setdefault(k, {})[t] = o

            # Pick near-ATM strikes
            strikes_sorted = sorted(by_strike.keys(), key=lambda x: abs(x - underlying))[: strikes_around_atm * 2]
            T = _days_to_expiry(nearest_expiry) / 365.0

            sym_rows: List[Dict[str, Any]] = []
            for strike in strikes_sorted:
                ce = by_strike[strike].get("CE")
                pe = by_strike[strike].get("PE")
                if not ce or not pe:
                    continue
                C = float(ce.get("last_price", 0) or 0)
                P = float(pe.get("last_price", 0) or 0)
                if C <= 0 or P <= 0:
                    continue

                parity = _compute_parity(S=underlying, X=strike, C=C, P=P, r=r, T=T)
                row = {
                    "underlying": sym,
                    "spot": round(underlying, 2),
                    "strike": strike,
                    "expiry": nearest_expiry,
                    "T_years": round(T, 4),
                    "call_price": round(C, 2),
                    "put_price":  round(P, 2),
                    "call_oi":   float(ce.get("oi", 0) or 0),
                    "put_oi":    float(pe.get("oi", 0) or 0),
                    "call_vol":  float(ce.get("volume", 0) or 0),
                    "put_vol":   float(pe.get("volume", 0) or 0),
                    "parity":    parity,
                    "score":     abs(parity["mispricing_pct"]),
                }
                sym_rows.append(row)
                all_results.append(row)

            per_symbol[sym] = {
                "spot": round(underlying, 2),
                "expiry": nearest_expiry,
                "strikes_scanned": len(sym_rows),
                "best": max(sym_rows, key=lambda r: r["score"]) if sym_rows else None,
            }
        except Exception as e:
            log.warning(f"parity-scanner {sym} failed: {e}")
            per_symbol[sym] = {"error": str(e)}
            continue

    if not all_results:
        return {
            "best": None,
            "top": [],
            "per_symbol": per_symbol,
            "scanned": syms,
            "message": "No usable option-chain pairs found right now. Try outside market hours? NSE may be rate-limiting.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # Rank by absolute mispricing %
    all_results.sort(key=lambda r: r["score"], reverse=True)
    best = all_results[0]
    top_list = all_results[: top]

    # Generate payoff chart for the BEST opportunity
    best_chart = _payoff_chart(
        S=best["spot"], X=best["strike"],
        C=best["call_price"], P=best["put_price"],
        r=r, T=best["T_years"],
    )

    return {
        "best": {**best, "chart_data": best_chart},
        "top": top_list,
        "per_symbol": per_symbol,
        "scanned": syms,
        "params": {"strikes_around_atm": strikes_around_atm, "r": r, "top": top},
        "message": f"Best opportunity: {best['underlying']} {int(best['strike'])} "
                   f"{best['parity']['signal']} ({best['parity']['mispricing_pct']:+.3f}%)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
