/**
 * PaulTudorPanel — Paul Tudor Jones Macro + Trend Following Scanner
 * Part of "Top Trader Concepts" → TRADERS tab
 */
import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { TrendingUp, TrendingDown, Shield, Search, BarChart2 } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const STAGE_CONFIG = {
  PTJ_UPTREND: { label: 'PTJ UPTREND', color: '#f97316', bg: 'rgba(249,115,22,0.12)', border: 'rgba(249,115,22,0.3)'  },
  PTJ_WATCH:   { label: 'PTJ WATCH',   color: '#fbbf24', bg: 'rgba(251,191,36,0.10)', border: 'rgba(251,191,36,0.28)' },
  NO_MATCH:    { label: 'NO MATCH',    color: '#71717a', bg: 'rgba(113,113,122,0.08)', border: 'rgba(113,113,122,0.2)' },
};

function ScoreBar({ score }) {
  const color = score >= 76 ? '#f97316' : score >= 60 ? '#fbbf24' : '#f87171';
  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center">
        <span className="text-[9px] text-zinc-500 uppercase tracking-wider">PTJ Score</span>
        <span className="text-[11px] font-black font-mono" style={{ color }}>{score.toFixed(1)}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
        <div className="h-full rounded-full transition-all duration-700"
          style={{ width: `${Math.min(score, 100)}%`, background: color }} />
      </div>
    </div>
  );
}

function StatCell({ label, value, color = '#a1a1aa' }) {
  return (
    <div className="rounded-lg p-2 text-center"
      style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
      <p className="text-[8px] text-zinc-600 mb-0.5 leading-tight">{label}</p>
      <p className="text-[11px] font-black font-mono" style={{ color }}>{value ?? '—'}</p>
    </div>
  );
}

function MetricRow({ label, value, color }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-[8px] text-zinc-500">{label}</span>
      <span className="text-[9px] font-bold font-mono" style={{ color }}>{value}</span>
    </div>
  );
}

