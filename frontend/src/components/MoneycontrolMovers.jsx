import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  ArrowClockwise, Lightning, Clock, TrendUp, Warning,
  CaretDown, CaretUp, Play, Trophy, ChartLineUp, CheckCircle, XCircle,
} from '@phosphor-icons/react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api/moneycontrol`;

/* ─── Helpers ──────────────────────────────────────────────────── */
function fmtPrice(v) {
  if (!v && v !== 0) return '—';
  return `₹${Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtNum(v) {
  if (!v) return '—';
  if (v >= 10_000_000) return `${(v / 10_000_000).toFixed(1)} Cr`;
  if (v >= 100_000)    return `${(v / 100_000).toFixed(1)} L`;
  if (v >= 1_000)      return `${(v / 1_000).toFixed(1)} K`;
  return String(v);
}

function pctColor(v) {
  return v >= 0 ? 'text-emerald-400' : 'text-rose-400';
}

/* ─── Performance Badge ─────────────────────────────────────────── */
function PerfBadge({ perf, compact = false }) {
  if (!perf) return null;

  const { status, pct_return, exit_ltp, entry_ltp } = perf;

  if (status === 'pending' || !status) return (
    <span className="flex items-center gap-1 text-[9px] px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/25 text-amber-400 font-bold">
      <Clock size={8} /> PENDING 9:15 AM
    </span>
  );

  if (status === 'expired') return (
    <span className="text-[9px] text-zinc-600 border border-zinc-700 px-1.5 py-0.5 rounded-full">EXPIRED</span>
  );

  if (status === 'no_data' || status === 'error') return (
    <span className="text-[9px] text-zinc-600 border border-zinc-700 px-1.5 py-0.5 rounded-full">N/A</span>
  );

  const isWin = status === 'win';
  if (compact) return (
    <span className={`flex items-center gap-1 text-[10px] font-black px-2 py-0.5 rounded-full border ${
      isWin
        ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300'
        : 'bg-rose-500/15 border-rose-500/30 text-rose-300'
    }`}>
      {isWin ? <CheckCircle size={9} weight="fill" /> : <XCircle size={9} weight="fill" />}
      {pct_return >= 0 ? '+' : ''}{pct_return?.toFixed(1)}%
    </span>
  );

  return (
    <div className={`rounded-lg p-2.5 border mt-2 ${
      isWin
        ? 'bg-emerald-500/10 border-emerald-500/25'
        : 'bg-rose-500/10 border-rose-500/25'
    }`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {isWin
            ? <CheckCircle size={12} weight="fill" className="text-emerald-400" />
            : <XCircle    size={12} weight="fill" className="text-rose-400" />}
          <span className={`text-xs font-black ${isWin ? 'text-emerald-300' : 'text-rose-300'}`}>
            {isWin ? 'PROFIT' : 'LOSS'} · {pct_return >= 0 ? '+' : ''}{pct_return?.toFixed(1)}%
          </span>
        </div>
        <span className="text-[10px] text-zinc-500">Exit at 9:15 AM</span>
      </div>
      {entry_ltp && exit_ltp && (
        <div className="flex items-center gap-3 mt-1 text-[10px]">
          <span className="text-zinc-500">Entry: <span className="font-mono text-zinc-300">{fmtPrice(entry_ltp)}</span></span>
          <span className="text-zinc-600">→</span>
          <span className="text-zinc-500">Exit: <span className={`font-mono font-bold ${isWin ? 'text-emerald-300' : 'text-rose-300'}`}>{fmtPrice(exit_ltp)}</span></span>
        </div>
      )}
    </div>
  );
}

/* ─── Win Stats Bar ─────────────────────────────────────────────── */
function WinStatsBar({ stats }) {
  if (!stats || stats.total_tracked === 0) return null;
  const { total_tracked, wins, win_rate_pct, avg_return, best } = stats;
  return (
    <div className="flex items-center gap-3 px-3 py-2 bg-zinc-900/60 rounded-lg border border-white/8 text-[10px] flex-wrap"
         data-testid="mc-win-stats">
      <div className="flex items-center gap-1.5">
        <Trophy size={11} className="text-amber-400" weight="fill" />
        <span className="text-zinc-500">Win Rate</span>
        <span className={`font-black ${win_rate_pct >= 50 ? 'text-emerald-400' : 'text-rose-400'}`}>
          {wins}/{total_tracked} ({win_rate_pct}%)
        </span>
      </div>
      {avg_return !== null && (
        <div className="flex items-center gap-1">
          <ChartLineUp size={10} className="text-sky-400" />
          <span className="text-zinc-500">Avg</span>
          <span className={`font-bold ${avg_return >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {avg_return >= 0 ? '+' : ''}{avg_return}%
          </span>
        </div>
      )}
      {best?.symbol && (
        <div className="flex items-center gap-1 text-zinc-500">
          Best: <span className="text-amber-300 font-bold">{best.symbol}</span>
          <span className="text-emerald-400 font-bold">+{best.pct?.toFixed(1)}%</span>
        </div>
      )}
    </div>
  );
}

