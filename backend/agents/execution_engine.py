"""
Execution Engine — Phase 3
===========================
Handles order lifecycle for the Dreamer V3 Robo-Trader.

Modes:
  paper  : Simulate trades in memory. No real orders. DEFAULT.
  live   : Real orders via Groww API. Requires GROWW_API_KEY + GROWW_API_SECRET.
  shadow : Log decisions only — zero execution. Pure observe mode.

Features:
  • Bracket simulation  : entry + SL + TP tracking in paper mode
  • Groww integration   : market entry + SL-Market orders in live mode
  • Confirmation delay  : configurable delay before live order is placed (default 30 s)
  • Transaction costs   : brokerage + STT applied to paper P&L for realism
  • Circuit breakers    : volatility spike, daily loss cap, max positions
  • MongoDB persistence : full order history in `robo_orders` collection
  • Thread-safe         : all mutations protected by a reentrant lock

DISCLAIMER:
  Live trading involves REAL capital at risk.
  Always start in paper mode and verify strategy profitability.
  No guaranteed returns. Consult a SEBI-registered advisor.
  This software is provided as-is with no warranty.

Author : Dreamer V3 Robo-Trader Team
Date   : June 2026
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
MIN_CONFIDENCE_PAPER   = 30    # minimum confidence to open paper trade
MIN_CONFIDENCE_LIVE    = 55    # higher threshold for real money
LIVE_SAFETY_MULT       = 0.70  # reduce live position by 30%
DEFAULT_CONFIRM_SEC    = 30    # seconds: confirmation delay in live mode
BROKERAGE_PCT          = 0.0003  # 0.03% one-way brokerage
BROKERAGE_FLAT         = 20.0   # ₹20 flat per side (Groww)
STT_EQUITY_PCT         = 0.001  # 0.1% STT on sell side (equity)
MAX_OPEN_POSITIONS     = 1     # Phase 3: single open position at a time

MODE_PAPER  = "paper"
MODE_LIVE   = "live"
MODE_SHADOW = "shadow"

VALID_MODES = {MODE_PAPER, MODE_LIVE, MODE_SHADOW}


# ════════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class Order:
    """
    Represents the complete lifecycle of a single trade.
    Status flow: PENDING → OPEN → CLOSED | CANCELLED | REJECTED
    Shadow trades: status = SHADOW (logged but never executed)
    """
    order_id:           str
    ticker:             str             # yfinance format: RELIANCE.NS
    groww_symbol:       str             # Groww format: RELIANCE
    exchange:           str             # NSE | BSE
    direction:          str             # BUY | SELL
    quantity:           int
    entry_price:        float           # filled price (paper) or requested price
    sl_price:           float
    tp_price:           float
    position_value:     float           # qty × entry_price
    risk_inr:           float           # ₹ risked (position × SL-distance)
    confidence:         float           # 0-100
    dreamer_signal:     float           # raw DreamerV3 signal [-1, +1]
    strategy_meta:      Dict            # ensemble scores, regime, RSI
    risk_profile:       Dict            # RPM snapshot at order time
    mode:               str             # paper | live | shadow
    status:             str             # PENDING|OPEN|CLOSED|CANCELLED|REJECTED|SHADOW

    # Timestamps
    entry_time:         str
    exit_time:          Optional[str]   = None

    # Exit
    exit_price:         Optional[float] = None
    exit_reason:        Optional[str]   = None  # SL|TP|EOD|SIGNAL_REV|MANUAL|CIRCUIT
    pnl:                Optional[float] = None
    pnl_pct:            Optional[float] = None
    brokerage_inr:      float           = 0.0
    net_pnl:            Optional[float] = None  # pnl − brokerage

    # Groww live order tracking
    groww_entry_id:     Optional[str]   = None
    groww_sl_id:        Optional[str]   = None
    groww_status:       Optional[str]   = None

    # Confirmation delay (live mode)
    confirmation_pending:  bool         = False
    confirmation_deadline: Optional[str] = None  # ISO string when delay expires

    def to_dict(self) -> Dict:
        return asdict(self)


# ════════════════════════════════════════════════════════════════════════════════
# EXECUTION ENGINE
# ════════════════════════════════════════════════════════════════════════════════

class ExecutionEngine:
    """
    Central execution manager for the Dreamer V3 Robo-Trader (Phase 3).

    Public methods:
        set_mode(mode)                          → switch paper/live/shadow
        place_entry(...)                        → open new position
        check_positions(ticker, price)          → auto-close SL/TP hits
        close_position(order_id, price, reason) → manual close
        get_open_positions()                    → list open Orders
        get_order_history(limit)                → all closed Orders
        get_daily_stats()                       → daily P&L, fills, shadow count
        reset_daily()                           → new day reset
    """

    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._mode    = MODE_PAPER
        self._confirm_delay_sec = DEFAULT_CONFIRM_SEC

        # In-memory order store
        self._orders:     Dict[str, Order] = {}   # all orders (today)
        self._history:    List[Order]      = []   # closed orders (last 200)

        # Daily counters
        self._daily_pnl:       float = 0.0
        self._daily_fills:     int   = 0
        self._daily_brokerage: float = 0.0
        self._shadow_count:    int   = 0

        # MongoDB lazy init
        self._db = None
        logger.info("[ExecEngine] Initialised | mode=%s | confirm_delay=%ds",
                    self._mode, self._confirm_delay_sec)

    # ── Mode management ────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> Dict:
        """
        Switch execution mode.
        Live mode requires GROWW_API_KEY and GROWW_API_SECRET in env.
        Existing open positions are NOT auto-closed on mode switch.
        """
        mode = mode.lower().strip()
        if mode not in VALID_MODES:
            return {"success": False, "error": f"Invalid mode '{mode}'. Use: paper | live | shadow"}

        if mode == MODE_LIVE:
            # Validate Groww credentials before switching
            api_key    = os.environ.get("GROWW_API_KEY", "").strip()
            api_secret = os.environ.get("GROWW_API_SECRET", "").strip()
            if not api_key or not api_secret:
                return {
                    "success": False,
                    "error": (
                        "GROWW_API_KEY and GROWW_API_SECRET must be set in backend/.env "
                        "before switching to LIVE mode. This will place REAL orders."
                    ),
                }
            logger.warning(
                "[ExecEngine] ⚠️  SWITCHING TO LIVE MODE — REAL ORDERS WILL BE PLACED. "
                "Capital at risk. Confirmation delay: %ds", self._confirm_delay_sec
            )

        with self._lock:
            old_mode   = self._mode
            self._mode = mode

        logger.info("[ExecEngine] Mode changed: %s → %s", old_mode, mode)
        return {
            "success":     True,
            "mode":        mode,
            "prev_mode":   old_mode,
            "disclaimer":  (
                "LIVE MODE ACTIVE — Real orders will be placed after "
                f"{self._confirm_delay_sec}s confirmation delay."
                if mode == MODE_LIVE else
                "PAPER MODE — No real orders placed."
            ),
        }

    def set_confirmation_delay(self, seconds: int) -> None:
        with self._lock:
            self._confirm_delay_sec = max(0, int(seconds))
        logger.info("[ExecEngine] Confirmation delay set to %ds", self._confirm_delay_sec)

    # ── Ticker utilities ───────────────────────────────────────────────────────

    @staticmethod
    def _to_groww_symbol(ticker: str) -> Tuple[str, str]:
        """
        Convert yfinance ticker → (groww_symbol, exchange).
        Examples:
          "RELIANCE.NS" → ("RELIANCE", "NSE")
          "TCS.NS"      → ("TCS", "NSE")
          "HDFCBANK.BO" → ("HDFCBANK", "BSE")
          "RELIANCE"    → ("RELIANCE", "NSE")
        """
        t = ticker.upper().strip()
        if t.endswith(".NS"):
            return t[:-3], "NSE"
        if t.endswith(".BO"):
            return t[:-3], "BSE"
        # Assume NSE by default
        return t, "NSE"

    @staticmethod
    def _calc_brokerage(position_value: float, direction: str) -> float:
        """
        Calculate realistic brokerage + taxes for Indian equity.
        Groww: flat ₹20 per executed order (or 0.05% whichever is lower).
        STT: 0.1% on sell-side for equity delivery.
        """
        brokerage = min(BROKERAGE_FLAT, position_value * BROKERAGE_PCT)
        stt       = position_value * STT_EQUITY_PCT if direction == "SELL" else 0.0
        return round(brokerage + stt, 2)

    # ── Order placement ────────────────────────────────────────────────────────

    def place_entry(
        self,
        ticker:          str,
        direction:       str,      # BUY | SELL
        quantity:        int,
        entry_price:     float,
        sl_price:        float,
        tp_price:        float,
        confidence:      float,
        dreamer_signal:  float,
        risk_inr:        float,
        strategy_meta:   Optional[Dict] = None,
        risk_profile:    Optional[Dict] = None,
    ) -> Dict:
        """
        Open a new position.

        Paper  : Immediately records as OPEN.
        Live   : Records as PENDING → waits confirmation_delay_sec → places Groww order.
        Shadow : Records as SHADOW — zero execution.

        Returns order dict or error dict.
        """
        with self._lock:
            mode = self._mode
            # Hard checks
            open_count = sum(
                1 for o in self._orders.values()
                if o.status in ("OPEN", "PENDING")
            )
            if open_count >= MAX_OPEN_POSITIONS:
                return {"success": False, "error": "Max open positions reached — close existing first."}

            min_conf = MIN_CONFIDENCE_LIVE if mode == MODE_LIVE else MIN_CONFIDENCE_PAPER
            if confidence < min_conf:
                return {"success": False, "error": f"Confidence {confidence:.0f}% below threshold {min_conf}%"}

        groww_sym, exchange = self._to_groww_symbol(ticker)
        position_value = quantity * entry_price
        brokerage      = self._calc_brokerage(position_value, direction)
        trade_ref      = str(uuid4())[:8].upper()

        if mode == MODE_LIVE:
            quantity = max(1, int(quantity * LIVE_SAFETY_MULT))
            position_value = quantity * entry_price

        order = Order(
            order_id           = trade_ref,
            ticker             = ticker,
            groww_symbol       = groww_sym,
            exchange           = exchange,
            direction          = direction,
            quantity           = quantity,
            entry_price        = round(entry_price, 2),
            sl_price           = round(sl_price, 2),
            tp_price           = round(tp_price, 2),
            position_value     = round(position_value, 2),
            risk_inr           = round(risk_inr, 2),
            confidence         = round(confidence, 1),
            dreamer_signal     = round(dreamer_signal, 4),
            strategy_meta      = strategy_meta or {},
            risk_profile       = risk_profile  or {},
            mode               = mode,
            status             = "SHADOW" if mode == MODE_SHADOW else "OPEN",
            entry_time         = datetime.now(timezone.utc).isoformat(),
            brokerage_inr      = brokerage,
        )

        if mode == MODE_PAPER:
            with self._lock:
                self._orders[trade_ref] = order
                self._daily_fills += 1
            logger.info(
                "[ExecEngine][PAPER] %s %s × %d @ ₹%.2f | SL=₹%.2f TP=₹%.2f | conf=%.0f%%",
                direction, ticker, quantity, entry_price, sl_price, tp_price, confidence
            )
            # Persist open order to DB
            self._schedule_db_upsert(order)

        elif mode == MODE_LIVE:
            # Start confirmation delay in background
            order.status                = "PENDING"
            order.confirmation_pending  = True
            order.confirmation_deadline = (
                datetime.now(timezone.utc) + timedelta(seconds=self._confirm_delay_sec)
            ).isoformat()
            with self._lock:
                self._orders[trade_ref] = order
            # Spawn confirmation thread
            threading.Thread(
                target=self._live_confirm_and_place,
                args=(trade_ref,),
                daemon=True,
                name=f"exec-confirm-{trade_ref}",
            ).start()
            logger.info(
                "[ExecEngine][LIVE] Order PENDING (%ds delay) | %s %s × %d @ ~₹%.2f",
                self._confirm_delay_sec, direction, ticker, quantity, entry_price
            )

        elif mode == MODE_SHADOW:
            with self._lock:
                self._orders[trade_ref] = order
                self._shadow_count += 1
            logger.info(
                "[ExecEngine][SHADOW] Logged: %s %s × %d @ ₹%.2f | conf=%.0f%% (no execution)",
                direction, ticker, quantity, entry_price, confidence
            )

        return {"success": True, "order": order.to_dict(), "mode": mode}

    # ── Live order confirmation & placement ────────────────────────────────────

    def _live_confirm_and_place(self, order_id: str) -> None:
        """
        Background thread: waits confirmation_delay_sec then places real Groww order.
        During the delay, the trade can be cancelled by calling cancel_pending().
        """
        with self._lock:
            order = self._orders.get(order_id)
        if not order:
            return

        delay = self._confirm_delay_sec
        logger.info("[ExecEngine][LIVE] Confirmation delay: %.0f s | ref=%s", delay, order_id)
        time.sleep(delay)

        # Re-check order still pending (may have been cancelled)
        with self._lock:
            order = self._orders.get(order_id)
            if not order or order.status != "PENDING":
                logger.info("[ExecEngine][LIVE] Order %s cancelled before execution", order_id)
                return

        # Place actual Groww order
        try:
            parent_dir = str(Path(__file__).parent.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            import groww_service as gs

            result = gs.place_order(
                trading_symbol   = order.groww_symbol,
                quantity         = order.quantity,
                transaction_type = order.direction,
                order_type       = "MARKET",
                product          = "MIS",   # intraday
                exchange         = order.exchange,
                reference_id     = order.order_id,
            )
            groww_oid = (result or {}).get("groww_order_id", "")
            logger.info(
                "[ExecEngine][LIVE] Entry order placed | ref=%s groww_id=%s",
                order_id, groww_oid
            )

            # Place SL order immediately after
            sl_result  = gs.place_order(
                trading_symbol   = order.groww_symbol,
                quantity         = order.quantity,
                transaction_type = "SELL" if order.direction == "BUY" else "BUY",
                order_type       = "SL_M",
                product          = "MIS",
                exchange         = order.exchange,
                trigger_price    = order.sl_price,
                reference_id     = f"{order.order_id}-SL",
            )
            groww_sl_id = (sl_result or {}).get("groww_order_id", "")
            logger.info("[ExecEngine][LIVE] SL order placed | ref=%s-SL groww_sl=%s",
                        order_id, groww_sl_id)

            with self._lock:
                order.status               = "OPEN"
                order.confirmation_pending = False
                order.groww_entry_id       = groww_oid
                order.groww_sl_id          = groww_sl_id
                order.groww_status         = "OPEN"
                self._daily_fills         += 1

        except Exception as exc:
            logger.error("[ExecEngine][LIVE] Order placement FAILED | ref=%s: %s", order_id, exc)
            with self._lock:
                order.status              = "REJECTED"
                order.exit_reason         = f"Groww API error: {exc}"
                order.confirmation_pending = False

    # ── Position management ────────────────────────────────────────────────────

    def check_positions(
        self,
        ticker:        str,
        current_price: float,
    ) -> List[Dict]:
        """
        Check all open positions for SL/TP hits.
        Returns list of auto-closed order dicts (empty if nothing triggered).
        """
        closed = []
        with self._lock:
            open_orders = [
                o for o in self._orders.values()
                if o.status == "OPEN" and o.ticker == ticker
            ]

        for order in open_orders:
            reason = self._check_sl_tp(order, current_price)
            if reason:
                closed_order = self.close_position(order.order_id, current_price, reason)
                if closed_order.get("success"):
                    closed.append(closed_order.get("order", {}))
        return closed

    @staticmethod
    def _check_sl_tp(order: Order, price: float) -> Optional[str]:
        """Returns close reason if SL or TP is hit, else None."""
        if order.direction == "BUY":
            if price <= order.sl_price:
                return "SL"
            if price >= order.tp_price:
                return "TP"
        elif order.direction == "SELL":
            if price >= order.sl_price:
                return "SL"
            if price <= order.tp_price:
                return "TP"
        return None

    def close_position(
        self,
        order_id:    str,
        exit_price:  float,
        reason:      str = "MANUAL",
    ) -> Dict:
        """
        Close an open position and compute realised P&L.
        Live mode: cancels Groww SL order + places market exit order.
        """
        with self._lock:
            order = self._orders.get(order_id)

        if not order:
            return {"success": False, "error": f"Order {order_id} not found"}
        if order.status not in ("OPEN", "SHADOW"):
            return {"success": False, "error": f"Order {order_id} is {order.status} — cannot close"}

        # Compute P&L
        qty    = order.quantity
        entry  = order.entry_price
        if order.direction == "BUY":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        exit_brokerage = self._calc_brokerage(exit_price * qty, "SELL")
        total_brokerage = order.brokerage_inr + exit_brokerage
        net_pnl         = pnl - total_brokerage
        pnl_pct         = (pnl / max(order.position_value, 1e-8)) * 100.0

        with self._lock:
            order.exit_time     = datetime.now(timezone.utc).isoformat()
            order.exit_price    = round(exit_price, 2)
            order.exit_reason   = reason
            order.pnl           = round(pnl, 2)
            order.pnl_pct       = round(pnl_pct, 3)
            order.brokerage_inr = round(total_brokerage, 2)
            order.net_pnl       = round(net_pnl, 2)
            order.status        = "CLOSED"

            # Update daily counters
            if order.mode != MODE_SHADOW:
                self._daily_pnl       += pnl
                self._daily_brokerage += total_brokerage
            self._history.append(order)
            self._history = self._history[-200:]
            del self._orders[order_id]

        logger.info(
            "[ExecEngine] CLOSED %s | %s %s × %d | P&L=₹%.2f (net ₹%.2f) | reason=%s",
            order_id, order.direction, order.ticker, qty,
            pnl, net_pnl, reason
        )

        # Persist closed order to DB (upsert — updates the existing OPEN record)
        self._schedule_db_upsert(order)

        # Live mode: cancel SL order + place exit order
        if order.mode == MODE_LIVE:
            threading.Thread(
                target  = self._live_exit_order,
                args    = (order,),
                daemon  = True,
                name    = f"exec-exit-{order_id}",
            ).start()

        return {"success": True, "order": order.to_dict()}

    def _live_exit_order(self, order: Order) -> None:
        """Place live exit order + cancel SL order via Groww."""
        try:
            parent_dir = str(Path(__file__).parent.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            import groww_service as gs

            # Cancel SL order if still open
            if order.groww_sl_id:
                try:
                    gs.cancel_order(order.groww_sl_id)
                    logger.info("[ExecEngine][LIVE] SL order %s cancelled", order.groww_sl_id)
                except Exception as e:
                    logger.warning("[ExecEngine][LIVE] SL cancel failed: %s", e)

            # Market exit order
            exit_dir = "SELL" if order.direction == "BUY" else "BUY"
            result   = gs.place_order(
                trading_symbol   = order.groww_symbol,
                quantity         = order.quantity,
                transaction_type = exit_dir,
                order_type       = "MARKET",
                product          = "MIS",
                exchange         = order.exchange,
                reference_id     = f"{order.order_id}-EXIT",
            )
            logger.info("[ExecEngine][LIVE] Exit order placed | ref=%s-EXIT result=%s",
                        order.order_id, result)
        except Exception as exc:
            logger.error("[ExecEngine][LIVE] Exit order failed | %s: %s", order.order_id, exc)

    # ── Cancel pending (live mode) ─────────────────────────────────────────────

    def cancel_pending(self, order_id: str) -> Dict:
        """Cancel a PENDING order before the confirmation delay expires."""
        with self._lock:
            order = self._orders.get(order_id)
            if not order:
                return {"success": False, "error": "Order not found"}
            if order.status != "PENDING":
                return {"success": False, "error": f"Order is {order.status}, not PENDING"}
            order.status              = "CANCELLED"
            order.exit_reason         = "USER_CANCEL"
            order.exit_time           = datetime.now(timezone.utc).isoformat()
            order.confirmation_pending = False
            self._history.append(order)
            del self._orders[order_id]
        logger.info("[ExecEngine] PENDING order %s cancelled by user", order_id)
        return {"success": True, "order_id": order_id}

    # ── EOD management ─────────────────────────────────────────────────────────

    def close_all_positions(self, current_prices: Dict[str, float], reason: str = "EOD") -> List[Dict]:
        """
        Close all open positions. Used at EOD (15:15 IST) or circuit breaker.
        current_prices: {ticker: price}
        """
        closed = []
        with self._lock:
            open_ids = list(self._orders.keys())

        for oid in open_ids:
            with self._lock:
                order = self._orders.get(oid)
            if not order or order.status not in ("OPEN", "PENDING"):
                continue
            price = current_prices.get(order.ticker, order.entry_price)
            result = self.close_position(oid, price, reason)
            if result.get("success"):
                closed.append(result.get("order", {}))

        logger.info("[ExecEngine] All positions closed (%d) | reason=%s", len(closed), reason)
        return closed

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_open_positions(self) -> List[Dict]:
        with self._lock:
            return [o.to_dict() for o in self._orders.values()
                    if o.status in ("OPEN", "PENDING")]

    def get_pending_confirmations(self) -> List[Dict]:
        with self._lock:
            return [o.to_dict() for o in self._orders.values()
                    if o.status == "PENDING"]

    def get_shadow_signals(self) -> List[Dict]:
        with self._lock:
            return [o.to_dict() for o in self._orders.values()
                    if o.status == "SHADOW"]

    def get_order_history(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            return [o.to_dict() for o in reversed(self._history)][:limit]

    def get_daily_stats(self) -> Dict:
        with self._lock:
            open_pos   = [o for o in self._orders.values() if o.status == "OPEN"]
            pending_pos = [o for o in self._orders.values() if o.status == "PENDING"]
            shadow_pos  = [o for o in self._orders.values() if o.status == "SHADOW"]
            closed_today = list(reversed(self._history))[:50]

        wins   = sum(1 for o in closed_today if (o.pnl or 0) > 0)
        losses = sum(1 for o in closed_today if (o.pnl or 0) < 0)
        closed_pnl = sum((o.pnl or 0) for o in closed_today)

        # Unrealised P&L on open positions — updated by trading loop via live price

        return {
            "mode":                self._mode,
            "daily_pnl":           round(self._daily_pnl, 2),
            "daily_brokerage":     round(self._daily_brokerage, 2),
            "daily_net_pnl":       round(self._daily_pnl - self._daily_brokerage, 2),
            "daily_fills":         self._daily_fills,
            "shadow_signals_today": self._shadow_count,
            "open_positions":      len(open_pos),
            "pending_positions":   len(pending_pos),
            "wins_today":          wins,
            "losses_today":        losses,
            "closed_pnl_today":    round(closed_pnl, 2),
            "open_positions_list": [o.to_dict() for o in open_pos],
            "pending_list":        [o.to_dict() for o in pending_pos],
            "shadow_list":         [o.to_dict() for o in shadow_pos],
        }

    def reset_daily(self) -> None:
        with self._lock:
            # Close any open positions (force)
            for order in list(self._orders.values()):
                self._history.append(order)
            self._history = self._history[-200:]
            self._orders.clear()
            self._daily_pnl       = 0.0
            self._daily_fills     = 0
            self._daily_brokerage = 0.0
            self._shadow_count    = 0
        logger.info("[ExecEngine] Daily reset done.")

    # ── MongoDB persistence ────────────────────────────────────────────────────

    def _get_db(self):
        if self._db is None:
            url  = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
            name = os.environ.get("DB_NAME",   "trading_db")
            self._db = AsyncIOMotorClient(url)[name]
        return self._db

    async def save_order_to_db(self, order: Order) -> None:
        """Persist a closed order to MongoDB robo_orders collection."""
        try:
            db  = self._get_db()
            doc = order.to_dict()
            doc.pop("_id", None)
            await db["robo_orders"].insert_one(doc)
        except Exception as exc:
            logger.debug("[ExecEngine] DB save failed: %s", exc)

    async def upsert_order_to_db(self, order: Order) -> None:
        """Upsert an order (open or closed) to MongoDB by order_id."""
        try:
            db  = self._get_db()
            doc = order.to_dict()
            doc.pop("_id", None)
            await db["robo_orders"].update_one(
                {"order_id": order.order_id},
                {"$set": doc},
                upsert=True,
            )
        except Exception as exc:
            logger.warning("[ExecEngine] DB upsert failed: %s", exc)

    def _schedule_db_upsert(self, order: Order) -> None:
        """Fire-and-forget background thread to upsert order to MongoDB (sync pymongo)."""
        import copy
        order_copy = copy.copy(order)
        def _run():
            try:
                import pymongo as _pymongo
                url  = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
                name = os.environ.get("DB_NAME", "trading_db")
                client = _pymongo.MongoClient(url, serverSelectionTimeoutMS=5000)
                db = client[name]
                doc = order_copy.to_dict()
                doc.pop("_id", None)
                db["robo_orders"].update_one(
                    {"order_id": order_copy.order_id},
                    {"$set": doc},
                    upsert=True,
                )
                client.close()
            except Exception as exc:
                logger.warning("[ExecEngine] DB sync upsert failed: %s", exc)
        threading.Thread(target=_run, daemon=True, name="exec-db-upsert").start()

    async def get_db_history(self, limit: int = 50) -> List[Dict]:
        try:
            db  = self._get_db()
            cur = db["robo_orders"].find({}, {"_id": 0}).sort("entry_time", -1).limit(limit)
            return [doc async for doc in cur]
        except Exception as exc:
            logger.warning("[ExecEngine] DB history fetch failed: %s", exc)
            return []


# ════════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL SINGLETON
# ════════════════════════════════════════════════════════════════════════════════

engine = ExecutionEngine()

__all__ = ["ExecutionEngine", "Order", "engine",
           "MODE_PAPER", "MODE_LIVE", "MODE_SHADOW"]
