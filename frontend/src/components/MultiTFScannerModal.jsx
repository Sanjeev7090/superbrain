/**
 * Multi-Timeframe + Multi-Asset Scanner Modal
 * Scans F&O / BankNifty / Finnifty / Midcap / Index stocks across
 * up to 3 timeframes (15M, 1H, 1D) and shows MTF confluence.
 */
import React, { useState, useRef, useCallback } from 'react';
import {
  X, Play, Stop, ChartBar, TrendUp, TrendDown,
  Minus, ArrowsClockwise, DownloadSimple, MagnifyingGlass,
  SortAscending, SortDescending, Funnel,
} from '@phosphor-icons/react';
import { toast } from 'sonner';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// ── Segment options ──────────────────────────────────────────────────────────
const SEGMENTS = [
  { id: 'all',          label: 'All',         color: 'text-zinc-300',    bg: 'bg-zinc-500/20' },
  { id: 'fo',           label: 'F&O',         color: 'text-blue-300',    bg: 'bg-blue-500/20' },
  { id: 'index',        label: 'Indices',     color: 'text-yellow-300',  bg: 'bg-yellow-500/20' },
  { id: 'banknifty',    label: 'BankNifty',   color: 'text-sky-300',     bg: 'bg-sky-500/20' },
  { id: 'finnifty',     label: 'FinNifty',    color: 'text-violet-300',  bg: 'bg-violet-500/20' },
  { id: 'midcap',       label: 'Midcap',      color: 'text-orange-300',  bg: 'bg-orange-500/20' },
  { id: 'cash',         label: 'Cash',        color: 'text-emerald-300', bg: 'bg-emerald-500/20' },
  { id: 'most_active',  label: 'Most Active', color: 'text-pink-300',    bg: 'bg-pink-500/20' },
  { id: 'breakout_15m', label: '15m Breakout', color: 'text-amber-300',  bg: 'bg-amber-500/20' },
];

const TF_OPTIONS = [
  { id: '15m', label: '15 Min' },
  { id: '1h',  label: '1 Hour' },
  { id: '1d',  label: 'Daily'  },
];

// ── Small helpers ────────────────────────────────────────────────────────────
const DirBadge = ({ dir, size = 'sm' }) => {
  if (!dir || dir === 'WAIT') return (
    <span className="px-1 py-0.5 text-[8px] font-bold rounded bg-zinc-700/50 text-zinc-500">WAIT</span>
  );
  const isBuy = dir === 'BUY';
  return (
    <span className={`flex items-center gap-0.5 px-1.5 py-0.5 text-[8px] font-bold rounded ${
      isBuy ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'
    }`}>
      {isBuy ? <TrendUp size={9} weight="bold" /> : <TrendDown size={9} weight="bold" />}
      {dir}
    </span>
  );
};

const ConfluenceDots = ({ count, total }) => (
  <div className="flex items-center gap-0.5">
    {Array.from({ length: total }).map((_, i) => (
      <div key={i} className={`w-2 h-2 rounded-full ${
        i < count ? 'bg-[#00E676]' : 'bg-zinc-700'
      }`} />
    ))}
    <span className="text-[9px] font-mono text-zinc-400 ml-1">{count}/{total}</span>
  </div>
);

