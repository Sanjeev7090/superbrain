import { useEffect, useRef } from 'react';

/**
 * TimeframeLevels - Draws permanent high/low levels for multiple timeframes
 * Always visible on chart, updates when stock changes
 */
const TimeframeLevels = ({ series, bars }) => {
  const priceLines = useRef([]);

  // Clear all price lines
  const clearLines = () => {
    priceLines.current.forEach(line => {
      try {
        if (series && line) {
          series.removePriceLine(line);
        }
      } catch (e) {}
    });
    priceLines.current = [];
  };

  // Calculate high/low for a time period
  const calculateHighLow = (bars, periodBars) => {
    if (!bars || bars.length === 0) return { high: 0, low: 0 };
    
    const recentBars = bars.slice(-periodBars);
    const high = Math.max(...recentBars.map(b => b.high));
    const low = Math.min(...recentBars.map(b => b.low));
    
    return { high, low };
  };

  // Draw timeframe levels
  useEffect(() => {
    if (!series || !bars || bars.length === 0) {
      clearLines();
      return;
    }

    clearLines();

    // Estimate bars per timeframe (assuming 1D bars)
    const barsPerDay = 1;
    const barsPerWeek = 5;
    const barsPerMonth = 20;
    const barsPerYear = 252;

    // Calculate levels for different timeframes
    const timeframes = [
      // Major timeframes (dashed lines)
      { name: '4Y High', period: barsPerYear * 4, type: 'high', color: '#22c55e', style: 2 },
      { name: '4Y Low', period: barsPerYear * 4, type: 'low', color: '#22c55e', style: 2 },
      
      { name: '1Y High', period: barsPerYear, type: 'high', color: '#eab308', style: 2 },
      { name: '1Y Low', period: barsPerYear, type: 'low', color: '#eab308', style: 2 },
      
      { name: '6M High', period: barsPerMonth * 6, type: 'high', color: '#a855f7', style: 2 },
      { name: '6M Low', period: barsPerMonth * 6, type: 'low', color: '#a855f7', style: 2 },
      
      { name: '30D High', period: 30, type: 'high', color: '#f97316', style: 2 },
      { name: '30D Low', period: 30, type: 'low', color: '#f97316', style: 2 },
      
      { name: '1W High', period: barsPerWeek, type: 'high', color: '#06b6d4', style: 2 },
      { name: '1W Low', period: barsPerWeek, type: 'low', color: '#06b6d4', style: 2 },
      
      // Shorter timeframes (solid lines) - for intraday bars
      { name: '4H High', period: 16, type: 'high', color: '#ef4444', style: 0 },
      { name: '4H Low', period: 16, type: 'low', color: '#ef4444', style: 0 },
      
      { name: '1H High', period: 4, type: 'high', color: '#f59e0b', style: 0 },
      { name: '1H Low', period: 4, type: 'low', color: '#f59e0b', style: 0 },
      
      { name: '30M High', period: 2, type: 'high', color: '#84cc16', style: 0 },
      { name: '30M Low', period: 2, type: 'low', color: '#84cc16', style: 0 },
    ];

    timeframes.forEach(tf => {
      try {
        const { high, low } = calculateHighLow(bars, tf.period);
        const price = tf.type === 'high' ? high : low;
        
        if (price > 0) {
          const line = series.createPriceLine({
            price: price,
            color: tf.color,
            lineWidth: 1,
            lineStyle: tf.style, // 0=solid, 2=dashed
            axisLabelVisible: true,
            title: tf.name,
          });
          
          priceLines.current.push(line);
        }
      } catch (e) {
        console.error('Error adding price line:', e);
      }
    });

    return () => clearLines();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, bars]);

  return null; // No DOM rendering
};

export default TimeframeLevels;
