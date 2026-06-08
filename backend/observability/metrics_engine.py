"""
Observability & Anomaly Detection Engine.

Features:
  1. Trade metrics tracking (win rate, P&L, Sharpe, drawdown, etc.)
  2. Z-score anomaly detection on key metrics
  3. IQR-based outlier detection
  4. Alert generation with severity levels (INFO / WARNING / CRITICAL)
  5. Prometheus-compatible /metrics text endpoint
  6. Circuit breaker state machine
  7. Kill switch (emergency stop)
  8. Human-in-loop approval queue for large positions
"""

import logging
import time
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ─── Global state ─────────────────────────────────────────────────────────────

_lock          = threading.Lock()
_kill_switch   = False           # hard stop all trading
_circuit_state = "NORMAL"        # NORMAL / WARNING / TRIPPED
_approval_queue: List[dict] = [] # pending human approvals

# Trade metrics
_metrics = {
    "total_trades":       0,
    "winning_trades":     0,
    "losing_trades":      0,
    "gross_pnl":          0.0,
    "max_drawdown":       0.0,
    "current_drawdown":   0.0,
    "peak_equity":        1.0,
    "current_equity":     1.0,
    "win_rate":           0.0,
    "avg_win":            0.0,
    "avg_loss":           0.0,
    "profit_factor":      0.0,
    "sharpe_rolling":     0.0,
    "consecutive_losses": 0,
    "last_trade_time":    None,
}

_pnl_history:   deque = deque(maxlen=500)   # per-trade P&L
_equity_curve:  deque = deque(maxlen=500)   # equity snapshots
_anomaly_alerts: deque = deque(maxlen=100)  # recent alerts

# ─── Circuit Breakers ─────────────────────────────────────────────────────────

