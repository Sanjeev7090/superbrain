import { useEffect, useRef, useState, useCallback } from "react";
import { createChart } from "lightweight-charts";
import { fetchOrderBook } from "../../lib/hybridApi";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api/hybrid`;

const TF_LIST = ["5m", "15m", "1h", "1d", "1w"];

export default function QSCChart({ symbol, livePrice, onChangeSymbol, options, allAssets }) {
  const containerRef = useRef(null);
  const chartRef     = useRef(null);
  const candleRef    = useRef(null);
  const gannRefs     = useRef([]);
  const [tf, setTf]  = useState("1h");
  const [bars, setBars]       = useState([]);
  const [loading, setLoading] = useState(false);
  const [pivotMode, setPivotMode] = useState(false);
  const [pivot, setPivot]     = useState(null);
  const [showGann, setShowGann]   = useState(false);
  const [currency, setCurrency]   = useState("USD");

  // Detect currency for selected symbol
  useEffect(() => {
    const asset = allAssets?.find(a => a.symbol === symbol);
    setCurrency(asset?.currency || "USD");
  }, [symbol, allAssets]);

  // Clear chart immediately when symbol changes
  useEffect(() => {
    setBars([]);
    if (candleRef.current) {
      try { candleRef.current.setData([]); } catch {}
    }
    if (chartRef.current) {
      try { chartRef.current.timeScale().fitContent(); } catch {}
    }
  }, [symbol]);

  // ---- Fetch chart data ----
  const fetchBars = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/chart/${symbol}`, { params: { tf } });
      setBars(data.bars || []);
    } catch { setBars([]); }
    finally { setLoading(false); }
  }, [symbol, tf]);

  useEffect(() => { fetchBars(); }, [fetchBars]);

  // ---- Init lightweight-charts ----
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 340,
      layout: { background: { color: "#0A0A0A" }, textColor: "#555" },
      grid: { vertLines: { color: "rgba(255,255,255,0.025)" }, horzLines: { color: "rgba(255,255,255,0.025)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
      timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, rightOffset: 8, barSpacing: 5 },
      crosshair: { mode: 1 },
      localization: { locale: "en-US" },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    });
    chartRef.current = chart;

    const cs = chart.addCandlestickSeries({
      upColor: "#3366FF", downColor: "#FF3333",
      borderVisible: false, wickUpColor: "#3366FF", wickDownColor: "#FF3333",
    });
    candleRef.current = cs;
    chart.timeScale().fitContent();

    const onResize = () => {
      if (containerRef.current && chart)
        chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.remove(); };
  }, []);

  // ---- Update candle data ----
  useEffect(() => {
    if (!candleRef.current || !bars.length) return;
    const sorted = [...bars].sort((a, b) => a.time - b.time);
    // Deduplicate by time
    const seen = new Set();
    const unique = sorted.filter(b => { if (seen.has(b.time)) return false; seen.add(b.time); return true; });
    candleRef.current.setData(unique);
    chartRef.current?.timeScale().fitContent();
    clearGann();
  }, [bars]);

  // ---- Live price update on last candle ----
  useEffect(() => {
    if (!candleRef.current || !livePrice || !bars.length) return;
    const last = bars[bars.length - 1];
    if (!last) return;
    candleRef.current.update({
      time: last.time,
      open: last.open, high: Math.max(last.high, livePrice),
      low: Math.min(last.low, livePrice), close: livePrice,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livePrice]);

  // ---- Gann Fan drawing ----
  const clearGann = () => {
    gannRefs.current.forEach(s => { try { chartRef.current?.removeSeries(s); } catch {} });
    gannRefs.current = [];
  };

  const drawGann = useCallback((pivotBar) => {
    if (!chartRef.current || !pivotBar || !bars.length) return;
    clearGann();
    const sorted = [...bars].sort((a, b) => a.time - b.time);
    const idx = sorted.findIndex(b => b.time === pivotBar.time);
    if (idx === -1) return;
    const pivotPrice = pivotBar.close;
    const priceRange = Math.max(...sorted.map(b => b.high)) - Math.min(...sorted.map(b => b.low));
    const avgPricePerBar = priceRange / sorted.length;
    const angles = [
      { name: "1×1", ratio: 1.0,   color: "#3366FF", w: 2 },
      { name: "2×1", ratio: 2.0,   color: "#8B5CF6", w: 1 },
      { name: "1×2", ratio: 0.5,   color: "#FF3333", w: 1 },
      { name: "3×1", ratio: 3.0,   color: "#F59E0B", w: 1 },
      { name: "1×3", ratio: 0.333, color: "#10B981", w: 1 },
    ];
    const dir = pivotBar.type === "low" ? 1 : -1;
    angles.forEach(a => {
      try {
        const s = chartRef.current.addLineSeries({
          color: a.color, lineWidth: a.w, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, title: a.name,
        });
        const pts = [{ time: sorted[idx].time, value: pivotPrice }];
        for (let i = 1; idx + i < sorted.length; i++) {
          pts.push({ time: sorted[idx + i].time, value: pivotPrice + i * avgPricePerBar * a.ratio * dir });
        }
        if (pts.length >= 2) s.setData(pts);
        gannRefs.current.push(s);
      } catch {}
    });
  }, [bars]);

  // Click handler for pivot selection
  useEffect(() => {
    if (!chartRef.current || !pivotMode) return;
    const handler = (p) => {
      if (!candleRef.current) return;
      const bar = candleRef.current.coordinateToPrice(p.point.y);
      const time = chartRef.current.timeScale().coordinateToTime(p.point.x);
      if (!bar || !time) return;
      const pivotBar = bars.reduce((acc, b) => (!acc || Math.abs(b.time - time) < Math.abs(acc.time - time)) ? b : acc, null);
      if (pivotBar) {
        const withType = { ...pivotBar, type: bar > pivotBar.close ? "high" : "low" };
        setPivot(withType);
        if (showGann) drawGann(withType);
        setPivotMode(false);
      }
    };
    chartRef.current.subscribeClick(handler);
    return () => { try { chartRef.current?.unsubscribeClick(handler); } catch {} };
  }, [pivotMode, bars, showGann, drawGann]);

  useEffect(() => {
    if (showGann && pivot) drawGann(pivot);
    else clearGann();
  }, [showGann, pivot, drawGann]);

  const last = bars[bars.length - 1];
  const change = last && bars[0] ? ((last.close - bars[0].close) / bars[0].close * 100) : 0;
  const up = change >= 0;
  const currSymbol = currency === "INR" ? "₹" : "$";

  return (
    <div className="qsc-card flex flex-col" data-testid="qsc-chart">
      {/* Header */}
      <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-3">
          {/* Symbol selector */}
          <select
            value={symbol} onChange={e => onChangeSymbol(e.target.value)}
            className="bg-transparent border border-white/15 text-white font-mono text-xs px-2 py-1.5 focus:outline-none focus:border-white/40"
            data-testid="qsc-chart-symbol-select"
          >
            {(options || []).map(o => (
              <option key={o} value={o} className="bg-[#0A0A0A]">{o}</option>
            ))}
            {allAssets?.filter(a => !options?.includes(a.symbol)).map(a => (
              <option key={a.symbol} value={a.symbol} className="bg-[#0A0A0A]">{a.symbol}</option>
            ))}
          </select>

          {/* Price */}
          <div>
            <span className="font-mono text-2xl tracking-tight text-white" data-testid="qsc-live-price">
              {currSymbol}{(livePrice ?? last?.close ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </span>
            <span className="ml-2 text-[11px] font-mono" style={{ color: up ? "#3366FF" : "#FF3333" }}>
              {up ? "▲" : "▼"} {Math.abs(change).toFixed(2)}%
            </span>
          </div>
        </div>

        {/* Right controls */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {/* TF buttons */}
          {TF_LIST.map(t => (
            <button key={t} onClick={() => setTf(t)}
              className={`btn-flat text-[9px] px-2 py-1 ${tf === t ? "bg-white text-black" : ""}`}
              data-testid={`qsc-tf-${t}`}
            >{t.toUpperCase()}</button>
          ))}
          <div className="w-px h-4 bg-white/10 mx-1" />
          {/* Gann Fan */}
          <button onClick={() => setShowGann(g => !g)}
            className={`btn-flat text-[9px] px-2 py-1 ${showGann ? "bg-[#3366FF] text-black border-[#3366FF]" : ""}`}
            title="Gann Fan" data-testid="qsc-gann-btn"
          >GANN</button>
          {showGann && (
            <button onClick={() => setPivotMode(p => !p)}
              className={`btn-flat text-[9px] px-2 py-1 ${pivotMode ? "bg-white text-black" : ""}`}
              data-testid="qsc-pivot-btn"
            >{pivotMode ? "CLICK PIVOT" : "SET PIVOT"}</button>
          )}
          <button onClick={fetchBars} className="btn-flat text-[9px] px-2 py-1" data-testid="qsc-refresh-btn">↺</button>
        </div>
      </div>

      {/* Chart area */}
      <div className="relative" style={{ height: 340 }}>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-[#0A0A0A]/80 z-10">
            <span className="text-[10px] font-mono uppercase tracking-widest text-neutral-500 animate-pulse">Loading...</span>
          </div>
        )}
        <div ref={containerRef} style={{ width: "100%", height: 340 }} />
      </div>

      {/* Gann status bar */}
      {showGann && (
        <div className="px-4 py-1.5 border-t border-white/5 flex items-center gap-3 flex-wrap">
          <span className="text-[9px] font-mono text-neutral-500 uppercase tracking-widest">Gann Fan</span>
          {[{ n: "1×1", c: "#3366FF" }, { n: "2×1", c: "#8B5CF6" }, { n: "1×2", c: "#FF3333" },
            { n: "3×1", c: "#F59E0B" }, { n: "1×3", c: "#10B981" }].map(a => (
            <span key={a.n} className="text-[9px] font-mono" style={{ color: a.c }}>{a.n}</span>
          ))}
          {pivot && (
            <span className="text-[9px] font-mono text-neutral-500 ml-2">
              Pivot @ {currSymbol}{pivot.close?.toLocaleString()}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
