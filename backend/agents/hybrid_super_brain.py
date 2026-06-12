# backend/agents/hybrid_super_brain.py
"""
╔══════════════════════════════════════════════════════════════════════╗
║          H Y B R I D   S U P E R   B R A I N   v5.0               ║
║           — THE FULLY CENTRAL DECISION ENGINE —                     ║
╠══════════════════════════════════════════════════════════════════════╣
║  All trading decisions route through this single class.             ║
║                                                                      ║
║  Pipeline (in order):                                               ║
║    1. Kronos Circuit-Breaker  — market-hours gate                  ║
║    2. DreamerV3 Layer         — RL world-model confidence           ║
║    3. SMC Analyzer            — Order Blocks, FVG, BOS, CHoCH       ║
║    4. Kronos Cycle Signal     — intraday phase + strength           ║
║    5. DeltaDash Score         — multi-TF technical composite        ║
║    6. MiroFish LangGraph      — 5-node LLM multi-agent (async)      ║
║    7. LayerEvolution Coeffs   — adaptive trust weights              ║
║    8. Agent Consensus         — StrategyCollaborator cached state    ║
║    9. Psychological Analysis  — FOMO / apathy / regime              ║
║   10. Survival Engine         — daily PnL fear scalar               ║
║   11. Meta Reasoner           — asymmetric edge synthesis           ║
║   12. Elite Decision Gate     — final BUY / SELL / HOLD             ║
║   13. Position Sizing         — sl_price, tp_price, quantity        ║
║                                                                      ║
║  Consumers:                                                          ║
║    • trading_loop.py          → decide_sync()                       ║
║    • dreamer_robo_orchestrator→ decide_sync_full()                  ║
║    • hybrid_brain_router.py   → think_and_decide()                  ║
║    • robo_router.py           → think_and_decide()                  ║
╚══════════════════════════════════════════════════════════════════════╝
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

from .smc_analyzer import SMCAnalyzer
from .kronos import KronosScheduler

logger = logging.getLogger("hybrid_super_brain")

# ATR multipliers (mirror dreamer_robo_orchestrator)
ATR_MULT_SL = 2.0
ATR_MULT_TP = 3.0

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
    """Tracks consecutive daily target misses → fear / boost scalars."""

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
                "status":  "warning" if self.consecutive_fail < 3 else "danger",
                "fear":    round(self.fear_level, 3),
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


# ─────────────────────────────────────────────────────────────────────────────
# 2) MIROFISH LANGRAPH WRAPPER
# ─────────────────────────────────────────────────────────────────────────────
class MiroFishLangGraph:
    """Wrapper around the mirofish_langgraph module (5-node LLM pipeline)."""

    async def run_analysis(self, market_data: Dict, news: str = "", chart_data=None) -> Dict:
        try:
            import sys
            import os as _os
            sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
            from mirofish_langgraph import run_mirofish_stream, compute_indicators
            ticker    = market_data.get("ticker", market_data.get("symbol", "NIFTY"))
            bars      = chart_data or market_data.get("bars", [])
            indicators = compute_indicators(bars) if bars else {}
            state = {
                "ticker":      ticker,
                "timeframe":   market_data.get("timeframe", "1D"),
                "bars":        bars,
                "indicators":  indicators,
                "news":        news,
                "market_data": market_data,
            }
            result: Dict = {}
            async for chunk in run_mirofish_stream(state):
                result.update(chunk)
            conf = float(result.get("final_confidence", result.get("confidence", 50)))
            return {
                "consensus_confidence": conf,
                "signal":    result.get("signal", result.get("action", "HOLD")),
                "reasoning": result.get("reasoning", result.get("summary", "")),
                "agents":    result.get("agents", []),
            }
        except Exception as e:
            logger.debug("[MiroFish] run_analysis: %s", e)
            return {"consensus_confidence": 50.0, "signal": "HOLD", "reasoning": str(e), "agents": []}


# ─────────────────────────────────────────────────────────────────────────────
# 3) DELTADASH SCOREBOARD WRAPPER
# ─────────────────────────────────────────────────────────────────────────────
class DeltaDashScoreboard:
    """Wrapper to fetch DeltaDash multi-TF technical score for a ticker."""

    def get_score(self, ticker: str) -> Dict:
        try:
            import sys
            import os as _os
            sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
            import deltadash_router as _dd
            sym   = ticker.replace(".NS", "").replace(".BO", "").upper()
            cache = getattr(_dd, "_SCORE_CACHE", {})
            for k, v in cache.items():
                if sym in k.upper():
                    return v
            return {"Total": 150, "ticker": ticker, "source": "default"}
        except Exception as e:
            logger.debug("[DeltaDash] get_score: %s", e)
            return {"Total": 150, "ticker": ticker}


# ─────────────────────────────────────────────────────────────────────────────
# 4) HYBRID SUPER BRAIN v5.0 — THE FULLY CENTRAL ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class HybridSuperBrain:
    """🌍 FULLY CENTRAL BRAIN — ALL decisions route through here."""

    def __init__(self, config: Dict = None):
        self.config = config or {}

        # ── Sub-components ──────────────────────────────────────
        self.kronos    = KronosScheduler()
        self.smc       = SMCAnalyzer()
        self.deltadash = DeltaDashScoreboard()
        self.mirofish  = MiroFishLangGraph()
        self.survival  = MildSurvivalEngine(
            daily_target_pct=float(self.config.get("daily_target_pct", 0.5)),
        )

        # ── Brain state ─────────────────────────────────────────
        self.current_pnl_pct  = 0.0
        self.brain_enabled    = True
        self._decision_cache: Dict[str, Any] = {}
        self._cache_ttl       = 30.0

        # ── Lazy heavy imports ──────────────────────────────────
        self._dreamer_fn   = None   # rl_agent.dreamer_trainer.get_prediction
        self._risk_cls     = None   # RiskPortfolioManager class
        self._danger_fn    = None   # danger_scanner.async_danger_scan

        logger.info("🚀 HybridSuperBrain v5.0 — Fully Central Engine Online")

    # ── Lazy loaders ─────────────────────────────────────────────────────────

    def _get_dreamer_fn(self):
        if self._dreamer_fn is None:
            try:
                import importlib
                rl = importlib.import_module("rl_agent.dreamer_trainer")
                self._dreamer_fn = rl.get_prediction
            except Exception as e:
                logger.debug("[Brain] dreamer_fn unavailable: %s", e)
        return self._dreamer_fn

    def _get_risk_cls(self):
        if self._risk_cls is None:
            try:
                from .risk_portfolio_manager import RiskPortfolioManager
                self._risk_cls = RiskPortfolioManager
            except Exception as e:
                logger.debug("[Brain] RPM unavailable: %s", e)
        return self._risk_cls

    def _get_danger_fn(self):
        if self._danger_fn is None:
            try:
                from .danger_scanner import async_danger_scan
                self._danger_fn = async_danger_scan
            except Exception as e:
                logger.debug("[Brain] danger_fn unavailable: %s", e)
        return self._danger_fn

    # ── Layer helpers ─────────────────────────────────────────────────────────

    def _layer_dreamer(self, symbol: str, dreamer_confidence: Optional[float] = None) -> Dict:
        """Layer 2: DreamerV3 signal from RL world model."""
        if dreamer_confidence is not None:
            return {
                "signal":     "BUY" if dreamer_confidence > 60 else "SELL" if dreamer_confidence < 40 else "HOLD",
                "confidence": float(dreamer_confidence),
                "active":     True,
                "source":     "provided",
            }
        fn = self._get_dreamer_fn()
        if fn is not None:
            try:
                pred = fn(symbol)
                return {
                    "signal":          pred.get("signal", "HOLD"),
                    "confidence":      float(pred.get("confidence", 0)),
                    "strategy_weights": pred.get("strategy_weights", {}),
                    "wm_loss":         float(pred.get("wm_loss", 0.0)),
                    "active":          pred.get("signal", "HOLD") != "HOLD" or pred.get("confidence", 0) > 0,
                    "source":          "dreamerv3",
                }
            except Exception as e:
                logger.debug("[Brain] dreamer layer: %s", e)
        return {"signal": "HOLD", "confidence": 0.0, "active": False, "source": "unavailable"}

    def _layer_evolution_coeffs(self) -> Dict:
        """Layer 7: Adaptive trust weights from LayerEvolutionEngine."""
        try:
            from .layer_evolution import layer_evolution
            return layer_evolution.get_coefficients()
        except Exception:
            return {
                "fomo": 12.0, "apathy": 5.0, "regime": 4.0,
                "fear": 15.0, "meta_scale": 1.0, "dreamer_scale": 1.0,
            }

    def _layer_agent_consensus(self, symbol: str) -> Dict:
        """Layer 8: Strategy collaborator multi-agent consensus (from cache)."""
        try:
            from .dreamer_robo_orchestrator import _state, _lock
            with _lock:
                disc = dict(_state.get("agent_discussion") or {})
            if disc and disc.get("ticker", "").upper() == symbol.upper():
                return {
                    "signal":     disc.get("consensus", "HOLD"),
                    "confidence": float(disc.get("consensus_confidence", 0)),
                    "score":      float(disc.get("weighted_score", 0)),
                    "available":  True,
                }
        except Exception:
            pass
        return {"signal": "HOLD", "confidence": 0.0, "score": 0.0, "available": False}

    def _layer_market_context(self, market_data: Dict, symbol: str) -> Dict:
        """Layer 9a: Enrich market_data with live price / ATR if sparse."""
        ctx = dict(market_data or {})
        if "price" not in ctx or float(ctx.get("price", 0)) <= 0:
            try:
                from agents.dreamer_robo_orchestrator import _fetch_market_context
                live = _fetch_market_context(symbol)
                for k, v in live.items():
                    ctx.setdefault(k, v)
            except Exception:
                ctx.setdefault("price", 0.0)
                ctx.setdefault("atr_pct", 0.015)
                ctx.setdefault("atr14", 0.0)
                ctx.setdefault("regime", "UNKNOWN")
                ctx.setdefault("rsi14", 50.0)
        return ctx

    # ── Psychological Analysis ────────────────────────────────────────────────

    def _psychological_analysis(self, ctx: Dict, news: str = "") -> Dict:
        momentum = float(ctx.get("momentum_strength",
                        ctx.get("change_pct", 0.0) / 5.0 + 0.5))
        momentum  = max(0.0, min(1.0, momentum))
        vol       = float(ctx.get("atr_pct", ctx.get("volatility", 0.015)))
        rsi       = float(ctx.get("rsi14", 50.0))

        fomo   = min(0.95, momentum * 0.8 + vol * 22)
        apathy = max(0.0,  (1 - momentum) * 0.55)

        # RSI-based narrative credibility (overextended = less credible)
        cred = 1.0 - abs(rsi - 50.0) / 70.0
        cred = max(0.0, min(1.0, cred))

        regime = ctx.get("regime", "UNKNOWN")
        if regime in ("UNKNOWN", ""):
            regime = "bullish" if momentum > 0.65 else "ranging"

        return {
            "fomo_score":            round(fomo, 3),
            "apathy_score":          round(apathy, 3),
            "narrative_credibility": round(cred, 3),
            "regime":                regime,
            "momentum":              round(momentum, 3),
        }

    # ── Meta Reasoning ────────────────────────────────────────────────────────

    async def _top_trader_meta_reasoning(
        self,
        ctx:     Dict,
        psych:   Dict,
        miro:    Dict,
        dreamer: Dict,
        smc:     Dict,
        kronos:  Dict,
        agents:  Dict,
        coeffs:  Dict,
    ) -> Dict:
        fomo      = psych.get("fomo_score", 0.5)
        regime    = psych.get("regime", "ranging")
        miro_conf = miro.get("consensus_confidence", 50.0) / 100.0
        dr_conf   = dreamer.get("confidence", 0.0) / 100.0
        smc_score = float(smc.get("smc_score", 50)) / 100.0  # normalise to 0-1
        kronos_ph = kronos.get("phase", "unknown")
        kronos_adv = kronos.get("time_advantage", False)
        ag_conf   = agents.get("confidence", 0.0) / 100.0

        meta_scale = float(coeffs.get("meta_scale", 1.0))

        # Weighted edge
        edge = (
            miro_conf   * 0.25 * meta_scale +
            dr_conf     * 0.30 * float(coeffs.get("dreamer_scale", 1.0)) +
            smc_score   * 0.20 +
            fomo        * 0.10 +
            ag_conf     * 0.10 +
            (0.05 if kronos_adv else 0.0)
        )
        edge = max(0.0, min(1.0, edge))
        asymmetric = edge > 0.50

        # Summary
        lines = [
            f"Regime: {regime.upper()}",
            f"DreamerV3: {dreamer.get('signal','?')}({dr_conf:.0%})",
            f"MiroFish: {miro_conf:.0%}",
            f"SMC: {smc.get('signal','neutral').upper()}({smc.get('smc_score',50)})",
            f"Kronos: {kronos_ph}",
        ]
        if agents.get("available"):
            lines.append(f"Agents: {agents.get('signal','?')}({ag_conf:.0%})")
        summary = " | ".join(lines)

        # Recommendation
        bos_bull = smc.get("bos_bullish", False) or smc.get("order_block", False)
        bos_bear = smc.get("bos_bearish", False) or smc.get("fair_value_gap", False)
        dr_sig   = dreamer.get("signal", "HOLD")

        if bos_bull and dr_sig == "BUY" and miro_conf > 0.55:
            rec = "STRONG BUY — DreamerV3 + BOS/OB + MiroFish aligned"
        elif bos_bear and dr_sig == "SELL" and miro_conf < 0.45:
            rec = "STRONG SELL — DreamerV3 + SMC bearish + MiroFish consensus"
        elif asymmetric and dr_sig in ("BUY", "SELL"):
            rec = f"{dr_sig} — edge {edge:.0%} (DreamerV3 + SMC)"
        elif kronos_adv and dr_sig != "HOLD":
            rec = f"{dr_sig} — Kronos {kronos_ph} window (edge {edge:.0%})"
        else:
            rec = "HOLD — insufficient conviction"

        return {
            "edge_score":      round(edge, 3),
            "asymmetric_edge": asymmetric,
            "summary":         summary,
            "recommendation":  rec,
        }

    # ── Position Sizing ───────────────────────────────────────────────────────

    @staticmethod
    def _calc_position_size(
        signal:     str,
        ctx:        Dict,
        prefs=None,
        risk=None,
        size_scalar: float = 1.0,
    ) -> Dict:
        """Compute sl_price, tp_price, quantity, position_value."""
        price = float(ctx.get("price", 0.0))
        atr14 = float(ctx.get("atr14", price * 0.015 if price > 0 else 0.0))

        if signal == "BUY":
            sl_price = price - atr14 * ATR_MULT_SL
            tp_price = price + atr14 * ATR_MULT_TP
        elif signal == "SELL":
            sl_price = price + atr14 * ATR_MULT_SL
            tp_price = price - atr14 * ATR_MULT_TP
        else:
            sl_price = tp_price = price

        quantity = 1
        position_value = 0.0

        if prefs is not None and risk is not None and price > 0:
            try:
                alloc   = float(getattr(prefs, "allocated_capital", 0) or 0)
                rpt_pct = float(getattr(risk,  "risk_per_trade_pct", 1.0) or 1.0)
                rr      = float(getattr(risk,  "recommended_rr", 2.0) or 2.0)
                sl_dist = atr14 * ATR_MULT_SL
                if alloc > 0 and sl_dist > 0:
                    raw_qty  = (alloc * rpt_pct / 100.0) / sl_dist
                    quantity = max(1, int(round(raw_qty * size_scalar)))
                    tp_price = (price + atr14 * ATR_MULT_SL * rr) if signal == "BUY" \
                               else (price - atr14 * ATR_MULT_SL * rr)
                position_value = round(quantity * price, 2)
            except Exception as e:
                logger.debug("[Brain] position sizing: %s", e)

        return {
            "entry_price":    round(max(0.01, price), 2),
            "sl_price":       round(max(0.01, sl_price), 2) if signal != "HOLD" else None,
            "tp_price":       round(max(0.01, tp_price), 2) if signal != "HOLD" else None,
            "quantity":       quantity,
            "position_value": position_value,
            "direction":      1 if signal == "BUY" else (-1 if signal == "SELL" else 0),
        }

    # ── Elite Decision ────────────────────────────────────────────────────────

    def _elite_decision(
        self,
        miro:     Dict,
        dreamer:  Dict,
        delta:    Dict,
        psych:    Dict,
        meta:     Dict,
        smc:      Dict,
        kronos:   Dict,
        agents:   Dict,
        survival: Dict,
        coeffs:   Dict,
    ) -> Dict:
        fear         = float((survival or {}).get("fear", 0.0))
        fear_penalty = fear * float(coeffs.get("fear", 15.0))
        size_scalar  = max(0.2, 1.0 - fear * 0.8)

        dr_conf   = float(dreamer.get("confidence", 0.0))
        miro_conf = float(miro.get("consensus_confidence", 50.0))
        dt_total  = float(delta.get("Total", 150))
        smc_val   = float(smc.get("smc_score", 50)) * 0.5   # 0–50 range
        ag_conf   = float(agents.get("confidence", 0.0)) if agents.get("available") else 0.0
        fomo      = float(psych.get("fomo_score", 0.5))

        final_conf = (
            dr_conf   * 0.30 * float(coeffs.get("dreamer_scale", 1.0)) +
            miro_conf * 0.25 * float(coeffs.get("meta_scale", 1.0)) +
            dt_total / 3.0  * 0.15 +
            smc_val         * 0.15 +
            ag_conf         * 0.10 +
            fomo * float(coeffs.get("fomo", 12.0))
        ) - fear_penalty

        final_conf = max(20.0, min(97.0, final_conf))

        # ── Consensus vote ───────────────────────────────────────────────────
        votes = []
        if dreamer.get("signal") in ("BUY", "SELL"):
            votes.append(dreamer["signal"])
        if miro.get("signal") in ("BUY", "SELL"):
            votes.append(miro["signal"])
        if agents.get("available") and agents.get("signal") in ("BUY", "SELL"):
            votes.append(agents["signal"])
        if smc.get("signal") == "bullish":
            votes.append("BUY")
        elif smc.get("signal") == "bearish":
            votes.append("SELL")
        if kronos.get("time_advantage"):
            votes.append(dreamer.get("signal", "HOLD"))

        buy_votes  = votes.count("BUY")
        sell_votes = votes.count("SELL")
        majority   = max(buy_votes, sell_votes)
        total_v    = len(votes) or 1

        # ── Decision gate ────────────────────────────────────────────────────
        ob_signal   = smc.get("order_block", False)
        fvg_signal  = smc.get("fair_value_gap", False)
        meta_edge   = meta.get("asymmetric_edge", False)
        dr_sig      = dreamer.get("signal", "HOLD")

        if fear > 0.80:
            action = "HOLD"
        elif final_conf > 61 and meta_edge and ob_signal and buy_votes > sell_votes:
            action = "BUY"
        elif final_conf < 39 and fvg_signal and sell_votes > buy_votes:
            action = "SELL"
        elif final_conf > 65 and majority / total_v >= 0.6 and buy_votes > sell_votes:
            action = "BUY"
        elif final_conf > 65 and majority / total_v >= 0.6 and sell_votes > buy_votes:
            action = "SELL"
        elif final_conf > 70 and dr_sig in ("BUY", "SELL") and dr_conf > 60:
            action = dr_sig  # strong DreamerV3 alone
        else:
            action = "HOLD"

        strategy_consensus = (
            f"DV3={dreamer.get('signal','?')}({dr_conf:.0f}%) "
            f"MF={miro.get('signal','?')} "
            f"SMC={smc.get('signal','?')} "
            f"AG={'✓' if agents.get('available') else '—'}"
        )

        return {
            "action":             action,
            "confidence":         round(final_conf, 1),
            "reasoning":          meta.get("summary", ""),
            "smc_signal":         smc.get("signal", "neutral"),
            "smc_score":          smc.get("smc_score", 50),
            "kronos_cycle":       kronos.get("phase", "unknown"),
            "kronos_strength":    kronos.get("strength", 55),
            "top_trader_view":    meta.get("recommendation", "HOLD"),
            "fear_level":         round(fear, 3),
            "size_scalar":        round(size_scalar, 3),
            "psych":              psych,
            "survival":           survival or {},
            "strategy_consensus": strategy_consensus,
            "quality_score":      round(final_conf * (1 - fear * 0.5), 1),
            "dreamer":            dreamer,
            "agents":             agents,
            "components": {
                "dreamer_signal":  dreamer.get("signal", "HOLD"),
                "dreamer_conf":    round(dr_conf, 1),
                "miro_conf":       round(miro_conf, 1),
                "miro_signal":     miro.get("signal", "?"),
                "delta_total":     dt_total,
                "smc_score":       smc.get("smc_score", 50),
                "kronos_phase":    kronos.get("phase", "unknown"),
                "agent_signal":    agents.get("signal", "—"),
                "agent_conf":      round(ag_conf, 1),
                "buy_votes":       buy_votes,
                "sell_votes":      sell_votes,
            },
            "risk_alert": "danger" if fear > 0.6 else "warning" if fear > 0.3 else "good",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # ★ PRIMARY ENTRY POINT — All decisions flow through here ★
    # ─────────────────────────────────────────────────────────────────────────
    async def think_and_decide(
        self,
        market_data:         Dict  = None,
        news:                str   = "",
        symbol:              str   = "NIFTY",
        chart_data                 = None,
        dreamer_confidence:  Optional[float] = None,
        prefs                      = None,
        risk                       = None,
    ) -> Dict[str, Any]:
        """
        THE single entry point for all trading decisions.

        Parameters
        ----------
        market_data        : live OHLCV + indicators dict (enriched internally if sparse)
        news               : optional news headline string
        symbol             : ticker string (e.g. 'NIFTY', 'RELIANCE')
        chart_data         : optional list of OHLCV bars for SMC
        dreamer_confidence : optional pre-computed DreamerV3 confidence override
        prefs              : RoboPreferences object (for position sizing)
        risk               : RiskProfile object (for position sizing)
        """
        market_data = market_data or {}

        if not self.brain_enabled:
            return self._hold(symbol, reason="Brain disabled")

        # Cache check
        cache_key = f"{symbol}_{int(time.time() // self._cache_ttl)}"
        if cache_key in self._decision_cache:
            return self._decision_cache[cache_key]

        await self.survival.load()

        try:
            # ── Layer 1: Kronos circuit breaker ─────────────────────────────
            if not self.kronos.should_trade_now():
                return self._hold(symbol, reason="Outside NSE market hours")

            # ── Layer 2: DreamerV3 ───────────────────────────────────────────
            dreamer = self._layer_dreamer(symbol, dreamer_confidence)

            # ── Layer 3: SMC ─────────────────────────────────────────────────
            smc_input   = chart_data if chart_data else market_data
            smc_analysis = self.smc.analyze(smc_input)

            # ── Layer 4: Kronos cycle ─────────────────────────────────────────
            kronos_signal = self.kronos.get_cycle_signal(symbol)

            # ── Layer 5: DeltaDash ───────────────────────────────────────────
            delta_score = self.deltadash.get_score(symbol)

            # ── Layer 6: MiroFish (async, parallel) ──────────────────────────
            miro_task = asyncio.create_task(
                self.mirofish.run_analysis(market_data, news, chart_data)
            )

            # ── Layer 7: LayerEvolution ──────────────────────────────────────
            coeffs = self._layer_evolution_coeffs()

            # ── Layer 8: Agent Consensus ─────────────────────────────────────
            agents = self._layer_agent_consensus(symbol)

            # ── Layer 9: Market Context ──────────────────────────────────────
            ctx = self._layer_market_context(market_data, symbol)

            # ── Layer 10: Psychological + Survival ───────────────────────────
            psych           = self._psychological_analysis(ctx, news)
            survival_status = self.survival.update(self.current_pnl_pct)

            # ── Layer 6 result ────────────────────────────────────────────────
            try:
                miro_result = await asyncio.wait_for(miro_task, timeout=12.0)
            except Exception:
                miro_result = {"consensus_confidence": 50.0, "signal": "HOLD", "reasoning": "timeout", "agents": []}

            # ── Layer 11: Meta Reasoning ─────────────────────────────────────
            meta = await self._top_trader_meta_reasoning(
                ctx, psych, miro_result, dreamer,
                smc_analysis, kronos_signal, agents, coeffs,
            )

            # ── Layer 12: Elite Decision ─────────────────────────────────────
            decision = self._elite_decision(
                miro_result, dreamer, delta_score,
                psych, meta, smc_analysis, kronos_signal,
                agents, survival_status, coeffs,
            )
            decision["symbol"] = symbol

            # ── Layer 13: Position Sizing ────────────────────────────────────
            sizing = self._calc_position_size(
                decision["action"], ctx, prefs, risk, decision["size_scalar"]
            )
            decision.update(sizing)

            # Persist audit
            asyncio.create_task(self._save_audit(symbol, decision, meta))

            self._decision_cache[cache_key] = decision
            return decision

        except Exception as e:
            logger.error("[HybridBrain v5] think_and_decide error: %s", e, exc_info=True)
            return self._hold(symbol, reason=f"System Safety: {e}")

    # ── Audit ─────────────────────────────────────────────────────────────────

    async def _save_audit(self, symbol: str, decision: Dict, meta: Dict):
        try:
            doc = {
                "id":         str(uuid4()),
                "symbol":     symbol,
                "action":     decision.get("action"),
                "confidence": decision.get("confidence"),
                "reasoning":  decision.get("reasoning"),
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "meta":       meta,
            }
            await _get_db().hybrid_brain_audit.insert_one(doc)
        except Exception as e:
            logger.debug("[HybridBrain] audit save: %s", e)

    async def get_recent_audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            cursor = (
                _get_db().hybrid_brain_audit
                .find({}, {"_id": 0})
                .sort("timestamp", -1)
                .limit(limit)
            )
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.warning("[HybridBrain] audit fetch: %s", e)
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

    # ── Sync wrapper (trading_loop / orchestrator threads) ───────────────────

    def decide_sync(
        self,
        market_data:        Dict[str, Any] = None,
        news:               str = "",
        symbol:             str = "NIFTY",
        dreamer_confidence: Optional[float] = None,
        prefs               = None,
        risk                = None,
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper. Used by:
          • trading_loop.py worker thread
          • dreamer_robo_orchestrator worker thread
        """
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                self.think_and_decide(
                    market_data=market_data,
                    news=news,
                    symbol=symbol,
                    dreamer_confidence=dreamer_confidence,
                    prefs=prefs,
                    risk=risk,
                )
            )
        except Exception as e:
            logger.warning("[HybridBrain] decide_sync failed: %s", e)
            return self._hold(symbol, reason=f"decide_sync error: {e}")

    def decide_sync_full(
        self,
        ticker: str,
        prefs  = None,
        risk   = None,
        market_data: Dict = None,
        news:   str = "",
    ) -> Dict[str, Any]:
        """
        Full sync call with prefs + risk for complete position sizing.
        Used by dreamer_robo_orchestrator._get_dreamer_decision().
        """
        return self.decide_sync(
            market_data=market_data or {},
            news=news,
            symbol=ticker,
            prefs=prefs,
            risk=risk,
        )

    def update_daily_pnl_sync(self, pnl_pct: float):
        """Sync PnL update (called every tick by orchestrator/trading_loop)."""
        self.current_pnl_pct = float(pnl_pct)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _hold(self, symbol: str = "NIFTY", reason: str = "Safety HOLD") -> Dict:
        return {
            "action":          "HOLD",
            "confidence":      25.0,
            "reasoning":       reason,
            "symbol":          symbol,
            "smc_signal":      "neutral",
            "smc_score":       50,
            "kronos_cycle":    "unknown",
            "kronos_strength": 55,
            "top_trader_view": f"HOLD — {reason}",
            "fear_level":      round(self.survival.fear_level, 3),
            "size_scalar":     max(0.2, 1.0 - self.survival.fear_level),
            "psych":           {},
            "survival":        {"fear": self.survival.fear_level, "status": "hold"},
            "strategy_consensus": "—",
            "quality_score":   0.0,
            "dreamer":         {"signal": "HOLD", "confidence": 0.0},
            "agents":          {"available": False},
            "components":      {},
            "risk_alert":      "good",
            "entry_price":     0.0,
            "sl_price":        None,
            "tp_price":        None,
            "quantity":        0,
            "position_value":  0.0,
            "direction":       0,
        }


# ─── Singleton — import this everywhere ──────────────────────────────────────
hybrid_brain = HybridSuperBrain()