const ScoreBar = ({ score }) => {
  const pct = Math.min(Math.max(score, 0), 100);
  const col = pct >= 65 ? 'bg-emerald-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-zinc-600';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div className={`h-full ${col} transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[9px] font-mono text-zinc-400">{pct}</span>
    </div>
  );
};

const TFCell = ({ tfData, tf }) => {
  if (!tfData) return <span className="text-[8px] text-zinc-700">—</span>;
  const { direction, weighted_score, signal_count } = tfData;
  if (direction === 'WAIT' || !direction)
    return <span className="text-[8px] text-zinc-600">—</span>;
  const isBuy = direction === 'BUY';
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className={`text-[8px] font-bold ${isBuy ? 'text-emerald-400' : 'text-red-400'}`}>
        {direction}
      </span>
      <span className="text-[7px] text-zinc-500">{weighted_score}%</span>
    </div>
  );
};

// ── Result row ───────────────────────────────────────────────────────────────
const ResultRow = ({ row, tfs, onSelect, idx }) => {
  const { name, ticker, segment, current_price, dominant_direction,
          mtf_confluence, total_timeframes, overall_score, tf_signals,
          best_entry, best_sl, best_target } = row;
  const isBuy = dominant_direction === 'BUY';
  const segInfo = SEGMENTS.find(s => s.id === segment) || SEGMENTS[0];

  return (
    <tr
      className="border-b border-white/5 hover:bg-white/[0.03] cursor-pointer transition-colors"
      onClick={() => onSelect && onSelect(row)}
      data-testid={`mtf-row-${ticker.replace(/[^a-zA-Z0-9]/g, '')}`}
    >
      <td className="py-2 px-2">
        <div className="flex items-center gap-1.5">
          <span className={`text-[7px] font-bold px-1 py-0.5 rounded uppercase ${segInfo.bg} ${segInfo.color}`}>
            {segInfo.label}
          </span>
          <div>
            <p className="text-[10px] font-bold text-white leading-none">{ticker.replace('.NS','')}</p>
            <p className="text-[8px] text-zinc-500 truncate max-w-[90px]">{name}</p>
          </div>
        </div>
      </td>
      <td className="py-2 px-2 text-[9px] font-mono text-zinc-300">
        ₹{current_price?.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
      </td>
      {/* Per-TF columns */}
      {tfs.map(tf => (
        <td key={tf} className="py-2 px-1.5 text-center">
          <TFCell tfData={tf_signals?.[tf]} tf={tf} />
        </td>
      ))}
      {/* MTF Confluence */}
      <td className="py-2 px-2">
        <ConfluenceDots count={mtf_confluence} total={total_timeframes} />
      </td>
      {/* Overall score */}
      <td className="py-2 px-2">
        <ScoreBar score={overall_score} />
      </td>
      {/* Direction */}
      <td className="py-2 px-2">
        <DirBadge dir={dominant_direction} />
      </td>
      {/* Levels */}
      <td className="py-2 px-2 text-[8px] font-mono">
        <div className="space-y-0.5">
          <p className="text-zinc-400">E: ₹{best_entry?.toFixed(2)}</p>
          <p className="text-red-400">SL: ₹{best_sl?.toFixed(2)}</p>
          <p className="text-emerald-400">T: ₹{best_target?.toFixed(2)}</p>
        </div>
      </td>
    </tr>
  );
};

// ── Main Modal ───────────────────────────────────────────────────────────────
const MultiTFScannerModal = ({ onClose, onStockSelect }) => {
  const [segment,    setSegment]    = useState('fo');
  const [selTFs,     setSelTFs]     = useState(['15m', '1h', '1d']);
  const [scanning,   setScanning]   = useState(false);
  const [results,    setResults]    = useState([]);
  const [progress,   setProgress]   = useState({ current: 0, total: 0, symbol: '' });
  const [done,       setDone]       = useState(false);
  const [search,     setSearch]     = useState('');
  const [dirFilter,  setDirFilter]  = useState('ALL');
  const [sortCol,    setSortCol]    = useState('overall_score');
  const [sortAsc,    setSortAsc]    = useState(false);
  const [minConf,    setMinConf]    = useState(1);    // min MTF confluence

  const esRef = useRef(null);

  const toggleTF = (tf) => {
    setSelTFs(prev =>
      prev.includes(tf) ? (prev.length > 1 ? prev.filter(t => t !== tf) : prev) : [...prev, tf]
    );
  };

  const startScan = useCallback(() => {
    if (esRef.current) { esRef.current.close(); esRef.current = null; }
    setResults([]);
    setDone(false);
    setProgress({ current: 0, total: 0, symbol: '' });
    setScanning(true);

    const tfsParam = selTFs.join(',');
    const url = `${API}/multi-tf-scanner/scan?segment=${segment}&timeframes=${tfsParam}`;
    const es  = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      if (!e.data || e.data === '[DONE]') return;
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === 'progress') {
          setProgress({ current: ev.current, total: ev.total, symbol: ev.symbol });
        } else if (ev.type === 'result') {
          setResults(prev => [...prev, ev]);
        } else if (ev.type === 'done') {
          setDone(true);
          setScanning(false);
          es.close();
          toast.success(`Scan complete — ${ev.total_found} signals in ${ev.total_scanned} assets`);
        }
      } catch {}
    };

    es.onerror = () => {
      setScanning(false);
      es.close();
    };
  }, [segment, selTFs]);

  const stopScan = () => {
    esRef.current?.close();
    esRef.current = null;
    setScanning(false);
    setDone(true);
  };

  const handleSelect = (row) => {
    const ticker = row.ticker.replace('.NS', '').replace('^', '');
    if (onStockSelect) {
      onStockSelect({ ticker: row.ticker, name: row.name });
      toast.success(`Loaded ${ticker}`);
    }
    onClose?.();
  };

  // Filter + sort
  const filtered = results
    .filter(r => {
      if (dirFilter !== 'ALL' && r.dominant_direction !== dirFilter) return false;
      if (r.mtf_confluence < minConf) return false;
      if (search) {
        const q = search.toLowerCase();
        return r.ticker.toLowerCase().includes(q) || r.name.toLowerCase().includes(q);
      }
      return true;
    })
    .sort((a, b) => {
      let va = a[sortCol] ?? 0, vb = b[sortCol] ?? 0;
      if (typeof va === 'string') va = va.charCodeAt(0);
      if (typeof vb === 'string') vb = vb.charCodeAt(0);
      return sortAsc ? va - vb : vb - va;
    });

  const buyCount  = results.filter(r => r.dominant_direction === 'BUY').length;
  const sellCount = results.filter(r => r.dominant_direction === 'SELL').length;
  const fullConf  = results.filter(r => r.mtf_confluence === selTFs.length).length;

  const handleSort = (col) => {
    if (sortCol === col) setSortAsc(p => !p);
    else { setSortCol(col); setSortAsc(false); }
  };

  const downloadCSV = () => {
    const header = ['Ticker','Name','Segment','Price','Direction','MTF_Confluence','Score',
                    ...selTFs.map(tf => `${tf}_dir`), 'Entry','SL','Target'].join(',');
    const rows = filtered.map(r =>
      [r.ticker, r.name, r.segment, r.current_price, r.dominant_direction,
       r.mtf_confluence, r.overall_score,
       ...selTFs.map(tf => r.tf_signals?.[tf]?.direction || 'WAIT'),
       r.best_entry, r.best_sl, r.best_target].join(',')
    );
    const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = `mtf-scan-${new Date().toISOString().slice(0,10)}.csv`; a.click();
  };

  const SortIcon = ({ col }) =>
    sortCol === col
      ? sortAsc ? <SortAscending size={9} className="text-[#00E676]" />
                : <SortDescending size={9} className="text-[#00E676]" />
      : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-2"
      data-testid="mtf-scanner-modal"
    >
      <div className="w-full max-w-5xl max-h-[92vh] flex flex-col rounded-lg border border-white/10 bg-[#0a0a0a] shadow-2xl overflow-hidden">

        {/* ── Header ── */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
          <div className="flex items-center gap-2">
            <ChartBar size={16} weight="fill" className="text-[#00E676]" />
            <div>
              <p className="text-[11px] font-black uppercase tracking-widest text-white">
                Multi-TF Scanner
              </p>
              <p className="text-[8px] text-zinc-500">F&O · Cash · Indices — Multi-Timeframe Confluence</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-white/10 rounded text-zinc-500 hover:text-white transition-colors"
            data-testid="close-mtf-scanner"
          >
            <X size={14} />
          </button>
        </div>

        {/* ── Controls ── */}
        <div className="px-4 py-3 border-b border-white/10 space-y-2.5 shrink-0">
          {/* Segment selector */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[8px] text-zinc-500 uppercase tracking-wider mr-1">Segment:</span>
            {SEGMENTS.map(s => (
              <button
                key={s.id}
                onClick={() => setSegment(s.id)}
                data-testid={`seg-filter-${s.id}`}
                className={`px-2 py-0.5 text-[9px] font-bold rounded transition-all ${
                  segment === s.id
                    ? `${s.bg} ${s.color} ring-1 ring-current`
                    : 'bg-zinc-800 text-zinc-500 hover:bg-zinc-700'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          {/* TF selector + min confluence + start */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-1.5">
              <span className="text-[8px] text-zinc-500 uppercase tracking-wider">Timeframes:</span>
              {TF_OPTIONS.map(tf => (
                <button
                  key={tf.id}
                  onClick={() => toggleTF(tf.id)}
                  data-testid={`tf-toggle-${tf.id}`}
                  className={`px-2 py-0.5 text-[9px] font-bold rounded border transition-all ${
                    selTFs.includes(tf.id)
                      ? 'bg-[#00E676]/20 text-[#00E676] border-[#00E676]/40'
                      : 'bg-zinc-800 text-zinc-500 border-transparent hover:border-zinc-600'
                  }`}
                >
                  {tf.label}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-[8px] text-zinc-500 uppercase tracking-wider">Min Confluence:</span>
              {[1, 2, 3].filter(n => n <= selTFs.length).map(n => (
                <button key={n}
                  onClick={() => setMinConf(n)}
                  data-testid={`min-conf-${n}`}
                  className={`w-6 h-6 text-[9px] font-bold rounded transition-all ${
                    minConf === n ? 'bg-[#00E676] text-black' : 'bg-zinc-800 text-zinc-500 hover:bg-zinc-700'
                  }`}
                >{n}</button>
              ))}
            </div>

            <div className="ml-auto flex items-center gap-2">
              {done && results.length > 0 && (
                <button
                  onClick={downloadCSV}
                  className="flex items-center gap-1 px-2 py-1 text-[9px] font-bold text-zinc-400 hover:text-white rounded bg-zinc-800 hover:bg-zinc-700 transition-all"
                  data-testid="mtf-download-csv"
                >
                  <DownloadSimple size={10} />CSV
                </button>
              )}
              <button
                onClick={scanning ? stopScan : startScan}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-black uppercase rounded transition-all ${
                  scanning
                    ? 'bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30'
                    : 'bg-[#00E676] text-black hover:opacity-90 active:scale-95'
                }`}
                data-testid="start-mtf-scan"
              >
                {scanning
                  ? <><Stop size={10} weight="fill" />Stop</>
                  : <><Play size={10} weight="fill" />{done && results.length > 0 ? 'Re-Scan' : 'Scan'}</>
                }
              </button>
            </div>
          </div>

          {/* Progress bar */}
          {(scanning || (done && progress.total > 0)) && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[8px] text-zinc-500">
                  {scanning
                    ? `Scanning: ${progress.symbol?.replace('.NS','')} (${progress.current}/${progress.total})`
                    : `Scanned ${progress.total} assets`}
                </span>
                <span className="text-[8px] font-mono text-[#00E676]">
                  {progress.total ? Math.round(progress.current / progress.total * 100) : 0}%
                </span>
              </div>
              <div className="h-0.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-[#00E676] to-emerald-400 transition-all duration-300 rounded-full"
                  style={{ width: `${progress.total ? (progress.current / progress.total * 100) : 0}%` }}
                />
              </div>
            </div>
          )}
        </div>

        {/* ── Stats bar ── */}
        {results.length > 0 && (
          <div className="flex items-center gap-4 px-4 py-2 border-b border-white/5 shrink-0 bg-zinc-900/30">
            <span className="text-[9px] text-zinc-500">{filtered.length} results</span>
            <span className="text-[9px] text-emerald-400 font-bold">{buyCount} BUY</span>
            <span className="text-[9px] text-red-400 font-bold">{sellCount} SELL</span>
            <span className="text-[9px] text-[#00E676] font-bold">{fullConf} full-TF aligned</span>

            {/* Direction filter */}
            <div className="flex items-center gap-1 ml-auto">
              {['ALL','BUY','SELL'].map(d => (
                <button key={d}
                  onClick={() => setDirFilter(d)}
                  data-testid={`mtf-dir-${d}`}
                  className={`px-2 py-0.5 text-[8px] font-bold rounded transition-all ${
                    dirFilter === d ? 'bg-zinc-600 text-white' : 'bg-zinc-800 text-zinc-500 hover:bg-zinc-700'
                  }`}>{d}</button>
              ))}
            </div>

            {/* Search */}
            <div className="flex items-center gap-1 bg-zinc-800 rounded px-2 py-0.5">
              <MagnifyingGlass size={9} className="text-zinc-500" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search…"
                className="bg-transparent text-[9px] text-white w-20 outline-none placeholder:text-zinc-600"
                data-testid="mtf-search"
              />
            </div>
          </div>
        )}

        {/* ── Table ── */}
        <div className="flex-1 overflow-y-auto">
          {results.length === 0 && !scanning ? (
            <div className="flex flex-col items-center justify-center h-40 text-center">
              <ChartBar size={24} className="text-zinc-700 mb-2" />
              <p className="text-[10px] text-zinc-500">Select segment + timeframes and click Scan</p>
              <p className="text-[8px] text-zinc-600 mt-1">
                Scans {SEGMENTS.find(s => s.id === segment)?.label || 'selected'} stocks across {selTFs.join(', ')} timeframes
              </p>
              {segment === 'most_active' && (
                <p className="text-[8px] text-pink-300/80 mt-1">
                  Live NSE most-active equities (Volume + Value, deduped) · 15m Breakout strategy active
                </p>
              )}
              {segment === 'breakout_15m' && (
                <p className="text-[8px] text-amber-300/80 mt-1">
                  Donchian-20 breakout + Volume ≥1.3× · only stocks firing the 15m Breakout setup are listed
                </p>
              )}
            </div>
          ) : filtered.length === 0 && done ? (
            <div className="flex items-center justify-center h-24">
              <p className="text-[10px] text-zinc-600">No signals match current filters</p>
            </div>
          ) : (
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-zinc-900/90 backdrop-blur z-10 border-b border-white/5">
                <tr>
                  <th className="py-2 px-2 text-[8px] text-zinc-500 font-bold uppercase">Asset</th>
                  <th
                    className="py-2 px-2 text-[8px] text-zinc-500 font-bold uppercase cursor-pointer hover:text-zinc-300"
                    onClick={() => handleSort('current_price')}
                  >Price <SortIcon col="current_price" /></th>
                  {selTFs.map(tf => (
                    <th key={tf} className="py-2 px-1.5 text-[8px] text-zinc-500 font-bold uppercase text-center">
                      {TF_OPTIONS.find(t => t.id === tf)?.label || tf}
                    </th>
                  ))}
                  <th
                    className="py-2 px-2 text-[8px] text-zinc-500 font-bold uppercase cursor-pointer hover:text-zinc-300"
                    onClick={() => handleSort('mtf_confluence')}
                  >Confluence <SortIcon col="mtf_confluence" /></th>
                  <th
                    className="py-2 px-2 text-[8px] text-zinc-500 font-bold uppercase cursor-pointer hover:text-zinc-300"
                    onClick={() => handleSort('overall_score')}
                  >Score <SortIcon col="overall_score" /></th>
                  <th
                    className="py-2 px-2 text-[8px] text-zinc-500 font-bold uppercase cursor-pointer hover:text-zinc-300"
                    onClick={() => handleSort('dominant_direction')}
                  >Dir <SortIcon col="dominant_direction" /></th>
                  <th className="py-2 px-2 text-[8px] text-zinc-500 font-bold uppercase">Levels</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row, idx) => (
                  <ResultRow
                    key={`${row.ticker}-${idx}`}
                    row={row}
                    tfs={selTFs}
                    onSelect={handleSelect}
                    idx={idx}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="px-4 py-2 border-t border-white/5 shrink-0 flex items-center justify-between">
          <p className="text-[8px] text-zinc-600">
            Weighted confluence: Godzilla(22%) · SMC(20%) · MiroFish(18%) · ExpVol(12%) · <span className="text-pink-300">15m Breakout(10%)</span> · …
          </p>
          {scanning && (
            <div className="flex items-center gap-1 text-[8px] text-[#00E676]">
              <ArrowsClockwise size={9} className="animate-spin" />
              Scanning…
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default MultiTFScannerModal;
