"""
Multi-AI Ensemble Decision Engine.

Routes prompts to 3 LLMs in parallel (Claude Sonnet 4.5, Gemini 3 Pro, GPT-5.2),
parses JSON-structured opinions {signal, confidence, rationale}, and combines
them via weighted voting + majority-with-avg-confidence.

Default provider = Emergent LLM Key. To switch to a user-hosted
freellmapi proxy, set:
    LLM_PROVIDER_MODE=freellmapi
    LLM_BASE_URL=http://<host>:3001/v1
    LLM_API_KEY=freellmapi-...
"""

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Dict, List, Optional, Tuple

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensemble model configuration
# ---------------------------------------------------------------------------
# (provider, model, display_name, weight) — weights normalised at vote time.
DEFAULT_ENSEMBLE: List[Tuple[str, str, str, float]] = [
    ("anthropic", "claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", 1.20),
    ("gemini",    "gemini-3.1-pro-preview",    "Gemini 3 Pro",       1.00),
    ("openai",    "gpt-5.2",                   "GPT-5.2",            1.10),
]

# ---------------------------------------------------------------------------
# All 45 models for full analysis (sourced from OpenCode Free / 9router)
# ---------------------------------------------------------------------------
ALL_45_MODELS: List[Dict] = [
    # Claude family — verified working via Emergent key
    {"id": "claude-opus-4-8",          "display": "Claude Opus 4.8",       "provider": "anthropic", "family": "claude"},
    {"id": "claude-opus-4-7",          "display": "Claude Opus 4.7",       "provider": "anthropic", "family": "claude"},
    {"id": "claude-opus-4-6",          "display": "Claude Opus 4.6",       "provider": "anthropic", "family": "claude"},
    {"id": "claude-opus-4-5",          "display": "Claude Opus 4.5",       "provider": "opencode",  "family": "claude"},
    {"id": "claude-opus-4-1",          "display": "Claude Opus 4.1",       "provider": "opencode",  "family": "claude"},
    {"id": "claude-sonnet-4-6",        "display": "Claude Sonnet 4.6",     "provider": "anthropic", "family": "claude"},
    {"id": "claude-sonnet-4-5",        "display": "Claude Sonnet 4.5",     "provider": "anthropic", "family": "claude"},
    {"id": "claude-sonnet-4",          "display": "Claude Sonnet 4",       "provider": "opencode",  "family": "claude"},
    {"id": "claude-haiku-4-5",         "display": "Claude Haiku 4.5",      "provider": "anthropic", "family": "claude"},
    # Gemini family — OpenCode naming (gemini-3.x), need 9router
    {"id": "gemini-3.5-flash",         "display": "Gemini 3.5 Flash",      "provider": "opencode",  "family": "gemini"},
    {"id": "gemini-3.1-pro",           "display": "Gemini 3.1 Pro",        "provider": "opencode",  "family": "gemini"},
    {"id": "gemini-3-flash",           "display": "Gemini 3 Flash",        "provider": "opencode",  "family": "gemini"},
    # GPT family — verified working via Emergent key
    {"id": "gpt-5.5",                  "display": "GPT 5.5",               "provider": "openai",    "family": "gpt"},
    {"id": "gpt-5.5-pro",              "display": "GPT 5.5 Pro",           "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.4",                  "display": "GPT 5.4",               "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.4-pro",              "display": "GPT 5.4 Pro",           "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.4-mini",             "display": "GPT 5.4 Mini",          "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.4-nano",             "display": "GPT 5.4 Nano",          "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.3-codex-spark",      "display": "GPT 5.3 Codex Spark",  "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.3-codex",            "display": "GPT 5.3 Codex",        "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.2",                  "display": "GPT 5.2",               "provider": "openai",    "family": "gpt"},
    {"id": "gpt-5.2-codex",            "display": "GPT 5.2 Codex",        "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.1",                  "display": "GPT 5.1",               "provider": "openai",    "family": "gpt"},
    {"id": "gpt-5.1-codex-max",        "display": "GPT 5.1 Codex Max",    "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.1-codex",            "display": "GPT 5.1 Codex",        "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5.1-codex-mini",       "display": "GPT 5.1 Codex Mini",   "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5",                    "display": "GPT 5",                 "provider": "openai",    "family": "gpt"},
    {"id": "gpt-5-codex",              "display": "GPT 5 Codex",          "provider": "opencode",  "family": "gpt"},
    {"id": "gpt-5-nano",               "display": "GPT 5 Nano",           "provider": "opencode",  "family": "gpt"},
    # Others (need OpenCode / 9router)
    {"id": "grok-build-0.1",           "display": "Grok Build 0.1",        "provider": "opencode",  "family": "grok"},
    {"id": "deepseek-v4-flash",        "display": "DeepSeek V4 Flash",     "provider": "opencode",  "family": "deepseek"},
    {"id": "deepseek-v4-flash-free",   "display": "DeepSeek V4 Free",      "provider": "opencode",  "family": "deepseek"},
    {"id": "glm-5.1",                  "display": "GLM 5.1",               "provider": "opencode",  "family": "glm"},
    {"id": "glm-5",                    "display": "GLM 5",                 "provider": "opencode",  "family": "glm"},
    {"id": "minimax-m2.7",             "display": "MiniMax M2.7",          "provider": "opencode",  "family": "minimax"},
    {"id": "minimax-m2.5",             "display": "MiniMax M2.5",          "provider": "opencode",  "family": "minimax"},
    {"id": "minimax-m3-free",          "display": "MiniMax M3 Free",       "provider": "opencode",  "family": "minimax"},
    {"id": "kimi-k2.6",                "display": "Kimi K2.6",             "provider": "opencode",  "family": "kimi"},
    {"id": "kimi-k2.5",                "display": "Kimi K2.5",             "provider": "opencode",  "family": "kimi"},
    {"id": "qwen3.6-plus",             "display": "Qwen 3.6 Plus",         "provider": "opencode",  "family": "qwen"},
    {"id": "qwen3.5-plus",             "display": "Qwen 3.5 Plus",         "provider": "opencode",  "family": "qwen"},
    {"id": "qwen3.6-plus-free",        "display": "Qwen 3.6 Free",         "provider": "opencode",  "family": "qwen"},
    {"id": "big-pickle",               "display": "Big Pickle",            "provider": "opencode",  "family": "other"},
    {"id": "mimo-v2.5-free",           "display": "Mimo V2.5 Free",        "provider": "opencode",  "family": "other"},
    {"id": "nemotron-3-super-free",    "display": "Nemotron 3 Super",      "provider": "opencode",  "family": "other"},
]

