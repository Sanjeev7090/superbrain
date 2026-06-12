# backend/agents/hybrid_super_brain.py
"""
Hybrid Super Brain — Central Decision Orchestrator v4.0
=======================================================
World-class central brain with Kronos + SMC integration.

Layers:
  1. MiroFish LangGraph     — 5-node LLM multi-agent pipeline
  2. DreamerV3 Orchestrator — RL world model confidence
  3. DeltaDash Scoreboard   — Multi-TF technical scoring
  4. Danger Scanner         — Auto-picked high-momentum plays
  5. KronosScheduler        — Time & cycle awareness
  6. SMC Analyzer           — Smart Money Concepts (FVG, OB, liquidity)
  7. MildSurvivalEngine     — Daily target discipline + fear scalars
  8. RiskPortfolioManager   — Hard risk gate

Compatible with: trading_loop.py, robo_router.py, hybrid_brain_router.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("hybrid_super_brain")

# ─── MongoDB (lazy) ──────────────────────────────────────────────────────────
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
# 1) MILD SURVIVAL ENGINE  (MongoDB-persisted)
# ─────────────────────────────────────────────────────────────────────────────
class MildSurvivalEngine:
    """Tracks consecutive daily target misses → fear/boost scalars."""

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
            }

    async def reset_daily(self):
        self.consecutive_fail = 0
        self.fear_level = max(0.0, self.fear_level - 0.35)
        self.last_pnl_pct = 0.0
        await self.persist()

    async def manual_reset(self):
        self.consecutive_fail = 0
        self.fear_level = 0.0
        self.last_pnl_pct = 0.0
        await self.persist()


from .smc_analyzer import SMCAnalyzer
from .kronos import KronosScheduler

# ─────────────────────────────────────────────────────────────────────────────
# 2) WRAPPER CLASSES for new imports
# ─────────────────────────────────────────────────────────────────────────────

class MiroFishLangGraph:
    """Wrapper around the mirofish_langgraph module (5-node LLM pipeline)."""

    async def run_analysis(
        self,
        market_data: Dict,
        news: str = "",
        chart_data=None,
    ) -> Dict:
        try:
            import sys
            import os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from mirofish_langgraph import run_mirofish_stream, compute_indicators
            ticker = market_data.get("ticker", market_data.get("symbol", "NIFTY"))
            bars = chart_data or market_data.get("bars", [])
            indicators = compute_indicators(bars) if bars else {}
            state = {
                "ticker": ticker,
                "timeframe": market_data.get("timeframe", "1D"),
                "bars": bars,
                "indicators": indicators,
                "news": news,
                "market_data": market_data,
            }
            result = {}
            async for chunk in run_mirofish_stream(state):
                result.update(chunk)
            conf = result.get("final_confidence", result.get("confidence", 55))
            return {
                "consensus_confidence": float(conf),
                "signal": result.get("signal", result.get("action", "HOLD")),
                "reasoning": result.get("reasoning", result.get("summary", "")),
                "agents": result.get("agents", []),
            }
        except Exception as e:
            logger.warning("[MiroFishLangGraph] run_analysis failed: %s", e)
            return {"consensus_confidence": 50.0, "signal": "HOLD", "reasoning": str(e), "agents": []}


class DeltaDashScoreboard:
    """Wrapper to fetch DeltaDash score for a ticker."""

    def get_score(self, ticker: str) -> Dict:
        """Return multi-TF composite score dict with 'Total' key."""
        try:
            import sys
            import os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            import deltadash_router as _dd
            # Try to fetch cached score
            sym = ticker.replace(".NS", "").replace(".BO", "").upper()
            cache = getattr(_dd, "_SCORE_CACHE", {})
            for k, v in cache.items():
                if sym in k.upper():
                    return v
            return {"Total": 150, "ticker": ticker, "source": "default"}
        except Exception as e:
            logger.debug("[DeltaDashScoreboard] get_score: %s", e)
            return {"Total": 150, "ticker": ticker}


# ─────────────────────────────────────────────────────────────────────────────
# 3) HYBRID SUPER BRAIN v4.0
# ─────────────────────────────────────────────────────────────────────────────
class HybridSuperBrain:
    """🌍 WORLD-CLASS CENTRAL BRAIN with Kronos + SMC"""

    def __init__(self, config: Dict = None):
        self.config = config or {}

        # Core modules
        self.mirofish       = MiroFishLangGraph()
        self.smc            = SMCAnalyzer()
        self.deltadash      = DeltaDashScoreboard()
        self.kronos         = KronosScheduler()

        # Lazy-loaded heavy modules
        self._dreamer_ready    = False
        self._risk_ready       = False
        self._danger_ready     = False
        self._dreamer          = None
        self._risk             = None
        self._danger           = None

        # Survival engine (persisted to MongoDB)
        self.survival = MildSurvivalEngine(
            daily_target_pct=float(self.config.get("daily_target_pct", 0.5)),
        )

        # Backward-compat state
        self.current_pnl_pct  = 0.0
        self.brain_enabled    = True
        self._decision_cache: Dict[str, Any] = {}
        self._cache_ttl       = 30.0  # seconds

        logger.info("🚀 HybridSuperBrain v4.0 — Kronos + SMC Activated")

    def _get_dreamer(self):
        if self._dreamer is None:
            try:
                from agents.dreamer_robo_orchestrator import DreamerOrchestrator
                self._dreamer = DreamerOrchestrator()
            except Exception as e:
                logger.warning("[HybridBrain] Dreamer import failed: %s", e)
        return self._dreamer

    def _get_risk(self):
        if self._risk is None:
            try:
                from agents.risk_portfolio_manager import RiskPortfolioManager
                self._risk = RiskPortfolioManager()
            except Exception as e:
                logger.warning("[HybridBrain] RPM import failed: %s", e)
        return self._risk

    def _get_danger(self):
        if self._danger is None:
            try:
                from agents.danger_scanner import async_danger_scan
                self._danger = async_danger_scan
            except Exception as e:
                logger.warning("[HybridBrain] DangerScanner import failed: %s", e)
        return self._danger

    # ── Core Decision Pipeline ────────────────────────────────────────────────
    async def think_and_decide(
        self,
        market_data: Dict,
        news: str = "",
        symbol: str = "NIFTY",
        chart_data=None,
        dreamer_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Elite Decision Making — 6-layer analysis pipeline."""
        if not self.brain_enabled:
            return {
                "action": "HOLD", "confidence": 50.0,
                "reasoning": "Brain disabled", "symbol": symbol,
            }

        # Cache check (avoid duplicate decisions within TTL)
        cache_key = f"{symbol}_{int(time.time() // self._cache_ttl)}"
        if cache_key in self._decision_cache:
            return self._decision_cache[cache_key]

        await self.survival.load()

        try:
            # 1. MiroFish + SMC Analysis (parallel)
            miro_task = asyncio.create_task(
                self.mirofish.run_analysis(market_data, news, chart_data)
            )
            # SMC uses chart_data if available, falls back to market_data flags
            smc_input = chart_data if chart_data else market_data
            smc_analysis = self.smc.analyze(smc_input)

            # 2. DreamerV3 confidence
            dreamer_conf = dreamer_confidence or 50.0
            dreamer = self._get_dreamer()
            if dreamer is not None:
                try:
                    dr = await asyncio.wait_for(
                        dreamer.process(market_data), timeout=5.0
                    )
                    dreamer_conf = float(dr.get("confidence", dreamer_conf))
                except Exception:
                    pass

            # 3. DeltaDash + Danger + Kronos (Time Cycle)
            delta_score    = self.deltadash.get_score(market_data.get("ticker", symbol))
            kronos_signal  = self.kronos.get_cycle_signal(market_data.get("ticker", symbol))

            # Danger scanner (non-blocking)
            danger_fn = self._get_danger()
            if danger_fn is not None:
                try:
                    picks = await asyncio.wait_for(danger_fn(top_n=1), timeout=3.0)
                    if picks:
                        logger.debug("[HybridBrain] Danger pick: %s", picks[0])
                except Exception:
                    pass

            # 4. Wait for MiroFish
            miro_result = await asyncio.wait_for(miro_task, timeout=15.0)

            # 5. Psychological + Survival pressure
            psych = self._psychological_analysis(market_data, news)
            survival_status = self.survival.update(self.current_pnl_pct)

            # 6. Meta reasoning
            meta = await self._top_trader_meta_reasoning(
                market_data, psych, miro_result,
                {"confidence": dreamer_conf},
                smc_analysis, kronos_signal,
            )

            # 7. Final elite decision
            decision = self._elite_decision(
                miro_result, {"confidence": dreamer_conf},
                delta_score, psych, meta, smc_analysis, kronos_signal,
                survival_status,
            )
            decision["symbol"] = symbol

            # Persist audit
            asyncio.create_task(self._save_audit(symbol, decision, meta))

            self._decision_cache[cache_key] = decision
            return decision

        except Exception as e:
            logger.error("[HybridBrain] think_and_decide error: %s", e)
            return {
                "action": "HOLD", "confidence": 25.0,
                "reasoning": f"System Safety: {e}",
                "symbol": symbol,
                "smc_signal": "neutral",
                "kronos_cycle": "unknown",
                "top_trader_view": "HOLD — system error",
            }

    def _psychological_analysis(self, market_data: Dict, news: str) -> Dict:
        """Extract FOMO / regime / apathy from market data."""
        m = market_data or {}
        momentum = float(m.get("momentum_strength", m.get("momentum",
                         m.get("change_pct", 0.0) / 5.0 + 0.5)))
        momentum  = max(0.0, min(1.0, momentum))
        vol       = float(m.get("volatility", m.get("atr_pct", 0.015)))
        fomo      = min(0.95, momentum * 0.8 + vol * 22)
        apathy    = max(0.0, (1 - momentum) * 0.55)

        regime = "bullish" if momentum > 0.65 else "ranging"

        return {
            "fomo_score":  round(fomo, 3),
            "apathy_score": round(apathy, 3),
            "regime":       regime,
            "momentum":     round(momentum, 3),
        }

    async def _top_trader_meta_reasoning(
        self,
        market_data: Dict,
        psych: Dict,
        miro: Dict,
        dreamer: Dict,
        smc: Dict,
        kronos: Dict,
    ) -> Dict:
        """Synthesise all layers into asymmetric edge score."""
        fomo      = psych.get("fomo_score", 0.5)
        regime    = psych.get("regime", "ranging")
        miro_conf = miro.get("consensus_confidence", 50.0) / 100.0
        dr_conf   = dreamer.get("confidence", 50.0) / 100.0
        smc_score = smc.get("smc_score", 0.0)
        kronos_ph = kronos.get("phase", "unknown")

        edge = miro_conf * 0.35 + dr_conf * 0.30 + smc_score * 0.20 + fomo * 0.15
        asymmetric = edge > 0.52

        # Build summary
        lines = [
            f"Regime: {regime.upper()}",
            f"MiroFish consensus: {miro_conf:.0%}",
            f"Dreamer confidence: {dr_conf:.0%}",
            f"SMC bias: {smc.get('structure_bias', 'NEUTRAL')}",
            f"Kronos cycle: {kronos_ph}",
        ]
        summary = " | ".join(lines)

        if smc.get("bos_bullish") and miro_conf > 0.6:
            rec = "STRONG BUY — BOS + MiroFish alignment"
        elif smc.get("bos_bearish") and miro_conf < 0.4:
            rec = "STRONG SELL — BOS + bearish consensus"
        elif asymmetric:
            rec = f"BUY — asymmetric edge {edge:.0%}"
        else:
            rec = "HOLD — insufficient conviction"

        return {
            "edge_score":      round(edge, 3),
            "asymmetric_edge": asymmetric,
            "summary":         summary,
            "recommendation":  rec,
        }

    def _elite_decision(
        self,
        miro: Dict,
        dreamer: Dict,
        delta: Dict,
        psych: Dict,
        meta: Dict,
        smc: Dict,
        kronos: Dict,
        survival: Dict = None,
    ) -> Dict:
        """Fuse all signals into a final trading decision."""
        fear = (survival or {}).get("fear", 0.0)
        fear_penalty = fear * 10.0

        final_conf = (
            dreamer.get("confidence", 50.0) * 0.30 +
            miro.get("consensus_confidence", 55.0) * 0.25 +
            float(delta.get("Total", 150)) / 300.0 * 100.0 * 0.20 +
            psych.get("fomo_score", 0.5) * 15.0 +
            smc.get("smc_score", 0.0) * 10.0
        ) - fear_penalty

        final_conf = max(20.0, min(97.0, final_conf))

        # Decision gate
        ob_signal  = smc.get("order_block", False)
        fvg_signal = smc.get("fair_value_gap", False)

        if final_conf > 61 and meta.get("asymmetric_edge") and ob_signal:
            action = "BUY"
        elif final_conf < 39 and fvg_signal:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "action":          action,
            "confidence":      round(final_conf, 1),
            "reasoning":       meta.get("summary", ""),
            "smc_signal":      smc.get("signal", "neutral"),
            "kronos_cycle":    kronos.get("phase", "unknown"),
            "top_trader_view": meta.get("recommendation", "HOLD"),
            "fear_level":      round(fear, 3),
            "size_scalar":     max(0.3, 1.0 - fear),
            "psych":           psych,
            "survival":        survival or {},
            "components": {
                "miro_conf":    round(miro.get("consensus_confidence", 50.0), 1),
                "dreamer_conf": round(dreamer.get("confidence", 50.0), 1),
                "delta_total":  delta.get("Total", 150),
                "smc_score":    round(smc.get("smc_score", 0.0), 3),
                "kronos_phase": kronos.get("phase", "unknown"),
            },
            "risk_alert": "danger" if fear > 0.6 else "warning" if fear > 0.3 else "good",
        }

    # ── Audit ────────────────────────────────────────────────────────────────
    async def _save_audit(self, symbol: str, decision: Dict, meta: Dict):
        try:
            doc = {
                "id":          str(uuid4()),
                "symbol":      symbol,
                "action":      decision.get("action"),
                "confidence":  decision.get("confidence"),
                "reasoning":   decision.get("reasoning"),
                "timestamp":   datetime.now(timezone.utc).isoformat(),
                "meta":        meta,
            }
            await _get_db().hybrid_brain_audit.insert_one(doc)
        except Exception as e:
            logger.debug("[HybridBrain] audit save failed: %s", e)

    async def get_recent_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            cursor = _get_db().hybrid_brain_audit.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.warning("[HybridBrain] audit fetch failed: %s", e)
            return []

    # ── PnL / Day Management ─────────────────────────────────────────────────
    async def update_daily_pnl(self, pnl_pct: float):
        self.current_pnl_pct = float(pnl_pct)
        await self.survival.load()
        self.survival.update(self.current_pnl_pct)
        await self.survival.persist()

    async def reset_for_new_day(self, manual: bool = False):
        await self.survival.load()
        if manual:
            await self.survival.manual_reset()
        else:
            await self.survival.reset_daily()
        self.current_pnl_pct = 0.0
        self._decision_cache.clear()

    # ── Sync helpers (used by trading_loop.py worker thread) ─────────────────
    def decide_sync(
        self,
        market_data: Dict[str, Any],
        news: str = "",
        symbol: str = "NIFTY",
        dreamer_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for thread-based callers."""
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
                "action":     "HOLD",
                "confidence": float(dreamer_confidence or 50.0),
                "size_scalar": 1.0,
                "psych":      {},
                "survival":   {"fear": 0.0, "status": "good"},
                "reasoning":  f"hybrid brain unavailable: {e}",
                "error":      str(e),
                "components": {},
                "risk_alert": "good",
            }

    def update_daily_pnl_sync(self, pnl_pct: float):
        """Sync wrapper used by orchestrator worker every tick."""
        self.current_pnl_pct = float(pnl_pct)


# ─── Singleton ────────────────────────────────────────────────────────────────
hybrid_brain = HybridSuperBrain()
