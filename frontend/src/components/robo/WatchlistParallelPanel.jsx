/**
 * WatchlistParallelPanel
 * ======================
 * Shows all watchlist stocks under continuous observation:
 *  - Real-time signal (BUY/SELL/HOLD) with confidence
 *  - Trade plan: Entry, SL, TP for every ticker
 *  - Brain + DreamerV3 alignment
 *  - Regime + RSI snapshot
 *  - Position status (WATCHING / IN POSITION)
 *
 * Data source: roboState.watchlist_observations (populated by trading loop Phase A)
 */

import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Eye, Activity } from 'lucide-react';

const fmt2 = v => v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = v => v == null ? '—' : `${Number(v).toFixed(1)}%`;

const SIGNAL_CFG = {
  BUY:  { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.35)', text: '#10b981', icon: '▲' },
  SELL: { bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)',  text: '#ef4444', icon: '▼' },
  HOLD: { bg: 'rgba(107,114,128,0.10)',border: 'rgba(107,114,128,0.3)', text: '#6b7280', icon: '●' },
};

const REGIME_COLOR = {
  UPTREND:   '#10b981',
  DOWNTREND: '#ef4444',
  RANGING:   '#f59e0b',
  UNKNOWN:   '#6b7280',
};

function ConfBar({ pct, signal }) {
  const color = signal === 'BUY' ? '#10b981' : signal === 'SELL' ? '#ef4444' : '#6b7280';
  return (
    <div className="h-1 w-full rounded-full bg-zinc-800 overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${Math.min(100, pct || 0)}%`, background: color }}
      />
    </div>
  );
}

