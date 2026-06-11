import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// ─── Helpers ──────────────────────────────────────────────────────────────────

const fmt = (n, dec = 0) =>
  n == null
    ? '—'
    : new Intl.NumberFormat('en-IN', {
        minimumFractionDigits: dec,
        maximumFractionDigits: dec,
      }).format(n);

const fmtInr = (n) =>
  n == null ? '—' : `₹${fmt(n, 0)}`;

const fmtPct = (n, dec = 2) =>
  n == null ? '—' : `${Number(n).toFixed(dec)}%`;

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// ─── Color helpers ────────────────────────────────────────────────────────────
const SIGNAL_COLORS = {
  BUY:  { bg: '#10b981', text: '#ecfdf5', icon: '▲' },
  SELL: { bg: '#ef4444', text: '#fef2f2', icon: '▼' },
  HOLD: { bg: '#6b7280', text: '#f9fafb', icon: '●' },
};

const STATUS_MAP = {
  idle:            { label: 'Idle',            color: '#6b7280', pulse: false },
  scanning:        { label: 'Scanning…',        color: '#3b82f6', pulse: true  },
  trading:         { label: 'Trading',          color: '#10b981', pulse: true  },
  paused:          { label: 'Paused',           color: '#f59e0b', pulse: false },
  circuit_breaker: { label: 'Circuit Breaker',  color: '#ef4444', pulse: false },
};

// ─── Sub-components ───────────────────────────────────────────────────────────

function FeasibilityGauge({ score = 50, label = '', color = '#f59e0b' }) {
  const angle = clamp((score / 100) * 180, 0, 180);
  const rad   = (angle - 90) * (Math.PI / 180);
  const cx = 60, cy = 60, r = 48;
  const nx = cx + r * Math.cos(rad);
  const ny = cy + r * Math.sin(rad);
  const arcEnd = (pct) => {
    const a = (pct * 180 - 90) * (Math.PI / 180);
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const [bx, by] = arcEnd(0);
  const [ex, ey] = arcEnd(1);
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 120 70" className="w-32 h-20">
        {/* Track arc */}
        <path
          d={`M${bx},${by} A${r},${r} 0 0 1 ${ex},${ey}`}
          fill="none" stroke="#374151" strokeWidth="12" strokeLinecap="round"
        />
        {/* Fill arc */}
        {score > 0 && (
          <path
            d={`M${bx},${by} A${r},${r} 0 0 1 ${nx},${ny}`}
            fill="none" stroke={color} strokeWidth="12" strokeLinecap="round"
          />
        )}
        {/* Needle */}
        <line
          x1={cx} y1={cy}
          x2={cx + (r - 8) * Math.cos(rad)}
          y2={cy + (r - 8) * Math.sin(rad)}
          stroke="white" strokeWidth="2.5" strokeLinecap="round"
        />
        <circle cx={cx} cy={cy} r="4" fill="white" />
        <text x={cx} y={cy + 20} textAnchor="middle" fill={color} fontSize="14" fontWeight="bold">
          {score}
        </text>
      </svg>
      <span className="text-xs font-semibold mt-1" style={{ color }}>{label}</span>
    </div>
  );
}

function ProgressBar({ current, target, label }) {
  const pct    = target > 0 ? clamp((current / target) * 100, -100, 200) : 0;
  const pctVis = clamp(Math.abs(pct), 0, 100);
  const isNeg  = current < 0;
  const color  = isNeg ? '#ef4444' : pct >= 100 ? '#10b981' : '#3b82f6';
  return (
    <div className="w-full">
      {label && <p className="text-xs text-zinc-400 mb-1">{label}</p>}
      <div className="relative h-4 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pctVis}%`, background: color }}
        />
        <span
          className="absolute inset-0 flex items-center justify-center text-[10px] font-bold text-white"
          style={{ textShadow: '0 1px 2px #000' }}
        >
          {fmtPct(pct, 0)} of daily target
        </span>
      </div>
    </div>
  );
}

function StatCard({ label, value, sub, color = '#a1a1aa', icon }) {
  return (
    <div className="bg-zinc-800/60 border border-zinc-700/40 rounded-xl p-3 flex flex-col gap-1">
      <p className="text-[10px] text-zinc-500 uppercase tracking-wider flex items-center gap-1">
        {icon && <span>{icon}</span>}
        {label}
      </p>
      <p className="text-lg font-bold" style={{ color }}>{value}</p>
      {sub && <p className="text-[10px] text-zinc-500">{sub}</p>}
    </div>
  );
}

