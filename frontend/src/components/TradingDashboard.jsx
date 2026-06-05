import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import StockSearch from './StockSearch';
import ChartPanel from './ChartPanel';
import SignalDashboard from './SignalDashboard';
import SquareOf9Calculator from './SquareOf9Calculator';
import OIAnalysis from './OIAnalysis';
import AITradeAnalysis from './AITradeAnalysis';
import FallingKnifeAnalysis from './FallingKnifeAnalysis';
import ReversePriceSwings from './ReversePriceSwings';
import ExplosiveVolumeAnalysis from './ExplosiveVolumeAnalysis';
import GoldenSetupAnalysis from './GoldenSetupAnalysis';
import AIIndicatorScore from './AIIndicatorScore';
import GodzillaSetupAnalysis from './GodzillaSetupAnalysis';
import DemonAnalysis from './DemonAnalysis';
import GhostModeScanner from './GhostModeScanner';
import Watchlist from './Watchlist';
import PortfolioTracker from './PortfolioTracker';
import AlertSystem from './AlertSystem';
import GPTAnalysis from './GPTAnalysis';
import CryptoList from './CryptoList';
import CryptoDashboard from './CryptoDashboard';
import AutoScanner from './AutoScanner';
import SMCAnalysis from './SMCAnalysis';
import AMDSAnalysis from './AMDSAnalysis';
import MiroFishAnalysis from './MiroFishAnalysis';
import PACSOAnalysis from './PACSOAnalysis';
import StockNewsPopup from './StockNewsPopup';
import HybridDashboard from './hybrid/HybridDashboard';
import GannQSCPanel from './GannQSCPanel';
import RegulatoryWatchdogPanel from './RegulatoryWatchdogPanel';
import NarrativeSwingAnalysis from './NarrativeSwingAnalysis';
import HybridVWAPAnalysis from './HybridVWAPAnalysis';
import RLAgentPanel from './RLAgentPanel';
import EnsembleCockpitPanel from './EnsembleCockpitPanel';
import SectorRotationPicker from './SectorRotationPicker';
import MoneycontrolMovers from './MoneycontrolMovers';
import PECETracker from './PECETracker';
import VisualizeModal from './VisualizeModal';
import Gann3DPanel from './Gann3DPanel';
import VoiceCommandSystem from './VoiceCommandSystem';
import OrderFlowPanel from './OrderFlowPanel';
import KronosForecastPanel from './KronosForecastPanel';
import AIRouterPanel from './AIRouterPanel';
import GrowwPortfolio from './GrowwPortfolio';
import IndicesTickerBar from './IndicesTickerBar';
import TopOptionsSheet from './TopOptionsSheet';
import PaperTradingPanel from './PaperTradingPanel';
import SectorTrending from './SectorTrending';
import SectorStocksSheet from './SectorStocksSheet';
import TopMoversWidget from './TopMoversWidget';
import { Toaster, toast } from 'sonner';
import { Star, Wallet, Bell, ChartLineUp, List, CurrencyBtc, Lightning, Newspaper, ArrowsLeftRight, Sun, Moon } from '@phosphor-icons/react';
import { useTheme } from '../context/ThemeContext';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Map yfinance tickers → Groww trading symbols (avoids stale-state issue)
const YF_TO_GROWW = {
  '^NSEI':    { symbol: 'NIFTY',     exchange: 'NSE' },
  '^NSEBANK': { symbol: 'BANKNIFTY', exchange: 'NSE' },
  '^BSESN':   { symbol: 'SENSEX',    exchange: 'BSE' },
  '^CNXFIN':  { symbol: 'FINNIFTY',  exchange: 'NSE' },
  '^CNXIT':   { symbol: 'NIFTYIT',   exchange: 'NSE' },
  '^CNXAUTO': { symbol: 'NIFTYAUTO', exchange: 'NSE' },
  '^INDIAVIX':{ symbol: 'INDIAVIX',  exchange: 'NSE' },
};

