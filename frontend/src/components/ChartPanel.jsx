import React, { useEffect, useRef, useState, useCallback } from 'react';
import axios from 'axios';
import { createChart } from 'lightweight-charts';
import { ChartLine, TrendUp, TrendDown, PencilLine, Trash, Lightning } from '@phosphor-icons/react';
import GrowwTradeModal from './GrowwTradeModal';
import StrategyOverlay from './StrategyOverlay';
import TimeframeLevels from './TimeframeLevels';
import { useTheme } from '../context/ThemeContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const VP_WIDTH  = 100;   // 84px bars + 6px gap + 10px heatmap
const VP_BARS_W = 84;    // actual bar area width
const HEAT_X    = 90;    // heatmap column start
const HEAT_W    = 10;    // heatmap column width

const fmtVol = n => {
  if (!n && n !== 0) return '0';
  const v = Math.abs(n);
  if (v > 1e7) return `${(n/1e7).toFixed(1)}Cr`;
  if (v > 1e5) return `${(n/1e5).toFixed(1)}L`;
  if (v > 1e3) return `${(n/1e3).toFixed(1)}K`;
  return Number(n).toFixed(0);
};

// ── SMC Auto Mark: compute FVG / Liquidity / Order Blocks ─────────
function computeSMCData(bars) {
  const n = bars.length;
  if (n < 15) return { fvgs: [], swings: [], obs: [] };

  const fvgs = [];
  const obs  = [];

  for (let i = 2; i < n; i++) {
    // Bullish FVG: low[i] > high[i-2]
    if (bars[i].low > bars[i - 2].high) {
      const endIdx = Math.min(i + 20, n - 1);
      let mitigated = false;
      for (let j = i + 1; j <= endIdx; j++) {
        if (bars[j].low < bars[i].low && bars[j].high > bars[i - 2].high) { mitigated = true; break; }
      }
      fvgs.push({
        type: 'bull', top: bars[i].low, bottom: bars[i - 2].high, mitigated,
        startTime: bars[i - 1].timestamp / 1000,
        endTime: bars[endIdx].timestamp / 1000,
      });
      if (bars[i].close > bars[i].open)
        obs.push({ type: 'bull', high: bars[i - 1].high, low: bars[i - 1].low,
          startTime: bars[i - 1].timestamp / 1000, endTime: bars[i].timestamp / 1000 });
    }
    // Bearish FVG: high[i] < low[i-2]
    if (bars[i].high < bars[i - 2].low) {
      const endIdx = Math.min(i + 20, n - 1);
      let mitigated = false;
      for (let j = i + 1; j <= endIdx; j++) {
        if (bars[j].high > bars[i].high && bars[j].low < bars[i - 2].low) { mitigated = true; break; }
      }
      fvgs.push({
        type: 'bear', top: bars[i - 2].low, bottom: bars[i].high, mitigated,
        startTime: bars[i - 1].timestamp / 1000,
        endTime: bars[endIdx].timestamp / 1000,
      });
      if (bars[i].close < bars[i].open)
        obs.push({ type: 'bear', high: bars[i - 1].high, low: bars[i - 1].low,
          startTime: bars[i - 1].timestamp / 1000, endTime: bars[i].timestamp / 1000 });
    }
  }

  // Swing high / low — pivot 5,5
  const swings = [];
  for (let i = 5; i < n - 5; i++) {
    let isH = true, isL = true;
    for (let j = i - 5; j <= i + 5; j++) {
      if (j === i) continue;
      if (bars[j].high >= bars[i].high) isH = false;
      if (bars[j].low  <= bars[i].low)  isL = false;
    }
    const eIdx = Math.min(i + 50, n - 1);
    if (isH) swings.push({ type: 'high', price: bars[i].high, startTime: bars[i].timestamp / 1000, endTime: bars[eIdx].timestamp / 1000 });
    if (isL) swings.push({ type: 'low',  price: bars[i].low,  startTime: bars[i].timestamp / 1000, endTime: bars[eIdx].timestamp / 1000 });
  }

  return {
    fvgs:   fvgs.filter(f => !f.mitigated).slice(-40),
    swings: swings.slice(-40),
    obs:    obs.slice(-20),
  };
}

