/**
 * CANSLIMPanel — William O'Neil CANSLIM Strategy Scanner
 * Part of "Top Trader Concepts" → TRADERS tab
 */
import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { TrendingUp, Star, Search, BarChart2 } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const STAGE_CONFIG = {
  CANSLIM_LEADER: { label: 'CANSLIM LEADER', color: '#a78bfa', bg: 'rgba(167,139,250,0.12)', border: 'rgba(167,139,250,0.3)' },
  WEAK:           { label: 'WEAK',            color: '#f87171', bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.2)' },
  NO_MATCH:       { label: 'NO MATCH',         color: '#71717a', bg: 'rgba(113,113,122,0.08)', border: 'rgba(113,113,122,0.2)' },
};

/* CANSLIM letter breakdown config */
const LETTERS = [
  { key: 'C_A_Earnings',      label: 'C&A', desc: 'Earnings',      type: 'score' },
  { key: 'N_NewHigh',         label: 'N',   desc: 'New High',       type: 'bool'  },
  { key: 'S_Volume',          label: 'S',   desc: 'Volume',         type: 'score' },
  { key: 'L_RelativeStrength',label: 'L',   desc: 'Rel Strength',   type: 'score' },
  { key: 'I_Institutional',   label: 'I',   desc: 'Institutional',  type: 'score' },
  { key: 'M_MarketTrend',     label: 'M',   desc: 'Market',         type: 'trend' },
];

function letterColor(key, val) {
  if (key === 'N_NewHigh')    return val ? '#34d399' : '#f87171';
  if (key === 'M_MarketTrend') return val === 'UPTREND' ? '#34d399' : val === 'DOWNTREND' ? '#f87171' : '#fbbf24';
  const n = Number(val);
  return n >= 80 ? '#34d399' : n >= 65 ? '#fbbf24' : '#f87171';
}

function letterDisplay(key, val) {
  if (key === 'N_NewHigh')    return val ? 'YES' : 'NO';
  if (key === 'M_MarketTrend') return String(val);
  return Number(val).toFixed(0);
}