const TradingDashboard = () => {
  const [hybridMode, setHybridMode] = useState(false);
  const [selectedStock, setSelectedStock] = useState(null);
  const [stockData, setStockData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [pivotPoint, setPivotPoint] = useState(null);
  const [gannFan, setGannFan] = useState(null);
  const [signal, setSignal] = useState(null);
  const [semiLogScale, setSemiLogScale] = useState(false);
  const [timeframe, setTimeframe] = useState({ multiplier: 1, timespan: 'day', label: '1D' });
  const [activeTab, setActiveTab] = useState('scanner');
  const [leftTab, setLeftTab] = useState('search');
  const [mobilePanel, setMobilePanel] = useState('chart');
  const [cryptoChartDays, setCryptoChartDays] = useState(7);
  const [showNews, setShowNews] = useState(false);
  const [dataSource, setDataSource] = useState('groww'); // 'yahoo' | 'groww'
  const [optionsSheet, setOptionsSheet] = useState(null); // { symbol, name } | null
  const [activeStrategy, setActiveStrategy] = useState(null); // Strategy type for overlay
  const [strategyData, setStrategyData] = useState(null); // Strategy analysis data
  const [pendingPaperTrade, setPendingPaperTrade] = useState(null); // Paper trade from scanner/strategy
  const [paperAutoExecute, setPaperAutoExecute] = useState(false); // Auto-execute paper trades
  const [sectorSheet, setSectorSheet] = useState(null); // sector obj for stocks sheet
  const [showVisualize, setShowVisualize] = useState(false); // Heatmaps/Network modal
  const [show3D, setShow3D] = useState(false); // 3D Gann chart
  const { theme, toggleTheme } = useTheme();
  const wsRef = useRef(null);

  // Handler for strategy analysis completion - updates chart overlays
  const handleStrategyAnalysis = (strategyType, data) => {
    setActiveStrategy(strategyType);
    setStrategyData(data);
  };

  // Handler for paper trade from scanner signal button
  const handlePaperTradeFromSignal = (signal) => {
    setPendingPaperTrade({ ...signal, symbol: selectedStock?.ticker });
    setActiveTab('paper');
    setMobilePanel('right');
  };

  // Auto-execute paper trade handler (called when auto-execute is ON and new signal fires)
  const handleAutoExecuteTrade = useCallback(async (signal) => {
    if (!selectedStock) return;
    try {
      await axios.post(`${API}/paper-trade/order`, {
        symbol: selectedStock.ticker,
        name: selectedStock.name || selectedStock.ticker,
        direction: signal.direction,
        quantity: 10,
        entry_price: signal.entry,
        stop_loss: signal.stoploss,
        target: signal.targets?.[0] || signal.day_target || signal.entry,
        strategy: signal.strategy,
        source: 'AUTO',
      });
      toast.success(`Auto Paper Trade: ${signal.direction} ${selectedStock.ticker} via ${signal.strategy}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Auto trade failed');
    }
  }, [selectedStock]);
  useEffect(() => {
    const wsUrl = BACKEND_URL.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws/prices';
    try {
      const ws = new WebSocket(wsUrl);
      ws.onopen = () => { wsRef.current = ws; };
      ws.onclose = () => { wsRef.current = null; };
      ws.onerror = () => {};
      return () => { if (ws.readyState === WebSocket.OPEN) ws.close(); };
    } catch { /* WebSocket not critical */ }
  }, []);

  const subscribeWS = (ticker) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'subscribe', tickers: [ticker] }));
    }
  };

  const fetchStockData = async (ticker, tf, sourceOverride) => {
    setLoading(true);
    try {
      const src = sourceOverride || dataSource;
      if (src === 'groww') {
        // Skip Groww for options — option intraday is always from NSE
        if (ticker?.startsWith('OPT_')) {
          setLoading(false);
          return;
        }
        const intvMap = {
          '1MIN':'1m',
          '5M':'5m','10M':'10m','15M':'15m','30M':'30m',
          '1H':'1h','4H':'4h','1D':'1d','1W':'1w',
          '1M':'1d','6M':'1d','1Y':'1w',
        };
        const daysMap = {
          '1MIN':7,
          '5M':10,'10M':15,'15M':15,'30M':25,
          '1H':60,'4H':150,'1D':120,'1W':400,
          '1M':30,'6M':180,'1Y':365,
        };
        const interval = intvMap[tf.label] || '1d';
        const days = daysMap[tf.label] || 120;
        // Use ticker-based mapping first (avoids stale React state issue for indices)
        const growwMap = YF_TO_GROWW[ticker];
        const groww_symbol = growwMap?.symbol
          || selectedStock?.groww_symbol
          || (ticker || '').replace('.NS','').replace('.BO','').replace(/^\^/,'');
        const exchange = growwMap?.exchange
          || selectedStock?.exchange
          || (ticker.endsWith('.BO') ? 'BSE' : 'NSE');
        try {
          const response = await axios.get(`${API}/groww/candles/${groww_symbol}`, {
            params: { interval, days_back: days, exchange }
          });
          setStockData({ ticker, bars: response.data.bars || [] });
          const src_label = response.data.source === 'yfinance_fallback' ? 'yfinance' : 'Groww';
          toast.success(`Loaded ${tf.label} (${src_label}) for ${groww_symbol}`);
        } catch (growwErr) {
          // Groww failed → silent fallback to yfinance
          const params = { timespan: tf.timespan, multiplier: tf.multiplier, limit: 120 };
          const response = await axios.get(`${API}/stock/bars/${ticker}`, { params });
          setStockData(response.data);
          toast.success(`Loaded ${tf.label} (yfinance) for ${ticker}`);
        }
        return;
      }
      const params = { timespan: tf.timespan, multiplier: tf.multiplier, limit: 120 };
      if (tf.days) {
        const fromDate = new Date();
        fromDate.setDate(fromDate.getDate() - tf.days);
        params.from_date = fromDate.toISOString().split('T')[0];
        params.to_date = new Date().toISOString().split('T')[0];
      }
      const response = await axios.get(`${API}/stock/bars/${ticker}`, { params });
      setStockData(response.data);
      toast.success(`Loaded ${tf.label} data for ${ticker}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Failed to load stock data');
    } finally {
      setLoading(false);
    }
  };

  // Fetch crypto chart data and convert to stockData format
  const fetchCryptoData = async (coinId, days) => {
    setLoading(true);
    try {
      const response = await axios.get(`${API}/crypto/chart/${coinId}?days=${days}`);
      const bars = (response.data.bars || []).map(b => ({
        timestamp: b.timestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: 0,
      }));
      setStockData({ ticker: coinId.toUpperCase(), bars });
    } catch (error) {
      if (error?.response?.status !== 429) {
        toast.error('Failed to load crypto chart');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleStockSelect = (stock) => {
    setStockData(null);
    setPivotPoint(null);
    setGannFan(null);
    setSignal(null);
    setSelectedStock(stock);
    const defaultTf = { multiplier: 1, timespan: 'day', label: '1D' };
    setTimeframe(defaultTf);
    fetchStockData(stock.ticker, defaultTf);
    subscribeWS(stock.ticker);
    setMobilePanel('chart');
    // News popup NOT auto-opened — user can open via newspaper icon button
  };

  // Map index symbol → underlying chart ticker
  const INDEX_TICKER_MAP = {
    NIFTY: { ticker: '^NSEI', name: 'NIFTY 50' },
    BANKNIFTY: { ticker: '^NSEBANK', name: 'BANK NIFTY' },
    FINNIFTY: { ticker: '^CNXFIN', name: 'FIN NIFTY' },
    SENSEX: { ticker: '^BSESN', name: 'SENSEX' },
  };

  const handleIndexClick = (symbol, name) => {
    // Load index intraday chart in the main chart panel
    const indexInfo = INDEX_TICKER_MAP[symbol];
    if (indexInfo) {
      const stockObj = {
        ticker: indexInfo.ticker,
        name: indexInfo.name,
        type: 'INDEX',
        exchange: symbol === 'SENSEX' ? 'BSE' : 'NSE',
        groww_symbol: symbol,
      };
      setStockData(null);
      setPivotPoint(null);
      setGannFan(null);
      setSignal(null);
      setSelectedStock(stockObj);
      const intradayTf = { multiplier: 5, timespan: 'minute', label: '5M' };
      setTimeframe(intradayTf);
      fetchStockData(indexInfo.ticker, intradayTf);
      setMobilePanel('chart');
    }
    // Also open options sheet (Call/Put options)
    setOptionsSheet({ symbol, name });
  };

  // Fetch intraday OHLC bars for an option (NSE chart-databyindex)
  const fetchOptionIntraday = async (option, intervalMin = 1) => {
    setLoading(true);
    try {
      const expiry = option.expiry_display || option.expiry;
      const response = await axios.get(`${API}/option/intraday`, {
        params: {
          underlying: option.underlying,
          strike: option.strike,
          option_type: option.type,
          expiry,
          interval_min: intervalMin,
        },
      });
      setStockData({
        ticker: response.data.ticker,
        bars: response.data.bars || [],
      });
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Failed to load option chart');
    } finally {
      setLoading(false);
    }
  };

  const handleOptionSelect = (option) => {
    const expiryNorm = option.expiry_display || option.expiry || '';
    const isSensex = option.underlying === 'SENSEX' || option.is_indicative;

    // SENSEX options are on BSE — no NSE intraday chart available.
    // Load SENSEX index chart (^BSESN) as a reference chart instead.
    if (isSensex) {
      const sensexStock = {
        ticker: '^BSESN',
        name: `SENSEX (${option.instrument} reference)`,
        type: 'INDEX',
        exchange: 'BSE',
        groww_symbol: 'SENSEX',
      };
      setStockData(null);
      setPivotPoint(null);
      setGannFan(null);
      setSignal(null);
      setSelectedStock(sensexStock);
      const intradayTf = { multiplier: 5, timespan: 'minute', label: '5M' };
      setTimeframe(intradayTf);
      fetchStockData('^BSESN', intradayTf);
      setOptionsSheet(null);
      setMobilePanel('chart');
      toast.info(`SENSEX 5-min chart — ${option.instrument} (indicative)`);
      return;
    }

    // Build a synthetic stock object for the option so the chart panel knows
    // what to render. type='OPTION' lets us guard against stock-specific flows
    // (WS subscribe, signal/pivot, gann fan).
    const stock = {
      ticker: `OPT_${option.underlying}_${option.strike}_${option.type}_${expiryNorm}`,
      name: option.instrument,
      type: 'OPTION',
      underlying: option.underlying,
      strike: option.strike,
      optionType: option.type,
      expiry: expiryNorm,
      last_price: option.last_price,
      change_pct: option.change_pct,
      selectedOption: option,
    };
    setStockData(null);
    setPivotPoint(null);
    setGannFan(null);
    setSignal(null);
    setSelectedStock(stock);
    setOptionsSheet(null);
    const optTf = { multiplier: 1, timespan: 'minute', label: '1MIN' };
    setTimeframe(optTf);
    fetchOptionIntraday(option, 1);
    setMobilePanel('chart');
    toast.success(
      `${option.instrument} chart loaded`,
      {
        description: `₹${option.last_price.toFixed(2)} (${option.change_pct >= 0 ? '+' : ''}${option.change_pct.toFixed(2)}%) · Exp ${expiryNorm}`,
      }
    );
  };

  const handleCryptoSelect = (crypto) => {
    setStockData(null);
    setPivotPoint(null);
    setGannFan(null);
    setSignal(null);
    setSelectedStock(crypto);
    setCryptoChartDays(7);
    fetchCryptoData(crypto.coin_id, 7);
    setMobilePanel('chart');
  };

  const handleTimeframeChange = (tf) => {
    setTimeframe(tf);
    if (selectedStock) {
      setPivotPoint(null);
      setGannFan(null);
      setSignal(null);
      if (selectedStock.type === 'CRYPTO') {
        // Map timeframe to crypto days
        const daysMap = { '5M': 1, '10M': 1, '15M': 1, '30M': 1, '1H': 1, '4H': 1, '1D': 7, '1W': 30, '1M': 30, '6M': 180, '1Y': 365 };
        const days = daysMap[tf.label] || 7;
        setCryptoChartDays(days);
        fetchCryptoData(selectedStock.coin_id, days);
      } else if (selectedStock.type === 'OPTION' && selectedStock.selectedOption) {
        // Options support 1m / 5m / 15m intraday only (NSE chart-databyindex tick data)
        const optIntervalMap = { '1MIN': 1, '5M': 5, '10M': 10, '15M': 15 };
        const ivm = optIntervalMap[tf.label] || 1;
        fetchOptionIntraday(selectedStock.selectedOption, ivm);
      } else {
        fetchStockData(selectedStock.ticker, tf);
      }
    }
  };

  const handlePivotSelect = async (pivot) => {
    setPivotPoint(pivot);
    if (!pivot) return;
    try {
      const response = await axios.post(`${API}/gann/fan`, {
        ticker: selectedStock.ticker,
        pivot_price: pivot.price,
        pivot_timestamp: pivot.timestamp,
        bars_count: 50
      });
      setGannFan(response.data);
      toast.success('Gann Fan calculated');
      fetchSignal(pivot);
    } catch (error) {
      toast.error('Failed to calculate Gann Fan');
    }
  };

  const fetchSignal = useCallback(async (pivot) => {
    if (!selectedStock || !pivot || selectedStock.type === 'CRYPTO' || selectedStock.type === 'OPTION') return;
    try {
      const response = await axios.get(`${API}/signal/${selectedStock.ticker}`, {
        params: { pivot_price: pivot.price, pivot_timestamp: pivot.timestamp }
      });
      setSignal(response.data);
    } catch (error) { /* silent */ }
  }, [selectedStock]);

  useEffect(() => {
    if (pivotPoint && selectedStock && selectedStock.type !== 'CRYPTO' && selectedStock.type !== 'OPTION') {
      const interval = setInterval(() => fetchSignal(pivotPoint), 60000);
      return () => clearInterval(interval);
    }
  }, [pivotPoint, selectedStock, fetchSignal]);

  const isCrypto = selectedStock?.type === 'CRYPTO';
  const isOption = selectedStock?.type === 'OPTION';

  const rightTabs = [
    { id: 'scanner',    label: 'SCANNER'     },
    { id: 'strategies', label: 'STRATEGIES'  },
    { id: 'paper',      label: 'PAPER'       },
    { id: 'rlagent',    label: 'RL AGENT'    },
    { id: 'ensemble',   label: 'AI ASSEMBLE' },
    { id: 'picker',     label: 'PICKER'      },
    { id: 'pece',       label: 'PE-CE OI'    },
  ];

  const leftTabs = [
    { id: 'search', label: 'Search' },
    { id: 'crypto', label: 'Crypto', icon: CurrencyBtc },
    { id: 'watchlist', label: 'Watchlist', icon: Star },
    { id: 'groww', label: 'Groww', icon: Lightning },
    { id: 'portfolio', label: 'Portfolio', icon: Wallet },
    { id: 'alerts', label: 'Alerts', icon: Bell },
  ];

  const mobilePanels = [
    { id: 'left', label: 'Menu', icon: List },
    { id: 'chart', label: 'Chart', icon: ChartLineUp },
    { id: 'right', label: 'Strategies', icon: Star },
  ];

  return (
    <div className="h-screen overflow-hidden bg-slate-100 dark:bg-[#0A0A0A] text-slate-900 dark:text-white flex flex-col transition-colors duration-200" data-testid="trading-dashboard">
      <Toaster theme={theme} position="top-right" richColors />

      {/* HYBRID MODE OVERLAY */}
      {hybridMode && (
        <HybridDashboard onBack={() => setHybridMode(false)} />
      )}

      {/* Normal Gann Trader UI (hidden when hybrid mode is on) */}
      {!hybridMode && (<>

      {/* Header */}
      <header className="h-12 md:h-14 border-b border-slate-200 dark:border-white/10 flex items-center justify-between px-3 lg:px-6 bg-white/90 dark:bg-[#0A0A0A]/90 backdrop-blur-md z-50 shrink-0 transition-colors duration-200" data-testid="dashboard-header">
        <div className="flex items-center gap-3">
          <h1 className="liquid-glass-brand text-sm md:text-lg font-black tracking-tighter uppercase" style={{ fontFamily: "'Chivo', sans-serif" }}>
            <span className="text-slate-900 dark:text-white">GANN</span>
            <span className="text-[#00E676] ml-1">TRADER</span>
          </h1>
          <span className="hidden sm:inline text-[10px] text-slate-400 dark:text-zinc-500 font-mono tracking-wider border border-slate-200 dark:border-white/10 px-2 py-0.5">
            {isCrypto ? 'CRYPTO' : 'NSE'}
          </span>
        </div>
        <div className="flex items-center gap-2 md:gap-3">
          {selectedStock && (
            <div className="flex items-center gap-1.5">
              {isCrypto && selectedStock.image && (
                <img src={selectedStock.image} alt="" className="w-4 h-4 rounded-full" />
              )}
              <span className="text-[10px] md:text-xs font-mono text-[#00E676]" data-testid="selected-ticker">
                {isCrypto
                  ? selectedStock.symbol?.toUpperCase()
                  : isOption
                  ? selectedStock.name
                  : selectedStock.ticker}
              </span>
              {!isOption && (
                <span className="hidden sm:inline text-[10px] text-zinc-500">{selectedStock.name}</span>
              )}
              {isOption && selectedStock.expiry && (
                <span className="hidden sm:inline text-[10px] text-zinc-500">Exp {selectedStock.expiry}</span>
              )}
              {!isCrypto && !isOption && (
                <button
                  onClick={() => setShowNews(true)}
                  className="ml-1 p-1 rounded hover:bg-slate-100 dark:hover:bg-white/10 transition-colors"
                  title="View News"
                  data-testid="news-btn"
                >
                  <Newspaper size={14} className="text-sky-400" />
                </button>
              )}
            </div>
          )}
          {/* THEME TOGGLE BUTTON */}
          <button
            onClick={toggleTheme}
            className="p-1.5 rounded-md border border-slate-200 dark:border-white/10 text-slate-500 dark:text-zinc-400 hover:bg-slate-100 dark:hover:bg-white/10 hover:text-slate-800 dark:hover:text-white transition-all duration-200"
            title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
            data-testid="theme-toggle"
          >
            {theme === 'dark'
              ? <Sun size={14} weight="bold" />
              : <Moon size={14} weight="bold" />
            }
          </button>
          {/* VISUALIZE BUTTON */}
          <button
            onClick={() => setShowVisualize(true)}
            className="liquid-glass-btn flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest border border-violet-500/50 text-violet-400 hover:bg-violet-500/20 hover:border-violet-500 px-2.5 py-1.5 rounded"
            data-testid="visualize-btn"
            title="Heatmaps · Correlation · Options Flow"
          >
            <span className="text-[10px]">VISUAL</span>
          </button>
          {/* 3D CHARTS BUTTON */}
          <button
            onClick={() => setShow3D(true)}
            className="liquid-glass-btn flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/20 hover:border-cyan-500 px-2.5 py-1.5 rounded"
            data-testid="gann3d-btn"
            title="3D Gann · Price Surface · Astro Cycles"
          >
            <span className="text-[10px]">3D</span>
          </button>
          {/* HYBRID MODE BUTTON */}
          <button
            onClick={() => setHybridMode(true)}
            className="liquid-glass-btn flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest border border-[#3366FF]/50 text-[#3366FF] hover:bg-[#3366FF]/20 hover:border-[#3366FF] px-2.5 py-1.5 rounded"
            data-testid="hybrid-mode-btn"
            title="Switch to QSC Hybrid Mode"
          >
            <ArrowsLeftRight size={12} weight="bold" />
            <span className="hidden sm:inline">HYBRID</span>
          </button>
        </div>
      </header>

      {/* Indices Live Ticker — NIFTY 50 / SENSEX / BANK NIFTY (tap → top options) */}
      <IndicesTickerBar onIndexClick={handleIndexClick} />

      {/* Mobile Tab Bar — full-width 3-panel nav (improved) */}
      <div className="flex lg:hidden border-b border-slate-200 dark:border-white/10 shrink-0 bg-white dark:bg-[#0D0D0D] transition-colors duration-200">
        {mobilePanels.map(p => (
          <button key={p.id} onClick={() => setMobilePanel(p.id)}
            className={`flex-1 py-2.5 flex flex-col items-center justify-center gap-0.5 transition-all duration-200 relative ${
              mobilePanel === p.id
                ? 'text-[#00E676]'
                : 'text-slate-400 dark:text-zinc-500'
            }`}
            data-testid={`mobile-panel-${p.id}`}>
            {mobilePanel === p.id && (
              <span className="absolute top-0 inset-x-4 h-0.5 bg-[#00E676] rounded-b-full" />
            )}
            <p.icon size={18} weight={mobilePanel === p.id ? 'fill' : 'regular'} />
            <span className="text-[9px] font-bold uppercase tracking-wider whitespace-nowrap">{p.label}</span>
          </button>
        ))}
      </div>

      {/* Main Grid — flex-1 to fill remaining space */}
      <div className="flex-1 flex flex-col lg:grid lg:grid-cols-12 overflow-hidden min-h-0">

        {/* Left Sidebar */}
        <aside className={`lg:col-span-3 xl:col-span-2 border-r border-slate-200 dark:border-white/10 bg-white dark:bg-[#0A0A0A] flex flex-col overflow-y-auto transition-colors duration-200 ${mobilePanel !== 'left' ? 'hidden lg:flex' : 'flex'}`} data-testid="left-sidebar">
          {/* Left Tabs — horizontally scrollable on mobile */}
          <div className="flex border-b border-slate-200 dark:border-white/10 shrink-0 overflow-x-auto scrollbar-none bg-white dark:bg-[#0A0A0A]">
            {leftTabs.map(tab => (
              <button key={tab.id} onClick={() => setLeftTab(tab.id)}
                className={`flex-shrink-0 flex-1 min-w-[56px] py-2.5 px-1 text-[9px] font-bold uppercase tracking-[0.1em] transition-colors whitespace-nowrap ${
                  leftTab === tab.id
                    ? 'text-[#00E676] border-b-2 border-[#00E676] bg-[#00E676]/10 dark:bg-white/5'
                    : 'text-slate-400 dark:text-zinc-500 hover:text-slate-600 dark:hover:text-zinc-300'
                }`}
                data-testid={`left-tab-${tab.id}`}>
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto">
            {leftTab === 'search' && (
              <>
                <div className="p-3 border-b border-white/10">
                  <StockSearch onStockSelect={handleStockSelect} selectedStock={selectedStock} />
                </div>
                {/* GannQSC — super-fast in-RAM signal (auto-feeds when chart loads) */}
                {stockData?.bars?.length > 0 && selectedStock && (
                  <div className="border-b border-white/10 p-3">
                    <GannQSCPanel
                      bars={stockData.bars}
                      ticker={isCrypto ? selectedStock.symbol : selectedStock.ticker}
                    />
                  </div>
                )}
                {/* Regulatory Watchdog — global + Indian market sentiment */}
                <div className="border-b border-white/10 p-3">
                  <RegulatoryWatchdogPanel />
                </div>
                {/* Sector Trending — top NSE sector movers */}
                <SectorTrending onSectorSelect={(sector) => setSectorSheet(sector)} />

                {/* Top Movers Today */}
                <TopMoversWidget onStockSelect={(stock) => {
                  setSelectedStock({ ticker: stock.ticker, name: stock.name, type: 'stock' });
                  const tf = { multiplier: 1, timespan: 'day', label: '1D' };
                  setTimeframe(tf);
                  fetchStockData(stock.ticker, tf);
                  setMobilePanel('chart');
                }} />
                {signal && <div className="border-b border-white/10"><SignalDashboard signal={signal} /></div>}
                {stockData && !isCrypto && <div className="border-b border-white/10"><SquareOf9Calculator currentPrice={stockData.bars[stockData.bars.length - 1]?.close} /></div>}
                {selectedStock && selectedStock.type === 'INDEX' && <div className="border-b border-white/10"><OIAnalysis symbol={selectedStock.ticker.replace('.NS', '')} /></div>}
              </>
            )}
            {leftTab === 'crypto' && (
              <CryptoList onCryptoSelect={handleCryptoSelect} selectedCrypto={isCrypto ? selectedStock : null} />
            )}
            {leftTab === 'watchlist' && <Watchlist onStockSelect={handleStockSelect} selectedStock={selectedStock} />}
            {leftTab === 'groww' && <GrowwPortfolio />}
            {leftTab === 'portfolio' && <PortfolioTracker selectedStock={selectedStock} />}
            {leftTab === 'alerts' && <AlertSystem selectedStock={selectedStock} />}
          </div>
        </aside>

        {/* Center Chart */}
        <main className={`flex-1 lg:col-span-6 xl:col-span-7 flex flex-col relative min-h-0 overflow-y-auto overflow-x-hidden ${mobilePanel !== 'chart' ? 'hidden lg:flex' : 'flex'}`} data-testid="center-chart">
          {/* Chart — fixed height inside scrollable column so chart canvas always has room */}
          <div className="shrink-0" style={{ height: 'min(60vh, 560px)', minHeight: '320px' }}>
            <ChartPanel
              stockData={stockData}
              loading={loading}
              selectedStock={selectedStock}
              onPivotSelect={handlePivotSelect}
              pivotPoint={pivotPoint}
              gannFan={gannFan}
              semiLogScale={semiLogScale}
              setSemiLogScale={setSemiLogScale}
              timeframe={timeframe}
              onTimeframeChange={handleTimeframeChange}
              isCrypto={isCrypto}
              dataSource={dataSource}
              onDataSourceChange={(s) => {
                setDataSource(s);
                if (selectedStock && !isCrypto) {
                  fetchStockData(selectedStock.ticker, timeframe, s);
                }
              }}
              activeStrategy={activeStrategy}
              strategyData={strategyData}
            />
          </div>
          {/* Order Flow Panel — below chart, scroll to see */}
          {stockData?.bars?.length >= 30 && (
            <OrderFlowPanel stockData={stockData} selectedStock={selectedStock} />
          )}
          {/* Kronos AI Forecast — below Order Flow */}
          <KronosForecastPanel selectedStock={selectedStock} timeframe={timeframe} />
        </main>

        {/* Right Sidebar */}
        <aside className={`lg:col-span-3 border-l border-slate-200 dark:border-white/10 bg-white dark:bg-[#0A0A0A] flex flex-col overflow-hidden transition-colors duration-200 ${mobilePanel !== 'right' ? 'hidden lg:flex' : 'flex'}`} data-testid="right-sidebar">
          {/* Tabs — horizontally scrollable on mobile */}
          <div className="flex border-b border-slate-200 dark:border-white/10 shrink-0 overflow-x-auto scrollbar-none bg-white dark:bg-[#0A0A0A]">
            {rightTabs.map(tab => tab.isDivider ? (
              <div key={tab.id} className="flex items-center shrink-0 px-2 border-l border-slate-200 dark:border-white/10 select-none">
                <span className="text-[8px] font-black uppercase tracking-[0.2em] text-slate-400 dark:text-zinc-600 whitespace-nowrap">{tab.label}</span>
              </div>
            ) : (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                className={`flex-shrink-0 flex-1 min-w-[64px] py-2.5 px-2 text-[9px] font-bold uppercase tracking-[0.1em] transition-colors whitespace-nowrap ${
                  activeTab === tab.id
                    ? 'text-[#00E676] border-b-2 border-[#00E676] bg-[#00E676]/10 dark:bg-white/5'
                    : 'text-slate-400 dark:text-zinc-500 hover:text-slate-600 dark:hover:text-zinc-300'
                }`}
                data-testid={`tab-${tab.id}`}>
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          <div className="flex-1 overflow-y-auto">
            {activeTab === 'scanner' && (
              <AutoScanner
                selectedStock={selectedStock}
                onPaperTrade={handlePaperTradeFromSignal}
                autoExecute={paperAutoExecute}
                onAutoExecuteTrade={handleAutoExecuteTrade}
                onStockSelect={handleStockSelect}
              />
            )}

            {activeTab === 'strategies' && (
              <div className="divide-y divide-white/10">
                {selectedStock && stockData && (
                  <>
                    {isCrypto && <CryptoDashboard preSelectedCoin={selectedStock} />}
                    <SMCAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <AMDSAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <MiroFishAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <PACSOAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <GPTAnalysis stockData={stockData} selectedStock={selectedStock} timeframe={timeframe} onAnalysisComplete={handleStrategyAnalysis} />
                    <AITradeAnalysis stockData={stockData} selectedStock={selectedStock} timeframe={timeframe} onAnalysisComplete={handleStrategyAnalysis} />
                    <FallingKnifeAnalysis stockData={stockData} selectedStock={selectedStock} timeframe={timeframe} onAnalysisComplete={handleStrategyAnalysis} />
                    <ReversePriceSwings stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <ExplosiveVolumeAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <GoldenSetupAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <AIIndicatorScore stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <GodzillaSetupAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <DemonAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <NarrativeSwingAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                    <HybridVWAPAnalysis stockData={stockData} selectedStock={selectedStock} onAnalysisComplete={handleStrategyAnalysis} />
                  </>
                )}
                {!selectedStock && (
                  <div className="p-6 text-center">
                    <p className="text-slate-400 dark:text-zinc-500 text-sm">Select a stock or crypto to view strategies</p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'paper' && (
              <PaperTradingPanel
                selectedStock={selectedStock}
                pendingTrade={pendingPaperTrade}
                onPendingTradeConsumed={() => setPendingPaperTrade(null)}
                autoExecute={paperAutoExecute}
                onAutoExecuteChange={setPaperAutoExecute}
              />
            )}

            {activeTab === 'rlagent' && (
              <RLAgentPanel selectedStock={selectedStock} />
            )}

            {activeTab === 'ensemble' && (
              <EnsembleCockpitPanel selectedStock={selectedStock} />
            )}

            {activeTab === 'picker' && (
              <>
                <SectorRotationPicker onStockSelect={handleStockSelect} />
                <MoneycontrolMovers
                  onPaperTrade={(sig) => {
                    setPendingPaperTrade({ ...sig, symbol: sig.symbol });
                    setActiveTab('paper');
                    setMobilePanel('right');
                  }}
                />
              </>
            )}

            {activeTab === 'pece' && (
              <PECETracker />
            )}
          </div>
        </aside>
      </div>

      {/* Visualize Modal */}
      {showVisualize && (
        <VisualizeModal
          onClose={() => setShowVisualize(false)}
          selectedStock={selectedStock}
        />
      )}

      {/* 3D Gann Panel */}
      {show3D && (
        <Gann3DPanel
          onClose={() => setShow3D(false)}
          stockData={stockData}
          selectedStock={selectedStock}
        />
      )}

      {/* Voice Command System */}
      <VoiceCommandSystem
        onLoadStock={(symbol) => {
          const stock = { ticker: symbol, name: symbol.replace('.NS',''), type: 'stock' };
          handleStockSelect(stock);
        }}
        onNavigate={(tabId) => setActiveTab(tabId)}
        onSetAlert={(price) => {
          setActiveTab('scanner');
          setMobilePanel('right');
        }}
        onRunStrategy={(strat) => {
          setActiveTab('strategies');
          setMobilePanel('right');
        }}
        onScanMarket={() => {
          setActiveTab('scanner');
          setMobilePanel('right');
        }}
      />

      {/* News Popup */}
      {showNews && selectedStock && !isCrypto && !isOption && (
        <StockNewsPopup
          ticker={selectedStock.ticker}
          onClose={() => setShowNews(false)}
        />
      )}

      {/* Top Options Sheet (opens when an index pill is tapped) */}
      {optionsSheet && (
        <TopOptionsSheet
          symbol={optionsSheet.symbol}
          name={optionsSheet.name}
          onClose={() => setOptionsSheet(null)}
          onOptionSelect={handleOptionSelect}
        />
      )}

      {/* Sector Stocks Sheet (opens when a sector is clicked) */}
      {sectorSheet && (
        <SectorStocksSheet
          sector={sectorSheet}
          onClose={() => setSectorSheet(null)}
          onStockSelect={(stock) => {
            setSelectedStock({ ticker: stock.ticker, name: stock.name, type: 'stock' });
            const tf = { multiplier: 1, timespan: 'day', label: '1D' };
            setTimeframe(tf);
            fetchStockData(stock.ticker, tf);
            setMobilePanel('chart');
          }}
        />
      )}
    </>)}
    </div>
  );
};

export default TradingDashboard;