/* ─── Source Badge ──────────────────────────────────────────────── */
function SourceBadge({ source }) {
  if (!source) return null;
  const map = {
    moneycontrol:     { label: 'MC LIVE',    cls: 'bg-emerald-500/15 border-emerald-500/30 text-emerald-400' },
    yfinance_fallback:{ label: 'YF FALLBACK',cls: 'bg-sky-500/15 border-sky-500/30 text-sky-400' },
    demo:             { label: 'DEMO',       cls: 'bg-amber-500/15 border-amber-500/30 text-amber-400' },
  };
  const badge = map[source] || map.demo;
  return (
    <span className={`flex items-center gap-1 text-[9px] px-2 py-0.5 rounded-full border font-bold uppercase tracking-wider ${badge.cls}`}>
      {source === 'demo' && <Warning size={9} weight="fill" />}
      {badge.label}
    </span>
  );
}

/* ─── Signal Card ───────────────────────────────────────────────── */
function SignalCard({ stock, rank, onPaperTrade }) {
  const atm  = stock.atm_info || {};
  const chg  = stock.weekly_change_pct || 0;
  const isUp = chg >= 0;
  const perf = stock.performance || null;

  return (
    <div
      className="rounded-xl border border-white/10 bg-zinc-900/60 overflow-hidden"
      data-testid={`mc-signal-${stock.symbol}`}
    >
      {/* Card Header */}
      <div className={`px-3 py-2 flex items-center justify-between ${isUp ? 'bg-emerald-500/10' : 'bg-rose-500/10'}`}>
        <div className="flex items-center gap-2">
          <span className={`text-[9px] font-black px-1.5 py-0.5 rounded ${isUp ? 'bg-emerald-500/30 text-emerald-300' : 'bg-rose-500/30 text-rose-300'}`}>
            #{rank}
          </span>
          <div>
            <p className="text-xs font-black text-zinc-100 leading-none">{stock.symbol}</p>
            <p className="text-[10px] text-zinc-500 mt-0.5 leading-none truncate max-w-[120px]">{stock.company_name}</p>
          </div>
        </div>
        <div className="text-right flex flex-col items-end gap-1">
          <p className="text-xs font-mono font-bold text-zinc-200">{fmtPrice(stock.current_price)}</p>
          <p className={`text-[10px] font-bold ${pctColor(chg)}`}>
            {chg >= 0 ? '+' : ''}{chg.toFixed(2)}% 1W
          </p>
        </div>
      </div>

      {/* ATM Signal Details */}
      <div className="px-3 py-2.5 space-y-1.5">
        {/* Signal label */}
        <div className="flex items-center gap-1.5 mb-2">
          <TrendUp size={12} className="text-[#00E676]" weight="bold" />
          <span className="text-[10px] font-black text-[#00E676] uppercase tracking-wider">
            {atm.signal || 'BUY OTM CALL'}
          </span>
          {atm.estimated && (
            <span className="text-[8px] text-zinc-600 border border-zinc-700 px-1 rounded">EST</span>
          )}
        </div>

        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
          <div>
            <span className="text-zinc-600">OTM Strike</span>
            <p className="font-mono font-bold text-amber-300">{fmtPrice(atm.atm_strike)}</p>
          </div>
          <div>
            <span className="text-zinc-600">Expiry</span>
            <p className="font-mono text-zinc-300">{atm.expiry || '—'}</p>
          </div>
          <div>
            <span className="text-zinc-600">Option LTP</span>
            <p className="font-mono font-bold text-zinc-100">{fmtPrice(atm.option_ltp)}</p>
          </div>
          <div>
            <span className="text-zinc-600">IV</span>
            <p className="font-mono text-purple-300">{atm.iv ? `${atm.iv}%` : '—'}</p>
          </div>
        </div>

        {/* SL / Target bar */}
        <div className="mt-2 flex items-center gap-2 text-[10px]">
          <div className="flex-1 text-center py-1 rounded bg-rose-500/15 border border-rose-500/20">
            <p className="text-rose-400 font-bold">SL</p>
            <p className="font-mono text-rose-300">{fmtPrice(atm.sl_price)}</p>
            <p className="text-zinc-600">-{atm.sl_pct || 10}%</p>
          </div>
          <div className="flex-1 text-center py-1 rounded bg-emerald-500/15 border border-emerald-500/20">
            <p className="text-emerald-400 font-bold">Target</p>
            <p className="font-mono text-emerald-300">{fmtPrice(atm.target_price)}</p>
            <p className="text-zinc-600">+{atm.target_pct || 20}%</p>
          </div>
        </div>

        {/* Timing */}
        <div className="flex items-center justify-between text-[10px] pt-1 border-t border-white/5">
          <span className="flex items-center gap-1 text-zinc-500">
            <Clock size={9} />
            Entry: <span className="text-sky-400 font-bold">{atm.entry_time || '3:15 PM IST'}</span>
          </span>
          <span className="text-zinc-500">
            Exit: <span className="text-amber-400 font-bold">{atm.exit_time || '9:15 AM (next day)'}</span>
          </span>
        </div>

        {/* Margin + Lot */}
        {atm.lot_size && (
          <div className="flex items-center justify-between text-[10px] text-zinc-500 pt-0.5">
            <span>Lot: <span className="text-zinc-300 font-mono">{atm.lot_size}</span></span>
            <span>~Margin: <span className="text-zinc-300 font-mono">{fmtPrice(atm.margin_approx)}</span></span>
          </div>
        )}

        {/* Performance Result (shown if next-day data available) */}
        <PerfBadge perf={perf} />

        {/* Execute Button */}
        <button
          onClick={() => onPaperTrade && onPaperTrade(stock, atm)}
          className="w-full mt-1.5 py-1.5 rounded-lg bg-[#00E676]/15 border border-[#00E676]/30 text-[#00E676] text-[10px] font-black uppercase tracking-wider hover:bg-[#00E676]/25 hover:border-[#00E676]/60 transition-all flex items-center justify-center gap-1.5"
          data-testid={`mc-execute-${stock.symbol}`}
        >
          <Play size={10} weight="fill" />
          Execute on Paper Trade
        </button>
      </div>
    </div>
  );
}

