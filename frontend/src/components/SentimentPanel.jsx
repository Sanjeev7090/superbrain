/**
 * SentimentPanel — News Sentiment + Fear & Greed Index
 */
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Newspaper, TrendingUp, TrendingDown, Minus, RefreshCw, Activity } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const LABEL_CFG = {
  BULLISH:          { color: '#10b981', bg: '#10b98118', icon: TrendingUp },
  SLIGHTLY_BULLISH: { color: '#34d399', bg: '#34d39918', icon: TrendingUp },
  NEUTRAL:          { color: '#6b7280', bg: '#6b728018', icon: Minus },
  SLIGHTLY_BEARISH: { color: '#f59e0b', bg: '#f59e0b18', icon: TrendingDown },
  BEARISH:          { color: '#ef4444', bg: '#ef444418', icon: TrendingDown },
};

function SentimentPill({ label }) {
  const cfg = LABEL_CFG[label] || LABEL_CFG.NEUTRAL;
  return (
    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: cfg.bg, color: cfg.color }}>
      {label?.replace('_', ' ')}
    </span>
  );
}

function FearGreedGauge({ score, label }) {
  const angle = ((score / 100) * 180) - 90;
  const color = score < 25 ? '#ef4444' : score < 45 ? '#f59e0b' : score < 55 ? '#6b7280' : score < 75 ? '#10b981' : '#22c55e';

  return (
    <div className="flex flex-col items-center">
      <svg width="140" height="80" viewBox="-10 0 160 80">
        {/* Background arc */}
        <path d="M 10 70 A 60 60 0 0 1 130 70" fill="none" stroke="#333" strokeWidth="10" strokeLinecap="round" />
        {/* Colored arc proportional to score */}
        <path
          d={`M 10 70 A 60 60 0 ${score > 50 ? 1 : 0} 1 ${70 + 60 * Math.cos((angle - 90) * Math.PI / 180)} ${70 + 60 * Math.sin((angle - 90) * Math.PI / 180)}`}
          fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
        />
        {/* Needle */}
        <line
          x1="70" y1="70"
          x2={70 + 48 * Math.cos((angle - 90) * Math.PI / 180)}
          y2={70 + 48 * Math.sin((angle - 90) * Math.PI / 180)}
          stroke={color} strokeWidth="2.5" strokeLinecap="round"
        />
        <circle cx="70" cy="70" r="4" fill={color} />
        {/* Score text */}
        <text x="70" y="65" textAnchor="middle" fill={color} fontSize="16" fontWeight="bold">{Math.round(score)}</text>
      </svg>
      <span className="text-[11px] font-bold mt-1" style={{ color }}>{label?.replace(/_/g, ' ')}</span>
      <div className="flex justify-between w-full text-[8px] text-zinc-600 mt-1 px-2">
        <span>FEAR</span><span>NEUTRAL</span><span>GREED</span>
      </div>
    </div>
  );
}

