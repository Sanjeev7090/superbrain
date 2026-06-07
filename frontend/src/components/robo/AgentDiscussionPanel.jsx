import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Search, Plus, X, Zap, TrendingUp, TrendingDown, Minus,
  RefreshCw, AlertTriangle, Activity, Target, Brain,
  ChevronDown, ChevronUp, Flame, BarChart2, Filter, ArrowUpDown,
  Cpu, TrendingUp as Learn,
} from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const fmtInr = v => v == null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

// ── Signal badge ──────────────────────────────────────────────────────────────
function SignalBadge({ signal, confidence, size = 'sm' }) {
  const cfg = {
    BUY:  { bg: 'rgba(16,185,129,0.15)', border: 'rgba(16,185,129,0.4)',  color: '#10b981', Icon: TrendingUp },
    SELL: { bg: 'rgba(239,68,68,0.15)',  border: 'rgba(239,68,68,0.4)',   color: '#ef4444', Icon: TrendingDown },
    HOLD: { bg: 'rgba(161,161,170,0.1)', border: 'rgba(161,161,170,0.2)', color: '#71717a', Icon: Minus },
  }[signal] || { bg: 'rgba(113,113,122,0.1)', border: 'rgba(113,113,122,0.2)', color: '#71717a', Icon: Minus };

  const s = size === 'lg'
    ? { px: 'px-3', py: 'py-1.5', text: 'text-xs' }
    : { px: 'px-2', py: 'py-0.5', text: 'text-[9px]' };

  return (
    <span
      className={`inline-flex items-center gap-1 ${s.px} ${s.py} rounded-full ${s.text} font-black`}
      style={{ background: cfg.bg, border: `1px solid ${cfg.border}`, color: cfg.color }}
    >
      <cfg.Icon size={size === 'lg' ? 11 : 8} />
      {signal} {confidence != null ? `${confidence.toFixed(0)}%` : ''}
    </span>
  );
}

// ── Win Probability mini arc ──────────────────────────────────────────────────
function WinArc({ prob }) {
  const pct   = Math.min(100, Math.max(0, prob || 0));
  const color = pct >= 65 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444';
  const r = 14, cx = 18, cy = 18;
  const circ = Math.PI * r;
  const offset = circ - (pct / 100) * circ;
  return (
    <div className="flex flex-col items-center">
      <svg width="36" height="22" viewBox="0 0 36 22">
        <path d={`M4,18 A${r},${r} 0 0,1 32,18`} fill="none" stroke="#27272a" strokeWidth="3" />
        <path
          d={`M4,18 A${r},${r} 0 0,1 32,18`}
          fill="none" stroke={color} strokeWidth="3"
          strokeDasharray={`${circ}`} strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
        <text x={cx} y={17} textAnchor="middle" fontSize="7" fontWeight="bold" fill={color}>
          {Math.round(pct)}%
        </text>
      </svg>
      <p className="text-[7px] text-zinc-600 -mt-0.5">Win %</p>
    </div>
  );
}

// ── Volume bar ────────────────────────────────────────────────────────────────
function VolBar({ ratio }) {
  const r     = Math.min(4, ratio || 1);
  const pct   = ((r - 1) / 3) * 100;
  const color = r >= 2 ? '#f59e0b' : r >= 1.5 ? '#8b5cf6' : '#52525b';
  return (
    <div className="flex flex-col gap-0.5">
      <div className="h-1 w-full bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${Math.max(5, pct)}%`, background: color }}
        />
      </div>
      <p className="text-[7px]" style={{ color }}>{r.toFixed(1)}× Vol</p>
    </div>
  );
}

// ── Conviction dots ───────────────────────────────────────────────────────────
function ConvictionDots({ agreementMap, consensus }) {
  const AGENTS = ['KronosAI', 'IntradayMomentum', 'TechComposite', 'Breakout15m', 'MiroFish', 'ActiveScanner'];
  const ICONS  = { KronosAI: '🔮', IntradayMomentum: '⚡', TechComposite: '🔬', Breakout15m: '📈', MiroFish: '🐟', ActiveScanner: '📡' };

  return (
    <div className="flex items-center gap-0.5 flex-wrap">
      {AGENTS.map(a => {
        const sig = agreementMap?.[a];
        if (!sig) return null;
        const agrees = sig === consensus;
        const color = sig === 'BUY' ? '#10b981' : sig === 'SELL' ? '#ef4444' : '#52525b';
        return (
          <span
            key={a}
            title={`${a}: ${sig}`}
            className="text-[8px] px-1 py-0.5 rounded"
            style={{
              background: agrees ? `${color}22` : 'transparent',
              border: `1px solid ${agrees ? color : '#3f3f46'}`,
              opacity: sig === 'HOLD' ? 0.5 : 1,
            }}
          >
            {ICONS[a]}
          </span>
        );
      })}
    </div>
  );
}