_CIRCUIT_RULES = {
    "daily_drawdown":      {"threshold": -0.05, "severity": "CRITICAL"},  # 5% daily DD
    "consecutive_losses":  {"threshold": 5,     "severity": "WARNING"},
    "win_rate_floor":      {"threshold": 0.25,  "severity": "WARNING"},   # <25% over 20 trades
    "gross_loss":          {"threshold": -0.10, "severity": "CRITICAL"},  # 10% total loss
    "volatility_spike":    {"threshold": 3.0,   "severity": "WARNING"},   # Z>3 on P&L vol
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _z_score(series: np.ndarray, value: float) -> float:
    if len(series) < 5:
        return 0.0
    mu  = series.mean()
    std = series.std()
    return float((value - mu) / (std + 1e-9))


def _iqr_outlier(series: np.ndarray, value: float) -> bool:
    if len(series) < 10:
        return False
    q1, q3 = np.percentile(series, 25), np.percentile(series, 75)
    iqr     = q3 - q1
    return value < q1 - 1.5 * iqr or value > q3 + 1.5 * iqr


def _emit_alert(name: str, severity: str, message: str, value=None):
    alert = {
        "id":        f"{name}_{int(time.time())}",
        "name":      name,
        "severity":  severity,
        "message":   message,
        "value":     value,
        "timestamp": _now_iso(),
        "ack":       False,
    }
    with _lock:
        _anomaly_alerts.appendleft(alert)
    logger.warning("ALERT [%s] %s: %s", severity, name, message)
    return alert


# ─── Main Recording API ───────────────────────────────────────────────────────

def record_trade(
    pnl_pct:    float,
    direction:  str,
    ticker:     str,
    capital_at_risk: float = 0.0,
):
    """Record a completed trade and update all metrics."""
    with _lock:
        _pnl_history.append(pnl_pct)

        _metrics["total_trades"]   += 1
        _metrics["gross_pnl"]      += pnl_pct
        _metrics["last_trade_time"] = _now_iso()

        # Win / Loss
        if pnl_pct > 0:
            _metrics["winning_trades"]     += 1
            _metrics["consecutive_losses"]  = 0
        else:
            _metrics["losing_trades"]      += 1
            _metrics["consecutive_losses"] += 1

        # Equity curve
        eq = _metrics["current_equity"] * (1 + pnl_pct)
        _metrics["current_equity"]  = eq
        _metrics["peak_equity"]     = max(_metrics["peak_equity"], eq)
        dd = (_metrics["peak_equity"] - eq) / (_metrics["peak_equity"] + 1e-9)
        _metrics["current_drawdown"] = dd
        _metrics["max_drawdown"]     = max(_metrics["max_drawdown"], dd)
        _equity_curve.append({"equity": round(eq, 6), "time": _now_iso()})

        # Win rate
        nt = _metrics["total_trades"]
        _metrics["win_rate"] = round(_metrics["winning_trades"] / max(nt, 1), 4)

        # Avg win / loss
        pnl_arr = np.array(list(_pnl_history))
        wins  = pnl_arr[pnl_arr > 0]
        losses = pnl_arr[pnl_arr < 0]
        _metrics["avg_win"]  = round(float(wins.mean())  if len(wins)   > 0 else 0.0, 6)
        _metrics["avg_loss"] = round(float(abs(losses.mean())) if len(losses) > 0 else 0.0, 6)

        # Profit factor
        gross_win  = float(wins.sum())  if len(wins)   > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        _metrics["profit_factor"] = round(gross_win / (gross_loss + 1e-9), 4)

        # Rolling Sharpe (last 20 trades)
        if len(pnl_arr) >= 5:
            recent = pnl_arr[-20:]
            rf_daily = 0.065 / 252
            _metrics["sharpe_rolling"] = round(
                float((recent.mean() - rf_daily) / (recent.std() + 1e-9) * np.sqrt(252)), 4
            )

    # ── Circuit breaker checks ──
    _check_circuit_breakers(pnl_pct)

    # ── Anomaly detection ──
    _detect_anomalies(pnl_pct)


def _check_circuit_breakers(latest_pnl: float):
    with _lock:
        dd  = _metrics["current_drawdown"]
        cls = _metrics["consecutive_losses"]
        wr  = _metrics["win_rate"]
        nt  = _metrics["total_trades"]
        gpnl = _metrics["gross_pnl"]
    global _circuit_state

    # Daily drawdown
    if dd >= abs(_CIRCUIT_RULES["daily_drawdown"]["threshold"]):
        _emit_alert("daily_drawdown", "CRITICAL",
                    f"Drawdown {dd:.1%} exceeded 5% threshold", dd)
        _trip_circuit("Drawdown limit breached")
        return

    # Consecutive losses
    if cls >= _CIRCUIT_RULES["consecutive_losses"]["threshold"]:
        _emit_alert("consecutive_losses", "WARNING",
                    f"{cls} consecutive losses — review strategy", cls)
        if _circuit_state == "NORMAL":
            with _lock:
                _circuit_state = "WARNING"

    # Win rate floor (only after 20 trades)
    if nt >= 20 and wr < _CIRCUIT_RULES["win_rate_floor"]["threshold"]:
        _emit_alert("win_rate_floor", "WARNING",
                    f"Win rate {wr:.1%} below 25% — performance degradation", wr)

    # Total gross loss
    if gpnl <= _CIRCUIT_RULES["gross_loss"]["threshold"]:
        _emit_alert("gross_loss", "CRITICAL",
                    f"Gross loss {gpnl:.1%} exceeded 10% — emergency stop", gpnl)
        _trip_circuit("Gross loss limit breached")


def _trip_circuit(reason: str):
    global _circuit_state
    with _lock:
        _circuit_state = "TRIPPED"
    logger.error("CIRCUIT BREAKER TRIPPED: %s", reason)


def _detect_anomalies(latest_pnl: float):
    pnl_arr = np.array(list(_pnl_history))
    if len(pnl_arr) < 10:
        return

    # Z-score on latest P&L
    z = _z_score(pnl_arr[:-1], latest_pnl)
    if abs(z) > 3.0:
        _emit_alert(
            "pnl_z_score", "WARNING",
            f"P&L Z-score {z:.2f} — statistical anomaly detected", z,
        )

    # IQR outlier
    if _iqr_outlier(pnl_arr[:-1], latest_pnl):
        _emit_alert(
            "pnl_outlier", "WARNING",
            f"P&L {latest_pnl:.4f} is an IQR outlier", latest_pnl,
        )

    # Volatility spike (last 5 vs last 20)
    if len(pnl_arr) >= 20:
        recent_vol  = pnl_arr[-5:].std()
        baseline_vol = pnl_arr[-20:-5].std()
        if baseline_vol > 1e-9 and recent_vol / baseline_vol > 3.0:
            _emit_alert(
                "volatility_spike", "WARNING",
                f"P&L volatility spike ×{recent_vol/baseline_vol:.1f} vs baseline",
                round(recent_vol / baseline_vol, 2),
            )


# ─── Kill Switch ──────────────────────────────────────────────────────────────

def activate_kill_switch(reason: str = "Manual"):
    global _kill_switch
    with _lock:
        _kill_switch = True
    _emit_alert("kill_switch", "CRITICAL", f"Kill switch activated: {reason}")
    logger.critical("KILL SWITCH ACTIVATED: %s", reason)


def deactivate_kill_switch():
    global _kill_switch
    with _lock:
        _kill_switch = False
    _emit_alert("kill_switch", "INFO", "Kill switch deactivated — trading resumed")


def reset_circuit():
    global _circuit_state
    with _lock:
        _circuit_state = "NORMAL"
        _metrics["consecutive_losses"] = 0


def is_trading_allowed() -> bool:
    with _lock:
        return not _kill_switch and _circuit_state != "TRIPPED"


# ─── Human-in-Loop Approval ───────────────────────────────────────────────────

def request_approval(
    ticker: str,
    direction: str,
    quantity: float,
    price: float,
    reason: str,
    risk_pct: float,
) -> str:
    """Queue a trade for human approval. Returns approval_id."""
    approval_id = f"appr_{int(time.time())}_{ticker}"
    item = {
        "id":        approval_id,
        "ticker":    ticker,
        "direction": direction,
        "quantity":  quantity,
        "price":     round(price, 2),
        "value":     round(quantity * price, 2),
        "risk_pct":  round(risk_pct * 100, 2),
        "reason":    reason,
        "status":    "PENDING",
        "requested": _now_iso(),
        "resolved":  None,
    }
    with _lock:
        _approval_queue.append(item)
    _emit_alert("approval_required", "INFO",
                f"Human approval required: {direction} {quantity} {ticker} @ ₹{price:.2f}",
                approval_id)
    return approval_id


def resolve_approval(approval_id: str, approved: bool, comment: str = "") -> bool:
    with _lock:
        for item in _approval_queue:
            if item["id"] == approval_id:
                item["status"]   = "APPROVED" if approved else "REJECTED"
                item["resolved"] = _now_iso()
                item["comment"]  = comment
                return True
    return False


def get_pending_approvals() -> List[dict]:
    with _lock:
        return [i for i in _approval_queue if i["status"] == "PENDING"]


# ─── Public getters ───────────────────────────────────────────────────────────

def get_metrics() -> dict:
    with _lock:
        m = dict(_metrics)
    m["kill_switch_active"] = _kill_switch
    m["circuit_state"]      = _circuit_state
    m["trading_allowed"]    = not _kill_switch and _circuit_state != "TRIPPED"
    m["equity_curve"]       = list(_equity_curve)[-50:]  # last 50
    m["pnl_history"]        = list(_pnl_history)[-50:]
    return m


def get_alerts(limit: int = 30) -> List[dict]:
    with _lock:
        return list(_anomaly_alerts)[:limit]


def get_circuit_status() -> dict:
    with _lock:
        return {
            "state":               _circuit_state,
            "kill_switch":         _kill_switch,
            "trading_allowed":     not _kill_switch and _circuit_state != "TRIPPED",
            "consecutive_losses":  _metrics["consecutive_losses"],
            "current_drawdown":    round(_metrics["current_drawdown"], 4),
            "rules":               _CIRCUIT_RULES,
        }


# ─── Prometheus text format ───────────────────────────────────────────────────

def prometheus_metrics() -> str:
    with _lock:
        m = dict(_metrics)

    lines = [
        "# HELP gann_trader_total_trades Total trades executed",
        "# TYPE gann_trader_total_trades counter",
        f"gann_trader_total_trades {m['total_trades']}",
        "# HELP gann_trader_win_rate Current win rate",
        "# TYPE gann_trader_win_rate gauge",
        f"gann_trader_win_rate {m['win_rate']}",
        "# HELP gann_trader_gross_pnl Total gross P&L fraction",
        "# TYPE gann_trader_gross_pnl gauge",
        f"gann_trader_gross_pnl {m['gross_pnl']:.6f}",
        "# HELP gann_trader_max_drawdown Maximum drawdown",
        "# TYPE gann_trader_max_drawdown gauge",
        f"gann_trader_max_drawdown {m['max_drawdown']:.6f}",
        "# HELP gann_trader_sharpe_rolling Rolling Sharpe ratio",
        "# TYPE gann_trader_sharpe_rolling gauge",
        f"gann_trader_sharpe_rolling {m['sharpe_rolling']}",
        "# HELP gann_trader_profit_factor Profit factor",
        "# TYPE gann_trader_profit_factor gauge",
        f"gann_trader_profit_factor {m['profit_factor']}",
        f"gann_trader_kill_switch {1 if _kill_switch else 0}",
        f"gann_trader_circuit_tripped {1 if _circuit_state == 'TRIPPED' else 0}",
    ]
    return "\n".join(lines) + "\n"
