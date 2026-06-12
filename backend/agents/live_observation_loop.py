"""
LiveObservationLoop
====================
Dedicated background loop that continuously feeds real-market observations
into DreamerV3's live training buffer — independent of the trading loop.

This ensures DreamerV3 Live Training stays in "EVOLVING" state for ALL
watchlist stocks regardless of whether auto-trading mode is ON or OFF.

Behaviour:
  • Fetches live market context for every ticker in the watch set
  • Builds 38-dim obs vectors and pushes them into the dreamer live buffer
  • Triggers mini world-model + actor-critic updates via push_live_experience
  • Auto-starts when any ticker is added to watchlist or selected as primary
  • Runs in a daemon thread — survives backend hot-reloads

Interval: 60 seconds by default (keeps market data fresh without hammering yfinance)
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Ensure backend package is on path for cross-imports
_BACKEND_DIR = str(Path(__file__).parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

DEFAULT_INTERVAL_S = 60   # seconds between observation cycles
MIN_INTERVAL_S     = 30
MAX_INTERVAL_S     = 300


class LiveObservationLoop:
    """
    Lightweight observation-only loop.
    No order execution — pure market data → DreamerV3 training pipeline.
    """

    def __init__(self):
        self._thread:     Optional[threading.Thread] = None
        self._stop_evt:   threading.Event             = threading.Event()
        self._lock:       threading.Lock              = threading.Lock()
        self._tickers:    List[str]                   = []
        self._running:    bool                        = False
        self._interval_s: int                         = DEFAULT_INTERVAL_S
        self._cycle_count: int                        = 0
        self._last_cycle_time: Optional[str]          = None
        self._last_cycle_tickers: List[str]           = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def start_or_update(self, tickers: List[str], interval_s: int = DEFAULT_INTERVAL_S) -> Dict:
        """
        Start the loop (if not already running) and set the active ticker list.
        Safe to call multiple times — idempotent when already running.
        """
        clean = [t.strip().upper() for t in tickers if t.strip()]
        if not clean:
            return {"success": False, "message": "No tickers provided — loop not started"}

        interval_s = int(max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, interval_s)))

        with self._lock:
            self._tickers    = clean
            self._interval_s = interval_s

            if self._running:
                logger.info("[LiveObsLoop] Tickers updated → %s", clean)
                return {
                    "success":  True,
                    "message":  f"Tickers updated ({len(clean)})",
                    "tickers":  clean,
                    "running":  True,
                }

            # Not running → start now
            self._stop_evt.clear()
            self._running = True

        self._thread = threading.Thread(
            target    = self._run_loop,
            daemon    = True,
            name      = "live-obs-loop",
        )
        self._thread.start()
        logger.info("[LiveObsLoop] Started | tickers=%s interval=%ds", clean, interval_s)
        return {
            "success": True,
            "message": f"Live observation loop started for {len(clean)} ticker(s)",
            "tickers": clean,
        }

    def stop(self) -> Dict:
        self._stop_evt.set()
        with self._lock:
            self._running = False
        logger.info("[LiveObsLoop] Stop requested")
        return {"success": True, "message": "Live observation loop stopping"}

    def get_status(self) -> Dict:
        with self._lock:
            return {
                "running":             self._running,
                "tickers":             list(self._tickers),
                "cycle_count":         self._cycle_count,
                "last_cycle_time":     self._last_cycle_time,
                "last_cycle_tickers":  list(self._last_cycle_tickers),
                "interval_s":          self._interval_s,
            }

    # ── Main Loop ──────────────────────────────────────────────────────────────

    def _run_loop(self):
        logger.info("[LiveObsLoop] Thread started")

        # Small initial delay so the HTTP response returns first
        time.sleep(5)

        while not self._stop_evt.is_set():
            t_start = time.time()

            with self._lock:
                tickers    = list(self._tickers)
                interval_s = self._interval_s

            if tickers:
                trained = self._observation_cycle(tickers)
                with self._lock:
                    self._cycle_count          += 1
                    self._last_cycle_time       = datetime.now(timezone.utc).isoformat()
                    self._last_cycle_tickers    = trained
            else:
                logger.debug("[LiveObsLoop] No tickers — idle this cycle")

            # Sleep with early-exit on stop
            elapsed   = time.time() - t_start
            sleep_rem = max(0.0, interval_s - elapsed)
            deadline  = time.time() + sleep_rem
            while time.time() < deadline:
                if self._stop_evt.is_set():
                    break
                time.sleep(1.0)

        with self._lock:
            self._running = False
        logger.info("[LiveObsLoop] Thread exiting")

    # ── Observation Cycle ──────────────────────────────────────────────────────

    def _observation_cycle(self, tickers: List[str]) -> List[str]:
        """
        For each ticker: fetch live market context → build obs → push experience.
        Returns list of tickers that were successfully processed.
        """
        trained: List[str] = []
        try:
            from agents.dreamer_robo_orchestrator import _fetch_market_context
            from rl_agent.dreamer_trainer import push_live_experience, build_live_obs
        except Exception as imp_e:
            logger.warning("[LiveObsLoop] Import failed: %s", imp_e)
            return trained

        for ticker in tickers:
            if self._stop_evt.is_set():
                break
            try:
                ctx        = _fetch_market_context(ticker)
                live_price = float(ctx.get("price", 0.0))
                if live_price <= 0:
                    continue

                regime = ctx.get("regime", "UNKNOWN")
                rsi14  = float(ctx.get("rsi14", 50.0))

                # Build observation (pure-observation, no position held)
                obs = build_live_obs(
                    ctx,
                    position       = 0.0,
                    entry_price    = 0.0,
                    capital_health = 1.0,
                )

                # Neutral HOLD action vector
                action         = np.zeros(16, dtype=np.float32)
                action[12]     = 0.0   # no directional signal
                action[13]     = 0.5   # SL mid
                action[14]     = 0.5   # TP mid
                action[15]     = 0.3   # conservative exposure

                # Reward proxy: small positive signal aligned with trend
                if regime == "UPTREND" and rsi14 < 65:
                    reward = 0.30
                elif regime == "DOWNTREND" and rsi14 > 35:
                    reward = 0.30
                elif regime in ("UPTREND", "DOWNTREND"):
                    reward = 0.15
                else:
                    reward = 0.08   # ranging — low information

                push_live_experience(ticker, obs, action, reward, obs, done=0.0)
                trained.append(ticker)

                logger.debug(
                    "[LiveObsLoop] %s observed | regime=%s rsi=%.1f reward=%.2f",
                    ticker, regime, rsi14, reward,
                )

            except Exception as exc:
                logger.debug("[LiveObsLoop] %s observation failed: %s", ticker, exc)

        if trained:
            logger.info("[LiveObsLoop] Cycle done | %d/%d tickers observed",
                        len(trained), len(tickers))
        return trained


# ── Module-level singleton ─────────────────────────────────────────────────────
live_obs_loop = LiveObservationLoop()

__all__ = ["LiveObservationLoop", "live_obs_loop"]