export default function SentimentPanel({ selectedStock }) {
  const [ticker, setTicker]     = useState(selectedStock?.ticker || 'RELIANCE.NS');
  const [news, setNews]         = useState(null);
  const [market, setMarket]     = useState(null);
  const [fearGreed, setFearGreed] = useState(null);
  const [loading, setLoading]   = useState(false);

  const fetchAll = useCallback(async (tk) => {
    const t = tk || ticker;
    setLoading(true);
    try {
      const [nRes, mRes, fgRes] = await Promise.all([
        axios.get(`${API}/advanced/sentiment/news`, { params: { ticker: t } }),
        axios.get(`${API}/advanced/sentiment/market`, { params: { tickers: 'RELIANCE.NS,TCS.NS,INFY.NS,HDFCBANK.NS,ICICIBANK.NS' } }),
        axios.get(`${API}/advanced/sentiment/fear-greed`, { params: { pcr: 1.0, india_vix: 15, breadth: 0.5 } }),
      ]);
      setNews(nRes.data);
      setMarket(mRes.data);
      setFearGreed(fgRes.data);
    } catch (e) {
      toast.error('Sentiment fetch failed');
    } finally { setLoading(false); }
  }, [ticker]);

  useEffect(() => { fetchAll(); }, []);

  useEffect(() => {
    if (selectedStock?.ticker) {
      setTicker(selectedStock.ticker);
      fetchAll(selectedStock.ticker);
    }
  }, [selectedStock?.ticker]);

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Newspaper size={15} className="text-blue-400" />
          <span className="text-[13px] font-bold text-white">Sentiment Intelligence</span>
        </div>
        <button onClick={() => fetchAll()} disabled={loading} className="text-zinc-500 hover:text-white transition-colors disabled:opacity-40">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Ticker input */}
      <div className="flex gap-2">
        <input
          value={ticker}
          onChange={e => setTicker(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && fetchAll()}
          placeholder="RELIANCE.NS"
          className="flex-1 bg-zinc-900 border border-zinc-700 rounded px-2.5 py-1.5 text-[11px] text-zinc-200 font-mono focus:outline-none focus:border-blue-500"
          data-testid="sentiment-ticker-input"
        />
        <button onClick={() => fetchAll()} disabled={loading}
          className="px-3 py-1.5 text-[11px] font-bold bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50"
          data-testid="sentiment-fetch-btn">
          Fetch
        </button>
      </div>

      {/* Fear & Greed */}
      {fearGreed && (
        <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3">
          <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold mb-2">Fear & Greed Index</p>
          <FearGreedGauge score={fearGreed.score} label={fearGreed.label} />
          <div className="grid grid-cols-2 gap-2 mt-3 text-[10px]">
            {Object.entries(fearGreed.components || {}).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-zinc-500">{k.replace('_score','').replace(/_/g,' ')}</span>
                <span className="text-zinc-300">{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stock news sentiment */}
      {news && (
        <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">
              {news.ticker?.replace('.NS','')} News ({news.article_count} articles)
            </p>
            <SentimentPill label={news.aggregate_label} />
          </div>
          <div className="flex gap-4 text-[10px] mb-3">
            <span className="text-green-400">↑ {news.bullish_count} Bullish</span>
            <span className="text-zinc-500">↔ {news.neutral_count} Neutral</span>
            <span className="text-red-400">↓ {news.bearish_count} Bearish</span>
            <span className="text-blue-400 ml-auto">Score: {news.avg_score?.toFixed(3)}</span>
          </div>
          <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
            {news.articles?.slice(0, 8).map((a, i) => {
              const cfg = LABEL_CFG[a.label] || LABEL_CFG.NEUTRAL;
              return (
                <div key={i} className="flex items-start gap-2 py-1.5 border-b border-zinc-800/50 last:border-0" data-testid={`news-item-${i}`}>
                  <div className="w-1 h-1 rounded-full mt-2 shrink-0" style={{ background: cfg.color }} />
                  <div className="min-w-0 flex-1">
                    <a href={a.url} target="_blank" rel="noopener noreferrer"
                      className="text-[11px] text-zinc-300 hover:text-white leading-tight line-clamp-2">
                      {a.title}
                    </a>
                    <div className="flex items-center gap-2 mt-0.5">
                      <SentimentPill label={a.label} />
                      <span className="text-[9px] text-zinc-600">{a.score?.toFixed(3)}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Market-wide sentiment */}
      {market && (
        <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Market Sentiment</p>
            <span className="text-[11px] font-bold" style={{ color: market.aggregate_score > 0.1 ? '#10b981' : market.aggregate_score < -0.1 ? '#ef4444' : '#6b7280' }}>
              {market.market_mood}
            </span>
          </div>
          <div className="flex gap-3 text-[10px] mb-2">
            <span className="text-green-400">↑ {market.bullish_pct}% Bullish</span>
            <span className="text-red-400">↓ {market.bearish_pct}% Bearish</span>
          </div>
          <div className="space-y-1">
            {Object.entries(market.tickers || {}).map(([t, d]) => {
              const cfg = LABEL_CFG[d.label] || LABEL_CFG.NEUTRAL;
              return (
                <div key={t} className="flex items-center justify-between text-[10px]">
                  <span className="text-zinc-400">{t.replace('.NS','')}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-600">{d.articles} articles</span>
                    <SentimentPill label={d.label} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!news && !loading && (
        <div className="text-center py-8 text-zinc-600 text-[12px]">Click Fetch to load sentiment data</div>
      )}
    </div>
  );
}
