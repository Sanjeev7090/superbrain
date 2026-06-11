import React, { useState, useCallback, useEffect, useRef } from 'react';
import axios from 'axios';
import { X, ArrowsClockwise, ChartLine, Lightning, CaretUp, CaretDown } from '@phosphor-icons/react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api/deltadash`;

/* ── Colour helpers ─────────────────────────────────────────────── */
const totalColor = (total) => {
  if (total >= 200) return { bg: 'rgba(27,94,32,0.90)',    text: '#A5D6A7' };
  if (total >= 160) return { bg: 'rgba(46,125,50,0.80)',   text: '#C8E6C9' };
  if (total >= 130) return { bg: 'rgba(56,142,60,0.70)',   text: '#DCEDC8' };
  if (total >= 100) return { bg: 'rgba(76,175,80,0.55)',   text: '#F1F8E9' };
  if (total >= 70)  return { bg: 'rgba(139,195,74,0.35)',  text: '#F9FBE7' };
  if (total >= 40)  return { bg: 'rgba(255,235,59,0.20)',  text: '#FFFDE7' };
  return               { bg: 'rgba(183,28,28,0.35)',        text: '#FFCDD2' };
};

const cellBg = (score) => {
  if (score >= 40) return '#1B5E20';
  if (score >= 30) return '#2E7D32';
  if (score >= 20) return '#388E3C';
  if (score >= 12) return '#558B2F';
  if (score >= 5)  return '#33691E';
  return '#4A1010';
};

const fmtNum = (v, dp = 2) =>
  v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp });

/* ── Sub-components ─────────────────────────────────────────────── */
const ScoreCell = ({ value }) => (
  <td
    className="px-2 py-1.5 text-center text-xs font-bold tabular-nums"
    style={{ background: cellBg(value), color: '#fff' }}
  >
    {value}
  </td>
);

const TotalCell = ({ total }) => {
  const { bg, text } = totalColor(total);
  return (
    <td
      className="px-2 py-1.5 text-center text-xs font-black tabular-nums"
      style={{ background: bg, color: text }}
    >
      {total}
    </td>
  );
};

const Row = ({ row, onSelect }) => {
  const { bg } = totalColor(row.total);
  const pos = row.pct_chg >= 0;
  return (
    <tr
      className="border-t border-white/5 cursor-pointer hover:brightness-125 transition-all"
      style={{ background: bg }}
      onClick={() => onSelect(row)}
      data-testid={`dd-row-${row.name}`}
    >
      <td className="px-3 py-1.5 text-xs font-bold text-white whitespace-nowrap">{row.name}</td>
      <TotalCell total={row.total} />
      <ScoreCell value={row.oly} />
      <ScoreCell value={row.i125} />
      <ScoreCell value={row.i75} />
      <ScoreCell value={row.i25} />
      <ScoreCell value={row.i15} />
      <ScoreCell value={row.i5} />
      <td className="px-2 py-1.5 text-right text-xs font-mono text-white tabular-nums">
        {fmtNum(row.curr_rate, 2)}
      </td>
      <td className={`px-2 py-1.5 text-right text-xs font-mono tabular-nums ${pos ? 'text-emerald-300' : 'text-rose-300'}`}>
        {pos ? '+' : ''}{fmtNum(row.rs_chg, 2)}
      </td>
      <td className={`px-2 py-1.5 text-right text-xs font-mono tabular-nums ${pos ? 'text-emerald-300' : 'text-rose-300'}`}>
        <span className="flex items-center justify-end gap-0.5">
          {pos ? <CaretUp size={10} weight="fill" /> : <CaretDown size={10} weight="fill" />}
          {Math.abs(row.pct_chg).toFixed(2)}%
        </span>
      </td>
      <td className="px-2 py-1.5 text-right text-xs font-mono text-slate-300 tabular-nums">
        {fmtNum(row.atr14, 2)}
      </td>
    </tr>
  );
};

const TableHeader = () => (
  <thead>
    <tr className="text-[10px] font-bold uppercase tracking-wider text-slate-400 bg-slate-900/80">
      <th className="px-3 py-2 text-left sticky left-0 bg-slate-900/95 z-10">Name</th>
      <th className="px-2 py-2 text-center">Total</th>
      <th className="px-2 py-2 text-center">Oly</th>
      <th className="px-2 py-2 text-center">I-125</th>
      <th className="px-2 py-2 text-center">I-75</th>
      <th className="px-2 py-2 text-center">I-25</th>
      <th className="px-2 py-2 text-center">I-15</th>
      <th className="px-2 py-2 text-center">I-5</th>
      <th className="px-2 py-2 text-right">CurrRate</th>
      <th className="px-2 py-2 text-right">RsChg</th>
      <th className="px-2 py-2 text-right">%Chg</th>
      <th className="px-2 py-2 text-right">Oly14ATR</th>
    </tr>
  </thead>
);

