"""
Adaptive Learning Engine — Robot 3.0 Self-Improvement
======================================================
Continuously learns from Kronos AI + trade outcomes + price validation
to dynamically optimise agent weights in the StrategyCollaborator.

How it works (exactly like DreamerV3 learns from Kronos in RL):
  ┌─────────────────────────────────────────────────────────┐
  │  DreamerV3 RL             │  Robot 3.0 Adaptive          │
  │  Kronos → reward shaping  │  Kronos → weight shaping     │
  │  Every 10 episodes        │  Every price validation      │
  │  Bonus ≤ 0.15             │  EMA accuracy update 0-100   │
  └─────────────────────────────────────────────────────────┘

Learning Sources:
  1. Trade Outcomes  — SL/TP hit → direct reward/penalty per agent
  2. Price Validation — 30 min after scan → check if prediction was correct
  3. Kronos Teacher Signal — if Kronos correct with high conf,
                             agents that disagreed get penalised extra

Algorithm:
  accuracy[agent] = EMA(accuracy[agent], 100 if correct else 0, lr)
  weight[agent]   = clip(accuracy[agent]/sum_accuracy, MIN, MAX)
  → normalise weights to sum = 1.0
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Starting weights (same as AGENT_WEIGHTS in strategy_collaborator) ─────────
BASE_WEIGHTS: Dict[str, float] = {
    "KronosAI":          0.22,
    "IntradayMomentum":  0.25,
    "TechComposite":     0.20,
    "Breakout15m":       0.18,
    "MiroFish":          0.10,
    "ActiveScanner":     0.05,
}

# ── Learning hyperparameters ───────────────────────────────────────────────────
LR_NORMAL      = 0.10   # base EMA learning rate
LR_KRONOS      = 0.13   # Kronos gets slightly faster lr (teacher)
LR_TRADE       = 0.20   # direct trade feedback is strongest signal
LR_TEACHER_PEN = 0.06   # penalty from Kronos teacher override

MIN_WEIGHT     = 0.04   # floor — no agent completely silenced
MAX_WEIGHT     = 0.42   # ceiling — no single agent dominates

VALIDATION_DELAY_MIN = 30   # validate prediction after 30 min
VALIDATION_LOOP_SEC  = 300  # background thread cadence: 5 min
MAX_HISTORY          = 200  # keep last 200 predictions


# ── Prediction record ──────────────────────────────────────────────────────────
@dataclass
class PredictionRecord:
    record_id:     str
    ticker:        str
    timestamp:     float           # unix epoch
    entry_price:   float
    consensus:     str             # BUY / SELL / HOLD
    agent_signals: Dict[str, str]  # {agent_name: signal}
    kronos_signal: str             # Kronos signal at scan time
    kronos_conf:   float           # Kronos confidence 0–100
    validated:     bool  = False
    outcome:       Optional[str] = None   # UP / DOWN / FLAT


# ── Weight change log entry ────────────────────────────────────────────────────
@dataclass
class WeightChange:
    agent:     str
    delta:     float   # positive = weight increased
    new_w:     float
    trigger:   str     # trade / price / kronos
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AdaptiveLearningEngine:
    """
    Self-improving weight engine for Robot 3.0.

    Exported singleton: ``learner`` (module-level).

    Usage:
        from .adaptive_learner import learner

        # After scan: store prediction for later validation
        learner.record_prediction(ticker, price, consensus, agent_signals,
                                  kronos_signal, kronos_conf)

        # After paper trade closes:
        learner.record_trade_outcome(ticker, trade_signal, outcome, agent_signals)

        # Get current dynamic weights for consensus blending:
        weights = learner.get_dynamic_weights()

        # Get full state for API/UI:
        state = learner.get_state()
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Accuracy scores per agent (EMA, clamped to 10–95)
        self._accuracy: Dict[str, float] = {a: 50.0 for a in BASE_WEIGHTS}

        # Current dynamic weights (start = base, evolve over time)
        self._weights: Dict[str, float] = dict(BASE_WEIGHTS)

        # Prediction history
        self._history: List[PredictionRecord] = []

        # Stats
        self.learning_iterations     = 0
        self.trade_outcomes_learned  = 0
        self.price_validations_done  = 0
        self.total_correct           = 0
        self.total_predictions       = 0

        # Weight change log (last 30)
        self._changes: List[WeightChange] = []

        # Best-performing agent (changes over time)
        self.best_agent: str = "KronosAI"

        # Background validation thread
        self._stop_evt = threading.Event()
        self._vthread  = threading.Thread(
            target=self._validation_loop, daemon=True, name="robo-adaptive-learner"
        )
        self._vthread.start()
        logger.info("[AdaptiveLearner] Engine started — %d agents tracked", len(BASE_WEIGHTS))

    # ── Public: record prediction for later validation ────────────────────────

    def record_prediction(
        self,
        ticker:        str,
        entry_price:   float,
        consensus:     str,
        agent_signals: Dict[str, str],
        kronos_signal: str   = "HOLD",
        kronos_conf:   float = 50.0,
    ) -> None:
        """Store scan prediction for price validation in 30 min."""
        if entry_price <= 0:
            return
        rec = PredictionRecord(
            record_id     = str(uuid.uuid4())[:8].upper(),
            ticker        = ticker,
            timestamp     = time.time(),
            entry_price   = entry_price,
            consensus     = consensus,
            agent_signals = dict(agent_signals),
            kronos_signal = kronos_signal,
            kronos_conf   = kronos_conf,
        )
        with self._lock:
            self._history.append(rec)
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[-MAX_HISTORY:]

        logger.debug("[AdaptiveLearner] Prediction recorded: %s %s @ ₹%.2f", ticker, consensus, entry_price)

    # ── Public: direct trade outcome feedback ─────────────────────────────────

    def record_trade_outcome(
        self,
        ticker:        str,
        trade_signal:  str,          # BUY / SELL — direction traded
        outcome:       str,          # WIN / LOSS
        agent_signals: Dict[str, str],
    ) -> None:
        """
        Called when paper trade closes (SL or TP hit).
        Direct highest-weight learning signal.
        """
        for agent_name, agent_sig in agent_signals.items():
            agreed  = (agent_sig == trade_signal)
            correct = (outcome == "WIN" and agreed) or (outcome == "LOSS" and not agreed)
            self._ema_update(agent_name, correct, lr=LR_TRADE)

        self.trade_outcomes_learned += 1
        old_w = dict(self._weights)
        self._rebalance("trade")
        self._log_weight_changes(old_w, "trade")

        logger.info(
            "[AdaptiveLearner] Trade feedback | %s %s → %s | iter=%d | best=%s",
            ticker, trade_signal, outcome, self.learning_iterations, self.best_agent,
        )

    # ── Public: getters ───────────────────────────────────────────────────────

    def get_dynamic_weights(self) -> Dict[str, float]:
        """Return current dynamic weights — drop-in replacement for static AGENT_WEIGHTS."""
        with self._lock:
            return dict(self._weights)

    def get_state(self) -> Dict:
        """Full state for API/UI."""
        with self._lock:
            weight_vs_base = {
                a: round(self._weights.get(a, 0) - BASE_WEIGHTS.get(a, 0), 4)
                for a in BASE_WEIGHTS
            }
            best = max(self._accuracy, key=self._accuracy.get, default="KronosAI")
            return {
                "agent_weights":          {k: round(v, 4) for k, v in self._weights.items()},
                "accuracy_scores":        {k: round(v, 1) for k, v in self._accuracy.items()},
                "base_weights":           dict(BASE_WEIGHTS),
                "weight_vs_base":         weight_vs_base,
                "best_agent":             best,
                "learning_iterations":    self.learning_iterations,
                "trade_outcomes_learned": self.trade_outcomes_learned,
                "price_validations_done": self.price_validations_done,
                "overall_accuracy":       round(
                    self.total_correct / max(self.total_predictions, 1) * 100, 1
                ),
                "pending_validations":    sum(1 for r in self._history if not r.validated),
                "recent_changes":         [
                    {
                        "agent":   c.agent,
                        "delta":   c.delta,
                        "new_w":   round(c.new_w, 4),
                        "trigger": c.trigger,
                        "ts":      c.timestamp,
                    }
                    for c in self._changes[-10:]
                ],
            }

    def reset_to_base(self) -> None:
        """Reset all weights and accuracy scores to defaults."""
        with self._lock:
            self._accuracy = {a: 50.0 for a in BASE_WEIGHTS}
            self._weights  = dict(BASE_WEIGHTS)
            self.learning_iterations    = 0
            self.trade_outcomes_learned = 0
            self.price_validations_done = 0
            self.total_correct          = 0
            self.total_predictions      = 0
            self._changes.clear()
        logger.info("[AdaptiveLearner] Reset to base weights")

    # ── Private: EMA update ───────────────────────────────────────────────────

    def _ema_update(self, agent_name: str, was_correct: bool, lr: float = LR_NORMAL) -> None:
        with self._lock:
            if agent_name not in self._accuracy:
                return
            target = 100.0 if was_correct else 0.0
            old    = self._accuracy[agent_name]
            new    = old + lr * (target - old)
            self._accuracy[agent_name] = round(float(np.clip(new, 10.0, 95.0)), 2)
            self.total_predictions += 1
            if was_correct:
                self.total_correct += 1

    def _rebalance(self, trigger: str = "price") -> None:
        """Compute new weights from accuracy scores (bounded + normalised)."""
        with self._lock:
            total = sum(self._accuracy.values()) + 1e-8
            new_w: Dict[str, float] = {}
            for a in BASE_WEIGHTS:
                raw = self._accuracy.get(a, 50.0) / total
                new_w[a] = float(np.clip(raw, MIN_WEIGHT, MAX_WEIGHT))

            # Normalise to sum = 1.0
            s = sum(new_w.values()) + 1e-8
            for a in new_w:
                new_w[a] = round(new_w[a] / s, 4)

            self._weights = new_w
            self.best_agent = max(self._accuracy, key=self._accuracy.get)
            self.learning_iterations += 1

    def _log_weight_changes(self, old_w: Dict[str, float], trigger: str) -> None:
        with self._lock:
            for a, new_val in self._weights.items():
                delta = round(new_val - old_w.get(a, BASE_WEIGHTS.get(a, 0.1)), 4)
                if abs(delta) >= 0.002:
                    self._changes.append(WeightChange(agent=a, delta=delta, new_w=new_val, trigger=trigger))
            self._changes = self._changes[-30:]

    # ── Background validation loop ────────────────────────────────────────────

    def _validation_loop(self) -> None:
        logger.info("[AdaptiveLearner] Validation loop started (every %ds)", VALIDATION_LOOP_SEC)
        while not self._stop_evt.is_set():
            try:
                self._run_validation_pass()
            except Exception as exc:
                logger.debug("[AdaptiveLearner] Validation pass error: %s", exc)
            self._stop_evt.wait(VALIDATION_LOOP_SEC)

    def _run_validation_pass(self) -> None:
        """Fetch current prices and validate predictions older than delay."""
        cutoff = time.time() - VALIDATION_DELAY_MIN * 60
        with self._lock:
            pending = [
                r for r in self._history
                if not r.validated and r.timestamp < cutoff and r.entry_price > 0
            ]

        if not pending:
            return

        tickers = list({r.ticker for r in pending})
        prices  = self._batch_price_fetch(tickers)
        if not prices:
            return

        validated_cnt = 0
        for rec in pending:
            cur = prices.get(rec.ticker)
            if not cur or cur <= 0:
                continue

            chg_pct = (cur - rec.entry_price) / (rec.entry_price + 1e-8) * 100
            if   chg_pct >= 0.35:   actual = "UP"
            elif chg_pct <= -0.35:  actual = "DOWN"
            else:                   actual = "FLAT"   # inconclusive

            rec.outcome   = actual
            rec.validated = True

            if actual == "FLAT":
                continue

            old_w = dict(self._weights)

            # ── Update each agent's accuracy ──────────────────────────
            for agent_name, agent_sig in rec.agent_signals.items():
                if agent_sig == "HOLD":
                    continue  # HOLD = no directional bet → skip
                correct = (
                    (agent_sig == "BUY"  and actual == "UP") or
                    (agent_sig == "SELL" and actual == "DOWN")
                )
                self._ema_update(agent_name, correct, lr=LR_NORMAL)

            # ── Kronos teacher signal ─────────────────────────────────
            # If Kronos was right with high confidence,
            # agents that DISAGREED with Kronos get extra penalised.
            k_sig  = rec.kronos_signal
            k_conf = rec.kronos_conf / 100.0
            k_right = (
                (k_sig == "BUY"  and actual == "UP") or
                (k_sig == "SELL" and actual == "DOWN")
            )
            if k_sig not in ("HOLD", ""):
                # Confidence-scaled Kronos lr
                k_lr = LR_KRONOS * (0.5 + 0.5 * k_conf)
                self._ema_update("KronosAI", k_right, lr=k_lr)

                # Teacher override: if Kronos was highly confident AND correct
                # → penalise agents that contradicted Kronos
                if k_right and k_conf >= 0.65:
                    for agent_name, agent_sig in rec.agent_signals.items():
                        if agent_name == "KronosAI":
                            continue
                        contradicted = (
                            (k_sig == "BUY"  and agent_sig == "SELL") or
                            (k_sig == "SELL" and agent_sig == "BUY")
                        )
                        if contradicted:
                            self._ema_update(agent_name, False, lr=LR_TEACHER_PEN)

            self._rebalance("price")
            self._log_weight_changes(old_w, "kronos" if k_right and k_conf >= 0.65 else "price")
            validated_cnt += 1

        if validated_cnt:
            self.price_validations_done += validated_cnt
            logger.info(
                "[AdaptiveLearner] Validated %d predictions | best=%s (%.1f%% acc) | iter=%d",
                validated_cnt,
                max(self._accuracy, key=self._accuracy.get),
                max(self._accuracy.values()),
                self.learning_iterations,
            )

    @staticmethod
    def _batch_price_fetch(tickers: List[str]) -> Dict[str, float]:
        try:
            import yfinance as yf
            import pandas as pd
            raw = yf.download(tickers, period="1d", interval="1m",
                              progress=False, auto_adjust=True)
            if raw.empty:
                return {}
            prices: Dict[str, float] = {}
            close = raw["Close"]
            if isinstance(close, pd.Series):
                # Single ticker
                prices[tickers[0]] = float(close.iloc[-1])
            else:
                for t in tickers:
                    if t in close.columns:
                        val = close[t].dropna()
                        if len(val):
                            prices[t] = float(val.iloc[-1])
            return prices
        except Exception as exc:
            logger.debug("[AdaptiveLearner] Price fetch error: %s", exc)
            return {}


# ── Module-level singleton ────────────────────────────────────────────────────
learner = AdaptiveLearningEngine()

__all__ = ["AdaptiveLearningEngine", "learner", "BASE_WEIGHTS"]