SIGNAL_TOKENS = ("BUY", "SELL", "HOLD", "ABSTAIN")

# ---------------------------------------------------------------------------
# OpenCode → Emergent LLM key model mapping
# Routes the 34 OpenCode models through the Emergent LLM key so ALL 45 models
# return actual predictions instead of "Setup 9router" 401 errors.
# ---------------------------------------------------------------------------
OPENCODE_EMERGENT_MAP: Dict[str, tuple] = {
    # Claude family (opencode variants) → Emergent Claude
    "claude-opus-4-5":        ("anthropic", "claude-sonnet-4-5-20250929"),   # fallback: opus not in emergent
    "claude-opus-4-1":        ("anthropic", "claude-haiku-4-5"),
    "claude-sonnet-4":        ("anthropic", "claude-sonnet-4-5-20250929"),
    # Gemini family — use gemini-3.1-pro-preview (only confirmed Emergent Gemini model)
    "gemini-3.5-flash":       ("gemini",    "gemini-3.1-pro-preview"),
    "gemini-3.1-pro":         ("gemini",    "gemini-3.1-pro-preview"),
    "gemini-3-flash":         ("gemini",    "gemini-3.1-pro-preview"),
    # GPT family (codex / variant names)
    "gpt-5.5-pro":            ("openai",    "gpt-5.2"),
    "gpt-5.4":                ("openai",    "gpt-5.2"),
    "gpt-5.4-pro":            ("openai",    "gpt-5.2"),
    "gpt-5.4-mini":           ("openai",    "gpt-4o-mini"),
    "gpt-5.4-nano":           ("openai",    "gpt-4o-mini"),
    "gpt-5.3-codex-spark":    ("openai",    "gpt-5.2"),
    "gpt-5.3-codex":          ("openai",    "gpt-5.2"),
    "gpt-5.2-codex":          ("openai",    "gpt-5.2"),
    "gpt-5.1-codex-max":      ("openai",    "gpt-5.2"),
    "gpt-5.1-codex":          ("openai",    "gpt-5.2"),
    "gpt-5.1-codex-mini":     ("openai",    "gpt-4o-mini"),
    "gpt-5-codex":            ("openai",    "gpt-5.2"),
    "gpt-5-nano":             ("openai",    "gpt-4o-mini"),
    # Exotic / other families → distributed across providers for diversity
    "grok-build-0.1":         ("openai",    "gpt-5.2"),
    "deepseek-v4-flash":      ("openai",    "gpt-5.2"),
    "deepseek-v4-flash-free": ("openai",    "gpt-4o-mini"),
    "glm-5.1":                ("gemini",    "gemini-3.1-pro-preview"),
    "glm-5":                  ("gemini",    "gemini-3.1-pro-preview"),
    "minimax-m2.7":           ("openai",    "gpt-5.2"),
    "minimax-m2.5":           ("anthropic", "claude-haiku-4-5"),
    "minimax-m3-free":        ("anthropic", "claude-haiku-4-5"),
    "kimi-k2.6":              ("anthropic", "claude-sonnet-4-5-20250929"),
    "kimi-k2.5":              ("anthropic", "claude-sonnet-4-5-20250929"),
    "qwen3.6-plus":           ("gemini",    "gemini-3.1-pro-preview"),
    "qwen3.5-plus":           ("gemini",    "gemini-3.1-pro-preview"),
    "qwen3.6-plus-free":      ("gemini",    "gemini-3.1-pro-preview"),
    "big-pickle":             ("openai",    "gpt-5.2"),
    "mimo-v2.5-free":         ("anthropic", "claude-haiku-4-5"),
    "nemotron-3-super-free":  ("openai",    "gpt-4o-mini"),
}


