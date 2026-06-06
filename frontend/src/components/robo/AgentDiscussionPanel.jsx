import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Search, Plus, X, Zap, TrendingUp, TrendingDown, Minus,
  RefreshCw, BarChart2, AlertTriangle, Activity, Target,
  Brain, ChevronDown, ChevronUp,
} from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const fmtPct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`;
const fmtInr = v => v == null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

// ── Signal badge ──────────────────────────────────────────────────────────────
function SignalBadge({ signal, confidence, size = 'sm' }) {
  const cfg = {
    BUY:  { bg: 'rgba(16,185,129,0.15)', border: 'rgba(16,185,129,0.4)',  color: '#10b981', Icon: TrendingUp },
    SELL: { bg: 'rgba(239,68,68,0.15)',  border: 'rgba(239,68,68,0.4)',   color: '#ef4444', Icon: TrendingDown },
    HOLD: { bg: 'rgba(161,161,170,0.1)', border: 'rgba(161,161,170,0.2)', color: '#71717a', Icon: Minus },
  }[signal] || { bg: 'rgba(113,113,122,0.1)', border: 'rgba(113,113,122,0.2)', color: '#71717a', Icon: Minus };

  const s = size === 'lg' ? { px: 'px-3', py: 'py-1.5', text: 'text-xs' } : { px: 'px-2', py: 'py-0.5', text: 'text-[9px]' };

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

// ── Confidence bar ────────────────────────────────────────────────────────────
function ConfBar({ signal, confidence }) {
  const color = { BUY: '#10b981', SELL: '#ef4444', HOLD: '#6b7280' }[signal] || '#6b7280';
  return (
    <div className="h-1 bg-zinc-800 rounded-full overflow-hidden mt-1">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${Math.min(confidence || 0, 100)}%`, background: color }}
      />
    </div>
  );
}

// ── Agent Card ────────────────────────────────────────────────────────────────
function AgentCard({ sig }) {
  const [expanded, setExpanded] = useState(false);
  if (!sig) return null;

  const ICONS = {
    KronosAI:      '🔮',
    Breakout15m:   '⚡',
    TechComposite: '🔬',
    MiroFish:      '🐟',
    ActiveScanner: '📡',
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

      <ConfBar signal={sig.signal} confidence={sig.confidence} />

      {sig.entry > 0 && (
        <div className="grid grid-cols-3 gap-1 text-center">
          {[
            { l: 'Entry', v: `₹${sig.entry?.toLocaleString('en-IN')}`, c: '#a1a1aa' },
            { l: 'SL',    v: `₹${sig.sl?.toLocaleString('en-IN')}`,    c: '#ef4444' },
            { l: 'T1',    v: `₹${sig.target?.toLocaleString('en-IN')}`, c: '#10b981' },
          ].map(({ l, v, c }) => (
            <div key={l} className="bg-zinc-800/60 rounded-lg p-1">
              <p className="text-[7px] text-zinc-600">{l}</p>
              <p className="text-[9px] font-mono font-bold" style={{ color: c }}>{v}</p>
            </div>
          ))}
        </div>
      )}

      <div>
        <p className="text-[8px] text-zinc-500 leading-relaxed line-clamp-2">
          {sig.reasoning}
        </p>
        {sig.reasoning?.length > 90 && (
          <button
            onClick={() => setExpanded(x => !x)}
            className="flex items-center gap-0.5 text-[7px] text-zinc-600 hover:text-zinc-400 mt-0.5"
          >
            {expanded ? <ChevronUp size={8} /> : <ChevronDown size={8} />}
            {expanded ? 'less' : 'more'}
          </button>
        )}
        {expanded && (
          <p className="text-[8px] text-zinc-500 leading-relaxed mt-1">{sig.reasoning}</p>
        )}
      </div>

      {sig.error && (
        <p className="text-[7px] text-red-400/70 flex items-center gap-1">
          <AlertTriangle size={7} /> {sig.error}
        </p>
      )}
    </div>
  );
}