/* ─── History Row ───────────────────────────────────────────────── */
function HistoryRow({ day }) {
  const [expanded, setExpanded] = useState(false);

  // Compute quick win/loss summary for header
  const tracked = (day.stocks || []).filter(s => s.performance?.status === 'win' || s.performance?.status === 'loss');
  const wins    = tracked.filter(s => s.performance?.status === 'win').length;
  const summaryLabel = tracked.length > 0 ? `${wins}/${tracked.length} wins` : null;

  return (
    <div className="border-b border-white/5">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-white/5 transition-colors text-left"
        data-testid={`mc-history-${day.date}`}
      >
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-mono text-zinc-400">{day.date}</span>
          <SourceBadge source={day.source} />
          {summaryLabel && (
            <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full border ${
              wins === tracked.length
                ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400'
                : wins > 0
                ? 'bg-amber-500/10 border-amber-500/25 text-amber-400'
                : 'bg-rose-500/10 border-rose-500/25 text-rose-400'
            }`}>
              {summaryLabel}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-zinc-500">{(day.stocks || []).length} picks</span>
          {expanded ? <CaretUp size={10} className="text-zinc-500" /> : <CaretDown size={10} className="text-zinc-500" />}
        </div>
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-1.5">
          {(day.stocks || []).map((s, i) => (
            <div key={i} className="flex items-center justify-between text-[11px] py-1.5 border-b border-white/5 gap-2">
              <span className="font-bold text-zinc-200 w-20 shrink-0">{s.symbol}</span>
              <span className={`font-mono ${pctColor(s.weekly_change_pct)} shrink-0`}>
                {s.weekly_change_pct >= 0 ? '+' : ''}{s.weekly_change_pct?.toFixed?.(2)}%
              </span>
              <span className="text-amber-300 font-mono shrink-0">OTM {fmtPrice(s.atm_info?.atm_strike)}</span>
              <div className="shrink-0">
                <PerfBadge perf={s.performance} compact />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Main Component ─────────────────────────────────────────────── */
export default function MoneycontrolMovers({ onPaperTrade }) {
  const [data,        setData]        = useState(null);
  const [history,     setHistory]     = useState([]);
  const [winStats,    setWinStats]    = useState(null);
  const [loading,     setLoading]     = useState(false);
  const [running,     setRunning]     = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [error,       setError]       = useState(null);
  const pollRef = useRef(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/movers`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const r = await fetch(`${API}/history?limit=14`);
      if (!r.ok) return;
      const d = await r.json();
      setHistory(d.history || []);
      if (d.win_stats) setWinStats(d.win_stats);
    } catch {}
  }, []);

  const triggerRun = async () => {
    setRunning(true);
    setError(null);
    try {
      const r = await fetch(`${API}/run`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setData(d.result);
      await loadHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => {
    loadData();
    loadHistory();
    pollRef.current = setInterval(loadData, 5 * 60_000);
    return () => clearInterval(pollRef.current);
  }, []); // eslint-disable-line

  const handleExecute = (stock, atm) => {
    if (onPaperTrade) {
      onPaperTrade({
        symbol:    stock.symbol,
        direction: 'BUY',
        entry:     atm.option_ltp,
        stoploss:  atm.sl_price,
        targets:   [atm.target_price],
        strategy:  'MC-OTM-CALL',
      });
    }
  };

  const isDemo = data?.source === 'demo';
  const runAt  = data?.run_at
    ? new Date(data.run_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' })
    : null;

  return (
    <div className="border-t-2 border-[#00E676]/20 bg-[#0A0A0A]" data-testid="mc-movers">
      {/* Section Header */}
      <div className="px-3 pt-3 pb-2 border-b border-white/10">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Lightning size={13} className="text-[#00E676]" weight="fill" />
            <h3 className="text-xs font-black tracking-tight text-zinc-100 uppercase">
              STOCKS OTM
            </h3>
            <span className="text-[9px] text-zinc-600 font-mono">·</span>
            <span className="text-[9px] text-zinc-500 font-mono uppercase tracking-wider">Moneycontrol Movers</span>
            <SourceBadge source={data?.source} />
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => { loadData(); loadHistory(); }}
              disabled={loading}
              className="p-1.5 rounded border border-white/10 text-zinc-500 hover:text-zinc-200 hover:border-white/25 hover:bg-white/5 transition-all disabled:opacity-40"
              title="Refresh"
              data-testid="mc-refresh-btn"
            >
              <ArrowClockwise size={11} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={triggerRun}
              disabled={running}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded border border-[#00E676]/30 text-[#00E676] text-[9px] font-black uppercase tracking-wider hover:bg-[#00E676]/10 hover:border-[#00E676]/50 transition-all disabled:opacity-40"
              data-testid="mc-run-btn"
            >
              <Play size={9} weight="fill" />
              {running ? 'Running...' : 'Run Now'}
            </button>
          </div>
        </div>

        {/* Sub-info row */}
        <div className="flex items-center gap-3 mt-1.5 flex-wrap">
          {runAt && (
            <span className="flex items-center gap-1 text-[10px] text-zinc-500">
              <Clock size={9} />
              Last run: <span className="text-zinc-400">{runAt}</span>
            </span>
          )}
          <span className="text-[10px] text-zinc-600">Auto-runs daily at 3:00 PM IST</span>
          {isDemo && (
            <span className="text-[10px] text-amber-500 flex items-center gap-1">
              <Warning size={9} weight="fill" /> Demo data — click "Run Now" for live
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="mx-3 mt-2 p-2 text-xs text-rose-400 border border-rose-500/30 bg-rose-500/10 rounded-lg">
          {error}
        </div>
      )}

      {/* Content */}
      {loading && !data ? (
        <div className="flex items-center justify-center h-28">
          <div className="w-5 h-5 border-2 border-[#00E676]/30 border-t-[#00E676] rounded-full animate-spin" />
        </div>
      ) : (
        <div className="px-3 py-3 space-y-3">
          {/* Top 3 Movers Table */}
          {data?.stocks?.length > 0 && (
            <div>
              <p className="text-[9px] font-black text-zinc-600 uppercase tracking-wider mb-1.5">
                1-Week F&O Top Gainers
              </p>
              <table className="w-full text-[11px] border-collapse" data-testid="mc-stocks-table">
                <thead>
                  <tr className="border-b border-white/10">
                    <th className="text-left py-1 text-zinc-600 font-bold">#</th>
                    <th className="text-left py-1 text-zinc-600 font-bold">Symbol</th>
                    <th className="text-right py-1 text-zinc-600 font-bold">Price</th>
                    <th className="text-right py-1 text-zinc-600 font-bold">1W%</th>
                    {data.stocks[0]?.volume > 0 && (
                      <th className="text-right py-1 text-zinc-600 font-bold">Vol</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {data.stocks.map((s, i) => (
                    <tr key={s.symbol} className="border-b border-white/5 hover:bg-white/5 transition-colors"
                        data-testid={`mc-stock-row-${s.symbol}`}>
                      <td className="py-1.5 text-zinc-600 font-mono">{i + 1}</td>
                      <td className="py-1.5">
                        <div>
                          <span className="font-bold text-zinc-100">{s.symbol}</span>
                          <p className="text-[9px] text-zinc-600 truncate max-w-[80px]">{s.company_name}</p>
                        </div>
                      </td>
                      <td className="py-1.5 text-right font-mono text-zinc-300">{fmtPrice(s.current_price)}</td>
                      <td className={`py-1.5 text-right font-bold ${pctColor(s.weekly_change_pct)}`}>
                        {s.weekly_change_pct >= 0 ? '+' : ''}{s.weekly_change_pct?.toFixed?.(2)}%
                      </td>
                      {data.stocks[0]?.volume > 0 && (
                        <td className="py-1.5 text-right text-zinc-500">{fmtNum(s.volume)}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Signals Ready Label */}
          {data?.stocks?.length > 0 && (
            <div className="flex items-center gap-2 py-1.5 px-3 rounded-lg bg-[#00E676]/8 border border-[#00E676]/20">
              <Clock size={10} className="text-[#00E676]" />
              <span className="text-[10px] text-[#00E676] font-bold">
                OTM Call Signals ready at {data.signals_ready_at || '3:15 PM IST'} — Execute & hold till next morning
              </span>
            </div>
          )}

          {/* Signal Cards */}
          {(data?.stocks || []).map((stock, i) => (
            <SignalCard
              key={stock.symbol}
              stock={stock}
              rank={i + 1}
              onPaperTrade={handleExecute}
            />
          ))}

          {/* Historical Picks Toggle */}
          {history.length > 0 && (
            <div className="mt-2">
              <button
                onClick={() => setShowHistory(v => !v)}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg border border-white/10 hover:bg-white/5 transition-colors"
                data-testid="mc-history-toggle"
              >
                <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                  <Trophy size={10} className="text-amber-400" weight="fill" />
                  Historical Picks ({history.length} days)
                </span>
                {showHistory ? <CaretUp size={11} className="text-zinc-500" /> : <CaretDown size={11} className="text-zinc-500" />}
              </button>

              {showHistory && (
                <div className="mt-1 space-y-1.5">
                  {/* Win Rate Stats Bar */}
                  <WinStatsBar stats={winStats} />

                  <div className="border border-white/10 rounded-lg overflow-hidden">
                    {history.map((day, i) => (
                      <HistoryRow key={i} day={day} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
