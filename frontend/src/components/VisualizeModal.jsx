import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import * as d3 from 'd3';
import { Treemap, ResponsiveContainer, Tooltip } from 'recharts';
import { X, ChartPieSlice, Graph, GridFour } from '@phosphor-icons/react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const TABS = [
  { id: 'heatmap',  label: 'Market Heatmap',      icon: GridFour },
  { id: 'corr',     label: 'Correlation Matrix',  icon: ChartPieSlice },
  { id: 'network',  label: 'Options Flow Network', icon: Graph },
];

// ── Sector colour scale ─────────────────────────────────────────────────────
const sectorColor = (pct) => {
  if (pct >  3) return '#00c853';
  if (pct >  1) return '#69f0ae';
  if (pct >  0) return '#b9f6ca';
  if (pct > -1) return '#ffcdd2';
  if (pct > -3) return '#ef9a9a';
  return '#c62828';
};

// ── Correlation colour ──────────────────────────────────────────────────────
const corrColor = (v) => {
  if (v > 0.6)  return '#00c853';
  if (v > 0.2)  return '#66bb6a';
  if (v > -0.2) return '#424242';
  if (v > -0.6) return '#ef5350';
  return '#c62828';
};

// ── Custom Treemap tile ─────────────────────────────────────────────────────
const SectorTile = ({ x, y, width, height, name, value, change_pct }) => {
  if (width < 30 || height < 20) return null;
  const color = sectorColor(change_pct ?? 0);
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={color} stroke="#0a0a0a" strokeWidth={2} rx={3} />
      <text x={x + width / 2} y={y + height / 2 - 6} textAnchor="middle" fill="#fff" fontSize={Math.min(12, width / 8)} fontWeight="bold">
        {name?.length > 12 ? name.slice(0, 11) + '…' : name}
      </text>
      <text x={x + width / 2} y={y + height / 2 + 10} textAnchor="middle" fill="rgba(255,255,255,0.8)" fontSize={Math.min(11, width / 9)}>
        {change_pct >= 0 ? '+' : ''}{change_pct?.toFixed(2)}%
      </text>
    </g>
  );
};

// ── Correlation Matrix SVG ──────────────────────────────────────────────────
function CorrelationMatrix({ data }) {
  if (!data?.tickers) return <p className="text-zinc-500 text-xs text-center mt-10">Loading…</p>;
  const { tickers, matrix } = data;
  const n = tickers.length;
  const cell = Math.min(36, Math.floor((Math.min(window.innerWidth, 900) - 120) / n));

  return (
    <div className="overflow-auto">
      <svg width={n * cell + 100} height={n * cell + 100}>
        {/* Column labels */}
        {tickers.map((t, j) => (
          <text key={j} x={100 + j * cell + cell / 2} y={92} textAnchor="end"
            transform={`rotate(-45,${100 + j * cell + cell / 2},92)`}
            fill="#9ca3af" fontSize={9}>{t}</text>
        ))}
        {/* Row labels */}
        {tickers.map((t, i) => (
          <text key={i} x={94} y={100 + i * cell + cell / 2 + 4} textAnchor="end"
            fill="#9ca3af" fontSize={9}>{t}</text>
        ))}
        {/* Cells */}
        {matrix.map((row, i) => row.map((val, j) => (
          <g key={`${i}-${j}`}>
            <rect x={100 + j * cell} y={100 + i * cell} width={cell - 1} height={cell - 1}
              fill={corrColor(val)} rx={2} opacity={i === j ? 1 : 0.85} />
            {cell > 26 && (
              <text x={100 + j * cell + cell / 2} y={100 + i * cell + cell / 2 + 4}
                textAnchor="middle" fill="rgba(255,255,255,0.9)" fontSize={8} fontWeight={i === j ? 'bold' : 'normal'}>
                {val.toFixed(2)}
              </text>
            )}
          </g>
        )))}
        {/* Diagonal highlight */}
        {tickers.map((_, i) => (
          <rect key={i} x={100 + i * cell} y={100 + i * cell} width={cell - 1} height={cell - 1}
            fill="none" stroke="#fff" strokeWidth={1.5} strokeOpacity={0.4} rx={2} />
        ))}
      </svg>
      {/* Legend */}
      <div className="flex items-center gap-2 mt-3 px-4">
        {[[-1,'#c62828'],[-0.5,'#ef5350'],[0,'#424242'],[0.5,'#66bb6a'],[1,'#00c853']].map(([v,c]) => (
          <div key={v} className="flex items-center gap-1">
            <div className="w-3 h-3 rounded" style={{background:c}} />
            <span className="text-[10px] text-zinc-500">{v}</span>
          </div>
        ))}
        <span className="text-[10px] text-zinc-600 ml-auto">3-month return correlation</span>
      </div>
    </div>
  );
}