const ChartPanel = ({
  stockData, loading, selectedStock, onPivotSelect, pivotPoint, gannFan,
  semiLogScale, setSemiLogScale, timeframe, onTimeframeChange, isCrypto,
  dataSource, onDataSourceChange, activeStrategy, strategyData
}) => {
  const chartContainerRef = useRef();
  const chartRef = useRef(null);
  const candlestickSeriesRef = useRef(null);
  const gannLineSeriesRef = useRef([]);
  // Volume Profile refs
  const vpCanvasRef = useRef(null);
  const vpDataRef = useRef(null);
  const vpAnimRef = useRef(null);
  const vpPriceLinesRef = useRef([]);
  const vpHoverYRef = useRef(null);
  // SMC Auto Mark refs
  const smcCanvasRef = useRef(null);
  const smcDataRef   = useRef(null);
  const smcAnimRef   = useRef(null);
  const [smcActive, setSmcActive] = useState(true);
  const [selectMode, setSelectMode] = useState(null);
  const [showGannLines, setShowGannLines] = useState(true);
  const [lineExtension, setLineExtension] = useState(50);
  const [isMovingMode, setIsMovingMode] = useState(false);
  const [tfOpen, setTfOpen] = useState(false);
  const [showTrade, setShowTrade] = useState(false);
  const [vpActive, setVpActive] = useState(false);
  const [vpTooltip, setVpTooltip] = useState(null);
  const { theme } = useTheme();

  const timeframes = [
    { multiplier: 1, timespan: 'minute', label: '1MIN' },
    { multiplier: 5, timespan: 'minute', label: '5M' },
    { multiplier: 10, timespan: 'minute', label: '10M' },
    { multiplier: 15, timespan: 'minute', label: '15M' },
    { multiplier: 30, timespan: 'minute', label: '30M' },
    { multiplier: 1, timespan: 'hour', label: '1H' },
    { multiplier: 4, timespan: 'hour', label: '4H' },
    { multiplier: 1, timespan: 'day', label: '1D' },
    { multiplier: 1, timespan: 'week', label: '1W' },
    { multiplier: 1, timespan: 'day', label: '1M', days: 30 },
    { multiplier: 1, timespan: 'day', label: '6M', days: 180 },
    { multiplier: 1, timespan: 'week', label: '1Y', days: 365 },
  ];

  const clearGannLines = () => {
    if (chartRef.current && gannLineSeriesRef.current.length > 0) {
      gannLineSeriesRef.current.forEach(series => {
        try { chartRef.current.removeSeries(series); } catch (e) {}
      });
      gannLineSeriesRef.current = [];
    }
  };

  // ── Volume Profile helpers ───────────────────────────────────────
  const clearVPLines = useCallback(() => {
    vpPriceLinesRef.current.forEach(pl => {
      try { candlestickSeriesRef.current?.removePriceLine(pl); } catch (e) {}
    });
    vpPriceLinesRef.current = [];
  }, []);

  const drawVPCanvas = useCallback(() => {
    const canvas = vpCanvasRef.current;
    const series = candlestickSeriesRef.current;
    const d = vpDataRef.current;
    if (!canvas || !series || !d?.vp_bins?.length) return;
    const container = chartContainerRef.current;
    if (!container) return;
    const dpr = window.devicePixelRatio || 1;
    const H = container.clientHeight;
    if (canvas.style.height !== `${H}px`) {
      canvas.width = VP_WIDTH * dpr;
      canvas.height = H * dpr;
      canvas.style.width = `${VP_WIDTH}px`;
      canvas.style.height = `${H}px`;
    }
    const ctx = canvas.getContext('2d');
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, VP_WIDTH, H);

    const bins    = d.vp_bins;
    const maxVol  = Math.max(...bins.map(b => b.total_vol)) || 1;
    const rowH    = Math.max(3, (H / bins.length) * 0.72);
    const pocPrice = bins.find(b => b.is_poc)?.price_mid ?? bins[Math.floor(bins.length / 2)].price_mid;
    const maxDist  = Math.max(...bins.map(b => Math.abs(b.price_mid - pocPrice))) || 1;

    // ── 1. Buy / Sell bars ─────────────────────────────────────────
    bins.forEach(bin => {
      const y = series.priceToCoordinate(bin.price_mid);
      if (y == null || y < -rowH || y > H + rowH) return;
      const buyW  = (bin.buy_vol  / maxVol) * VP_BARS_W;
      const sellW = (bin.sell_vol / maxVol) * VP_BARS_W;
      const half  = rowH / 2;

      if (bin.in_value_area) {
        ctx.fillStyle = 'rgba(255,255,255,0.04)';
        ctx.fillRect(0, y - rowH, VP_BARS_W + 4, rowH * 2);
      }
      if (buyW > 0) {
        ctx.fillStyle = bin.in_value_area ? 'rgba(0,230,118,0.82)' : 'rgba(0,230,118,0.38)';
        ctx.fillRect(1, y - half, buyW, half);
      }
      if (sellW > 0) {
        ctx.fillStyle = bin.in_value_area ? 'rgba(255,59,48,0.82)' : 'rgba(255,59,48,0.38)';
        ctx.fillRect(1, y, sellW, half);
      }
      if (bin.is_poc) {
        ctx.strokeStyle = 'rgba(255,107,0,0.95)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 2]);
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(VP_BARS_W + 4, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#FF6B00';
        ctx.font = 'bold 7px monospace';
        ctx.fillText('◆', VP_BARS_W - 10, y - 2);
      }
    });

    // ── 2. VAH / VAL edge labels ───────────────────────────────────
    const markEdgeLabel = (price, label, color) => {
      if (!price) return;
      const y = series.priceToCoordinate(price);
      if (y == null || y < 4 || y > H - 4) return;
      ctx.fillStyle = color;
      ctx.font = 'bold 7px monospace';
      ctx.fillText(label, 2, y - 2);
    };
    markEdgeLabel(d.vah_price, 'VAH', '#A855F7');
    markEdgeLabel(d.val_price, 'VAL', '#06B6D4');

    // ── 3. Heatmap column — per-level Buy/Sell split ──────────────
    // Thin vertical separator
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 0.5;
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(HEAT_X - 2, 0); ctx.lineTo(HEAT_X - 2, H); ctx.stroke();

    bins.forEach(bin => {
      const y = series.priceToCoordinate(bin.price_mid);
      if (y == null || y < -rowH || y > H + rowH) return;

      const totalVol    = (bin.buy_vol || 0) + (bin.sell_vol || 0) || 1;
      const buyRatio    = bin.buy_vol  / totalVol;   // 0-1
      const sellRatio   = bin.sell_vol / totalVol;   // 0-1
      // Brightness: louder levels = more opaque
      const intensity   = 0.30 + (bin.total_vol / maxVol) * 0.70;
      // POC zone gets full brightness
      const alpha       = bin.is_poc ? 1.0 : intensity;

      const half = rowH;   // each cell spans rowH above + rowH below y

      // TOP half → Buy (green), width proportional to buy ratio
      const buyPixW  = Math.max(1, HEAT_W * buyRatio);
      const sellPixW = Math.max(1, HEAT_W * sellRatio);

      // Background fill (dark base)
      ctx.fillStyle = 'rgba(10,10,20,0.55)';
      ctx.fillRect(HEAT_X, y - half, HEAT_W, half * 2);

      // Buy bar — top half of cell
      ctx.fillStyle = `rgba(0,230,118,${alpha})`;
      ctx.fillRect(HEAT_X, y - half, buyPixW, half);

      // Sell bar — bottom half of cell
      ctx.fillStyle = `rgba(255,59,48,${alpha})`;
      ctx.fillRect(HEAT_X, y, sellPixW, half);

      // Thin mid-line separator between buy/sell
      ctx.fillStyle = 'rgba(0,0,0,0.4)';
      ctx.fillRect(HEAT_X, y - 0.5, HEAT_W, 1);

      // Dominant side indicator: bright edge glow
      const dominant = buyRatio > sellRatio ? 'buy' : sellRatio > buyRatio ? 'sell' : null;
      if (dominant && bin.total_vol / maxVol > 0.3) {
        ctx.fillStyle = dominant === 'buy'
          ? `rgba(0,230,118,${alpha * 0.6})`
          : `rgba(255,59,48,${alpha * 0.6})`;
        // Right-edge glow strip (1px)
        ctx.fillRect(HEAT_X + HEAT_W - 1, y - half, 1, half * 2);
      }

      // POC band — white-hot line + label
      if (bin.is_poc) {
        ctx.fillStyle = 'rgba(255,230,150,0.95)';
        ctx.fillRect(HEAT_X, y - 1, HEAT_W, 2);
        ctx.fillStyle = '#FF6B00';
        ctx.font = 'bold 6px monospace';
        ctx.fillText('H', HEAT_X + 2, y - 3);
      }
    });

    // ── 4. Hover highlight ────────────────────────────────────────
    const hoverY = vpHoverYRef.current;
    if (hoverY !== null) {
      let hBin = null, hMin = Infinity;
      bins.forEach(bin => {
        const y = series.priceToCoordinate(bin.price_mid);
        if (y == null) return;
        const dist = Math.abs(y - hoverY);
        if (dist < hMin) { hMin = dist; hBin = bin; }
      });
      if (hBin && hMin < rowH * 1.5) {
        const hy = series.priceToCoordinate(hBin.price_mid);
        if (hy != null) {
          ctx.fillStyle = 'rgba(255,255,255,0.10)';
          ctx.fillRect(0, hy - rowH, VP_WIDTH, rowH * 2);
          ctx.strokeStyle = 'rgba(255,255,255,0.35)';
          ctx.lineWidth = 0.5;
          ctx.setLineDash([]);
          ctx.beginPath(); ctx.moveTo(0, hy); ctx.lineTo(VP_WIDTH, hy); ctx.stroke();
        }
      }
    }

    ctx.restore();
  }, []);

  // ── SMC Canvas Draw ────────────────────────────────────────────
  const drawSMCCanvas = useCallback(() => {
    const canvas  = smcCanvasRef.current;
    const chart   = chartRef.current;
    const series  = candlestickSeriesRef.current;
    const smc     = smcDataRef.current;
    if (!canvas || !chart || !series || !smc) return;
    const container = chartContainerRef.current;
    if (!container) return;

    const W = container.clientWidth;
    const H = container.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width  = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width  = `${W}px`;
      canvas.style.height = `${H}px`;
    }

    const ctx = canvas.getContext('2d');
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const ts  = chart.timeScale();
    const toX = t  => { try { return ts.timeToCoordinate(t); } catch { return null; } };
    const toY = p  => series.priceToCoordinate(p);

    // ── 1. Liquidity lines (Swing High / Low) ─────────────────
    smc.swings.forEach(sw => {
      const x1 = toX(sw.startTime);
      const x2 = toX(sw.endTime);
      const y  = toY(sw.price);
      if (x1 == null || x2 == null || y == null) return;
      ctx.save();
      ctx.strokeStyle = 'rgba(255,185,0,0.72)';
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(255,185,0,0.85)';
      ctx.font = 'bold 7px monospace';
      const lbl = sw.type === 'high' ? 'LH' : 'LL';
      ctx.fillText(lbl, Math.max(8, x1 - 14), y - 2);
      ctx.restore();
    });

    // ── 2. FVG Boxes ──────────────────────────────────────────
    smc.fvgs.forEach(fvg => {
      const x1   = toX(fvg.startTime);
      const x2   = toX(fvg.endTime);
      const yTop = toY(fvg.top);
      const yBot = toY(fvg.bottom);
      if (x1 == null || x2 == null || yTop == null || yBot == null) return;
      const left = Math.min(x1, x2);
      const top  = Math.min(yTop, yBot);
      const w    = Math.abs(x2 - x1);
      const h    = Math.max(2, Math.abs(yBot - yTop));
      ctx.save();
      if (fvg.type === 'bull') {
        ctx.fillStyle   = 'rgba(0,230,118,0.11)';
        ctx.strokeStyle = 'rgba(0,230,118,0.85)';
      } else {
        ctx.fillStyle   = 'rgba(255,59,48,0.11)';
        ctx.strokeStyle = 'rgba(255,59,48,0.85)';
      }
      ctx.lineWidth = 1;
      ctx.fillRect(left, top, w, h);
      ctx.strokeRect(left, top, w, h);
      ctx.fillStyle = fvg.type === 'bull' ? 'rgba(0,230,118,0.92)' : 'rgba(255,59,48,0.92)';
      ctx.font = 'bold 8px monospace';
      ctx.fillText(fvg.type === 'bull' ? 'FVG+' : 'FVG-', left + 3, top + 9);
      ctx.restore();
    });

    // ── 3. Order Blocks ────────────────────────────────────────
    smc.obs.forEach(ob => {
      const x1 = toX(ob.startTime);
      const x2 = toX(ob.endTime);
      const yH = toY(ob.high);
      const yL = toY(ob.low);
      if (x1 == null || x2 == null || yH == null || yL == null) return;
      const left  = Math.min(x1, x2);
      const top   = Math.min(yH, yL);
      const w     = Math.max(6, Math.abs(x2 - x1));
      const h     = Math.max(2, Math.abs(yL - yH));
      ctx.save();
      if (ob.type === 'bull') {
        ctx.fillStyle   = 'rgba(59,130,246,0.18)';
        ctx.strokeStyle = 'rgba(59,130,246,0.85)';
      } else {
        ctx.fillStyle   = 'rgba(255,100,0,0.18)';
        ctx.strokeStyle = 'rgba(255,100,0,0.85)';
      }
      ctx.lineWidth = 1.5;
      ctx.fillRect(left, top, w, h);
      ctx.strokeRect(left, top, w, h);
      ctx.fillStyle = ob.type === 'bull' ? 'rgba(59,130,246,0.9)' : 'rgba(255,100,0,0.9)';
      ctx.font = 'bold 7px monospace';
      ctx.fillText('OB', left + 2, top + 8);
      ctx.restore();
    });

    ctx.restore();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchVolumeProfile = useCallback(async (bars, ticker) => {
    if (!bars || bars.length < 30) return;
    try {
      const resp = await axios.post(`${API}/orderflow/analyze`, {
        ticker,
        bars,
        n_vp_bins: 30,
        n_fp_levels: 8,
        vp_lookback: Math.min(60, bars.length),
      });
      vpDataRef.current = resp.data;
      clearVPLines();
      const d = resp.data;
      if (candlestickSeriesRef.current) {
        [
          [d.poc_price, 'POC', '#FF6B00', 1],
          [d.vah_price, 'VAH', '#A855F7', 2],
          [d.val_price, 'VAL', '#06B6D4', 2],
        ].forEach(([price, title, color, lineStyle]) => {
          if (!price) return;
          try {
            const pl = candlestickSeriesRef.current.createPriceLine({
              price, color, lineWidth: 1, lineStyle, axisLabelVisible: true, title,
            });
            vpPriceLinesRef.current.push(pl);
          } catch (e) {}
        });
      }
      setVpActive(true);
    } catch (e) {
      console.warn('VP fetch:', e.message);
    }
  }, [clearVPLines]);

  // VP interaction handlers
  const handleVPMouseMove = useCallback((e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    vpHoverYRef.current = e.clientY - rect.top;
  }, []);

  const handleVPMouseLeave = useCallback(() => {
    vpHoverYRef.current = null;
  }, []);

  const handleVPClick = useCallback((e) => {
    e.stopPropagation();
    const series = candlestickSeriesRef.current;
    const d = vpDataRef.current;
    if (!series || !d?.vp_bins?.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clickY = e.clientY - rect.top;
    const maxVol = Math.max(...d.vp_bins.map(b => b.total_vol)) || 1;
    const H = chartContainerRef.current?.clientHeight || 300;
    const rowH = Math.max(3, (H / d.vp_bins.length) * 0.72);
    let closestBin = null, minDist = Infinity;
    d.vp_bins.forEach(bin => {
      const y = series.priceToCoordinate(bin.price_mid);
      if (y == null) return;
      const dist = Math.abs(y - clickY);
      if (dist < minDist) { minDist = dist; closestBin = bin; }
    });
    if (closestBin && minDist < rowH * 1.5) {
      if (vpTooltip?.bin?.price_mid === closestBin.price_mid) {
        setVpTooltip(null);
        return;
      }
      setVpTooltip({
        y: Math.max(8, Math.min(clickY, H - 200)),
        bin: closestBin,
        maxVol,
        poc: d.poc_price,
        vah: d.vah_price,
        val: d.val_price,
      });
    } else {
      setVpTooltip(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vpTooltip]);

  const drawGannLines = (pivot, extension) => {
    if (!chartRef.current || !pivot || !stockData || !showGannLines) return;
    clearGannLines();
    const bars = stockData.bars;
    const pivotIndex = bars.findIndex(b => Math.abs(b.timestamp - pivot.timestamp) < 86400000);
    if (pivotIndex === -1) return;
    const pivotPrice = pivot.price;
    const isBullish = pivot.type === 'low';
    const priceRange = Math.max(...bars.map(b => b.high)) - Math.min(...bars.map(b => b.low));
    const avgPricePerBar = priceRange / bars.length;
    const angles = [
      { name: '1x1', ratio: 1.0, color: '#3B82F6', width: 3 },
      { name: '2x1', ratio: 2.0, color: '#A855F7', width: 2 },
      { name: '1x2', ratio: 0.5, color: '#FF0055', width: 2 },
      { name: '3x1', ratio: 3.0, color: '#F5A623', width: 1 },
      { name: '1x3', ratio: 0.333, color: '#00E676', width: 1 },
    ];
    const direction = isBullish ? 1 : -1;
    const barsToProject = Math.min(extension, bars.length - pivotIndex);

    angles.forEach(angle => {
      try {
        const lineSeries = chartRef.current.addLineSeries({
          color: angle.color, lineWidth: angle.width, lineStyle: 0,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: angle.name,
        });
        const lineData = [{ time: bars[pivotIndex].timestamp / 1000, value: pivotPrice }];
        for (let i = 1; i <= barsToProject; i++) {
          const barIndex = pivotIndex + i;
          if (barIndex >= bars.length) break;
          lineData.push({
            time: bars[barIndex].timestamp / 1000,
            value: pivotPrice + (i * avgPricePerBar * angle.ratio * direction)
          });
        }
        if (lineData.length >= 2) {
          lineSeries.setData(lineData);
          gannLineSeriesRef.current.push(lineSeries);
        }
      } catch (e) {}
    });
  };

  useEffect(() => {
    // Use refs so cleanup always has access to the latest instances
    let retryTimer;
    let chartInst = null;
    let handleResize = null;
    let roInst = null;

    const initChart = () => {
      if (!chartContainerRef.current) return;
      const h = chartContainerRef.current.clientHeight;
      if (h < 10) {
        retryTimer = setTimeout(initChart, 40);   // retry until layout settles
        return;
      }
      const isDark = document.documentElement.classList.contains('dark');
      const chart = createChart(chartContainerRef.current, {
        width: chartContainerRef.current.clientWidth,
        height: h,
        layout: {
          background: { color: isDark ? '#0A0A0A' : '#FFFFFF' },
          textColor: isDark ? '#52525B' : '#64748B',
        },
        grid: {
          vertLines: { color: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.04)' },
          horzLines: { color: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.04)' },
        },
        rightPriceScale: { borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.1)', mode: semiLogScale ? 2 : 0 },
        timeScale: { borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.1)', timeVisible: true, rightOffset: 10, barSpacing: 6, minBarSpacing: 0.5 },
        crosshair: { mode: 1 },
        localization: { locale: 'en-US' },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      });
      chartInst = chart;
      chartRef.current = chart;

      const cs = chart.addCandlestickSeries({
        upColor: '#00E676', downColor: '#FF3B30', borderVisible: false,
        wickUpColor: '#00E676', wickDownColor: '#FF3B30',
      });
      candlestickSeriesRef.current = cs;
      chart.timeScale().fitContent();

      handleResize = () => {
        if (chartContainerRef.current && chart) {
          chart.applyOptions({
            width: chartContainerRef.current.clientWidth,
            height: chartContainerRef.current.clientHeight,
          });
        }
      };
      window.addEventListener('resize', handleResize);

      // ResizeObserver — fires when container height changes (OrderFlow expand/collapse)
      roInst = new ResizeObserver(handleResize);
      roInst.observe(chartContainerRef.current);
    };

    initChart();

    // useEffect cleanup — always runs, even if chart was never created
    return () => {
      clearTimeout(retryTimer);
      if (handleResize) window.removeEventListener('resize', handleResize);
      if (roInst) roInst.disconnect();
      clearGannLines();
      if (chartInst) chartInst.remove();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (chartRef.current) chartRef.current.applyOptions({ rightPriceScale: { mode: semiLogScale ? 2 : 0 } });
  }, [semiLogScale]);

  // Update chart colors when theme changes
  useEffect(() => {
    if (!chartRef.current) return;
    const isDark = theme === 'dark';
    chartRef.current.applyOptions({
      layout: {
        background: { color: isDark ? '#0A0A0A' : '#FFFFFF' },
        textColor: isDark ? '#52525B' : '#64748B',
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.04)' },
        horzLines: { color: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.04)' },
      },
      rightPriceScale: { borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.1)' },
      timeScale: { borderColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.1)' },
    });
  }, [theme]);

  useEffect(() => {
    if (!stockData || !candlestickSeriesRef.current) return;
    const chartData = stockData.bars.map(bar => ({ time: bar.timestamp / 1000, open: bar.open, high: bar.high, low: bar.low, close: bar.close }));
    candlestickSeriesRef.current.setData(chartData);
    chartRef.current.timeScale().fitContent();
  }, [stockData]);

  // ── Volume Profile: fetch when stock/data changes ──────────────
  useEffect(() => {
    clearVPLines();
    vpDataRef.current = null;
    setVpActive(false);
    setVpTooltip(null);
    if (!stockData?.bars?.length) return;
    const ticker = selectedStock?.ticker || selectedStock?.symbol || 'STOCK';
    // Small delay so chart settles first
    const t = setTimeout(() => fetchVolumeProfile(stockData.bars, ticker), 400);
    return () => clearTimeout(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stockData, selectedStock]);

  // ── Volume Profile: animation loop ────────────────────────────
  useEffect(() => {
    if (!vpActive) {
      if (vpAnimRef.current) cancelAnimationFrame(vpAnimRef.current);
      return;
    }
    const loop = () => {
      drawVPCanvas();
      vpAnimRef.current = requestAnimationFrame(loop);
    };
    vpAnimRef.current = requestAnimationFrame(loop);
    return () => { if (vpAnimRef.current) cancelAnimationFrame(vpAnimRef.current); };
  }, [vpActive, drawVPCanvas]);

  // ── SMC: compute when bars change ─────────────────────────────
  useEffect(() => {
    smcDataRef.current = null;
    if (!stockData?.bars?.length) return;
    smcDataRef.current = computeSMCData(stockData.bars);
  }, [stockData]);

  // ── SMC: animation loop ────────────────────────────────────────
  useEffect(() => {
    if (!smcActive) {
      if (smcAnimRef.current) cancelAnimationFrame(smcAnimRef.current);
      const c = smcCanvasRef.current;
      if (c) { const ctx = c.getContext('2d'); ctx.clearRect(0, 0, c.width, c.height); }
      return;
    }
    const loop = () => { drawSMCCanvas(); smcAnimRef.current = requestAnimationFrame(loop); };
    smcAnimRef.current = requestAnimationFrame(loop);
    return () => { if (smcAnimRef.current) cancelAnimationFrame(smcAnimRef.current); };
  }, [smcActive, drawSMCCanvas]);

  useEffect(() => {
    if (showGannLines && pivotPoint && stockData) {
      setTimeout(() => drawGannLines(pivotPoint, lineExtension), 50);
    } else { clearGannLines(); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pivotPoint, showGannLines, stockData, lineExtension]);

  const handleChartClick = (param) => {
    if (!stockData || !param.time) return;
    const clickedTime = param.time * 1000;
    const bar = stockData.bars.find(b => Math.abs(b.timestamp - clickedTime) < 86400000);
    if (!bar) return;
    if (isMovingMode && pivotPoint) {
      const price = pivotPoint.type === 'high' ? bar.high : bar.low;
      onPivotSelect({ price, timestamp: bar.timestamp, type: pivotPoint.type });
      return;
    }
    if (selectMode) {
      const price = selectMode === 'high' ? bar.high : bar.low;
      onPivotSelect({ price, timestamp: bar.timestamp, type: selectMode });
      setSelectMode(null);
      setIsMovingMode(true);
    }
  };

  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.subscribeClick(handleChartClick);
    return () => { if (chartRef.current) { try { chartRef.current.unsubscribeClick(handleChartClick); } catch (e) {} } };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectMode, stockData, isMovingMode, pivotPoint]);

  const handleDeleteGann = () => { onPivotSelect(null); clearGannLines(); setIsMovingMode(false); };

  return (
    <div className="flex flex-col h-full" data-testid="chart-panel">
      {/* Chart Toolbar — scrollable row on mobile */}
      <div className="flex items-center justify-between px-2 py-1 border-b border-slate-200 dark:border-white/10 bg-white dark:bg-[#0A0A0A] shrink-0 gap-1 overflow-x-auto scrollbar-none transition-colors duration-200">
        <div className="flex items-center gap-1 flex-nowrap shrink-0">
          {/* Compact TF trigger — mobile only */}
          <button
            onClick={() => setTfOpen(!tfOpen)}
            className="md:hidden px-2 py-1 text-[10px] font-mono font-bold uppercase tracking-wider bg-slate-900 dark:bg-white text-white dark:text-black flex items-center gap-1 shrink-0"
            data-testid="tf-trigger"
          >
            {timeframe.label}
            <span className="text-[8px]">{tfOpen ? '▴' : '▾'}</span>
          </button>
          {/* Timeframes — desktop always visible, mobile only when tfOpen */}
          <div className={`${tfOpen ? 'flex' : 'hidden'} md:flex items-center gap-1 flex-nowrap shrink-0`}>
          {timeframes.map((tf) => (
            <button
              key={tf.label}
              onClick={() => { onTimeframeChange(tf); setTfOpen(false); }}
              className={`px-2 py-1 text-[10px] font-mono font-bold uppercase tracking-wider transition-all whitespace-nowrap min-w-[28px] ${
                timeframe.label === tf.label
                  ? 'bg-slate-900 dark:bg-white text-white dark:text-black'
                  : 'text-slate-400 dark:text-zinc-500 hover:text-slate-800 dark:hover:text-white active:text-slate-900 dark:active:text-white'
              }`}
              data-testid={`tf-${tf.label}`}
            >
              {tf.label}
            </button>
          ))}
          </div>
          <div className="w-px h-4 bg-slate-200 dark:bg-white/10 mx-1 shrink-0" />
          {/* Gann toggle */}
          <button
            onClick={() => setShowGannLines(!showGannLines)}
            className={`px-2 py-1 text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1 whitespace-nowrap shrink-0 ${
              showGannLines ? 'text-[#3B82F6]' : 'text-zinc-500'
            }`}
            data-testid="gann-toggle"
          >
            <ChartLine size={12} weight="bold" />
            <span className="hidden sm:inline">GANN</span>
          </button>
          {/* SMC toggle */}
          <button
            onClick={() => setSmcActive(!smcActive)}
            className={`px-2 py-1 text-[10px] font-bold uppercase tracking-wider transition-all whitespace-nowrap shrink-0 border ${
              smcActive
                ? 'text-[#F5A623] border-[#F5A623]/40 bg-[#F5A623]/8'
                : 'text-zinc-500 border-transparent'
            }`}
            data-testid="smc-toggle"
            title="SMC Auto Mark — FVG + Liquidity + Order Blocks"
          >
            SMC
          </button>
          {/* Log toggle */}
          <button
            onClick={() => setSemiLogScale(!semiLogScale)}
            className={`px-2 py-1 text-[10px] font-bold uppercase tracking-wider transition-all whitespace-nowrap shrink-0 ${
              semiLogScale ? 'text-[#F5A623]' : 'text-zinc-500'
            }`}
            data-testid="log-toggle"
          >
            LOG
          </button>
          {/* Data source toggle — Yahoo / Groww (Indian stocks only) */}
          {!isCrypto && onDataSourceChange && (
            <>
              <div className="w-px h-4 bg-slate-200 dark:bg-white/10 mx-1 shrink-0" />
              <div className="flex items-center gap-0 shrink-0 border border-slate-200 dark:border-white/10">
                <button
                  onClick={() => onDataSourceChange('yahoo')}
                  className={`px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider transition-all ${
                    dataSource === 'yahoo' ? 'bg-slate-900 dark:bg-white text-white dark:text-black' : 'text-slate-400 dark:text-zinc-500 hover:text-slate-800 dark:hover:text-white'
                  }`}
                  data-testid="src-yahoo"
                  title="Yahoo Finance"
                >Y</button>
                <button
                  onClick={() => onDataSourceChange('groww')}
                  className={`px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider transition-all ${
                    dataSource === 'groww' ? 'bg-[#00E676] text-black' : 'text-slate-400 dark:text-zinc-500 hover:text-slate-800 dark:hover:text-white'
                  }`}
                  data-testid="src-groww"
                  title="Groww live data"
                >G</button>
              </div>
            </>
          )}
          {/* Trade button (Indian stocks) — opens Groww order modal */}
          {!isCrypto && selectedStock && (
            <>
          <div className="w-px h-4 bg-slate-200 dark:bg-white/10 mx-1 shrink-0" />
              <button
                onClick={() => setShowTrade(true)}
                className="px-2 py-1 text-[10px] font-black uppercase tracking-wider bg-[#00E676] text-black hover:opacity-90 active:opacity-80 flex items-center gap-1 whitespace-nowrap shrink-0"
                data-testid="trade-btn"
              >
                <Lightning size={11} weight="fill" />
                TRADE
              </button>
            </>
          )}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {!pivotPoint && (
            <>
              <button
                onClick={() => setSelectMode('high')}
                className={`px-2 py-1 text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-0.5 whitespace-nowrap ${
                  selectMode === 'high' ? 'bg-[#FF3B30] text-white' : 'text-slate-400 dark:text-zinc-500 hover:text-slate-800 dark:hover:text-white'
                }`}
                data-testid="select-high-btn"
              >
                <TrendUp size={11} weight="bold" />
                <span className="hidden xs:inline">HIGH</span>
              </button>
              <button
                onClick={() => setSelectMode('low')}
                className={`px-2 py-1 text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-0.5 whitespace-nowrap ${
                  selectMode === 'low' ? 'bg-[#00E676] text-black' : 'text-slate-400 dark:text-zinc-500 hover:text-slate-800 dark:hover:text-white'
                }`}
                data-testid="select-low-btn"
              >
                <TrendDown size={11} weight="bold" />
                <span className="hidden xs:inline">LOW</span>
              </button>
            </>
          )}
          {pivotPoint && (
            <>
              <span className="text-[9px] font-mono text-slate-500 dark:text-zinc-400 whitespace-nowrap">
                P: {pivotPoint.price.toFixed(0)}
              </span>
              <button
                onClick={() => setIsMovingMode(!isMovingMode)}
                className={`px-2 py-1 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${
                  isMovingMode ? 'bg-[#F5A623] text-black' : 'text-slate-400 dark:text-zinc-500 hover:text-slate-800 dark:hover:text-white'
                }`}
                data-testid="move-pivot-btn"
              >
                {isMovingMode ? 'MOVE' : 'MOVE'}
              </button>
              <button onClick={handleDeleteGann} className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-slate-400 dark:text-zinc-500 hover:text-[#FF3B30]" data-testid="clear-gann-btn">
                <Trash size={12} weight="bold" />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Extension slider */}
      {pivotPoint && showGannLines && (
        <div className="flex items-center gap-3 px-3 py-1 border-b border-slate-200 dark:border-white/10 bg-white dark:bg-[#0A0A0A] shrink-0">
          <span className="text-[10px] text-slate-400 dark:text-zinc-500 font-mono whitespace-nowrap">Ext: {lineExtension}</span>
          <input
            type="range"
            min={10} max={100} step={5}
            value={lineExtension}
            onChange={(e) => setLineExtension(Number(e.target.value))}
            className="flex-1 h-1 accent-[#3B82F6]"
            data-testid="line-extension-slider"
          />
          <div className="flex items-center gap-2 text-[9px] font-mono">
            <span className="text-[#3B82F6]">1x1</span>
            <span className="text-[#A855F7]">2x1</span>
            <span className="text-[#FF0055]">1x2</span>
            <span className="text-[#F5A623]">3x1</span>
            <span className="text-[#00E676]">1x3</span>
          </div>
        </div>
      )}

      {/* Chart area */}
      <div className="flex-1 relative" ref={chartContainerRef}>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/80 dark:bg-[#0A0A0A]/80 z-10">
            <p className="text-xs font-mono text-slate-400 dark:text-zinc-400 animate-pulse">Loading chart data...</p>
          </div>
        )}
        {!loading && !stockData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <ChartLine size={48} className="text-slate-300 dark:text-zinc-700 mb-3" />
            <p className="text-sm text-slate-400 dark:text-zinc-500">Select a stock or crypto to view chart</p>
            <p className="text-[10px] text-slate-300 dark:text-zinc-600 mt-1 font-mono">Scroll to zoom / Drag to pan</p>
          </div>
        )}

        {/* Volume Profile Canvas Overlay — left side, clickable for price-level detail */}
        <canvas
          ref={vpCanvasRef}
          onClick={handleVPClick}
          onMouseMove={handleVPMouseMove}
          onMouseLeave={handleVPMouseLeave}
          style={{
            position: 'absolute', left: 0, top: 0,
            zIndex: 5,
            cursor: vpActive ? 'crosshair' : 'default',
            display: vpActive ? 'block' : 'none',
          }}
        />

        {/* SMC Auto Mark Canvas — full chart overlay, pointer-events none */}
        <canvas
          ref={smcCanvasRef}
          style={{
            position: 'absolute', left: 0, top: 0,
            zIndex: 4,
            pointerEvents: 'none',
            display: smcActive ? 'block' : 'none',
          }}
        />

        {/* VP Tooltip — price level detail popup */}
        {vpTooltip && (
          <div
            style={{ position: 'absolute', left: VP_WIDTH + 6, top: vpTooltip.y, zIndex: 25, minWidth: 168 }}
            className="bg-[#0D0D0D] border border-white/20 shadow-2xl text-[9px] font-mono"
            data-testid="vp-tooltip"
          >
            {/* Header */}
            <div className="px-2.5 py-1.5 border-b border-white/10 flex items-center justify-between gap-2">
              <span className="text-white font-bold text-[11px]">₹{vpTooltip.bin.price_mid.toFixed(2)}</span>
              <div className="flex gap-1 items-center flex-wrap justify-end">
                {vpTooltip.bin.is_poc && (
                  <span className="text-[#FF6B00] text-[7px] font-bold px-1 border border-[#FF6B00]/50">◆ POC</span>
                )}
                {Math.abs(vpTooltip.bin.price_mid - vpTooltip.vah) < vpTooltip.vah * 0.005 && (
                  <span className="text-[#A855F7] text-[7px] px-1 border border-[#A855F7]/50">VAH</span>
                )}
                {Math.abs(vpTooltip.bin.price_mid - vpTooltip.val) < vpTooltip.val * 0.005 && (
                  <span className="text-[#06B6D4] text-[7px] px-1 border border-[#06B6D4]/50">VAL</span>
                )}
                {vpTooltip.bin.in_value_area && !vpTooltip.bin.is_poc && (
                  <span className="text-zinc-600 text-[7px]">VA</span>
                )}
                <button
                  onClick={() => setVpTooltip(null)}
                  className="text-zinc-600 hover:text-white ml-1 text-[9px] leading-none"
                  data-testid="vp-tooltip-close"
                >✕</button>
              </div>
            </div>
            {/* Volume bars */}
            <div className="px-2.5 py-2 space-y-2">
              {[
                { label: 'Buy', vol: vpTooltip.bin.buy_vol, color: '#00E676' },
                { label: 'Sell', vol: vpTooltip.bin.sell_vol, color: '#FF3B30' },
              ].map(({ label, vol, color }) => (
                <div key={label}>
                  <div className="flex justify-between mb-0.5">
                    <span style={{ color }}>{label}</span>
                    <span style={{ color }} className="font-bold">{fmtVol(vol)}</span>
                  </div>
                  <div className="h-1.5 bg-zinc-800 rounded-sm overflow-hidden">
                    <div
                      style={{ width: `${(vol / vpTooltip.maxVol) * 100}%`, backgroundColor: color }}
                      className="h-full rounded-sm"
                    />
                  </div>
                </div>
              ))}
              {/* Stats */}
              <div className="pt-1 border-t border-white/5 space-y-1">
                {[
                  {
                    label: 'Delta',
                    val: fmtVol(vpTooltip.bin.buy_vol - vpTooltip.bin.sell_vol),
                    color: vpTooltip.bin.buy_vol >= vpTooltip.bin.sell_vol ? '#00E676' : '#FF3B30',
                    prefix: vpTooltip.bin.buy_vol >= vpTooltip.bin.sell_vol ? '+' : '',
                  },
                  { label: 'Total Vol', val: fmtVol(vpTooltip.bin.total_vol), color: '#D4D4D8' },
                  { label: '% of Peak', val: `${((vpTooltip.bin.total_vol / vpTooltip.maxVol) * 100).toFixed(1)}%`, color: '#A1A1AA' },
                ].map(({ label, val, color, prefix = '' }) => (
                  <div key={label} className="flex justify-between">
                    <span className="text-zinc-500">{label}</span>
                    <span style={{ color }}>{prefix}{val}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
        
        {/* Strategy Overlay Component */}
        <StrategyOverlay 
          chart={chartRef.current}
          bars={stockData?.bars}
          strategyData={strategyData}
          strategyType={activeStrategy}
          isActive={!!activeStrategy && !!strategyData}
        />
        
        {/* Timeframe Levels - Always visible */}
        <TimeframeLevels 
          series={candlestickSeriesRef.current}
          bars={stockData?.bars}
        />
      </div>

      {/* Status bar */}
      {selectMode && (
        <div className="px-3 py-1 border-t border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-[#141414] text-[10px] font-mono text-[#F5A623] shrink-0">
          Click on chart to select {selectMode === 'high' ? 'swing high' : 'swing low'} point
        </div>
      )}
      {isMovingMode && pivotPoint && (
        <div className="px-3 py-1 border-t border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-[#141414] text-[10px] font-mono text-[#F5A623] shrink-0">
          Click anywhere on chart to move pivot
        </div>
      )}

      {/* Groww Trade modal */}
      {showTrade && selectedStock && (
        <GrowwTradeModal
          ticker={selectedStock.ticker}
          currentPrice={stockData?.bars?.length ? stockData.bars[stockData.bars.length - 1].close : null}
          onClose={() => setShowTrade(false)}
        />
      )}
    </div>
  );
};

export default ChartPanel;
