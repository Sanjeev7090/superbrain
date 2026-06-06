/**
 * TargetCapitalSettings — Phase 4
 * Full-screen modal for editing Daily Profit Target and Allocated Capital.
 * Shows live risk preview (Kelly, VaR, feasibility) on change.
 */
import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { X, TrendingUp, Wallet, Shield, AlertTriangle } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const fmt    = (v, d = 0) => v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtInr = v => `₹${fmt(v, 0)}`;
const fmtPct = (v, d = 1) => v == null ? '—' : `${Number(v).toFixed(d)}%`;

const RISK_LEVELS = [
  { key: 'conservative', label: 'Conservative', icon: '🛡️', desc: 'Lower risk, lower returns. Tight SL.' },
  { key: 'moderate',     label: 'Moderate',     icon: '⚖️', desc: 'Balanced risk/reward. Recommended.' },
  { key: 'aggressive',   label: 'Aggressive',   icon: '⚡', desc: 'Higher risk, higher target. Wider SL.' },
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

export default function TargetCapitalSettings({ settings, onSave, onClose }) {
  const [form, setForm]           = useState({ ...settings });
  const [preview, setPreview]     = useState(null);
  const [prevLoading, setPrevLoading] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const [error, setError]         = useState(null);
  const debounceRef               = useRef(null);

  // Auto-preview on input change (debounced 600ms)
  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(fetchPreview, 600);
    return () => clearTimeout(debounceRef.current);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.daily_profit_target, form.allocated_capital, form.risk_tolerance]);

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

  const handleSave = async () => {
    setSaveLoading(true);
    setError(null);
    try {
      await axios.post(`${API}/robo/settings`, {
        daily_profit_target: Number(form.daily_profit_target),
        allocated_capital:   Number(form.allocated_capital),
        ticker:              form.ticker,
        risk_tolerance:      form.risk_tolerance,
      });
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
              <label className="block text-xs font-bold text-zinc-300 mb-2">Primary Ticker</label>
              <input
                type="text"
                value={form.ticker}
                onChange={e => setForm(f => ({ ...f, ticker: e.target.value.toUpperCase() }))}
                className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-2.5 text-white text-sm font-mono focus:outline-none focus:border-violet-500 transition-colors"
                placeholder="e.g. RELIANCE.NS"
                data-testid="ticker-input"
              />
            </div>
            <div>
              <label className="block text-xs font-bold text-zinc-300 mb-2">Risk Tolerance</label>
              <div className="flex gap-1.5">
                {RISK_LEVELS.map(({ key, label, icon }) => (
                  <button
                    key={key}
                    onClick={() => setForm(f => ({ ...f, risk_tolerance: key }))}
                    className={`flex-1 py-2 rounded-xl text-[10px] font-bold border transition-all ${
                      form.risk_tolerance === key
                        ? 'bg-violet-600/20 border-violet-500/50 text-violet-300'
                        : 'bg-zinc-900 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                    }`}
                    title={RISK_LEVELS.find(r => r.key === key)?.desc}
                  >
                    {icon} {label}
                  </button>
                ))}
              </div>
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
