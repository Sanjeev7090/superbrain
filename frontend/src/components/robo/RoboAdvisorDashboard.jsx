/**
 * RoboAdvisorDashboard — Phase 4
 * ================================
 * Complete institutional-grade Dreamer V3 Robo-Trader UI.
 *
 * Data sources (polling every 3 seconds):
 *  GET /api/robo/status       — core state, P&L, decision, circuit-breakers
 *  GET /api/robo/loop-status  — APScheduler loop state
 *  GET /api/robo/positions    — open positions + exec stats
 *  GET /api/robo/orders       — closed order history + P&L summary
 *
 * DISCLAIMER: PAPER TRADING ONLY by default. No guaranteed returns.
 * No financial advice. Past performance ≠ future results.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Settings, RefreshCw, AlertTriangle, TrendingUp,
  Activity, Zap, BarChart2, Clock,
} from 'lucide-react';

import DailyProgressBar      from './DailyProgressBar';
import AgentStatusPanel      from './AgentStatusPanel';
import TradeExplainability   from './TradeExplainability';
import RoboControls          from './RoboControls';
import TargetCapitalSettings from './TargetCapitalSettings';
import AgentDiscussionPanel  from './AgentDiscussionPanel';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const fmt    = (v, d = 0) => v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtInr = v => `₹${fmt(v, 0)}`;
const fmtPct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`;

// ── Status config ─────────────────────────────────────────────────────────────
const STATUS_MAP = {
  idle:            { label: 'Idle',             color: '#6b7280', pulse: false },
  scanning:        { label: 'Scanning',          color: '#f59e0b', pulse: true  },
  trading:         { label: 'Trading',           color: '#10b981', pulse: true  },
  paused:          { label: 'Paused',            color: '#6b7280', pulse: false },
  circuit_breaker: { label: 'Circuit Breaker',   color: '#ef4444', pulse: false },
  'market_closed': { label: 'Market Closed',     color: '#6b7280', pulse: false },
  error:           { label: 'Error',             color: '#ef4444', pulse: false },
};

// ── Stat card ─────────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, color = '#a1a1aa', icon: Icon, glow }) {
  return (
    <div
      className="bg-zinc-900/80 border border-zinc-800/60 rounded-xl p-3 flex flex-col gap-1 transition-all"
      style={glow ? { boxShadow: `0 0 16px ${color}18`, borderColor: color + '30' } : {}}
    >
      <div className="flex items-center gap-1.5">
        {Icon && <Icon size={10} style={{ color }} />}
        <p className="text-[9px] text-zinc-600 uppercase tracking-widest">{label}</p>
      </div>
      <p className="text-lg font-black tabular-nums leading-none" style={{ color }}>{value}</p>
      {sub && <p className="text-[9px] text-zinc-600">{sub}</p>}
    </div>
  );
}

// ── Open position mini-card ───────────────────────────────────────────────────
function OpenPositionCard({ pos, onClose }) {
  const isBuy  = pos.direction === 'BUY';
  const hasPnl = pos.unrealized_pnl != null;
  const pnlPos = (pos.unrealized_pnl || 0) >= 0;
  const [closing, setClosing] = useState(false);

  const handleClose = async () => {
    if (!window.confirm(`Close ${pos.ticker} position? This cannot be undone.`)) return;
    setClosing(true);
    try {
      await onClose(pos.order_id);
    } finally {
      setClosing(false);
    }
  };

  return (
    <div
      className="bg-zinc-900/80 border rounded-xl p-3 transition-all"
      style={{
        borderColor: hasPnl
          ? (pnlPos ? '#10b98150' : '#ef444450')
          : (isBuy ? '#10b98140' : '#ef444440'),
        boxShadow: hasPnl
          ? `0 0 20px ${pnlPos ? '#10b981' : '#ef4444'}15`
          : `0 0 16px ${isBuy ? '#10b981' : '#ef4444'}10`,
      }}
      data-testid={`open-pos-${pos.order_id}`}
    >
      {/* Top row: direction + ticker + mode + live P&L + close btn */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span
            className="text-[9px] font-black px-1.5 py-0.5 rounded"
            style={{
              background: isBuy ? '#10b98120' : '#ef444420',
              color:      isBuy ? '#10b981' : '#ef4444',
            }}
          >
            {pos.direction}
          </span>
          <span className="text-xs font-mono font-bold text-white">{pos.ticker}</span>
          {pos.status === 'PENDING' && (
            <span className="text-[8px] text-amber-400 bg-amber-900/30 px-1.5 py-0.5 rounded animate-pulse">
              PENDING {pos.mode === 'live' ? '30s' : ''}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {hasPnl && (
            <span
              className={`text-[10px] font-black px-2 py-0.5 rounded-lg ${pnlPos ? 'bg-emerald-900/30 text-emerald-400' : 'bg-red-900/30 text-red-400'}`}
              data-testid={`pos-unrealized-pnl-${pos.order_id}`}
            >
              {pnlPos ? '+' : ''}₹{Math.abs(pos.unrealized_pnl || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </span>
          )}
          <span
            className="text-[8px] px-1.5 py-0.5 rounded bg-zinc-800"
            style={{ color: pos.mode === 'live' ? '#ef4444' : pos.mode === 'shadow' ? '#818cf8' : '#a1a1aa' }}
          >
            {(pos.mode || 'PAPER').toUpperCase()}
          </span>
          {onClose && (
            <button
              onClick={handleClose}
              disabled={closing}
              data-testid={`close-pos-btn-${pos.order_id}`}
              title="Close this position"
              className="text-[9px] font-bold px-2 py-0.5 rounded-lg transition-all disabled:opacity-40"
              style={{
                background: 'rgba(239,68,68,0.12)',
                border: '1px solid rgba(239,68,68,0.35)',
                color: '#fca5a5',
              }}
            >
              {closing ? '...' : 'Close'}
            </button>
          )}
        </div>
      </div>

      {/* Live price row */}
      {pos.current_price != null && (
        <div className="flex items-center gap-2 mb-2 px-1">
          <span className="text-[9px] text-zinc-500">CMP</span>
          <span className="text-[11px] font-black text-white">₹{pos.current_price?.toLocaleString('en-IN')}</span>
          {pos.price_change != null && (
            <span className={`text-[9px] font-semibold ${pos.price_change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {pos.price_change >= 0 ? '+' : ''}₹{pos.price_change?.toFixed(2)}
              {pos.price_change_pct != null && ` (${pos.price_change_pct >= 0 ? '+' : ''}${pos.price_change_pct?.toFixed(2)}%)`}
            </span>
          )}
          {pos.pnl_pct != null && (
            <span className={`ml-auto text-[9px] font-bold px-1.5 py-0.5 rounded ${
              pos.pnl_pct >= 0 ? 'text-emerald-300 bg-emerald-900/20' : 'text-red-300 bg-red-900/20'
            }`} data-testid={`pos-pnl-pct-${pos.order_id}`}>
              {pos.pnl_pct >= 0 ? '+' : ''}{pos.pnl_pct?.toFixed(2)}%
            </span>
          )}
        </div>
      )}

      <div className="grid grid-cols-3 gap-1.5 text-center">
        {[
          { l: 'Entry',  v: fmtInr(pos.entry_price),    c: '#f59e0b' },
          { l: 'SL',     v: fmtInr(pos.sl_price),       c: '#ef4444' },
          { l: 'TP',     v: fmtInr(pos.tp_price),       c: '#10b981' },
        ].map(({ l, v, c }) => (
          <div key={l} className="bg-zinc-800/50 rounded-lg py-1">
            <p className="text-[8px] text-zinc-600">{l}</p>
            <p className="text-[10px] font-bold" style={{ color: c }}>{v}</p>
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[9px] text-zinc-500">
        <span>{pos.quantity} units · {fmtInr(pos.position_value)}</span>
        <span>Conf: {pos.confidence}%</span>
      </div>
    </div>
  );
}

// ── Notification area ─────────────────────────────────────────────────────────
function NotificationBanner({ rs, loopStatus }) {
  const msgs = [];
  if (rs?.circuit_breaker)
    msgs.push({ type: 'error', text: `Circuit breaker: ${rs.circuit_reason || 'Limit hit'}` });
  if (loopStatus?.last_error)
    msgs.push({ type: 'warn', text: `Loop error: ${loopStatus.last_error?.slice(0, 80)}` });
  if (rs?.consecutive_losses >= 3)
    msgs.push({ type: 'warn', text: `${rs.consecutive_losses} consecutive losses — position size reduced 50%` });
  if (!loopStatus?.market_open && rs?.auto_mode)
    msgs.push({ type: 'info', text: 'NSE market closed — loop waiting for 09:15 IST' });

  if (msgs.length === 0) return null;
  const typeStyle = {
    error: 'bg-red-900/25 border-red-700/40 text-red-400',
    warn:  'bg-amber-900/20 border-amber-700/30 text-amber-400',
    info:  'bg-blue-900/20 border-blue-700/30 text-blue-400',
  };
  return (
    <div className="space-y-1.5" data-testid="notification-banner">
      {msgs.map((m, i) => (
        <div key={i} className={`flex items-center gap-2 px-4 py-2 rounded-xl border text-xs ${typeStyle[m.type]}`}>
          <AlertTriangle size={12} className="flex-shrink-0" />
          {m.text}
        </div>
      ))}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// MAIN DASHBOARD
// ════════════════════════════════════════════════════════════════════════════

export default function RoboAdvisorDashboard({ selectedStock, onSelectStock }) {
  // ── State ─────────────────────────────────────────────────────────────────
  const [roboState,     setRoboState]     = useState(null);
  const [loopStatus,    setLoopStatus]    = useState(null);
  const [positions,     setPositions]     = useState([]);
  const [orders,        setOrders]        = useState([]);
  const [orderStats,    setOrderStats]    = useState({});
  const [execStats,     setExecStats]     = useState({});
  const [settings,      setSettings]      = useState({
    daily_profit_target: 1000,
    allocated_capital:   100000,
    ticker:              'RELIANCE.NS',
    risk_tolerance:      'moderate',
  });

  const [settingsOpen,   setSettingsOpen]   = useState(false);
  const [execMode,       setExecMode]       = useState('paper');
  const [intervalMin,    setIntervalMin]    = useState(5);
  const [loading,        setLoading]        = useState(false);
  const [modeLoading,    setModeLoading]    = useState(false);
  const [recalcLoading,  setRecalcLoading]  = useState(false);
  const [lastRefresh,    setLastRefresh]    = useState(null);
  const [activeTab,      setActiveTab]      = useState('overview'); // overview | agents

  // Hybrid Brain state
  const [brainState,    setBrainState]    = useState(null);
  const [brainDecision, setBrainDecision] = useState(null);
  const [brainAudit,    setBrainAudit]    = useState([]);
  const [brainLoading,  setBrainLoading]  = useState(false);
  const [brainTab,      setBrainTab]      = useState('state'); // state | audit

  // Danger mode picks
  const [dangerPicks, setDangerPicks]     = useState([]);
  const [dangerScanning, setDangerScanning] = useState(false);

  const pollRef = useRef(null);

  // Sync ticker from parent chart
  useEffect(() => {
    if (selectedStock?.ticker &&
        selectedStock.type !== 'CRYPTO' &&
        selectedStock.type !== 'OPTION') {
      setSettings(p => ({ ...p, ticker: selectedStock.ticker }));
    }
  }, [selectedStock]);

  // ── Fetch all state ────────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    try {
      const [st, loop, pos, ord, brSt, brAudit] = await Promise.all([
        axios.get(`${API}/robo/status`),
        axios.get(`${API}/robo/loop-status`).catch(() => ({ data: {} })),
        axios.get(`${API}/robo/positions`).catch(() => ({ data: {} })),
        axios.get(`${API}/robo/orders?limit=50`).catch(() => ({ data: {} })),
        axios.get(`${API}/hybrid-brain/state`).catch(() => ({ data: null })),
        axios.get(`${API}/hybrid-brain/audit?limit=10`).catch(() => ({ data: { decisions: [] } })),
      ]);

      // Core state
      if (st.data) {
        setRoboState(st.data);
        setSettings({
          daily_profit_target: st.data.daily_profit_target || 1000,
          allocated_capital:   st.data.allocated_capital   || 100000,
          ticker:              st.data.ticker              || 'RELIANCE.NS',
          risk_tolerance:      st.data.risk_tolerance      || 'moderate',
        });
        if (st.data.mode) setExecMode(st.data.mode);
      }
      // Loop
      if (loop.data?.loop)        setLoopStatus(loop.data.loop);
      if (loop.data?.exec_stats)  setExecStats(loop.data.exec_stats);

      // Positions
      if (pos.data?.open_positions) {
        setPositions(pos.data.open_positions || []);
        if (pos.data.mode) setExecMode(pos.data.mode);
      }
      // Orders
      if (ord.data?.orders) {
        setOrders(ord.data.orders || []);
        setOrderStats({
          daily_pnl:     ord.data.daily_pnl,
          daily_net_pnl: ord.data.daily_net_pnl,
          wins:          ord.data.wins,
          losses:        ord.data.losses,
          win_rate:      ord.data.win_rate,
          brokerage:     ord.data.daily_brokerage,
        });
      }
      // Brain
      if (brSt.data) setBrainState(brSt.data);
      if (brAudit.data?.decisions) setBrainAudit(brAudit.data.decisions);

      setLastRefresh(new Date());
    } catch { /* silent */ }
  }, []);

  // Polling 3s
  useEffect(() => {
    fetchAll();
    pollRef.current = setInterval(fetchAll, 3000);
    return () => clearInterval(pollRef.current);
  }, [fetchAll]);

  // ── Handlers ───────────────────────────────────────────────────────────────
  const handleToggleAuto = async () => {
    const isActive = roboState?.auto_mode;
    setLoading(true);
    try {
      if (isActive) {
        await axios.post(`${API}/robo/stop`);
        toast.success('Auto mode stopped');
      } else {
        const res = await axios.post(`${API}/robo/start`, {
          ticker:           settings.ticker,
          interval_minutes: intervalMin,
        });
        toast.success(res.data.message || 'Auto mode started');

        // Activate Hybrid Brain in parallel (backend warmup may already be running,
        // but also fire a manual initial decision so UI shows brain immediately)
        setTimeout(async () => {
          try {
            const symbol = (settings.ticker || 'NIFTY').replace('.NS', '').replace('.BO', '');
            const brRes = await axios.post(`${API}/hybrid-brain/decide`, { symbol });
            setBrainDecision(brRes.data);
            const auditRes = await axios.get(`${API}/hybrid-brain/audit?limit=10`);
            if (auditRes.data?.decisions) setBrainAudit(auditRes.data.decisions);
            toast.success(`Brain activated — ${brRes.data.action} @ ${brRes.data.confidence?.toFixed(1)}% conf`, {
              icon: '🧠',
              duration: 4000,
            });
          } catch { /* silent — brain is optional */ }
        }, 2500);  // slight delay so backend warmup has a head start
      }
      await fetchAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message || 'Toggle failed');
    } finally {
      setLoading(false);
    }
  };

  const handleModeChange = async (newMode) => {
    setModeLoading(true);
    try {
      const res = await axios.post(`${API}/robo/mode`, { mode: newMode });
      if (res.data.success) {
        setExecMode(newMode);
        toast.success(`Switched to ${newMode.toUpperCase()} mode`);
      } else {
        toast.error(res.data.error || 'Mode switch failed');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally {
      setModeLoading(false);
    }
  };

  const handleSetInterval = async (mins) => {
    setIntervalMin(mins);
    if (roboState?.auto_mode) {
      try {
        await axios.post(`${API}/robo/set-interval`, { interval_minutes: mins });
        toast.success(`Scan interval → ${mins} min`);
      } catch { /* silent */ }
    }
  };

  const handleRecalculate = async () => {
    setRecalcLoading(true);
    try {
      await axios.post(`${API}/robo/recalculate`, { trigger: 'manual' });
      await fetchAll();
      toast.success('Risk profile recalculated');
    } catch (e) {
      toast.error('Recalculate failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setRecalcLoading(false);
    }
  };

  const handleCloseAll = async () => {
    if (!window.confirm('Close ALL open positions? This cannot be undone.')) return;
    try {
      const res = await axios.post(`${API}/robo/close-all`);
      if (res.data.success) {
        toast.success(`Closed ${res.data.closed_count} position(s)`);
        await fetchAll();
      } else {
        toast.error(res.data.error || 'Close-all failed');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    }
  };

  const handleClosePosition = async (orderId) => {
    try {
      const res = await axios.post(`${API}/robo/close-position/${orderId}`);
      if (res.data.success) {
        const pnl = res.data.order?.pnl ?? res.data.pnl ?? null;
        toast.success(`Position closed${pnl != null ? ` · P&L ₹${pnl.toFixed(0)}` : ''}`);
        await fetchAll();
      } else {
        toast.error(res.data.error || 'Close failed');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    }
  };

  const handleResetDaily = async () => {
    try {
      await axios.post(`${API}/robo/reset-daily`);
      await fetchAll();
      toast.success('Daily counters reset');
    } catch { /* silent */ }
  };

  const handleBrainDecide = async () => {
    setBrainLoading(true);
    try {
      const symbol = (settings.ticker || 'NIFTY').replace('.NS', '').replace('.BO', '');
      const res = await axios.post(`${API}/hybrid-brain/decide`, { symbol });
      setBrainDecision(res.data);
      const auditRes = await axios.get(`${API}/hybrid-brain/audit?limit=10`);
      if (auditRes.data?.decisions) setBrainAudit(auditRes.data.decisions);
      toast.success(`Brain: ${res.data.action} @ ${res.data.confidence?.toFixed(1)}% conf`);
    } catch (e) {
      toast.error('Brain decide failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setBrainLoading(false);
    }
  };

  const handleBrainResetDay = async () => {
    try {
      await axios.post(`${API}/hybrid-brain/reset-daily`);
      const res = await axios.get(`${API}/hybrid-brain/state`);
      if (res.data) setBrainState(res.data);
      toast.success('Brain day reset — fear decayed');
    } catch { /* silent */ }
  };

  const handleDangerScan = async (force = false) => {
    setDangerScanning(true);
    try {
      const res = await axios.get(`${API}/robo/danger-scan?top=8${force ? '&force=true' : ''}`);
      if (res.data?.top_picks) setDangerPicks(res.data.top_picks);
    } catch (e) {
      toast.error('Danger scan failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setDangerScanning(false);
    }
  };

  // ── Derived ────────────────────────────────────────────────────────────────
  const rs          = roboState;
  const rp          = rs?.risk_profile || {};
  const dec         = rs?.current_decision || {};
  const status      = rs?.status || 'idle';
  const statusCfg   = STATUS_MAP[status] || STATUS_MAP.idle;
  const isActive    = rs?.auto_mode || false;
  const isBrainActive = isActive && (rs?.brain_active || brainDecision != null);
  const dailyPnl    = rs?.daily_pnl || 0;
  const target      = rs?.daily_profit_target || settings.daily_profit_target || 1;
  const isDangerMode = settings.risk_tolerance === 'danger';

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#050508] text-white font-sans">

      {/* ─── Sticky Header ─────────────────────────────────────────────── */}
      <div className="sticky top-0 z-20 bg-[#050508]/95 backdrop-blur border-b border-zinc-800/60 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          {/* Logo + Status */}
          <div className="flex items-center gap-3 min-w-0">
            <div
              className="w-9 h-9 rounded-xl flex items-center justify-center text-base flex-shrink-0"
              style={{ background: 'linear-gradient(135deg, #7c3aed, #4f46e5)', boxShadow: '0 0 16px #7c3aed40' }}
            >
              🤖
            </div>
            <div className="min-w-0">
              {/* Row 1: title + compact badges */}
              <div className="flex items-center gap-1.5">
                <h1 className="text-sm font-black text-white tracking-tight">Robot 3.O</h1>
                <span className="text-[9px] text-zinc-500 font-mono hidden xs:inline">Robo-Advisor</span>
                {settings.risk_tolerance === 'danger' && (
                  <span
                    className="text-[7px] font-black px-1 py-px rounded animate-pulse"
                    style={{ background: 'rgba(239,68,68,0.18)', color: '#f87171', border: '1px solid rgba(239,68,68,0.35)' }}
                    data-testid="danger-mode-badge"
                  >
                    F&O
                  </span>
                )}
                {isBrainActive && (
                  <span
                    className="text-[7px] font-black px-1 py-px rounded flex items-center gap-0.5 animate-pulse"
                    style={{ background: 'rgba(139,92,246,0.18)', color: '#c4b5fd', border: '1px solid rgba(139,92,246,0.35)' }}
                    data-testid="brain-active-badge"
                  >
                    <span className="w-1 h-1 rounded-full bg-violet-400 animate-ping inline-block" />
                    AI
                  </span>
                )}
              </div>
              {/* Row 2: status + mode + ticker */}
              <div className="flex items-center gap-1.5 mt-0.5">
                <div className={`flex items-center gap-1 ${statusCfg.pulse ? 'animate-pulse' : ''}`}>
                  <div className="w-1.5 h-1.5 rounded-full" style={{ background: statusCfg.color }} />
                  <span className="text-[9px] font-semibold" style={{ color: statusCfg.color }}>
                    {statusCfg.label}
                  </span>
                </div>
                <span className="text-[9px] text-zinc-600">·</span>
                <span
                  className="text-[9px] font-bold"
                  style={{ color: execMode === 'live' ? '#ef4444' : execMode === 'shadow' ? '#818cf8' : '#10b981' }}
                >
                  {execMode?.toUpperCase()}
                </span>
                {rs?.ticker && (
                  <>
                    <span className="text-[9px] text-zinc-600">·</span>
                    <span className="text-[9px] font-mono text-zinc-400 truncate max-w-[80px]">{rs.ticker}</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Right actions */}
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {lastRefresh && (
              <span className="text-[9px] text-zinc-700 hidden sm:block">
                {lastRefresh.toLocaleTimeString('en-IN')}
              </span>
            )}
            <button
              onClick={handleRecalculate}
              disabled={recalcLoading}
              className="p-2 rounded-xl bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 hover:text-white transition-all disabled:opacity-50"
              title="Force recalculate risk profile"
              data-testid="recalc-btn"
            >
              <RefreshCw size={12} className={recalcLoading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={() => setSettingsOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-300 hover:text-white text-[10px] font-semibold transition-all"
              data-testid="settings-btn"
            >
              <Settings size={11} />
              <span className="hidden sm:inline">Settings</span>
            </button>
          </div>
        </div>
      </div>

      {/* ─── Disclaimer Banner ────────────────────────────────────────── */}
      <div
        className="px-4 py-1.5 border-b"
        style={{
          background: execMode === 'live' ? 'rgba(239,68,68,0.06)' : 'rgba(245,158,11,0.06)',
          borderColor: execMode === 'live' ? 'rgba(239,68,68,0.2)' : 'rgba(245,158,11,0.15)',
        }}
      >
        <p
          className="text-[9px] text-center"
          style={{ color: execMode === 'live' ? '#ef4444' : '#d97706' }}
        >
          {execMode === 'live'
            ? '🔴 LIVE MODE — REAL ORDERS ON GROWW. Capital at risk. No guaranteed returns.'
            : '⚠️ PAPER TRADING — No real capital. No guaranteed returns. Past performance ≠ future results. SEBI advisor recommended.'}
        </p>
      </div>

      {/* ─── Scrollable body ───────────────────────────────────────────── */}
      <div className="px-3 py-4 space-y-4 max-w-5xl mx-auto">

        {/* Notifications */}
        <NotificationBanner rs={rs} loopStatus={loopStatus} />

        {/* ── Daily Progress (hero) ──────────────────────────────────── */}
        <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-4"
          style={{ boxShadow: dailyPnl > 0 ? '0 0 30px rgba(16,185,129,0.06)' : dailyPnl < 0 ? '0 0 30px rgba(239,68,68,0.06)' : 'none' }}>
          <DailyProgressBar
            currentPnl={dailyPnl}
            target={target}
            dailyTargetPct={rs?.daily_target_pct}
          />
        </div>

        {/* ── Quick Stats ──────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <StatCard
            label="Trades Today"
            value={rs?.daily_trades || 0}
            icon={Activity}
            color="#a78bfa"
            glow={isActive}
          />
          <StatCard
            label="Win / Loss"
            value={`${rs?.win_trades || 0}W / ${rs?.loss_trades || 0}L`}
            sub={rs?.win_trades || rs?.loss_trades
              ? `${Math.round((rs.win_trades/(Math.max(rs.win_trades+rs.loss_trades,1)))*100)}% WR`
              : 'No trades yet'}
            icon={BarChart2}
            color="#06b6d4"
          />
          <StatCard
            label="Dreamer Conf"
            value={dec.confidence != null ? fmtPct(dec.confidence) : '—'}
            sub={dec.signal || 'No signal'}
            icon={Zap}
            color={(dec.confidence||0) >= 60 ? '#10b981' : (dec.confidence||0) >= 35 ? '#f59e0b' : '#6b7280'}
            glow={(dec.confidence||0) >= 60}
          />
          <StatCard
            label="Next Scan"
            value={loopStatus?.next_cycle_time
              ? new Date(loopStatus.next_cycle_time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
              : '—'}
            sub={loopStatus?.running ? `Every ${loopStatus.interval_minutes}m` : 'Loop stopped'}
            icon={Clock}
            color={loopStatus?.running ? '#10b981' : '#6b7280'}
          />
        </div>

        {/* ── Tab Switcher ──────────────────────────────────────────────── */}
        <div className="flex gap-1 p-1 bg-zinc-900/60 border border-zinc-800/50 rounded-xl w-fit">
          {[
            { id: 'overview', label: 'Overview',       icon: BarChart2 },
            { id: 'agents',   label: 'Agent Discussion', icon: Activity  },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              data-testid={`tab-${id}`}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-[10px] font-bold transition-all ${
                activeTab === id
                  ? 'bg-violet-600/25 border border-violet-500/40 text-violet-200'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              <Icon size={11} />
              {label}
            </button>
          ))}
        </div>

        {/* ── OVERVIEW TAB ──────────────────────────────────────────────── */}
        {activeTab === 'overview' && (<>

        {/* ── Main 3-column layout ─────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">

          {/* Controls column */}
          <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-4">
            <p className="text-[9px] text-zinc-600 uppercase tracking-widest font-bold mb-3">
              Auto-Trader Controls
            </p>
            <RoboControls
              isActive={isActive}
              execMode={execMode}
              intervalMin={intervalMin}
              circuitBreaker={rs?.circuit_breaker || false}
              loading={loading}
              modeLoading={modeLoading}
              onToggleAuto={handleToggleAuto}
              onModeChange={handleModeChange}
              onSetInterval={handleSetInterval}
            />

            {/* Brain live status strip — visible when auto mode running */}
            {isActive && (
              <div
                className="mt-3 rounded-xl px-3 py-2 border flex items-center gap-3"
                style={{ background: 'rgba(139,92,246,0.06)', borderColor: 'rgba(139,92,246,0.2)' }}
                data-testid="brain-live-strip"
              >
                {/* Pulsing brain indicator */}
                <div className="relative flex-shrink-0">
                  <div
                    className="w-6 h-6 rounded-lg flex items-center justify-center text-[9px] font-black"
                    style={{ background: isBrainActive ? 'rgba(139,92,246,0.3)' : 'rgba(63,63,70,0.5)', color: isBrainActive ? '#c4b5fd' : '#52525b' }}
                  >
                    HSB
                  </div>
                  {isBrainActive && (
                    <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-violet-400 animate-ping" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[9px] font-bold" style={{ color: isBrainActive ? '#c4b5fd' : '#52525b' }}>
                    {isBrainActive ? 'Hybrid Brain Active' : 'Brain Standby'}
                  </p>
                  {isBrainActive && brainDecision && (
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className={`text-[8px] font-black px-1.5 py-0.5 rounded ${
                        brainDecision.action === 'BUY' ? 'bg-emerald-900/30 text-emerald-400'
                        : brainDecision.action === 'SELL' ? 'bg-red-900/30 text-red-400'
                        : 'bg-zinc-800 text-zinc-400'
                      }`}>{brainDecision.action}</span>
                      <span className="text-[8px] text-zinc-500">{brainDecision.confidence?.toFixed(0)}% conf</span>
                      {brainDecision.survival?.fear > 0.3 && (
                        <span className="text-[8px] text-amber-400">⚠ fear {(brainDecision.survival.fear * 100).toFixed(0)}%</span>
                      )}
                      <span className="text-[8px] text-purple-400/60">{brainDecision.psych?.regime}</span>
                    </div>
                  )}
                </div>
                {/* Manual fire button */}
                <button
                  onClick={handleBrainDecide}
                  disabled={brainLoading}
                  data-testid="brain-fire-mini-btn"
                  className="text-[8px] px-2 py-1 rounded-lg font-bold transition-all disabled:opacity-50 shrink-0"
                  style={{ background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)', color: '#a78bfa' }}
                >
                  {brainLoading ? '⟳' : '⚡'}
                </button>
              </div>
            )}

            {/* Reset daily */}
            <button
              onClick={handleResetDaily}
              className="w-full mt-3 py-1.5 rounded-xl bg-zinc-800/50 hover:bg-zinc-800 border border-zinc-800 text-zinc-500 hover:text-zinc-300 text-[10px] font-semibold transition-all"
              data-testid="reset-daily-btn"
            >
              Reset Daily Counters
            </button>
          </div>

          {/* Agent status */}
          <div className="lg:col-span-2 bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-4">
            <AgentStatusPanel
              roboState={rs}
              loopStatus={loopStatus}
              execStats={execStats}
            />
          </div>
        </div>

        {/* ── Risk Profile Row ──────────────────────────────────────────── */}
        {rp.feasibility_label && (
          <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <p className="text-[9px] text-zinc-600 uppercase tracking-widest font-bold">
                Risk Profile · {rp.feasibility_label}
              </p>
              <div
                className="px-2.5 py-1 rounded-full text-[9px] font-black"
                style={{
                  background: (rp.feasibility_color || '#f59e0b') + '18',
                  color:      rp.feasibility_color || '#f59e0b',
                  border:     `1px solid ${rp.feasibility_color || '#f59e0b'}30`,
                }}
              >
                {rp.feasibility_score ?? '—'}/100
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
              {[
                { l: 'Daily Ret',  v: fmtPct(rp.required_daily_return_pct),    c: rp.feasibility_color || '#f59e0b' },
                { l: 'Pos Size',   v: fmtInr(rp.position_size_inr),             c: '#3b82f6' },
                { l: 'Max Loss',   v: fmtInr(rp.max_daily_loss_inr),            c: '#ef4444' },
                { l: 'VaR 95%',   v: fmtInr(rp.var_95_inr),                    c: '#f97316' },
                { l: 'CVaR 95%',  v: fmtInr(rp.cvar_95_inr),                   c: '#dc2626' },
                { l: 'Kelly',     v: fmtPct((rp.kelly_fraction||0)*100, 2),     c: '#a78bfa' },
                { l: 'Budget',    v: rp.risk_budget_state || 'NORMAL',          c: rp.risk_budget_state === 'STOP' ? '#ef4444' : '#10b981' },
                { l: 'R:R Ratio', v: `1:${rp.recommended_rr || '—'}`,          c: '#06b6d4' },
              ].map(({ l, v, c }) => (
                <div key={l} className="bg-zinc-800/50 rounded-xl p-2 text-center">
                  <p className="text-[8px] text-zinc-600 mb-0.5">{l}</p>
                  <p className="text-[11px] font-bold" style={{ color: c }}>{v}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Open Positions ────────────────────────────────────────────── */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-[9px] text-zinc-600 uppercase tracking-widest font-bold flex items-center gap-1.5">
              {positions.length > 0 && (
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              )}
              Open Positions ({positions.length})
            </p>
            {positions.length > 0 && (
              <button
                onClick={handleCloseAll}
                className="text-[9px] text-red-400 hover:text-red-300 bg-red-900/20 hover:bg-red-900/30 border border-red-700/30 px-2.5 py-1 rounded-lg transition-all"
                data-testid="close-all-btn"
              >
                Close All
              </button>
            )}
          </div>
          {positions.length === 0 ? (
            <div className="bg-zinc-900/40 border border-zinc-800/40 rounded-2xl flex items-center justify-center py-8 text-zinc-700">
              <div className="text-center">
                <div className="text-2xl mb-1.5">📊</div>
                <p className="text-xs">No open positions</p>
                <p className="text-[9px] mt-0.5">
                  {isActive ? 'Waiting for entry signal…' : 'Start auto mode to begin trading'}
                </p>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {positions.map(pos => <OpenPositionCard key={pos.order_id} pos={pos} onClose={handleClosePosition} />)}
            </div>
          )}
        </div>

        {/* ── DANGER MODE: F&O Universe Picks ───────────────────────────── */}
        {isDangerMode && (
          <div
            className="rounded-2xl border p-4"
            style={{ background: 'rgba(239,68,68,0.05)', borderColor: 'rgba(239,68,68,0.25)' }}
            data-testid="danger-picks-panel"
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <div
                  className="w-7 h-7 rounded-lg flex items-center justify-center"
                  style={{ background: 'rgba(239,68,68,0.2)', border: '1px solid rgba(239,68,68,0.4)' }}
                >
                  <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="#ef4444" strokeWidth="2.5">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                  </svg>
                </div>
                <div>
                  <p className="text-xs font-black text-red-400">Danger Mode — F&O Picks</p>
                  <p className="text-[9px] text-zinc-600">Momentum + PCR Priority · Auto-refreshes each cycle</p>
                </div>
              </div>
              <button
                onClick={() => handleDangerScan(true)}
                disabled={dangerScanning}
                data-testid="danger-scan-btn"
                className="px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all disabled:opacity-50"
                style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)', color: '#fca5a5' }}
              >
                {dangerScanning ? '⟳ Scanning…' : '🔍 Scan Now'}
              </button>
            </div>

            {dangerPicks.length === 0 ? (
              <div className="py-6 text-center text-zinc-600">
                <p className="text-xs">No scan results yet</p>
                <p className="text-[9px] mt-0.5">Click "Scan Now" to run F&O universe scan</p>
              </div>
            ) : (
              <div className="space-y-1.5" data-testid="danger-picks-list">
                {dangerPicks.map((pick, i) => {
                  const pcrColor = pick.pcr_signal === 'STRONGLY_BULLISH' ? '#10b981'
                    : pick.pcr_signal === 'BULLISH' ? '#34d399'
                    : pick.pcr_signal === 'BEARISH' ? '#f87171'
                    : pick.pcr_signal === 'STRONGLY_BEARISH' ? '#ef4444'
                    : '#6b7280';
                  return (
                    <div key={pick.ticker}
                      className="flex items-center gap-2 px-3 py-2.5 rounded-xl border"
                      style={{ background: i === 0 ? 'rgba(239,68,68,0.08)' : 'rgba(39,39,42,0.5)', borderColor: i === 0 ? 'rgba(239,68,68,0.35)' : 'rgba(63,63,70,0.5)' }}
                      data-testid={`danger-pick-${pick.ticker}`}
                    >
                      {/* Rank */}
                      <span className="text-[9px] font-black w-4 text-center"
                        style={{ color: i === 0 ? '#ef4444' : '#6b7280' }}>
                        #{i + 1}
                      </span>
                      {/* Type badge */}
                      <span className="text-[8px] font-bold px-1.5 py-0.5 rounded"
                        style={{ background: pick.type === 'index' ? 'rgba(139,92,246,0.2)' : 'rgba(59,130,246,0.15)', color: pick.type === 'index' ? '#a78bfa' : '#60a5fa' }}>
                        {pick.type === 'index' ? 'IDX' : 'STK'}
                      </span>
                      {/* Ticker + sector */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="text-[11px] font-black text-white">{pick.ticker.replace('.NS','').replace('.BO','')}</span>
                          <span className="text-[8px] text-zinc-600">{pick.sector}</span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className={`text-[9px] font-semibold ${pick.r5d >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {pick.r5d >= 0 ? '+' : ''}{pick.r5d}% 5d
                          </span>
                          <span className="text-[8px] text-zinc-600">Vol×{pick.vol_ratio?.toFixed(1)}</span>
                          <span className="text-[8px] text-zinc-600">RSI {pick.rsi}</span>
                        </div>
                      </div>
                      {/* PCR signal */}
                      {pick.pcr_signal && pick.pcr_signal !== 'NEUTRAL' && (
                        <div className="text-right">
                          <span className="text-[8px] font-bold px-1.5 py-0.5 rounded"
                            style={{ background: `${pcrColor}20`, color: pcrColor }}>
                            {pick.pcr_signal.replace('STRONGLY_', 'S.').replace('_', ' ')}
                          </span>
                          <p className="text-[8px] text-zinc-600 mt-0.5">+{pick.pcr_boost}pts</p>
                        </div>
                      )}
                      {/* Score */}
                      <div className="text-right w-12">
                        <p className="text-[11px] font-black" style={{ color: i === 0 ? '#ef4444' : '#9ca3af' }}>
                          {pick.final_score?.toFixed(0)}
                        </p>
                        <p className="text-[8px] text-zinc-600">score</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ── Trade Explainability Log ──────────────────────────────────── */}
        <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-4">
          <TradeExplainability orders={orders} orderStats={orderStats} />
        </div>

        {/* ── Hybrid Super Brain ────────────────────────────────────────── */}
        <div
          className="rounded-2xl p-4 border"
          style={{ background: 'rgba(88,28,135,0.06)', borderColor: 'rgba(139,92,246,0.25)' }}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg flex items-center justify-center text-[10px] font-black"
                style={{ background: 'linear-gradient(135deg,#7c3aed,#c026d3)', color: '#fff' }}>
                HSB
              </div>
              <div>
                <h3 className="text-xs font-black text-white">Hybrid Super Brain</h3>
                <p className="text-[9px] text-zinc-500">Psychology + Survival · DreamerV3 Fusion</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {/* Sub-tabs */}
              <div className="flex rounded-lg border border-zinc-800 overflow-hidden">
                {[['state', 'Brain State'], ['audit', 'Decision Log']].map(([t, label]) => (
                  <button key={t} onClick={() => setBrainTab(t)}
                    data-testid={`brain-tab-${t}`}
                    className={`px-2.5 py-1 text-[9px] font-bold transition-colors ${
                      brainTab === t
                        ? 'bg-violet-700/40 text-violet-200'
                        : 'bg-zinc-900 text-zinc-500 hover:text-zinc-300'
                    }`}>{label}</button>
                ))}
              </div>
              <button
                onClick={handleBrainDecide}
                disabled={brainLoading}
                data-testid="brain-decide-btn"
                className="px-3 py-1 rounded-lg text-[10px] font-bold transition-all disabled:opacity-50"
                style={{ background: 'rgba(124,58,237,0.2)', border: '1px solid rgba(139,92,246,0.4)', color: '#c4b5fd' }}
              >
                {brainLoading ? '⟳ Thinking…' : '⚡ Fire Brain'}
              </button>
            </div>
          </div>

          {/* Brain State tab */}
          {brainTab === 'state' && (
            <>
              {/* Key metrics row */}
              {brainState && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
                  {/* Fear gauge */}
                  <div className={`rounded-xl p-3 text-center border ${
                    brainState.fear_level > 0.6 ? 'bg-red-900/15 border-red-700/40'
                    : brainState.fear_level > 0.3 ? 'bg-amber-900/15 border-amber-700/40'
                    : 'bg-emerald-900/15 border-emerald-700/40'
                  }`}>
                    <p className="text-[8px] text-zinc-600 uppercase tracking-wider mb-1.5">Fear Level</p>
                    <div className="relative w-10 h-10 mx-auto mb-1">
                      <svg viewBox="0 0 44 44" className="w-full h-full -rotate-90">
                        <circle cx="22" cy="22" r="17" fill="none" stroke="#27272a" strokeWidth="5"/>
                        <circle cx="22" cy="22" r="17" fill="none"
                          stroke={brainState.fear_level > 0.6 ? '#ef4444' : brainState.fear_level > 0.3 ? '#f59e0b' : '#10b981'}
                          strokeWidth="5" strokeLinecap="round"
                          strokeDasharray={`${brainState.fear_level * 106.8} 106.8`}
                        />
                      </svg>
                      <span className="absolute inset-0 flex items-center justify-center text-[9px] font-black"
                        style={{ color: brainState.fear_level > 0.6 ? '#ef4444' : brainState.fear_level > 0.3 ? '#f59e0b' : '#10b981' }}>
                        {(brainState.fear_level * 100).toFixed(0)}%
                      </span>
                    </div>
                    <p className="text-[8px] font-bold"
                      style={{ color: brainState.fear_level > 0.8 ? '#ef4444' : brainState.fear_level > 0.6 ? '#f97316' : brainState.fear_level > 0.3 ? '#f59e0b' : '#10b981' }}>
                      {brainState.fear_level > 0.8 ? 'CIRCUIT BREAK' : brainState.fear_level > 0.6 ? 'High Fear' : brainState.fear_level > 0.3 ? 'Cautious' : 'Calm'}
                    </p>
                  </div>
                  {/* Consecutive fails */}
                  <div className={`rounded-xl p-3 text-center border ${
                    brainState.consecutive_fail >= 3 ? 'bg-red-900/15 border-red-700/40' : 'bg-zinc-900/60 border-zinc-800/50'
                  }`}>
                    <p className="text-[8px] text-zinc-600 uppercase tracking-wider mb-1.5">Consec. Misses</p>
                    <p className="text-2xl font-black tabular-nums"
                      style={{ color: brainState.consecutive_fail >= 3 ? '#ef4444' : brainState.consecutive_fail >= 1 ? '#f59e0b' : '#10b981' }}>
                      {brainState.consecutive_fail}
                    </p>
                    <p className="text-[8px] text-zinc-600">/{brainState.config?.grace_days || 5} grace</p>
                  </div>
                  {/* Daily target */}
                  <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-xl p-3 text-center">
                    <p className="text-[8px] text-zinc-600 uppercase tracking-wider mb-1.5">Daily Target</p>
                    <p className="text-lg font-black text-blue-400">{brainState.daily_target_pct?.toFixed(2)}%</p>
                    <p className="text-[8px] text-zinc-600">
                      PnL: <span className={brainState.current_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {brainState.current_pnl_pct?.toFixed(3)}%
                      </span>
                    </p>
                  </div>
                  {/* Last PnL */}
                  <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-xl p-3 text-center">
                    <p className="text-[8px] text-zinc-600 uppercase tracking-wider mb-1.5">Last PnL</p>
                    <p className="text-lg font-black"
                      style={{ color: (brainState.last_pnl_pct||0) >= 0 ? '#10b981' : '#ef4444' }}>
                      {brainState.last_pnl_pct?.toFixed(3)}%
                    </p>
                    <p className="text-[8px] text-zinc-600">Grace: {brainState.grace_days}d</p>
                  </div>
                </div>
              )}

              {/* Latest Decision */}
              {brainDecision ? (
                <div className={`rounded-xl border p-3 mb-3 ${
                  brainDecision.action === 'BUY' ? 'bg-emerald-900/10 border-emerald-700/30'
                  : brainDecision.action === 'SELL' ? 'bg-red-900/10 border-red-700/30'
                  : 'bg-zinc-900/60 border-zinc-800/50'
                }`}>
                  {/* Action + conf */}
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <span className={`text-sm font-black px-3 py-1 rounded-lg ${
                        brainDecision.action === 'BUY' ? 'bg-emerald-500/15 text-emerald-400'
                        : brainDecision.action === 'SELL' ? 'bg-red-500/15 text-red-400'
                        : 'bg-zinc-700/50 text-zinc-300'
                      }`} data-testid="brain-action-badge">
                        {brainDecision.action === 'BUY' ? '▲' : brainDecision.action === 'SELL' ? '▼' : '●'} {brainDecision.action}
                      </span>
                      <div>
                        <p className="text-xs font-bold text-white">{brainDecision.symbol}</p>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          <div className="h-1.5 w-20 bg-zinc-800 rounded-full overflow-hidden">
                            <div className="h-full rounded-full" style={{
                              width: `${brainDecision.confidence}%`,
                              background: brainDecision.confidence > 68 ? '#10b981' : brainDecision.confidence > 40 ? '#f59e0b' : '#ef4444',
                            }}/>
                          </div>
                          <span className="text-[9px] text-zinc-400">{brainDecision.confidence?.toFixed(1)}% conf</span>
                        </div>
                      </div>
                    </div>
                    <div className="text-right">
                      <span className={`px-2 py-0.5 rounded text-[8px] font-bold ${
                        brainDecision.risk_alert === 'good' ? 'bg-emerald-900/30 text-emerald-400'
                        : brainDecision.risk_alert === 'warning' ? 'bg-amber-900/30 text-amber-400'
                        : 'bg-red-900/30 text-red-400'
                      }`}>{(brainDecision.risk_alert||'').toUpperCase()}</span>
                      {brainDecision.cached && (
                        <p className="text-[8px] text-amber-600 mt-0.5">cached</p>
                      )}
                    </div>
                  </div>

                  {/* Psych metrics */}
                  {brainDecision.psych && (
                    <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5 mb-2">
                      {[
                        { l: 'FOMO',     v: brainDecision.psych.fomo_score,             f: v => (v*100).toFixed(0)+'%', c: brainDecision.psych.fomo_score > 0.6 ? '#f97316' : '#a1a1aa' },
                        { l: 'Apathy',   v: brainDecision.psych.apathy_score,           f: v => (v*100).toFixed(0)+'%', c: brainDecision.psych.apathy_score > 0.5 ? '#6b7280' : '#a1a1aa' },
                        { l: 'Cred',     v: brainDecision.psych.narrative_credibility,  f: v => (v*100).toFixed(0)+'%', c: brainDecision.psych.narrative_credibility > 0.5 ? '#10b981' : '#ef4444' },
                        { l: 'Momentum', v: brainDecision.psych.momentum,               f: v => (v*100).toFixed(0)+'%', c: brainDecision.psych.momentum > 0.55 ? '#10b981' : brainDecision.psych.momentum < 0.4 ? '#ef4444' : '#f59e0b' },
                        { l: 'Vol×',     v: brainDecision.psych.volume_thrust,          f: v => v?.toFixed(2)+'x',       c: brainDecision.psych.volume_thrust > 1.2 ? '#10b981' : '#a1a1aa' },
                        { l: 'Regime',   v: brainDecision.psych.regime,                 f: v => (v||'—').replace('_',' ').slice(0,8), c: '#a78bfa' },
                      ].map(({ l, v, f, c }) => (
                        <div key={l} className="bg-zinc-900/70 rounded-lg p-1.5 text-center">
                          <p className="text-[8px] text-zinc-600 mb-0.5">{l}</p>
                          <p className="text-[10px] font-bold" style={{ color: c }}>{v != null ? f(v) : '—'}</p>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Confidence components */}
                  {brainDecision.components && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {Object.entries(brainDecision.components).map(([k, v]) => (
                        <span key={k} className={`text-[8px] px-1.5 py-0.5 rounded-full font-semibold ${
                          Number(v) > 0 ? 'bg-emerald-900/30 text-emerald-400' : Number(v) < 0 ? 'bg-red-900/30 text-red-400' : 'bg-zinc-800 text-zinc-500'
                        }`}>
                          {k.replace(/_/g,' ')}: {Number(v) > 0 ? '+' : ''}{Number(v).toFixed(2)}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Hidden value gap + reasoning */}
                  {brainDecision.psych?.hidden_value_gap && (
                    <p className="text-[9px] text-zinc-400 mb-1">{brainDecision.psych.hidden_value_gap}</p>
                  )}
                  {brainDecision.reasoning && (
                    <p className="text-[8px] text-zinc-600 font-mono border-t border-zinc-800/60 pt-1.5">
                      {brainDecision.reasoning}
                    </p>
                  )}
                </div>
              ) : (
                <div className="rounded-xl border border-zinc-800/40 bg-zinc-900/40 py-6 text-center text-zinc-600">
                  <p className="text-xs">No brain decision yet</p>
                  <p className="text-[9px] mt-0.5">Click "⚡ Fire Brain" to generate a fresh decision</p>
                </div>
              )}

              {/* Size scalar + reset */}
              {brainDecision && (
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-lg px-3 py-1.5">
                    <p className="text-[8px] text-zinc-600">Size Scalar</p>
                    <p className="text-sm font-black text-violet-400">×{brainDecision.size_scalar?.toFixed(3)}</p>
                  </div>
                  <button
                    onClick={handleBrainResetDay}
                    className="px-3 py-1.5 rounded-lg bg-zinc-800/50 hover:bg-zinc-800 border border-zinc-800 text-zinc-500 hover:text-zinc-300 text-[10px] font-semibold transition-all"
                    data-testid="brain-reset-day-btn"
                  >
                    Reset Brain Day
                  </button>
                </div>
              )}
            </>
          )}

          {/* Audit tab */}
          {brainTab === 'audit' && (
            <>
              {brainAudit.length === 0 ? (
                <div className="py-8 text-center text-zinc-600">
                  <p className="text-xs">No brain decisions logged</p>
                  <p className="text-[9px] mt-0.5">Click "⚡ Fire Brain" to start</p>
                </div>
              ) : (
                <div className="space-y-1.5 max-h-72 overflow-y-auto" data-testid="brain-audit-list">
                  {brainAudit.map((d, i) => (
                    <div key={d.id || i} className={`flex items-start gap-2 p-2.5 rounded-xl border ${
                      d.action === 'BUY' ? 'bg-emerald-900/08 border-emerald-800/30'
                      : d.action === 'SELL' ? 'bg-red-900/08 border-red-800/30'
                      : 'bg-zinc-900/50 border-zinc-800/40'
                    }`}>
                      <span className="text-[9px] font-black px-1.5 py-0.5 rounded shrink-0"
                        style={{
                          background: d.action==='BUY'?'rgba(16,185,129,0.15)':d.action==='SELL'?'rgba(239,68,68,0.15)':'rgba(107,114,128,0.2)',
                          color:      d.action==='BUY'?'#10b981':d.action==='SELL'?'#ef4444':'#9ca3af',
                        }}>{d.action}</span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-[10px] font-bold text-white">{d.symbol}</span>
                          <span className="text-[9px] text-violet-400">{d.confidence?.toFixed(1)}%</span>
                          <span className={`text-[8px] px-1.5 py-0.5 rounded font-bold ${
                            d.risk_alert==='good'?'bg-emerald-900/25 text-emerald-400':d.risk_alert==='danger'?'bg-red-900/25 text-red-400':'bg-amber-900/25 text-amber-400'
                          }`}>{(d.risk_alert||'').toUpperCase()}</span>
                          {d.psych?.regime && <span className="text-[8px] text-purple-400">{d.psych.regime}</span>}
                        </div>
                        {d.psych?.hidden_value_gap && (
                          <p className="text-[8px] text-zinc-500 mt-0.5 truncate">{d.psych.hidden_value_gap}</p>
                        )}
                      </div>
                      <span className="text-[8px] text-zinc-600 shrink-0">
                        {d.timestamp ? new Date(d.timestamp).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}) : ''}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Capital State Vector (collapsed) ─────────────────────────── */}
        {rs?.capital_state_vector && (
          <details className="bg-zinc-900/40 border border-zinc-800/40 rounded-2xl overflow-hidden">
            <summary className="px-4 py-3 text-[10px] text-zinc-500 cursor-pointer hover:text-zinc-300 transition-colors font-semibold uppercase tracking-wider">
              DreamerV3 Capital State Vector (6 dims)
            </summary>
            <div className="px-4 pb-4 grid grid-cols-3 sm:grid-cols-6 gap-2">
              {Object.entries(rs.capital_state_vector).map(([key, val]) => (
                <div key={key} className="bg-zinc-800/50 rounded-xl p-2">
                  <p className="text-[8px] text-zinc-600 mb-1 leading-tight">
                    {key.replace(/_/g, ' ')}
                  </p>
                  <div className="h-1 bg-zinc-700 rounded-full overflow-hidden mb-1">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${Math.min(Math.abs(val) * 100, 100)}%`,
                        background: val < 0 ? '#ef4444' : '#8b5cf6',
                      }}
                    />
                  </div>
                  <p className="text-[10px] font-mono text-center" style={{ color: val < 0 ? '#ef4444' : '#a78bfa' }}>
                    {Number(val).toFixed(3)}
                  </p>
                </div>
              ))}
            </div>
          </details>
        )}

        {/* ── Full risk warnings ────────────────────────────────────────── */}
        {rp.feasibility_warnings?.length > 0 && (
          <div className="space-y-1">
            {rp.feasibility_warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2 text-[10px] text-amber-400 bg-amber-900/12 border border-amber-700/20 rounded-xl px-3 py-2">
                <AlertTriangle size={10} className="flex-shrink-0 mt-0.5" />
                {w}
              </div>
            ))}
          </div>
        )}

        {/* Bottom spacer */}
        <div className="h-6" />

        {/* ── OVERVIEW TAB close ─────────────────────────────────────────── */}
        </>)}

        {/* ── AGENTS TAB ────────────────────────────────────────────────── */}
        {activeTab === 'agents' && (
          <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-4">
            <AgentDiscussionPanel capital={settings.allocated_capital || 100000} onSelectStock={onSelectStock} />
          </div>
        )}

        {/* Bottom spacer */}
        <div className="h-6" />
      </div>

      {/* ─── Settings Modal ────────────────────────────────────────────── */}
      {settingsOpen && (
        <TargetCapitalSettings
          settings={settings}
          onSave={fetchAll}
          onClose={() => setSettingsOpen(false)}
          onSelectStock={onSelectStock}
        />
      )}
    </div>
  );
}