// ── Intraday Pick Card ────────────────────────────────────────────────────────
function PickCard({ pick, onSelect, isSelected }) {
  const ticker = pick.ticker.replace('.NS', '').replace('.BO', '');
  const consensus = pick.consensus;
  const borderCol = consensus === 'BUY' ? 'rgba(16,185,129,0.3)' : consensus === 'SELL' ? 'rgba(239,68,68,0.3)' : 'rgba(63,63,70,0.6)';
  const bgCol     = consensus === 'BUY' ? 'rgba(16,185,129,0.04)' : consensus === 'SELL' ? 'rgba(239,68,68,0.04)' : 'transparent';
  const glow      = isSelected ? (consensus === 'BUY' ? '0 0 18px rgba(16,185,129,0.15)' : '0 0 18px rgba(239,68,68,0.15)') : 'none';

  const winPct  = pick.win_probability || 0;
  const conv    = pick.conviction_score || 0;

  // Rank badge color: ≥70 = gold, ≥55 = silver, else bronze
  const iScore = pick.intraday_score || 0;
  const rankColor = iScore >= 70 ? '#f59e0b' : iScore >= 55 ? '#94a3b8' : '#78350f';

  return (
    <button
      onClick={() => onSelect(pick.ticker)}
      data-testid={`intraday-pick-${ticker}`}
      className="w-full text-left rounded-2xl p-3 transition-all hover:scale-[1.01] active:scale-[0.99] space-y-2"
      style={{
        background: bgCol,
        border: `1px solid ${borderCol}`,
        boxShadow: glow,
        cursor: 'pointer',
      }}
    >
      {/* Row 1: Ticker + Signal + Score badge */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="text-xs font-black px-1.5 py-0.5 rounded-md"
            style={{ color: rankColor, background: `${rankColor}18`, border: `1px solid ${rankColor}40` }}
          >
            {iScore.toFixed(0)}
          </span>
          <span className="text-sm font-black text-white tracking-wide truncate">{ticker}</span>
        </div>
        <SignalBadge signal={consensus} confidence={pick.confidence} />
      </div>

      {/* Row 2: Win Arc + Volume + Conviction */}
      <div className="flex items-end justify-between gap-2">
        <WinArc prob={winPct} />
        <div className="flex-1 space-y-1">
          <VolBar ratio={pick.volume_ratio || 1} />
          <div className="flex items-center gap-1">
            <span className="text-[7px] text-zinc-600">Conv:</span>
            <div className="h-1 flex-1 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all"
                style={{
                  width: `${conv}%`,
                  background: conv >= 70 ? '#10b981' : conv >= 50 ? '#f59e0b' : '#6b7280',
                }}
              />
            </div>
            <span className="text-[7px] text-zinc-500">{conv.toFixed(0)}%</span>
          </div>
        </div>
      </div>

      {/* Row 3: Agent agreement dots */}
      <ConvictionDots agreementMap={pick.agent_agreement_map} consensus={consensus} />

      {/* Row 4: Bull/Bear count */}
      <div className="flex items-center gap-2 text-[7px] text-zinc-600">
        <span className="text-emerald-500">{pick.bull}B</span>
        <span className="text-zinc-700">·</span>
        <span className="text-red-500">{pick.bear}S</span>
        <span className="text-zinc-700">·</span>
        <span>
          {winPct >= 65 ? '🔥 High Prob' : winPct >= 50 ? '⚡ Moderate' : '⚠️ Risky'}
        </span>
      </div>
    </button>
  );
}

// ── Agent Card (for discussion detail) ───────────────────────────────────────
function AgentCard({ sig }) {
  const [expanded, setExpanded] = useState(false);
  if (!sig) return null;

  const ICONS = {
    KronosAI:          '🔮',
    Breakout15m:       '📈',
    TechComposite:     '🔬',
    IntradayMomentum:  '⚡',
    MiroFish:          '🐟',
    ActiveScanner:     '📡',
  };

  return (
    <div
      className="bg-zinc-900 border border-zinc-800 rounded-xl p-3 flex flex-col gap-2"
      data-testid={`agent-card-${sig.agent_name}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm flex-shrink-0">{ICONS[sig.agent_name] || '🤖'}</span>
          <span className="text-[10px] font-black text-zinc-300 truncate">{sig.agent_name}</span>
          {sig.timeframe && (
            <span className="text-[8px] text-zinc-600 bg-zinc-800 rounded px-1 py-0.5 font-mono flex-shrink-0">
              {sig.timeframe}
            </span>
          )}
        </div>
        <SignalBadge signal={sig.signal} confidence={sig.confidence} />
      </div>

      <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${Math.min(sig.confidence || 0, 100)}%`,
            background: { BUY: '#10b981', SELL: '#ef4444', HOLD: '#6b7280' }[sig.signal] || '#6b7280',
          }}
        />
      </div>

      {sig.entry > 0 && (
        <div className="grid grid-cols-3 gap-1 text-center">
          {[
            { l: 'Entry', v: `₹${sig.entry?.toLocaleString('en-IN')}`,  c: '#a1a1aa' },
            { l: 'SL',    v: `₹${sig.sl?.toLocaleString('en-IN')}`,     c: '#ef4444' },
            { l: 'T1',    v: `₹${sig.target?.toLocaleString('en-IN')}`, c: '#10b981' },
          ].map(({ l, v, c }) => (
            <div key={l} className="bg-zinc-800/60 rounded-lg p-1">
              <p className="text-[7px] text-zinc-600">{l}</p>
              <p className="text-[9px] font-mono font-bold" style={{ color: c }}>{v}</p>
            </div>
          ))}
        </div>
      )}

      <p className={`text-[8px] text-zinc-500 leading-relaxed ${expanded ? '' : 'line-clamp-2'}`}>
        {sig.reasoning}
      </p>
      {sig.reasoning?.length > 90 && (
        <button
          onClick={() => setExpanded(x => !x)}
          className="flex items-center gap-0.5 text-[7px] text-zinc-600 hover:text-zinc-400"
        >
          {expanded ? <ChevronUp size={8} /> : <ChevronDown size={8} />}
          {expanded ? 'less' : 'more'}
        </button>
      )}
      {sig.error && (
        <p className="text-[7px] text-red-400/70 flex items-center gap-1">
          <AlertTriangle size={7} /> {sig.error}
        </p>
      )}
    </div>
  );
}