function ScoreBar({ score }) {
  const color = score >= 78 ? '#a78bfa' : score >= 60 ? '#fbbf24' : '#f87171';
  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center">
        <span className="text-[9px] text-zinc-500 uppercase tracking-wider">CANSLIM Score</span>
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

export default function CANSLIMPanel({ selectedStock }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [ticker,  setTicker]  = useState('');

  const runScan = useCallback(async () => {
    const sym = (ticker.trim() || selectedStock?.ticker || '').toUpperCase();
    if (!sym) { toast.error('Ticker enter karo ya pehle stock select karo'); return; }
    setLoading(true); setData(null);
    try {
      const res = await axios.post(`${API}/strategy/canslim`, { ticker: sym });
      setData(res.data);
      res.data.is_match
        ? toast.success(`CANSLIM Leader — ${sym} · Score ${res.data.confluence_score}%`)
        : toast.info(`Not a leader yet: ${res.data.reason?.slice(0, 60)}…`);
    } catch (e) {
      toast.error('Scan failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [ticker, selectedStock]);

  const stage = data ? STAGE_CONFIG[data.stage] || STAGE_CONFIG.NO_MATCH : null;
  const fmt   = v => v != null ? `₹${Number(v).toLocaleString('en-IN')}` : '—';
  const bd    = data?.canslim_breakdown || {};

  return (
    <div className="rounded-xl border overflow-hidden"
      style={{ background: 'rgba(9,9,11,0.97)', borderColor: 'rgba(167,139,250,0.2)' }}
      data-testid="canslim-panel">

      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b"
        style={{ borderColor: 'rgba(167,139,250,0.15)', background: 'rgba(167,139,250,0.04)' }}>
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded flex items-center justify-center"
            style={{ background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.3)' }}>
            <Star size={10} className="text-violet-400" />
          </div>
          <div>
            <span className="text-[11px] font-black text-violet-400 tracking-wide uppercase">O'Neil CANSLIM</span>
            <span className="text-[8px] text-zinc-600 ml-2">C·A·N·S·L·I·M</span>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <input type="text"
            placeholder={selectedStock?.ticker || 'TATATECH.NS'}
            value={ticker}
            onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && runScan()}
            className="w-28 px-2 py-1 rounded text-[9px] font-mono bg-zinc-900 border border-zinc-700 text-zinc-200 placeholder-zinc-600 outline-none focus:border-violet-600"
            data-testid="canslim-ticker-input"
          />
          <button data-testid="canslim-scan-btn" onClick={runScan} disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[9px] font-black transition-all disabled:opacity-50"
            style={{
              background: loading ? 'rgba(107,114,128,0.2)' : 'rgba(167,139,250,0.15)',
              color:      loading ? '#6b7280' : '#a78bfa',
              border:     `1px solid ${loading ? 'rgba(107,114,128,0.3)' : 'rgba(167,139,250,0.35)'}`,
            }}>
            {loading
              ? <><span className="w-2.5 h-2.5 border border-t-violet-400 border-zinc-600 rounded-full animate-spin" /> SCANNING…</>
              : <><Search size={9} /> SCAN</>}
          </button>
        </div>
      </div>

      {/* Empty */}
      {!data && !loading && (
        <div className="flex flex-col items-center justify-center py-7 gap-2">
          <BarChart2 size={22} className="text-zinc-700" />
          <p className="text-[10px] text-zinc-600">Click SCAN to analyse CANSLIM criteria</p>
          <p className="text-[8px] text-zinc-700 text-center max-w-[230px]">
            Current earnings · New highs · Supply/demand · Leader strength · Institutional · Market
          </p>
        </div>
      )}

      {loading && (
        <div className="flex flex-col items-center justify-center py-7 gap-2">
          <span className="w-6 h-6 border-2 border-t-violet-400 border-zinc-700 rounded-full animate-spin" />
          <p className="text-[10px] text-zinc-500">Analysing CANSLIM criteria…</p>
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
                data-testid="canslim-stage-badge">
                {stage.label}
              </span>
            </div>
            {data.current_price && (
              <span className="text-[9px] font-mono text-zinc-400">{fmt(data.current_price)}</span>
            )}
          </div>

          {/* Score bar */}
          <ScoreBar score={data.confluence_score} />

          {/* CANSLIM letter grid */}
          {Object.keys(bd).length > 0 && (
            <div className="grid grid-cols-6 gap-1">
              {LETTERS.map(({ key, label, desc }) => {
                const val = bd[key];
                const col = letterColor(key, val);
                return (
                  <div key={key} className="flex flex-col items-center gap-0.5 rounded-lg py-1.5"
                    style={{ background: `${col}10`, border: `1px solid ${col}25` }}
                    title={desc}>
                    <span className="text-[10px] font-black" style={{ color: col }}>{label}</span>
                    <span className="text-[7px] font-mono text-zinc-500">{letterDisplay(key, val)}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Entry / SL / Target */}
          {data.is_match && (
            <div className="grid grid-cols-3 gap-1.5">
              <StatCell label="Entry"         value={fmt(data.entry_price)} color="#34d399" />
              <StatCell label="Stop Loss"     value={fmt(data.stop_loss)}   color="#f87171" />
              <StatCell label="Target (+25%)" value={fmt(data.target)}      color="#a78bfa" />
            </div>
          )}

          {/* Strength signals */}
          {data.strength_signals?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {data.strength_signals.map((s, i) => (
                <span key={i} className="text-[8px] px-2 py-0.5 rounded-full font-semibold"
                  style={{ background: 'rgba(167,139,250,0.1)', color: '#a78bfa', border: '1px solid rgba(167,139,250,0.25)' }}>
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
