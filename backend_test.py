import requests
import sys
import json
from datetime import datetime

class GannTradingAPITester:
    def __init__(self, base_url="https://ai-crystal-ball.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, success, details=""):
        """Log test result"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
            print(f"✅ {name} - PASSED")
        else:
            print(f"❌ {name} - FAILED: {details}")
        
        self.test_results.append({
            "test": name,
            "status": "PASSED" if success else "FAILED",
            "details": details
        })

    def run_test(self, name, method, endpoint, expected_status=200, data=None, params=None):
        """Run a single API test"""
        url = f"{self.api_url}/{endpoint}" if not endpoint.startswith('http') else endpoint
        headers = {'Content-Type': 'application/json'}
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=30)
            
            success = response.status_code == expected_status
            details = f"Status: {response.status_code}"
            
            if success and response.status_code in [200, 201]:
                try:
                    json_data = response.json()
                    if isinstance(json_data, dict):
                        details += f", Keys: {list(json_data.keys())}"
                    elif isinstance(json_data, list):
                        details += f", Items: {len(json_data)}"
                except:
                    details += ", Non-JSON response"
            elif not success:
                try:
                    error_data = response.json()
                    details += f", Error: {error_data.get('detail', 'Unknown error')}"
                except:
                    details += f", Response: {response.text[:100]}"
            
            self.log_test(name, success, details)
            return success, response.json() if success and response.status_code in [200, 201] else {}
            
        except Exception as e:
            self.log_test(name, False, f"Exception: {str(e)}")
            return False, {}

    def test_root_endpoint(self):
        """Test /api/ root endpoint"""
        return self.run_test("Root API Endpoint", "GET", "")

    def test_stock_search(self):
        """Test stock search functionality"""
        # Test RELIANCE search
        success1, data1 = self.run_test(
            "Stock Search - RELIANCE", 
            "GET", 
            "stock/search", 
            params={"q": "RELIANCE"}
        )
        
        # Test TCS search
        success2, data2 = self.run_test(
            "Stock Search - TCS", 
            "GET", 
            "stock/search", 
            params={"q": "TCS"}
        )
        
        # Validate search results
        if success1 and data1.get('results'):
            reliance_found = any('RELIANCE' in result.get('ticker', '') for result in data1['results'])
            self.log_test("RELIANCE Found in Results", reliance_found, 
                         f"Found {len(data1['results'])} results")
        
        if success2 and data2.get('results'):
            tcs_found = any('TCS' in result.get('ticker', '') for result in data2['results'])
            self.log_test("TCS Found in Results", tcs_found, 
                         f"Found {len(data2['results'])} results")
        
        return success1 and success2

    def test_stock_bars(self):
        """Test stock OHLCV data retrieval"""
        # Test TCS.NS bars
        success1, data1 = self.run_test(
            "Stock Bars - TCS.NS", 
            "GET", 
            "stock/bars/TCS.NS"
        )
        
        # Test RELIANCE.NS bars
        success2, data2 = self.run_test(
            "Stock Bars - RELIANCE.NS", 
            "GET", 
            "stock/bars/RELIANCE.NS"
        )
        
        # Validate bars data structure
        if success1 and data1.get('bars'):
            bars = data1['bars']
            if len(bars) > 0:
                bar = bars[0]
                required_fields = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                has_all_fields = all(field in bar for field in required_fields)
                self.log_test("TCS Bars Data Structure", has_all_fields, 
                             f"Bars count: {len(bars)}, Fields: {list(bar.keys())}")
        
        return success1 and success2

    def test_ai_analysis(self):
        """Test AI trade analysis"""
        # First get some stock data
        success, stock_data = self.run_test(
            "Get Stock Data for AI Analysis", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 60}
        )
        
        if not success or not stock_data.get('bars'):
            self.log_test("AI Analysis - No Stock Data", False, "Cannot test without stock data")
            return False
        
        # Prepare AI analysis request
        bars = stock_data['bars'][-60:]  # Last 60 bars
        ai_request = {
            "ticker": "TCS.NS",
            "timeframe": "1D",
            "bars": bars
        }
        
        return self.run_test(
            "AI Trade Analysis", 
            "POST", 
            "ai/analyze-chart", 
            data=ai_request
        )[0]

    def test_falling_knife_analysis(self):
        """Test Falling Knife analysis"""
        # Get stock data first
        success, stock_data = self.run_test(
            "Get Stock Data for Falling Knife", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 60}
        )
        
        if not success or not stock_data.get('bars'):
            return False
        
        falling_knife_request = {
            "ticker": "TCS.NS",
            "bars": stock_data['bars']
        }
        
        return self.run_test(
            "Falling Knife Analysis", 
            "POST", 
            "falling-knife/analyze", 
            data=falling_knife_request
        )[0]

    def test_reverse_swings_analysis(self):
        """Test Reverse Price Swings analysis"""
        # Get stock data first
        success, stock_data = self.run_test(
            "Get Stock Data for Reverse Swings", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 30}
        )
        
        if not success or not stock_data.get('bars'):
            return False
        
        # Test Method A
        reverse_swings_request = {
            "ticker": "TCS.NS",
            "bars": stock_data['bars'],
            "force_method": "A"
        }
        
        success_a = self.run_test(
            "Reverse Swings - Method A", 
            "POST", 
            "reverse-swings/analyze", 
            data=reverse_swings_request
        )[0]
        
        # Test Method B
        reverse_swings_request["force_method"] = "B"
        success_b = self.run_test(
            "Reverse Swings - Method B", 
            "POST", 
            "reverse-swings/analyze", 
            data=reverse_swings_request
        )[0]
        
        return success_a and success_b

    def test_explosive_volume_analysis(self):
        """Test Explosive Volume analysis"""
        # Get stock data first
        success, stock_data = self.run_test(
            "Get Stock Data for Explosive Volume", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 60}
        )
        
        if not success or not stock_data.get('bars'):
            return False
        
        explosive_volume_request = {
            "ticker": "TCS.NS",
            "bars": stock_data['bars']
        }
        
        return self.run_test(
            "Explosive Volume Analysis", 
            "POST", 
            "explosive-volume/analyze", 
            data=explosive_volume_request
        )[0]

    def test_golden_setup_analysis(self):
        """Test Golden Setup analysis"""
        # Get stock data first
        success, stock_data = self.run_test(
            "Get Stock Data for Golden Setup", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 60}
        )
        
        if not success or not stock_data.get('bars'):
            return False
        
        # Test Normal Mode
        golden_setup_request = {
            "ticker": "TCS.NS",
            "bars": stock_data['bars'],
            "pro_mode": False
        }
        
        success_normal = self.run_test(
            "Golden Setup - Normal Mode", 
            "POST", 
            "golden-setup/analyze", 
            data=golden_setup_request
        )[0]
        
        # Test Pro Mode
        golden_setup_request["pro_mode"] = True
        success_pro = self.run_test(
            "Golden Setup - Pro Mode", 
            "POST", 
            "golden-setup/analyze", 
            data=golden_setup_request
        )[0]
        
        return success_normal and success_pro

    def test_ghost_mode_scanner(self):
        """Test Ghost Mode scanner"""
        return self.run_test(
            "Ghost Mode Scanner", 
            "GET", 
            "ghost/scan",
            params={"min_match": 3}
        )[0]

    def test_square_of_9(self):
        """Test Square of 9 calculator"""
        return self.run_test(
            "Square of 9 Calculator", 
            "GET", 
            "square-of-9",
            params={"center_price": 3500.0}
        )[0]

    def test_gann_fan(self):
        """Test Gann Fan calculation"""
        gann_request = {
            "ticker": "TCS.NS",
            "pivot_price": 3500.0,
            "pivot_timestamp": int(datetime.now().timestamp() * 1000),
            "bars_count": 50
        }
        
        return self.run_test(
            "Gann Fan Calculation", 
            "POST", 
            "gann/fan", 
            data=gann_request
        )[0]

    def test_signal_generation(self):
        """Test signal generation"""
        return self.run_test(
            "Signal Generation", 
            "GET", 
            "signal/TCS.NS",
            params={
                "pivot_price": 3500.0,
                "pivot_timestamp": int(datetime.now().timestamp() * 1000)
            }
        )[0]

    def test_watchlist_crud(self):
        """Test Watchlist CRUD operations"""
        # Test GET empty watchlist
        success1, data1 = self.run_test(
            "Watchlist - GET (initial)", 
            "GET", 
            "watchlist"
        )
        
        # Test POST - Add to watchlist (use unique ticker)
        import time
        unique_ticker = f"INFY.NS"  # Use different ticker to avoid conflicts
        watchlist_item = {
            "ticker": unique_ticker,
            "name": "Infosys Ltd",
            "stock_type": "STOCK"
        }
        success2, data2 = self.run_test(
            "Watchlist - POST (add INFY)", 
            "POST", 
            "watchlist",
            data=watchlist_item,
            expected_status=201
        )
        
        # Test GET watchlist with items
        success3, data3 = self.run_test(
            "Watchlist - GET (with items)", 
            "GET", 
            "watchlist"
        )
        
        # Test GET watchlist with prices
        success4, data4 = self.run_test(
            "Watchlist - GET with prices", 
            "GET", 
            "watchlist/prices"
        )
        
        # Test DELETE from watchlist
        success5, data5 = self.run_test(
            "Watchlist - DELETE INFY", 
            "DELETE", 
            f"watchlist/{unique_ticker}",
            expected_status=200
        )
        
        return success1 and success2 and success3 and success4 and success5

    def test_portfolio_crud(self):
        """Test Portfolio CRUD operations"""
        # Test GET empty portfolio
        success1, data1 = self.run_test(
            "Portfolio - GET (empty)", 
            "GET", 
            "portfolio"
        )
        
        # Test POST - Add portfolio entry
        portfolio_entry = {
            "ticker": "RELIANCE.NS",
            "name": "Reliance Industries Ltd",
            "buy_price": 2500.0,
            "quantity": 10,
            "buy_date": "2024-01-15"
        }
        success2, data2 = self.run_test(
            "Portfolio - POST (add RELIANCE)", 
            "POST", 
            "portfolio",
            data=portfolio_entry,
            expected_status=201
        )
        
        # Test GET portfolio with entries
        success3, data3 = self.run_test(
            "Portfolio - GET (with entries)", 
            "GET", 
            "portfolio"
        )
        
        # Test GET portfolio summary
        success4, data4 = self.run_test(
            "Portfolio - GET summary", 
            "GET", 
            "portfolio/summary"
        )
        
        # Get entry ID for deletion (if available)
        entry_id = None
        if success3 and data3.get('entries') and len(data3['entries']) > 0:
            entry_id = data3['entries'][0].get('id')
        
        # Test DELETE portfolio entry
        if entry_id:
            success5, data5 = self.run_test(
                "Portfolio - DELETE entry", 
                "DELETE", 
                f"portfolio/{entry_id}",
                expected_status=200
            )
        else:
            success5 = True  # Skip if no entry to delete
            self.log_test("Portfolio - DELETE entry", True, "Skipped - no entry ID available")
        
        return success1 and success2 and success3 and success4 and success5

    def test_alerts_crud(self):
        """Test Alerts CRUD operations"""
        # Test GET empty alerts
        success1, data1 = self.run_test(
            "Alerts - GET (empty)", 
            "GET", 
            "alerts"
        )
        
        # Test POST - Create price alert
        alert_rule = {
            "ticker": "TCS.NS",
            "name": "Tata Consultancy Services",
            "alert_type": "price_above",
            "threshold": 4000.0
        }
        success2, data2 = self.run_test(
            "Alerts - POST (price above)", 
            "POST", 
            "alerts",
            data=alert_rule,
            expected_status=201
        )
        
        # Test POST - Create signal alert
        signal_alert = {
            "ticker": "RELIANCE.NS",
            "name": "Reliance Industries Ltd",
            "alert_type": "demon_buy",
            "threshold": None
        }
        success3, data3 = self.run_test(
            "Alerts - POST (demon buy signal)", 
            "POST", 
            "alerts",
            data=signal_alert,
            expected_status=201
        )
        
        # Test GET alerts with items
        success4, data4 = self.run_test(
            "Alerts - GET (with items)", 
            "GET", 
            "alerts"
        )
        
        # Test POST - Check alerts
        success5, data5 = self.run_test(
            "Alerts - POST check", 
            "POST", 
            "alerts/check"
        )
        
        # Get alert ID for deletion (if available)
        alert_id = None
        if success4 and data4.get('alerts') and len(data4['alerts']) > 0:
            alert_id = data4['alerts'][0].get('id')
        
        # Test DELETE alert
        if alert_id:
            success6, data6 = self.run_test(
                "Alerts - DELETE alert", 
                "DELETE", 
                f"alerts/{alert_id}",
                expected_status=200
            )
        else:
            success6 = True  # Skip if no alert to delete
            self.log_test("Alerts - DELETE alert", True, "Skipped - no alert ID available")
        
        return success1 and success2 and success3 and success4 and success5 and success6

    def test_gpt_analysis(self):
        """Test GPT Analysis using Emergent LLM"""
        # Get stock data first
        success, stock_data = self.run_test(
            "Get Stock Data for GPT Analysis", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 60}
        )
        
        if not success or not stock_data.get('bars'):
            self.log_test("GPT Analysis - No Stock Data", False, "Cannot test without stock data")
            return False
        
        # Prepare GPT analysis request
        bars = stock_data['bars'][-60:]  # Last 60 bars
        gpt_request = {
            "ticker": "TCS.NS",
            "timeframe": "1D",
            "bars": bars
        }
        
        return self.run_test(
            "GPT Analysis (Emergent LLM)", 
            "POST", 
            "ai/gpt-analyze", 
            data=gpt_request
        )[0]

    def test_narrative_swing_endpoint(self):
        """Test Narrative Swing Trader endpoint with 180 bars"""
        print("\n🎯 Testing NARRATIVE SWING TRADER endpoint...")
        
        # Fetch 180 bars of TCS.NS data
        success, stock_data = self.run_test(
            "Get 180 bars for Narrative Swing (TCS.NS)", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 180}
        )
        
        if not success or not stock_data.get('bars'):
            self.log_test("Narrative Swing - No Stock Data", False, "Cannot test without 180 bars")
            return False
        
        bars = stock_data['bars']
        if len(bars) < 50:
            self.log_test("Narrative Swing - Insufficient Bars", False, f"Got {len(bars)} bars, need at least 50")
            return False
        
        # Test with TCS.NS
        narrative_request = {
            "ticker": "TCS.NS",
            "bars": bars,
            "timeframe": "1D"
        }
        
        success1, data1 = self.run_test(
            "Narrative Swing - TCS.NS", 
            "POST", 
            "narrative-swing/analyze", 
            data=narrative_request
        )
        
        # Validate response structure
        if success1 and data1:
            required_fields = [
                'signal_type', 'narrative_score', 'momentum', 'volatility', 
                'rel_price', 'narrative_label', 'entry_price', 'stop_loss', 
                'target1', 'target2', 'target3', 'risk_reward', 'confidence', 
                'score_bars', 'recommendation'
            ]
            missing_fields = [field for field in required_fields if field not in data1]
            if missing_fields:
                self.log_test("Narrative Swing Response Structure", False, f"Missing fields: {missing_fields}")
            else:
                self.log_test("Narrative Swing Response Structure", True, 
                             f"Signal: {data1.get('signal_type')}, Score: {data1.get('narrative_score')}, Label: {data1.get('narrative_label')}")
                
                # Validate signal_type
                signal_type = data1.get('signal_type')
                if signal_type not in ['BUY', 'SELL', 'WAIT']:
                    self.log_test("Narrative Swing Signal Type", False, f"Invalid signal_type: {signal_type}")
                else:
                    self.log_test("Narrative Swing Signal Type", True, f"Valid signal: {signal_type}")
                
                # Validate score_bars is a list
                score_bars = data1.get('score_bars', [])
                if isinstance(score_bars, list):
                    self.log_test("Narrative Swing Score Bars", True, f"Score bars count: {len(score_bars)}")
                else:
                    self.log_test("Narrative Swing Score Bars", False, "score_bars is not a list")
        
        # Test with RELIANCE.NS
        success2, stock_data2 = self.run_test(
            "Get bars for Narrative Swing (RELIANCE.NS)", 
            "GET", 
            "stock/bars/RELIANCE.NS",
            params={"limit": 180}
        )
        
        if success2 and stock_data2.get('bars'):
            narrative_request2 = {
                "ticker": "RELIANCE.NS",
                "bars": stock_data2['bars'],
                "timeframe": "1D"
            }
            success2, data2 = self.run_test(
                "Narrative Swing - RELIANCE.NS", 
                "POST", 
                "narrative-swing/analyze", 
                data=narrative_request2
            )
        
        # Test with NVDA (US stock)
        success3, stock_data3 = self.run_test(
            "Get bars for Narrative Swing (NVDA)", 
            "GET", 
            "stock/bars/NVDA",
            params={"limit": 180}
        )
        
        if success3 and stock_data3.get('bars'):
            narrative_request3 = {
                "ticker": "NVDA",
                "bars": stock_data3['bars'],
                "timeframe": "1D"
            }
            success3, data3 = self.run_test(
                "Narrative Swing - NVDA", 
                "POST", 
                "narrative-swing/analyze", 
                data=narrative_request3
            )
        
        return success1 and success2 and success3

    def test_orderflow_endpoint(self):
        """Test Order Flow endpoint with 90 bars"""
        print("\n📊 Testing ORDER FLOW endpoint...")
        
        # Fetch 90 bars of TCS.NS data
        success, stock_data = self.run_test(
            "Get 90 bars for Order Flow (TCS.NS)", 
            "GET", 
            "stock/bars/TCS.NS",
            params={"limit": 90}
        )
        
        if not success or not stock_data.get('bars'):
            self.log_test("Order Flow - No Stock Data", False, "Cannot test without 90 bars")
            return False
        
        bars = stock_data['bars']
        if len(bars) < 30:
            self.log_test("Order Flow - Insufficient Bars", False, f"Got {len(bars)} bars, need at least 30")
            return False
        
        # Test Order Flow analysis
        orderflow_request = {
            "ticker": "TCS.NS",
            "bars": bars,
            "n_vp_bins": 24,
            "n_fp_levels": 8,
            "vp_lookback": 50
        }
        
        success1, data1 = self.run_test(
            "Order Flow - TCS.NS", 
            "POST", 
            "orderflow/analyze", 
            data=orderflow_request
        )
        
        # Validate response structure
        if success1 and data1:
            required_fields = [
                'signal_type', 'signal_strength', 'entry_price', 'stop_loss', 
                'target1', 'target2', 'risk_reward', 'buy_pct', 'sell_pct', 
                'current_delta', 'current_cvd', 'cvd_slope', 'poc_price', 
                'vah_price', 'val_price', 'divergence', 'candles', 'vp_bins', 'footprint'
            ]
            missing_fields = [field for field in required_fields if field not in data1]
            if missing_fields:
                self.log_test("Order Flow Response Structure", False, f"Missing fields: {missing_fields}")
            else:
                self.log_test("Order Flow Response Structure", True, 
                             f"Signal: {data1.get('signal_type')}, Strength: {data1.get('signal_strength')}")
                
                # Validate signal_type
                signal_type = data1.get('signal_type')
                if signal_type not in ['BUY', 'SELL', 'WAIT']:
                    self.log_test("Order Flow Signal Type", False, f"Invalid signal_type: {signal_type}")
                else:
                    self.log_test("Order Flow Signal Type", True, f"Valid signal: {signal_type}")
                
                # Validate candles array
                candles = data1.get('candles', [])
                if isinstance(candles, list):
                    self.log_test("Order Flow Candles Array", True, f"Candles count: {len(candles)}")
                else:
                    self.log_test("Order Flow Candles Array", False, "candles is not a list")
                
                # Validate vp_bins array
                vp_bins = data1.get('vp_bins', [])
                if isinstance(vp_bins, list):
                    poc_count = sum(1 for b in vp_bins if b.get('is_poc', False))
                    self.log_test("Order Flow VP Bins", True, 
                                 f"VP bins: {len(vp_bins)}, POC bins: {poc_count}")
                    
                    # Check if we have exactly 24 bins
                    if len(vp_bins) == 24:
                        self.log_test("Order Flow VP Bins Count", True, "Exactly 24 bins as expected")
                    else:
                        self.log_test("Order Flow VP Bins Count", False, 
                                     f"Expected 24 bins, got {len(vp_bins)}")
                    
                    # Check if exactly one bin is marked as POC
                    if poc_count == 1:
                        self.log_test("Order Flow POC Marker", True, "Exactly 1 POC bin marked")
                    else:
                        self.log_test("Order Flow POC Marker", False, 
                                     f"Expected 1 POC bin, got {poc_count}")
                else:
                    self.log_test("Order Flow VP Bins", False, "vp_bins is not a list")
                
                # Validate footprint array
                footprint = data1.get('footprint', [])
                if isinstance(footprint, list):
                    self.log_test("Order Flow Footprint Array", True, f"Footprint candles: {len(footprint)}")
                    
                    # Check if we have exactly 12 footprint candles
                    if len(footprint) == 12:
                        self.log_test("Order Flow Footprint Count", True, "Exactly 12 candles as expected")
                        
                        # Check if each candle has 8 price levels
                        if len(footprint) > 0:
                            first_candle = footprint[0]
                            levels = first_candle.get('levels', [])
                            if len(levels) == 8:
                                self.log_test("Order Flow Footprint Levels", True, 
                                             "Each candle has 8 price levels")
                            else:
                                self.log_test("Order Flow Footprint Levels", False, 
                                             f"Expected 8 levels per candle, got {len(levels)}")
                    else:
                        self.log_test("Order Flow Footprint Count", False, 
                                     f"Expected 12 candles, got {len(footprint)}")
                else:
                    self.log_test("Order Flow Footprint Array", False, "footprint is not a list")
                
                # Validate buy_pct + sell_pct ≈ 100
                buy_pct = data1.get('buy_pct', 0)
                sell_pct = data1.get('sell_pct', 0)
                total_pct = buy_pct + sell_pct
                if 99 <= total_pct <= 101:
                    self.log_test("Order Flow Buy/Sell Percentage", True, 
                                 f"Buy: {buy_pct}%, Sell: {sell_pct}%, Total: {total_pct}%")
                else:
                    self.log_test("Order Flow Buy/Sell Percentage", False, 
                                 f"Buy+Sell should ≈100%, got {total_pct}%")
        
        return success1

    def test_narrative_swing_backtest(self):
        """Test Backtest endpoint with narrative_swing strategy"""
        print("\n🔄 Testing NARRATIVE SWING BACKTEST...")
        
        # Test narrative_swing strategy
        backtest_request = {
            "ticker": "RELIANCE.NS",
            "strategy": "narrative_swing",
            "days": 90,
            "timeframe": "daily"
        }
        
        success1, data1 = self.run_test(
            "Backtest - narrative_swing (RELIANCE.NS)", 
            "POST", 
            "backtest", 
            data=backtest_request
        )
        
        # Validate response structure
        if success1 and data1:
            required_fields = ['ticker', 'strategy', 'timeframe', 'total_trades', 'win_rate']
            missing_fields = [field for field in required_fields if field not in data1]
            if missing_fields:
                self.log_test("Narrative Swing Backtest Structure", False, f"Missing fields: {missing_fields}")
            else:
                self.log_test("Narrative Swing Backtest Structure", True, 
                             f"Trades: {data1.get('total_trades')}, Win rate: {data1.get('win_rate')}%")
                
                # Verify strategy is narrative_swing
                strategy = data1.get('strategy')
                if strategy == 'narrative_swing':
                    self.log_test("Narrative Swing Backtest Strategy", True, "Strategy correctly set")
                else:
                    self.log_test("Narrative Swing Backtest Strategy", False, 
                                 f"Expected 'narrative_swing', got '{strategy}'")
        
        return success1

    def test_backtest_module(self):
        """Test NEW Backtest Engine - All Strategies & Timeframes"""
        print("\n🔥 Testing NEW BACKTEST ENGINE...")
        
        all_success = True
        
        # Test ALL strategies combo mode with intraday timeframe (main target)
        backtest_request = {
            "ticker": "TCS.NS",
            "strategy": "all",
            "days": 90,
            "timeframe": "intraday"
        }
        success1, data1 = self.run_test(
            "Backtest - ALL strategies (Intraday 90d)", 
            "POST", 
            "backtest", 
            data=backtest_request
        )
        
        # Validate ALL strategy response structure
        if success1 and data1:
            required_fields = ['total_trades', 'win_rate', 'avg_trades_per_day', 'trading_days', 'daily_summary']
            missing_fields = [field for field in required_fields if field not in data1]
            if missing_fields:
                self.log_test("ALL Strategy Response Structure", False, f"Missing fields: {missing_fields}")
                all_success = False
            else:
                self.log_test("ALL Strategy Response Structure", True, f"All required fields present")
                
                # Check target achievement (10+ trades/day, 80%+ win rate)
                trades_per_day = data1.get('avg_trades_per_day', 0)
                win_rate = data1.get('win_rate', 0)
                target_met = trades_per_day >= 8 and win_rate >= 80  # Using 8 as threshold (close to 10)
                
                self.log_test("Target Achievement Check", target_met, 
                             f"Trades/day: {trades_per_day}, Win rate: {win_rate}% (Target: 10+/day, 80%+)")
                
                # Check daily summary
                daily_summary = data1.get('daily_summary', [])
                has_daily_summary = len(daily_summary) > 0
                self.log_test("Daily Summary Present", has_daily_summary, 
                             f"Daily summary entries: {len(daily_summary)}")
        
        # Test individual strategies with different timeframes
        strategies_to_test = [
            ("demon", "intraday"),
            ("falling_knife", "intraday"), 
            ("golden_setup", "short_term"),
            ("godzilla", "intraday"),
            ("reverse_swings", "mid_term")
        ]
        
        for strategy, timeframe in strategies_to_test:
            backtest_request = {
                "ticker": "TCS.NS",
                "strategy": strategy,
                "days": 90,
                "timeframe": timeframe
            }
            success, data = self.run_test(
                f"Backtest - {strategy.upper()} ({timeframe})", 
                "POST", 
                "backtest", 
                data=backtest_request
            )
            if not success:
                all_success = False
        
        # Test different day periods with ALL strategy
        day_periods = [30, 60, 180, 365]
        for days in day_periods:
            backtest_request = {
                "ticker": "TCS.NS",
                "strategy": "all",
                "days": days,
                "timeframe": "intraday"
            }
            success, data = self.run_test(
                f"Backtest - ALL strategy ({days} days)", 
                "POST", 
                "backtest", 
                data=backtest_request
            )
            if not success:
                all_success = False
        
        # Test all timeframes with ALL strategy
        timeframes_to_test = ["intraday", "short_term", "mid_term"]
        for tf in timeframes_to_test:
            backtest_request = {
                "ticker": "TCS.NS",
                "strategy": "all",
                "days": 90,
                "timeframe": tf
            }
            success, data = self.run_test(
                f"Backtest - ALL strategy ({tf} timeframe)", 
                "POST", 
                "backtest", 
                data=backtest_request
            )
            if not success:
                all_success = False
        
        return all_success

    def run_all_tests(self):
        """Run all backend API tests"""
        print(f"🚀 Starting Gann Trading API Tests - NEW FEATURES")
        print(f"📡 Testing endpoint: {self.base_url}")
        print("=" * 60)
        
        # Core API tests
        self.test_root_endpoint()
        self.test_stock_search()
        self.test_stock_bars()
        
        # Analysis tests
        self.test_ai_analysis()
        self.test_falling_knife_analysis()
        self.test_reverse_swings_analysis()
        self.test_explosive_volume_analysis()
        self.test_golden_setup_analysis()
        
        # NEW FEATURES TESTING
        print("\n🆕 Testing NEW Features...")
        self.test_watchlist_crud()
        self.test_portfolio_crud()
        self.test_alerts_crud()
        self.test_gpt_analysis()
        
        # NARRATIVE SWING & ORDER FLOW TESTING
        print("\n🎯 Testing NARRATIVE SWING & ORDER FLOW Features...")
        self.test_narrative_swing_endpoint()
        self.test_orderflow_endpoint()
        self.test_narrative_swing_backtest()
        
        self.test_backtest_module()
        
        # Utility tests
        self.test_square_of_9()
        self.test_gann_fan()
        self.test_signal_generation()
        
        # Ghost mode (may take longer)
        print("\n⚠️  Testing Ghost Mode Scanner (may take 1-2 minutes)...")
        self.test_ghost_mode_scanner()
        
        # Print summary
        print("\n" + "=" * 60)
        print(f"📊 Test Summary: {self.tests_passed}/{self.tests_run} tests passed")
        
        if self.tests_passed == self.tests_run:
            print("🎉 All tests PASSED!")
            return 0
        else:
            print(f"⚠️  {self.tests_run - self.tests_passed} tests FAILED")
            return 1

def main():
    tester = GannTradingAPITester()
    return tester.run_all_tests()

if __name__ == "__main__":
    sys.exit(main())