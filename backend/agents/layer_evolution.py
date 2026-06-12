"""
Robot 3.0 Layer Evolution Engine
=================================
Propagates DreamerV3 LIVE-training reward signals to EVERY intelligence
layer of Robot 3.0 — so the *whole brain* evolves together, not just the
RL core. Goal: zero blind-spots when taking trades.

Layers tracked (trust score 0.05 → 0.95, EMA-updated):
  1. dreamer        — DreamerV3 RL core (live reward + WM-loss trend)
  2. psychology     — FOMO / apathy / regime harvester
  3. strategy       — StrategyCollaborator 6-agent consensus
  4. mirofish_meta  — MiroFish LangGraph + MetaReasoner adjustment
  5. survival       — MildSurvivalEngine fear/caution
  6. risk_gate      — RPM heat / budget gate

Learning signals (strong → weak):
  • evolve_from_trade_close()   lr=0.20  — real P&L when a position closes
  • evolve_from_live_training() lr=0.08  — per-scan-cycle live reward proxy
  • notify_dreamer_step()       lr=0.02  — world-model loss improving/worsening

Evolved trust scores feed back into HybridSuperBrain._hybrid_engine() as
ADAPTIVE coefficients (fomo/apathy/fear/regime multipliers + dreamer/meta
signal scaling), closing the full training loop across all layers.

State persisted to MongoDB collection ``layer_evolution_state`` so the
evolution survives restarts.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("layer_evolution")

LAYERS = ["dreamer", "psychology", "strategy", "mirofish_meta", "survival", "risk_gate"]

# Learning rates per trigger
LR_TRADE_CLOSE = 0.20   # real P&L — strongest signal
LR_LIVE_CYCLE  = 0.08   # per-cycle live reward proxy
LR_DREAMER     = 0.02   # WM-loss trend micro-updates

TRUST_MIN, TRUST_MAX = 0.05, 0.95
PERSIST_EVERY_SEC    = 10.0
MAX_EVENTS           = 30


class LayerEvolutionEngine:
    """Singleton — see module docstring. Thread-safe (called from trading
    loop thread, dreamer mini-train thread and FastAPI handlers)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._trust:   Dict[str, float] = {l: 0.50 for l in LAYERS}
        self._updates: Dict[str, int]   = {l: 0 for l in LAYERS}
        self._correct: Dict[str, int]   = {l: 0 for l in LAYERS}
        self._events:  List[Dict]       = []
        self._last_ctx: Dict[str, Dict] = {}   # ticker → last decision context
        self.total_updates       = 0
        self.trade_closes_learned = 0
        self.dreamer_steps       = 0
        self._wm_ema: Optional[float] = None
        self._last_persist = 0.0
        self._loaded = False
        self._load()

    # ── Persistence (sync pymongo — called from worker threads) ──────────────

    def _coll(self):
        from pymongo import MongoClient
        url  = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        name = os.environ.get("DB_NAME", "trading_db")
        client = MongoClient(url, serverSelectionTimeoutMS=2000)
        return client[name]["layer_evolution_state"]

    def _load(self):
        try:
            doc = self._coll().find_one({"_id": "main"})
            if doc:
                with self._lock:
                    for l in LAYERS:
                        self._trust[l]   = float(doc.get("trust", {}).get(l, 0.5))
                        self._updates[l] = int(doc.get("updates", {}).get(l, 0))
                        self._correct[l] = int(doc.get("correct", {}).get(l, 0))
                    self.total_updates        = int(doc.get("total_updates", 0))
                    self.trade_closes_learned = int(doc.get("trade_closes_learned", 0))
                    self.dreamer_steps        = int(doc.get("dreamer_steps", 0))
                logger.info("[LayerEvo] State restored from MongoDB (%d total updates)",
                            self.total_updates)
        except Exception as e:
            logger.debug("[LayerEvo] load skipped: %s", e)
        self._loaded = True

    def _maybe_persist(self):
        now = time.time()
        if now - self._last_persist < PERSIST_EVERY_SEC:
            return
        self._last_persist = now
        try:
            with self._lock:
                doc = {
                    "trust":   dict(self._trust),
                    "updates": dict(self._updates),
                    "correct": dict(self._correct),
                    "total_updates":        self.total_updates,
                    "trade_closes_learned": self.trade_closes_learned,
                    "dreamer_steps":        self.dreamer_steps,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            self._coll().update_one({"_id": "main"}, {"$set": doc}, upsert=True)
        except Exception as e:
            logger.debug("[LayerEvo] persist skipped: %s", e)

    # ── Core EMA update ───────────────────────────────────────────────────────

    def _update(self, layer: str, correct: bool, lr: float, trigger: str):
        with self._lock:
            old    = self._trust[layer]
            target = 1.0 if correct else 0.0
            new    = max(TRUST_MIN, min(TRUST_MAX, old + lr * (target - old)))
            self._trust[layer]   = round(new, 4)
            self._updates[layer] += 1
            if correct:
                self._correct[layer] += 1
            self.total_updates += 1
            self._events.append({
                "layer":   layer,
                "correct": correct,
                "delta":   round(new - old, 4),
                "trust":   round(new, 4),
                "trigger": trigger,
                "ts":      datetime.now(timezone.utc).isoformat(),
            })
            self._events = self._events[-MAX_EVENTS:]

    # ── Public: live-cycle learning (called every scan cycle per ticker) ─────

    def evolve_from_live_training(
        self,
        ticker:     str,
        reward:     float,
        brain_block: Optional[Dict[str, Any]],
        signal:     str,
        confidence: float,
        lr:         float = LR_LIVE_CYCLE,
        trigger:    str   = "live_cycle",
    ):
        """Propagate DreamerV3's live reward proxy to all brain layers."""
        r_pos = reward > 0
        bb    = brain_block or {}
        comp  = bb.get("components", {}) or {}

        # 1. Dreamer core — direct reward feedback
        self._update("dreamer", r_pos, lr, trigger)

        # 2. Psychology — did FOMO/regime/apathy push in the right direction?
        psych_push = (comp.get("fomo_boost", 0.0)
                      + comp.get("regime_bonus", 0.0)
                      + comp.get("apathy_drag", 0.0))   # apathy stored negative
        if abs(psych_push) > 0.5:
            self._update("psychology", (psych_push > 0) == r_pos, lr, trigger)

        # 3. Survival — caution (fear penalty) is correct when outcome is bad
        if comp.get("fear_penalty", 0.0) < -0.5:
            self._update("survival", not r_pos, lr, trigger)

        # 4. MiroFish/Meta — confidence_adjust direction vs outcome
        ma = comp.get("meta_adjust", 1.0)
        if abs(ma - 1.0) > 0.02:
            self._update("mirofish_meta", (ma > 1.0) == r_pos, lr, trigger)

        # 5. Strategy — consensus alignment with executed signal vs outcome
        sc = bb.get("strategy_consensus")
        if sc in ("BUY", "SELL") and signal in ("BUY", "SELL"):
            self._update("strategy", (sc == signal) == r_pos, lr, trigger)

        # Remember context for the stronger trade-close signal later
        self._last_ctx[ticker] = {
            "signal":     signal,
            "confidence": confidence,
            "components": dict(comp),
            "strategy_consensus": sc,
            "strategy_agents": bb.get("strategy_agents") or [],
            "ts": time.time(),
        }
        self._maybe_persist()

    # ── Public: trade-close learning (strongest signal — real P&L) ───────────

    def evolve_from_trade_close(self, ticker: str, pnl: float):
        """Real money outcome — re-train every layer with the strongest lr."""
        win = pnl > 0
        ctx = self._last_ctx.get(ticker)

        # Risk gate allowed this trade — judge it on the real outcome
        self._update("risk_gate", win, 0.12, "trade_close")

        if ctx:
            self.evolve_from_live_training(
                ticker,
                reward=1.0 if win else -1.0,
                brain_block={
                    "components": ctx.get("components", {}),
                    "strategy_consensus": ctx.get("strategy_consensus"),
                },
                signal=ctx.get("signal", "HOLD"),
                confidence=ctx.get("confidence", 50.0),
                lr=LR_TRADE_CLOSE,
                trigger="trade_close",
            )
            # Also feed the 6 strategy agents (AdaptiveLearner) directly
            try:
                agents_sig = {
                    a.get("agent"): a.get("signal")
                    for a in ctx.get("strategy_agents", [])
                    if a.get("agent")
                }
                if agents_sig and ctx.get("signal") in ("BUY", "SELL"):
                    from .adaptive_learner import learner
                    learner.record_trade_outcome(
                        ticker, ctx["signal"], "WIN" if win else "LOSS", agents_sig
                    )
            except Exception as e:
                logger.debug("[LayerEvo] adaptive learner feed failed: %s", e)
        else:
            self._update("dreamer", win, LR_TRADE_CLOSE, "trade_close")

        with self._lock:
            self.trade_closes_learned += 1
        logger.info("[LayerEvo] Trade close learned | %s pnl=%.2f → all layers re-trained (%s)",
                    ticker, pnl, "WIN" if win else "LOSS")
        self._maybe_persist()

    # ── Public: dreamer mini-train step (WM-loss trend micro-update) ─────────

    def notify_dreamer_step(self, wm_loss: float, actor_loss: float = 0.0,
                            critic_loss: float = 0.0):
        with self._lock:
            self.dreamer_steps += 1
            prev = self._wm_ema
            self._wm_ema = wm_loss if prev is None else 0.9 * prev + 0.1 * wm_loss
        if prev is not None:
            self._update("dreamer", wm_loss < prev, LR_DREAMER, "dreamer_step")
        self._maybe_persist()

    # ── Public: adaptive coefficients for HybridSuperBrain ───────────────────

    def get_coefficients(self) -> Dict[str, float]:
        """Map layer trust → scoring coefficients used in _hybrid_engine.
        trust 0.5 reproduces the original static values exactly."""
        with self._lock:
            t = dict(self._trust)
        return {
            "fomo":          round(12.0 * (0.5 + t["psychology"]), 3),    # 6.6 – 17.4
            "apathy":        round(5.0 * (1.5 - t["psychology"]), 3),     # 2.75 – 7.25
            "regime":        round(4.0 * (0.5 + t["psychology"]), 3),
            "fear":          round(15.0 * (0.5 + t["survival"]), 3),      # trusted caution bites harder
            "meta_scale":    round(0.5 + t["mirofish_meta"], 3),          # 0.55 – 1.45
            "dreamer_scale": round(0.5 + t["dreamer"], 3),
        }

    # ── Public: state for API / UI ────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            layers = {
                l: {
                    "trust":    round(self._trust[l], 4),
                    "trust_pct": round(self._trust[l] * 100, 1),
                    "updates":  self._updates[l],
                    "accuracy": round(self._correct[l] / max(self._updates[l], 1) * 100, 1),
                }
                for l in LAYERS
            }
            return {
                "enabled":              True,
                "layers":               layers,
                "total_updates":        self.total_updates,
                "trade_closes_learned": self.trade_closes_learned,
                "dreamer_steps":        self.dreamer_steps,
                "wm_loss_ema":          round(self._wm_ema, 6) if self._wm_ema is not None else None,
                "recent_events":        list(self._events[-10:]),
                "as_of":                datetime.now(timezone.utc).isoformat(),
            }

    def get_full_state(self) -> Dict[str, Any]:
        s = self.get_state()
        s["coefficients"] = self.get_coefficients()
        return s

    def reset(self):
        with self._lock:
            self._trust   = {l: 0.50 for l in LAYERS}
            self._updates = {l: 0 for l in LAYERS}
            self._correct = {l: 0 for l in LAYERS}
            self._events.clear()
            self._last_ctx.clear()
            self.total_updates        = 0
            self.trade_closes_learned = 0
            self.dreamer_steps        = 0
            self._wm_ema              = None
        self._last_persist = 0.0
        self._maybe_persist()
        logger.info("[LayerEvo] Reset to base trust (0.50 all layers)")


# ── Module-level singleton ────────────────────────────────────────────────────
layer_evolution = LayerEvolutionEngine()

__all__ = ["LayerEvolutionEngine", "layer_evolution", "LAYERS"]
