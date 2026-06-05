"""FastAPI router for Multi-AI Ensemble Decision Engine."""

import json
import logging
from typing import Optional

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel

from . import engine as ensemble_engine
from . import gann_optimizer

logger = logging.getLogger(__name__)

ensemble_router = APIRouter(prefix="/api/ensemble", tags=["Multi-AI Ensemble"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignalRequest(BaseModel):
    ticker: str
    context: Optional[dict] = None
    extra_prompt: Optional[str] = None


class GannRequest(BaseModel):
    ticker: str


class FreePrompt(BaseModel):
    user_text: str
    system_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers: extract SL/Target from per_model parsed JSON
# ---------------------------------------------------------------------------

def _extract_levels(parsed: Optional[dict], context: dict) -> dict:
    """Pull entry/SL/target fields from a model's parsed JSON response."""
    if not parsed:
        return {}
    cur = context.get("close", 0) or 0
    entry  = parsed.get("entry_price") or parsed.get("entry") or cur
    sl     = parsed.get("stop_loss") or parsed.get("sl") or None
    t1     = parsed.get("target_1") or parsed.get("t1") or None
    t2     = parsed.get("target_2") or parsed.get("t2") or None
    t3     = parsed.get("target_3") or parsed.get("t3") or None
    try:
        return {
            "entry_price": round(float(entry), 2),
            "stop_loss":   round(float(sl), 2)  if sl  is not None else None,
            "target_1":    round(float(t1), 2)  if t1  is not None else None,
            "target_2":    round(float(t2), 2)  if t2  is not None else None,
            "target_3":    round(float(t3), 2)  if t3  is not None else None,
        }
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@ensemble_router.get("/status")
async def status():
    return ensemble_engine.get_status()


@ensemble_router.post("/signal")
async def signal(req: SignalRequest):
    """
    Full ensemble signal: 3 AI models + Kronos (if loaded) each give BUY/SELL/SL/Target.
    """
    context = req.context
    if not context:
        df = gann_optimizer._fetch_recent_bars(req.ticker)
        if df is None or len(df) < 20:
            return {"success": False, "error": f"Could not fetch data for {req.ticker}"}
        context = gann_optimizer._market_context(df)

    prompt = {
        "ticker": req.ticker,
        "snapshot": context,
        "task": (
            "Output STRICT JSON with: signal (BUY/SELL/HOLD), confidence (0-100), "
            "entry_price, stop_loss, target_1, target_2, target_3, rationale."
        ),
    }
    if req.extra_prompt:
        prompt["additional_instructions"] = req.extra_prompt

    verdict = await ensemble_engine.ask_ensemble(json.dumps(prompt, indent=2))

    # Enrich per_model results with extracted price levels + flatten parsed JSON
    votes_map = {v["model"]: v for v in verdict.get("votes", [])}
    per_model_enriched = []
    for r in verdict.get("per_model", []):
        levels = _extract_levels(r.get("parsed"), context)
        # Merge parsed fields (signal, confidence, rationale) + vote weight
        vote_info = votes_map.get(r.get("model"), {})
        parsed = r.get("parsed") or {}
        enriched = {
            **r,
            "signal":     vote_info.get("signal") or parsed.get("signal") or "HOLD",
            "confidence": vote_info.get("confidence") or parsed.get("confidence") or 0,
            "rationale":  vote_info.get("rationale") or parsed.get("rationale") or "",
            "weight":     vote_info.get("weight", 1.0),
            **levels,
        }
        per_model_enriched.append(enriched)

    # Add Kronos as model #4
    kronos_result = None
    try:
        from kronos_router import get_kronos_signal
        kronos_raw = await get_kronos_signal(req.ticker)
        if kronos_raw:
            # Normalise to match ensemble per_model format
            sig = kronos_raw["signal"]
            if sig == "WAIT":
                sig = "HOLD"
            kronos_result = {
                "model":       "Kronos AI",
                "provider":    "kronos",
                "ok":          True,
                "signal":      sig,
                "confidence":  kronos_raw["confidence"],
                "rationale":   kronos_raw["rationale"],
                "weight":      1.0,
                "entry_price": kronos_raw.get("entry_price"),
                "stop_loss":   kronos_raw.get("stop_loss"),
                "target_1":    kronos_raw.get("target_1"),
                "target_2":    kronos_raw.get("target_2"),
                "target_3":    kronos_raw.get("target_3"),
                "risk_reward": kronos_raw.get("risk_reward"),
            }
            per_model_enriched.append(kronos_result)
    except Exception as e:
        logger.warning("Kronos signal fetch skipped: %s", e)

    verdict["per_model"] = per_model_enriched

    return {
        "success":      True,
        "ticker":       req.ticker,
        "context":      context,
        "verdict":      verdict,
        "kronos_loaded": kronos_result is not None,
    }


@ensemble_router.post("/full-analysis")
async def full_analysis(req: SignalRequest):
    """
    Call ALL 45 models (Claude, GPT, Gemini, DeepSeek, GLM, Minimax, Kimi, Qwen...)
    in parallel and return numbered BUY/SELL/SL/Target results.
    OpenCode/9router models need 9router running at localhost:20128.
    """
    context = req.context
    if not context:
        df = gann_optimizer._fetch_recent_bars(req.ticker)
        if df is None or len(df) < 20:
            return {"success": False, "error": f"Could not fetch data for {req.ticker}"}
        context = gann_optimizer._market_context(df)

    prompt = {
        "ticker": req.ticker,
        "snapshot": context,
        "task": (
            "Output STRICT JSON with: signal (BUY/SELL/HOLD), confidence (0-100), "
            "entry_price, stop_loss, target_1, target_2, target_3, rationale."
        ),
    }

    all_results = await ensemble_engine.ask_all_models(json.dumps(prompt, indent=2))

    # Enrich each result with signal/confidence from parsed JSON
    enriched = []
    for r in all_results:
        parsed = r.get("parsed") or {}
        levels = _extract_levels(parsed, context)
        enriched.append({
            **r,
            "signal":     parsed.get("signal") or r.get("signal") or "HOLD",
            "confidence": parsed.get("confidence") or r.get("confidence") or 0,
            "rationale":  parsed.get("rationale") or r.get("rationale") or "",
            **levels,
        })

    # Add Kronos as model #46
    try:
        from kronos_router import get_kronos_signal
        kronos_raw = await get_kronos_signal(req.ticker)
        if kronos_raw:
            sig = kronos_raw["signal"]
            if sig == "WAIT":
                sig = "HOLD"
            enriched.append({
                "num":         len(enriched) + 1,
                "model":       "Kronos AI",
                "family":      "kronos",
                "provider":    "kronos",
                "ok":          True,
                "signal":      sig,
                "confidence":  kronos_raw["confidence"],
                "rationale":   kronos_raw["rationale"],
                "entry_price": kronos_raw.get("entry_price"),
                "stop_loss":   kronos_raw.get("stop_loss"),
                "target_1":    kronos_raw.get("target_1"),
                "target_2":    kronos_raw.get("target_2"),
                "target_3":    kronos_raw.get("target_3"),
                "weight":      1.0,
            })
    except Exception as e:
        logger.warning("Kronos skipped in full-analysis: %s", e)

    # Build quick consensus from successful votes
    ok_results = [r for r in enriched if r.get("ok") and r.get("signal") in ("BUY", "SELL", "HOLD")]
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for r in ok_results:
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1
    consensus = max(counts, key=counts.get) if ok_results else "HOLD"
    avg_conf = int(sum(r.get("confidence", 0) for r in ok_results) / len(ok_results)) if ok_results else 0

    # Surface budget-exceeded error clearly
    budget_errors = [r for r in enriched if "budget" in (r.get("error") or "").lower()]
    budget_warning = budget_errors[0].get("error") if budget_errors else None

    return {
        "success":       True,
        "ticker":        req.ticker,
        "models":        enriched,
        "total":         len(enriched),
        "successful":    len(ok_results),
        "consensus":     consensus,
        "avg_confidence": avg_conf,
        "vote_counts":   counts,
        "budget_warning": budget_warning,
    }


@ensemble_router.get("/full-analysis/models")
async def full_analysis_models():
    """Return list of all 45 models that full-analysis will call."""
    return {"models": ensemble_engine.ALL_45_MODELS, "count": len(ensemble_engine.ALL_45_MODELS)}


@ensemble_router.post("/gann-optimize")
async def gann_optimize(req: GannRequest):
    """AI-driven Gann + Square-of-9 optimisation."""
    return await gann_optimizer.ai_optimize_gann(req.ticker)


@ensemble_router.post("/ask")
async def ask_free(req: FreePrompt):
    """Free-form ensemble prompt (for power users / debugging)."""
    sys_msg = req.system_message or ensemble_engine.ENSEMBLE_SYSTEM_PROMPT
    return await ensemble_engine.ask_ensemble(req.user_text, system_message=sys_msg)
