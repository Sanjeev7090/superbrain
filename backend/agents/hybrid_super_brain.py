"""
Hybrid Super Brain — Central Decision Orchestrator v2
=====================================================
Combines all intelligence layers into one unified decision pipeline:

  1. MildSurvivalEngine     — Daily target discipline, fear/boost scalars (MongoDB-persisted)
  2. PsychologicalHarvester — FOMO, apathy, regime, narrative credibility
  3. StrategyCollaborator   — 6 parallel technical agents (Breakout, Momentum, Kronos…)
  4. MiroFish LangGraph     — 5-node LLM pipeline (Tech→Vol→Sentiment→Risk→Decision)
  5. MetaReasoner            — Synthesises all layers into asymmetric edge score
  6. DreamerV3 Coupling      — Pulls live confidence from running orchestrator
  7. RiskPortfolioManager    — Hard risk gate (heat, budget, circuit breakers)

Central audit trail saved to MongoDB collection ``hybrid_brain_audit``.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("hybrid_super_brain")

# ─── MongoDB (lazy) ───────────────────────────────────────────────────────────
_mongo_client: Optional[AsyncIOMotorClient] = None
_db = None


def _get_db():
    global _mongo_client, _db
    if _db is None:
        url  = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        name = os.environ.get("DB_NAME",   "trading_db")
        _mongo_client = AsyncIOMotorClient(url)
        _db = _mongo_client[name]
    return _db


# ─────────────────────────────────────────────────────────────────────────────
# 1) MILD SURVIVAL ENGINE  (MongoDB-persisted, grace period, overnight decay)
# ─────────────────────────────────────────────────────────────────────────────
class MildSurvivalEngine:
    """Tracks consecutive daily target misses → fear/boost scalars.
    Persists to MongoDB so fear survives restarts.
    """

    def __init__(self, daily_target_pct: float = 0.5, grace_days: int = 5):
        self.daily_target     = float(daily_target_pct) / 100.0
        self.grace_days       = grace_days
        self.consecutive_fail = 0
        self.fear_level       = 0.0
        self.last_pnl_pct     = 0.0
        self._loaded          = False

    async def load(self):
        if self._loaded:
            return
        try:
            doc = await _get_db().hybrid_brain_state.find_one({"_id": "survival"})
            if doc:
                self.consecutive_fail = int(doc.get("consecutive_fail", 0))
                self.fear_level       = float(doc.get("fear_level", 0.0))
                self.last_pnl_pct     = float(doc.get("last_pnl_pct", 0.0))
        except Exception as e:
            logger.warning("[Survival] load failed: %s", e)
        self._loaded = True

    async def persist(self):
        try:
            await _get_db().hybrid_brain_state.update_one(
                {"_id": "survival"},
                {"$set": {
                    "consecutive_fail": self.consecutive_fail,
                    "fear_level":       self.fear_level,
                    "last_pnl_pct":     self.last_pnl_pct,
                    "updated_at":       datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning("[Survival] persist failed: %s", e)

    def update(self, daily_pnl_pct: float) -> Dict[str, Any]:
        self.last_pnl_pct = float(daily_pnl_pct)
        if daily_pnl_pct >= self.daily_target:
            self.consecutive_fail = 0
            self.fear_level = max(0.0, self.fear_level - 0.25)
            return {
                "status": "good",
                "fear":   round(self.fear_level, 3),
                "boost":  1.4,
                "consecutive_fail": self.consecutive_fail,
                "target_pct": self.daily_target * 100,
                "actual_pct": daily_pnl_pct * 100,
            }
        else:
            self.consecutive_fail += 1
            ramp = 0.10 if self.consecutive_fail <= self.grace_days else 0.18
            self.fear_level = min(1.0, ramp * self.consecutive_fail)
            return {
                "status": "warning" if self.consecutive_fail < 3 else "danger",
                "fear":   round(self.fear_level, 3),
                "penalty": round(-6 * self.fear_level, 3),
                "consecutive_fail": self.consecutive_fail,
                "target_pct": self.daily_target * 100,
                "actual_pct": daily_pnl_pct * 100,
            }

    async def reset_daily(self):
        """
        New-day overnight decay — called automatically at midnight.
        Decays fear by 0.35 (natural overnight recovery), resets consecutive_fail.
        """
        self.consecutive_fail = 0
        self.fear_level = max(0.0, self.fear_level - 0.35)
        self.last_pnl_pct = 0.0
        await self.persist()

    async def manual_reset(self):
        """
        Manual full reset — called when user clicks 'Reset Brain Day' button.
        Completely clears fear level, consecutive fails, and PnL tracker.
        """
        self.consecutive_fail = 0
        self.fear_level = 0.0
        self.last_pnl_pct = 0.0
        await self.persist()


# ─────────────────────────────────────────────────────────────────────────────
# 2) PSYCHOLOGICAL HARVESTER  (real market data — FOMO, apathy, regime)
# ─────────────────────────────────────────────────────────────────────────────
_POS_NEWS = ("upgrade", "beat", "surge", "rally", "positive", "buy", "outperform",
             "expansion", "record high", "breakout", "bullish", "strong")
_NEG_NEWS = ("downgrade", "miss", "crash", "plunge", "negative", "sell", "underperform",
             "loss", "record low", "breakdown", "bearish", "fraud", "weak")


class PsychologicalHarvester:
    """Extracts FOMO / Apathy / Narrative credibility / Regime from real market data."""

    @staticmethod
    def _bounded(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, float(x)))

    def analyze(self, market_data: Dict[str, Any], news: str = "") -> Dict[str, Any]:
        m = market_data or {}

        momentum  = float(m.get("momentum_strength", m.get("momentum",
                          m.get("change_pct", 0.0) / 5.0 + 0.5)))
        momentum  = self._bounded(momentum)

        vol       = float(m.get("volatility_index", m.get("volatility", m.get("atr_pct", 0.015))))
        vol_thrust = float(m.get("volume_thrust", m.get("oi_bullish", 1.0)))

        # OI-weighted FOMO
        oi_factor = self._bounded(float(m.get("oi_bullish", 0.5))) * 0.25
        fomo      = self._bounded(0.25 * momentum + 1.4 * vol + 0.20 * (vol_thrust - 1.0) + oi_factor + 0.05)

        apathy    = self._bounded((1 - momentum) * 0.55 + max(0.0, 1.0 - vol_thrust) * 0.30)

        text      = (news or "").lower()
        pos_hits  = sum(1 for w in _POS_NEWS if w in text)
        neg_hits  = sum(1 for w in _NEG_NEWS if w in text)
        cred      = 0.50 if (pos_hits == 0 and neg_hits == 0) else \
                    self._bounded(0.5 + 0.08 * (pos_hits - neg_hits))

        if vol > 0.025 and momentum > 0.65:
            regime = "trending_up"
        elif vol > 0.025 and momentum < 0.35:
            regime = "trending_down"
        elif vol > 0.035:
            regime = "volatile"
        elif abs(momentum - 0.5) < 0.12:
            regime = "ranging"
        else:
            regime = "drift"

        if fomo > 0.7 and cred < 0.45:
            gap = "Overheated narrative — fade strength, watch options skew"
        elif apathy > 0.6 and cred > 0.55:
            gap = "Sleepy strength — accumulation zone, watch order blocks"
        elif regime in ("trending_up", "trending_down"):
            gap = "Trend intact — ride momentum, manage trailing stop"
        else:
            gap = "Mean-reversion bias — fade extremes within range"

        return {
            "fomo_score":            round(fomo, 3),
            "apathy_score":          round(apathy, 3),
            "narrative_credibility": round(cred, 3),
            "regime":                regime,
            "volatility":            round(vol, 4),
            "momentum":              round(momentum, 3),
            "volume_thrust":         round(vol_thrust, 3),
            "oi_bullish":            round(self._bounded(float(m.get("oi_bullish", 0.5))), 3),
            "hidden_value_gap":      gap,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3) META REASONER  (LangGraph 5-agent pipeline → asymmetric edge synthesis)
# ─────────────────────────────────────────────────────────────────────────────
class MetaReasoner:
    """
    Runs MiroFish LangGraph (Technical→Volume→Sentiment→Risk→Decision)
    and synthesises the final asymmetric edge score.
    Falls back gracefully when LLM is unavailable.
    """

    async def analyze(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        psych: Dict[str, Any],
        dreamer_conf: float,
        strategy_consensus: Optional[str] = None,
        strategy_conf: float = 50.0,
    ) -> Dict[str, Any]:
        miro_decision: Optional[Dict] = None

        # ── Try MiroFish LangGraph ───────────────────────────────────────────
        try:
            from mirofish_langgraph import get_mirofish_graph
            graph = get_mirofish_graph()

            # Prepare initial state for the 5-agent pipeline
            initial_state = {
                "ticker":    symbol,
                "bars":      market_data.get("bars", []),
                "news_text": market_data.get("news_text", ""),
                "indicators": {
                    "momentum":    psych.get("momentum", 0.5),
                    "volatility":  psych.get("volatility", 0.015),
                    "fomo":        psych.get("fomo_score", 0.5),
                    "dreamer_conf": dreamer_conf,
                },
            }
            # Run pipeline (ainvoke = non-streaming, returns final state)
            final_state = await asyncio.wait_for(
                graph.ainvoke(initial_state),
                timeout=20.0,
            )
            miro_decision = final_state.get("decision") or {}
            logger.info("[MetaReasoner] MiroFish result for %s: %s", symbol, miro_decision.get("signal"))
        except asyncio.TimeoutError:
            logger.warning("[MetaReasoner] MiroFish timed out for %s — using heuristic", symbol)
        except Exception as e:
            logger.debug("[MetaReasoner] MiroFish unavailable: %s", e)

        # ── Compute asymmetric edge score ────────────────────────────────────
        # Combine: dreamer + strategy + miro + psych regime
        miro_signal  = (miro_decision or {}).get("signal", "HOLD")
        miro_conf    = float((miro_decision or {}).get("confidence", dreamer_conf))
        miro_bullish = 1 if miro_signal == "BUY" else (-1 if miro_signal == "SELL" else 0)

        strategy_bullish = 1 if strategy_consensus == "BUY" else (-1 if strategy_consensus == "SELL" else 0)

        # Agreement between dreamer, miro, and strategy boosts edge
        signals = [
            1 if dreamer_conf > 60 else (-1 if dreamer_conf < 40 else 0),
            miro_bullish,
            strategy_bullish,
        ]
        agreement = sum(signals)  # -3 to +3

        asymmetric = (
            dreamer_conf > 62
            and psych["fomo_score"] > 0.55
            and psych["narrative_credibility"] > 0.5
            and agreement >= 2
        )

        # Confidence adjustment factor
        if agreement >= 2:
            conf_adjust = 1.25
        elif agreement <= -2:
            conf_adjust = 0.70
        elif asymmetric:
            conf_adjust = 1.15
        else:
            conf_adjust = 0.90

        recommendation = (
            "BUY" if agreement >= 2 and dreamer_conf > 58
            else "SELL" if agreement <= -2 and dreamer_conf < 42
            else "HOLD"
        )

        return {
            "asymmetric_edge":    asymmetric,
            "agreement_score":    agreement,
            "confidence_adjust":  round(conf_adjust, 3),
            "recommendation":     recommendation,
            "miro_signal":        miro_signal,
            "miro_confidence":    round(miro_conf, 1),
            "miro_raw":           miro_decision,
            "strategy_consensus": strategy_consensus,
            "summary": (
                f"Dreamer {dreamer_conf:.0f}% · MiroFish {miro_signal} · "
                f"Strategy {strategy_consensus or 'N/A'} · "
                f"FOMO {psych['fomo_score']:.2f} · Regime {psych['regime']}"
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4) HYBRID SUPER BRAIN  (Central Orchestrator)
# ─────────────────────────────────────────────────────────────────────────────
class HybridSuperBrain:
    """Central Brain — Orchestrates ALL intelligence layers into ONE decision."""

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.config  = cfg
        self.survival = MildSurvivalEngine(
            daily_target_pct=float(cfg.get("daily_target_pct", 0.5)),
            grace_days=int(cfg.get("grace_days", 5)),
        )
        self.psych   = PsychologicalHarvester()
        self.meta    = MetaReasoner()
        self.current_pnl_pct  = 0.0
        self._decision_cache: Dict[str, tuple] = {}   # symbol → (decision, ts)
        self._cache_ttl = 60.0
        self.brain_enabled: bool = True  # ON/OFF toggle

    # ── Public API ────────────────────────────────────────────────────────────

    async def think_and_decide(
        self,
        market_data: Dict[str, Any],
        news: str = "",
        symbol: str = "NIFTY",
        dreamer_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Full Central Decision Pipeline:
          1. Load survival state
          2. Psychological analysis
          3. StrategyCollaborator (6 agents parallel)
          4. MiroFish LangGraph (5-node LLM pipeline)
          5. MetaReasoner synthesis
          6. DreamerV3 confidence (live)
          7. Survival pressure
          8. Hybrid scoring engine
          9. Risk gate (RPM heat check)
         10. Audit trail
        """
        await self.survival.load()

        # Cache check
        now = datetime.now(timezone.utc).timestamp()
        cached = self._decision_cache.get(symbol)
        if cached and (now - cached[1] < self._cache_ttl):
            return {**cached[0], "cached": True}

        # ── Step 1: Psychology ──────────────────────────────────────────────
        # Enrich market_data with news text for miro
        md = dict(market_data or {})
        md.setdefault("news_text", news)
        psych = self.psych.analyze(md, news)

        # ── Step 2: DreamerV3 confidence ────────────────────────────────────
        if dreamer_confidence is None:
            dreamer_confidence = await self._fetch_dreamer_confidence(symbol)
        base_conf = float(dreamer_confidence)

        # ── Step 3: StrategyCollaborator (6 parallel agents) ─────────────────
        strategy_consensus, strategy_conf, strategy_agents = await self._run_strategy(symbol)

        # ── Step 4 & 5: MetaReasoner (MiroFish LangGraph inside) ────────────
        meta = await self.meta.analyze(
            symbol=symbol,
            market_data=md,
            psych=psych,
            dreamer_conf=base_conf,
            strategy_consensus=strategy_consensus,
            strategy_conf=strategy_conf,
        )

        # ── Step 6: Survival pressure ────────────────────────────────────────
        survival = self.survival.update(self.current_pnl_pct)

        # ── Step 7: Hybrid scoring engine ───────────────────────────────────
        decision = self._hybrid_engine(
            dreamer={"confidence": base_conf},
            psych=psych,
            meta=meta,
            survival=survival,
            market_data=md,
        )
        # Preserve symbol + id for audit trail
        decision.setdefault("id", str(uuid4()))
        decision.setdefault("symbol", symbol)

        # ── Step 8: Risk gate ────────────────────────────────────────────────
        risk_ok, risk_reason = await self._check_risk_gate()
        decision["risk_ok"]     = risk_ok
        decision["risk_reason"] = risk_reason
        if not risk_ok and decision["action"] != "HOLD":
            decision["action"]  = "HOLD"
            decision["reasoning"] += f" | RISK-GATE: {risk_reason}"

        # ── Step 9: Attach enriched context ─────────────────────────────────
        decision["strategy_agents"]    = strategy_agents
        decision["strategy_consensus"] = strategy_consensus
        decision["strategy_conf"]      = round(strategy_conf, 1)
        decision["miro_signal"]        = meta.get("miro_signal")
        decision["miro_confidence"]    = meta.get("miro_confidence")
        decision["meta_summary"]       = meta.get("summary")
        decision["agreement_score"]    = meta.get("agreement_score", 0)

        self._decision_cache[symbol] = (decision, now)
        await self._audit(decision)
        await self.survival.persist()

        return {**decision, "cached": False}

    # ── Hybrid Scoring Engine ─────────────────────────────────────────────────
    def _hybrid_engine(self, dreamer, psych, meta, survival, market_data) -> Dict:
        base_conf = dreamer.get('confidence', 50)
        final_conf = (base_conf * 0.45) + (psych["fomo_score"] * 15) - (survival["fear"] * 12)
        final_conf *= meta.get("confidence_adjust", 1.0)
        final_conf = max(20, min(95, final_conf))

        # High Quality Filter (Accuracy Protector)
        quality_score = (
            market_data.get('deltadash_total', 0) * 0.4 +
            market_data.get('oi_score', 50) * 0.3 +
            (1 if abs(market_data.get('parity_mispricing', 0)) < 1.2 else 0) * 30
        )

        if final_conf > 58 and quality_score >= 65:
            action = "BUY"
        elif final_conf < 40 and quality_score >= 65:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "action": action,
            "confidence": round(final_conf, 1),
            "quality_score": round(quality_score, 1),
            "reasoning": f"Conf:{final_conf:.1f} | Delta:{market_data.get('deltadash_total',0)} | Quality:{quality_score:.1f}",
            "psych":    psych,
            "survival": survival,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    # ── StrategyCollaborator Runner ───────────────────────────────────────────
    async def _run_strategy(self, symbol: str):
        """Run StrategyCollaborator in thread pool (sync → async)."""
        try:
            from .strategy_collaborator import StrategyCollaborator
            collab = StrategyCollaborator()
            loop = asyncio.get_event_loop()
            disc = await asyncio.wait_for(
                loop.run_in_executor(None, collab.run_discussion, symbol, 100_000.0, "moderate", False),
                timeout=15.0,
            )
            consensus    = disc.consensus or "HOLD"
            conf         = float(disc.consensus_confidence or 50.0)
            # Build compact agent summary
            agents = []
            for sig in (disc.agent_signals or []):
                if hasattr(sig, 'agent_name'):
                    agents.append({
                        "agent":      sig.agent_name,
                        "signal":     sig.signal,
                        "confidence": sig.confidence,
                    })
                elif isinstance(sig, dict):
                    agents.append({
                        "agent":      sig.get("agent_name", sig.get("agent", "?")),
                        "signal":     sig.get("signal", "HOLD"),
                        "confidence": sig.get("confidence", 0),
                    })
            return consensus, conf, agents
        except asyncio.TimeoutError:
            logger.warning("[HybridBrain] StrategyCollaborator timed out for %s", symbol)
        except Exception as e:
            logger.debug("[HybridBrain] StrategyCollaborator failed: %s", e)
        return "HOLD", 50.0, []

    # ── DreamerV3 Confidence Puller ───────────────────────────────────────────
    async def _fetch_dreamer_confidence(self, symbol: str) -> float:
        try:
            from .dreamer_robo_orchestrator import get_robo_state
            state = get_robo_state() or {}
            conf  = state.get("last_confidence") or state.get("confidence") or 60.0
            return float(conf)
        except Exception:
            return 60.0

    # ── Risk Gate (RPM heat check) ────────────────────────────────────────────
    async def _check_risk_gate(self):
        """Returns (trade_ok: bool, reason: str)."""
        try:
            from .risk_portfolio_manager import rpm
            if rpm.is_heat_exceeded():
                return False, "Portfolio heat exceeded — wait for positions to close"
            rp = getattr(rpm, 'last_risk_profile', None) or {}
            if rp.get("risk_budget_state") == "STOP":
                return False, "Daily risk budget exhausted"
            if rp.get("should_stop_trading"):
                return False, "Circuit-breaker: max daily loss hit"
        except Exception as e:
            logger.debug("[HybridBrain] RPM gate check failed: %s", e)
        return True, "OK"

    # ── Audit Trail ───────────────────────────────────────────────────────────
    async def _audit(self, decision: Dict[str, Any]):
        try:
            doc = {k: v for k, v in decision.items() if k != "miro_raw"}
            doc["_id"] = decision["id"]
            await _get_db().hybrid_brain_audit.insert_one(doc)
        except Exception as e:
            logger.debug("[HybridBrain] audit insert failed: %s", e)

    async def get_recent_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            cursor = _get_db().hybrid_brain_audit.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.warning("[HybridBrain] audit fetch failed: %s", e)
            return []

    # ── PnL / Day management ─────────────────────────────────────────────────
    async def update_daily_pnl(self, pnl_pct: float):
        self.current_pnl_pct = float(pnl_pct)
        await self.survival.load()
        self.survival.update(self.current_pnl_pct)
        await self.survival.persist()

    async def reset_for_new_day(self, manual: bool = False):
        """Reset brain for a new trading day.
        
        manual=True  → full fear clear (user clicked 'Reset Brain Day')
        manual=False → overnight decay (called automatically at midnight)
        """
        await self.survival.load()
        if manual:
            await self.survival.manual_reset()
        else:
            await self.survival.reset_daily()
        self.current_pnl_pct = 0.0

    # ── Sync helpers (used by orchestrator worker thread) ────────────────────
    def decide_sync(
        self,
        market_data: Dict[str, Any],
        news: str = "",
        symbol: str = "NIFTY",
        dreamer_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for thread-based callers (dreamer_robo_orchestrator)."""
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    raise RuntimeError("running loop")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                self.think_and_decide(
                    market_data=market_data,
                    news=news,
                    symbol=symbol,
                    dreamer_confidence=dreamer_confidence,
                )
            )
        except Exception as e:
            logger.warning("[HybridBrain] decide_sync failed: %s", e)
            return {
                "action": "HOLD",
                "confidence": float(dreamer_confidence or 50.0),
                "size_scalar": 1.0,
                "psych": {}, "survival": {"fear": 0.0, "status": "good"},
                "reasoning": f"hybrid brain unavailable: {e}",
                "error": str(e),
                "components": {}, "risk_alert": "good",
            }

    def update_daily_pnl_sync(self, pnl_pct: float):
        """Sync wrapper used by orchestrator worker every tick."""
        self.current_pnl_pct = float(pnl_pct)


# ─── Singleton ────────────────────────────────────────────────────────────────
hybrid_brain = HybridSuperBrain()
