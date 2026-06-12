import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { CaretUp, CaretDown } from '@phosphor-icons/react';
import { useMultiTick } from '../hooks/useLiveTick';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// yfinance symbols for NSE indices
const INDEX_WS_SYMBOLS = ["^NSEI", "^BSESN", "^NSEBANK"];

// Map REST data key → WS symbol for merging
const INDEX_WS_MAP = {
  NIFTY:      "^NSEI",
  SENSEX:     "^BSESN",
  BANKNIFTY:  "^NSEBANK",
};

/**
 * Horizontal ticker bar showing live data for NIFTY 50, SENSEX, BANK NIFTY.
 * Uses WebSocket tick streaming (2s updates) with REST fallback (15s).
 * Tapping any index calls `onIndexClick(symbol, name)` so the parent can
 * open a "Top Options" sheet for that index.
 */
const IndicesTickerBar = ({ onIndexClick }) => {
  const [indices, setIndices] = useState([]);
  const [loading, setLoading] = useState(true);
  const intervalRef = useRef(null);

  // WebSocket live ticks — 2-second updates
  const { ticks: wsTicks, connected: wsConnected } = useMultiTick(INDEX_WS_SYMBOLS);

  const fetchIndices = async () => {
    try {
      const res = await axios.get(`${API}/indices/live`);
      setIndices(res.data?.indices || []);
    } catch (e) {
      // silent — keep stale data
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchIndices();
    intervalRef.current = setInterval(fetchIndices, 15000); // REST fallback every 15s
    return () => clearInterval(intervalRef.current);
  }, []);

  if (loading && indices.length === 0) {
    return (
      <div className="border-b border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-[#0B0B0B] px-2 py-2 flex gap-2 overflow-x-auto scrollbar-none shrink-0">
        {[1, 2, 3].map((i) => (
          <div key={i} className="min-w-[140px] h-[44px] rounded-md bg-slate-200 dark:bg-white/[0.04] animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div
      className="border-b border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-[#0B0B0B] px-2 py-2 flex gap-2 overflow-x-auto scrollbar-none shrink-0 transition-colors duration-200"
      data-testid="indices-ticker-bar"
    >
      {/* WebSocket live status dot */}
      <div className="flex items-center self-center px-1" title={wsConnected ? 'Live WebSocket (2s)' : 'Polling (15s)'}>
        <span
          className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500'}`}
          data-testid="ws-live-dot"
        />
      </div>

      {indices.map((idx) => {
        const up = (idx.change || 0) >= 0;
        const supportsOptions = idx.symbol === 'NIFTY' || idx.symbol === 'BANKNIFTY' || idx.symbol === 'SENSEX';

        // Merge WebSocket live tick if available
        const wsSym  = INDEX_WS_MAP[idx.symbol];
        const wsTick = wsSym ? wsTicks[wsSym] : null;
        const price    = wsTick?.price      ?? idx.price;
        const changePct = wsTick?.change_pct ?? idx.change_pct;
        const isUp     = (changePct ?? 0) >= 0;
        const isLive   = !!wsTick;

        return (
          <button
            key={idx.key}
            onClick={() => supportsOptions && onIndexClick?.(idx.symbol, idx.name)}
            disabled={!supportsOptions}
            className={`min-w-[150px] flex-shrink-0 text-left rounded-md border px-3 py-2 transition-all ${
              supportsOptions
                ? 'border-slate-200 dark:border-white/10 bg-white dark:bg-white/[0.03] hover:bg-slate-50 dark:hover:bg-white/[0.08] hover:border-[#00E676]/40 active:scale-[0.98] cursor-pointer'
                : 'border-slate-100 dark:border-white/5 bg-slate-50 dark:bg-white/[0.02] opacity-70 cursor-not-allowed'
            }`}
            data-testid={`index-pill-${idx.key}`}
          >
            <div className="flex items-center justify-between mb-0.5">
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-700 dark:text-white/90 truncate">
                {idx.name}
              </span>
              <div className="flex items-center gap-1">
                {isLive && (
                  <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" title="Live tick" />
                )}
                {supportsOptions && (
                  <span className="text-[8px] font-mono text-[#00E676]/80">OPT</span>
                )}
              </div>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-bold font-mono text-slate-900 dark:text-white tabular-nums">
                {price?.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
              </span>
              <span
                className={`text-[10px] font-mono font-semibold flex items-center gap-0.5 ${
                  isUp ? 'text-[#00E676]' : 'text-[#FF3D71]'
                }`}
              >
                {isUp ? <CaretUp size={9} weight="fill" /> : <CaretDown size={9} weight="fill" />}
                {Math.abs(changePct || 0).toFixed(2)}%
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
};

export default IndicesTickerBar;
