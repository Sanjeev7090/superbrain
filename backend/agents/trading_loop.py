"""
Trading Loop — Phase 3
=======================
Autonomous scan-and-execute loop for the Dreamer V3 Robo-Trader.

Architecture:
  Uses APScheduler BackgroundScheduler (already in requirements: APScheduler==3.11.2).
  Each cycle runs in a background thread (thread-pool executor).

Scan Cycle (every N minutes, configurable 1–30):
  1.  Market hours check       → skip if NSE closed (Mon–Fri 09:15–15:30 IST)
  2.  Circuit breaker check    → skip cycle if kill-switch is active
  3.  Fetch live price + ATR   → via yfinance (Groww fallback if available)
  4.  Check open positions     → auto-close SL/TP hits
  5.  EOD management           → close all at 15:15 IST, reset at 09:15
  6.  DreamerV3 decision       → confidence + direction (from existing dreamer_trainer)
  7.  RPM risk sizing          → position size, SL, TP via RiskPortfolioManager
  8.  Meta decision            → combine DreamerV3 + technical signal (regime, RSI, vol)
  9.  Execute                  → via ExecutionEngine (paper / live / shadow)
  10. Update orchestrator state→ daily_pnl, target_pct, last_decision, etc.

Safety Features:
  • NSE market hours gate      (09:15–15:30 IST, weekdays only)
  • High-volatility pause      (India VIX proxy: ATR > 3.5% → reduce confidence)
  • Circuit breaker integration (daily loss, drawdown, consecutive losses)
  • EOD forced close           (15:15 IST cutoff)
  • Graceful stop              (current cycle finishes before stopping)

DISCLAIMER: No guaranteed returns. Paper mode default. SEBI-registered advisor recommended.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── APScheduler ────────────────────────────────────────────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.executors.pool import ThreadPoolExecutor as ApsThreadPool
    _SCHEDULER_AVAILABLE = True
except ImportError:
    _SCHEDULER_AVAILABLE = False
    logger.warning("[TradingLoop] APScheduler not installed — falling back to threading.Timer")

# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_INTERVAL_MIN  = 5     # scan every 5 minutes (minimum 1, maximum 30)
MIN_INTERVAL_MIN      = 1
MAX_INTERVAL_MIN      = 30
META_CONFIDENCE_FLOOR = 30    # minimum meta-confidence to execute (paper) — matches ExecEngine MIN_CONFIDENCE_PAPER
META_CONFIDENCE_LIVE  = 50    # live mode threshold

# NSE trading hours (IST = UTC+5:30)
NSE_OPEN_H,  NSE_OPEN_M  = 9,  15
NSE_CLOSE_H, NSE_CLOSE_M = 15, 30
EOD_CLOSE_H, EOD_CLOSE_M = 15, 15   # close all positions at 15:15

# Technical signal weights
W_DREAMER    = 0.60   # DreamerV3 weight (when active)
W_TECHNICAL  = 0.40   # Technical composite weight

# Volatility circuit breaker
HIGH_VOL_ATR_PCT = 0.035   # ATR > 3.5% → "high volatility"
EXTREME_VOL_PCT  = 0.055   # ATR > 5.5% → force HOLD


# ════════════════════════════════════════════════════════════════════════════════
# TRADING LOOP
# ════════════════════════════════════════════════════════════════════════════════

class TradingLoop:
    """
    Autonomous scan-and-execute loop.

    Usage:
        loop = TradingLoop()
        loop.start(interval_minutes=5)
        ...
        loop.stop()
    """

    def __init__(self) -> None:
        self._scheduler:    Optional[BackgroundScheduler] = None
        self._timer:        Optional[threading.Timer]     = None   # fallback
        self._running:      bool  = False
        self._interval_min: int   = DEFAULT_INTERVAL_MIN
        self._lock:         threading.Lock = threading.Lock()
        self._cycle_count:  int   = 0
        self._eod_closed:   bool  = False   # tracks if EOD close ran today
        self._today_str:    str   = ""      # "YYYY-MM-DD" for EOD reset detection

        # State exposed to orchestrator + API
        self._loop_state: Dict = {
            "running":           False,
            "interval_minutes":  DEFAULT_INTERVAL_MIN,
            "cycle_count":       0,
            "last_cycle_time":   None,
            "next_cycle_time":   None,
            "last_cycle_status": "idle",
            "last_error":        None,
            "market_open":       False,
            "eod_closed":        False,
        }

        logger.info("[TradingLoop] Initialised | default_interval=%dmin", self._interval_min)

    # ── Start / Stop ───────────────────────────────────────────────────────────

    def start(self, interval_minutes: int = DEFAULT_INTERVAL_MIN) -> Dict:
        """Start the periodic scan loop."""
        interval_minutes = int(np.clip(interval_minutes, MIN_INTERVAL_MIN, MAX_INTERVAL_MIN))

        with self._lock:
            if self._running:
                return {"success": False, "error": "Trading loop already running"}
            self._interval_min = interval_minutes
            self._running      = True
            self._cycle_count  = 0

        if _SCHEDULER_AVAILABLE:
            self._start_with_apscheduler(interval_minutes)
        else:
            self._start_with_timer(interval_minutes)

        with self._lock:
            self._loop_state.update({
                "running":          True,
                "interval_minutes": interval_minutes,
                "last_cycle_status": "started",
            })

        logger.info("[TradingLoop] Started | interval=%dmin | scheduler=%s",
                    interval_minutes, "APScheduler" if _SCHEDULER_AVAILABLE else "Timer")
        return {
            "success":          True,
            "interval_minutes": interval_minutes,
            "message":          f"Trading loop started — scanning every {interval_minutes} min",
        }

    def _start_with_apscheduler(self, interval_minutes: int) -> None:
        executors = {"default": ApsThreadPool(max_workers=1)}
        self._scheduler = BackgroundScheduler(executors=executors)
        self._scheduler.add_job(
            self._run_cycle,
            trigger   = "interval",
            minutes   = interval_minutes,
            id        = "trading_loop",
            max_instances = 1,    # never overlap cycles
            coalesce  = True,
        )
        # Run once immediately on start (after 5 seconds)
        self._scheduler.add_job(
            self._run_cycle,
            trigger   = "date",
            run_date  = datetime.now(timezone.utc) + timedelta(seconds=5),
            id        = "trading_loop_immediate",
        )
        self._scheduler.start()
        logger.debug("[TradingLoop] APScheduler started")

    def _start_with_timer(self, interval_minutes: int) -> None:
        """Fallback: threading.Timer-based loop."""
        def _loop_body():
            if not self._running:
                return
            try:
                self._run_cycle()
            finally:
                with self._lock:
                    running = self._running
                if running:
                    self._timer = threading.Timer(
                        interval_minutes * 60,
                        _loop_body,
                    )
                    self._timer.daemon = True
                    self._timer.start()

        self._timer = threading.Timer(5, _loop_body)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> Dict:
        """Stop the loop gracefully (current cycle finishes first)."""
        with self._lock:
            if not self._running:
                return {"success": False, "error": "Loop not running"}
            self._running = False

        if self._scheduler and self._scheduler.running:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

        if self._timer:
            self._timer.cancel()
            self._timer = None

        with self._lock:
            self._loop_state.update({
                "running":          False,
                "last_cycle_status": "stopped",
            })

        logger.info("[TradingLoop] Stopped")
        return {"success": True, "message": "Trading loop stopped"}

    def set_interval(self, minutes: int) -> Dict:
        """
        Change scan interval on the fly.
        Restarts the scheduler with new interval.
        """
        minutes = int(np.clip(minutes, MIN_INTERVAL_MIN, MAX_INTERVAL_MIN))
        was_running = self._running
        if was_running:
            self.stop()
            time.sleep(1)
            return self.start(interval_minutes=minutes)
        with self._lock:
            self._interval_min = minutes
            self._loop_state["interval_minutes"] = minutes
        return {"success": True, "interval_minutes": minutes}

    def get_status(self) -> Dict:
        with self._lock:
            return dict(self._loop_state)

    # ── Main Scan Cycle ────────────────────────────────────────────────────────

    def _run_cycle(self) -> None:
        """
        One full scan cycle. Thread-safe. Never raises — all errors caught.

        Multi-stock parallel trading:
          • Reads watchlist from _prefs (falls back to single _prefs.ticker)
          • Scans each ticker independently
          • Allows up to max_parallel_trades concurrent positions
          • Each ticker gets independent DreamerV3 + technical analysis
        """
        cycle_id = f"C{self._cycle_count + 1:04d}"
        t_start  = time.perf_counter()
        logger.info("[TradingLoop][%s] Cycle start", cycle_id)

        try:
            # ── Import heavy deps locally to avoid circular imports ──────────
            parent_dir = str(Path(__file__).parent.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)

            from agents.execution_engine import engine
            from agents.dreamer_robo_orchestrator import (
                _prefs, _state, _upd, _lock,
                _recalculate_risk_full, _recalculate_risk, _check_circuit_breakers,
                get_robo_state,
            )
            from agents.risk_portfolio_manager import rpm

            with self._lock:
                self._cycle_count += 1
            now_utc = datetime.now(timezone.utc)
            now_ist = now_utc + timedelta(hours=5, minutes=30)

            # ── Step 1: Market hours ─────────────────────────────────────────
            market_open = self._is_market_open(now_ist)
            self._update_state("market_open", market_open)

            # Keep brain active flag alive regardless of market hours
            # (set by warmup task at start, refreshed each cycle when market open)
            if _state.get("brain_active"):
                self._update_state("brain_active", True)

            if not market_open:
                logger.info("[TradingLoop][%s] Market closed (%s IST) — skipping",
                            cycle_id, now_ist.strftime("%H:%M %a"))
                self._update_state("last_cycle_status", "market_closed")
                return

            prefs = _prefs

            # ── Step 2: Daily reset on new day ───────────────────────────────
            today_str = now_ist.strftime("%Y-%m-%d")
            if today_str != self._today_str:
                self._today_str = today_str
                self._eod_closed = False
                engine.reset_daily()
                _upd(daily_pnl=0.0, daily_trades=0, daily_target_pct=0.0,
                     daily_drawdown=0.0, circuit_breaker=False, circuit_reason=None)
                logger.info("[TradingLoop][%s] New day reset | %s", cycle_id, today_str)

            # ── Step 3: EOD forced close (15:15 IST) ────────────────────────
            eod_cutoff = now_ist.replace(
                hour=EOD_CLOSE_H, minute=EOD_CLOSE_M, second=0, microsecond=0
            )
            if now_ist >= eod_cutoff and not self._eod_closed:
                logger.info("[TradingLoop][%s] EOD cutoff — closing all positions", cycle_id)
                # Close all positions across all tickers
                all_open = engine.get_open_positions()
                if all_open:
                    prices = {}
                    for pos in all_open:
                        tkr = pos.get("ticker", prefs.ticker)
                        if tkr not in prices:
                            p = self._fetch_price(tkr)
                            prices[tkr] = p or pos.get("entry_price", 0.0)
                    engine.close_all_positions(prices, reason="EOD")
                self._eod_closed = True
                _upd(open_trade=None, status="idle")
                self._update_state("eod_closed", True)
                self._update_state("last_cycle_status", "eod_close")
                return

            # ── Step 4: Build ticker watchlist ───────────────────────────────
            watchlist = list(prefs.watchlist) if prefs.watchlist else []
            if prefs.ticker not in watchlist:
                watchlist.insert(0, prefs.ticker)  # primary ticker always in list
            max_parallel = getattr(prefs, 'max_parallel_trades', 3)
            # Sync engine max positions
            engine.set_max_positions(max_parallel)

            # ── DANGER MODE: Override watchlist with F&O universe scan ────────
            if getattr(prefs, 'risk_tolerance', 'moderate') == 'danger':
                try:
                    from .danger_scanner import run_danger_scan
                    picks = run_danger_scan(top_n=max_parallel + 2)
                    if picks:
                        danger_tickers = [p["ticker"] for p in picks]
                        logger.info(
                            "[TradingLoop][%s] DANGER MODE — F&O scan picks: %s",
                            cycle_id, danger_tickers[:max_parallel]
                        )
                        _upd(danger_picks=danger_tickers)
                        watchlist = danger_tickers[:max_parallel + 2]
                except Exception as _de:
                    logger.warning("[TradingLoop][%s] Danger scan failed, using configured watchlist: %s", cycle_id, _de)

            # ── Step 5: Check ALL open positions (SL/TP) for ALL tickers ─────
            all_open_before = engine.get_open_positions()
            for pos in list(all_open_before):
                tkr = pos.get("ticker", prefs.ticker)
                price = self._fetch_price(tkr)
                if price and price > 0:
                    closed_positions = engine.check_positions(tkr, price)
                    for cp in closed_positions:
                        pnl = cp.get("pnl", 0.0) or 0.0
                        with _lock:
                            _state["daily_pnl"]       += pnl
                            _state["total_paper_pnl"] += pnl
                            _state["total_trades"]    += 1
                            _state["current_capital"] += pnl
                            _state["peak_capital"]     = max(_state["peak_capital"],
                                                              _state["current_capital"])
                            if pnl >= 0:
                                _state["win_trades"]         += 1
                                _state["consecutive_losses"]  = 0
                            else:
                                _state["loss_trades"]         += 1
                                _state["consecutive_losses"]  += 1
                            _state["audit_trail"] = ([cp] + _state["audit_trail"])[:100]
                            target = _state.get("daily_profit_target") or prefs.daily_profit_target
                            _state["daily_target_pct"] = (
                                _state["daily_pnl"] / target * 100 if target > 0 else 0.0
                            )
                        # Update open_trade to remaining open positions
                        remaining = engine.get_open_positions()
                        _upd(open_trade=remaining[0] if remaining else None)
                        # Telegram notification
                        try:
                            from agents.telegram_notifier import notify_trade_closed, notify_daily_target_reached
                            notify_trade_closed(cp)
                            with _lock:
                                dpnl_now = _state.get("daily_pnl", 0.0)
                                tgt_now  = _state.get("daily_profit_target") or prefs.daily_profit_target
                            if dpnl_now >= tgt_now > 0:
                                notify_daily_target_reached(dpnl_now, tgt_now)
                        except Exception:
                            pass
                        logger.info("[TradingLoop][%s] Position closed | %s P&L=₹%.2f | reason=%s",
                                    cycle_id, tkr, pnl, cp.get("exit_reason", "?"))

            # ── Step 6: Circuit breaker check ────────────────────────────────
            risk = _recalculate_risk(prefs)
            tripped, reason = _check_circuit_breakers(prefs, risk)
            if tripped:
                all_open = engine.get_open_positions()
                if all_open:
                    prices = {pos.get("ticker", prefs.ticker): self._fetch_price(pos.get("ticker", prefs.ticker)) or pos.get("entry_price", 0.0) for pos in all_open}
                    engine.close_all_positions(prices, reason="CIRCUIT_BREAKER")
                _upd(circuit_breaker=True, circuit_reason=reason,
                     status="circuit_breaker", auto_mode=False, open_trade=None)
                logger.warning("[TradingLoop][%s] CIRCUIT BREAKER: %s", cycle_id, reason)
                try:
                    from agents.telegram_notifier import notify_circuit_breaker
                    notify_circuit_breaker(reason)
                except Exception:
                    pass
                self.stop()
                self._update_state("last_cycle_status", "circuit_breaker")
                return

            # ── Step 7: RPM full recalculate ──────────────────────────────────
            with _lock:
                current_pnl   = _state.get("daily_pnl", 0.0)
                daily_trades  = _state.get("daily_trades", 0)
            risk_profile = _recalculate_risk_full(
                trigger      = "loop_cycle",
                current_pnl  = current_pnl,
                trades_today = daily_trades,
            )
            _upd(risk_profile=risk_profile)

            if risk_profile.get("should_stop_trading"):
                logger.info("[TradingLoop][%s] RPM budget says STOP — skipping entry", cycle_id)
                self._update_state("last_cycle_status", "budget_stop")
                return

            # ── Step 8: Multi-stock scan & execute ────────────────────────────
            current_open_count = len(engine.get_open_positions())
            entries_this_cycle = 0
            last_signal = "HOLD"
            last_conf   = 0

            for ticker in watchlist:
                # Stop if max parallel reached
                if current_open_count + entries_this_cycle >= max_parallel:
                    logger.info("[TradingLoop][%s] Max parallel (%d) reached — stopping scan",
                                cycle_id, max_parallel)
                    break

                # Skip if this ticker already has an open position
                if engine.has_open_position_for(ticker):
                    logger.debug("[TradingLoop][%s] %s already has open position — skipping",
                                 cycle_id, ticker)
                    continue

                # Fetch market context for this ticker
                ctx = self._fetch_market_context(ticker)
                live_price = ctx.get("price", 0.0)

                if live_price <= 0:
                    logger.warning("[TradingLoop][%s] %s — price fetch failed, skipping",
                                   cycle_id, ticker)
                    continue

                # DreamerV3 decision
                dreamer_dec = self._get_dreamer_decision_safe(prefs, risk)

                # ── Hybrid Brain sync on each cycle ──────────────────────────
                brain_block = {}
                try:
                    from agents.hybrid_super_brain import hybrid_brain
                    daily_pnl_pct = _state.get("daily_pnl", 0.0) / max(prefs.allocated_capital, 1.0)
                    hybrid_brain.update_daily_pnl_sync(daily_pnl_pct)
                    brain_block = hybrid_brain.decide_sync(
                        market_data=ctx,
                        symbol=ticker.replace(".NS", "").replace(".BO", "").upper(),
                        dreamer_confidence=dreamer_dec.get("confidence", 60.0),
                    )
                    _upd(
                        brain_active=True,
                        brain_action=brain_block.get("action", "HOLD"),
                        brain_confidence=brain_block.get("confidence", 0.0),
                        brain_fear=brain_block.get("survival", {}).get("fear", 0.0),
                        brain_regime=brain_block.get("psych", {}).get("regime", ""),
                        brain_last_decision=brain_block,
                    )
                except Exception as _be:
                    logger.debug("[TradingLoop][%s] Brain sync failed: %s", cycle_id, _be)

                # Meta decision (brain-aware)
                meta = self._compute_meta_decision(dreamer_dec, ctx, risk_profile)

                # ── Brain override logic ──────────────────────────────────────
                # If brain and dreamer agree → boost confidence
                # If brain vetoes (HOLD, high fear) → downgrade signal
                if brain_block:
                    brain_action = brain_block.get("action", "HOLD")
                    brain_conf   = brain_block.get("confidence", 50.0)
                    brain_fear   = brain_block.get("survival", {}).get("fear", 0.0)
                    meta_signal  = meta.get("signal", "HOLD")
                    meta_conf    = meta.get("confidence", 0)

                    # Alignment boost: both agree → +10% confidence
                    if brain_action == meta_signal and meta_signal != "HOLD":
                        boosted_conf = min(100, meta_conf + 10)
                        meta = {
                            **meta, "confidence": boosted_conf, "brain_boost": True,
                            "brain_reason": f"Brain+Dreamer agreed → {meta_signal} | Brain {brain_conf:.0f}% conf +10 boost",
                        }
                    # Fear circuit: brain fear > 0.7 → force HOLD
                    elif brain_fear > 0.70:
                        meta = {
                            **meta, "signal": "HOLD", "confidence": 0,
                            "brain_override": f"HOLD — brain fear {brain_fear:.2f}",
                            "brain_reason": f"Brain CIRCUIT-BREAKER: fear={brain_fear:.0%} → forced HOLD",
                        }
                        logger.info("[TradingLoop][%s] Brain fear=%.2f → forced HOLD for %s",
                                    cycle_id, brain_fear, ticker)
                    # Disagreement: brain says opposite → reduce confidence by 15%
                    elif brain_action != "HOLD" and brain_action != meta_signal:
                        reduced_conf = max(0, meta_conf - 15)
                        meta = {
                            **meta, "confidence": reduced_conf, "brain_disagree": True,
                            "brain_reason": f"Brain disagrees ({brain_action} vs Dreamer {meta_signal}) → −15 conf penalty",
                        }
                    else:
                        # Brain neutral (HOLD) while dreamer has signal — note it
                        meta = {
                            **meta,
                            "brain_reason": f"Brain neutral (HOLD) | Dreamer {meta_signal} {meta_conf:.0f}%",
                        }

                _upd(
                    current_decision    = {
                        **dreamer_dec, "meta": meta, "ticker": ticker,
                        "brain": brain_block,
                    },
                    dreamer_signal      = dreamer_dec.get("direction", 0.0),
                    dreamer_confidence  = dreamer_dec.get("confidence", 0),
                    dreamer_weights     = dreamer_dec.get("strategy_weights", {}),
                    dreamer_wm_loss     = dreamer_dec.get("wm_loss", 0.0),
                    last_decision_time  = datetime.now(timezone.utc).isoformat(),
                    status              = "scanning",
                )

                signal     = meta.get("signal", "HOLD")
                confidence = meta.get("confidence", 0)
                last_signal = signal
                last_conf   = confidence

                # Mode-aware confidence floor
                try:
                    from agents.execution_engine import MODE_LIVE as _MODE_LIVE
                    conf_floor = META_CONFIDENCE_LIVE if engine._mode == _MODE_LIVE else META_CONFIDENCE_FLOOR
                except Exception:
                    conf_floor = META_CONFIDENCE_FLOOR

                if signal in ("BUY", "SELL") and confidence >= conf_floor:
                    qty       = risk_profile.get("quantity", 1) or 1
                    atr14_val = ctx.get("atr14", live_price * 0.015)

                    if signal == "BUY":
                        sl_price = live_price - atr14_val * 2.0
                        tp_price = live_price + atr14_val * 3.0
                    else:
                        sl_price = live_price + atr14_val * 2.0
                        tp_price = live_price - atr14_val * 3.0

                    risk_inr = risk_profile.get("final_risk_inr",
                               risk_profile.get("risk_per_trade_pct", 1.0) / 100.0
                               * prefs.allocated_capital)

                    exec_result = engine.place_entry(
                        ticker         = ticker,
                        direction      = signal,
                        quantity       = qty,
                        entry_price    = live_price,
                        sl_price       = max(0.01, sl_price),
                        tp_price       = max(0.01, tp_price),
                        confidence     = confidence,
                        dreamer_signal = dreamer_dec.get("direction", 0.0),
                        risk_inr       = risk_inr,
                        strategy_meta  = meta,
                        risk_profile   = risk_profile,
                    )

                    if exec_result.get("success"):
                        placed_order = exec_result.get("order", {})
                        with _lock:
                            _state["daily_trades"] += 1
                            _state["open_trade"]    = placed_order
                            _state["status"]        = "trading"
                        entries_this_cycle += 1
                        try:
                            from agents.telegram_notifier import notify_trade_opened
                            notify_trade_opened(placed_order)
                        except Exception:
                            pass
                        logger.info(
                            "[TradingLoop][%s] ORDER PLACED | %s %s × %d @ ₹%.2f | "
                            "SL=₹%.2f TP=₹%.2f | conf=%.0f%%",
                            cycle_id, signal, ticker, qty,
                            live_price, sl_price, tp_price, confidence
                        )
                    else:
                        logger.info("[TradingLoop][%s] %s entry skipped: %s",
                                    cycle_id, ticker, exec_result.get("error", "?"))
                else:
                    logger.info("[TradingLoop][%s] %s HOLD | signal=%s conf=%.0f%% (floor=%.0f%%)",
                                cycle_id, ticker, signal, confidence, conf_floor)

            # If no new entries and no current trades → scanning status
            final_open = engine.get_open_positions()
            if final_open:
                _upd(status="trading", open_trade=final_open[0])
            elif entries_this_cycle == 0:
                _upd(status="scanning")

            # ── Step 9: Update loop state ─────────────────────────────────────
            with _lock:
                dpnl   = _state.get("daily_pnl", 0.0)
                target = _state.get("daily_profit_target") or prefs.daily_profit_target
                _state["daily_target_pct"] = (dpnl / target * 100) if target > 0 else 0.0

            elapsed_ms = (time.perf_counter() - t_start) * 1000
            self._update_state("last_cycle_status",
                               f"ok:{last_signal}:{last_conf:.0f}% ({len(watchlist)} tickers, {len(final_open)} open)")
            self._update_state("last_cycle_time",
                               datetime.now(timezone.utc).isoformat())
            self._update_state("next_cycle_time",
                               (datetime.now(timezone.utc)
                                + timedelta(minutes=self._interval_min)).isoformat())
            self._update_state("cycle_count", self._cycle_count)

            logger.info("[TradingLoop][%s] Cycle done in %.0fms | tickers=%d open=%d new=%d",
                        cycle_id, elapsed_ms, len(watchlist), len(final_open), entries_this_cycle)

        except Exception as exc:
            logger.exception("[TradingLoop][%s] Cycle error: %s", cycle_id, exc)
            self._update_state("last_error", str(exc))
            self._update_state("last_cycle_status", f"error:{exc!s:.80}")
            try:
                from agents.dreamer_robo_orchestrator import _upd
                _upd(error=str(exc))
            except Exception:
                pass

    # ── Market hours ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_market_open(now_ist: datetime) -> bool:
        """Returns True if NSE is currently trading (Mon–Fri, 09:15–15:30 IST)."""
        if now_ist.weekday() >= 5:   # Saturday=5, Sunday=6
            return False
        t = (now_ist.hour, now_ist.minute)
        open_t  = (NSE_OPEN_H,  NSE_OPEN_M)
        close_t = (NSE_CLOSE_H, NSE_CLOSE_M)
        return open_t <= t < close_t

    # ── Price / context fetch ─────────────────────────────────────────────────

    @staticmethod
    def _fetch_price(ticker: str) -> Optional[float]:
        """Fast live price: tries Groww first, falls back to yfinance."""
        # Try Groww (requires API keys)
        try:
            parent_dir = str(Path(__file__).parent.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            import groww_service as gs
            sym = ticker.replace(".NS", "").replace(".BO", "").upper()
            ltp_map = gs.get_ltp([f"NSE_{sym}"])
            price = ltp_map.get(f"NSE_{sym}", {})
            if isinstance(price, dict):
                price = price.get("ltp") or price.get("close") or 0.0
            return float(price) if price else None
        except Exception:
            pass

        # Fallback: yfinance 1-min candle
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="1d", interval="1m")
            return float(hist["Close"].iloc[-1]) if not hist.empty else None
        except Exception:
            return None

    @staticmethod
    def _fetch_market_context(ticker: str) -> Dict:
        """Full market context (price, ATR, regime, RSI). Delegates to orchestrator helper."""
        try:
            from agents.dreamer_robo_orchestrator import _fetch_market_context
            return _fetch_market_context(ticker)
        except Exception as exc:
            logger.warning("[TradingLoop] Market context failed: %s", exc)
            return {"price": 0.0, "atr_pct": 0.015, "atr14": 0.0,
                    "regime": "UNKNOWN", "rsi14": 50.0, "vol_ratio": 1.0}

    # ── DreamerV3 decision ────────────────────────────────────────────────────

    @staticmethod
    def _get_dreamer_decision_safe(prefs, risk) -> Dict:
        """Get DreamerV3 decision — returns HOLD if not available."""
        try:
            from agents.dreamer_robo_orchestrator import _get_dreamer_decision
            return _get_dreamer_decision(prefs.ticker, prefs, risk)
        except Exception as exc:
            logger.debug("[TradingLoop] DreamerV3 unavailable: %s", exc)
            return {
                "signal": "HOLD", "confidence": 0, "direction": 0,
                "dreamer_active": False, "wm_loss": 0.0, "strategy_weights": {},
            }

    # ── Meta decision ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_meta_decision(
        dreamer_dec:  Dict,
        market_ctx:   Dict,
        risk_profile: Dict,
    ) -> Dict:
        """
        Combine DreamerV3 signal + technical indicators into a final trade decision.

        Technical composite:
          +20  if UPTREND   + RSI < 60
          -20  if DOWNTREND + RSI > 40
          +10  if vol_ratio > 1.5 (volume confirmation)
          -15  if RSI > 75 (overbought) or RSI < 25 (oversold)
          -10  if EXTREME volatility (ATR > 5.5%)

        DreamerV3 weight: 0.6 (when active), 0.0 (when idle)
        Technical weight: 0.4 (always)
        If DreamerV3 not active: technical carries 100% weight.
        """
        dreamer_active  = dreamer_dec.get("dreamer_active", False)
        dreamer_signal  = dreamer_dec.get("signal", "HOLD")
        dreamer_conf    = dreamer_dec.get("confidence", 0)

        regime    = market_ctx.get("regime", "UNKNOWN")
        rsi14     = float(market_ctx.get("rsi14",    50.0))
        vol_ratio = float(market_ctx.get("vol_ratio", 1.0))
        atr_pct   = float(market_ctx.get("atr_pct",  0.015))

        # ── Technical score (-50 to +50) ──────────────────────────────────────
        tech_score = 0.0

        if regime == "UPTREND":
            tech_score += 20
        elif regime == "DOWNTREND":
            tech_score -= 20

        if vol_ratio >= 1.5:
            tech_score += 10 if tech_score >= 0 else -10   # volume confirms direction
        elif vol_ratio >= 1.0 and tech_score != 0:
            tech_score += 5 if tech_score > 0 else -5      # mild volume support

        # RSI scoring — extreme + mid-zones
        if rsi14 > 75:
            tech_score -= 15   # overbought
        elif rsi14 < 25:
            tech_score += 15   # oversold (contrarian + momentum)
        elif rsi14 >= 60 and rsi14 <= 70:
            tech_score -= 8    # mild overbought
        elif rsi14 >= 30 and rsi14 <= 40:
            tech_score += 8    # mild oversold

        if atr_pct > EXTREME_VOL_PCT:
            tech_score = tech_score * 0.5   # halve confidence in extreme vol

        # Convert to direction + confidence
        tech_signal = "BUY" if tech_score > 10 else ("SELL" if tech_score < -10 else "HOLD")
        tech_conf   = float(np.clip(abs(tech_score) / 50.0 * 100.0, 0, 100))

        # ── Combine ────────────────────────────────────────────────────────────
        if dreamer_active and dreamer_signal != "HOLD" and dreamer_conf > 0:
            # Weighted blend
            d_score = (1 if dreamer_signal == "BUY" else -1) * dreamer_conf
            t_score = (1 if tech_signal    == "BUY" else (-1 if tech_signal == "SELL" else 0)) * tech_conf

            combined_score = d_score * W_DREAMER + t_score * W_TECHNICAL

            # Signals must AGREE for high confidence
            if dreamer_signal != "HOLD" and tech_signal != "HOLD" and dreamer_signal != tech_signal:
                combined_score *= 0.5   # disagreement → reduce confidence

            final_signal = "BUY" if combined_score > 5 else ("SELL" if combined_score < -5 else "HOLD")
            final_conf   = float(np.clip(abs(combined_score), 0, 100))
            source       = "dreamer+technical"
        else:
            # Only technical → boost confidence since tech now carries 100% weight (not 40%)
            # Scale factor 1.5× lets the same tech_score reach actionable thresholds.
            final_signal = tech_signal
            final_conf   = float(np.clip(tech_conf * 1.5, 0, 100))
            source       = "technical_only"

        # Risk budget multiplier
        budget_mult = risk_profile.get("risk_budget_multiplier", 1.0)
        final_conf  = final_conf * budget_mult

        logger.debug(
            "[MetaDecision] dreamer=%s(%.0f) tech=%s(%.0f) → final=%s(%.0f) source=%s",
            dreamer_signal, dreamer_conf, tech_signal, tech_conf,
            final_signal, final_conf, source,
        )

        return {
            "signal":        final_signal,
            "confidence":    round(final_conf, 1),
            "dreamer_signal": dreamer_signal,
            "dreamer_conf":   dreamer_conf,
            "tech_signal":    tech_signal,
            "tech_conf":      round(tech_conf, 1),
            "regime":         regime,
            "rsi14":          rsi14,
            "vol_ratio":      round(vol_ratio, 3),
            "atr_pct":        round(atr_pct * 100, 3),
            "source":         source,
            "budget_mult":    round(budget_mult, 3),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _update_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._loop_state[key] = value


# ════════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL SINGLETON
# ════════════════════════════════════════════════════════════════════════════════

loop = TradingLoop()

__all__ = ["TradingLoop", "loop"]
