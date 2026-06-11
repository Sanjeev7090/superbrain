/**
 * TradeExplainability — Phase 4
 * Trade log with expandable "Why this trade?" reasoning.
 * Shows: DreamerV3 signal, technical confluence, risk profile snapshot.
 */
import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

const fmt    = (v, d = 0) => v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtInr = v => `₹${fmt(v, 0)}`;
const fmtPct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`;
const fmtTime = iso => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  catch { return '—'; }
};

function ExplainRow({ order, index }) {
  const [expanded, setExpanded] = useState(false);
  const pnl    = order.pnl    ?? 0;
  const netPnl = order.net_pnl ?? 0;
  const isPos  = pnl >= 0;
  const meta   = order.strategy_meta || {};
  const rp     = order.risk_profile  || {};

  const reasonMap = {
    'SL':            { label: 'Stop Loss Hit',     color: '#ef4444', icon: '🛑' },
    'TP':            { label: 'Take Profit Hit',   color: '#10b981', icon: '✅' },
    'EOD':           { label: 'End of Day Close',  color: '#f59e0b', icon: '🌙' },
    'CIRCUIT_BREAKER': { label: 'Circuit Breaker', color: '#ef4444', icon: '⚡' },
    'MANUAL':        { label: 'Manual Close',      color: '#a1a1aa', icon: '👆' },
    'EMERGENCY_CLOSE': { label: 'Emergency Close', color: '#ef4444', icon: '🆘' },
  };
  const exitInfo = reasonMap[order.exit_reason] || { label: order.exit_reason || '—', color: '#a1a1aa', icon: '•' };

  return (
    <div
      className="border-b border-zinc-800/50 last:border-0"
      data-testid={`trade-row-${order.order_id}`}
    >
      {/* Summary row */}
      <button
        className="w-full flex items-center gap-2 py-2.5 px-3 hover:bg-zinc-800/30 transition-colors text-left"
        onClick={() => setExpanded(x => !x)}
      >
        {/* Expand icon */}
        <div className="text-zinc-600 flex-shrink-0 w-3">
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        </div>

        {/* Direction badge */}
        <span
          className="text-[9px] font-black px-1.5 py-0.5 rounded flex-shrink-0"
          style={{
            background: order.direction === 'BUY' ? '#10b98120' : '#ef444420',
            color:      order.direction === 'BUY' ? '#10b981'   : '#ef4444',
          }}
        >
          {order.direction}
        </span>

        {/* Mode badge */}
        <span className="text-[8px] px-1 py-0.5 rounded bg-zinc-800 text-zinc-500 flex-shrink-0">
          {(order.mode || 'PAPER').toUpperCase()}
        </span>

        {/* Ticker */}
        <span className="text-[11px] font-mono text-zinc-300 font-semibold flex-shrink-0">
          {order.ticker}
        </span>

        {/* Qty × Entry */}
        <span className="text-[10px] text-zinc-500 hidden sm:inline">
          {order.quantity}× @ {fmtInr(order.entry_price)}
        </span>

        {/* Exit reason */}
        <span className="text-[9px] flex-shrink-0" style={{ color: exitInfo.color }}>
          {exitInfo.icon} {exitInfo.label}
        </span>

        {/* Brain alignment badge — inline in summary row */}
        {meta.brain_reason && (
          <span
            className={`text-[8px] font-bold px-1.5 py-0.5 rounded-full flex-shrink-0 hidden md:inline ${
              meta.brain_boost    ? 'bg-emerald-900/30 text-emerald-400'
              : meta.brain_override ? 'bg-red-900/30 text-red-400'
              : meta.brain_disagree ? 'bg-amber-900/30 text-amber-400'
              : 'bg-purple-900/30 text-purple-400'
            }`}
            title={meta.brain_reason}
            data-testid={`brain-reason-badge-${order.order_id}`}
          >
            {meta.brain_boost     ? '🧠 Agreed'
             : meta.brain_override ? '🧠 Override'
             : meta.brain_disagree ? '🧠 Disagree'
             : '🧠'}
          </span>
        )}

        {/* Time */}
        <span className="text-[9px] text-zinc-600 ml-auto flex-shrink-0 hidden sm:block">
          {fmtTime(order.entry_time)}
        </span>

        {/* P&L */}
        <span
          className="text-[11px] font-black flex-shrink-0 min-w-[60px] text-right"
          style={{ color: isPos ? '#10b981' : '#ef4444' }}
        >
          {isPos ? '+' : ''}{fmtInr(pnl)}
        </span>
      </button>

      {/* Expanded explainability section */}
      {expanded && (
        <div className="px-4 pb-3 pt-1 bg-zinc-900/40 text-[10px]">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">

            {/* Why this trade? — DreamerV3 reasoning */}
            <div className="bg-zinc-900 rounded-xl p-3 border border-violet-500/20">
              <p className="text-[9px] font-bold text-violet-400 uppercase tracking-wider mb-2 flex items-center gap-1">
                🧠 DreamerV3 Reasoning
              </p>
              <div className="space-y-1.5">
                <div className="flex justify-between">
                  <span className="text-zinc-500">Raw Signal</span>
                  <span className="font-bold text-violet-300">{fmt(order.dreamer_signal, 3)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Confidence</span>
                  <span className="font-bold text-white">{fmtPct(order.confidence)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Source</span>
                  <span className="font-bold text-indigo-300 capitalize">
                    {meta.source || 'technical_only'}
                  </span>
                </div>
                {meta.dreamer_conf != null && (
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Dreamer Conf</span>
                    <span className="font-bold text-violet-400">{fmtPct(meta.dreamer_conf)}</span>
                  </div>
                )}
                {meta.tech_conf != null && (
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Technical Conf</span>
                    <span className="font-bold text-blue-400">{fmtPct(meta.tech_conf)}</span>
                  </div>
                )}

                {/* Brain Alignment row inside DreamerV3 card */}
                {meta.brain_reason && (
                  <div className="mt-2 pt-2 border-t border-zinc-800">
                    <p className="text-[9px] font-bold text-fuchsia-400 mb-1">HSB Alignment</p>
                    <p
                      className={`text-[9px] leading-relaxed px-2 py-1 rounded ${
                        meta.brain_boost    ? 'bg-emerald-900/20 text-emerald-300'
                        : meta.brain_override ? 'bg-red-900/20 text-red-300'
                        : meta.brain_disagree ? 'bg-amber-900/20 text-amber-300'
                        : 'bg-purple-900/20 text-purple-300'
                      }`}
                      data-testid={`brain-reason-detail-${order.order_id}`}
                    >
                      {meta.brain_reason}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Market context at trade time */}
            <div className="bg-zinc-900 rounded-xl p-3 border border-cyan-500/20">
              <p className="text-[9px] font-bold text-cyan-400 uppercase tracking-wider mb-2 flex items-center gap-1">
                📡 Market Context
              </p>
              <div className="space-y-1.5">
                <div className="flex justify-between">
                  <span className="text-zinc-500">Regime</span>
                  <span className="font-bold" style={{
                    color: meta.regime === 'UPTREND' ? '#10b981' : meta.regime === 'DOWNTREND' ? '#ef4444' : '#f59e0b'
                  }}>{meta.regime || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">RSI 14</span>
                  <span className="font-bold text-white">{meta.rsi14 ? fmt(meta.rsi14, 1) : '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Vol Ratio</span>
                  <span className="font-bold text-amber-300">{meta.vol_ratio ? fmt(meta.vol_ratio, 2) : '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">ATR %</span>
                  <span className="font-bold text-zinc-300">{meta.atr_pct ? `${fmt(meta.atr_pct, 2)}%` : '—'}</span>
                </div>
              </div>
            </div>

            {/* Risk profile + outcome */}
            <div className="bg-zinc-900 rounded-xl p-3 border border-emerald-500/20">
              <p className="text-[9px] font-bold text-emerald-400 uppercase tracking-wider mb-2 flex items-center gap-1">
                ⚖️ Risk & Outcome
              </p>
              <div className="space-y-1.5">
                <div className="flex justify-between">
                  <span className="text-zinc-500">Entry</span>
                  <span className="font-bold text-white">{fmtInr(order.entry_price)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Exit</span>
                  <span className="font-bold text-white">{fmtInr(order.exit_price)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">SL / TP</span>
                  <span className="font-bold text-zinc-300">
                    {fmtInr(order.sl_price)} / {fmtInr(order.tp_price)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Risk ₹</span>
                  <span className="font-bold text-amber-400">{fmtInr(order.risk_inr)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Brokerage</span>
                  <span className="font-bold text-zinc-500">{fmtInr(order.brokerage_inr)}</span>
                </div>
                <div className="flex justify-between border-t border-zinc-700 pt-1 mt-1">
                  <span className="text-zinc-400 font-bold">Net P&L</span>
                  <span className="font-black" style={{ color: netPnl >= 0 ? '#10b981' : '#ef4444' }}>
                    {netPnl >= 0 ? '+' : ''}{fmtInr(netPnl)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function TradeExplainability({ orders = [], orderStats = {} }) {
  const wins   = orderStats.wins   || 0;
  const losses = orderStats.losses || 0;
  const total  = wins + losses;
  const wr     = total > 0 ? ((wins / total) * 100).toFixed(1) : '—';

  return (
    <div data-testid="trade-explainability">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-xs font-bold text-zinc-300 uppercase tracking-widest">
            Trade Log &amp; Explainability
          </h2>
          <p className="text-[9px] text-zinc-600 mt-0.5">Click any row to see DreamerV3 reasoning</p>
        </div>
        {total > 0 && (
          <div className="flex items-center gap-3 text-[10px]">
            <span className="text-emerald-400 font-bold">{wins}W</span>
            <span className="text-red-400 font-bold">{losses}L</span>
            <span className="text-zinc-400">{wr}% WR</span>
            <span
              className="font-black text-sm"
              style={{ color: (orderStats.daily_net_pnl||0) >= 0 ? '#10b981' : '#ef4444' }}
            >
              {(orderStats.daily_net_pnl||0) >= 0 ? '+' : ''}₹{Math.abs(orderStats.daily_net_pnl||0).toFixed(0)} net
            </span>
          </div>
        )}
      </div>

      {/* Trade list */}
      <div className="bg-zinc-900/50 rounded-xl border border-zinc-800/50 overflow-hidden">
        {orders.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-zinc-600">
            <div className="text-3xl mb-2">📋</div>
            <p className="text-sm font-medium">No trades yet</p>
            <p className="text-[10px] mt-0.5">Start auto mode to begin trading</p>
          </div>
        ) : (
          <div className="max-h-72 overflow-y-auto">
            {orders.map((order, i) => (
              <ExplainRow key={order.order_id || i} order={order} index={i} />
            ))}
          </div>
        )}
      </div>

      {/* Disclaimer */}
      <p className="text-[9px] text-zinc-700 mt-2">
        ⚠️ Trade decisions are made by an ML model. Not financial advice.
        Past performance ≠ future results. Capital at risk.
      </p>
    </div>
  );
}
