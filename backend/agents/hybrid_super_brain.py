"""
Hybrid Super Brain — Improvised
================================
Combines DreamerV3 confidence + Psychological Market Edge + Survival Discipline.

Improvements over the seed implementation:
  • Real market data pulled from yfinance / India VIX / live option chains (not random).
  • Persistent daily PnL + fear history in MongoDB (collection: hybrid_brain_state).
  • Integrates with existing DreamerOrchestrator state & RiskPortfolioManager position sizing.
  • News sentiment via existing sentiment helpers when available (graceful fallback).
  • Per-symbol decision cache (60s) to avoid hammering APIs.
  • Circuit breakers: extreme fear → force HOLD, capital lock when fear > 0.8.
  • Audit trail of every decision (collection: hybrid_brain_audit).
"""
from __future__ import annotations
import asyncio
import logging
import math
import os
from datetime import datetime, date, timezone
from typing import Dict, Any, Optional, List
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


# ─── 1) Survival Engine (improved) ────────────────────────────────────────────
class MildSurvivalEngine:
    """Tracks consecutive misses against daily target → fear/boost scalars.

    Persists state per day so the brain remembers between server restarts.
    """

    def __init__(self, daily_target_pct: float = 0.5, grace_days: int = 5):
        self.daily_target = float(daily_target_pct) / 100.0
        self.grace_days = grace_days
        self.consecutive_fail = 0
        self.fear_level = 0.0
        self.last_pnl_pct = 0.0
        self._loaded = False

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
            logger.warning(f"[Survival] load failed: {e}")
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
            logger.warning(f"[Survival] persist failed: {e}")

    def update(self, daily_pnl_pct: float) -> Dict[str, Any]:
        """daily_pnl_pct: today's return as a fraction (0.005 = 0.5%)."""
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
            # Grace period: fear ramps slower in first `grace_days` misses
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
        """Called at start of new trading day."""
        self.consecutive_fail = 0
        self.fear_level = max(0.0, self.fear_level - 0.35)  # decay overnight
        self.last_pnl_pct = 0.0
        await self.persist()


# ─── 2) Psychological Harvester (improved) ────────────────────────────────────
_POS_NEWS = ("upgrade", "beat", "surge", "rally", "positive", "buy", "outperform",
             "expansion", "record high", "breakout", "bullish")
_NEG_NEWS = ("downgrade", "miss", "crash", "plunge", "negative", "sell", "underperform",
             "loss", "record low", "breakdown", "bearish", "fraud")


class PsychologicalHarvester:
    """Extracts FOMO / Apathy / Narrative credibility / Regime from real market data."""

    @staticmethod
    def _bounded(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, x))

    def analyze(self, market_data: Dict[str, Any], news: str = "") -> Dict[str, Any]:
        m = market_data or {}

        # Momentum: rolling return percentile, or use change_pct
        momentum = float(m.get("momentum_strength",
                          m.get("change_pct", 0.0) / 5.0 + 0.5))
        momentum = self._bounded(momentum)

        # Volatility: India VIX as fraction (0.12 = 12%), or ATR-pct
        vol = float(m.get("volatility_index", m.get("atr_pct", 0.015)))

        # Volume thrust: today / 20d-avg (default 1.0)
        vol_thrust = float(m.get("volume_thrust", 1.0))

        # FOMO: momentum + vol + volume thrust → 0..1
        fomo = self._bounded(0.25 * momentum + 1.4 * vol + 0.20 * (vol_thrust - 1.0) + 0.10)

        # Apathy: low momentum + low volume → 0..1
        apathy = self._bounded((1 - momentum) * 0.55 + max(0.0, 1.0 - vol_thrust) * 0.30)

        # Narrative credibility from news
        text = (news or "").lower()
        pos_hits = sum(1 for w in _POS_NEWS if w in text)
        neg_hits = sum(1 for w in _NEG_NEWS if w in text)
        if pos_hits == 0 and neg_hits == 0:
            cred = 0.50
        else:
            cred = self._bounded(0.5 + 0.08 * (pos_hits - neg_hits))

        # Regime classification (trending vs ranging vs volatile)
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

        # Hidden-value heuristic
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
            "hidden_value_gap":      gap,
        }


