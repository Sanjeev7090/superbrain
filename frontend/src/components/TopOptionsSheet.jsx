import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { X, CaretUp, CaretDown, Spinner, ChartBar } from '@phosphor-icons/react';
import PCRGauge from './PCRGauge';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

/**
 * Bottom-sheet style modal showing the top traded options for an index.
 * Filter pills: All / Call / Put.
 * On row tap, calls onOptionSelect(option) so parent can open the chart.
 */
const TopOptionsSheet = ({ symbol, name, onClose, onOptionSelect }) => {
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState('all'); // all | call | put
  const [sortBy, setSortBy] = useState('volume'); // volume | oi | change
  const [selectedExpiry, setSelectedExpiry] = useState(null);
  const [showPCR, setShowPCR] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const intervalRef = useRef(null);

  const fetchOptions = async (expiry = null) => {
    if (!symbol) return;
    try {
      setError(null);
      const params = { limit: 20, option_type: filter, sort_by: sortBy };
      if (expiry) params.expiry = expiry;
      const res = await axios.get(`${API}/indices/top-options/${symbol}`, { params });
      setData(res.data);
      if (!selectedExpiry && res.data?.nearest_expiry) setSelectedExpiry(res.data.nearest_expiry);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load options');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    setData(null);
    setSelectedExpiry(null);
    fetchOptions();
    intervalRef.current = setInterval(() => fetchOptions(selectedExpiry), 60000);
    return () => clearInterval(intervalRef.current);
  }, [symbol, filter, sortBy]); // eslint-disable-line

  const handleExpiryChange = (exp) => {
    setSelectedExpiry(exp);
    setLoading(true);
    fetchOptions(exp);
  };

  if (!symbol) return null;

  const options = data?.options || [];
  const underlyingPrice = data?.underlying_price || 0;
  const isSensex = symbol === 'SENSEX';
  const allExpiries = data?.all_expiries || [];
  const indiaVix = data?.india_vix;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
      data-testid="top-options-sheet"
    >
      <div
        className="w-full sm:max-w-2xl bg-[#0B0B0B] border-t sm:border border-white/10 rounded-t-2xl sm:rounded-2xl shadow-2xl flex flex-col max-h-[90vh] sm:max-h-[80vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-4 pt-3 pb-2 border-b border-white/10 shrink-0">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-black uppercase tracking-wide text-white">
                {name || symbol} Options
              </h2>
              {underlyingPrice > 0 && (
                <span className="text-[10px] font-mono text-zinc-400 border border-white/10 px-1.5 py-0.5 rounded">
                  ₹{underlyingPrice.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                </span>
              )}
              {isSensex && indiaVix != null && (
                <span className="text-[10px] font-mono text-cyan-400 border border-cyan-400/30 bg-cyan-400/10 px-1.5 py-0.5 rounded">
                  VIX {indiaVix.toFixed(1)}%
                </span>
              )}
              {isSensex && data?.is_live_derived && (
                <span className="text-[9px] font-bold uppercase tracking-wider text-emerald-400 border border-emerald-400/30 bg-emerald-400/10 px-1.5 py-0.5 rounded">
                  Live-Derived
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowPCR(p => !p)}
                className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-bold transition-all ${
                  showPCR ? 'bg-violet-500/20 text-violet-300 border border-violet-500/40' : 'bg-zinc-800/60 text-zinc-400 border border-white/10 hover:text-white'
                }`}
                data-testid="pcr-toggle-btn"
              >
                <ChartBar size={11} weight="bold" /> PCR
              </button>
              <button
                onClick={onClose}
                className="p-1.5 hover:bg-white/10 rounded transition-colors"
                data-testid="close-options-sheet"
              >
                <X size={16} weight="bold" className="text-zinc-400" />
              </button>
            </div>
          </div>

          {/* Expiry selector (for SENSEX — multiple Thursday expiries) */}
          {isSensex && allExpiries.length > 1 && (
            <div className="flex gap-1.5 overflow-x-auto pb-1 mb-2 scrollbar-hide">
              {allExpiries.map((exp) => (
                <button
                  key={exp}
                  onClick={() => handleExpiryChange(exp)}
                  className={`shrink-0 px-2.5 py-1 text-[10px] font-mono font-semibold rounded-lg border transition-all ${
                    (selectedExpiry || data?.nearest_expiry) === exp
                      ? 'bg-violet-500/20 text-violet-300 border-violet-500/50'
                      : 'bg-transparent text-zinc-500 border-white/10 hover:border-white/20 hover:text-zinc-300'
                  }`}
                  data-testid={`expiry-btn-${exp}`}
                >
                  {exp}
                </button>
              ))}
            </div>
          )}

          {/* Non-SENSEX expiry + sort */}
          {!isSensex && data?.nearest_expiry && (
            <div className="text-[10px] text-zinc-500 font-mono mb-2">
              Expiry: <span className="text-[#00E676]">{data.nearest_expiry}</span>
              {' · '}Sorted by{' '}
              <button
                onClick={() => setSortBy(sortBy === 'volume' ? 'oi' : sortBy === 'oi' ? 'change' : 'volume')}
                className="underline decoration-dotted hover:text-white"
              >
                {sortBy.toUpperCase()}
              </button>
            </div>
          )}

          {/* SENSEX live-derived info */}
          {isSensex && data?.is_live_derived && (
            <div className="mx-0 mb-2 flex items-start gap-2 rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2">
              <p className="text-[9px] text-cyan-400/80 leading-tight">
                Prices derived from live SENSEX spot + India VIX {indiaVix?.toFixed(1)}% · Black-Scholes · BSE Thursday expiry schedule
              </p>
            </div>
          )}

          {/* PCR Gauge — collapsible */}
          {showPCR && (
            <div className="mb-2">
              <PCRGauge symbol={symbol} />
            </div>
          )}

          {/* Filter pills */}
          <div className="flex gap-1.5">
            {[
              { id: 'all', label: 'All' },
              { id: 'call', label: 'Call' },
              { id: 'put', label: 'Put' },
            ].map((f) => (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                className={`flex-1 sm:flex-initial sm:min-w-[64px] px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider rounded transition-all ${
                  filter === f.id
                    ? f.id === 'call'
                      ? 'bg-[#00E676]/20 text-[#00E676] border border-[#00E676]/50'
                      : f.id === 'put'
                      ? 'bg-[#FF3D71]/20 text-[#FF3D71] border border-[#FF3D71]/50'
                      : 'bg-white/10 text-white border border-white/20'
                    : 'bg-transparent text-zinc-500 border border-white/10 hover:bg-white/5 hover:text-zinc-300'
                }`}
                data-testid={`filter-${f.id}`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="py-8 flex flex-col items-center justify-center gap-2 text-zinc-500">
              <Spinner size={24} className="animate-spin" />
              <span className="text-xs">Loading option chain…</span>
            </div>
          )}

          {!loading && error && (
            <div className="py-8 text-center text-[#FF3D71] text-xs px-4">
              <div className="font-semibold mb-1">Couldn&apos;t load options</div>
              <div className="opacity-70">{error}</div>
            </div>
          )}

          {!loading && !error && options.length === 0 && (
            <div className="py-8 text-center text-zinc-500 text-xs">No options found</div>
          )}

          {!loading && !error && options.length > 0 && (
            <div className="divide-y divide-white/5">
              {options.map((opt, i) => {
                const isCall = opt.type === 'CE';
                const up = (opt.change_pct || 0) >= 0;
                return (
                  <button
                    key={`${opt.strike}-${opt.type}-${i}`}
                    onClick={() => onOptionSelect?.(opt)}
                    className="w-full px-4 py-3 flex items-center justify-between hover:bg-white/[0.04] active:bg-white/[0.08] transition-colors text-left"
                    data-testid={`option-row-${i}`}
                  >
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      <div
                        className={`w-1 h-10 rounded-full shrink-0 ${
                          isCall ? 'bg-[#00E676]' : 'bg-[#FF3D71]'
                        }`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-bold text-white truncate flex items-center gap-1.5">
                          {opt.instrument}
                        </div>
                        <div className="text-[10px] text-zinc-500 font-mono mt-0.5 truncate">
                          {opt.expiry_display || opt.expiry}
                          {opt.volume > 0 && (
                            <span className="ml-2">
                              Vol: {opt.volume > 100000 ? `${(opt.volume / 100000).toFixed(1)}L` : opt.volume.toLocaleString('en-IN')}
                            </span>
                          )}
                          {opt.iv > 0 && <span className="ml-2">IV: {opt.iv.toFixed(1)}%</span>}
                          {opt.delta != null && <span className="ml-2">Δ {opt.delta > 0 ? '+' : ''}{opt.delta}</span>}
                          {opt.theta != null && <span className="ml-2">θ {opt.theta.toFixed(1)}</span>}
                        </div>
                      </div>
                    </div>
                    <div className="text-right shrink-0 ml-2">
                      <div className="text-sm font-bold font-mono text-white tabular-nums">
                        ₹{opt.last_price?.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                      </div>
                      <div
                        className={`text-[10px] font-mono font-semibold flex items-center justify-end gap-0.5 ${
                          up ? 'text-[#00E676]' : 'text-[#FF3D71]'
                        }`}
                      >
                        {up ? <CaretUp size={9} weight="fill" /> : <CaretDown size={9} weight="fill" />}
                        {Math.abs(opt.change_pct || 0).toFixed(2)}%
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-white/10 text-[9px] text-zinc-600 font-mono text-center shrink-0">
          {isSensex
            ? `Live-Derived · SENSEX spot + India VIX ${indiaVix?.toFixed(1) || '—'}% · Refreshes every 60s`
            : 'Live NSE option chain · refreshes every 60s'}
        </div>
      </div>
    </div>
  );
};

export default TopOptionsSheet;