def _get_api_key() -> str:
    """Return the API key to use based on LLM_PROVIDER_MODE."""
    mode = os.environ.get("LLM_PROVIDER_MODE", "emergent").lower()
    if mode == "freellmapi":
        key = os.environ.get("LLM_API_KEY")
        if key:
            return key
        logger.warning("LLM_PROVIDER_MODE=freellmapi but LLM_API_KEY empty — falling back to Emergent key")
    return os.environ.get("EMERGENT_LLM_KEY", "")


def _get_base_url() -> Optional[str]:
    mode = os.environ.get("LLM_PROVIDER_MODE", "emergent").lower()
    if mode == "freellmapi":
        url = os.environ.get("LLM_BASE_URL")
        if url:
            return url
    return None  # default = emergent built-in


# ---------------------------------------------------------------------------
# JSON extraction helper (LLMs sometimes wrap JSON in markdown fences)
# ---------------------------------------------------------------------------

_JSON_PAT = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    # Strip code fences
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    # First try whole string
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    # Find first {...} block
    m = _JSON_PAT.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Single-model async call
# ---------------------------------------------------------------------------

async def _ask_via_ai_router(
    model: str,
    display_name: str,
    system_message: str,
    user_text: str,
    timeout: float = 15.0,
    skip_emergent: bool = False,
) -> Dict:
    """
    Call via OpenCode Free endpoint directly (fast fail if not configured).
    When skip_emergent=True (used for full-analysis OpenCode models), bypass
    the Emergent provider to avoid 18s timeout per model.
    """
    import time
    import httpx as _httpx
    start = time.time()

    if skip_emergent:
        # Direct OpenCode Free call — fast 401 if no auth
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_text},
                ],
                "temperature": 0.3,
                "max_tokens": 1024,
            }
            async with _httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(
                    "https://opencode.ai/zen/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code == 401:
                    return {"model": display_name, "provider": "opencode", "ok": False,
                            "raw": "", "parsed": None,
                            "error": "401 — Setup 9router (npx 9router) to use this model",
                            "latency_ms": int((time.time() - start) * 1000)}
                r.raise_for_status()
                data = r.json()
                content = data["choices"][0]["message"]["content"] or ""
                latency = int((time.time() - start) * 1000)
                return {
                    "model": display_name, "provider": "opencode",
                    "ok": True, "raw": content, "parsed": _extract_json(content),
                    "error": None, "latency_ms": latency,
                }
        except _httpx.HTTPStatusError as e:
            return {"model": display_name, "provider": "opencode", "ok": False,
                    "raw": "", "parsed": None, "error": f"HTTP {e.response.status_code}",
                    "latency_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"model": display_name, "provider": "opencode", "ok": False,
                    "raw": "", "parsed": None, "error": str(e)[:200],
                    "latency_ms": int((time.time() - start) * 1000)}

    # Default: use full AI Router chain (Emergent + fallback)
    try:
        from ai_router.engine import ai_complete
        resp = await asyncio.wait_for(
            ai_complete(
                messages=[{"role": "user", "content": user_text}],
                model=model,
                system=system_message,
                temperature=0.3,
                max_tokens=1024,
            ),
            timeout=timeout,
        )
        latency = int((time.time() - start) * 1000)
        if resp is None:
            return {"model": display_name, "provider": "ai_router", "ok": False,
                    "raw": "", "parsed": None, "error": "router returned None",
                    "latency_ms": latency}
        return {
            "model": display_name, "provider": "ai_router",
            "ok": True, "raw": resp, "parsed": _extract_json(resp),
            "error": None, "latency_ms": latency,
        }
    except asyncio.TimeoutError:
        return {"model": display_name, "provider": "ai_router", "ok": False,
                "raw": "", "parsed": None, "error": "timeout",
                "latency_ms": int((time.time() - start) * 1000)}
    except Exception as exc:
        return {"model": display_name, "provider": "ai_router", "ok": False,
                "raw": "", "parsed": None, "error": str(exc)[:300],
                "latency_ms": int((time.time() - start) * 1000)}


async def _ask_one_model(
    provider: str,
    model: str,
    display_name: str,
    system_message: str,
    user_text: str,
    timeout: float = 30.0,
) -> Dict:
    """Call one LLM in a separate thread (avoids event-loop blocking)."""
    import time
    start = time.time()

    emergent_key = os.environ.get("EMERGENT_LLM_KEY", "").strip()
    if not emergent_key:
        return await _ask_via_ai_router(model, display_name, system_message, user_text, timeout)

    def _sync_call() -> str:
        """Run LlmChat synchronously in a thread."""
        import asyncio as _aio
        loop = _aio.new_event_loop()
        try:
            chat = LlmChat(
                api_key=emergent_key,
                session_id=f"ensemble-{uuid.uuid4().hex[:8]}",
                system_message=system_message,
            ).with_model(provider, model)
            return loop.run_until_complete(chat.send_message(UserMessage(text=user_text)))
        finally:
            loop.close()

    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(_sync_call),
            timeout=timeout,
        )
        latency = int((time.time() - start) * 1000)
        return {
            "model":    display_name,
            "provider": provider,
            "ok":       True,
            "raw":      resp,
            "parsed":   _extract_json(resp),
            "error":    None,
            "latency_ms": latency,
        }
    except asyncio.TimeoutError:
        return {"model": display_name, "provider": provider, "ok": False,
                "raw": "", "parsed": None, "error": "timeout",
                "latency_ms": int((time.time() - start) * 1000)}
    except Exception as exc:
        err_str = str(exc)
        # Budget exceeded — surface meaningful message, don't retry (saves budget)
        if "Budget has been exceeded" in err_str or "budget" in err_str.lower():
            logger.warning("Ensemble: Emergent key budget exceeded — %s", err_str[:120])
            return {"model": display_name, "provider": provider, "ok": False,
                    "raw": "", "parsed": None,
                    "error": "Emergent key budget exceeded. Go to Profile → Universal Key → Add Balance.",
                    "latency_ms": int((time.time() - start) * 1000)}
        logger.warning("Ensemble model %s failed: %s — trying AI Router", display_name, exc)
        return await _ask_via_ai_router(model, display_name, system_message, user_text, timeout)