// ── Options Flow Network (D3 force) ─────────────────────────────────────────
function OptionsNetwork({ symbol }) {
  const svgRef = useRef(null);
  const [netData, setNetData] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadNetwork = useCallback(async (sym) => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/viz/options-network/${sym}`);
      setNetData(res.data);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (symbol) loadNetwork(symbol);
  }, [symbol, loadNetwork]);

  useEffect(() => {
    if (!netData?.nodes?.length || !svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    const W = svgRef.current.clientWidth || 700;
    const H = 420;

    const nodes = netData.nodes.map(d => ({ ...d }));
    const edges = netData.edges
      .filter(e => e.weight > 0)
      .map(e => ({ ...e, source: e.source, target: e.target }));

    const maxOI = d3.max(nodes, d => d.oi) || 1;
    const rScale = d3.scaleSqrt().domain([0, maxOI]).range([4, 28]);

    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges).id(d => d.id).distance(60).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-120))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collision', d3.forceCollide().radius(d => rScale(d.oi) + 4));

    const g = svg.append('g');

    // Zoom
    svg.call(d3.zoom().scaleExtent([0.3, 3]).on('zoom', (e) => g.attr('transform', e.transform)));

    // Edges
    const link = g.append('g').selectAll('line').data(edges).enter().append('line')
      .attr('stroke', '#374151').attr('stroke-width', d => Math.max(1, Math.log(d.weight / 1000 + 1)));

    // Nodes
    const node = g.append('g').selectAll('g').data(nodes).enter().append('g')
      .call(d3.drag()
        .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

    node.append('circle')
      .attr('r', d => rScale(d.oi))
      .attr('fill', d => d.type === 'call' ? '#10b981' : d.type === 'put' ? '#ef4444' : '#f59e0b')
      .attr('fill-opacity', 0.85)
      .attr('stroke', d => d.type === 'call' ? '#34d399' : d.type === 'put' ? '#f87171' : '#fbbf24')
      .attr('stroke-width', 1.5);

    node.append('text')
      .attr('text-anchor', 'middle').attr('dy', '0.35em')
      .attr('fill', 'white').attr('font-size', d => d.type === 'atm' ? 10 : 8)
      .attr('font-weight', d => d.type === 'atm' ? 'bold' : 'normal')
      .text(d => d.type === 'atm' ? 'OTM' : d.label);

    node.append('title').text(d => `${d.label}\nOI: ${d.oi?.toLocaleString()}\nVol: ${d.volume?.toLocaleString()}`);

    sim.on('tick', () => {
      link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      node.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    return () => sim.stop();
  }, [netData]);

  const SYMBOLS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY'];

  return (
    <div>
      {/* Symbol picker */}
      <div className="flex gap-2 mb-3 px-4">
        {SYMBOLS.map(s => (
          <button key={s} onClick={() => loadNetwork(s)}
            className={`px-3 py-1 text-[11px] font-bold rounded transition-colors ${
              netData?.symbol === s ? 'bg-violet-600 text-white' : 'bg-white/5 text-zinc-400 hover:bg-white/10'
            }`}>{s}</button>
        ))}
        {loading && <span className="text-[10px] text-zinc-500 ml-2 self-center animate-pulse">Loading…</span>}
      </div>
      {netData?.error && <p className="text-[10px] text-amber-500 px-4 mb-2">Note: {netData.error}</p>}
      <svg ref={svgRef} width="100%" height={420} className="bg-[#0f0f0f] rounded-lg" />
      {/* Legend */}
      <div className="flex gap-4 mt-2 px-4 text-[10px] text-zinc-500">
        {[['#10b981','Call OI'],['#ef4444','Put OI'],['#f59e0b','OTM']].map(([c,l]) => (
          <div key={l} className="flex items-center gap-1">
            <div className="w-2.5 h-2.5 rounded-full" style={{background:c}} />
            <span>{l}</span>
          </div>
        ))}
        <span className="ml-auto">Drag nodes • Scroll to zoom</span>
      </div>
    </div>
  );
}

// ── Main Modal ──────────────────────────────────────────────────────────────
export default function VisualizeModal({ onClose, selectedStock }) {
  const [tab, setTab] = useState('heatmap');
  const [sectorData, setSectorData] = useState(null);
  const [corrData, setCorrData] = useState(null);
  const [corrLoading, setCorrLoading] = useState(false);

  useEffect(() => {
    axios.get(`${API}/sectors/trending`).then(r => {
      const sectors = r.data.sectors || [];
      setSectorData({
        name: 'Sectors', children: sectors.map(s => ({
          name: s.name, size: 1, change_pct: s.change_pct
        }))
      });
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (tab !== 'corr' || corrData) return;
    setCorrLoading(true);
    axios.get(`${API}/viz/correlation-matrix`).then(r => setCorrData(r.data)).catch(() => {}).finally(() => setCorrLoading(false));
  }, [tab, corrData]);

  const defaultSymbol = selectedStock?.ticker?.replace('.NS','') || 'NIFTY';

  return (
    <div className="fixed inset-0 z-[100] bg-black/90 flex flex-col" data-testid="visualize-modal">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 bg-[#0a0a0a]">
        <div className="flex items-center gap-3">
          <span className="text-sm font-black tracking-widest uppercase text-white">Visualize</span>
          <span className="text-[10px] text-zinc-500 font-mono">Heatmaps · Correlations · Options Flow</span>
        </div>
        <button onClick={onClose} className="p-1.5 rounded hover:bg-white/10 transition-colors" data-testid="close-visualize">
          <X size={18} className="text-zinc-400" />
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-white/10 bg-[#0a0a0a] shrink-0">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            data-testid={`viz-tab-${t.id}`}
            className={`flex items-center gap-2 px-5 py-2.5 text-[11px] font-bold tracking-widest uppercase transition-colors ${
              tab === t.id ? 'text-violet-400 border-b-2 border-violet-400' : 'text-zinc-500 hover:text-zinc-300'
            }`}>
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto bg-[#0d0d0d] p-4">

        {/* Sector Heatmap */}
        {tab === 'heatmap' && (
          <div>
            <p className="text-[10px] text-zinc-500 mb-3 uppercase tracking-widest">NSE Sector Performance — Today</p>
            {sectorData ? (
              <ResponsiveContainer width="100%" height={520}>
                <Treemap data={[sectorData]} dataKey="size" aspectRatio={4/3}
                  content={<SectorTile />}>
                  <Tooltip content={({ payload }) => payload?.[0] ? (
                    <div className="bg-[#1a1a1a] border border-white/10 rounded px-3 py-2 text-xs">
                      <p className="text-white font-bold">{payload[0].payload.name}</p>
                      <p className={payload[0].payload.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {payload[0].payload.change_pct >= 0 ? '+' : ''}{payload[0].payload.change_pct?.toFixed(2)}%
                      </p>
                    </div>
                  ) : null} />
                </Treemap>
              </ResponsiveContainer>
            ) : <p className="text-zinc-500 text-sm text-center mt-20 animate-pulse">Loading sector data…</p>}
          </div>
        )}

        {/* Correlation Matrix */}
        {tab === 'corr' && (
          <div>
            <p className="text-[10px] text-zinc-500 mb-3 uppercase tracking-widest">Return Correlation Matrix — NSE Large Caps (3M)</p>
            {corrLoading ? <p className="text-zinc-500 text-sm text-center mt-20 animate-pulse">Computing correlations…</p>
              : <CorrelationMatrix data={corrData} />}
          </div>
        )}

        {/* Options Flow Network */}
        {tab === 'network' && (
          <div>
            <p className="text-[10px] text-zinc-500 mb-3 uppercase tracking-widest">Options Flow Network — Live OI Distribution</p>
            <OptionsNetwork symbol={defaultSymbol} />
          </div>
        )}
      </div>
    </div>
  );
}