function TickerRow({ ticker, obs, isExpanded, onToggle, isTraining }) {
  const sig     = obs.signal || 'HOLD';
  const sCfg    = SIGNAL_CFG[sig] || SIGNAL_CFG.HOLD;
  const hasPos  = obs.has_position;
  const conf    = obs.confidence || 0;
  const rColor  = REGIME_COLOR[obs.regime] || REGIME_COLOR.UNKNOWN;
  const fear    = (obs.brain_fear || 0) * 100;
  const symbol  = ticker.replace('.NS', '').replace('.BO', '');

  // R:R ratio
  let rrRatio = '—';
  if (obs.sl_price && obs.tp_price && obs.entry_target) {
    const risk   = Math.abs(obs.entry_target - obs.sl_price);
    const reward = Math.abs(obs.tp_price - obs.entry_target);
    if (risk > 0) rrRatio = `1:${(reward / risk).toFixed(1)}`;
  }

  return (
    <div
      className="rounded-xl border transition-all duration-200"
      style={{
        background: hasPos
          ? 'rgba(16,185,129,0.05)'
          : isExpanded
          ? 'rgba(30,30,40,0.8)'
          : 'rgba(24,24,32,0.7)',
        borderColor: hasPos
          ? 'rgba(16,185,129,0.3)'
          : isExpanded
          ? 'rgba(63,63,80,0.7)'
          : 'rgba(39,39,52,0.6)',
      }}
      data-testid={`watchlist-obs-${symbol}`}
    >
      {/* ── Collapsed Row ─────────────────────────────────────────────── */}
      <button
        className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left"
        onClick={onToggle}
      >
        {/* Expand chevron */}
        <span className="text-zinc-600 flex-shrink-0">
          {isExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        </span>

        {/* Ticker symbol */}
        <span className="text-[11px] font-black text-white w-[64px] flex-shrink-0 font-mono">
          {symbol}
        </span>

        {/* Signal badge */}
        <span
          className="text-[8px] font-black px-1.5 py-0.5 rounded flex-shrink-0"
          style={{ background: sCfg.bg, border: `1px solid ${sCfg.border}`, color: sCfg.text }}
        >
          {sCfg.icon} {sig}
        </span>

        {/* Confidence bar + pct */}
        <div className="flex-1 min-w-0 flex flex-col gap-0.5">
          <ConfBar pct={conf} signal={sig} />
        </div>
        <span className="text-[9px] font-bold w-[30px] text-right flex-shrink-0"
          style={{ color: sCfg.text }}>
          {conf.toFixed(0)}%
        </span>

        {/* Price */}
        <span className="text-[10px] font-mono text-zinc-300 w-[56px] text-right flex-shrink-0">
          {obs.price ? `₹${fmt2(obs.price)}` : '—'}
        </span>

        {/* Regime */}
        <span
          className="text-[8px] font-bold w-[68px] text-right flex-shrink-0 hidden sm:block"
          style={{ color: rColor }}
        >
          {obs.regime || '—'}
        </span>

        {/* Status: IN POSITION or WATCHING or TRAINING */}
        {hasPos ? (
          <span className="text-[7px] font-black px-1.5 py-0.5 rounded flex-shrink-0 bg-emerald-900/30 text-emerald-400 border border-emerald-700/30">
            IN POS
          </span>
        ) : isTraining ? (
          <span className="text-[7px] font-black px-1.5 py-0.5 rounded flex-shrink-0 border flex items-center gap-0.5"
            style={{ background: 'rgba(99,102,241,0.15)', borderColor: 'rgba(99,102,241,0.4)', color: '#818cf8' }}>
            <span className="inline-block w-1 h-1 rounded-full bg-indigo-400 animate-pulse" />
            LIVE
          </span>
        ) : (
          <span className="text-[7px] font-bold px-1.5 py-0.5 rounded flex-shrink-0 bg-zinc-800/50 text-zinc-600">
            <Eye size={8} className="inline mr-0.5" />WATCH
          </span>
        )}
      </button>

      {/* ── Expanded Detail ────────────────────────────────────────────── */}
      {isExpanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-zinc-800/50 pt-2">

          {/* Trade plan levels */}
          {sig !== 'HOLD' && obs.sl_price && obs.tp_price ? (
            <div className="grid grid-cols-4 gap-1.5">
              {[
                { l: 'Entry',  v: `₹${fmt2(obs.entry_target)}`, c: '#f59e0b' },
                { l: 'SL',     v: `₹${fmt2(obs.sl_price)}`,     c: '#ef4444' },
                { l: 'TP',     v: `₹${fmt2(obs.tp_price)}`,     c: '#10b981' },
                { l: 'R:R',    v: rrRatio,                       c: '#60a5fa' },
              ].map(({ l, v, c }) => (
                <div key={l} className="rounded-lg p-1.5 text-center bg-zinc-900/60">
                  <p className="text-[7px] text-zinc-600 mb-0.5">{l}</p>
                  <p className="text-[10px] font-black" style={{ color: c }}>{v}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg px-3 py-2 bg-zinc-900/40 text-center">
              <p className="text-[9px] text-zinc-600">No trade plan — signal is HOLD</p>
            </div>
          )}

          {/* Indicators row */}
          <div className="flex flex-wrap gap-2 items-center">
            <div className="flex items-center gap-1">
              <span className="text-[8px] text-zinc-600">RSI</span>
              <span className="text-[9px] font-bold"
                style={{ color: (obs.rsi14 || 50) > 70 ? '#ef4444' : (obs.rsi14 || 50) < 30 ? '#10b981' : '#f59e0b' }}>
                {obs.rsi14?.toFixed(1) || '50.0'}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[8px] text-zinc-600">ATR</span>
              <span className="text-[9px] font-bold text-zinc-400">{obs.atr_pct?.toFixed(2) || '—'}%</span>
            </div>

            {/* DreamerV3 */}
            <div className="flex items-center gap-1 ml-auto">
              <span className="text-[7px] text-zinc-600 uppercase">DV3</span>
              <span className={`text-[8px] font-bold px-1 py-px rounded ${
                obs.dreamer_signal === 'BUY'  ? 'bg-emerald-900/20 text-emerald-400'
                : obs.dreamer_signal === 'SELL' ? 'bg-red-900/20 text-red-400'
                : 'bg-zinc-800 text-zinc-500'
              }`}>{obs.dreamer_signal || 'HOLD'} {obs.dreamer_conf?.toFixed(0)}%</span>
            </div>

            {/* Brain */}
            <div className="flex items-center gap-1">
              <span className="text-[7px] text-zinc-600 uppercase">Brain</span>
              <span className={`text-[8px] font-bold px-1 py-px rounded ${
                obs.brain_action === 'BUY'  ? 'bg-emerald-900/20 text-emerald-400'
                : obs.brain_action === 'SELL' ? 'bg-red-900/20 text-red-400'
                : 'bg-zinc-800 text-zinc-500'
              }`}>{obs.brain_action || 'HOLD'}</span>
              {fear > 20 && (
                <span className="text-[7px] text-amber-400">⚠{fear.toFixed(0)}%</span>
              )}
            </div>
          </div>

          {/* Brain reason */}
          {obs.brain_reason && (
            <p className="text-[8px] text-zinc-600 leading-relaxed px-1">
              {obs.brain_reason}
            </p>
          )}

          {/* Error state */}
          {obs.error && (
            <p className="text-[8px] text-red-400 px-1">Price unavailable — skipped this cycle</p>
          )}
        </div>
      )}
    </div>
  );
}


export default function WatchlistParallelPanel({ roboState, isActive }) {
  const [expanded, setExpanded] = useState({});

  const obs         = roboState?.watchlist_observations || {};
  const tickers     = Object.keys(obs);
  const loopStatus  = roboState?.live_obs_status || {};
  const loopRunning = loopStatus?.running || false;
  const trainingTickers = new Set(loopStatus?.tickers || []);

  // Summary counts
  const buyCount  = tickers.filter(t => obs[t]?.signal === 'BUY').length;
  const sellCount = tickers.filter(t => obs[t]?.signal === 'SELL').length;
  const inPos     = tickers.filter(t => obs[t]?.has_position).length;

  // All watchlist tickers from prefs (even if no obs yet)
  const watchlistFromPrefs = roboState?.watchlist || [];
  const allTickers         = tickers.length > 0 ? tickers
    : watchlistFromPrefs.map(t => t.replace('.NS','').replace('.BO',''));

  const toggle = (t) => setExpanded(p => ({ ...p, [t]: !p[t] }));

  if (tickers.length === 0 && watchlistFromPrefs.length === 0) {
    return (
      <div
        className="rounded-2xl border border-dashed border-zinc-800/60 flex items-center justify-center py-8"
        data-testid="watchlist-obs-empty"
        style={{ background: 'rgba(15,15,20,0.6)' }}
      >
        <div className="text-center">
          <Eye size={18} className="text-zinc-700 mx-auto mb-2" />
          <p className="text-xs text-zinc-600">No stocks in watchlist</p>
          <p className="text-[9px] text-zinc-700 mt-0.5">Add stocks in Settings → they'll be observed each scan cycle</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="rounded-2xl border"
      style={{
        background: 'rgba(12,12,18,0.8)',
        borderColor: isActive ? 'rgba(99,102,241,0.3)' : 'rgba(39,39,52,0.6)',
        boxShadow: isActive ? '0 0 24px rgba(99,102,241,0.06)' : 'none',
      }}
      data-testid="watchlist-parallel-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800/50">
        <div className="flex items-center gap-2">
          <div
            className="w-6 h-6 rounded-lg flex items-center justify-center"
            style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)' }}
          >
            <Activity size={11} style={{ color: '#818cf8' }} />
          </div>
          <div>
            <p className="text-[10px] font-black text-white">Parallel Watchlist Observer</p>
            <p className="text-[8px] text-zinc-600">All stocks observed every cycle — trade plans generated</p>
          </div>
        </div>

        {/* Summary chips */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {/* Live training indicator */}
          {loopRunning && (
            <span className="text-[8px] font-black px-1.5 py-0.5 rounded flex items-center gap-1"
              style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.35)', color: '#818cf8' }}>
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
              DV3 TRAINING LIVE
            </span>
          )}
          {buyCount > 0 && (
            <span className="text-[8px] font-black px-1.5 py-0.5 rounded bg-emerald-900/20 text-emerald-400 border border-emerald-700/20">
              {buyCount} BUY
            </span>
          )}
          {sellCount > 0 && (
            <span className="text-[8px] font-black px-1.5 py-0.5 rounded bg-red-900/20 text-red-400 border border-red-700/20">
              {sellCount} SELL
            </span>
          )}
          {inPos > 0 && (
            <span className="text-[8px] font-black px-1.5 py-0.5 rounded bg-blue-900/20 text-blue-400 border border-blue-700/20">
              {inPos} IN POS
            </span>
          )}
          <span className="text-[8px] text-zinc-600 font-mono">{allTickers.length} stocks</span>
        </div>
      </div>

      {/* Column headers */}
      <div className="flex items-center gap-2.5 px-3 py-1.5 border-b border-zinc-800/30">
        <span className="text-[7px] text-zinc-700 uppercase w-[11px]" />
        <span className="text-[7px] text-zinc-700 uppercase w-[64px]">Ticker</span>
        <span className="text-[7px] text-zinc-700 uppercase w-[40px]">Signal</span>
        <span className="text-[7px] text-zinc-700 uppercase flex-1">Confidence</span>
        <span className="text-[7px] text-zinc-700 uppercase w-[30px] text-right">%</span>
        <span className="text-[7px] text-zinc-700 uppercase w-[56px] text-right">Price</span>
        <span className="text-[7px] text-zinc-700 uppercase w-[68px] text-right hidden sm:block">Regime</span>
        <span className="text-[7px] text-zinc-700 uppercase w-[42px] text-right">Status</span>
      </div>

      {/* Ticker rows — show all obs tickers OR watchlist prefs if no obs yet */}
      <div className="divide-y divide-zinc-800/30">
        {tickers.length > 0
          ? tickers.map(ticker => (
              <TickerRow
                key={ticker}
                ticker={ticker}
                obs={obs[ticker]}
                isExpanded={!!expanded[ticker]}
                onToggle={() => toggle(ticker)}
                isTraining={trainingTickers.has(ticker) || trainingTickers.has(ticker + '.NS') || trainingTickers.has(ticker + '.BO')}
              />
            ))
          : watchlistFromPrefs.map(ticker => {
              const sym = ticker.replace('.NS','').replace('.BO','');
              return (
                <div key={ticker}
                  className="flex items-center gap-2.5 px-3 py-2.5"
                  data-testid={`watchlist-obs-${sym}`}
                >
                  <span className="text-[10px] font-mono font-black text-zinc-400 w-[64px]">{sym}</span>
                  {(trainingTickers.has(ticker) || trainingTickers.has(sym)) ? (
                    <span className="text-[7px] font-black px-1.5 py-0.5 rounded border flex items-center gap-0.5"
                      style={{ background: 'rgba(99,102,241,0.15)', borderColor: 'rgba(99,102,241,0.4)', color: '#818cf8' }}>
                      <span className="inline-block w-1 h-1 rounded-full bg-indigo-400 animate-pulse" />
                      TRAINING
                    </span>
                  ) : (
                    <span className="text-[7px] text-zinc-600 px-1.5 py-0.5 rounded bg-zinc-800/40">
                      Waiting for first scan…
                    </span>
                  )}
                </div>
              );
            })
        }
      </div>

      <div className="px-4 py-2 border-t border-zinc-800/40">
        <p className="text-[7px] text-zinc-700">
          {loopRunning
            ? `DreamerV3 training LIVE · ${trainingTickers.size} ticker(s) · refreshes every ${loopStatus?.interval_s || 60}s · ${loopStatus?.cycle_count || 0} cycles`
            : isActive
            ? `Observations refresh every scan cycle · execution limited to ${roboState?.max_parallel_trades || 3} parallel positions`
            : 'Add stocks to watchlist → DreamerV3 live training auto-starts'}
        </p>
      </div>
    </div>
  );
}