// ── Monte Carlo Card ──────────────────────────────────────────────────────────
function MonteCarloCard({ mc }) {
  if (!mc || mc.error) return null;
  const tgtPct  = Math.round((mc.target_hit_prob || 0) * 100);
  const slPct   = Math.round((mc.sl_hit_prob || 0) * 100);
  const neutPct = Math.round((mc.neutral_prob || 0) * 100);
  return (
    <div className="bg-zinc-900/50 border border-zinc-800/60 rounded-xl p-3" data-testid="monte-carlo-card">
      <p className="text-[9px] text-zinc-500 font-bold uppercase tracking-widest mb-2">
        Monte Carlo — {mc.paths_analyzed?.toLocaleString()} paths
      </p>
      <div className="flex gap-0 rounded-lg overflow-hidden h-3 mb-2">
        <div style={{ width: `${tgtPct}%`, background: '#10b981' }} title={`Target: ${tgtPct}%`} />
        <div style={{ width: `${neutPct}%`, background: '#6b7280' }} title={`Neutral: ${neutPct}%`} />
        <div style={{ width: `${slPct}%`, background: '#ef4444' }} title={`SL: ${slPct}%`} />
      </div>
      <div className="grid grid-cols-3 gap-1 text-center mb-2">
        {[
          { l: 'Target Hit', v: `${tgtPct}%`, c: '#10b981' },
          { l: 'Neutral',    v: `${neutPct}%`, c: '#6b7280' },
          { l: 'SL Hit',     v: `${slPct}%`,  c: '#ef4444' },
        ].map(({ l, v, c }) => (
          <div key={l}>
            <p className="text-[7px] text-zinc-600">{l}</p>
            <p className="text-[10px] font-black" style={{ color: c }}>{v}</p>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-1 text-center">
        {[
          { l: 'Exp P&L',  v: fmtInr(mc.expected_pnl_inr), c: (mc.expected_pnl_inr || 0) >= 0 ? '#10b981' : '#ef4444' },
          { l: 'Qty',      v: mc.quantity || '—',           c: '#a78bfa' },
          { l: 'P5 P&L',   v: fmtInr(mc.pnl_p5),           c: '#ef4444' },
          { l: 'P95 P&L',  v: fmtInr(mc.pnl_p95),          c: '#10b981' },
        ].map(({ l, v, c }) => (
          <div key={l} className="bg-zinc-800/40 rounded-lg p-1">
            <p className="text-[7px] text-zinc-600">{l}</p>
            <p className="text-[9px] font-bold" style={{ color: c }}>{v}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Robot Learning Panel ─────────────────────────────────────────────────────
function RobotLearningPanel({ learningState, onReset }) {
  if (!learningState) return null;

  const {
    agent_weights, accuracy_scores, base_weights, weight_vs_base,
    best_agent, learning_iterations, trade_outcomes_learned,
    price_validations_done, overall_accuracy, pending_validations, recent_changes,
  } = learningState;

  const AGENTS = [
    { name: 'KronosAI',         icon: '🔮', label: 'Kronos AI' },
    { name: 'IntradayMomentum', icon: '⚡', label: 'Intraday' },
    { name: 'TechComposite',    icon: '🔬', label: 'Technical' },
    { name: 'Breakout15m',      icon: '📈', label: 'Breakout' },
    { name: 'MiroFish',         icon: '🐟', label: 'MiroFish' },
    { name: 'ActiveScanner',    icon: '📡', label: 'Scanner' },
  ];

  const isLearning = learning_iterations > 0;
  const isActive   = pending_validations > 0 || learning_iterations > 0;

  return (
    <div
      className="rounded-2xl p-3 space-y-3"
      style={{ background: 'rgba(124,58,237,0.04)', border: '1px solid rgba(124,58,237,0.15)' }}
      data-testid="robot-learning-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu size={11} className="text-violet-400" />
          <span className="text-[9px] font-black text-violet-300 uppercase tracking-widest">
            Robot 3.0 Self-Learning
          </span>
          {isActive && (
            <span
              className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"
              title="Actively learning"
            />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[7px] text-zinc-600">
            iter {learning_iterations}
          </span>
          <button
            onClick={onReset}
            className="text-[7px] text-zinc-700 hover:text-red-400 px-1.5 py-0.5 rounded border border-zinc-800"
            data-testid="learning-reset-btn"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Stats strip */}
      <div className="grid grid-cols-4 gap-1 text-center">
        {[
          { l: 'Iterations', v: learning_iterations, c: '#a78bfa' },
          { l: 'Trades Learnt', v: trade_outcomes_learned, c: '#10b981' },
          { l: 'Price Valid.', v: price_validations_done, c: '#f59e0b' },
          { l: 'Accuracy', v: `${overall_accuracy}%`, c: overall_accuracy >= 55 ? '#10b981' : '#ef4444' },
        ].map(({ l, v, c }) => (
          <div key={l} className="bg-zinc-900/50 rounded-xl p-1.5">
            <p className="text-[7px] text-zinc-600 leading-tight">{l}</p>
            <p className="text-[10px] font-black" style={{ color: c }}>{v}</p>
          </div>
        ))}
      </div>

      {/* Kronos Teacher line */}
      <div
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl"
        style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.2)' }}
      >
        <span className="text-base">🔮</span>
        <div className="flex-1">
          <p className="text-[8px] font-bold text-violet-300">Kronos AI — Teacher Signal</p>
          <p className="text-[7px] text-zinc-600">
            High-confidence Kronos signals correct other agents' weights
          </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] font-black text-violet-300">
            {(agent_weights?.['KronosAI'] * 100 || 0).toFixed(1)}%
          </p>
          <p className="text-[7px]" style={{
            color: (weight_vs_base?.['KronosAI'] || 0) >= 0 ? '#10b981' : '#ef4444'
          }}>
            {(weight_vs_base?.['KronosAI'] || 0) >= 0 ? '↑' : '↓'}
            {Math.abs(((weight_vs_base?.['KronosAI'] || 0) * 100)).toFixed(1)}%
          </p>
        </div>
      </div>

      {/* Best agent badge */}
      {best_agent && learning_iterations > 0 && (
        <div className="flex items-center gap-1.5">
          <span className="text-[7px] text-zinc-600">Best performing:</span>
          <span
            className="text-[7px] font-black px-1.5 py-0.5 rounded-full"
            style={{ background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.3)', color: '#f59e0b' }}
          >
            {best_agent} — {(accuracy_scores?.[best_agent] || 50).toFixed(1)}% acc
          </span>
        </div>
      )}

      {/* Agent accuracy bars */}
      <div className="space-y-1.5">
        {AGENTS.map(({ name, icon, label }) => {
          const acc    = accuracy_scores?.[name] || 50;
          const wt     = (agent_weights?.[name] || 0) * 100;
          const delta  = (weight_vs_base?.[name] || 0) * 100;
          const isUp   = delta > 0.3;
          const isDown = delta < -0.3;
          const accColor = acc >= 60 ? '#10b981' : acc >= 45 ? '#f59e0b' : '#ef4444';
          const isBest = name === best_agent && learning_iterations > 0;

          return (
            <div key={name} className="flex items-center gap-2" data-testid={`agent-learn-${name}`}>
              <span className="text-[10px] w-4 flex-shrink-0">{icon}</span>
              <span
                className="text-[7px] font-bold w-14 flex-shrink-0"
                style={{ color: isBest ? '#f59e0b' : '#71717a' }}
              >
                {label}{isBest ? ' ⭐' : ''}
              </span>
              {/* Accuracy bar */}
              <div className="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${acc}%`, background: accColor }}
                />
              </div>
              <span className="text-[7px] font-mono w-7 text-right" style={{ color: accColor }}>
                {acc.toFixed(0)}%
              </span>
              {/* Weight delta arrow */}
              <span
                className="text-[7px] font-black w-6 text-right"
                style={{ color: isUp ? '#10b981' : isDown ? '#ef4444' : '#52525b' }}
              >
                {isUp ? '↑' : isDown ? '↓' : '—'}
                {Math.abs(delta) > 0.3 ? Math.abs(delta).toFixed(1) : ''}
              </span>
            </div>
          );
        })}
      </div>

      {/* Recent weight changes */}
      {recent_changes?.length > 0 && (
        <div className="space-y-1">
          <p className="text-[7px] text-zinc-700 uppercase tracking-widest font-bold">Recent changes</p>
          {recent_changes.slice(-4).map((c, i) => (
            <div key={i} className="flex items-center gap-1.5 text-[7px]">
              <span>{c.trigger === 'trade' ? '💼' : c.trigger === 'kronos' ? '🔮' : '📊'}</span>
              <span className="text-zinc-600">{c.agent}</span>
              <span style={{ color: c.delta > 0 ? '#10b981' : '#ef4444' }}>
                {c.delta > 0 ? '+' : ''}{(c.delta * 100).toFixed(2)}%
              </span>
              <span className="text-zinc-700 ml-auto">→ {(c.new_w * 100).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      )}

      {pending_validations > 0 && (
        <p className="text-[7px] text-zinc-700 animate-pulse">
          {pending_validations} prediction(s) awaiting price validation (~30 min)…
        </p>
      )}
    </div>
  );
}


// ── MAIN COMPONENT ────────────────────────────────────────────────────────────
export default function AgentDiscussionPanel({ capital = 100000, onSelectStock }) {
  const [discussion,    setDiscussion]    = useState(null);
  const [topPicks,      setTopPicks]      = useState([]);
  const [manualTickers, setManualTickers] = useState([]);
  const [scanMode,      setScanMode]      = useState('hybrid');
  const [scanLoading,   setScanLoading]   = useState(false);
  const [tickerInput,   setTickerInput]   = useState('');
  const [lastScanTime,  setLastScanTime]  = useState(null);
  const [deepMode,      setDeepMode]      = useState(false);
  const [searchQuery,   setSearchQuery]   = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [selectedTicker, setSelectedTicker] = useState(null);
  const [learningState, setLearningState] = useState(null);

  // Filters & Sort
  const [filter,  setFilter]  = useState('all');   // all | high_vol | high_win | strong
  const [sortBy,  setSortBy]  = useState('score'); // score | win | volume | confidence

  const searchDebounceRef = React.useRef(null);

  const fetchLearningState = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/robo/learning-state`).catch(() => ({ data: {} }));
      if (res.data?.learning) setLearningState(res.data.learning);
    } catch { /* silent */ }
  }, []);

  const fetchDiscussion = useCallback(async () => {
    try {
      const [discRes, manualRes] = await Promise.all([
        axios.get(`${API}/robo/agent-discussion`).catch(() => ({ data: {} })),
        axios.get(`${API}/robo/manual-stocks`).catch(() => ({ data: {} })),
      ]);
      if (discRes.data?.discussion) setDiscussion(discRes.data.discussion);
      if (discRes.data?.top_picks)  setTopPicks(discRes.data.top_picks);
      if (discRes.data?.last_scan_time) setLastScanTime(discRes.data.last_scan_time);
      if (manualRes.data?.manual_tickers) setManualTickers(manualRes.data.manual_tickers);
    } catch { /* silent */ }
  }, []);

  React.useEffect(() => {
    fetchDiscussion();
    fetchLearningState();
  }, [fetchDiscussion, fetchLearningState]);

  const handleScan = async (ticker = null) => {
    setScanLoading(true);
    try {
      const payload = ticker
        ? { ticker, deep: deepMode }
        : { mode: scanMode, deep: deepMode };
      await axios.post(`${API}/robo/scan-now`, payload);
      toast.success(ticker ? `Analysing ${ticker}…` : `Scanning ${scanMode} universe…`);
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        await fetchDiscussion();
        if (attempts % 5 === 0) fetchLearningState();  // refresh learning every 10s
        if (attempts >= 20) clearInterval(poll);
      }, 2000);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Scan failed');
    } finally {
      setScanLoading(false);
    }
  };

  const handleLearningReset = async () => {
    try {
      await axios.post(`${API}/robo/learning-reset`);
      toast.success('Learning reset — weights back to factory defaults');
      fetchLearningState();
    } catch { toast.error('Reset failed'); }
  };

  const handleAddTicker = async () => {
    const t = tickerInput.trim().toUpperCase();
    if (!t) return;
    try {
      const res = await axios.post(`${API}/robo/manual-stocks/add`, { ticker: t });
      if (res.data.success) {
        setManualTickers(res.data.manual_tickers || []);
        setTickerInput(''); setSearchQuery('');
        toast.success(`Added ${res.data.ticker}`);
      }
    } catch { toast.error('Failed to add ticker'); }
  };

  const handleRemoveTicker = async (ticker) => {
    try {
      const res = await axios.post(`${API}/robo/manual-stocks/remove`, { ticker });
      if (res.data.success) setManualTickers(res.data.manual_tickers || []);
    } catch { /* silent */ }
  };

  const handleLoadInChart = async (ticker) => {
    handleScan(ticker);
    setSelectedTicker(ticker);
    try {
      const base = ticker.replace('.NS', '').replace('.BO', '');
      const res  = await axios.get(`${API}/stock/search`, { params: { q: base } });
      const results = res.data.results || [];
      const match   = results.find(r => r.ticker === ticker) || results[0];
      if (match) {
        if (onSelectStock) onSelectStock(match);
        axios.post(`${API}/robo/settings`, { ticker: match.ticker }).catch(() => {});
        toast.success(`Loaded ${match.name || ticker}`);
      }
    } catch { /* scan still runs */ }
  };

  const handleSearchInput = (val) => {
    setSearchQuery(val);
    setTickerInput(val.toUpperCase());
    clearTimeout(searchDebounceRef.current);
    if (val.length < 1) { setSearchResults([]); return; }
    searchDebounceRef.current = setTimeout(async () => {
      setSearchLoading(true);
      try {
        const res = await axios.get(`${API}/stock/search`, { params: { q: val } });
        setSearchResults(res.data.results || []);
      } catch { setSearchResults([]); }
      finally { setSearchLoading(false); }
    }, 400);
  };

  const handleSelectSearchResult = (stock) => {
    setTickerInput(stock.ticker);
    setSearchQuery(stock.ticker);
    setSearchResults([]);
  };

  // ── Apply filter & sort ──────────────────────────────────────────────────
  const filteredPicks = React.useMemo(() => {
    let p = [...topPicks];
    if (filter === 'high_vol')  p = p.filter(x => (x.volume_ratio || 1) >= 1.5);
    if (filter === 'high_win')  p = p.filter(x => (x.win_probability || 0) >= 60);
    if (filter === 'strong')    p = p.filter(x => (x.confidence || 0) >= 70);
    if (filter === 'buy')       p = p.filter(x => x.consensus === 'BUY');

    if (sortBy === 'win')        p.sort((a, b) => (b.win_probability || 0) - (a.win_probability || 0));
    else if (sortBy === 'volume') p.sort((a, b) => (b.volume_ratio || 0) - (a.volume_ratio || 0));
    else if (sortBy === 'confidence') p.sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
    else p.sort((a, b) => (b.intraday_score || 0) - (a.intraday_score || 0));
    return p;
  }, [topPicks, filter, sortBy]);

  const disc    = discussion;
  const signals = disc?.agent_signals || [];

  return (
    <div className="space-y-4" data-testid="agent-discussion-panel">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[9px] text-zinc-500 uppercase tracking-widest font-bold">
            Agent Collaboration
          </p>
          {lastScanTime && (
            <p className="text-[8px] text-zinc-700 mt-0.5">
              Last: {new Date(lastScanTime).toLocaleTimeString('en-IN')}
              {topPicks.length > 0 && <span className="ml-1 text-violet-600">· {topPicks.length} stocks scanned</span>}
            </p>
          )}
        </div>
        <button
          onClick={() => { fetchDiscussion(); fetchLearningState(); }}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-500 hover:text-white transition-all"
          data-testid="refresh-discussion-btn"
        >
          <RefreshCw size={11} />
        </button>
      </div>

      {/* ── Scan Controls ──────────────────────────────────────────────────── */}
      <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-3 space-y-3">
        {/* Mode pills */}
        <div className="flex items-center gap-2 flex-wrap">
          {['auto', 'manual', 'hybrid'].map(m => (
            <button
              key={m}
              onClick={() => setScanMode(m)}
              data-testid={`scan-mode-${m}`}
              className={`px-3 py-1 rounded-xl text-[9px] font-bold transition-all ${
                scanMode === m
                  ? 'bg-violet-600/30 border border-violet-500/50 text-violet-300'
                  : 'bg-zinc-800/50 border border-zinc-700/40 text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {m.charAt(0).toUpperCase() + m.slice(1)}
            </button>
          ))}
          <button
            onClick={() => setDeepMode(x => !x)}
            data-testid="deep-mode-toggle"
            className={`px-3 py-1 rounded-xl text-[9px] font-bold transition-all ml-auto ${
              deepMode
                ? 'bg-amber-600/20 border border-amber-500/40 text-amber-300'
                : 'bg-zinc-800/50 border border-zinc-700/40 text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {deepMode ? '🧠 Deep AI' : 'Fast Mode'}
          </button>
        </div>

        {/* Ticker search */}
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <div className="flex items-center gap-2 bg-zinc-800/60 border border-zinc-700/40 rounded-xl px-3 py-1.5">
              <Search size={10} className="text-zinc-600 flex-shrink-0" />
              <input
                value={searchQuery}
                onChange={e => handleSearchInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') { setSearchResults([]); handleAddTicker(); }
                  if (e.key === 'Escape') setSearchResults([]);
                }}
                placeholder="Add ticker e.g. TCS"
                className="bg-transparent text-[10px] text-white placeholder-zinc-600 outline-none w-full"
                data-testid="ticker-input"
              />
              {searchLoading && <span className="w-2.5 h-2.5 border border-zinc-600 border-t-violet-400 rounded-full animate-spin flex-shrink-0" />}
            </div>
            {searchResults.length > 0 && (
              <div className="absolute left-0 right-0 top-full mt-1 bg-zinc-900 border border-zinc-700 rounded-xl overflow-hidden z-50 max-h-48 overflow-y-auto shadow-xl">
                {searchResults.slice(0, 8).map((stock, i) => (
                  <button
                    key={i}
                    onClick={() => handleSelectSearchResult(stock)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-zinc-800 transition-colors border-b border-zinc-800/60 last:border-0"
                  >
                    <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-900/40 text-emerald-400 font-mono flex-shrink-0">
                      {stock.exchange || 'NSE'}
                    </span>
                    <span className="text-[10px] font-mono font-bold text-white">{stock.ticker}</span>
                    <span className="text-[9px] text-zinc-500 truncate ml-auto">{stock.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={handleAddTicker}
            className="p-2 rounded-xl bg-violet-600/20 border border-violet-500/30 text-violet-400 hover:bg-violet-600/30 transition-all"
            data-testid="add-ticker-btn"
          >
            <Plus size={12} />
          </button>
          <button
            onClick={() => handleScan()}
            disabled={scanLoading}
            data-testid="scan-now-btn"
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-xl text-[10px] font-black transition-all disabled:opacity-50"
            style={{
              background: scanLoading ? '#27272a' : 'linear-gradient(135deg, #7c3aed, #4f46e5)',
              boxShadow: scanLoading ? 'none' : '0 0 20px rgba(124,58,237,0.3)',
              color: 'white',
            }}
          >
            {scanLoading ? <RefreshCw size={11} className="animate-spin" /> : <Activity size={11} />}
            {scanLoading ? 'Scanning…' : 'Scan Now'}
          </button>
        </div>

        {/* Manual tickers */}
        {manualTickers.length > 0 && (
          <div className="flex flex-wrap gap-1.5" data-testid="manual-tickers-list">
            {manualTickers.map(t => (
              <span key={t} className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-violet-900/20 border border-violet-700/30 text-violet-300 text-[9px] font-mono">
                {t}
                <button onClick={() => handleRemoveTicker(t)} data-testid={`remove-${t}`} className="text-violet-500 hover:text-red-400">
                  <X size={8} />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ── Intraday Top Picks ─────────────────────────────────────────────── */}
      {topPicks.length > 0 && (
        <div data-testid="intraday-picks-section">
          {/* Section header */}
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Flame size={11} className="text-amber-400" />
              <p className="text-[9px] text-zinc-400 uppercase tracking-widest font-bold">
                Intraday Best Picks
              </p>
              <span
                className="text-[7px] px-1.5 py-0.5 rounded-full font-bold"
                style={{ background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)', color: '#a78bfa' }}
              >
                {filteredPicks.length}/{topPicks.length}
              </span>
            </div>
            {/* Sort */}
            <div className="flex items-center gap-1">
              <ArrowUpDown size={9} className="text-zinc-600" />
              <select
                value={sortBy}
                onChange={e => setSortBy(e.target.value)}
                data-testid="sort-picks-select"
                className="bg-zinc-900 border border-zinc-700 text-zinc-400 text-[8px] rounded-lg px-1.5 py-1 outline-none"
              >
                <option value="score">Score</option>
                <option value="win">Win%</option>
                <option value="volume">Volume</option>
                <option value="confidence">Conf</option>
              </select>
            </div>
          </div>

          {/* Filter pills */}
          <div className="flex gap-1.5 mb-3 flex-wrap">
            {[
              { id: 'all',      label: 'All',          icon: <Target size={8} /> },
              { id: 'buy',      label: 'BUY only',     icon: <TrendingUp size={8} /> },
              { id: 'high_vol', label: 'High Volume',  icon: <BarChart2 size={8} /> },
              { id: 'high_win', label: 'Win 60%+',     icon: <Flame size={8} /> },
              { id: 'strong',   label: 'Conf 70%+',    icon: <Zap size={8} /> },
            ].map(f => (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                data-testid={`filter-${f.id}`}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[8px] font-bold transition-all"
                style={{
                  background: filter === f.id ? 'rgba(124,58,237,0.2)' : 'rgba(39,39,42,0.6)',
                  border: `1px solid ${filter === f.id ? 'rgba(124,58,237,0.5)' : 'rgba(63,63,70,0.5)'}`,
                  color: filter === f.id ? '#c4b5fd' : '#71717a',
                }}
              >
                {f.icon}
                {f.label}
              </button>
            ))}
          </div>

          {filteredPicks.length === 0 ? (
            <div className="text-center py-4">
              <Filter size={18} className="text-zinc-700 mx-auto mb-1" />
              <p className="text-[9px] text-zinc-600">No stocks match this filter.</p>
              <button onClick={() => setFilter('all')} className="text-[8px] text-violet-500 mt-0.5">Show all</button>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2" data-testid="intraday-picks-grid">
              {filteredPicks.map(p => (
                <PickCard
                  key={p.ticker}
                  pick={p}
                  onSelect={handleLoadInChart}
                  isSelected={selectedTicker === p.ticker}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Consensus Summary (for selected/best ticker) ────────────────── */}
      {disc && (
        <div
          className="bg-zinc-900/60 border rounded-2xl p-4"
          style={{
            borderColor: disc.consensus === 'BUY' ? 'rgba(16,185,129,0.3)' : disc.consensus === 'SELL' ? 'rgba(239,68,68,0.3)' : 'rgba(113,113,122,0.2)',
          }}
          data-testid="consensus-summary"
        >
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-[8px] text-zinc-600 uppercase tracking-widest font-bold">
                Agent Consensus · {disc.ticker}
              </p>
              <p className="text-[8px] text-zinc-700 mt-0.5">
                {disc.bull_count}B · {disc.bear_count}S · {disc.hold_count}H
                &nbsp;·&nbsp; score: {disc.weighted_score > 0 ? '+' : ''}{disc.weighted_score?.toFixed(1)}
                {disc.conviction_score > 0 && <span className="ml-1 text-violet-600">· conv {disc.conviction_score?.toFixed(0)}%</span>}
              </p>
            </div>
            <SignalBadge signal={disc.consensus} confidence={disc.consensus_confidence} size="lg" />
          </div>

          {/* Win + Volume row */}
          {(disc.win_probability > 0 || disc.volume_ratio > 0) && (
            <div className="flex gap-3 mb-3 items-center">
              <WinArc prob={disc.win_probability} />
              <div className="flex-1">
                <VolBar ratio={disc.volume_ratio} />
              </div>
              <ConvictionDots agreementMap={disc.agent_agreement_map} consensus={disc.consensus} />
            </div>
          )}

          {/* Bull/bear bar */}
          <div className="flex gap-0 rounded-full overflow-hidden h-2 mb-3">
            {[
              { count: disc.bull_count, color: '#10b981' },
              { count: disc.hold_count, color: '#6b7280' },
              { count: disc.bear_count, color: '#ef4444' },
            ].map(({ count, color }, i) => {
              const total = (disc.agent_signals?.length || 1);
              return (
                <div key={i} style={{ width: `${(count / total) * 100}%`, background: color, opacity: 0.8 }} />
              );
            })}
          </div>

          {/* Dreamer V3 verdict */}
          <div
            className="rounded-xl p-3"
            style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.2)' }}
            data-testid="dreamer-final-thought"
          >
            <div className="flex items-center gap-2 mb-1.5">
              <Brain size={11} className="text-violet-400" />
              <span className="text-[9px] font-black text-violet-300">Dreamer V3 Final Verdict</span>
              <SignalBadge signal={disc.dreamer_final_signal} confidence={disc.dreamer_final_confidence} />
            </div>
            <p className="text-[8px] text-violet-200/70 leading-relaxed">
              {disc.dreamer_reasoning || 'Awaiting Dreamer V3 analysis…'}
            </p>
          </div>
        </div>
      )}

      {/* ── 6-Agent Cards Grid ────────────────────────────────────────────── */}
      {signals.length > 0 ? (
        <div>
          <p className="text-[8px] text-zinc-600 uppercase tracking-widest font-bold mb-2">
            Agent Signals — {disc?.ticker}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2" data-testid="agent-cards">
            {signals.map(sig => <AgentCard key={sig.agent_name} sig={sig} />)}
          </div>
        </div>
      ) : topPicks.length === 0 ? (
        <div className="bg-zinc-900/40 border border-zinc-800/40 rounded-2xl flex items-center justify-center py-10">
          <div className="text-center">
            <div className="text-3xl mb-2">🤝</div>
            <p className="text-xs text-zinc-600">No agent discussion yet</p>
            <p className="text-[9px] text-zinc-700 mt-1">Click "Scan Now" to run 6-agent intraday analysis</p>
          </div>
        </div>
      ) : null}

      {/* ── Monte Carlo ────────────────────────────────────────────────────── */}
      {disc?.monte_carlo && Object.keys(disc.monte_carlo).length > 0 && (
        <MonteCarloCard mc={disc.monte_carlo} />
      )}

      {/* ── Robot 3.0 Self-Learning Panel ────────────────────────────── */}
      <RobotLearningPanel
        learningState={learningState}
        onReset={handleLearningReset}
      />
    </div>
  );
}
