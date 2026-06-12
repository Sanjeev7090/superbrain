/**
 * TargetCapitalSettings — Phase 4
 * Full-screen modal for editing Daily Profit Target and Allocated Capital.
 * Shows live risk preview (Kelly, VaR, feasibility) on change.
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { X, TrendingUp, Wallet, Shield, AlertTriangle, Search, Plus, Trash2, Layers, Zap, RefreshCw, TrendingDown, Minus } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const fmt    = (v, d = 0) => v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtInr = v => `₹${fmt(v, 0)}`;
const fmtPct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`;

const RISK_LEVELS = [
  { key: 'conservative', label: 'Conservative', icon: 'shield',     desc: 'Lower risk, lower returns. Tight SL.', color: '#10b981' },
  { key: 'moderate',     label: 'Moderate',     icon: 'scale',      desc: 'Balanced risk/reward. Recommended.', color: '#3b82f6' },
  { key: 'aggressive',   label: 'Aggressive',   icon: 'zap',        desc: 'Higher risk, higher target. Wider SL.', color: '#f59e0b' },
  { key: 'danger',       label: 'Danger',       icon: 'skull',      desc: 'F&O Universe Only · PCR Priority · No direct stock trades.', color: '#ef4444', warn: true },
];

const TARGET_PRESETS  = [250, 500, 1000, 2000, 5000];
const CAPITAL_PRESETS = [25000, 50000, 100000, 200000, 500000];

function FeasibilityBadge({ score, label, color }) {
  if (score == null) return null;
  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded-xl"
      style={{ background: color + '12', border: `1px solid ${color}30` }}
    >
      <div className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-black"
        style={{ background: color + '20', color }}>
        {score}
      </div>
      <div>
        <p className="text-xs font-bold" style={{ color }}>{label}</p>
        <p className="text-[9px] text-zinc-500">Feasibility score / 100</p>
      </div>
    </div>
  );
}

export default function TargetCapitalSettings({ settings, onSave, onClose, onSelectStock }) {
  const [form, setForm]           = useState({ ...settings });
  const [preview, setPreview]     = useState(null);
  const [prevLoading, setPrevLoading] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const [error, setError]         = useState(null);
  const debounceRef               = useRef(null);

  // Ticker search state
  const [tickerQuery,   setTickerQuery]   = useState(settings.ticker || '');
  const [tickerResults, setTickerResults] = useState([]);
  const [tickerLoading, setTickerLoading] = useState(false);

  // Watchlist state
  const [watchlist,         setWatchlist]         = useState([]);
  const [maxParallel,       setMaxParallel]        = useState(3);
  const [wlInput,           setWlInput]            = useState('');
  const [wlResults,         setWlResults]          = useState([]);
  const [wlLoading,         setWlLoading]          = useState(false);
  const wlDebounceRef = useRef(null);

  // Auto-Discover state
  const [discovering,   setDiscovering]   = useState(false);
  const [discovered,    setDiscovered]    = useState([]);
  const [discoverErr,   setDiscoverErr]   = useState(null);
  const [showDiscover,  setShowDiscover]  = useState(false);

  // Auto-preview on input change (debounced 600ms)
  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(fetchPreview, 600);
    return () => clearTimeout(debounceRef.current);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.daily_profit_target, form.allocated_capital, form.risk_tolerance]);

  // Load existing watchlist on mount
  useEffect(() => {
    axios.get(`${API}/robo/watchlist`).then(r => {
      setWatchlist(r.data.watchlist || []);
      setMaxParallel(r.data.max_parallel_trades || 3);
    }).catch(() => {});
  }, []);

  const fetchPreview = async () => {
    setPrevLoading(true);
    setError(null);
    try {
      const res = await axios.post(`${API}/robo/risk-preview`, {
        daily_profit_target: Number(form.daily_profit_target) || 1000,
        allocated_capital:   Number(form.allocated_capital)   || 100000,
        risk_tolerance:      form.risk_tolerance || 'moderate',
      });
      setPreview(res.data.preview);
    } catch {
      setPreview(null);
    } finally {
      setPrevLoading(false);
    }
  };

  // Ticker search handler
  const handleTickerChange = useCallback((val) => {
    setTickerQuery(val);
    setForm(f => ({ ...f, ticker: val.toUpperCase() }));
  }, []);

  // Debounced ticker search via useEffect (proper React pattern)
  useEffect(() => {
    if (tickerQuery.length < 1) { setTickerResults([]); return; }
    const timer = setTimeout(async () => {
      setTickerLoading(true);
      try {
        const res = await axios.get(`${API}/stock/search`, { params: { q: tickerQuery } });
        setTickerResults(res.data.results || []);
      } catch { setTickerResults([]); } finally { setTickerLoading(false); }
    }, 400);
    return () => clearTimeout(timer);
  }, [tickerQuery]);

  // Debounced watchlist ticker search
  useEffect(() => {
    if (wlInput.length < 1) { setWlResults([]); return; }
    clearTimeout(wlDebounceRef.current);
    wlDebounceRef.current = setTimeout(async () => {
      setWlLoading(true);
      try {
        const res = await axios.get(`${API}/stock/search`, { params: { q: wlInput } });
        setWlResults(res.data.results || []);
      } catch { setWlResults([]); } finally { setWlLoading(false); }
    }, 400);
    return () => clearTimeout(wlDebounceRef.current);
  }, [wlInput]);

  const addToWatchlist = (ticker) => {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    const final = t.endsWith('.NS') || t.endsWith('.BO') ? t : t + '.NS';
    if (!watchlist.includes(final)) {
      setWatchlist(prev => [...prev, final]);
      // Auto-set as Primary Ticker if none set yet
      if (!form.ticker || form.ticker.trim() === '') {
        setTickerQuery(final);
        setForm(f => ({ ...f, ticker: final }));
      }
    }
    setWlInput('');
    setWlResults([]);
  };

  const removeFromWatchlist = async (t) => {
    const updated = watchlist.filter(x => x !== t);
    setWatchlist(updated);
    // Immediately persist to backend so removal survives without clicking Save
    try {
      await axios.post(`${API}/robo/watchlist`, {
        watchlist:           updated,
        max_parallel_trades: maxParallel,
      });
    } catch { /* silent — main save will re-sync */ }
  };

  const runAutoDiscover = async (forceRefresh = false) => {
    setDiscovering(true);
    setDiscoverErr(null);
    setShowDiscover(true);
    try {
      const res = await axios.get(`${API}/robo/watchlist/discover${forceRefresh ? '?refresh=true' : ''}`);
      setDiscovered(res.data.candidates || []);
    } catch (e) {
      setDiscoverErr('Scan failed — try again');
      setDiscovered([]);
    } finally {
      setDiscovering(false);
    }
  };

  const addTopN = (n) => {
    const toAdd = discovered.filter(c => c.direction === 'BUY').slice(0, n);
    if (toAdd.length === 0) {
      // fallback: just take top n by score
      discovered.slice(0, n).forEach(c => addToWatchlist(c.ticker));
    } else {
      toAdd.forEach(c => addToWatchlist(c.ticker));
    }
  };

  const handleSelectTicker = (stock) => {
    setTickerQuery(stock.ticker);
    setForm(f => ({ ...f, ticker: stock.ticker }));
    setTickerResults([]);
    // Also load in chart if callback available
    if (onSelectStock) onSelectStock(stock);
  };

  const handleSave = async () => {
    setSaveLoading(true);
    setError(null);
    try {
      await Promise.all([
        axios.post(`${API}/robo/settings`, {
          daily_profit_target: Number(form.daily_profit_target),
          allocated_capital:   Number(form.allocated_capital),
          ticker:              form.ticker,
          risk_tolerance:      form.risk_tolerance,
        }),
        axios.post(`${API}/robo/watchlist`, {
          watchlist:           watchlist,
          max_parallel_trades: maxParallel,
        }),
      ]);
      // ── Sync Primary Ticker → DreamerV3 RL ticker on save ──
      if (form.ticker && onSelectStock) {
        onSelectStock({ ticker: form.ticker, name: form.ticker.replace('.NS', '').replace('.BO', ''), type: 'stock' });
      }
      onSave?.();
      onClose?.();
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Save failed');
    } finally {
      setSaveLoading(false);
    }
  };

  const p = preview;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/80 backdrop-blur-sm overflow-y-auto py-6 px-4">
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl w-full max-w-2xl shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-zinc-800">
          <div>
            <h2 className="font-black text-white text-base flex items-center gap-2">
              <span className="text-violet-400">⚙</span> Robo-Trader Settings
            </h2>
            <p className="text-[10px] text-zinc-500 mt-0.5">Changes apply instantly — system recalculates automatically</p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-xl bg-zinc-800 hover:bg-zinc-700 flex items-center justify-center text-zinc-400 hover:text-white transition-colors"
            data-testid="settings-close-btn"
          >
            <X size={14} />
          </button>
        </div>

        <div className="p-5 space-y-5">
          {error && (
            <div className="bg-red-900/20 border border-red-700/40 rounded-xl px-4 py-2.5 text-red-400 text-sm flex items-center gap-2">
              <AlertTriangle size={14} />
              {error}
            </div>
          )}

          {/* Daily Target */}
          <div>
            <label className="flex items-center gap-1.5 text-xs font-bold text-zinc-300 mb-2">
              <TrendingUp size={12} className="text-emerald-400" />
              Daily Profit Target (₹)
            </label>
            <input
              type="number"
              value={form.daily_profit_target}
              onChange={e => setForm(f => ({ ...f, daily_profit_target: e.target.value }))}
              className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-2.5 text-white text-sm font-semibold focus:outline-none focus:border-emerald-500 transition-colors"
              placeholder="e.g. 1000"
              min="1"
              data-testid="target-input"
            />
            <div className="flex gap-1.5 mt-2">
              {TARGET_PRESETS.map(v => (
                <button
                  key={v}
                  onClick={() => setForm(f => ({ ...f, daily_profit_target: v }))}
                  className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold border transition-all ${
                    Number(form.daily_profit_target) === v
                      ? 'bg-emerald-600/20 border-emerald-500/50 text-emerald-400'
                      : 'bg-zinc-900 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                  }`}
                >
                  ₹{v >= 1000 ? `${v/1000}k` : v}
                </button>
              ))}
            </div>
          </div>

          {/* Allocated Capital */}
          <div>
            <label className="flex items-center gap-1.5 text-xs font-bold text-zinc-300 mb-2">
              <Wallet size={12} className="text-blue-400" />
              Allocated Capital (₹)
            </label>
            <input
              type="number"
              value={form.allocated_capital}
              onChange={e => setForm(f => ({ ...f, allocated_capital: e.target.value }))}
              className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-2.5 text-white text-sm font-semibold focus:outline-none focus:border-blue-500 transition-colors"
              placeholder="e.g. 100000"
              min="1000"
              data-testid="capital-input"
            />
            <div className="flex gap-1.5 mt-2">
              {CAPITAL_PRESETS.map(v => (
                <button
                  key={v}
                  onClick={() => setForm(f => ({ ...f, allocated_capital: v }))}
                  className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold border transition-all ${
                    Number(form.allocated_capital) === v
                      ? 'bg-blue-600/20 border-blue-500/50 text-blue-400'
                      : 'bg-zinc-900 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                  }`}
                >
                  {v >= 100000 ? `₹${v/100000}L` : `₹${v/1000}k`}
                </button>
              ))}
            </div>
          </div>

          {/* Ticker + Risk tolerance */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs font-bold text-zinc-300">Primary Ticker</label>
                {watchlist.length > 0 && (
                  <span className="text-[8px] text-zinc-500 font-medium">
                    {watchlist.length} from watchlist ↓
                  </span>
                )}
              </div>
              <div className="relative">
                <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-700 rounded-xl px-3 py-2.5 focus-within:border-violet-500 transition-colors">
                  <Search size={11} className="text-zinc-500 flex-shrink-0" />
                  <input
                    type="text"
                    value={tickerQuery}
                    onChange={e => handleTickerChange(e.target.value)}
                    onKeyDown={e => e.key === 'Escape' && setTickerResults([])}
                    onBlur={() => {
                      // Sync typed ticker to DreamerV3 RL on blur
                      const t = tickerQuery.trim().toUpperCase();
                      if (t && t.length >= 3 && onSelectStock && t !== form.ticker) {
                        const final = t.endsWith('.NS') || t.endsWith('.BO') ? t : t + '.NS';
                        onSelectStock({ ticker: final, name: final.replace('.NS', '').replace('.BO', ''), type: 'stock' });
                      }
                    }}
                    className="flex-1 bg-transparent text-white text-sm font-mono outline-none placeholder-zinc-600"
                    placeholder="e.g. RELIANCE.NS"
                    data-testid="ticker-input"
                  />
                  {tickerLoading && (
                    <span className="w-3 h-3 border border-zinc-600 border-t-violet-400 rounded-full animate-spin flex-shrink-0" />
                  )}
                </div>
                {tickerResults.length > 0 && (
                  <div className="absolute left-0 right-0 top-full mt-1 bg-zinc-900 border border-zinc-700 rounded-xl overflow-hidden z-50 max-h-52 overflow-y-auto shadow-2xl">
                    {tickerResults.slice(0, 8).map((stock, i) => {
                      const badge = stock.type === 'INDEX' ? 'IDX' : (stock.exchange || 'NSE');
                      const badgeColor = stock.type === 'INDEX'
                        ? 'bg-amber-900/30 text-amber-400'
                        : stock.exchange === 'BSE'
                          ? 'bg-purple-900/30 text-purple-400'
                          : 'bg-emerald-900/30 text-emerald-400';
                      return (
                        <button
                          key={i}
                          onClick={() => handleSelectTicker(stock)}
                          className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-zinc-800 transition-colors border-b border-zinc-800/50 last:border-0"
                        >
                          <span className={`text-[8px] font-bold px-1.5 py-0.5 rounded font-mono flex-shrink-0 ${badgeColor}`}>
                            {badge}
                          </span>
                          <span className="text-xs font-mono font-bold text-white">{stock.ticker}</span>
                          <span className="text-[9px] text-zinc-500 truncate ml-auto">{stock.name}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Watchlist tickers shown as clickable Primary chips */}
              {watchlist.length > 0 && (
                <div className="mt-2">
                  <p className="text-[8px] text-zinc-600 mb-1.5 flex items-center gap-1">
                    <span className="w-1 h-1 rounded-full bg-violet-500 inline-block" />
                    Watchlist — click any to set as Primary
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {watchlist.map(t => {
                      const isActive = form.ticker === t;
                      return (
                        <button
                          key={t}
                          onClick={() => {
                            setTickerQuery(t);
                            setForm(f => ({ ...f, ticker: t }));
                            setTickerResults([]);
                            // ── Sync to DreamerV3 RL ticker automatically ──
                            if (onSelectStock) onSelectStock({ ticker: t, name: t.replace('.NS', '').replace('.BO', ''), type: 'stock' });
                          }}
                          data-testid={`primary-wl-chip-${t}`}
                          className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-mono font-bold transition-all"
                          style={{
                            background: isActive ? 'rgba(139,92,246,0.25)' : 'rgba(139,92,246,0.08)',
                            border: isActive ? '1px solid rgba(139,92,246,0.6)' : '1px solid rgba(139,92,246,0.2)',
                            color: isActive ? '#c4b5fd' : '#7c3aed',
                            boxShadow: isActive ? '0 0 8px rgba(139,92,246,0.25)' : 'none',
                          }}
                        >
                          {isActive && <span className="w-1 h-1 rounded-full bg-violet-400 animate-pulse" />}
                          {t.replace('.NS', '').replace('.BO', '')}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
            <div>
              <label className="block text-xs font-bold text-zinc-300 mb-2">Risk Tolerance</label>
              <div className="grid grid-cols-2 gap-1.5">
                {RISK_LEVELS.map(({ key, label, icon, desc, color, warn }) => {
                  const isActive = form.risk_tolerance === key;
                  const isDanger = key === 'danger';
                  return (
                    <button
                      key={key}
                      data-testid={`risk-level-${key}`}
                      onClick={() => setForm(f => ({ ...f, risk_tolerance: key }))}
                      className={`relative flex flex-col items-start px-3 py-2 rounded-xl border transition-all text-left ${
                        isActive
                          ? isDanger
                            ? 'bg-red-900/20 border-red-500/60 text-red-300'
                            : 'bg-violet-600/20 border-violet-500/50 text-violet-300'
                          : 'bg-zinc-900 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                      }`}
                      title={desc}
                      style={isActive && isDanger ? { boxShadow: '0 0 16px rgba(239,68,68,0.2)' } : undefined}
                    >
                      {isDanger && isActive && (
                        <span className="absolute -top-1.5 -right-1.5 text-[8px] font-black px-1.5 py-0.5 rounded-full bg-red-500 text-white animate-pulse">
                          F&O ONLY
                        </span>
                      )}
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span
                          className="w-4 h-4 rounded flex items-center justify-center"
                          style={{ background: isActive ? `${color}25` : 'transparent' }}
                        >
                          {icon === 'shield'  && <svg viewBox="0 0 24 24" className="w-3 h-3" fill="none" stroke={isActive ? color : '#71717a'} strokeWidth="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>}
                          {icon === 'scale'   && <svg viewBox="0 0 24 24" className="w-3 h-3" fill="none" stroke={isActive ? color : '#71717a'} strokeWidth="2.5"><path d="M12 3v18M5 6l7-3 7 3M4 10l8 4 8-4M3 14l9 4.5L21 14"/></svg>}
                          {icon === 'zap'     && <svg viewBox="0 0 24 24" className="w-3 h-3" fill="none" stroke={isActive ? color : '#71717a'} strokeWidth="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>}
                          {icon === 'skull'   && <svg viewBox="0 0 24 24" className="w-3 h-3" fill="none" stroke={isActive ? color : '#71717a'} strokeWidth="2.5"><path d="M12 2a9 9 0 0 1 9 9c0 3.6-2 6.8-5 8.3V21H8v-1.7C5 17.8 3 14.6 3 11a9 9 0 0 1 9-9zm-3 9a1.5 1.5 0 1 0 3 0 1.5 1.5 0 0 0-3 0zm6 0a1.5 1.5 0 1 0 3 0 1.5 1.5 0 0 0-3 0z"/></svg>}
                        </span>
                        <span className="text-[10px] font-bold" style={{ color: isActive ? color : undefined }}>
                          {label}
                        </span>
                      </div>
                      <span className="text-[8px] text-zinc-600 leading-tight">{
                        isDanger
                          ? 'F&O scan · PCR priority'
                          : desc.split('.')[0]
                      }</span>
                    </button>
                  );
                })}
              </div>

              {/* Danger mode notice */}
              {form.risk_tolerance === 'danger' && (
                <div
                  className="mt-2 rounded-xl px-3 py-2 border flex items-start gap-2"
                  style={{ background: 'rgba(239,68,68,0.08)', borderColor: 'rgba(239,68,68,0.3)' }}
                  data-testid="danger-mode-warning"
                >
                  <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 shrink-0 mt-0.5" fill="none" stroke="#ef4444" strokeWidth="2.5">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                  </svg>
                  <div>
                    <p className="text-[10px] font-black text-red-400 mb-0.5">Danger Mode Active</p>
                    <p className="text-[9px] text-red-300/70 leading-relaxed">
                      Direct equity trades disabled. Robot will auto-scan entire F&O universe (indices + 30 stocks),
                      prioritize by PCR signal strength, and pick the best opportunity each cycle.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* ── Multi-Stock Watchlist ─────────────────────────────────────── */}
          <div className="border border-zinc-800 rounded-xl overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-2.5 bg-zinc-900/50 border-b border-zinc-800">
              <Layers size={12} className="text-violet-400" />
              <p className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">
                Parallel Trading Watchlist
              </p>
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[9px] text-zinc-600">Max 10 tickers</span>
                <button
                  onClick={() => runAutoDiscover(false)}
                  disabled={discovering}
                  data-testid="auto-discover-btn"
                  className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[9px] font-bold border transition-all ${
                    discovering
                      ? 'bg-violet-900/30 border-violet-700/40 text-violet-400 cursor-wait'
                      : 'bg-violet-600/15 border-violet-600/40 text-violet-300 hover:bg-violet-600/25 hover:border-violet-500/60'
                  }`}
                >
                  <Zap size={8} className={discovering ? 'animate-pulse' : ''} />
                  {discovering ? 'Scanning...' : 'Auto-Discover'}
                </button>
              </div>
            </div>
            <div className="p-4 space-y-3">
              {/* Max parallel trades picker */}
              <div>
                <p className="text-[9px] text-zinc-500 uppercase tracking-wider mb-1.5 font-semibold">
                  Max Parallel Positions
                </p>
                <div className="flex gap-1.5">
                  {[1, 2, 3, 4, 5].map(n => (
                    <button
                      key={n}
                      onClick={() => setMaxParallel(n)}
                      data-testid={`parallel-btn-${n}`}
                      className={`flex-1 py-2 rounded-xl text-[10px] font-black border transition-all ${
                        maxParallel === n
                          ? 'bg-violet-600/20 border-violet-500/50 text-violet-300'
                          : 'bg-zinc-900 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                      }`}
                    >
                      {n}×
                    </button>
                  ))}
                </div>
                <p className="text-[9px] text-zinc-600 mt-1">
                  Robot will open up to {maxParallel} simultaneous position{maxParallel > 1 ? 's' : ''} across watchlist stocks
                </p>
              </div>

              {/* Watchlist chips */}
              {watchlist.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {watchlist.map(t => (
                    <span
                      key={t}
                      className="flex items-center gap-1 px-2 py-1 bg-violet-900/20 border border-violet-700/30 rounded-lg text-[10px] font-mono font-bold text-violet-300"
                    >
                      {t}
                      <button
                        onClick={() => removeFromWatchlist(t)}
                        className="text-zinc-500 hover:text-red-400 transition-colors"
                        data-testid={`remove-wl-${t}`}
                      >
                        <Trash2 size={9} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* ── Auto-Discover Results Panel ────────────────────────── */}
              {showDiscover && (
                <div className="border border-zinc-800 rounded-xl overflow-hidden bg-zinc-950/60">
                  <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-800/80">
                    <div className="flex items-center gap-1.5">
                      <Zap size={9} className="text-violet-400" />
                      <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-wider">
                        {discovering ? 'Scanning momentum...' : `${discovered.length} candidates found`}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {!discovering && discovered.length > 0 && (
                        <>
                          <button
                            onClick={() => addTopN(maxParallel)}
                            className="text-[8px] font-bold text-emerald-400 border border-emerald-700/40 bg-emerald-900/20 px-2 py-0.5 rounded-md hover:bg-emerald-800/30 transition-colors"
                            data-testid="add-top-n-btn"
                          >
                            + Add Top {maxParallel}
                          </button>
                          <button
                            onClick={() => runAutoDiscover(true)}
                            className="text-zinc-500 hover:text-zinc-300 transition-colors"
                            title="Rescan"
                            data-testid="rescan-btn"
                          >
                            <RefreshCw size={9} />
                          </button>
                        </>
                      )}
                      <button
                        onClick={() => setShowDiscover(false)}
                        className="text-zinc-600 hover:text-zinc-400 transition-colors"
                      >
                        <X size={9} />
                      </button>
                    </div>
                  </div>

                  {/* Loading skeleton */}
                  {discovering && (
                    <div className="p-2 space-y-1.5">
                      {[0,1,2,3,4].map(i => (
                        <div key={i} className="h-9 bg-zinc-800/50 rounded-lg animate-pulse" style={{animationDelay:`${i*100}ms`}} />
                      ))}
                    </div>
                  )}

                  {/* Error */}
                  {discoverErr && !discovering && (
                    <div className="p-3 text-center text-[10px] text-red-400">{discoverErr}</div>
                  )}

                  {/* Results list */}
                  {!discovering && discovered.length > 0 && (
                    <div className="divide-y divide-zinc-800/60 max-h-52 overflow-y-auto">
                      {discovered.map((c, i) => {
                        const isBuy  = c.direction === 'BUY';
                        const isSell = c.direction === 'SELL';
                        const inWl   = watchlist.includes(c.ticker);
                        return (
                          <div
                            key={c.ticker}
                            className="flex items-center gap-2 px-3 py-2 hover:bg-zinc-900/40 transition-colors"
                            style={{ animationDelay: `${i * 60}ms` }}
                            data-testid={`discover-card-${c.ticker}`}
                          >
                            {/* Rank */}
                            <span className="text-[8px] font-black text-zinc-700 w-3 text-center">{i+1}</span>

                            {/* Direction badge */}
                            <span className={`text-[7px] font-black px-1 py-0.5 rounded min-w-[28px] text-center ${
                              isBuy  ? 'bg-emerald-900/40 text-emerald-400 border border-emerald-700/30' :
                              isSell ? 'bg-red-900/40 text-red-400 border border-red-700/30' :
                                       'bg-zinc-800 text-zinc-500 border border-zinc-700'
                            }`}>
                              {c.direction}
                            </span>

                            {/* Name + sector */}
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1">
                                <span className="text-[10px] font-mono font-bold text-white truncate">{c.ticker.replace('.NS','')}</span>
                                <span className="text-[7px] text-zinc-600 truncate">{c.sector}</span>
                              </div>
                              {/* Score bar */}
                              <div className="flex items-center gap-1 mt-0.5">
                                <div className="h-1 flex-1 bg-zinc-800 rounded-full overflow-hidden">
                                  <div
                                    className={`h-full rounded-full transition-all duration-500 ${
                                      isBuy ? 'bg-emerald-500' : isSell ? 'bg-red-500' : 'bg-zinc-500'
                                    }`}
                                    style={{ width: `${Math.min(100, c.score)}%` }}
                                  />
                                </div>
                                <span className="text-[7px] text-zinc-500 whitespace-nowrap">{c.score.toFixed(0)}pt</span>
                              </div>
                            </div>

                            {/* Stats */}
                            <div className="text-right flex-shrink-0">
                              <div className={`text-[9px] font-bold ${c.chg_5d >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                {c.chg_5d >= 0 ? '+' : ''}{c.chg_5d?.toFixed(1)}%
                              </div>
                              <div className="text-[7px] text-zinc-600">
                                {c.vol_ratio?.toFixed(1)}× vol
                              </div>
                            </div>

                            {/* Add button */}
                            <button
                              onClick={() => addToWatchlist(c.ticker)}
                              disabled={inWl}
                              data-testid={`discover-add-${c.ticker}`}
                              className={`flex-shrink-0 text-[8px] font-bold px-1.5 py-0.5 rounded-md border transition-all ${
                                inWl
                                  ? 'text-zinc-600 border-zinc-700 cursor-default'
                                  : isBuy
                                  ? 'text-emerald-400 border-emerald-700/40 hover:bg-emerald-900/20'
                                  : 'text-violet-400 border-violet-700/40 hover:bg-violet-900/20'
                              }`}
                            >
                              {inWl ? '✓' : '+'}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Add ticker input */}
              <div className="relative">
                <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-700 rounded-xl px-3 py-2 focus-within:border-violet-500 transition-colors">
                  <Plus size={11} className="text-zinc-500 flex-shrink-0" />
                  <input
                    type="text"
                    value={wlInput}
                    onChange={e => setWlInput(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && wlInput.trim()) { addToWatchlist(wlInput); }
                      if (e.key === 'Escape') { setWlInput(''); setWlResults([]); }
                    }}
                    placeholder="Add ticker (e.g. TCS, INFY.NS)..."
                    className="flex-1 bg-transparent text-white text-xs font-mono outline-none placeholder-zinc-600"
                    data-testid="watchlist-input"
                  />
                  {wlLoading && <span className="w-3 h-3 border border-zinc-600 border-t-violet-400 rounded-full animate-spin flex-shrink-0" />}
                  {wlInput.trim() && (
                    <button
                      onClick={() => addToWatchlist(wlInput)}
                      className="text-[9px] font-bold text-violet-400 hover:text-violet-300 whitespace-nowrap"
                    >
                      Add
                    </button>
                  )}
                </div>
                {wlResults.length > 0 && (
                  <div className="absolute left-0 right-0 top-full mt-1 bg-zinc-900 border border-zinc-700 rounded-xl overflow-hidden z-50 max-h-40 overflow-y-auto shadow-2xl">
                    {wlResults.slice(0, 6).map((stock, i) => (
                      <button
                        key={i}
                        onClick={() => addToWatchlist(stock.ticker)}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-zinc-800 transition-colors border-b border-zinc-800/50 last:border-0"
                      >
                        <span className="text-[8px] font-bold px-1.5 py-0.5 rounded font-mono bg-emerald-900/30 text-emerald-400">{stock.exchange || 'NSE'}</span>
                        <span className="text-xs font-mono font-bold text-white">{stock.ticker}</span>
                        <span className="text-[9px] text-zinc-500 truncate ml-auto">{stock.name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <p className="text-[9px] text-zinc-600 italic">
                Primary ticker ({form.ticker || 'RELIANCE.NS'}) is always included automatically
              </p>
            </div>
          </div>

          {/* Live Preview */}
          <div className="border border-zinc-800 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2.5 bg-zinc-900/50 border-b border-zinc-800">
              <p className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">Live Risk Preview</p>
              {prevLoading && (
                <span className="animate-spin inline-block w-3 h-3 border border-zinc-500 border-t-violet-500 rounded-full" />
              )}
            </div>
            {p ? (
              <div className="p-4">
                <div className="flex items-center justify-between mb-4">
                  <FeasibilityBadge score={p.feasibility_score} label={p.feasibility_label} color={p.feasibility_color || '#f59e0b'} />
                  <div className="text-right">
                    <p className="text-[10px] text-zinc-500">Required daily return</p>
                    <p className="text-xl font-black" style={{ color: p.feasibility_color || '#f59e0b' }}>
                      {fmtPct(p.required_daily_return_pct)}
                    </p>
                  </div>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {[
                    { label: 'Position Size', value: fmtInr(p.position_size_inr),      color: '#3b82f6' },
                    { label: 'Max Daily Loss', value: fmtInr(p.daily_loss_limit),       color: '#ef4444' },
                    { label: 'VaR 95%',        value: fmtInr(p.var_95_inr),             color: '#f97316' },
                    { label: 'Kelly Fraction', value: fmtPct((p.kelly_fraction||0)*100, 2), color: '#a78bfa' },
                    { label: 'Win Rate Needed', value: fmtPct(p.required_win_rate_min, 0), color: '#06b6d4' },
                    { label: 'Vol Regime',     value: p.vol_regime || '—',              color: '#a1a1aa' },
                    { label: 'NSE History',    value: `${p.hist_exceedance_pct ?? '—'}% of days`, color: '#a1a1aa' },
                    { label: 'Budget State',   value: p.risk_budget_state || 'NORMAL',  color: p.risk_budget_state === 'STOP' ? '#ef4444' : '#10b981' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-zinc-900 rounded-lg px-2.5 py-2">
                      <p className="text-[8px] text-zinc-600 uppercase tracking-wide mb-0.5">{label}</p>
                      <p className="text-[11px] font-bold" style={{ color }}>{value}</p>
                    </div>
                  ))}
                </div>
                {p.feasibility_warnings?.length > 0 && (
                  <div className="mt-3 space-y-1">
                    {p.feasibility_warnings.map((w, i) => (
                      <div key={i} className="flex items-start gap-2 text-[10px] text-amber-400 bg-amber-900/15 border border-amber-700/20 rounded-lg px-3 py-1.5">
                        <AlertTriangle size={10} className="flex-shrink-0 mt-0.5" />
                        {w}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center py-6 text-zinc-600 text-xs">
                {prevLoading ? 'Calculating preview…' : 'Enter values to see risk preview'}
              </div>
            )}
          </div>

          {/* Risk disclaimer */}
          <div className="bg-amber-900/15 border border-amber-700/25 rounded-xl px-4 py-3 flex items-start gap-2.5">
            <Shield size={14} className="text-amber-400 flex-shrink-0 mt-0.5" />
            <p className="text-[10px] text-amber-400">
              <strong>DISCLAIMER:</strong> No guaranteed returns. Higher daily targets require
              higher risk. Always start with paper trading. Past performance ≠ future results.
              Consult a SEBI-registered investment advisor before live trading.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex gap-3 p-5 border-t border-zinc-800">
          <button
            onClick={onClose}
            className="flex-1 py-2.5 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-400 rounded-xl text-sm font-semibold transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saveLoading}
            className="flex-1 py-2.5 bg-violet-600 hover:bg-violet-500 text-white rounded-xl text-sm font-black transition-colors disabled:opacity-50"
            data-testid="settings-save-btn"
          >
            {saveLoading ? 'Saving…' : 'Save & Apply'}
          </button>
        </div>
      </div>
    </div>
  );
}