function TradeRow({ trade, index }) {
  const isPnlPos = (trade.pnl || 0) >= 0;
  return (
    <div
      className={`flex items-center gap-2 py-2 px-3 rounded-lg border ${
        index % 2 === 0 ? 'bg-zinc-800/30' : 'bg-zinc-800/10'
      } border-zinc-700/20`}
    >
      <span
        className="text-[10px] font-bold px-2 py-0.5 rounded"
        style={{
          background: trade.direction === 'BUY' ? '#10b98133' : '#ef444433',
          color: trade.direction === 'BUY' ? '#10b981' : '#ef4444',
        }}
      >
        {trade.direction}
      </span>
      <span className="text-zinc-300 text-xs font-mono flex-1 min-w-0 truncate">{trade.ticker}</span>
      <span className="text-zinc-400 text-[10px]">#{trade.trade_id}</span>
      <span className="text-zinc-400 text-[10px]">@ ₹{fmt(trade.entry_price, 0)}</span>
      <span className={`text-xs font-semibold ml-auto ${isPnlPos ? 'text-emerald-400' : 'text-red-400'}`}>
        {isPnlPos ? '+' : ''}₹{fmt(trade.pnl, 0)}
      </span>
      <span className="text-[10px] text-zinc-500">{trade.exit_reason}</span>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function RoboDashboard({ selectedStock }) {
  // Settings state
  const [settings, setSettings] = useState({
    daily_profit_target: 1000,
    allocated_capital: 100000,
    ticker: 'RELIANCE.NS',
    risk_tolerance: 'moderate',
  });
  const [editSettings, setEditSettings] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [recalcLoading, setRecalcLoading] = useState(false);
  const [capitalState, setCapitalState] = useState(null);

  // Robo state
  const [roboState, setRoboState] = useState(null);
  const [audit, setAudit] = useState([]);
  const [auditMeta, setAuditMeta] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  // Phase 3 state
  const [execMode, setExecMode]         = useState('paper');   // paper | live | shadow
  const [loopStatus, setLoopStatus]     = useState(null);
  const [openPositions, setOpenPositions] = useState([]);
  const [orderHistory, setOrderHistory] = useState([]);
  const [orderStats, setOrderStats]     = useState({});
  const [modeLoading, setModeLoading]   = useState(false);
  const [intervalMin, setIntervalMin]   = useState(5);
  const [modeWarning, setModeWarning]   = useState(null);
  const [showLiveWarn, setShowLiveWarn] = useState(false);

  // Hybrid Brain state
  const [brainState, setBrainState]       = useState(null);
  const [brainDecision, setBrainDecision] = useState(null);
  const [brainAudit, setBrainAudit]       = useState([]);
  const [brainLoading, setBrainLoading]   = useState(false);
  const [brainTab, setBrainTab]           = useState('state'); // 'state' | 'audit'

  // Sync ticker from parent
  useEffect(() => {
    if (selectedStock?.ticker && selectedStock.type !== 'CRYPTO' && selectedStock.type !== 'OPTION') {
      setSettings(p => ({ ...p, ticker: selectedStock.ticker }));
    }
  }, [selectedStock]);

  // Fetch full state
  const fetchState = useCallback(async () => {
    try {
      const [stRes, auditRes, loopRes, posRes, ordRes, brainStateRes, brainAuditRes] = await Promise.all([
        axios.get(`${API}/robo/status`),
        axios.get(`${API}/robo/audit?limit=20`),
        axios.get(`${API}/robo/loop-status`).catch(() => ({ data: {} })),
        axios.get(`${API}/robo/positions`).catch(() => ({ data: {} })),
        axios.get(`${API}/robo/orders?limit=30`).catch(() => ({ data: {} })),
        axios.get(`${API}/hybrid-brain/state`).catch(() => ({ data: null })),
        axios.get(`${API}/hybrid-brain/audit?limit=15`).catch(() => ({ data: { decisions: [] } })),
      ]);
      setRoboState(stRes.data);
      setAudit(auditRes.data.trades || []);
      if (brainStateRes.data) setBrainState(brainStateRes.data);
      if (brainAuditRes.data?.decisions) setBrainAudit(brainAuditRes.data.decisions);
      // Also pick up brain decisions from combined audit endpoint
      if (auditRes.data?.brain_decisions?.length) setBrainAudit(auditRes.data.brain_decisions);
      setAuditMeta({
        total_pnl: auditRes.data.total_pnl,
        win_count: auditRes.data.win_count,
        loss_count: auditRes.data.loss_count,
        win_rate: auditRes.data.win_rate,
      });
      // Sync settings from robo state
      if (stRes.data) {
        setSettings({
          daily_profit_target: stRes.data.daily_profit_target || 1000,
          allocated_capital:   stRes.data.allocated_capital   || 100000,
          ticker:              stRes.data.ticker              || 'RELIANCE.NS',
          risk_tolerance:      stRes.data.risk_tolerance      || 'moderate',
        });
        if (stRes.data.capital_state_vector) setCapitalState(stRes.data.capital_state_vector);
        if (stRes.data.mode) setExecMode(stRes.data.mode);
      }
      // Phase 3: loop + positions + orders
      if (loopRes.data?.loop) setLoopStatus(loopRes.data.loop);
      if (posRes.data?.open_positions) {
        setOpenPositions(posRes.data.open_positions || []);
        if (posRes.data.mode) setExecMode(posRes.data.mode);
      }
      if (ordRes.data?.orders) {
        setOrderHistory(ordRes.data.orders || []);
        setOrderStats({
          daily_pnl:    ordRes.data.daily_pnl,
          daily_net_pnl: ordRes.data.daily_net_pnl,
          wins:          ordRes.data.wins,
          losses:        ordRes.data.losses,
          win_rate:      ordRes.data.win_rate,
          brokerage:     ordRes.data.daily_brokerage,
        });
      }
    } catch (e) {
      /* silent */
    }
  }, []);

  // Polling
  useEffect(() => {
    fetchState();
    pollRef.current = setInterval(fetchState, 3000);
    return () => clearInterval(pollRef.current);
  }, [fetchState]);

  // ─── Handlers ──────────────────────────────────────────────────────────────

  const handleOpenSettings = () => {
    setEditSettings({ ...settings });
    setPreview(null);
    setSettingsOpen(true);
  };

  const handlePreview = async () => {
    if (!editSettings) return;
    setPreviewLoading(true);
    try {
      const res = await axios.post(`${API}/robo/risk-preview`, {
        daily_profit_target: Number(editSettings.daily_profit_target),
        allocated_capital:   Number(editSettings.allocated_capital),
        risk_tolerance:      editSettings.risk_tolerance,
      });
      setPreview(res.data.preview);
    } catch (e) {
      setError('Preview failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleSaveSettings = async () => {
    if (!editSettings) return;
    setLoading(true);
    try {
      await axios.post(`${API}/robo/settings`, {
        daily_profit_target: Number(editSettings.daily_profit_target),
        allocated_capital:   Number(editSettings.allocated_capital),
        ticker:              editSettings.ticker,
        risk_tolerance:      editSettings.risk_tolerance,
      });
      setSettingsOpen(false);
      await fetchState();
    } catch (e) {
      setError('Save failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const handleRecalculate = async () => {
    setRecalcLoading(true);
    try {
      await axios.post(`${API}/robo/recalculate`, { trigger: 'manual' });
      await fetchState();
    } catch (e) {
      setError('Recalculate failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setRecalcLoading(false);
    }
  };

  const handleToggleAuto = async () => {
    const isActive = roboState?.auto_mode;
    setLoading(true);
    try {
      if (isActive) {
        await axios.post(`${API}/robo/stop`);
      } else {
        await axios.post(`${API}/robo/start`, {
          ticker: settings.ticker,
          interval_minutes: intervalMin,
        });
      }
      await fetchState();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleModeChange = async (newMode) => {
    if (newMode === 'live' && execMode !== 'live') {
      setShowLiveWarn(true);
      return;
    }
    setModeLoading(true);
    setModeWarning(null);
    try {
      const res = await axios.post(`${API}/robo/mode`, { mode: newMode });
      if (res.data.success) {
        setExecMode(newMode);
        setModeWarning(res.data.disclaimer);
      } else {
        setError(res.data.error || 'Mode switch failed');
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setModeLoading(false);
    }
  };

  const handleConfirmLiveMode = async () => {
    setShowLiveWarn(false);
    setModeLoading(true);
    try {
      const res = await axios.post(`${API}/robo/mode`, { mode: 'live' });
      if (res.data.success) {
        setExecMode('live');
        setModeWarning(res.data.disclaimer);
      } else {
        setError(res.data.error || 'Live mode switch failed');
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setModeLoading(false);
    }
  };

  const handleSetInterval = async (mins) => {
    setIntervalMin(mins);
    if (roboState?.auto_mode) {
      try {
        await axios.post(`${API}/robo/set-interval`, { interval_minutes: mins });
      } catch (e) { /* silent */ }
    }
  };

  const handleCloseAll = async () => {
    if (!window.confirm('Close ALL open positions now? This cannot be undone.')) return;
    try {
      const res = await axios.post(`${API}/robo/close-all`);
      if (res.data.success) await fetchState();
      else setError(res.data.error || 'Close-all failed');
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  const handleResetDaily = async () => {
    try {
      await axios.post(`${API}/robo/reset-daily`);
      await fetchState();
    } catch (e) { /* silent */ }
  };

  const handleBrainDecide = async () => {
    setBrainLoading(true);
    try {
      const symbol = (settings.ticker || 'NIFTY').replace('.NS', '').replace('.BO', '');
      const res = await axios.post(`${API}/hybrid-brain/decide`, { symbol });
      setBrainDecision(res.data);
      // Refresh audit list
      const auditRes = await axios.get(`${API}/hybrid-brain/audit?limit=15`);
      if (auditRes.data?.decisions) setBrainAudit(auditRes.data.decisions);
    } catch (e) {
      setError('Brain decide failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setBrainLoading(false);
    }
  };

  const handleBrainResetDay = async () => {
    try {
      await axios.post(`${API}/hybrid-brain/reset-daily`);
      const res = await axios.get(`${API}/hybrid-brain/state`);
      if (res.data) setBrainState(res.data);
    } catch (e) { /* silent */ }
  };

  // ─── Derived state ─────────────────────────────────────────────────────────

  const rs     = roboState;
  const rp     = rs?.risk_profile || {};
  const dec    = rs?.current_decision;
  const status = rs?.status || 'idle';
  const statusCfg = STATUS_MAP[status] || STATUS_MAP.idle;
  const isActive = rs?.auto_mode;
  const dailyPnl = rs?.daily_pnl || 0;
  const openTrade = rs?.open_trade;

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#0d0d0f] text-white font-sans">
      {/* ── Header ── */}
      <div className="sticky top-0 z-20 bg-[#0d0d0f]/95 backdrop-blur border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center justify-between max-w-6xl mx-auto">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-indigo-600 flex items-center justify-center text-sm font-bold">
              🤖
            </div>
            <div>
              <h1 className="text-base font-bold text-white">Dreamer V3 Robo-Trader</h1>
              <p className="text-[10px] text-zinc-500">Institutional-Grade Autonomous System · PAPER MODE</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Status badge */}
            <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-semibold ${statusCfg.pulse ? 'animate-pulse' : ''}`}
              style={{ borderColor: statusCfg.color + '40', background: statusCfg.color + '15', color: statusCfg.color }}>
              <div className="w-1.5 h-1.5 rounded-full" style={{ background: statusCfg.color }} />
              {statusCfg.label}
            </div>
            <button
              onClick={handleOpenSettings}
              className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-xs text-zinc-300 transition-colors"
            >
              ⚙ Settings
            </button>
          </div>
        </div>
      </div>

      {/* ── Disclaimer ── */}
      <div className="border-b border-amber-700/30 px-4 py-1.5" style={{
        background: execMode === 'live'
          ? 'rgba(239,68,68,0.08)'
          : 'rgba(120,53,15,0.15)',
      }}>
        <p className="text-[10px] text-center max-w-4xl mx-auto" style={{
          color: execMode === 'live' ? '#ef4444' : '#d97706',
        }}>
          {execMode === 'live'
            ? '🔴 LIVE MODE ACTIVE — REAL ORDERS ON GROWW. Capital at risk. Circuit breakers active. No guaranteed returns.'
            : '⚠️ PAPER TRADING — No real capital at risk. No guaranteed returns. Past performance ≠ future results. Consult a SEBI-registered advisor.'}
        </p>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-4 space-y-4">
        {/* ── Error ── */}
        {error && (
          <div className="bg-red-900/30 border border-red-700/50 rounded-xl px-4 py-3 flex items-center justify-between">
            <span className="text-red-400 text-sm">{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-200 text-lg ml-4">×</button>
          </div>
        )}

        {/* ── Circuit Breaker Alert ── */}
        {rs?.circuit_breaker && (
          <div className="bg-red-900/40 border border-red-600/60 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-red-400 text-lg">🔴</span>
              <h3 className="text-red-300 font-bold">Circuit Breaker Tripped</h3>
            </div>
            <p className="text-red-400 text-sm">{rs.circuit_reason}</p>
            <p className="text-red-500 text-xs mt-1">Auto mode paused to protect capital. Review and reset to resume.</p>
          </div>
        )}

        {/* ── Top Row: Target Settings + Feasibility ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Settings Summary Card */}
          <div className="lg:col-span-2 bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <div className="flex items-start justify-between mb-4">
              <div>
                <h2 className="text-sm font-bold text-white">Trading Parameters</h2>
                <p className="text-[10px] text-zinc-500 mt-0.5">Editable at any time · system recalculates instantly</p>
              </div>
              <button
                onClick={handleOpenSettings}
                className="px-3 py-1.5 bg-violet-600/20 hover:bg-violet-600/30 border border-violet-500/30 text-violet-300 text-xs rounded-lg transition-colors"
              >
                Edit Settings
              </button>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard
                label="Daily Target"
                value={fmtInr(rs?.daily_profit_target || settings.daily_profit_target)}
                color="#10b981"
                icon="🎯"
              />
              <StatCard
                label="Allocated Capital"
                value={fmtInr(rs?.allocated_capital || settings.allocated_capital)}
                color="#3b82f6"
                icon="💰"
              />
              <StatCard
                label="Required Daily Return"
                value={fmtPct(rp.required_daily_return_pct)}
                color={rp.feasibility_color || '#f59e0b'}
                icon="📈"
              />
              <StatCard
                label="Risk / Trade"
                value={fmtPct(rp.risk_per_trade_pct, 1)}
                color="#a78bfa"
                sub={`≤ ${rp.max_trades_per_day} trades/day`}
                icon="⚖️"
              />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
              <StatCard
                label="Position Size"
                value={fmtInr(rp.position_size_inr)}
                color="#f59e0b"
                icon="📊"
              />
              <StatCard
                label="Max Daily Loss"
                value={fmtInr(rp.max_daily_loss_inr)}
                color="#ef4444"
                sub="Circuit breaker level"
                icon="🛡️"
              />
              <StatCard
                label="VaR 1-Day 95%"
                value={fmtInr(rp.var_1day_95)}
                color="#f97316"
                icon="📉"
              />
              <StatCard
                label="Min Win-Rate Needed"
                value={fmtPct(rp.min_winrate_needed, 0)}
                color="#06b6d4"
                sub={`R:R = 1:${rp.recommended_rr}`}
                icon="🏆"
              />
            </div>
          </div>

          {/* Feasibility Gauge */}
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4 flex flex-col items-center justify-between">
            <h2 className="text-sm font-bold text-white self-start">Feasibility Score</h2>
            <FeasibilityGauge
              score={rp.feasibility_score || 0}
              label={rp.feasibility_label || 'Not computed'}
              color={rp.feasibility_color || '#6b7280'}
            />
            <div className="w-full mt-2 space-y-1 text-[10px]">
              {[
                ['< 0.2%/day', '#10b981', 'Easily Achievable'],
                ['0.2–0.5%',   '#84cc16', 'Achievable'],
                ['0.5–1%',     '#f59e0b', 'Moderate'],
                ['1–2%',       '#f97316', 'Aggressive'],
                ['> 2%',       '#ef4444', 'Unrealistic'],
              ].map(([range, color, lbl]) => (
                <div key={lbl} className="flex items-center gap-1.5">
                  <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
                  <span className="text-zinc-500">{range}</span>
                  <span className="ml-auto font-medium" style={{ color }}>{lbl}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Daily Progress ── */}
        <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-bold text-white">Daily Progress</h2>
            <div className="flex items-center gap-3">
              <span className={`text-lg font-bold ${dailyPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {dailyPnl >= 0 ? '+' : ''}{fmtInr(dailyPnl)}
              </span>
              <span className="text-zinc-500 text-sm">of {fmtInr(rs?.daily_profit_target)}</span>
            </div>
          </div>
          <ProgressBar
            current={dailyPnl}
            target={rs?.daily_profit_target || 1}
          />
          <div className="grid grid-cols-4 gap-3 mt-3">
            <StatCard label="Trades Today" value={rs?.daily_trades || 0} color="#a1a1aa" icon="🔄" />
            <StatCard label="Win / Loss" value={`${rs?.win_trades || 0} / ${rs?.loss_trades || 0}`} color="#a1a1aa" icon="📋" />
            <StatCard label="Consec. Losses" value={rs?.consecutive_losses || 0}
              color={(rs?.consecutive_losses || 0) >= 3 ? '#ef4444' : '#a1a1aa'} icon="⚠️" />
            <StatCard label="Capital" value={fmtInr(rs?.current_capital)} color="#3b82f6"
              sub={`Peak: ${fmtInr(rs?.peak_capital)}`} icon="💎" />
          </div>
        </div>

        {/* ── Phase 2: VaR / CVaR + Kelly + Dynamic Budget ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* VaR / CVaR */}
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white">VaR / CVaR Analysis</h2>
              <span className="text-[10px] text-zinc-500">Parametric Normal</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: 'VaR 95%',  value: fmtInr(rp.var_95_inr),  sub: fmtPct(rp.var_95_pct_of_capital) + ' of capital', color: '#f59e0b' },
                { label: 'VaR 99%',  value: fmtInr(rp.var_99_inr),  sub: fmtPct(rp.var_99_pct_of_capital) + ' of capital', color: '#f97316' },
                { label: 'CVaR 95%', value: fmtInr(rp.cvar_95_inr), sub: 'Expected shortfall',                              color: '#ef4444' },
                { label: 'CVaR 99%', value: fmtInr(rp.cvar_99_inr), sub: 'Tail risk',                                       color: '#dc2626' },
              ].map(({ label, value, sub, color }) => (
                <div key={label} className="bg-zinc-800/60 rounded-lg p-2 text-center">
                  <p className="text-[10px] text-zinc-500 mb-0.5">{label}</p>
                  <p className="text-sm font-bold" style={{ color }}>{value}</p>
                  <p className="text-[9px] text-zinc-600">{sub}</p>
                </div>
              ))}
            </div>
            <div className="mt-3 text-[10px] text-zinc-500 space-y-1">
              <p>VaR = max 1-day loss at confidence level</p>
              <p>CVaR = expected loss <em>given</em> VaR is breached</p>
            </div>
          </div>

          {/* Kelly + Volatility Regime */}
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <h2 className="text-sm font-bold text-white mb-3">Kelly Position Sizing</h2>
            <div className="space-y-2">
              {[
                { label: 'Kelly Fraction',   value: fmtPct(rp.kelly_fraction * 100, 3),  color: '#a78bfa' },
                { label: 'Kelly Position',   value: fmtInr(rp.kelly_position_inr),        color: '#8b5cf6' },
                { label: 'ATR Position',     value: fmtInr(rp.atr_position_inr || rp.position_size_inr), color: '#3b82f6' },
                { label: 'Final (min)',       value: fmtInr(rp.position_size_inr),         color: '#10b981' },
              ].map(({ label, value, color }) => (
                <div key={label} className="flex items-center justify-between py-1 border-b border-zinc-800">
                  <span className="text-[11px] text-zinc-400">{label}</span>
                  <span className="text-[11px] font-bold" style={{ color }}>{value}</span>
                </div>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <div
                className="flex-1 py-1.5 rounded-lg text-[10px] font-bold text-center"
                style={{
                  background: rp.vol_regime === 'HIGH' ? '#ef444420' : rp.vol_regime === 'LOW' ? '#10b98120' : '#3b82f620',
                  color: rp.vol_regime === 'HIGH' ? '#ef4444' : rp.vol_regime === 'LOW' ? '#10b981' : '#3b82f6',
                  border: `1px solid ${rp.vol_regime === 'HIGH' ? '#ef444440' : rp.vol_regime === 'LOW' ? '#10b98140' : '#3b82f640'}`,
                }}
              >
                {rp.vol_regime || '—'} VOL REGIME
              </div>
              <div className="px-2 py-1.5 bg-zinc-800 rounded-lg text-[10px] text-zinc-400">
                ×{rp.vol_regime_mult || 1}
              </div>
            </div>
            <p className="text-[10px] text-zinc-600 mt-2">
              Final = min(Kelly, ATR) × vol-regime mult. Conservative bias enforced.
            </p>
          </div>

          {/* Dynamic Budget + Portfolio Heat */}
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <h2 className="text-sm font-bold text-white mb-3">Dynamic Risk Budget</h2>
            {/* Budget state badge */}
            <div className="flex items-center gap-2 mb-3">
              <div
                className="px-3 py-1 rounded-full text-xs font-bold"
                style={{
                  background: rp.risk_budget_state === 'STOP' ? '#ef444420'
                    : rp.risk_budget_state === 'REDUCED' ? '#f59e0b20'
                    : rp.risk_budget_state === 'CAUTIOUS' ? '#f97316'+'20'
                    : '#10b98120',
                  color: rp.risk_budget_state === 'STOP' ? '#ef4444'
                    : rp.risk_budget_state === 'REDUCED' ? '#f59e0b'
                    : rp.risk_budget_state === 'CAUTIOUS' ? '#f97316'
                    : '#10b981',
                }}
              >
                {rp.risk_budget_state || 'NORMAL'}
              </div>
              <span className="text-[11px] text-zinc-500">
                ×{rp.risk_budget_multiplier || 1} multiplier
              </span>
            </div>
            <div className="space-y-2">
              {[
                { label: 'Remaining Budget', value: fmtInr(rp.risk_budget_remaining), color: '#10b981' },
                { label: 'Max Daily Loss',   value: fmtInr(rp.daily_loss_limit),       color: '#ef4444' },
                { label: 'Portfolio Heat',   value: fmtPct(rp.portfolio_heat_pct, 2),  color: rp.heat_exceeded ? '#ef4444' : '#a1a1aa' },
                { label: 'Heat Limit',       value: fmtPct(rp.max_portfolio_heat_pct, 0), color: '#6b7280' },
              ].map(({ label, value, color }) => (
                <div key={label} className="flex items-center justify-between py-1 border-b border-zinc-800">
                  <span className="text-[11px] text-zinc-400">{label}</span>
                  <span className="text-[11px] font-bold" style={{ color }}>{value}</span>
                </div>
              ))}
            </div>
            {rp.heat_exceeded && (
              <div className="mt-2 text-[10px] text-red-400 bg-red-900/20 rounded px-2 py-1">
                🔴 Portfolio heat exceeded — no new trades until positions close
              </div>
            )}
            <button
              onClick={handleRecalculate}
              disabled={recalcLoading}
              className="w-full mt-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/30 border border-blue-500/30 text-blue-300 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50"
            >
              {recalcLoading ? '⟳ Recalculating…' : '↺ Live Recalculate'}
            </button>
          </div>
        </div>

        {/* ── Phase 2: Feasibility Warnings + Historical Context ── */}
        {(rp.feasibility_warnings?.length > 0 || rp.hist_exceedance_pct != null) && (
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white">Feasibility Analysis — NSE Historical Context</h2>
              <span className="text-[10px] text-zinc-500">
                {rp.nse_median_comparison || ''}
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-3">
              <div className="bg-zinc-800/60 rounded-xl p-3 text-center">
                <p className="text-[10px] text-zinc-500 mb-1">% of NSE days that exceed target</p>
                <p className="text-xl font-black" style={{ color: rp.feasibility_color || '#f59e0b' }}>
                  {rp.hist_exceedance_pct != null ? `${rp.hist_exceedance_pct}%` : '—'}
                </p>
                <p className="text-[10px] text-zinc-600 mt-0.5">historical frequency</p>
              </div>
              <div className="bg-zinc-800/60 rounded-xl p-3 text-center">
                <p className="text-[10px] text-zinc-500 mb-1">Min win-rate to break even</p>
                <p className="text-xl font-black text-blue-400">
                  {rp.required_win_rate_min != null ? `${rp.required_win_rate_min}%` : '—'}
                </p>
                <p className="text-[10px] text-zinc-600 mt-0.5">at 1:1.5 R:R ratio</p>
              </div>
              <div className="bg-zinc-800/60 rounded-xl p-3 text-center">
                <p className="text-[10px] text-zinc-500 mb-1">Feasibility score</p>
                <p className="text-xl font-black" style={{ color: rp.feasibility_color || '#f59e0b' }}>
                  {rp.feasibility_score ?? '—'} / 100
                </p>
                <p className="text-[10px]" style={{ color: rp.feasibility_color }}>{rp.feasibility_label}</p>
              </div>
            </div>
            {/* Suggestion */}
            {rp.feasibility_suggestion && (
              <p className="text-xs text-zinc-400 bg-zinc-800/40 rounded-lg px-3 py-2 mb-2">
                💡 {rp.feasibility_suggestion}
              </p>
            )}
            {/* Warnings */}
            {rp.feasibility_warnings?.length > 0 && (
              <div className="space-y-1">
                {rp.feasibility_warnings.map((w, i) => (
                  <div key={i} className="text-xs text-amber-300 bg-amber-900/20 border border-amber-700/30 rounded-lg px-3 py-1.5">
                    {w}
                  </div>
                ))}
              </div>
            )}
            {/* Alternative targets */}
            {rp.alternative_targets && Object.keys(rp.alternative_targets).length > 0 && (
              <div className="mt-3">
                <p className="text-[10px] text-zinc-500 mb-1">Realistic alternatives:</p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(rp.alternative_targets).slice(0, 3).map(([label, val]) => (
                    <span key={label} className="text-[10px] px-2 py-1 bg-zinc-800 rounded text-zinc-400">
                      {label.split('(')[0].trim()}: <strong className="text-white">₹{fmt(val, 0)}</strong>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Phase 2: DreamerV3 Capital State Vector ── */}
        {capitalState && (
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white">DreamerV3 Capital State Vector</h2>
              <span className="text-[10px] text-zinc-500">Normalised inputs to world model</span>
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {Object.entries(capitalState).map(([key, val]) => {
                const pct = Math.abs(val) * 100;
                const isNeg = val < 0;
                const label = key.replace(/_/g, ' ').replace('normalised', 'norm').replace('fraction', 'frac');
                return (
                  <div key={key} className="bg-zinc-800/60 rounded-lg p-2">
                    <p className="text-[9px] text-zinc-500 mb-1 leading-tight">{label}</p>
                    <div className="h-1.5 bg-zinc-700 rounded-full overflow-hidden mb-1">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.min(pct, 100)}%`,
                          background: isNeg ? '#ef4444' : '#8b5cf6',
                        }}
                      />
                    </div>
                    <p className="text-[10px] font-bold text-center" style={{ color: isNeg ? '#ef4444' : '#a78bfa' }}>
                      {Number(val).toFixed(3)}
                    </p>
                  </div>
                );
              })}
            </div>
            <p className="text-[10px] text-zinc-600 mt-2">
              These 6 values are appended to the DreamerV3 observation vector every step,
              teaching the world model to optimize for your specific capital and target constraints.
            </p>
          </div>
        )}

        {/* ── PHASE 3: Execution Mode Control ── */}
        <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-bold text-white">Execution Mode</h2>
              <p className="text-[10px] text-zinc-500 mt-0.5">Controls how orders are placed</p>
            </div>
            {/* Mode badge */}
            <div
              className="px-3 py-1 rounded-full text-xs font-bold tracking-wide"
              style={{
                background: execMode === 'live' ? '#ef444420' : execMode === 'shadow' ? '#6366f120' : '#10b98120',
                color:      execMode === 'live' ? '#ef4444'   : execMode === 'shadow' ? '#818cf8'   : '#10b981',
                border: `1px solid ${execMode === 'live' ? '#ef444440' : execMode === 'shadow' ? '#6366f140' : '#10b98140'}`,
              }}
              data-testid="execution-mode-badge"
            >
              {execMode === 'live' ? '🔴 LIVE' : execMode === 'shadow' ? '👁 SHADOW' : '📄 PAPER'}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2 mb-3">
            {[
              { key: 'paper',  label: 'Paper',  icon: '📄', desc: 'Simulate trades in memory. No real orders. Safe default.' },
              { key: 'shadow', label: 'Shadow', icon: '👁',  desc: 'Observe-only mode. Logs decisions. Zero execution.' },
              { key: 'live',   label: 'Live',   icon: '🔴', desc: 'REAL orders on Groww. Requires API keys in .env.' },
            ].map(({ key, label, icon, desc }) => (
              <button
                key={key}
                onClick={() => handleModeChange(key)}
                disabled={modeLoading}
                data-testid={`mode-btn-${key}`}
                title={desc}
                className={`py-2 rounded-xl text-xs font-semibold transition-all border ${
                  execMode === key
                    ? key === 'live'
                      ? 'bg-red-600/30 border-red-500/60 text-red-300'
                      : key === 'shadow'
                      ? 'bg-indigo-600/30 border-indigo-500/60 text-indigo-300'
                      : 'bg-emerald-600/30 border-emerald-500/60 text-emerald-300'
                    : 'bg-zinc-800 border-zinc-700 text-zinc-400 hover:border-zinc-600'
                }`}
              >
                {icon} {label}
              </button>
            ))}
          </div>
          {/* Mode info */}
          <div className="text-[10px] space-y-1">
            {execMode === 'paper' && (
              <div className="bg-emerald-900/20 border border-emerald-700/30 rounded-lg px-3 py-2 text-emerald-400">
                Paper Mode: Simulates trades in memory. P&amp;L tracked with realistic brokerage costs. No Groww API needed.
              </div>
            )}
            {execMode === 'shadow' && (
              <div className="bg-indigo-900/20 border border-indigo-700/30 rounded-lg px-3 py-2 text-indigo-300">
                Shadow Mode: DreamerV3 observes market and logs what it would trade — but never executes. Pure monitoring.
              </div>
            )}
            {execMode === 'live' && (
              <div className="bg-red-900/30 border border-red-600/50 rounded-lg px-3 py-2 text-red-400">
                🔴 LIVE MODE ACTIVE — Real orders placed on Groww with 30s confirmation delay. GROWW_API_KEY required.
                30% position size reduction applied as safety margin.
              </div>
            )}
            {modeWarning && (
              <p className="text-zinc-500 mt-1 px-1">{modeWarning}</p>
            )}
          </div>
          {/* Scan interval */}
          <div className="mt-3 flex items-center gap-2">
            <span className="text-[10px] text-zinc-500 min-w-fit">Scan Interval:</span>
            <div className="flex gap-1">
              {[1, 5, 10, 15, 30].map(m => (
                <button
                  key={m}
                  onClick={() => handleSetInterval(m)}
                  data-testid={`interval-btn-${m}`}
                  className={`px-2 py-1 rounded text-[10px] font-semibold border transition-colors ${
                    intervalMin === m
                      ? 'bg-violet-600/30 border-violet-500/50 text-violet-300'
                      : 'bg-zinc-800 border-zinc-700 text-zinc-500 hover:border-zinc-600'
                  }`}
                >
                  {m}m
                </button>
              ))}
            </div>
            {loopStatus?.running && (
              <span className="ml-auto text-[10px] text-emerald-400 animate-pulse">● Loop active</span>
            )}
          </div>
        </div>

        {/* ── PHASE 3: Loop Status + Execution Stats ── */}
        {loopStatus && (
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white">Trading Loop Status</h2>
              <div className="flex items-center gap-2">
                <div
                  className={`w-2 h-2 rounded-full ${loopStatus.running ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-600'}`}
                />
                <span className="text-[10px] text-zinc-400">
                  {loopStatus.running ? 'Running' : 'Stopped'} · {loopStatus.interval_minutes}min interval
                </span>
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
              <StatCard
                label="Cycles Run"
                value={loopStatus.cycle_count || 0}
                color="#a78bfa"
                icon="🔄"
              />
              <StatCard
                label="Last Cycle"
                value={loopStatus.last_cycle_time
                  ? new Date(loopStatus.last_cycle_time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
                  : '—'}
                color="#a1a1aa"
                sub={loopStatus.last_cycle_status || ''}
                icon="⏱"
              />
              <StatCard
                label="Next Cycle"
                value={loopStatus.next_cycle_time
                  ? new Date(loopStatus.next_cycle_time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
                  : '—'}
                color={loopStatus.running ? '#10b981' : '#6b7280'}
                icon="⏰"
              />
              <StatCard
                label="Market Open"
                value={loopStatus.market_open ? 'YES' : 'NO'}
                color={loopStatus.market_open ? '#10b981' : '#ef4444'}
                sub="NSE 09:15–15:30 IST"
                icon="🏦"
              />
            </div>
            {/* Execution stats */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard
                label="Fills Today"
                value={orderStats.daily_pnl != null ? (orderHistory.length) : 0}
                color="#f59e0b"
                icon="✅"
              />
              <StatCard
                label="P&L Today"
                value={orderStats.daily_pnl != null ? `${(orderStats.daily_pnl||0)>=0?'+':''}₹${Math.abs(orderStats.daily_pnl||0).toFixed(0)}` : '—'}
                color={(orderStats.daily_pnl||0)>=0 ? '#10b981' : '#ef4444'}
                sub="gross"
                icon="💰"
              />
              <StatCard
                label="Net P&L"
                value={orderStats.daily_net_pnl != null ? `${(orderStats.daily_net_pnl||0)>=0?'+':''}₹${Math.abs(orderStats.daily_net_pnl||0).toFixed(0)}` : '—'}
                color={(orderStats.daily_net_pnl||0)>=0 ? '#10b981' : '#ef4444'}
                sub="after brokerage"
                icon="📊"
              />
              <StatCard
                label="Win Rate"
                value={orderStats.win_rate != null ? `${orderStats.win_rate}%` : '—'}
                color="#06b6d4"
                sub={`${orderStats.wins||0}W / ${orderStats.losses||0}L`}
                icon="🏆"
              />
            </div>
            {loopStatus.last_error && (
              <div className="mt-2 text-[10px] text-red-400 bg-red-900/20 rounded px-2 py-1">
                Last error: {loopStatus.last_error}
              </div>
            )}
          </div>
        )}

        {/* ── PHASE 3: Open Positions (Enhanced) ── */}
        {openPositions.length > 0 && (
          <div className="bg-zinc-900/80 border border-emerald-600/30 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-emerald-400 flex items-center gap-2">
                <span className="animate-pulse">●</span> Open Positions ({openPositions.length})
              </h2>
              <button
                onClick={handleCloseAll}
                className="px-3 py-1 rounded-lg bg-red-600/20 hover:bg-red-600/30 border border-red-500/40 text-red-300 text-xs font-semibold transition-colors"
                data-testid="close-all-btn"
              >
                Close All
              </button>
            </div>
            <div className="space-y-2">
              {openPositions.map((pos) => (
                <div
                  key={pos.order_id}
                  className="bg-zinc-800/50 border border-zinc-700/30 rounded-xl p-3"
                  data-testid={`position-row-${pos.order_id}`}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <span
                      className="text-[10px] font-bold px-2 py-0.5 rounded"
                      style={{
                        background: pos.direction === 'BUY' ? '#10b98133' : '#ef444433',
                        color:      pos.direction === 'BUY' ? '#10b981'   : '#ef4444',
                      }}
                    >
                      {pos.direction}
                    </span>
                    <span className="text-zinc-300 text-xs font-mono font-bold">{pos.ticker}</span>
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-700 text-zinc-400">{pos.mode?.toUpperCase()}</span>
                    {pos.status === 'PENDING' && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 animate-pulse">
                        PENDING CONFIRM
                      </span>
                    )}
                    <span className="ml-auto text-[10px] text-zinc-500">#{pos.order_id}</span>
                  </div>
                  <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 text-center">
                    {[
                      { label: 'Entry',    val: `₹${fmt(pos.entry_price, 0)}`,  color: '#f59e0b' },
                      { label: 'Qty',      val: pos.quantity,                     color: '#a1a1aa' },
                      { label: 'Value',    val: fmtInr(pos.position_value),       color: '#3b82f6' },
                      { label: 'SL',       val: `₹${fmt(pos.sl_price, 0)}`,      color: '#ef4444' },
                      { label: 'TP',       val: `₹${fmt(pos.tp_price, 0)}`,      color: '#10b981' },
                      { label: 'Conf',     val: `${pos.confidence}%`,            color: '#a78bfa' },
                    ].map(({ label, val, color }) => (
                      <div key={label} className="bg-zinc-900/60 rounded-lg p-1.5">
                        <p className="text-[9px] text-zinc-600">{label}</p>
                        <p className="text-[11px] font-bold" style={{ color }}>{val}</p>
                      </div>
                    ))}
                  </div>
                  {pos.strategy_meta?.source && (
                    <p className="text-[9px] text-zinc-600 mt-1 text-right">
                      Signal: {pos.strategy_meta.source} · Dreamer: {fmt(pos.dreamer_signal, 3)}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── PHASE 3: Order History (Engine orders, all modes) ── */}
        {orderHistory.length > 0 && (
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white">Execution Order History</h2>
              <div className="flex items-center gap-3 text-xs">
                <span className="text-emerald-400">{orderStats.wins||0}W</span>
                <span className="text-red-400">{orderStats.losses||0}L</span>
                <span className="text-zinc-400">{orderStats.win_rate||0}% WR</span>
                <span className={`font-bold ${(orderStats.daily_net_pnl||0)>=0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {(orderStats.daily_net_pnl||0)>=0?'+':''}₹{Math.abs(orderStats.daily_net_pnl||0).toFixed(0)} net
                </span>
              </div>
            </div>
            <div className="space-y-1 max-h-48 overflow-y-auto custom-scroll">
              {orderHistory.map((order, i) => {
                const isPnlPos = (order.pnl || 0) >= 0;
                return (
                  <div
                    key={order.order_id || i}
                    className={`flex items-center gap-2 py-1.5 px-3 rounded-lg border ${
                      i % 2 === 0 ? 'bg-zinc-800/30' : 'bg-zinc-800/10'
                    } border-zinc-700/20`}
                    data-testid={`order-row-${order.order_id}`}
                  >
                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded`}
                      style={{
                        background: order.direction === 'BUY' ? '#10b98133' : '#ef444433',
                        color:      order.direction === 'BUY' ? '#10b981' : '#ef4444',
                      }}>
                      {order.direction}
                    </span>
                    <span className="text-[9px] px-1 py-0.5 rounded bg-zinc-700 text-zinc-500">{order.mode}</span>
                    <span className="text-zinc-300 text-xs font-mono flex-1 truncate">{order.ticker}</span>
                    <span className="text-zinc-500 text-[10px]">@ ₹{fmt(order.entry_price,0)}</span>
                    <span className="text-zinc-500 text-[9px]">→ ₹{fmt(order.exit_price,0)}</span>
                    <span className={`text-xs font-bold ${isPnlPos ? 'text-emerald-400' : 'text-red-400'}`}>
                      {isPnlPos ? '+' : ''}₹{fmt(order.net_pnl || order.pnl, 0)}
                    </span>
                    <span className="text-[9px] text-zinc-600">{order.exit_reason}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Auto Mode Panel + DreamerV3 Decision ── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Auto Mode Control */}
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <h2 className="text-sm font-bold text-white mb-3">Auto Mode Control</h2>
            <div className="flex items-center gap-4 mb-4">
              <button
                onClick={handleToggleAuto}
                disabled={loading || rs?.circuit_breaker}
                data-testid="toggle-auto-btn"
                className={`flex-1 py-3 rounded-xl font-bold text-sm transition-all ${
                  isActive
                    ? 'bg-red-600/20 hover:bg-red-600/30 border border-red-500/40 text-red-300'
                    : rs?.circuit_breaker
                    ? 'bg-zinc-800 border border-zinc-700 text-zinc-600 cursor-not-allowed'
                    : 'bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/40 text-emerald-300'
                }`}
              >
                {loading ? '…' : isActive ? '⏹ Stop Auto Mode' : '▶ Start Auto Mode'}
              </button>
              <button
                onClick={handleResetDaily}
                className="px-3 py-3 rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-400 text-xs transition-colors"
                title="Reset daily counters"
              >
                🔄 Reset Day
              </button>
            </div>
            <div className="space-y-2 text-xs text-zinc-400">
              <div className="flex items-center gap-2">
                <span className="text-emerald-400">✓</span>
                {execMode === 'paper'  && 'Paper mode — no real orders placed'}
                {execMode === 'shadow' && 'Shadow mode — observe only, no orders'}
                {execMode === 'live'   && <span className="text-red-400">⚠ LIVE mode — real Groww orders</span>}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-emerald-400">✓</span>
                Scans every {intervalMin} min via APScheduler
              </div>
              <div className="flex items-center gap-2">
                <span className="text-emerald-400">✓</span>
                Circuit breaker at{' '}
                <span className="text-amber-400">{fmtInr(rp.max_daily_loss_inr)} loss</span> or 5% drawdown
              </div>
              <div className="flex items-center gap-2">
                <span className="text-emerald-400">✓</span>
                EOD forced close at 15:15 IST
              </div>
              <div className="flex items-center gap-2">
                <span className="text-blue-400">ℹ</span> Meta decision: DreamerV3 (60%) + Technical (40%)
              </div>
            </div>
            {dec?.dreamer_active === false && (
              <div className="mt-3 bg-amber-900/20 border border-amber-700/30 rounded-lg p-2 text-xs text-amber-400">
                ⚡ DreamerV3 not active. Go to <strong>RL Agent</strong> tab → Start Training first.
              </div>
            )}
          </div>

          {/* Current DreamerV3 Decision */}
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-white">DreamerV3 Decision</h2>
              {dec?.timestamp && (
                <span className="text-[10px] text-zinc-500">
                  {new Date(dec.timestamp).toLocaleTimeString('en-IN')}
                </span>
              )}
            </div>
            {dec ? (
              <div className="space-y-3">
                {/* Signal */}
                <div className="flex items-center gap-3">
                  <div
                    className="px-4 py-2 rounded-xl text-lg font-black"
                    style={{
                      background: (SIGNAL_COLORS[dec.signal] || SIGNAL_COLORS.HOLD).bg + '33',
                      color:      (SIGNAL_COLORS[dec.signal] || SIGNAL_COLORS.HOLD).bg,
                      border: `1px solid ${(SIGNAL_COLORS[dec.signal] || SIGNAL_COLORS.HOLD).bg}40`,
                    }}
                  >
                    {(SIGNAL_COLORS[dec.signal] || SIGNAL_COLORS.HOLD).icon} {dec.signal}
                  </div>
                  <div className="flex-1">
                    {/* Confidence bar */}
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] text-zinc-500">Confidence</span>
                      <span className="text-xs font-bold text-white">{dec.confidence}%</span>
                    </div>
                    <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${dec.confidence}%`,
                          background: (SIGNAL_COLORS[dec.signal] || SIGNAL_COLORS.HOLD).bg,
                        }}
                      />
                    </div>
                  </div>
                </div>
                {/* Price info */}
                {dec.entry_price > 0 && (
                  <div className="grid grid-cols-3 gap-2">
                    <div className="text-center">
                      <p className="text-[10px] text-zinc-500">Entry</p>
                      <p className="text-xs font-semibold text-white">₹{fmt(dec.entry_price, 0)}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-[10px] text-zinc-500">Stop Loss</p>
                      <p className="text-xs font-semibold text-red-400">₹{fmt(dec.sl_price, 0)}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-[10px] text-zinc-500">Target</p>
                      <p className="text-xs font-semibold text-emerald-400">₹{fmt(dec.tp_price, 0)}</p>
                    </div>
                  </div>
                )}
                {/* Market context */}
                <div className="grid grid-cols-3 gap-2">
                  <div className="bg-zinc-800/50 rounded-lg p-2 text-center">
                    <p className="text-[10px] text-zinc-500">Regime</p>
                    <p className="text-xs font-semibold" style={{
                      color: dec.regime === 'UPTREND' ? '#10b981' : dec.regime === 'DOWNTREND' ? '#ef4444' : '#f59e0b'
                    }}>{dec.regime || '—'}</p>
                  </div>
                  <div className="bg-zinc-800/50 rounded-lg p-2 text-center">
                    <p className="text-[10px] text-zinc-500">RSI</p>
                    <p className="text-xs font-semibold" style={{
                      color: (dec.rsi14 || 50) > 70 ? '#ef4444' : (dec.rsi14 || 50) < 30 ? '#10b981' : '#a1a1aa'
                    }}>{dec.rsi14 || '—'}</p>
                  </div>
                  <div className="bg-zinc-800/50 rounded-lg p-2 text-center">
                    <p className="text-[10px] text-zinc-500">Qty</p>
                    <p className="text-xs font-semibold text-white">{dec.quantity}</p>
                  </div>
                </div>
                {dec.message && (
                  <p className="text-[10px] text-zinc-500 mt-1 truncate">{dec.message}</p>
                )}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-32 text-zinc-600">
                <span className="text-3xl mb-2">🤖</span>
                <p className="text-xs">No decision yet</p>
                <p className="text-[10px]">Start auto mode to begin analysis</p>
              </div>
            )}
          </div>
        </div>

        {/* ── HYBRID SUPER BRAIN ── */}
        <div className="bg-zinc-900/80 border border-purple-700/40 rounded-2xl p-4">
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-purple-600 to-fuchsia-600 flex items-center justify-center text-xs font-black text-white">
                HSB
              </div>
              <div>
                <h2 className="text-sm font-bold text-white">Hybrid Super Brain</h2>
                <p className="text-[10px] text-zinc-500">Psychology + Survival Engine · DreamerV3 Fusion</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {/* Tab switcher */}
              <div className="flex rounded-lg overflow-hidden border border-zinc-700">
                {['state', 'audit'].map(t => (
                  <button key={t} onClick={() => setBrainTab(t)}
                    data-testid={`brain-tab-${t}`}
                    className={`px-3 py-1 text-[10px] font-semibold capitalize transition-colors ${
                      brainTab === t
                        ? 'bg-purple-700/50 text-purple-200'
                        : 'bg-zinc-800 text-zinc-500 hover:text-zinc-300'
                    }`}>{t === 'state' ? 'Brain State' : 'Decision Log'}</button>
                ))}
              </div>
              <button
                onClick={handleBrainDecide}
                disabled={brainLoading}
                data-testid="brain-decide-btn"
                className="px-3 py-1.5 rounded-lg bg-purple-600/20 hover:bg-purple-600/40 border border-purple-500/40 text-purple-300 text-xs font-bold transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {brainLoading ? '⟳ Thinking…' : '⚡ Fire Brain'}
              </button>
            </div>
          </div>

          {brainTab === 'state' && (
            <>
              {/* Brain State Metrics */}
              {brainState ? (
                <>
                  {/* Fear Level + Key Metrics */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                    {/* Fear Level */}
                    <div className={`rounded-xl p-3 text-center border ${
                      brainState.fear_level > 0.6 ? 'bg-red-900/20 border-red-600/40'
                      : brainState.fear_level > 0.3 ? 'bg-amber-900/20 border-amber-600/40'
                      : 'bg-emerald-900/20 border-emerald-600/40'
                    }`}>
                      <p className="text-[10px] text-zinc-500 mb-1">Fear Level</p>
                      <div className="relative w-12 h-12 mx-auto mb-1">
                        <svg viewBox="0 0 44 44" className="w-full h-full -rotate-90">
                          <circle cx="22" cy="22" r="18" fill="none" stroke="#374151" strokeWidth="5"/>
                          <circle cx="22" cy="22" r="18" fill="none"
                            stroke={brainState.fear_level > 0.6 ? '#ef4444' : brainState.fear_level > 0.3 ? '#f59e0b' : '#10b981'}
                            strokeWidth="5"
                            strokeLinecap="round"
                            strokeDasharray={`${brainState.fear_level * 113.1} 113.1`}
                          />
                        </svg>
                        <span className="absolute inset-0 flex items-center justify-center text-[10px] font-black"
                          style={{ color: brainState.fear_level > 0.6 ? '#ef4444' : brainState.fear_level > 0.3 ? '#f59e0b' : '#10b981' }}>
                          {(brainState.fear_level * 100).toFixed(0)}%
                        </span>
                      </div>
                      <p className="text-[9px] font-semibold" style={{ color: brainState.fear_level > 0.6 ? '#ef4444' : brainState.fear_level > 0.3 ? '#f59e0b' : '#10b981' }}>
                        {brainState.fear_level > 0.8 ? 'CIRCUIT BREAKER' : brainState.fear_level > 0.6 ? 'High Fear' : brainState.fear_level > 0.3 ? 'Cautious' : 'Calm'}
                      </p>
                    </div>
                    {/* Consecutive Fails */}
                    <div className={`rounded-xl p-3 text-center border ${
                      brainState.consecutive_fail >= 3 ? 'bg-red-900/20 border-red-600/40' : 'bg-zinc-800/60 border-zinc-700/40'
                    }`}>
                      <p className="text-[10px] text-zinc-500 mb-1">Consec. Misses</p>
                      <p className="text-2xl font-black" style={{ color: brainState.consecutive_fail >= 3 ? '#ef4444' : brainState.consecutive_fail >= 1 ? '#f59e0b' : '#10b981' }}>
                        {brainState.consecutive_fail}
                      </p>
                      <p className="text-[9px] text-zinc-500">/{brainState.config?.grace_days || 5} grace</p>
                    </div>
                    {/* Daily Target */}
                    <div className="bg-zinc-800/60 border border-zinc-700/40 rounded-xl p-3 text-center">
                      <p className="text-[10px] text-zinc-500 mb-1">Daily Target</p>
                      <p className="text-lg font-bold text-blue-400">{brainState.daily_target_pct?.toFixed(2)}%</p>
                      <p className="text-[9px] text-zinc-500">PnL: <span className={brainState.current_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{brainState.current_pnl_pct?.toFixed(2)}%</span></p>
                    </div>
                    {/* Last PnL */}
                    <div className={`rounded-xl p-3 text-center border ${
                      (brainState.last_pnl_pct || 0) >= (brainState.daily_target_pct || 0) ? 'bg-emerald-900/20 border-emerald-600/40' : 'bg-zinc-800/60 border-zinc-700/40'
                    }`}>
                      <p className="text-[10px] text-zinc-500 mb-1">Last PnL</p>
                      <p className="text-lg font-bold" style={{ color: (brainState.last_pnl_pct || 0) >= 0 ? '#10b981' : '#ef4444' }}>
                        {brainState.last_pnl_pct?.toFixed(3)}%
                      </p>
                      <p className="text-[9px] text-zinc-500">Grace days: {brainState.grace_days}</p>
                    </div>
                  </div>

                  {/* Latest Brain Decision */}
                  {brainDecision && (
                    <div className={`rounded-xl border p-4 mb-4 ${
                      brainDecision.action === 'BUY' ? 'bg-emerald-900/15 border-emerald-600/40'
                      : brainDecision.action === 'SELL' ? 'bg-red-900/15 border-red-600/40'
                      : 'bg-zinc-800/40 border-zinc-700/40'
                    }`}>
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-3">
                          <div className={`px-4 py-2 rounded-xl text-lg font-black ${
                            brainDecision.action === 'BUY' ? 'bg-emerald-500/20 text-emerald-400'
                            : brainDecision.action === 'SELL' ? 'bg-red-500/20 text-red-400'
                            : 'bg-zinc-700/50 text-zinc-300'
                          }`} data-testid="brain-action-badge">
                            {brainDecision.action === 'BUY' ? '▲' : brainDecision.action === 'SELL' ? '▼' : '●'} {brainDecision.action}
                          </div>
                          <div>
                            <p className="text-xs font-bold text-white">{brainDecision.symbol}</p>
                            <div className="flex items-center gap-1 mt-0.5">
                              <div className="h-1.5 w-24 bg-zinc-700 rounded-full overflow-hidden">
                                <div className="h-full rounded-full"
                                  style={{
                                    width: `${brainDecision.confidence}%`,
                                    background: brainDecision.confidence > 68 ? '#10b981' : brainDecision.confidence > 40 ? '#f59e0b' : '#ef4444',
                                  }}/>
                              </div>
                              <span className="text-[10px] text-zinc-400">{brainDecision.confidence?.toFixed(1)}% conf</span>
                            </div>
                          </div>
                        </div>
                        <div className="text-right">
                          <span className={`px-2 py-0.5 rounded text-[9px] font-bold ${
                            brainDecision.risk_alert === 'good' ? 'bg-emerald-900/30 text-emerald-400'
                            : brainDecision.risk_alert === 'warning' ? 'bg-amber-900/30 text-amber-400'
                            : 'bg-red-900/30 text-red-400'
                          }`}>{(brainDecision.risk_alert || '').toUpperCase()}</span>
                          <p className="text-[9px] text-zinc-600 mt-1">
                            {brainDecision.timestamp ? new Date(brainDecision.timestamp).toLocaleTimeString('en-IN') : ''}
                          </p>
                        </div>
                      </div>

                      {/* Psychology Breakdown */}
                      {brainDecision.psych && (
                        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-3">
                          {[
                            { label: 'FOMO', val: brainDecision.psych.fomo_score, color: brainDecision.psych.fomo_score > 0.6 ? '#f97316' : '#a1a1aa', fmt: v => (v*100).toFixed(0)+'%' },
                            { label: 'Apathy', val: brainDecision.psych.apathy_score, color: brainDecision.psych.apathy_score > 0.5 ? '#6b7280' : '#a1a1aa', fmt: v => (v*100).toFixed(0)+'%' },
                            { label: 'Cred', val: brainDecision.psych.narrative_credibility, color: brainDecision.psych.narrative_credibility > 0.5 ? '#10b981' : '#ef4444', fmt: v => (v*100).toFixed(0)+'%' },
                            { label: 'Momentum', val: brainDecision.psych.momentum, color: brainDecision.psych.momentum > 0.55 ? '#10b981' : brainDecision.psych.momentum < 0.4 ? '#ef4444' : '#f59e0b', fmt: v => (v*100).toFixed(0)+'%' },
                            { label: 'Vol Thrust', val: brainDecision.psych.volume_thrust, color: brainDecision.psych.volume_thrust > 1.2 ? '#10b981' : '#a1a1aa', fmt: v => v?.toFixed(2)+'x' },
                            { label: 'Volatility', val: brainDecision.psych.volatility, color: brainDecision.psych.volatility > 0.025 ? '#f97316' : '#a1a1aa', fmt: v => (v*100).toFixed(2)+'%' },
                          ].map(({ label, val, color, fmt: f }) => (
                            <div key={label} className="bg-zinc-900/60 rounded-lg p-2 text-center">
                              <p className="text-[9px] text-zinc-600 mb-0.5">{label}</p>
                              <p className="text-xs font-bold" style={{ color }}>{val != null ? f(val) : '—'}</p>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Components Breakdown */}
                      {brainDecision.components && (
                        <div className="flex flex-wrap gap-1.5 mb-3">
                          {Object.entries(brainDecision.components).map(([k, v]) => (
                            <span key={k} className={`text-[9px] px-2 py-0.5 rounded-full font-semibold ${
                              v > 0 ? 'bg-emerald-900/30 text-emerald-400' : v < 0 ? 'bg-red-900/30 text-red-400' : 'bg-zinc-800 text-zinc-500'
                            }`}>
                              {k.replace(/_/g,' ')}: {v > 0 ? '+' : ''}{Number(v).toFixed(2)}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Regime + Reasoning */}
                      <div className="flex items-start gap-2">
                        {brainDecision.psych?.regime && (
                          <span className="text-[9px] px-2 py-0.5 rounded bg-purple-900/40 text-purple-300 font-semibold shrink-0">
                            {brainDecision.psych.regime.toUpperCase().replace('_', ' ')}
                          </span>
                        )}
                        {brainDecision.psych?.hidden_value_gap && (
                          <p className="text-[10px] text-zinc-400 leading-relaxed">{brainDecision.psych.hidden_value_gap}</p>
                        )}
                      </div>
                      {brainDecision.reasoning && (
                        <p className="text-[9px] text-zinc-600 mt-2 font-mono border-t border-zinc-800 pt-2">
                          {brainDecision.reasoning}
                        </p>
                      )}
                      {brainDecision.cached && (
                        <p className="text-[9px] text-amber-600 mt-1">Cached decision (60s TTL)</p>
                      )}
                    </div>
                  )}

                  {/* Size Scalar + PnL Update */}
                  {brainDecision && (
                    <div className="flex items-center gap-3 flex-wrap">
                      <div className="bg-zinc-800/60 border border-zinc-700/40 rounded-lg px-3 py-2 text-center">
                        <p className="text-[9px] text-zinc-500">Size Scalar</p>
                        <p className="text-sm font-bold text-violet-400">×{brainDecision.size_scalar?.toFixed(3)}</p>
                      </div>
                      <button
                        onClick={handleBrainResetDay}
                        className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-400 text-xs transition-colors"
                        data-testid="brain-reset-day-btn"
                      >
                        Reset Brain Day
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <div className="flex flex-col items-center py-8 text-zinc-600">
                  <p className="text-xs">Loading brain state…</p>
                </div>
              )}
            </>
          )}

          {/* Audit Tab */}
          {brainTab === 'audit' && (
            <div>
              {brainAudit.length === 0 ? (
                <div className="text-center py-8 text-zinc-600">
                  <p className="text-xs">No brain decisions logged yet</p>
                  <p className="text-[10px] mt-1">Click "Fire Brain" to generate the first decision</p>
                </div>
              ) : (
                <div className="space-y-2 max-h-72 overflow-y-auto custom-scroll" data-testid="brain-audit-list">
                  {brainAudit.map((d, i) => (
                    <div key={d.id || i} className={`flex items-start gap-3 p-3 rounded-xl border ${
                      d.action === 'BUY' ? 'bg-emerald-900/10 border-emerald-800/40'
                      : d.action === 'SELL' ? 'bg-red-900/10 border-red-800/40'
                      : 'bg-zinc-800/30 border-zinc-700/30'
                    }`}>
                      <span className={`text-[10px] font-black px-2 py-0.5 rounded shrink-0 ${
                        d.action === 'BUY' ? 'bg-emerald-500/20 text-emerald-400'
                        : d.action === 'SELL' ? 'bg-red-500/20 text-red-400'
                        : 'bg-zinc-700 text-zinc-400'
                      }`}>{d.action}</span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[10px] text-zinc-300 font-semibold">{d.symbol}</span>
                          <span className="text-[9px] text-violet-400">{d.confidence?.toFixed(1)}% conf</span>
                          <span className={`text-[9px] px-1.5 py-0.5 rounded ${
                            d.risk_alert === 'good' ? 'text-emerald-400 bg-emerald-900/30'
                            : d.risk_alert === 'danger' ? 'text-red-400 bg-red-900/30'
                            : 'text-amber-400 bg-amber-900/30'
                          }`}>{(d.risk_alert||'').toUpperCase()}</span>
                          {d.psych?.regime && (
                            <span className="text-[9px] text-purple-400">{d.psych.regime}</span>
                          )}
                        </div>
                        {d.psych?.hidden_value_gap && (
                          <p className="text-[9px] text-zinc-500 mt-0.5 truncate">{d.psych.hidden_value_gap}</p>
                        )}
                      </div>
                      <span className="text-[9px] text-zinc-600 shrink-0">
                        {d.timestamp ? new Date(d.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : ''}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Open Position ── */}
        {openTrade && openTrade.status === 'OPEN' && (
          <div className="bg-zinc-900/80 border border-emerald-600/30 rounded-2xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-emerald-400 flex items-center gap-2">
                <span className="animate-pulse">●</span> Open Paper Position
              </h2>
              <span className="text-[10px] text-zinc-500">#{openTrade.trade_id}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
              {[
                { label: 'Direction',     val: openTrade.direction,                     color: openTrade.direction === 'BUY' ? '#10b981' : '#ef4444' },
                { label: 'Ticker',        val: openTrade.ticker,                        color: '#a1a1aa' },
                { label: 'Entry',         val: `₹${fmt(openTrade.entry_price, 0)}`,    color: '#f59e0b' },
                { label: 'Quantity',      val: openTrade.quantity,                      color: '#a1a1aa' },
                { label: 'Value',         val: fmtInr(openTrade.position_value),        color: '#3b82f6' },
                { label: 'SL',            val: `₹${fmt(openTrade.sl_price, 0)}`,       color: '#ef4444' },
                { label: 'TP',            val: `₹${fmt(openTrade.tp_price, 0)}`,       color: '#10b981' },
              ].map(({ label, val, color }) => (
                <div key={label} className="bg-zinc-800/60 rounded-lg p-2 text-center">
                  <p className="text-[10px] text-zinc-500">{label}</p>
                  <p className="text-xs font-bold mt-0.5" style={{ color }}>{val}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Strategy Weights ── */}
        {rs?.dreamer_weights && Object.keys(rs.dreamer_weights).length > 0 && (
          <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
            <h2 className="text-sm font-bold text-white mb-3">DreamerV3 Strategy Weights</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
              {Object.entries(rs.dreamer_weights)
                .sort(([, a], [, b]) => b - a)
                .map(([name, pct]) => (
                  <div key={name} className="bg-zinc-800/60 rounded-lg p-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px] text-zinc-400 truncate">{name}</span>
                      <span className="text-[10px] font-bold text-violet-400">{pct}%</span>
                    </div>
                    <div className="h-1.5 bg-zinc-700 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-500 transition-all duration-500"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* ── Audit Trail ── */}
        <div className="bg-zinc-900/80 border border-zinc-700/40 rounded-2xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-bold text-white">Unified Audit Log</h2>
              <p className="text-[10px] text-zinc-500">Paper Trades + Brain Decisions</p>
            </div>
            <div className="flex items-center gap-4 text-xs">
              {brainAudit.length > 0 && (
                <span className="text-purple-400">{brainAudit.length} brain</span>
              )}
              {auditMeta.win_count != null && (
                <>
                  <span className="text-emerald-400">{auditMeta.win_count} W</span>
                  <span className="text-red-400">{auditMeta.loss_count} L</span>
                  <span className="text-zinc-400">{auditMeta.win_rate}% WR</span>
                  <span className={`font-bold ${(auditMeta.total_pnl || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {(auditMeta.total_pnl || 0) >= 0 ? '+' : ''}₹{fmt(auditMeta.total_pnl, 0)}
                  </span>
                </>
              )}
            </div>
          </div>
          {audit.length === 0 && brainAudit.length === 0 ? (
            <div className="text-center py-8 text-zinc-600">
              <span className="text-3xl block mb-2">📋</span>
              <p className="text-sm">No trades or brain decisions yet</p>
              <p className="text-xs mt-1">Start auto mode or Fire Brain to begin</p>
            </div>
          ) : (
            <div className="space-y-1 max-h-64 overflow-y-auto custom-scroll" data-testid="unified-audit-log">
              {/* Brain decisions mixed in at top */}
              {brainAudit.slice(0, 5).map((d, i) => (
                <div key={d.id || `brain-${i}`} className={`flex items-center gap-2 py-2 px-3 rounded-lg border ${
                  d.action === 'BUY' ? 'bg-purple-900/10 border-purple-800/30' : d.action === 'SELL' ? 'bg-red-900/10 border-red-800/20' : 'bg-zinc-800/20 border-zinc-700/20'
                }`}>
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-800/40 text-purple-300 font-bold shrink-0">BRAIN</span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0`}
                    style={{ background: d.action==='BUY'?'#10b98133':d.action==='SELL'?'#ef444433':'#6b72803a', color: d.action==='BUY'?'#10b981':d.action==='SELL'?'#ef4444':'#9ca3af' }}>
                    {d.action}
                  </span>
                  <span className="text-zinc-300 text-xs font-mono flex-1 truncate">{d.symbol}</span>
                  <span className="text-violet-400 text-[10px]">{d.confidence?.toFixed(1)}%</span>
                  <span className="text-[9px] text-zinc-600">{d.timestamp ? new Date(d.timestamp).toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit'}) : ''}</span>
                </div>
              ))}
              {/* Paper trades */}
              {audit.map((trade, i) => (
                <TradeRow key={trade.trade_id || i} trade={trade} index={i} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── PHASE 3: Live Mode Warning Modal ── */}
      {showLiveWarn && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm px-4">
          <div className="bg-zinc-900 border border-red-700/60 rounded-2xl w-full max-w-md shadow-2xl">
            <div className="p-5 border-b border-red-800/40">
              <h2 className="font-black text-red-400 text-base flex items-center gap-2">
                🔴 LIVE TRADING WARNING
              </h2>
            </div>
            <div className="p-5 space-y-3 text-sm text-zinc-300">
              <p className="font-semibold text-red-300">
                You are about to switch to LIVE mode. This will place REAL orders on your Groww account.
              </p>
              <ul className="space-y-2 text-xs text-zinc-400 list-disc ml-4">
                <li>Real capital from your Groww account will be used</li>
                <li>No guaranteed returns — trading involves risk of loss</li>
                <li>GROWW_API_KEY and GROWW_API_SECRET must be in backend/.env</li>
                <li>30-second confirmation delay before each order</li>
                <li>30% position size reduction applied as safety margin</li>
                <li>Circuit breakers will close positions on drawdown events</li>
                <li>This system is experimental — use only with capital you can afford to lose</li>
              </ul>
              <p className="text-[10px] text-zinc-500">
                By confirming, you accept full responsibility for any financial outcomes.
                This software is provided as-is with no warranty.
              </p>
            </div>
            <div className="flex gap-3 p-5 border-t border-zinc-800">
              <button
                onClick={() => setShowLiveWarn(false)}
                className="flex-1 py-2.5 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-400 rounded-xl text-sm font-semibold"
              >
                Cancel — Stay in Paper
              </button>
              <button
                onClick={handleConfirmLiveMode}
                disabled={modeLoading}
                className="flex-1 py-2.5 bg-red-700 hover:bg-red-600 text-white rounded-xl text-sm font-bold disabled:opacity-50"
                data-testid="confirm-live-mode-btn"
              >
                {modeLoading ? 'Switching…' : 'I Understand — Enable Live'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Settings Modal ── */}
      {settingsOpen && editSettings && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4">
          <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-lg shadow-2xl">
            <div className="flex items-center justify-between p-5 border-b border-zinc-800">
              <h2 className="font-bold text-white">⚙ Robo-Trader Settings</h2>
              <button onClick={() => setSettingsOpen(false)} className="text-zinc-500 hover:text-white text-xl">×</button>
            </div>
            <div className="p-5 space-y-4">
              {/* Daily Target */}
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5 font-semibold">
                  Daily Profit Target (₹)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={editSettings.daily_profit_target}
                    onChange={e => setEditSettings(p => ({ ...p, daily_profit_target: e.target.value }))}
                    className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-violet-500"
                    placeholder="e.g. 1000"
                    min="1"
                  />
                  <div className="flex gap-1">
                    {[500, 1000, 2000, 5000].map(v => (
                      <button key={v} onClick={() => setEditSettings(p => ({ ...p, daily_profit_target: v }))}
                        className="px-2 py-1 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-xs text-zinc-400 transition-colors">
                        ₹{v >= 1000 ? v/1000 + 'k' : v}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Allocated Capital */}
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5 font-semibold">
                  Allocated Capital (₹)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={editSettings.allocated_capital}
                    onChange={e => setEditSettings(p => ({ ...p, allocated_capital: e.target.value }))}
                    className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-violet-500"
                    placeholder="e.g. 100000"
                    min="1000"
                  />
                  <div className="flex gap-1">
                    {[50000, 100000, 200000, 500000].map(v => (
                      <button key={v} onClick={() => setEditSettings(p => ({ ...p, allocated_capital: v }))}
                        className="px-2 py-1 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded text-xs text-zinc-400 transition-colors">
                        ₹{v >= 100000 ? v/100000 + 'L' : v/1000 + 'k'}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Ticker */}
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5 font-semibold">
                  Primary Ticker
                </label>
                <input
                  type="text"
                  value={editSettings.ticker}
                  onChange={e => setEditSettings(p => ({ ...p, ticker: e.target.value.toUpperCase() }))}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-violet-500"
                  placeholder="e.g. RELIANCE.NS"
                />
              </div>

              {/* Risk Tolerance */}
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5 font-semibold">
                  Risk Tolerance
                </label>
                <div className="flex gap-2">
                  {['conservative', 'moderate', 'aggressive'].map(level => (
                    <button
                      key={level}
                      onClick={() => setEditSettings(p => ({ ...p, risk_tolerance: level }))}
                      className={`flex-1 py-2 rounded-lg text-xs font-semibold capitalize transition-all border ${
                        editSettings.risk_tolerance === level
                          ? 'bg-violet-600/30 border-violet-500/60 text-violet-300'
                          : 'bg-zinc-800 border-zinc-700 text-zinc-400 hover:border-zinc-600'
                      }`}
                    >
                      {level === 'conservative' ? '🛡️' : level === 'moderate' ? '⚖️' : '⚡'} {level}
                    </button>
                  ))}
                </div>
              </div>

              {/* Preview button */}
              <button
                onClick={handlePreview}
                disabled={previewLoading}
                className="w-full py-2 bg-blue-600/20 hover:bg-blue-600/30 border border-blue-500/30 text-blue-300 rounded-lg text-sm font-semibold transition-colors"
              >
                {previewLoading ? 'Calculating…' : '🔍 Preview Risk Profile'}
              </button>

              {/* Preview results */}
              {preview && (
                <div
                  className="rounded-xl p-3 border"
                  style={{ borderColor: preview.feasibility_color + '40', background: preview.feasibility_color + '10' }}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-bold" style={{ color: preview.feasibility_color }}>
                      {preview.feasibility_label}
                    </span>
                    <span className="text-xs text-zinc-400">Score: {preview.feasibility_score}/100</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    {[
                      ['Daily return needed', fmtPct(preview.required_daily_return_pct)],
                      ['Kelly fraction',      fmtPct((preview.kelly_fraction || 0) * 100, 3)],
                      ['Kelly position',      fmtInr(preview.kelly_position_inr)],
                      ['Final position',      fmtInr(preview.position_size_inr)],
                      ['VaR 95%',             fmtInr(preview.var_95_inr)],
                      ['CVaR 95%',            fmtInr(preview.cvar_95_inr)],
                      ['VaR 99%',             fmtInr(preview.var_99_inr)],
                      ['Max daily loss',      fmtInr(preview.daily_loss_limit)],
                      ['Vol regime',          preview.vol_regime || '—'],
                      ['NSE history',         `${preview.hist_exceedance_pct}% of days`],
                      ['Min win-rate',        fmtPct(preview.required_win_rate_min, 0)],
                      ['Budget state',        preview.risk_budget_state || 'NORMAL'],
                    ].map(([k, v]) => (
                      <div key={k} className="flex justify-between">
                        <span className="text-zinc-500">{k}</span>
                        <span className="text-white font-semibold">{v}</span>
                      </div>
                    ))}
                  </div>
                  {/* Feasibility warnings in preview */}
                  {preview.feasibility_warnings?.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {preview.feasibility_warnings.map((w, i) => (
                        <p key={i} className="text-[10px] text-amber-400">{w}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="flex gap-3 p-5 border-t border-zinc-800">
              <button
                onClick={() => setSettingsOpen(false)}
                className="flex-1 py-2.5 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-400 rounded-xl text-sm font-semibold transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveSettings}
                disabled={loading}
                className="flex-1 py-2.5 bg-violet-600 hover:bg-violet-500 text-white rounded-xl text-sm font-bold transition-colors disabled:opacity-50"
              >
                {loading ? 'Saving…' : '💾 Save & Apply'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