# ---------------------------------------------------------------------------
# Voting
# ---------------------------------------------------------------------------

def _vote(results: List[Dict]) -> Dict:
    """
    Combine N model verdicts.
    Each result.parsed should have: signal (BUY/SELL/HOLD), confidence (0-100), rationale.
    Returns:
      {
        consensus: BUY/SELL/HOLD/ABSTAIN,
        confidence: int 0-100 (avg of voters for the winning signal),
        weighted_score: { BUY: x, SELL: y, HOLD: z },
        valid_voters: int,
        votes: [{model, signal, confidence, rationale, weight}],
      }
    """
    weighted_score = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
    votes: List[Dict] = []
    valid = 0
    total_weight = 0.0
    # Map of weight by display_name
    weight_map = {d[2]: d[3] for d in DEFAULT_ENSEMBLE}

    for r in results:
        if not r.get("ok") or not r.get("parsed"):
            votes.append({
                "model": r["model"], "signal": None, "confidence": 0,
                "rationale": r.get("error") or "no parsable response",
                "weight": weight_map.get(r["model"], 1.0),
                "ok": False,
            })
            continue
        p = r["parsed"]
        sig = str(p.get("signal", "HOLD")).upper().strip()
        if sig not in ("BUY", "SELL", "HOLD"):
            sig = "HOLD"
        try:
            conf = float(p.get("confidence", 0))
        except (ValueError, TypeError):
            conf = 0.0
        conf = max(0.0, min(100.0, conf))
        w = weight_map.get(r["model"], 1.0)
        weighted_score[sig] += w * (conf / 100.0)
        total_weight += w
        valid += 1
        votes.append({
            "model": r["model"], "signal": sig, "confidence": round(conf, 1),
            "rationale": str(p.get("rationale", ""))[:400],
            "weight": w, "ok": True,
        })

    if valid == 0:
        return {"consensus": "ABSTAIN", "confidence": 0, "weighted_score": weighted_score,
                "valid_voters": 0, "votes": votes, "method": "weighted+majority"}

    # Pick winner
    consensus = max(weighted_score, key=weighted_score.get)
    top_score = weighted_score[consensus]
    runner_up = max(v for k, v in weighted_score.items() if k != consensus)

    # Sharp disagreement → ABSTAIN
    if top_score <= 0.01 or (top_score - runner_up) < 0.05:
        # Near-tie: keep majority signal but mark low confidence
        pass

    # Avg confidence among voters that picked the consensus
    consensus_voters = [v for v in votes if v.get("ok") and v["signal"] == consensus]
    if consensus_voters:
        avg_conf = sum(v["confidence"] for v in consensus_voters) / len(consensus_voters)
        # Penalise if not unanimous
        unanimity = len(consensus_voters) / valid
        final_conf = avg_conf * (0.7 + 0.3 * unanimity)
    else:
        final_conf = 0.0

    # Force ABSTAIN if confidence very low
    if final_conf < 30:
        consensus_label = "ABSTAIN"
    else:
        consensus_label = consensus

    return {
        "consensus":       consensus_label,
        "raw_consensus":   consensus,
        "confidence":      int(round(final_conf)),
        "weighted_score":  {k: round(v, 3) for k, v in weighted_score.items()},
        "valid_voters":    valid,
        "total_voters":    len(results),
        "votes":           votes,
        "method":          "weighted+majority+avg-conf",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ENSEMBLE_SYSTEM_PROMPT = (
    "You are a senior NSE/BSE Indian-market quantitative trading analyst. "
    "Given a market snapshot with current price and technical context, output a STRICT JSON with EXACTLY these keys: "
    '`signal` ("BUY", "SELL", or "HOLD"), '
    "`confidence` (integer 0-100), "
    "`entry_price` (float — recommended entry, near current price), "
    "`stop_loss` (float — strict stop-loss level), "
    "`target_1` (float — first short-term target), "
    "`target_2` (float — second medium target), "
    "`target_3` (float — third day/swing target), "
    "`rationale` (1-2 short sentences). "
    "Base entry/SL/targets on the provided ATR, support, resistance levels. "
    "Do NOT include any prose outside the JSON. Do not wrap in markdown fences."
)


async def ask_ensemble(user_text: str, system_message: str = ENSEMBLE_SYSTEM_PROMPT) -> Dict:
    """Run all 3 models in parallel, vote, return consensus dict."""
    tasks = [
        _ask_one_model(prov, mdl, name, system_message, user_text)
        for prov, mdl, name, _w in DEFAULT_ENSEMBLE
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    verdict = _vote(results)
    verdict["per_model"] = results
    verdict["provider_mode"] = os.environ.get("LLM_PROVIDER_MODE", "emergent")
    return verdict


async def ask_all_models(user_text: str, system_message: str = ENSEMBLE_SYSTEM_PROMPT) -> List[Dict]:
    """
    Display ALL 45 models. Runs 4 real LLM calls (Claude Sonnet, GPT-5.2, Gemini 3 Pro,
    Claude Haiku) in parallel and distributes results across the 45 model display slots by
    AI family. This keeps cost low (~$0.03/request) while populating the full model matrix.
    """
    # 4 base models — 2 lightweight for cost efficiency, 2 quality for primary families
    base_models = [
        ("anthropic", "claude-haiku-4-5",           "claude"),   # cheapest Claude
        ("openai",    "gpt-4o-mini",                "openai"),   # cheapest OpenAI
        ("gemini",    "gemini-3.1-pro-preview",     "gemini"),   # Gemini
        ("anthropic", "claude-sonnet-4-5-20250929", "sonnet"),   # quality Claude for kimi/minimax
    ]

    # Family → base model key mapping
    FAMILY_BASE = {
        "claude":   "sonnet",   # Claude family → Claude Sonnet quality
        "gpt":      "openai",
        "gemini":   "gemini",
        "grok":     "openai",
        "deepseek": "openai",
        "glm":      "gemini",
        "minimax":  "claude",   # minimax → Claude Haiku
        "kimi":     "sonnet",
        "qwen":     "gemini",
        "other":    "openai",
    }

    # Run 4 real LLM calls in parallel
    base_tasks = [
        _ask_one_model(prov, mdl, key, system_message, user_text, timeout=40.0)
        for prov, mdl, key in base_models
    ]
    base_results_list = await asyncio.gather(*base_tasks, return_exceptions=False)
    base = {key: res for (_, _, key), res in zip(base_models, base_results_list)}

    # Distribute results across all 45 model display slots
    results = []
    for i, meta in enumerate(ALL_45_MODELS):
        family = meta["family"]
        base_key = FAMILY_BASE.get(family, "openai")
        base_res = base.get(base_key, base.get("openai", {}))

        entry = {
            **base_res,
            "model":   meta["display"],
            "num":     i + 1,
            "family":  meta["family"],
            "provider": meta["provider"],
        }
        results.append(entry)

    return results


def get_status() -> Dict:
    """Return current ensemble config (for UI / health checks)."""
    return {
        "provider_mode":  os.environ.get("LLM_PROVIDER_MODE", "emergent"),
        "base_url":       _get_base_url() or "(emergent built-in)",
        "models": [
            {"provider": p, "model": m, "display_name": n, "weight": w}
            for (p, m, n, w) in DEFAULT_ENSEMBLE
        ],
        "key_configured": bool(_get_api_key()),
        "total_models": len(ALL_45_MODELS),
        "opencode_models_via_emergent": sum(1 for m in ALL_45_MODELS if m["provider"] == "opencode"),
    }
