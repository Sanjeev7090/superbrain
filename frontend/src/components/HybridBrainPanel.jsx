import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Brain, Spinner, ArrowsClockwise, Warning, ShieldCheck, TrendUp, Pulse } from '@phosphor-icons/react';
import { X } from '@phosphor-icons/react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api/hybrid-brain`;

const SYMBOLS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX'];

const ACTION_STYLE = {
  BUY:  { color: 'text-emerald-400', bg: 'bg-emerald-500/15', border: 'border-emerald-500/40', label: 'BUY'  },
  SELL: { color: 'text-rose-400',    bg: 'bg-rose-500/15',    border: 'border-rose-500/40',    label: 'SELL' },
  HOLD: { color: 'text-amber-400',   bg: 'bg-amber-500/15',   border: 'border-amber-500/40',   label: 'HOLD' },
};

const fearColor = (f) => f >= 0.7 ? 'text-rose-400' : f >= 0.4 ? 'text-amber-400' : 'text-emerald-400';
const fearLabel = (f) => f >= 0.8 ? 'EXTREME' : f >= 0.5 ? 'HIGH' : f >= 0.25 ? 'MODERATE' : 'CALM';

const HybridBrainPanel = ({ onClose }) => {
  const [symbol, setSymbol]       = useState('NIFTY');
  const [news, setNews]           = useState('');
  const [decision, setDecision]   = useState(null);
  const [state, setState]         = useState(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [pnlInput, setPnlInput]   = useState('');
  const [history, setHistory]     = useState([]);

  const loadState = async () => {
    try {
      const res = await axios.get(`${API}/state`);
      setState(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    }
  };

  const loadHistory = async () => {
    try {
      const res = await axios.get(`${API}/audit`, { params: { limit: 20 } });
      setHistory(res.data.decisions || []);
    } catch (e) { /* silent */ }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, h] = await Promise.all([
          axios.get(`${API}/state`),
          axios.get(`${API}/audit`, { params: { limit: 20 } }),
        ]);
        if (!cancelled) {
          setState(s.data);
          setHistory(h.data.decisions || []);
        }
      } catch (e) {
        if (!cancelled) setError(e?.response?.data?.detail || e.message);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const decide = async () => {
    setLoading(true); setError(null);
    try {
      const res = await axios.post(`${API}/decide`, { symbol, news }, { timeout: 30000 });
      setDecision(res.data);
      loadHistory();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Decision failed');
    } finally { setLoading(false); }
  };

  const submitPnL = async () => {
    if (!pnlInput) return;
    const v = parseFloat(pnlInput) / 100.0;
    if (Number.isNaN(v)) return;
    try {
      const res = await axios.post(`${API}/update-pnl`, { pnl_pct: v });
      setState(res.data);
      setPnlInput('');
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    }
  };

  const resetDay = async () => {
    try {
      const res = await axios.post(`${API}/reset-daily`);
      setState(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    }
  };

  const act = decision ? (ACTION_STYLE[decision.action] || ACTION_STYLE.HOLD) : null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4" onClick={onClose}>
      <div
        className="bg-slate-900 border border-white/10 rounded-xl w-full max-w-6xl max-h-[95vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
        data-testid="hybrid-brain-modal"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10 bg-gradient-to-r from-fuchsia-900/40 via-indigo-900/40 to-cyan-900/30">
          <div className="flex items-center gap-2">
            <Brain size={24} weight="fill" className="text-fuchsia-400" />
            <div>
              <h2 className="text-lg font-bold text-white">Hybrid Super Brain</h2>
              <p className="text-xs text-slate-400">Dreamer + Psychology + Survival — fused decision engine</p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1" data-testid="brain-close-btn">
            <X size={22} />
          </button>
        </div>

        {/* Toolbar */}
        <div className="px-5 py-3 border-b border-white/10 flex items-center gap-2 flex-wrap">
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="bg-slate-800 border border-white/10 rounded px-2 py-1.5 text-sm text-white"
            data-testid="brain-symbol-select"
          >
            {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          <input
            value={news}
            onChange={(e) => setNews(e.target.value)}
            placeholder="(optional) paste headline / news for narrative scoring"
            className="flex-1 min-w-[200px] bg-slate-800 border border-white/10 rounded px-3 py-1.5 text-sm text-white placeholder-slate-500"
            data-testid="brain-news-input"
          />

          <button
            onClick={decide}
            disabled={loading}
            data-testid="brain-decide-btn"
            className="px-4 py-1.5 rounded bg-gradient-to-r from-fuchsia-500 to-indigo-500 text-white font-semibold flex items-center gap-2 hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition text-sm"
          >
            {loading ? <Spinner size={16} className="animate-spin" /> : <Pulse size={16} weight="bold" />}
            {loading ? 'Thinking…' : 'Decide'}
          </button>

          <button onClick={() => { loadState(); loadHistory(); }} className="p-1.5 text-slate-400 hover:text-white" title="Refresh state">
            <ArrowsClockwise size={18} />
          </button>
        </div>

        {error && <div className="px-5 py-2 text-xs text-rose-400 border-b border-white/5">⚠ {error}</div>}

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 grid gap-4 lg:grid-cols-3">
          {/* LEFT: State & PnL controls */}
          <div className="space-y-3 lg:col-span-1">
            <div className="bg-slate-800/40 rounded-lg p-4 border border-white/5">
              <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Survival State</div>
              {state ? (
                <>
                  <div className="flex items-end justify-between">
                    <div>
                      <div className="text-[10px] text-slate-500">FEAR LEVEL</div>
                      <div className={`text-3xl font-bold ${fearColor(state.fear_level)}`}>
                        {(state.fear_level * 100).toFixed(0)}%
                      </div>
                      <div className={`text-[10px] font-bold ${fearColor(state.fear_level)}`}>
                        {fearLabel(state.fear_level)}
                      </div>
                    </div>
                    {state.fear_level >= 0.5
                      ? <Warning size={32} weight="fill" className="text-rose-400" />
                      : <ShieldCheck size={32} weight="fill" className="text-emerald-400" />}
                  </div>
                  <div className="w-full h-2 bg-slate-700 rounded-full mt-2 overflow-hidden">
                    <div
                      className={`h-full transition-all duration-500 ${state.fear_level >= 0.7 ? 'bg-rose-500' : state.fear_level >= 0.4 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                      style={{ width: `${Math.min(100, state.fear_level * 100)}%` }}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-2 mt-3 text-xs">
                    <Stat label="Daily Target" value={`${state.daily_target_pct.toFixed(2)}%`} />
                    <Stat label="Today P&L"    value={`${state.current_pnl_pct.toFixed(2)}%`} valueClass={state.current_pnl_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'} />
                    <Stat label="Consec Miss"  value={state.consecutive_fail} />
                    <Stat label="Grace Days"   value={state.grace_days} />
                  </div>
                </>
              ) : (
                <div className="text-xs text-slate-500">Loading…</div>
              )}
            </div>

            <div className="bg-slate-800/40 rounded-lg p-4 border border-white/5">
              <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Update P&amp;L</div>
              <div className="flex gap-2">
                <input
                  type="number"
                  step="0.01"
                  value={pnlInput}
                  onChange={(e) => setPnlInput(e.target.value)}
                  placeholder="e.g. 0.42  (% return)"
                  className="flex-1 bg-slate-900 border border-white/10 rounded px-2 py-1.5 text-sm text-white"
                  data-testid="brain-pnl-input"
                />
                <button onClick={submitPnL} className="px-3 py-1.5 rounded bg-emerald-500 hover:bg-emerald-400 text-white text-xs font-semibold">Apply</button>
              </div>
              <button onClick={resetDay} className="mt-2 w-full text-xs px-2 py-1.5 rounded border border-white/10 text-slate-300 hover:bg-white/5">
                Reset Daily (overnight decay)
              </button>
            </div>

            <div className="bg-slate-800/40 rounded-lg p-4 border border-white/5">
              <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Recent Decisions</div>
              <div className="space-y-1 max-h-[260px] overflow-y-auto">
                {history.length === 0 && <div className="text-xs text-slate-500">No history yet.</div>}
                {history.map((h) => {
                  const a = ACTION_STYLE[h.action] || ACTION_STYLE.HOLD;
                  return (
                    <div key={h.id} className={`flex items-center justify-between text-xs px-2 py-1 rounded ${a.bg} border ${a.border}`}>
                      <div className="flex items-center gap-2">
                        <span className={`font-bold ${a.color}`}>{h.action}</span>
                        <span className="text-slate-300">{h.symbol}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-slate-400">{h.confidence}%</span>
                        <span className="text-slate-500 text-[10px]">{new Date(h.timestamp).toLocaleTimeString()}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* RIGHT: Latest decision detail */}
          <div className="lg:col-span-2 space-y-3">
            {!decision && !loading && (
              <div className="text-center py-16 text-slate-400">
                <Brain size={48} className="mx-auto mb-3 text-fuchsia-400/50" />
                <p className="text-sm">Click <b>Decide</b> to fuse Dreamer · Psychology · Survival into an action.</p>
              </div>
            )}

            {loading && (
              <div className="text-center py-16 text-slate-400">
                <Spinner size={42} className="mx-auto animate-spin text-fuchsia-400 mb-3" />
                <p className="text-sm">Pulling live market context · running psychology harvester…</p>
              </div>
            )}

            {decision && (
              <>
                {/* Action banner */}
                <div className={`rounded-xl p-5 border ${act.border} ${act.bg}`} data-testid="brain-decision-card">
                  <div className="flex items-start justify-between flex-wrap gap-3">
                    <div>
                      <div className="text-xs uppercase tracking-wider text-slate-400">Decision · {decision.symbol}</div>
                      <div className={`text-4xl font-bold ${act.color} flex items-center gap-2 mt-1`}>
                        <TrendUp size={28} weight="bold" />
                        {act.label}
                      </div>
                      <div className="text-sm text-slate-300 mt-1">{decision.reasoning}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-xs text-slate-400">Confidence</div>
                      <div className="text-4xl font-bold text-white">{decision.confidence}%</div>
                      <div className="text-xs text-slate-500">Size scalar × {decision.size_scalar}</div>
                    </div>
                  </div>
                </div>

                {/* Component breakdown */}
                <div className="bg-slate-800/40 rounded-lg p-4 border border-white/5">
                  <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Confidence Stack</div>
                  <div className="space-y-1.5">
                    {Object.entries(decision.components || {}).map(([k, v]) => (
                      <div key={k} className="flex items-center justify-between text-xs">
                        <span className="text-slate-400 capitalize">{k.replace(/_/g, ' ')}</span>
                        <span className={`font-mono font-semibold ${v >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {v >= 0 ? '+' : ''}{v}
                        </span>
                      </div>
                    ))}
                    <div className="border-t border-white/5 pt-1.5 mt-1.5 flex items-center justify-between text-xs">
                      <span className="text-slate-300 font-semibold">Final</span>
                      <span className="text-white font-bold">{decision.confidence}%</span>
                    </div>
                  </div>
                </div>

                {/* Psych + Survival side-by-side */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="bg-slate-800/40 rounded-lg p-4 border border-white/5">
                    <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Psychology</div>
                    <Gauge label="FOMO"      value={decision.psych.fomo_score}             tone={decision.psych.fomo_score   > 0.7 ? 'rose' : 'emerald'} />
                    <Gauge label="Apathy"    value={decision.psych.apathy_score}           tone={decision.psych.apathy_score > 0.6 ? 'amber' : 'slate'} />
                    <Gauge label="Narrative" value={decision.psych.narrative_credibility}  tone={decision.psych.narrative_credibility < 0.5 ? 'amber' : 'emerald'} />
                    <div className="mt-2 flex items-center justify-between text-xs">
                      <span className="text-slate-400">Regime</span>
                      <span className="text-violet-300 font-semibold">{decision.psych.regime}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-xs">
                      <span className="text-slate-400">Volatility</span>
                      <span className="text-slate-200">{(decision.psych.volatility * 100).toFixed(2)}%</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-xs">
                      <span className="text-slate-400">Volume Thrust</span>
                      <span className="text-slate-200">×{decision.psych.volume_thrust.toFixed(2)}</span>
                    </div>
                    <div className="mt-2 text-[11px] text-slate-300 italic">
                      💡 {decision.psych.hidden_value_gap}
                    </div>
                  </div>

                  <div className="bg-slate-800/40 rounded-lg p-4 border border-white/5">
                    <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Survival</div>
                    <div className="text-2xl font-bold text-white">{(decision.survival.fear * 100).toFixed(0)}% Fear</div>
                    <div className={`text-xs ${decision.survival.status === 'good' ? 'text-emerald-400' : decision.survival.status === 'danger' ? 'text-rose-400' : 'text-amber-400'}`}>
                      Status: {decision.survival.status.toUpperCase()}
                    </div>
                    <div className="grid grid-cols-2 gap-2 mt-3 text-xs">
                      <Stat label="Consec Miss" value={decision.survival.consecutive_fail} />
                      <Stat label="Target"      value={`${decision.survival.target_pct.toFixed(2)}%`} />
                      <Stat label="Actual"      value={`${decision.survival.actual_pct.toFixed(2)}%`}
                             valueClass={decision.survival.actual_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'} />
                      <Stat label="Risk Alert"  value={decision.risk_alert} />
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

const Stat = ({ label, value, valueClass = 'text-white' }) => (
  <div>
    <div className="text-[10px] text-slate-500 uppercase">{label}</div>
    <div className={`font-semibold ${valueClass}`}>{value}</div>
  </div>
);

const TONE = {
  emerald: 'bg-emerald-500',
  rose:    'bg-rose-500',
  amber:   'bg-amber-500',
  slate:   'bg-slate-500',
};
const Gauge = ({ label, value, tone = 'slate' }) => (
  <div className="mb-2">
    <div className="flex items-center justify-between text-[11px] text-slate-300 mb-1">
      <span>{label}</span>
      <span className="font-mono">{(value * 100).toFixed(0)}%</span>
    </div>
    <div className="w-full h-1.5 bg-slate-700 rounded-full overflow-hidden">
      <div className={`h-full ${TONE[tone]} transition-all duration-500`} style={{ width: `${Math.min(100, value * 100)}%` }} />
    </div>
  </div>
);

export default HybridBrainPanel;