export default function PaulTudorPanel({ selectedStock }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [ticker,  setTicker]  = useState('');

  const runScan = useCallback(async () => {
    const sym = (ticker.trim() || selectedStock?.ticker || '').toUpperCase();
    if (!sym) { toast.error('Ticker enter karo ya pehle stock select karo'); return; }
    setLoading(true); setData(null);
    try {
      const res = await axios.post(`${API}/strategy/paul-tudor`, { ticker: sym });
      setData(res.data);
      res.data.is_match
        ? toast.success(`PTJ Uptrend — ${sym} · RR ${res.data.risk_reward_ratio}:1`)
        : toast.info(`PTJ Watch: ${res.data.reason?.slice(0, 60)}…`);
    } catch (e) {
      toast.error('Scan failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [ticker, selectedStock]);

  const stage = data ? STAGE_CONFIG[data.stage] || STAGE_CONFIG.NO_MATCH : null;
  const fmt   = v => v != null ? `₹${Number(v).toLocaleString('en-IN')}` : '—';

  return (
    <div className="rounded-xl border overflow-hidden"
      style={{ background: 'rgba(9,9,11,0.97)', borderColor: 'rgba(249,115,22,0.2)' }}
      data-testid="paul-tudor-panel">

      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b"
        style={{ borderColor: 'rgba(249,115,22,0.15)', background: 'rgba(249,115,22,0.04)' }}>
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded flex items-center justify-center"
            style={{ background: 'rgba(249,115,22,0.15)', border: '1px solid rgba(249,115,22,0.3)' }}>
            <Shield size={10} className="text-orange-400" />
          </div>
          <div>
            <span className="text-[11px] font-black text-orange-400 tracking-wide uppercase">Paul Tudor Jones</span>
            <span className="text-[8px] text-zinc-600 ml-2">Macro + Trend Following</span>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <input type="text"
            placeholder={selectedStock?.ticker || 'TATATECH.NS'}
            value={ticker}
            onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && runScan()}
            className="w-28 px-2 py-1 rounded text-[9px] font-mono bg-zinc-900 border border-zinc-700 text-zinc-200 placeholder-zinc-600 outline-none focus:border-orange-600"
            data-testid="ptj-ticker-input"
          />
          <button data-testid="ptj-scan-btn" onClick={runScan} disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[9px] font-black transition-all disabled:opacity-50"
            style={{
              background: loading ? 'rgba(107,114,128,0.2)' : 'rgba(249,115,22,0.15)',
              color:      loading ? '#6b7280' : '#f97316',
              border:     `1px solid ${loading ? 'rgba(107,114,128,0.3)' : 'rgba(249,115,22,0.35)'}`,
            }}>
            {loading
              ? <><span className="w-2.5 h-2.5 border border-t-orange-400 border-zinc-600 rounded-full animate-spin" /> SCANNING…</>
              : <><Search size={9} /> SCAN</>}
          </button>
        </div>
      </div>

      {/* Empty */}
      {!data && !loading && (
        <div className="flex flex-col items-center justify-center py-7 gap-2">
          <BarChart2 size={22} className="text-zinc-700" />
          <p className="text-[10px] text-zinc-600">Click SCAN to run PTJ analysis</p>
          <p className="text-[8px] text-zinc-700 text-center max-w-[230px]">
            200-day MA rule · Asymmetric risk-reward · Macro proxy · Volume breakout
          </p>
        </div>
      )}

      {loading && (
        <div className="flex flex-col items-center justify-center py-7 gap-2">
          <span className="w-6 h-6 border-2 border-t-orange-400 border-zinc-700 rounded-full animate-spin" />
          <p className="text-[10px] text-zinc-500">Fetching 300 days + macro proxy…</p>
        </div>
      )}

      {data && !loading && (
        <div className="px-3 py-3 space-y-3">

          {/* Stage + symbol */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-black text-white">{data.symbol}</span>
              <span className="text-[8px] font-black px-2 py-0.5 rounded-full"
                style={{ background: stage.bg, color: stage.color, border: `1px solid ${stage.border}` }}
                data-testid="ptj-stage-badge">
                {stage.label}
              </span>
            </div>
            {data.current_price && (
              <span className="text-[9px] font-mono text-zinc-400">{fmt(data.current_price)}</span>
            )}
          </div>

          {/* Score bar */}
          <ScoreBar score={data.confluence_score} />

          {/* Key PTJ metrics */}
          <div className="rounded-lg px-3 py-2 space-y-1"
            style={{ background: 'rgba(249,115,22,0.05)', border: '1px solid rgba(249,115,22,0.12)' }}>
            <MetricRow
              label="200-day MA"
              value={data.above_ma200 ? 'ABOVE (Bullish)' : 'BELOW (Defensive)'}
              color={data.above_ma200 ? '#34d399' : '#f87171'}
            />
            <MetricRow
              label="Risk:Reward"
              value={`${data.risk_reward_ratio?.toFixed(2)}:1`}
              color={data.risk_reward_ratio >= 3 ? '#34d399' : '#fbbf24'}
            />
            <MetricRow
              label="Trend Strength"
              value={`${data.trend_strength?.toFixed(1)}%`}
              color={data.trend_strength >= 70 ? '#34d399' : '#fbbf24'}
            />
            <MetricRow
              label="Macro Score"
              value={`${data.macro_score?.toFixed(1)}`}
              color={data.macro_score >= 70 ? '#34d399' : data.macro_score >= 50 ? '#fbbf24' : '#f87171'}
            />
          </div>

          {/* Entry / SL / Target */}
          {data.is_match && (
            <div className="grid grid-cols-3 gap-1.5">
              <StatCell label="Entry"         value={fmt(data.entry_price)} color="#34d399" />
              <StatCell label="Stop Loss"     value={fmt(data.stop_loss)}   color="#f87171" />
              <StatCell label="Target (+25%)" value={fmt(data.target)}      color="#f97316" />
            </div>
          )}

          {/* Signals */}
          {data.strength_signals?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {data.strength_signals.map((s, i) => (
                <span key={i} className="text-[8px] px-2 py-0.5 rounded-full font-semibold"
                  style={{ background: 'rgba(249,115,22,0.1)', color: '#f97316', border: '1px solid rgba(249,115,22,0.25)' }}>
                  {s}
                </span>
              ))}
            </div>
          )}

          {/* Reason */}
          <p className="text-[8px] text-zinc-500 leading-relaxed border-l-2 border-zinc-800 pl-2">
            {data.reason}
          </p>

          <p className="text-[7px] text-zinc-700">{data.timestamp?.slice(0, 10)}</p>
        </div>
      )}
    </div>
  );
}