// ── Monte Carlo mini-chart ────────────────────────────────────────────────────
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
        <div
          className="transition-all duration-700"
          style={{ width: `${tgtPct}%`, background: '#10b981' }}
          title={`Target: ${tgtPct}%`}
        />
        <div
          className="transition-all duration-700"
          style={{ width: `${neutPct}%`, background: '#6b7280' }}
          title={`Neutral: ${neutPct}%`}
        />
        <div
          className="transition-all duration-700"
          style={{ width: `${slPct}%`, background: '#ef4444' }}
          title={`SL: ${slPct}%`}
        />
      </div>
      <div className="grid grid-cols-3 gap-1 text-center mb-2">
        {[
          { l: 'Target Hit', v: `${tgtPct}%`,  c: '#10b981' },
          { l: 'Neutral',    v: `${neutPct}%`, c: '#6b7280' },
          { l: 'SL Hit',     v: `${slPct}%`,   c: '#ef4444' },
        ].map(({ l, v, c }) => (
          <div key={l}>
            <p className="text-[7px] text-zinc-600">{l}</p>
            <p className="text-[10px] font-black" style={{ color: c }}>{v}</p>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-1 text-center">
        {[
          { l: 'Exp P&L',  v: fmtInr(mc.expected_pnl_inr),  c: (mc.expected_pnl_inr || 0) >= 0 ? '#10b981' : '#ef4444' },
          { l: 'Qty',      v: mc.quantity || '—',             c: '#a78bfa' },
          { l: 'P5 P&L',   v: fmtInr(mc.pnl_p5),            c: '#ef4444' },
          { l: 'P95 P&L',  v: fmtInr(mc.pnl_p95),           c: '#10b981' },
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

// ── Top Picks bar ─────────────────────────────────────────────────────────────
function TopPicksBar({ picks, onSelectTicker }) {
  if (!picks?.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="top-picks">
      {picks.map(p => (
        <button
          key={p.ticker}
          onClick={() => onSelectTicker?.(p.ticker)}
          data-testid={`pick-${p.ticker}`}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-[9px] font-bold transition-all hover:scale-105"
          style={{
            background: p.consensus === 'BUY' ? 'rgba(16,185,129,0.12)' : p.consensus === 'SELL' ? 'rgba(239,68,68,0.12)' : 'rgba(113,113,122,0.1)',
            border: `1px solid ${p.consensus === 'BUY' ? 'rgba(16,185,129,0.3)' : p.consensus === 'SELL' ? 'rgba(239,68,68,0.3)' : 'rgba(113,113,122,0.2)'}`,
            color: p.consensus === 'BUY' ? '#10b981' : p.consensus === 'SELL' ? '#ef4444' : '#6b7280',
          }}
        >
          <span className="text-zinc-400 font-mono">{p.ticker.replace('.NS', '')}</span>
          <span>{p.consensus}</span>
          <span className="text-zinc-600">{p.confidence?.toFixed(0)}%</span>
        </button>
      ))}
    </div>
  );
}

// ── MAIN COMPONENT ────────────────────────────────────────────────────────────
export default function AgentDiscussionPanel({ capital = 100000 }) {
  const [discussion,     setDiscussion]     = useState(null);
  const [topPicks,       setTopPicks]       = useState([]);
  const [manualTickers,  setManualTickers]  = useState([]);
  const [scanMode,       setScanMode]       = useState('hybrid');
  const [loading,        setLoading]        = useState(false);
  const [scanLoading,    setScanLoading]    = useState(false);
  const [tickerInput,    setTickerInput]    = useState('');
  const [lastScanTime,   setLastScanTime]   = useState(null);
  const [deepMode,       setDeepMode]       = useState(false);

  // Fetch current discussion + manual tickers on mount
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

  React.useEffect(() => { fetchDiscussion(); }, [fetchDiscussion]);

  // Trigger collaborative scan
  const handleScan = async (ticker = null) => {
    setScanLoading(true);
    try {
      const payload = ticker
        ? { ticker, deep: deepMode }
        : { mode: scanMode, deep: deepMode };

      await axios.post(`${API}/robo/scan-now`, payload);
      toast.success(ticker ? `Analysing ${ticker}…` : `Scan started in ${scanMode} mode`);

      // Poll for results
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        await fetchDiscussion();
        if (attempts >= 15) clearInterval(poll);
      }, 2000);

    } catch (e) {
      toast.error(e.response?.data?.detail || 'Scan failed');
    } finally {
      setScanLoading(false);
    }
  };

  // Add manual ticker
  const handleAddTicker = async () => {
    const t = tickerInput.trim().toUpperCase();
    if (!t) return;
    try {
      const res = await axios.post(`${API}/robo/manual-stocks/add`, { ticker: t });
      if (res.data.success) {
        setManualTickers(res.data.manual_tickers || []);
        setTickerInput('');
        toast.success(`Added ${res.data.ticker}`);
      }
    } catch (e) {
      toast.error('Failed to add ticker');
    }
  };

  // Remove manual ticker
  const handleRemoveTicker = async (ticker) => {
    try {
      const res = await axios.post(`${API}/robo/manual-stocks/remove`, { ticker });
      if (res.data.success) {
        setManualTickers(res.data.manual_tickers || []);
        toast.success(`Removed ${ticker}`);
      }
    } catch { /* silent */ }
  };

  const disc = discussion;
  const signals = disc?.agent_signals || [];

  return (
    <div className="space-y-4" data-testid="agent-discussion-panel">

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[9px] text-zinc-500 uppercase tracking-widest font-bold">
            Agent Collaboration
          </p>
          {lastScanTime && (
            <p className="text-[8px] text-zinc-700 mt-0.5">
              Last scan: {new Date(lastScanTime).toLocaleTimeString('en-IN')}
            </p>
          )}
        </div>
        <button
          onClick={fetchDiscussion}
          className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-500 hover:text-white transition-all"
          data-testid="refresh-discussion-btn"
        >
          <RefreshCw size={11} />
        </button>
      </div>

      {/* ── Scan Controls ──────────────────────────────────────────────────── */}
      <div className="bg-zinc-900/60 border border-zinc-800/50 rounded-2xl p-3 space-y-3">

        {/* Scan mode + deep toggle */}
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
            title="Deep mode uses LangGraph AI (~10s)"
          >
            {deepMode ? '🧠 Deep AI' : 'Fast Mode'}
          </button>
        </div>

        {/* Manual ticker input */}
        <div className="flex gap-2">
          <div className="flex-1 flex items-center gap-2 bg-zinc-800/60 border border-zinc-700/40 rounded-xl px-3 py-1.5">
            <Search size={10} className="text-zinc-600 flex-shrink-0" />
            <input
              value={tickerInput}
              onChange={e => setTickerInput(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === 'Enter' && handleAddTicker()}
              placeholder="Add ticker e.g. TCS"
              className="bg-transparent text-[10px] text-white placeholder-zinc-600 outline-none w-full"
              data-testid="ticker-input"
            />
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
              background: 'linear-gradient(135deg, #7c3aed, #4f46e5)',
              boxShadow: scanLoading ? 'none' : '0 0 20px rgba(124,58,237,0.3)',
              color: 'white',
            }}
          >
            {scanLoading
              ? <RefreshCw size={11} className="animate-spin" />
              : <Activity size={11} />}
            {scanLoading ? 'Scanning…' : 'Scan Now'}
          </button>
        </div>

        {/* Manual ticker chips */}
        {manualTickers.length > 0 && (
          <div className="flex flex-wrap gap-1.5" data-testid="manual-tickers-list">
            {manualTickers.map(t => (
              <span
                key={t}
                className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-violet-900/20 border border-violet-700/30 text-violet-300 text-[9px] font-mono"
              >
                {t}
                <button
                  onClick={() => handleRemoveTicker(t)}
                  data-testid={`remove-${t}`}
                  className="text-violet-500 hover:text-red-400 transition-colors"
                >
                  <X size={8} />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ── Top Picks ──────────────────────────────────────────────────────── */}
      {topPicks.length > 0 && (
        <div>
          <p className="text-[8px] text-zinc-600 uppercase tracking-widest font-bold mb-1.5">
            Top Picks
          </p>
          <TopPicksBar picks={topPicks} onSelectTicker={(t) => handleScan(t)} />
        </div>
      )}

      {/* ── Consensus Summary ──────────────────────────────────────────────── */}
      {disc && (
        <div
          className="bg-zinc-900/60 border rounded-2xl p-4"
          style={{
            borderColor: disc.consensus === 'BUY' ? 'rgba(16,185,129,0.3)' : disc.consensus === 'SELL' ? 'rgba(239,68,68,0.3)' : 'rgba(113,113,122,0.2)',
            boxShadow: disc.consensus === 'BUY' ? '0 0 30px rgba(16,185,129,0.06)' : disc.consensus === 'SELL' ? '0 0 30px rgba(239,68,68,0.06)' : 'none',
          }}
          data-testid="consensus-summary"
        >
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-[8px] text-zinc-600 uppercase tracking-widest font-bold">
                Agent Consensus · {disc.ticker}
              </p>
              <p className="text-[8px] text-zinc-700 mt-0.5">
                {disc.bull_count} bull · {disc.bear_count} bear · {disc.hold_count} hold
                &nbsp;·&nbsp; score: {disc.weighted_score > 0 ? '+' : ''}{disc.weighted_score?.toFixed(1)}
              </p>
            </div>
            <SignalBadge signal={disc.consensus} confidence={disc.consensus_confidence} size="lg" />
          </div>

          {/* Bull/bear/hold bars */}
          <div className="flex gap-0 rounded-full overflow-hidden h-2 mb-3">
            {[
              { label: 'Bull', count: disc.bull_count, color: '#10b981' },
              { label: 'Hold', count: disc.hold_count, color: '#6b7280' },
              { label: 'Bear', count: disc.bear_count, color: '#ef4444' },
            ].map(({ label, count, color }) => {
              const total = (disc.agent_signals?.length || 1);
              return (
                <div
                  key={label}
                  style={{ width: `${(count / total) * 100}%`, background: color, opacity: 0.8 }}
                  title={`${label}: ${count}`}
                />
              );
            })}
          </div>

          {/* Dreamer V3 Final Thought */}
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

      {/* ── Agent Cards Grid ───────────────────────────────────────────────── */}
      {signals.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2" data-testid="agent-cards">
          {signals.map(sig => <AgentCard key={sig.agent_name} sig={sig} />)}
        </div>
      ) : (
        <div className="bg-zinc-900/40 border border-zinc-800/40 rounded-2xl flex items-center justify-center py-10">
          <div className="text-center">
            <div className="text-3xl mb-2">🤝</div>
            <p className="text-xs text-zinc-600">No agent discussion yet</p>
            <p className="text-[9px] text-zinc-700 mt-1">
              Click "Scan Now" to run multi-agent analysis
            </p>
          </div>
        </div>
      )}

      {/* ── Monte Carlo ────────────────────────────────────────────────────── */}
      {disc?.monte_carlo && Object.keys(disc.monte_carlo).length > 0 && (
        <MonteCarloCard mc={disc.monte_carlo} />
      )}

    </div>
  );
}
