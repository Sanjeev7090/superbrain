/**
 * AgentStatusPanel — Phase 4
 * Visualises the 4-layer Dreamer V3 pipeline as animated agent cards.
 *
 * Agents:
 *  1. Perception Layer   — market data, ATR, regime detection
 *  2. DreamerV3 World Model — RSSM confidence, WM loss, signal
 *  3. Risk Portfolio Manager — Kelly, VaR, budget
 *  4. Execution Engine   — mode, orders, P&L
 */
import React from 'react';

const fmt    = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));
const fmtInr = v => `₹${Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const fmtPct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`;

// Status dot
function StatusDot({ state }) {
  const cfg = {
    active:          { color: '#10b981', pulse: true,  label: 'ACTIVE' },
    trading:         { color: '#10b981', pulse: true,  label: 'TRADING' },
    scanning:        { color: '#f59e0b', pulse: true,  label: 'SCANNING' },
    thinking:        { color: '#a78bfa', pulse: true,  label: 'THINKING' },
    idle:            { color: '#6b7280', pulse: false, label: 'IDLE' },
    paused:          { color: '#6b7280', pulse: false, label: 'PAUSED' },
    circuit_breaker: { color: '#ef4444', pulse: false, label: 'TRIPPED' },
    error:           { color: '#ef4444', pulse: false, label: 'ERROR' },
    done:            { color: '#10b981', pulse: false, label: 'DONE' },
  }[state] || { color: '#6b7280', pulse: false, label: state?.toUpperCase() || 'IDLE' };

  return (
    <div className="flex items-center gap-1.5">
      <div
        className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.pulse ? 'animate-pulse' : ''}`}
        style={{ background: cfg.color, boxShadow: cfg.pulse ? `0 0 6px ${cfg.color}` : 'none' }}
      />
      <span className="text-[9px] font-bold tracking-wider" style={{ color: cfg.color }}>
        {cfg.label}
      </span>
    </div>
  );
}

// Single agent card
function AgentCard({ number, name, subtitle, state, metrics, icon, accentColor = '#8b5cf6' }) {
  const isActive = ['active', 'scanning', 'thinking', 'trading'].includes(state);
  return (
    <div
      className="relative rounded-xl p-3 overflow-hidden transition-all duration-300"
      style={{
        background: isActive
          ? `linear-gradient(135deg, ${accentColor}10, ${accentColor}05)`
          : 'rgba(24,24,27,0.8)',
        border: `1px solid ${isActive ? accentColor + '40' : '#3f3f4640'}`,
        boxShadow: isActive ? `0 0 20px ${accentColor}10` : 'none',
      }}
      data-testid={`agent-card-${number}`}
    >
      {/* Top bar */}
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center text-sm flex-shrink-0"
            style={{ background: accentColor + '20', border: `1px solid ${accentColor}30` }}
          >
            {icon}
          </div>
          <div>
            <div className="flex items-center gap-1">
              <span className="text-[9px] text-zinc-600 font-mono">#{number}</span>
              <p className="text-[11px] font-bold text-white leading-tight">{name}</p>
            </div>
            <p className="text-[9px] text-zinc-500 leading-tight">{subtitle}</p>
          </div>
        </div>
        <StatusDot state={state} />
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 gap-1.5">
        {metrics.map(({ label, value, color }) => (
          <div key={label} className="bg-black/20 rounded-lg px-2 py-1.5">
            <p className="text-[8px] text-zinc-600 uppercase tracking-wide mb-0.5">{label}</p>
            <p className="text-[11px] font-bold truncate" style={{ color: color || '#a1a1aa' }}>
              {value}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AgentStatusPanel({ roboState, loopStatus, execStats }) {
  const rs   = roboState || {};
  const rp   = rs.risk_profile || {};
  const dec  = rs.current_decision || {};
  const meta = dec.meta || {};
  const ls   = loopStatus || {};

  // Derive individual agent states from overall robo state
  const overallStatus = rs.status || 'idle';
  const isRunning     = rs.auto_mode;

  const perceptionState = isRunning
    ? (ls.market_open ? 'scanning' : 'idle')
    : 'idle';

  const dreamerState = (() => {
    if (!isRunning) return 'idle';
    if (dec.dreamer_active === false) return 'idle';
    if (overallStatus === 'trading') return 'done';
    if (overallStatus === 'scanning') return 'thinking';
    return 'idle';
  })();

  const rpmState = (() => {
    if (!isRunning) return 'idle';
    if (rp.should_stop_trading) return 'paused';
    if (overallStatus === 'trading') return 'active';
    if (overallStatus === 'scanning') return 'thinking';
    return 'idle';
  })();

  const execState = (() => {
    if (rs.circuit_breaker) return 'circuit_breaker';
    if (!isRunning) return 'idle';
    if (rs.open_trade) return 'trading';
    if (overallStatus === 'scanning') return 'scanning';
    return 'idle';
  })();

  const agents = [
    {
      number: 1,
      name: 'Perception Layer',
      subtitle: 'Market Intelligence',
      state: perceptionState,
      icon: '📡',
      accentColor: '#06b6d4',
      metrics: [
        { label: 'Regime',    value: dec.regime    || rp.vol_regime || '—',    color: dec.regime === 'UPTREND' ? '#10b981' : dec.regime === 'DOWNTREND' ? '#ef4444' : '#f59e0b' },
        { label: 'RSI 14',    value: dec.rsi14 != null ? dec.rsi14.toFixed(1) : '—',  color: (dec.rsi14||50) > 70 ? '#ef4444' : (dec.rsi14||50) < 30 ? '#10b981' : '#a1a1aa' },
        { label: 'Vol Regime',value: rp.vol_regime  || '—',                    color: rp.vol_regime === 'HIGH' ? '#ef4444' : rp.vol_regime === 'LOW' ? '#10b981' : '#3b82f6' },
        { label: 'Market',    value: ls.market_open ? 'OPEN' : 'CLOSED',       color: ls.market_open ? '#10b981' : '#ef4444' },
      ],
    },
    {
      number: 2,
      name: 'DreamerV3',
      subtitle: 'World Model · RSSM',
      state: dreamerState,
      icon: '🧠',
      accentColor: '#8b5cf6',
      metrics: [
        { label: 'Signal',     value: dec.signal || 'HOLD',    color: dec.signal === 'BUY' ? '#10b981' : dec.signal === 'SELL' ? '#ef4444' : '#a1a1aa' },
        { label: 'Confidence', value: fmtPct(dec.confidence),  color: (dec.confidence||0) >= 60 ? '#10b981' : (dec.confidence||0) >= 40 ? '#f59e0b' : '#a1a1aa' },
        { label: 'WM Loss',    value: fmt(rs.dreamer_wm_loss, 4) || '—',       color: '#a78bfa' },
        { label: 'Source',     value: meta.source?.replace('dreamer+', '') || (dec.dreamer_active ? 'active' : 'off'), color: '#6366f1' },
      ],
    },
    {
      number: 3,
      name: 'Risk Manager',
      subtitle: 'RPM · Kelly · VaR',
      state: rpmState,
      icon: '⚖️',
      accentColor: '#f59e0b',
      metrics: [
        { label: 'Kelly Frac',  value: fmtPct((rp.kelly_fraction||0)*100, 2),  color: '#a78bfa' },
        { label: 'VaR 95%',     value: fmtInr(rp.var_95_inr),                  color: '#f97316' },
        { label: 'Budget',      value: rp.risk_budget_state || 'NORMAL',       color: rp.risk_budget_state === 'STOP' ? '#ef4444' : rp.risk_budget_state === 'REDUCED' ? '#f59e0b' : '#10b981' },
        { label: 'Heat',        value: fmtPct(rp.portfolio_heat_pct, 1),       color: rp.heat_exceeded ? '#ef4444' : '#a1a1aa' },
      ],
    },
    {
      number: 4,
      name: 'Execution Engine',
      subtitle: 'Paper / Live / Shadow',
      state: execState,
      icon: '⚡',
      accentColor: '#10b981',
      metrics: [
        { label: 'Mode',    value: (execStats?.mode || rs.mode || 'paper').toUpperCase(), color: execStats?.mode === 'live' ? '#ef4444' : execStats?.mode === 'shadow' ? '#818cf8' : '#10b981' },
        { label: 'Fills',   value: execStats?.daily_fills ?? '0',                         color: '#a1a1aa' },
        { label: 'Gross P&L', value: fmtInr(execStats?.daily_pnl),                        color: (execStats?.daily_pnl||0) >= 0 ? '#10b981' : '#ef4444' },
        { label: 'Net P&L', value: fmtInr(execStats?.daily_net_pnl),                      color: (execStats?.daily_net_pnl||0) >= 0 ? '#10b981' : '#ef4444' },
      ],
    },
  ];

  return (
    <div data-testid="agent-status-panel">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-bold text-zinc-300 uppercase tracking-widest">
          Agent Pipeline
        </h2>
        <div className="flex items-center gap-1">
          {[1, 2, 3, 4].map(n => (
            <div
              key={n}
              className={`w-1.5 h-1.5 rounded-full ${n <= 4 ? 'bg-violet-500' : 'bg-zinc-700'}`}
              style={{ opacity: isRunning ? 1 : 0.3 }}
            />
          ))}
          <span className="text-[9px] text-zinc-600 ml-1">4 agents</span>
        </div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {agents.map(a => <AgentCard key={a.number} {...a} />)}
      </div>

      {/* Pipeline flow indicator */}
      {isRunning && (
        <div className="mt-2 flex items-center justify-center gap-1 text-[9px] text-zinc-700">
          {['Perception', '→', 'DreamerV3', '→', 'RPM', '→', 'Execution'].map((item, i) => (
            <span
              key={i}
              className={item === '→' ? 'text-zinc-700' : 'font-semibold text-zinc-600'}
            >
              {item}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