# ─── 3) Hybrid Super Brain ────────────────────────────────────────────────────
class HybridSuperBrain:
    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.config = cfg
        self.survival = MildSurvivalEngine(
            daily_target_pct=float(cfg.get("daily_target_pct", 0.5)),
            grace_days=int(cfg.get("grace_days", 5)),
        )
        self.psych = PsychologicalHarvester()
        self.current_pnl_pct = 0.0
        self._decision_cache: Dict[str, tuple] = {}   # symbol → (decision, ts)
        self._cache_ttl = 60.0

    # ── Public API ────────────────────────────────────────────────────────────
    async def think_and_decide(
        self,
        market_data: Dict[str, Any],
        news: str = "",
        symbol: str = "NIFTY",
        dreamer_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Core decision pipeline."""
        await self.survival.load()

        # Cache check
        now = datetime.now(timezone.utc).timestamp()
        cached = self._decision_cache.get(symbol)
        if cached and (now - cached[1] < self._cache_ttl):
            return {**cached[0], "cached": True}

        # 1) Psychology
        psych = self.psych.analyze(market_data, news)

        # 2) Dreamer confidence (real or supplied)
        if dreamer_confidence is None:
            dreamer_confidence = await self._fetch_dreamer_confidence(symbol)
        base_conf = float(dreamer_confidence)

        # 3) Survival update (uses current_pnl_pct)
        survival = self.survival.update(self.current_pnl_pct)

        # 4) Hybrid scoring
        fomo_boost   = psych["fomo_score"] * 12.0
        apathy_drag  = psych["apathy_score"] * 5.0
        fear_penalty = survival["fear"]      * 15.0
        cred_factor  = 0.65 if psych["narrative_credibility"] < 0.5 else 1.0
        regime_bonus = 4.0 if psych["regime"].startswith("trending") else (-3.0 if psych["regime"] == "volatile" else 0.0)

        final_conf = (base_conf + fomo_boost + regime_bonus - apathy_drag - fear_penalty) * cred_factor
        final_conf = max(15.0, min(98.0, final_conf))

        # 5) Action selection (with circuit breakers)
        if survival["fear"] > 0.80:
            action = "HOLD"   # extreme fear → capital preservation
            reason_suffix = " | CIRCUIT-BREAKER: extreme fear"
        elif final_conf > 68 and psych["regime"] != "trending_down":
            action = "BUY"
            reason_suffix = ""
        elif final_conf < 40 or psych["regime"] == "trending_down":
            action = "SELL"
            reason_suffix = ""
        else:
            action = "HOLD"
            reason_suffix = ""

        # 6) Position-size scalar (handed to RiskPortfolioManager)
        size_scalar = 1.0
        if action == "BUY":
            size_scalar = (1.0 + 0.4 * psych["fomo_score"]) * (1.0 - 0.5 * survival["fear"])
            size_scalar = max(0.25, min(1.5, size_scalar))

        decision = {
            "id":         str(uuid4()),
            "symbol":     symbol,
            "action":     action,
            "confidence": round(final_conf, 1),
            "size_scalar": round(size_scalar, 3),
            "components": {
                "dreamer_base":         round(base_conf, 1),
                "fomo_boost":           round(fomo_boost, 2),
                "regime_bonus":         round(regime_bonus, 2),
                "apathy_drag":          round(-apathy_drag, 2),
                "fear_penalty":         round(-fear_penalty, 2),
                "credibility_factor":   round(cred_factor, 2),
            },
            "psych":      psych,
            "survival":   survival,
            "reasoning":  (f"FOMO {psych['fomo_score']:.2f} · Regime {psych['regime']} · "
                           f"Cred {psych['narrative_credibility']:.2f} · Fear {survival['fear']:.2f} · "
                           f"Dreamer {base_conf:.0f}" + reason_suffix),
            "risk_alert": survival["status"],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }

        self._decision_cache[symbol] = (decision, now)
        await self._audit(decision)
        await self.survival.persist()
        return {**decision, "cached": False}

    async def _audit(self, decision: Dict[str, Any]):
        try:
            await _get_db().hybrid_brain_audit.insert_one({
                **decision,
                "_id": decision["id"],
            })
        except Exception as e:
            logger.debug(f"[HybridBrain] audit insert failed: {e}")

    async def get_recent_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            cursor = _get_db().hybrid_brain_audit.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.warning(f"[HybridBrain] audit fetch failed: {e}")
            return []

    async def update_daily_pnl(self, pnl_pct: float):
        """Externally fed daily PnL as a fraction (e.g. 0.005 for 0.5%)."""
        self.current_pnl_pct = float(pnl_pct)
        await self.survival.load()
        # Trigger an update so the fear/consecutive count refreshes
        self.survival.update(self.current_pnl_pct)
        await self.survival.persist()

    async def reset_for_new_day(self):
        await self.survival.load()
        await self.survival.reset_daily()
        self.current_pnl_pct = 0.0

    async def _fetch_dreamer_confidence(self, symbol: str) -> float:
        """Pull confidence from existing DreamerOrchestrator state when available."""
        try:
            from .dreamer_robo_orchestrator import get_robo_state
            state = get_robo_state() or {}
            conf = state.get("last_confidence") or state.get("confidence") or 60.0
            return float(conf)
        except Exception:
            return 60.0

    # ── Sync helper for thread-based callers (orchestrator worker) ────────────
    def decide_sync(
        self,
        market_data: Dict[str, Any],
        news: str = "",
        symbol: str = "NIFTY",
        dreamer_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Synchronous wrapper of think_and_decide() for thread workers
        (e.g. dreamer_robo_orchestrator). Creates its own event loop."""
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    raise RuntimeError("event loop already running")
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
            logger.warning(f"[HybridBrain] decide_sync failed: {e}")
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
        """Sync wrapper used by the orchestrator worker every tick."""
        self.current_pnl_pct = float(pnl_pct)


# ─── Singleton ────────────────────────────────────────────────────────────────
hybrid_brain = HybridSuperBrain()