/* ── Score legend ────────────────────────────────────────────────── */
const Legend = () => (
  <div className="flex items-center gap-3 text-[10px] text-slate-400 flex-wrap">
    <span className="font-semibold">Score:</span>
    {[['≥200','#1B5E20'],['160','#2E7D32'],['130','#388E3C'],['100','#43A047'],['70','#558B2F'],['<40','#4A1010']].map(([label, bg]) => (
      <span key={label} className="flex items-center gap-1">
        <span className="inline-block w-3 h-3 rounded-sm" style={{ background: bg }} />
        {label}
      </span>
    ))}
    <span className="text-slate-500 ml-1">| Max per column: 50 pts</span>
  </div>
);

/* ── Main Component ─────────────────────────────────────────────── */
const DeltaDashScoreboard = ({ onClose, onSelectStock }) => {
  const [activeTab, setActiveTab] = useState('all');
  const [data, setData]           = useState(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [sortCol, setSortCol]     = useState('total');
  const [sortAsc, setSortAsc]     = useState(false);
  const [liveMode, setLiveMode]   = useState(false);
  const liveTimerRef              = useRef(null);

  const runScan = useCallback(async (forceRefresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${API}/scoreboard`, {
        params: { refresh: forceRefresh },
        timeout: 120000,
      });
      setData(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Scan failed');
    } finally {
      setLoading(false);
    }
  }, []);

  // Live mode: refresh every 60s
  useEffect(() => {
    if (liveTimerRef.current) clearInterval(liveTimerRef.current);
    if (liveMode) {
      liveTimerRef.current = setInterval(() => runScan(true), 60_000);
    }
    return () => { if (liveTimerRef.current) clearInterval(liveTimerRef.current); };
  }, [liveMode, runScan]);

  // Auto-refresh every 5 min passively
  useEffect(() => {
    const t = setInterval(() => {
      if (!liveMode) runScan(true);
    }, 300_000);
    return () => clearInterval(t);
  }, [liveMode, runScan]);

  const handleRowSelect = useCallback((row) => {
    if (onSelectStock) {
      const stockObj = {
        ticker: row.ticker,
        name:   row.name,
        type:   row.ticker.startsWith('^') ? 'INDEX' : 'NSE',
      };
      onSelectStock(stockObj);
    }
    onClose();
  }, [onClose, onSelectStock]);

  const filterAndSort = (rows) => {
    let filtered = rows || [];
    if (activeTab === 'positive') filtered = filtered.filter(r => r.total >= 100);
    if (activeTab === 'negative') filtered = filtered.filter(r => r.total < 100);
    return [...filtered].sort((a, b) => {
      const av = a[sortCol] ?? 0, bv = b[sortCol] ?? 0;
      return sortAsc ? av - bv : bv - av;
    });
  };

  const handleSort = (col) => {
    if (col === sortCol) setSortAsc(!sortAsc);
    else { setSortCol(col); setSortAsc(false); }
  };

  const TABS = [
    { id: 'all',      label: 'All' },
    { id: 'positive', label: '+ve Dashboard' },
    { id: 'negative', label: '-ve Dashboard' },
  ];

  const filteredStocks  = filterAndSort(data?.stocks);
  const filteredIndices = filterAndSort(data?.indices);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4"
      onClick={onClose}
    >
      <div
        className="bg-[#0A0F1E] border border-white/10 rounded-xl w-full max-w-[98vw] max-h-[96vh] overflow-hidden flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}
        data-testid="deltadash-modal"
      >
        {/* ── Header ── */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 bg-gradient-to-r from-blue-950/60 via-indigo-950/40 to-slate-900/60 shrink-0">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-indigo-600/30 border border-indigo-500/40">
              <span className="text-sm font-black text-indigo-300">dd</span>
            </div>
            <div>
              <h2 className="text-base font-black text-white tracking-tight">DeltaDash Analysis Scoreboard</h2>
              <p className="text-[10px] text-slate-400">Multi-timeframe technical scoring · NSE F&amp;O Universe</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {data?.generated_at && (
              <span className="text-[10px] text-slate-500 hidden sm:block">
                Last scan: {new Date(data.generated_at).toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-white p-1.5 rounded hover:bg-white/10 transition"
              data-testid="dd-close-btn"
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {/* ── Tab bar ── */}
        <div className="flex items-center gap-1 px-4 py-2 border-b border-white/10 bg-slate-900/40 shrink-0 overflow-x-auto scrollbar-none">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              data-testid={`dd-tab-${t.id}`}
              className={`px-3 py-1 text-xs font-bold rounded transition whitespace-nowrap ${
                activeTab === t.id
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-white hover:bg-white/8'
              }`}
            >
              {t.label}
            </button>
          ))}

          <div className="ml-auto flex items-center gap-2 shrink-0">
            <Legend />
            {/* Live toggle */}
            <button
              onClick={() => setLiveMode(p => !p)}
              data-testid="dd-live-btn"
              className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold transition border ${
                liveMode
                  ? 'bg-emerald-600/30 border-emerald-500/60 text-emerald-300'
                  : 'border-white/10 text-slate-500 hover:text-white hover:bg-white/8'
              }`}
              title={liveMode ? 'Live: refreshes every 60s' : 'Enable Live mode'}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${liveMode ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'}`} />
              LIVE
            </button>
            <button
              onClick={() => runScan(true)}
              disabled={loading}
              data-testid="dd-scan-btn"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-bold transition"
            >
              {loading
                ? <ArrowsClockwise size={14} className="animate-spin" />
                : <Lightning size={14} weight="fill" />}
              {loading ? 'Scanning…' : data ? 'Refresh' : 'Scan Now'}
            </button>
          </div>
        </div>

        {/* ── Body ── */}
        <div className="flex-1 overflow-auto">
          {!data && !loading && (
            <div className="flex flex-col items-center justify-center h-64 text-slate-400 gap-3">
              <div className="w-16 h-16 rounded-2xl bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center">
                <span className="text-2xl font-black text-indigo-400">dd</span>
              </div>
              <p className="text-sm font-semibold">Click <strong>Scan Now</strong> to load the scoreboard</p>
              <p className="text-xs text-slate-500">Scores 44+ symbols across 6 timeframes</p>
              <button
                onClick={() => runScan(false)}
                className="mt-2 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-bold transition"
                data-testid="dd-initial-scan-btn"
              >
                Start Scan
              </button>
            </div>
          )}

          {loading && (
            <div className="flex flex-col items-center justify-center h-64 text-slate-400 gap-3">
              <ArrowsClockwise size={40} className="animate-spin text-indigo-400" />
              <p className="text-sm">Scanning 44+ symbols across 6 timeframes…</p>
              <p className="text-xs text-slate-500">This may take 30-60 seconds</p>
            </div>
          )}

          {error && (
            <div className="p-4 text-rose-400 text-sm text-center">{error}</div>
          )}

          {data && !loading && (
            <div className="space-y-0">
              {/* ── Indices section ── */}
              {filteredIndices.length > 0 && (
                <div>
                  <div className="px-4 py-1.5 bg-slate-800/60 border-b border-white/5">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">NSE Indices</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <TableHeader />
                      <tbody>
                        {filteredIndices.map(row => (
                          <Row key={row.name} row={row} onSelect={handleRowSelect} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* ── Stocks section ── */}
              {filteredStocks.length > 0 && (
                <div>
                  <div className="px-4 py-1.5 bg-slate-800/60 border-b border-white/5 flex items-center gap-3">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">
                      NSE F&amp;O Stocks ({filteredStocks.length})
                    </span>
                    <span className="text-[9px] text-slate-500 flex items-center gap-1">
                      <ChartLine size={10} />
                      Click row to load chart
                    </span>
                    {/* Sort controls */}
                    <div className="ml-auto flex items-center gap-1">
                      {['total','oly','i5','i15','i25','i75','i125','pct_chg'].map(col => (
                        <button
                          key={col}
                          onClick={() => handleSort(col)}
                          className={`px-1.5 py-0.5 text-[9px] font-bold rounded transition ${
                            sortCol === col ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-white hover:bg-white/10'
                          }`}
                        >
                          {col === 'pct_chg' ? '%Chg' : col.toUpperCase()}
                          {sortCol === col && (sortAsc ? '↑' : '↓')}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <TableHeader />
                      <tbody>
                        {filteredStocks.map(row => (
                          <Row key={row.name} row={row} onSelect={handleRowSelect} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {filteredStocks.length === 0 && filteredIndices.length === 0 && (
                <div className="p-8 text-center text-slate-400 text-sm">
                  No results for "{activeTab === 'positive' ? '+ve' : '-ve'}" filter.
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="px-4 py-2 border-t border-white/5 bg-slate-900/60 flex items-center justify-between shrink-0">
          <span className="text-[10px] text-slate-500">
            Timeframes: Oly=Daily · I-125=90m · I-75=60m · I-25=30m · I-15=15m · I-5=5m · Max per column: 50pts
          </span>
          {data && (
            <span className="text-[10px] text-slate-500">
              Universe: {data.universe_size} symbols · Cached 5 min
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

export default DeltaDashScoreboard;
