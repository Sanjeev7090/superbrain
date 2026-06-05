import React, { useEffect, useRef } from 'react';

/**
 * StrategyOverlay - Draws strategy-specific lines, zones, and signals on chart
 * Lightweight and performant overlay system
 */
const StrategyOverlay = ({ 
  chart, 
  bars, 
  strategyData, 
  strategyType,
  isActive 
}) => {
  const overlaysRef = useRef([]);

  // Clear all overlays
  const clearOverlays = () => {
    if (chart && overlaysRef.current.length > 0) {
      overlaysRef.current.forEach(series => {
        try { chart.removeSeries(series); } catch (e) {}
      });
      overlaysRef.current = [];
    }
  };

  // Draw overlays based on strategy type
  useEffect(() => {
    if (!chart || !bars || !strategyData || !isActive) {
      clearOverlays();
      return;
    }

    clearOverlays();

    try {
      switch (strategyType) {
        case 'falling_knife':
          drawFallingKnifeOverlay();
          break;
        case 'golden_setup':
          drawGoldenSetupOverlay();
          break;
        case 'demon':
          drawDemonOverlay();
          break;
        case 'smc':
          drawSMCOverlay();
          break;
        case 'amds':
          drawAMDSOverlay();
          break;
        case 'reverse_swings':
          drawReverseSwingsOverlay();
          break;
        case 'godzilla':
          drawGodzillaOverlay();
          break;
        case 'narrative_swing':
          drawNarrativeSwingOverlay();
          break;
        default:
          break;
      }
    } catch (e) {
      console.error('Strategy overlay error:', e);
    }

    return () => clearOverlays();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, bars, strategyData, strategyType, isActive]);

  // Falling Knife: Show drop detection + bounce levels
  const drawFallingKnifeOverlay = () => {
    const { signal_type, entry_price, stop_loss, target } = strategyData;
    
    // Entry line
    if (entry_price) {
      const entrySeries = chart.addLineSeries({
        color: signal_type === 'BUY' ? '#00E676' : '#FF3333',
        lineWidth: 2,
        lineStyle: 2, // dashed
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'Entry'
      });
      const entryData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: entry_price
      }));
      entrySeries.setData(entryData);
      overlaysRef.current.push(entrySeries);
    }

    // Stop Loss zone
    if (stop_loss) {
      const slSeries = chart.addLineSeries({
        color: '#FF0055',
        lineWidth: 1,
        lineStyle: 1, // dotted
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'SL'
      });
      const slData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: stop_loss
      }));
      slSeries.setData(slData);
      overlaysRef.current.push(slSeries);
    }

    // Target line
    if (target) {
      const targetSeries = chart.addLineSeries({
        color: '#3B82F6',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'Target'
      });
      const targetData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: target
      }));
      targetSeries.setData(targetData);
      overlaysRef.current.push(targetSeries);
    }

    // Buy/Sell markers
    if (entry_price && bars.length > 0) {
      const lastBar = bars[bars.length - 1];
      const markers = [{
        time: lastBar.timestamp / 1000,
        position: signal_type === 'BUY' ? 'belowBar' : 'aboveBar',
        color: signal_type === 'BUY' ? '#00E676' : '#FF3333',
        shape: signal_type === 'BUY' ? 'arrowUp' : 'arrowDown',
        text: signal_type
      }];
      chart.candlestickSeries?.setMarkers?.(markers);
    }
  };

  // Golden Setup: SMA lines + breakout zones
  const drawGoldenSetupOverlay = () => {
    const { sma_10, sma_20, entry_price, stop_loss, target } = strategyData;

    // SMA-10
    if (sma_10 && Array.isArray(sma_10)) {
      const sma10Series = chart.addLineSeries({
        color: '#F5A623',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'SMA-10'
      });
      const smaData = sma_10.map((val, i) => ({
        time: bars[i]?.timestamp / 1000,
        value: val
      })).filter(d => d.time && d.value);
      sma10Series.setData(smaData);
      overlaysRef.current.push(sma10Series);
    }

    // Entry + SL + Target
    drawLevels(entry_price, stop_loss, target, strategyData.signal_type);
  };

  // DEMON: Multiple indicator confluences
  const drawDemonOverlay = () => {
    const { entry_price, stop_loss, target1, target2, signal_type } = strategyData;
    
    // Entry level
    drawLevels(entry_price, stop_loss, target1, signal_type);

    // Target 2 (if available)
    if (target2) {
      const t2Series = chart.addLineSeries({
        color: '#A855F7',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'T2'
      });
      const t2Data = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: target2
      }));
      t2Series.setData(t2Data);
      overlaysRef.current.push(t2Series);
    }
  };

  // SMC: Order blocks + FVG zones
  const drawSMCOverlay = () => {
    const { ob_high, ob_low, fvg_top, fvg_bottom, entry_price, stop_loss, target } = strategyData;

    // Order Block zone
    if (ob_high && ob_low) {
      const obTopSeries = chart.addLineSeries({
        color: 'rgba(59, 130, 246, 0.3)',
        lineWidth: 1,
        lineStyle: 0,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'OB High'
      });
      const obBottomSeries = chart.addLineSeries({
        color: 'rgba(59, 130, 246, 0.3)',
        lineWidth: 1,
        lineStyle: 0,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'OB Low'
      });
      const obData = bars.slice(-30).map(b => ({
        time: b.timestamp / 1000,
        value: ob_high
      }));
      const obLowData = bars.slice(-30).map(b => ({
        time: b.timestamp / 1000,
        value: ob_low
      }));
      obTopSeries.setData(obData);
      obBottomSeries.setData(obLowData);
      overlaysRef.current.push(obTopSeries, obBottomSeries);
    }

    // FVG zone
    if (fvg_top && fvg_bottom) {
      const fvgSeries = chart.addLineSeries({
        color: 'rgba(245, 166, 35, 0.2)',
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'FVG'
      });
      const fvgData = bars.slice(-30).map(b => ({
        time: b.timestamp / 1000,
        value: (fvg_top + fvg_bottom) / 2
      }));
      fvgSeries.setData(fvgData);
      overlaysRef.current.push(fvgSeries);
    }

    // Entry + SL + Target
    drawLevels(entry_price, stop_loss, target, strategyData.signal_type);
  };

  // AMDS: EMA + Accumulation zones
  const drawAMDSOverlay = () => {
    const { ema_20, accumulation_zone_low, accumulation_zone_high, entry_price, stop_loss, target } = strategyData;

    // EMA-20
    if (ema_20 && Array.isArray(ema_20)) {
      const emaSeries = chart.addLineSeries({
        color: '#A855F7',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'EMA-20'
      });
      const emaData = ema_20.map((val, i) => ({
        time: bars[i]?.timestamp / 1000,
        value: val
      })).filter(d => d.time && d.value);
      emaSeries.setData(emaData);
      overlaysRef.current.push(emaSeries);
    }

    // Accumulation zone
    if (accumulation_zone_low && accumulation_zone_high) {
      const accSeries = chart.addLineSeries({
        color: 'rgba(0, 230, 118, 0.2)',
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'Accumulation'
      });
      const accData = bars.slice(-40).map(b => ({
        time: b.timestamp / 1000,
        value: (accumulation_zone_low + accumulation_zone_high) / 2
      }));
      accSeries.setData(accData);
      overlaysRef.current.push(accSeries);
    }

    drawLevels(entry_price, stop_loss, target, strategyData.signal_type);
  };

  // Reverse Swings: Bollinger Bands + extremes
  const drawReverseSwingsOverlay = () => {
    const { bb_upper, bb_lower, bb_middle, entry_price, stop_loss, target } = strategyData;

    // BB Upper
    if (bb_upper && Array.isArray(bb_upper)) {
      const bbUpperSeries = chart.addLineSeries({
        color: 'rgba(255, 51, 51, 0.4)',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'BB Upper'
      });
      const upperData = bb_upper.map((val, i) => ({
        time: bars[i]?.timestamp / 1000,
        value: val
      })).filter(d => d.time && d.value);
      bbUpperSeries.setData(upperData);
      overlaysRef.current.push(bbUpperSeries);
    }

    // BB Lower
    if (bb_lower && Array.isArray(bb_lower)) {
      const bbLowerSeries = chart.addLineSeries({
        color: 'rgba(0, 230, 118, 0.4)',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'BB Lower'
      });
      const lowerData = bb_lower.map((val, i) => ({
        time: bars[i]?.timestamp / 1000,
        value: val
      })).filter(d => d.time && d.value);
      bbLowerSeries.setData(lowerData);
      overlaysRef.current.push(bbLowerSeries);
    }

    // BB Middle
    if (bb_middle && Array.isArray(bb_middle)) {
      const bbMidSeries = chart.addLineSeries({
        color: 'rgba(168, 85, 247, 0.3)',
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'BB Mid'
      });
      const midData = bb_middle.map((val, i) => ({
        time: bars[i]?.timestamp / 1000,
        value: val
      })).filter(d => d.time && d.value);
      bbMidSeries.setData(midData);
      overlaysRef.current.push(bbMidSeries);
    }

    drawLevels(entry_price, stop_loss, target, strategyData.signal_type);
  };

  // Godzilla: Breakout levels
  const drawGodzillaOverlay = () => {
    const { local_high, local_low, entry_price, stop_loss, target } = strategyData;

    // Local High
    if (local_high) {
      const highSeries = chart.addLineSeries({
        color: 'rgba(255, 51, 51, 0.5)',
        lineWidth: 2,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'Resistance'
      });
      const highData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: local_high
      }));
      highSeries.setData(highData);
      overlaysRef.current.push(highSeries);
    }

    // Local Low
    if (local_low) {
      const lowSeries = chart.addLineSeries({
        color: 'rgba(0, 230, 118, 0.5)',
        lineWidth: 2,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'Support'
      });
      const lowData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: local_low
      }));
      lowSeries.setData(lowData);
      overlaysRef.current.push(lowSeries);
    }

    drawLevels(entry_price, stop_loss, target, strategyData.signal_type);
  };

  // Narrative Swing: Score-based momentum zones
  const drawNarrativeSwingOverlay = () => {
    const { entry_price, stop_loss, target1, target2, target3, sma_90 } = strategyData;

    // SMA-90 (anchor)
    if (sma_90 && Array.isArray(sma_90)) {
      const smaSeries = chart.addLineSeries({
        color: 'rgba(168, 85, 247, 0.4)',
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'SMA-90'
      });
      const smaData = sma_90.map((val, i) => ({
        time: bars[i]?.timestamp / 1000,
        value: val
      })).filter(d => d.time && d.value);
      smaSeries.setData(smaData);
      overlaysRef.current.push(smaSeries);
    }

    // Entry + SL
    drawLevels(entry_price, stop_loss, target1, strategyData.signal_type);

    // T2 and T3
    if (target2) {
      const t2Series = chart.addLineSeries({
        color: '#3B82F6',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'T2'
      });
      const t2Data = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: target2
      }));
      t2Series.setData(t2Data);
      overlaysRef.current.push(t2Series);
    }

    if (target3) {
      const t3Series = chart.addLineSeries({
        color: '#A855F7',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'T3'
      });
      const t3Data = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: target3
      }));
      t3Series.setData(t3Data);
      overlaysRef.current.push(t3Series);
    }
  };

  // Helper: Draw Entry, SL, Target levels
  const drawLevels = (entry, sl, target, signalType) => {
    if (entry) {
      const entrySeries = chart.addLineSeries({
        color: signalType === 'BUY' ? '#00E676' : '#FF3333',
        lineWidth: 2,
        lineStyle: 0,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'Entry'
      });
      const entryData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: entry
      }));
      entrySeries.setData(entryData);
      overlaysRef.current.push(entrySeries);

      // Add marker at latest bar
      if (bars.length > 0) {
        const lastBar = bars[bars.length - 1];
        const markers = [{
          time: lastBar.timestamp / 1000,
          position: signalType === 'BUY' ? 'belowBar' : 'aboveBar',
          color: signalType === 'BUY' ? '#00E676' : '#FF3333',
          shape: signalType === 'BUY' ? 'arrowUp' : 'arrowDown',
          text: signalType,
          size: 1
        }];
        chart.candlestickSeries?.setMarkers?.(markers);
      }
    }

    if (sl) {
      const slSeries = chart.addLineSeries({
        color: '#FF0055',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'SL'
      });
      const slData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: sl
      }));
      slSeries.setData(slData);
      overlaysRef.current.push(slSeries);
    }

    if (target) {
      const targetSeries = chart.addLineSeries({
        color: '#3B82F6',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'Target'
      });
      const targetData = bars.slice(-50).map(b => ({
        time: b.timestamp / 1000,
        value: target
      }));
      targetSeries.setData(targetData);
      overlaysRef.current.push(targetSeries);
    }
  };

  return null; // No DOM rendering, only chart overlays
};

export default StrategyOverlay;
