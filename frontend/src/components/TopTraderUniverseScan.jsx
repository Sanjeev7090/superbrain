/**
 * TopTraderUniverseScan — Scans F&O universe across all 4 Top Trader strategies
 */
import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Zap, TrendingUp, ChevronRight, Star, Activity, Shield, BarChart2, Users } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const STRATEGY_META = {
  minervini:  { label: 'Minervini VCP',      color: '#eab308', border: 'rgba(234,179,8,0.3)',   bg: 'rgba(234,179,8,0.08)',  Icon: TrendingUp  },
  livermore:  { label: 'Livermore Pivotal',  color: '#60a5fa', border: 'rgba(96,165,250,0.3)',  bg: 'rgba(96,165,250,0.08)', Icon: Activity    },
  canslim:    { label: "O'Neil CANSLIM",     color: '#a78bfa', border: 'rgba(167,139,250,0.3)', bg: 'rgba(167,139,250,0.08)',Icon: Star        },
  paul_tudor: { label: 'Paul Tudor Jones',   color: '#f97316', border: 'rgba(249,115,22,0.3)',  bg: 'rgba(249,115,22,0.08)', Icon: Shield      },
};

const STRATEGY_ORDER = ['minervini', 'livermore', 'canslim', 'paul_tudor'];

function StockCard({ item, onLoad, loading }) {
  const fmt = v => v != null ? `₹${Number(v).toLocaleString('en-IN')}` : null;
  const isLoading = loading === item.ticker;

  return (
    <div
      className="rounded-lg px-3 py-2.5 cursor-pointer transition-all hover:scale-[1.01] active:scale-[0.99]"
      style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}
      onClick={() => !isLoading && onLoad(item)}
      data-testid={`top-trader-card-${item.ticker}`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          {isLoading
            ? <span className="w-3 h-3 border border-t-green-400 border-zinc-600 rounded-full animate-spin" />
            : <ChevronRight size={10} className="text-zinc-600" />
          }
          <span className="text-[10px] font-black text-white">{item.name || item.ticker.replace('.NS','')}</span>
          <span className="text-[7px] text-zinc-600 font-mono">{item.sector}</span>
        </div>
        <span
          className="text-[9px] font-black font-mono px-1.5 py-0.5 rounded"
          style={{ background: 'rgba(52,211,153,0.1)', color: '#34d399' }}
        >
          {item.confluence_score?.toFixed(0)}%
        </span>
      </div>

      {/* Entry / SL / Target row */}
      {(item.entry_price || item.stop_loss || item.target) && (
        <div className="flex items-center gap-2 mb-1">
          {item.entry_price && <span className="text-[7px] font-mono text-green-400">E {fmt(item.entry_price)}</span>}
          {item.stop_loss   && <span className="text-[7px] font-mono text-red-400">SL {fmt(item.stop_loss)}</span>}
          {item.target      && <span className="text-[7px] font-mono text-violet-400">T {fmt(item.target)}</span>}
        </div>
      )}

      {/* Signals */}
      {item.strength_signals?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {item.strength_signals.slice(0, 2).map((s, i) => (
            <span key={i} className="text-[6px] px-1 py-0.5 rounded bg-zinc-800 text-zinc-400">{s}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function MultiMatchCard({ item, onLoad, loading }) {
  const isLoading = loading === item.ticker;
  return (
    <div
      className="rounded-lg px-3 py-2.5 cursor-pointer transition-all hover:scale-[1.01]"
      style={{ background: 'rgba(52,211,153,0.06)', border: '1px solid rgba(52,211,153,0.2)' }}
      onClick={() => !isLoading && onLoad(item)}
      data-testid={`multi-match-card-${item.ticker}`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          {isLoading
            ? <span className="w-3 h-3 border border-t-green-400 border-zinc-600 rounded-full animate-spin" />
            : <Zap size={9} className="text-green-400" />
          }
          <span className="text-[10px] font-black text-white">{item.name || item.ticker.replace('.NS','')}</span>
          <span className="text-[7px] text-zinc-600">{item.sector}</span>
        </div>
        <span className="text-[8px] font-black font-mono text-green-400">{item.avg_score?.toFixed(0)}%</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {item.matched_strategies?.map(s => {
          const m = STRATEGY_META[s];
          return m ? (
            <span key={s} className="text-[7px] px-1.5 py-0.5 rounded-full font-bold"
              style={{ background: m.bg, color: m.color, border: `1px solid ${m.border}` }}>
              {m.label.split(' ')[0]}
            </span>
          ) : null;
        })}
      </div>
    </div>
  );
}

export default function TopTraderUniverseScan({ selectedStock, onStockLoad }) {
  const [scanning,    setScanning]    = useState(false);
  const [results,     setResults]     = useState(null);
  const [loadingTicker, setLoadingTicker] = useState(null);

  const runScan = useCallback(async () => {
    setScanning(true);
    setResults(null);
    try {
      const res = await axios.post(`${API}/strategy/top-trader-universe-scan`);
      setResults(res.data);
      const total = Object.values(res.data.results).reduce((a, b) => a + b.length, 0);
      toast.success(`Scan complete — ${total} matches across ${res.data.total_scanned} F&O stocks`);
    } catch (e) {
      toast.error('Scan failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setScanning(false);
    }
  }, []);

  const handleLoad = useCallback(async (item) => {
    if (loadingTicker) return;
    setLoadingTicker(item.ticker);
    try {
      await onStockLoad({ ticker: item.ticker, name: item.name, type: 'FO' });
    } finally {
      setLoadingTicker(null);
    }
  }, [loadingTicker, onStockLoad]);

  const totalMatches = results
    ? Object.values(results.results).reduce((a, b) => a + b.length, 0)
    : 0;

  return (
    <div className="flex flex-col h-full" style={{ background: 'rgba(9,9,11,0.98)' }}>

      {/* Top header + scan button */}
      <div className="px-3 pt-3 pb-2 flex items-center justify-between border-b border-zinc-800/60">
        <div className="flex items-center gap-2">
          <Users size={13} className="text-yellow-400" />
          <span className="text-[10px] font-black text-yellow-400 uppercase tracking-widest">Top Trader Concepts</span>
          {results && (
            <span className="text-[8px] text-zinc-500 font-mono">
              {totalMatches} matches / {results.total_scanned} stocks
            </span>
          )}
        </div>
        <button
          data-testid="universe-scan-btn"
          onClick={runScan}
          disabled={scanning}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-[9px] font-black transition-all disabled:opacity-60"
          style={{
            background: scanning ? 'rgba(107,114,128,0.2)' : 'linear-gradient(135deg, rgba(234,179,8,0.25), rgba(249,115,22,0.2))',
            color:  scanning ? '#6b7280' : '#fbbf24',
            border: `1px solid ${scanning ? 'rgba(107,114,128,0.3)' : 'rgba(234,179,8,0.4)'}`,
          }}
        >
          {scanning
            ? <><span className="w-3 h-3 border border-t-yellow-400 border-zinc-600 rounded-full animate-spin" /> SCANNING F&amp;O…</>
            : <><Zap size={10} /> SCAN F&amp;O UNIVERSE</>
          }
        </button>
      </div>

      {/* Progress / empty state */}
      {scanning && (
        <div className="flex flex-col items-center justify-center py-10 gap-3">
          <span className="w-8 h-8 border-2 border-t-yellow-400 border-zinc-700 rounded-full animate-spin" />
          <p className="text-[10px] text-zinc-500">Scanning 30 F&amp;O stocks across 4 strategies…</p>
          <p className="text-[8px] text-zinc-700">Minervini · Livermore · CANSLIM · Paul Tudor</p>
        </div>
      )}

      {!results && !scanning && (
        <div className="flex flex-col items-center justify-center py-10 gap-3">
          <BarChart2 size={28} className="text-zinc-700" />
          <p className="text-[11px] text-zinc-500 font-semibold">Scan F&amp;O Universe</p>
          <p className="text-[8px] text-zinc-600 text-center max-w-[220px]">
            Click the button above to scan 30 F&amp;O stocks against all 4 legendary trader strategies at once.
          </p>
          <div className="flex flex-wrap gap-1.5 justify-center mt-1">
            {STRATEGY_ORDER.map(k => {
              const m = STRATEGY_META[k];
              return (
                <span key={k} className="text-[7px] px-2 py-0.5 rounded-full font-bold"
                  style={{ background: m.bg, color: m.color, border: `1px solid ${m.border}` }}>
                  {m.label}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Results */}
      {results && !scanning && (
        <div className="overflow-y-auto flex-1 scrollbar-none p-2 space-y-3">

          {/* Multi-match top picks */}
          {results.multi_match?.length > 0 && (
            <section>
              <div className="flex items-center gap-1.5 mb-1.5 px-1">
                <Zap size={9} className="text-green-400" />
                <span className="text-[8px] font-black text-green-400 uppercase tracking-wider">
                  Multi-Strategy Picks ({results.multi_match.length})
                </span>
              </div>
              <div className="space-y-1">
                {results.multi_match.map(item => (
                  <MultiMatchCard key={item.ticker} item={item} onLoad={handleLoad} loading={loadingTicker} />
                ))}
              </div>
            </section>
          )}

          {/* Per-strategy sections */}
          {STRATEGY_ORDER.map(strat => {
            const m    = STRATEGY_META[strat];
            const list = results.results[strat] || [];
            if (!list.length) return (
              <section key={strat}>
                <div className="flex items-center gap-1.5 px-1 py-1">
                  <m.Icon size={9} style={{ color: m.color }} />
                  <span className="text-[8px] font-black uppercase tracking-wider" style={{ color: m.color }}>
                    {m.label}
                  </span>
                  <span className="text-[7px] text-zinc-700">— no matches</span>
                </div>
              </section>
            );
            return (
              <section key={strat}>
                <div className="flex items-center gap-1.5 mb-1.5 px-1">
                  <m.Icon size={9} style={{ color: m.color }} />
                  <span className="text-[8px] font-black uppercase tracking-wider" style={{ color: m.color }}>
                    {m.label}
                  </span>
                  <span className="text-[7px] text-zinc-600 font-mono">({list.length})</span>
                </div>
                <div className="space-y-1">
                  {list.map(item => (
                    <StockCard key={item.ticker} item={item} onLoad={handleLoad} loading={loadingTicker} />
                  ))}
                </div>
              </section>
            );
          })}

          <p className="text-[7px] text-zinc-700 text-center pb-2">
            Scanned at {results.scan_time?.slice(0, 19).replace('T', ' ')} UTC
          </p>
        </div>
      )}
    </div>
  );
}
