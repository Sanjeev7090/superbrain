/**
 * MinerviniVCPPanel — Mark Minervini Volatility Contraction Pattern Scanner
 * Part of "Top Trader Concepts" section in Strategies tab
 */
import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { TrendingUp, TrendingDown, Zap, Target, Shield, BarChart2, Search } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const STAGE_CONFIG = {
  VCP_BREAKOUT:    { label: 'VCP BREAKOUT',    color: '#34d399', bg: 'rgba(52,211,153,0.12)',  border: 'rgba(52,211,153,0.3)'  },
  VCP_CONTRACTION: { label: 'VCP CONTRACTION', color: '#fbbf24', bg: 'rgba(251,191,36,0.10)',  border: 'rgba(251,191,36,0.3)'  },
  NO_MATCH:        { label: 'NO MATCH',         color: '#f87171', bg: 'rgba(248,113,113,0.08)', border: 'rgba(248,113,113,0.25)' },
};

function ScoreBar({ score }) {
  const color = score >= 75 ? '#34d399' : score >= 55 ? '#fbbf24' : '#f87171';
  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center">
        <span className="text-[9px] text-zinc-500 uppercase tracking-wider">Confluence Score</span>
        <span className="text-[11px] font-black font-mono" style={{ color }}>{score.toFixed(1)}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${Math.min(score, 100)}%`, background: color }}
        />
      </div>
    </div>
  );
}

function StatCell({ label, value, color = '#a1a1aa' }) {
  return (
    <div className="rounded-lg p-2 text-center" style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
      <p className="text-[8px] text-zinc-600 mb-0.5 leading-tight">{label}</p>
      <p className="text-[11px] font-black font-mono" style={{ color }}>{value ?? '—'}</p>
    </div>
  );
}

function ContractionDots({ contractions }) {
  if (!contractions?.length) return null;
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[8px] text-zinc-600 uppercase tracking-wider mr-1">Contractions</span>
      {contractions.map((c, i) => (
        <div
          key={i}
          className="flex flex-col items-center gap-0.5"
          title={`Range: ${c.range_pct}% · ${c.tightness}`}
        >
          <div
            className="rounded-full"
            style={{
              width: 8 + (4 - i) * 3,
              height: 8 + (4 - i) * 3,
              background: c.tightness === 'tight' ? '#34d399' : c.tightness === 'medium' ? '#fbbf24' : '#71717a',
              opacity: 0.85,
            }}
          />
          <span className="text-[6px] text-zinc-700">{c.range_pct}%</span>
        </div>
      ))}
      <span className="text-[7px] text-zinc-700 ml-1">(shrinking)</span>
    </div>
  );
}

export default function MinerviniVCPPanel({ selectedStock }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [ticker,  setTicker]  = useState('');

  const runScan = useCallback(async () => {
    const sym = (ticker.trim() || selectedStock?.ticker || '').toUpperCase();
    if (!sym) {
      toast.error('Ticker enter karo ya pehle stock select karo');
      return;
    }
    setLoading(true);
    setData(null);
    try {
      const res = await axios.post(`${API}/strategy/minervini-vcp`, { ticker: sym });
      setData(res.data);
      if (res.data.is_match) {
        toast.success(`VCP Match — ${sym} · Score ${res.data.confluence_score}%`);
      } else {
        toast.info(`No VCP pattern: ${res.data.reason?.slice(0, 60)}…`);
      }
    } catch (e) {
      toast.error('Scan failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [ticker, selectedStock]);

  const stage  = data ? STAGE_CONFIG[data.stage] || STAGE_CONFIG.NO_MATCH : null;
  const fmt    = (v) => v != null ? `₹${Number(v).toLocaleString('en-IN')}` : '—';

  return (
    <div
      className="rounded-xl border overflow-hidden"
      style={{ background: 'rgba(9,9,11,0.97)', borderColor: 'rgba(234,179,8,0.2)' }}
      data-testid="minervini-vcp-panel"
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b"
        style={{ borderColor: 'rgba(234,179,8,0.15)', background: 'rgba(234,179,8,0.04)' }}
      >
        <div className="flex items-center gap-2">
          <div
            className="w-5 h-5 rounded flex items-center justify-center"
            style={{ background: 'rgba(234,179,8,0.15)', border: '1px solid rgba(234,179,8,0.3)' }}
          >
            <TrendingUp size={10} className="text-yellow-400" />
          </div>
          <div>
            <span className="text-[11px] font-black text-yellow-400 tracking-wide uppercase">Minervini VCP</span>
            <span className="text-[8px] text-zinc-600 ml-2">Volatility Contraction Pattern</span>
          </div>
        </div>

        {/* Ticker input + Scan button */}
        <div className="flex items-center gap-1.5">
          <input
            type="text"
            placeholder={selectedStock?.ticker || 'RELIANCE.NS'}
            value={ticker}
            onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && runScan()}
            className="w-28 px-2 py-1 rounded text-[9px] font-mono bg-zinc-900 border border-zinc-700 text-zinc-200 placeholder-zinc-600 outline-none focus:border-yellow-600"
            data-testid="vcp-ticker-input"
          />
          <button
            data-testid="vcp-scan-btn"
            onClick={runScan}
            disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[9px] font-black transition-all disabled:opacity-50"
            style={{
              background: loading ? 'rgba(107,114,128,0.2)' : 'rgba(234,179,8,0.15)',
              color: loading ? '#6b7280' : '#fbbf24',
              border: `1px solid ${loading ? 'rgba(107,114,128,0.3)' : 'rgba(234,179,8,0.35)'}`,
            }}
          >
            {loading
              ? <><span className="w-2.5 h-2.5 border border-t-yellow-400 border-zinc-600 rounded-full animate-spin" /> SCANNING…</>
              : <><Search size={9} /> SCAN</>
            }
          </button>
        </div>
      </div>

      {/* Empty state */}
      {!data && !loading && (
        <div className="flex flex-col items-center justify-center py-7 gap-2">
          <BarChart2 size={22} className="text-zinc-700" />
          <p className="text-[10px] text-zinc-600">Click SCAN to detect VCP pattern</p>
          <p className="text-[8px] text-zinc-700 text-center max-w-[220px]">
            Stage 2 uptrend · 52w high proximity · Progressive contraction · Volume dry-up
          </p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex flex-col items-center justify-center py-7 gap-2">
          <span className="w-6 h-6 border-2 border-t-yellow-400 border-zinc-700 rounded-full animate-spin" />
          <p className="text-[10px] text-zinc-500">Fetching 300 days of daily data…</p>
        </div>
      )}

      {/* Results */}
      {data && !loading && (
        <div className="px-3 py-3 space-y-3">

          {/* Stage badge + symbol */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-black text-white">{data.symbol}</span>
              <span
                className="text-[8px] font-black px-2 py-0.5 rounded-full"
                style={{ background: stage.bg, color: stage.color, border: `1px solid ${stage.border}` }}
                data-testid="vcp-stage-badge"
              >
                {stage.label}
              </span>
            </div>
            {data.current_price && (
              <span className="text-[9px] font-mono text-zinc-400">{fmt(data.current_price)}</span>
            )}
          </div>

          {/* Score bar */}
          <ScoreBar score={data.confluence_score} />

          {/* Entry / SL / Target */}
          {data.is_match && (
            <div className="grid grid-cols-3 gap-1.5">
              <StatCell label="Entry" value={fmt(data.entry_price)} color="#34d399" />
              <StatCell label="Stop Loss" value={fmt(data.stop_loss)} color="#f87171" />
              <StatCell label="Target (+15%)" value={fmt(data.target)} color="#a78bfa" />
            </div>
          )}

          {/* Contraction pattern dots */}
          <ContractionDots contractions={data.contraction_levels} />

          {/* Strength signals */}
          {data.strength_signals?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {data.strength_signals.map((s, i) => (
                <span
                  key={i}
                  className="text-[8px] px-2 py-0.5 rounded-full font-semibold"
                  style={{ background: 'rgba(52,211,153,0.1)', color: '#34d399', border: '1px solid rgba(52,211,153,0.25)' }}
                >
                  {s}
                </span>
              ))}
            </div>
          )}

          {/* Reason */}
          <p className="text-[8px] text-zinc-500 leading-relaxed border-l-2 border-zinc-800 pl-2">
            {data.reason}
          </p>

          {/* RS footer */}
          <p className="text-[7px] text-zinc-700">
            Relative Strength vs Nifty: {data.rel_strength?.toFixed(1)} · {data.timestamp?.slice(0, 10)}
          </p>
        </div>
      )}
    </div>
  );
}
