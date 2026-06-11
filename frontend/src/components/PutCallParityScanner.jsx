import React, { useState } from 'react';
import axios from 'axios';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, CartesianGrid,
} from 'recharts';
import { X, MagnifyingGlass, Spinner, TrendUp, TrendDown, Lightning } from '@phosphor-icons/react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api/options`;

const SIGNAL_STYLE = {
  CONVERSION:         { label: 'CONVERSION',          color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30' },
  REVERSE_CONVERSION: { label: 'REVERSE CONVERSION',  color: 'text-rose-400',    bg: 'bg-rose-500/10',    border: 'border-rose-500/30' },
  FAIRLY_PRICED:     { label: 'FAIR',                 color: 'text-slate-400',   bg: 'bg-slate-500/10',   border: 'border-slate-500/30' },
};

const fmtINR = (v) => `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const fmtPct = (v) => `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(3)}%`;

const PutCallParityScanner = ({ onClose }) => {
  const [scanData, setScanData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedRowIdx, setSelectedRowIdx] = useState(0);

  const runScan = async () => {
    setLoading(true);
    setError(null);
    setScanData(null);
    try {
      const res = await axios.get(`${API}/parity-scanner`, {
        params: {
          symbols: 'NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX',
          strikes_around_atm: 10,
          top: 15,
        },
        timeout: 45000,
      });
      setScanData(res.data);
      setSelectedRowIdx(0);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Scan failed');
    } finally {
      setLoading(false);
    }
  };

  // Build chart data either from `best` (initial) or from selected row in top list
  const buildChartFor = (row) => {
    if (!row) return [];
    // For the best row we already have chart_data from backend; for other rows we re-derive locally
    if (row.chart_data) {
      const cd = row.chart_data;
      return cd.stock_prices.map((price, i) => ({
        price,
        stock: cd.long_stock[i],
        call:  cd.long_call[i],
        put:   cd.long_put[i],
      }));
    }
    // Local payoff for non-best rows
    const S = row.spot, X = row.strike, C = row.call_price, P = row.put_price;
    const lo = S * 0.7, hi = S * 1.3;
    const n = 60, step = (hi - lo) / (n - 1);
    const data = [];
    for (let i = 0; i < n; i++) {
      const sp = lo + i * step;
      data.push({
        price: +sp.toFixed(2),
        stock: +(sp - S).toFixed(2),
        call:  +(Math.max(sp - X, 0) - C).toFixed(2),
        put:   +(Math.max(X - sp, 0) - P).toFixed(2),
      });
    }
    return data;
  };

  const topRows = scanData?.top || [];
  const selectedRow = topRows[selectedRowIdx] || scanData?.best;
  const chartData = buildChartFor(selectedRow);

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-2 sm:p-4" onClick={onClose}>
      <div
        className="bg-slate-900 border border-white/10 rounded-xl w-full max-w-6xl max-h-[95vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
        data-testid="parity-scanner-modal"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10 bg-gradient-to-r from-indigo-900/40 via-purple-900/30 to-fuchsia-900/40">
          <div className="flex items-center gap-2">
            <Lightning size={22} weight="fill" className="text-yellow-400" />
            <div>
              <h2 className="text-lg font-bold text-white">F&amp;O Put-Call Parity Scanner</h2>
              <p className="text-xs text-slate-400">Live arbitrage detection across NIFTY • BANKNIFTY • FINNIFTY • MIDCPNIFTY • SENSEX</p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1" data-testid="parity-close-btn">
            <X size={22} />
          </button>
        </div>

        {/* Toolbar */}
        <div className="px-5 py-3 border-b border-white/10 flex items-center gap-3 flex-wrap">
          <button
            onClick={runScan}
            disabled={loading}
            data-testid="parity-scan-btn"
            className="px-4 py-2 rounded-lg bg-gradient-to-r from-emerald-500 to-teal-500 text-white font-semibold flex items-center gap-2 hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            {loading ? <Spinner size={18} className="animate-spin" /> : <MagnifyingGlass size={18} weight="bold" />}
            {loading ? 'Scanning entire F&O universe…' : 'One-Click Scan F&O Parity'}
          </button>
          {scanData?.generated_at && (
            <span className="text-xs text-slate-400">
              Last scan: {new Date(scanData.generated_at).toLocaleTimeString()}
            </span>
          )}
          {error && <span className="text-xs text-rose-400">⚠ {error}</span>}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {!scanData && !loading && (
            <div className="text-center py-16 text-slate-400">
              <Lightning size={48} className="mx-auto mb-3 text-yellow-400/50" />
              <p className="text-sm">Click <b>One-Click Scan</b> to discover the best Put-Call Parity arbitrage right now.</p>
              <p className="text-xs mt-1 text-slate-500">Formula: C + X·e^(-rT) = P + S</p>
            </div>
          )}

          {loading && (
            <div className="text-center py-16 text-slate-400">
              <Spinner size={42} className="mx-auto animate-spin text-emerald-400 mb-3" />
              <p className="text-sm">Pulling live option chains and computing parity…</p>
            </div>
          )}

          {scanData?.best && (
            <>
              {/* Best Opportunity Card */}
              <BestOppCard row={selectedRow} isOverride={selectedRowIdx !== 0} />

              {/* Payoff chart */}
              <div className="bg-slate-800/40 rounded-lg p-3 border border-white/5">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-semibold text-white">Payoff Diagram — {selectedRow.underlying} {selectedRow.strike}</h3>
                  <span className="text-xs text-slate-400">
                    Spot {fmtINR(selectedRow.spot)} · Expiry {selectedRow.expiry}
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                    <XAxis dataKey="price" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                    <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                      labelStyle={{ color: '#cbd5e1' }}
                    />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <ReferenceLine x={selectedRow.strike} stroke="#fbbf24" strokeDasharray="4 4" label={{ value: 'Strike', fill: '#fbbf24', fontSize: 10 }} />
                    <ReferenceLine y={0} stroke="#475569" />
                    <Line type="monotone" dataKey="stock" stroke="#3b82f6" name="Long Stock" dot={false} strokeWidth={2} />
                    <Line type="monotone" dataKey="call"  stroke="#22c55e" name="Long Call"  dot={false} strokeWidth={2} />
                    <Line type="monotone" dataKey="put"   stroke="#ef4444" name="Long Put"   dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Top opportunities table */}
              <div className="bg-slate-800/40 rounded-lg border border-white/5 overflow-hidden">
                <div className="px-4 py-2 border-b border-white/5 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-white">Top {topRows.length} Arbitrage Opportunities</h3>
                  <span className="text-xs text-slate-400">Click a row to view payoff</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs" data-testid="parity-top-table">
                    <thead className="bg-slate-900/60 text-slate-400 uppercase">
                      <tr>
                        <th className="px-3 py-2 text-left">#</th>
                        <th className="px-3 py-2 text-left">Underlying</th>
                        <th className="px-3 py-2 text-right">Strike</th>
                        <th className="px-3 py-2 text-right">Spot</th>
                        <th className="px-3 py-2 text-right">Call</th>
                        <th className="px-3 py-2 text-right">Put</th>
                        <th className="px-3 py-2 text-right">Mispricing</th>
                        <th className="px-3 py-2 text-right">%</th>
                        <th className="px-3 py-2 text-center">Signal</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topRows.map((row, idx) => {
                        const sig = SIGNAL_STYLE[row.parity.signal] || SIGNAL_STYLE.FAIRLY_PRICED;
                        const sel = idx === selectedRowIdx;
                        return (
                          <tr
                            key={`${row.underlying}-${row.strike}-${idx}`}
                            onClick={() => setSelectedRowIdx(idx)}
                            className={`cursor-pointer border-t border-white/5 hover:bg-white/5 ${sel ? 'bg-indigo-500/10' : ''}`}
                          >
                            <td className="px-3 py-2 text-slate-400">{idx + 1}</td>
                            <td className="px-3 py-2 font-semibold text-white">{row.underlying}</td>
                            <td className="px-3 py-2 text-right text-slate-200">{row.strike}</td>
                            <td className="px-3 py-2 text-right text-slate-400">{fmtINR(row.spot)}</td>
                            <td className="px-3 py-2 text-right text-emerald-400">{row.call_price}</td>
                            <td className="px-3 py-2 text-right text-rose-400">{row.put_price}</td>
                            <td className={`px-3 py-2 text-right font-mono ${row.parity.mispricing > 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
                              {row.parity.mispricing > 0 ? '+' : ''}{row.parity.mispricing}
                            </td>
                            <td className={`px-3 py-2 text-right font-mono ${row.parity.mispricing > 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
                              {fmtPct(row.parity.mispricing_pct)}
                            </td>
                            <td className="px-3 py-2 text-center">
                              <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${sig.color} ${sig.bg} ${sig.border}`}>
                                {sig.label}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Per symbol summary */}
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
                {Object.entries(scanData.per_symbol || {}).map(([sym, info]) => (
                  <div key={sym} className="bg-slate-800/40 rounded-lg p-3 border border-white/5">
                    <div className="text-xs text-slate-400">{sym}</div>
                    {info.error ? (
                      <div className="text-xs text-rose-400 mt-1">{info.error}</div>
                    ) : (
                      <>
                        <div className="text-sm font-semibold text-white">{fmtINR(info.spot)}</div>
                        <div className="text-[10px] text-slate-500">{info.strikes_scanned} strikes · {info.expiry}</div>
                        {info.best && (
                          <div className="text-[11px] mt-1 text-amber-400">
                            Best: {info.best.strike} ({fmtPct(info.best.parity.mispricing_pct)})
                          </div>
                        )}
                      </>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

const BestOppCard = ({ row, isOverride }) => {
  if (!row) return null;
  const sig = SIGNAL_STYLE[row.parity.signal] || SIGNAL_STYLE.FAIRLY_PRICED;
  const isPositive = row.parity.mispricing > 0;
  return (
    <div className={`relative rounded-xl p-5 border ${sig.border} ${sig.bg}`} data-testid="best-opp-card">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-400 mb-1">
            {isOverride ? 'Selected Opportunity' : '🏆 Best Arbitrage Opportunity'}
          </div>
          <div className="text-2xl font-bold text-white">
            {row.underlying} {row.strike} <span className="text-sm text-slate-400">· {row.expiry}</span>
          </div>
          <div className={`mt-1 font-semibold ${sig.color} flex items-center gap-1.5`}>
            {isPositive ? <TrendDown size={16} /> : <TrendUp size={16} />}
            {sig.label}
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-slate-400">Mispricing</div>
          <div className={`text-3xl font-bold ${isPositive ? 'text-rose-400' : 'text-emerald-400'}`}>
            {fmtPct(row.parity.mispricing_pct)}
          </div>
          <div className="text-xs text-slate-500">
            {isPositive ? '+' : ''}{row.parity.mispricing} pts ({fmtINR(Math.abs(row.parity.mispricing))})
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 text-xs">
        <Stat label="Spot"   value={fmtINR(row.spot)} />
        <Stat label="Strike" value={row.strike} />
        <Stat label="Call"   value={`₹${row.call_price}`} valueClass="text-emerald-400" />
        <Stat label="Put"    value={`₹${row.put_price}`} valueClass="text-rose-400" />
        <Stat label="Call OI"  value={Number(row.call_oi).toLocaleString('en-IN')} />
        <Stat label="Put OI"   value={Number(row.put_oi).toLocaleString('en-IN')} />
        <Stat label="C + Xe^(-rT)" value={row.parity.left_side} />
        <Stat label="P + S"        value={row.parity.right_side} />
      </div>

      <div className="mt-3 p-3 rounded-lg bg-black/30 border border-white/5">
        <div className="text-xs text-slate-400 mb-1">Recommended Action</div>
        <div className="text-sm text-slate-200">{row.parity.action}</div>
      </div>
    </div>
  );
};

const Stat = ({ label, value, valueClass = 'text-white' }) => (
  <div>
    <div className="text-[10px] text-slate-500 uppercase">{label}</div>
    <div className={`font-semibold ${valueClass}`}>{value}</div>
  </div>
);

export default PutCallParityScanner;
