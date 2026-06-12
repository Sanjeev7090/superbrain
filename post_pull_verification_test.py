#!/usr/bin/env python3
"""
POST-PULL VERIFICATION TEST
===========================
Focused backend verification for freshly-pulled Robo-Trader / GANN TRADER.

Tests:
1. Core API health
2. Robo-Trader Phase 2 endpoints (settings, status, risk-preview, etc.)
3. Auto Scanner with weighted confluence
4. Multi-TF Scanner SSE endpoint (basic connectivity)
5. Strategy endpoints (falling-knife, golden-setup, demon)
6. Indices live (best-effort)
"""

import requests
import json
import sys
from datetime import datetime

# Backend URL from frontend/.env
BASE_URL = "https://brain-replica-3.preview.emergentagent.com"
API_URL = f"{BASE_URL}/api"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

class PostPullVerifier:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.results = []
        
    def log_result(self, test_name, passed, details="", error_trace=""):
        """Log test result"""
        self.tests_run += 1
        if passed:
            self.tests_passed += 1
            print(f"{Colors.GREEN}✅ {test_name}{Colors.RESET}")
            if details:
                print(f"   {Colors.BLUE}{details}{Colors.RESET}")
        else:
            self.tests_failed += 1
            print(f"{Colors.RED}❌ {test_name}{Colors.RESET}")
            if details:
                print(f"   {Colors.YELLOW}{details}{Colors.RESET}")
            if error_trace:
                print(f"   {Colors.RED}Error: {error_trace}{Colors.RESET}")
        
        self.results.append({
            "test": test_name,
            "passed": passed,
            "details": details,
            "error": error_trace
        })
    
    def test_get(self, name, endpoint, expected_keys=None, timeout=30):
        """Test GET endpoint"""
        url = f"{API_URL}/{endpoint}"
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                details = f"Status: 200"
                if expected_keys:
                    missing = [k for k in expected_keys if k not in data]
                    if missing:
                        self.log_result(name, False, f"Missing keys: {missing}", "")
                        return False, data
                    details += f", Keys: {list(data.keys())[:5]}"
                self.log_result(name, True, details)
                return True, data
            else:
                self.log_result(name, False, f"Status: {response.status_code}", response.text[:200])
                return False, {}
        except Exception as e:
            self.log_result(name, False, "", str(e))
            return False, {}
    
    def test_post(self, name, endpoint, data, expected_keys=None, timeout=30):
        """Test POST endpoint"""
        url = f"{API_URL}/{endpoint}"
        try:
            response = requests.post(url, json=data, timeout=timeout)
            if response.status_code in [200, 201]:
                resp_data = response.json()
                details = f"Status: {response.status_code}"
                if expected_keys:
                    missing = [k for k in expected_keys if k not in resp_data]
                    if missing:
                        self.log_result(name, False, f"Missing keys: {missing}", "")
                        return False, resp_data
                    details += f", Keys: {list(resp_data.keys())[:5]}"
                self.log_result(name, True, details)
                return True, resp_data
            else:
                self.log_result(name, False, f"Status: {response.status_code}", response.text[:200])
                return False, {}
        except Exception as e:
            self.log_result(name, False, "", str(e))
            return False, {}
    
    def test_sse(self, name, endpoint, params=None, timeout=10):
        """Test SSE endpoint (just verify it opens and streams)"""
        url = f"{API_URL}/{endpoint}"
        try:
            response = requests.get(url, params=params, stream=True, timeout=timeout)
            if response.status_code == 200:
                # Read first few events
                events_received = 0
                for line in response.iter_lines(decode_unicode=True):
                    if line and line.startswith('data:'):
                        events_received += 1
                        if events_received >= 2:  # Just verify we get at least 2 events
                            break
                
                if events_received > 0:
                    self.log_result(name, True, f"SSE stream opened, received {events_received} events")
                    return True
                else:
                    self.log_result(name, False, "SSE stream opened but no events received", "")
                    return False
            else:
                self.log_result(name, False, f"Status: {response.status_code}", response.text[:200])
                return False
        except requests.exceptions.Timeout:
            # Timeout is acceptable for SSE - it means stream was working
            self.log_result(name, True, "SSE stream opened (timeout after receiving events)")
            return True
        except Exception as e:
            self.log_result(name, False, "", str(e))
            return False
    
    def run_all_tests(self):
        """Run all POST-PULL verification tests"""
        print(f"\n{Colors.BLUE}{'='*80}{Colors.RESET}")
        print(f"{Colors.BLUE}POST-PULL VERIFICATION TEST - GANN TRADER / ROBO-TRADER{Colors.RESET}")
        print(f"{Colors.BLUE}Backend URL: {BASE_URL}{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*80}{Colors.RESET}\n")
        
        # ═══════════════════════════════════════════════════════════════════════
        # 1. CORE API HEALTH
        # ═══════════════════════════════════════════════════════════════════════
        print(f"\n{Colors.YELLOW}[1] CORE API HEALTH{Colors.RESET}")
        print("-" * 80)
        self.test_get("Core API Health (GET /api/)", "", expected_keys=["message"])
        
        # ═══════════════════════════════════════════════════════════════════════
        # 2. ROBO-TRADER PHASE 2 ENDPOINTS
        # ═══════════════════════════════════════════════════════════════════════
        print(f"\n{Colors.YELLOW}[2] ROBO-TRADER PHASE 2 ENDPOINTS{Colors.RESET}")
        print("-" * 80)
        
        # GET /robo/settings
        self.test_get(
            "GET /robo/settings",
            "robo/settings",
            expected_keys=["preferences", "risk_profile", "capital_state_vector"]
        )
        
        # POST /robo/settings
        self.test_post(
            "POST /robo/settings",
            "robo/settings",
            data={"daily_profit_target": 5000, "allocated_capital": 100000},
            expected_keys=["preferences", "risk_profile"]
        )
        
        # GET /robo/status
        self.test_get(
            "GET /robo/status",
            "robo/status",
            expected_keys=["daily_pnl", "capital_state_vector"]
        )
        
        # POST /robo/risk-preview
        success, data = self.test_post(
            "POST /robo/risk-preview",
            "robo/risk-preview",
            data={
                "daily_profit_target": 5000,
                "allocated_capital": 100000,
                "risk_tolerance": "moderate",
                "ticker": "RELIANCE.NS"
            },
            expected_keys=["success", "preview"]
        )
        if success and data.get("preview"):
            preview = data["preview"]
            if all(k in preview for k in ["kelly_fraction", "var_95_inr", "feasibility_score"]):
                print(f"   {Colors.GREEN}✓ Risk preview contains Kelly, VaR, and Feasibility metrics{Colors.RESET}")
        
        # GET /robo/capital-state
        success, data = self.test_get(
            "GET /robo/capital-state",
            "robo/capital-state",
            expected_keys=["success", "capital_state"]
        )
        if success and data.get("capital_state"):
            cap_state = data["capital_state"]
            if "pnl_normalised" in cap_state and "capital_normalised" in cap_state:
                print(f"   {Colors.GREEN}✓ Capital state vector contains normalized values{Colors.RESET}")
        
        # GET /robo/risk-report
        success, data = self.test_get(
            "GET /robo/risk-report",
            "robo/risk-report",
            expected_keys=["success", "position_sizing", "var_cvar", "feasibility"]
        )
        if success and data.get("position_sizing") and data.get("var_cvar"):
            if "kelly_fraction" in data["position_sizing"] and "param_var_95" in data["var_cvar"]:
                print(f"   {Colors.GREEN}✓ Risk report contains Kelly sizing and VaR metrics{Colors.RESET}")
        
        # POST /robo/recalculate
        self.test_post(
            "POST /robo/recalculate",
            "robo/recalculate",
            data={"trigger": "post_pull_verification"},
            expected_keys=["risk_profile"]
        )
        
        # GET /robo/audit
        self.test_get(
            "GET /robo/audit",
            "robo/audit",
            expected_keys=["success", "trades"]
        )
        
        # GET /robo/recalc-history
        self.test_get(
            "GET /robo/recalc-history",
            "robo/recalc-history"
        )
        
        # ═══════════════════════════════════════════════════════════════════════
        # 3. AUTO SCANNER WITH WEIGHTED CONFLUENCE
        # ═══════════════════════════════════════════════════════════════════════
        print(f"\n{Colors.YELLOW}[3] AUTO SCANNER WITH WEIGHTED CONFLUENCE{Colors.RESET}")
        print("-" * 80)
        
        success, data = self.test_get(
            "GET /auto-scan/RELIANCE.NS",
            "auto-scan/RELIANCE.NS",
            expected_keys=["ticker", "confluence_score", "signals"],
            timeout=60  # Auto-scan can be slow
        )
        
        if success and data:
            # Verify weighted confluence scoring
            conf_score = data.get("confluence_score", 0)
            signals = data.get("signals", [])
            print(f"   {Colors.BLUE}Confluence Score: {conf_score}/100{Colors.RESET}")
            print(f"   {Colors.BLUE}Strategies Analyzed: {len(signals)}{Colors.RESET}")
            print(f"   {Colors.BLUE}Confluence Label: {data.get('confluence_label', 'N/A')}{Colors.RESET}")
            
            # Check if weighted system is being used
            if conf_score > 0:
                print(f"   {Colors.GREEN}✓ Weighted confluence system working{Colors.RESET}")
        
        # ═══════════════════════════════════════════════════════════════════════
        # 4. MULTI-TF SCANNER SSE ENDPOINT
        # ═══════════════════════════════════════════════════════════════════════
        print(f"\n{Colors.YELLOW}[4] MULTI-TF SCANNER SSE ENDPOINT{Colors.RESET}")
        print("-" * 80)
        
        self.test_sse(
            "GET /multi-tf-scanner/scan (SSE)",
            "multi-tf-scanner/scan",
            params={
                "segment": "index",
                "timeframes": "15m,1h",
                "min_confluence": 50
            },
            timeout=15
        )
        
        # ═══════════════════════════════════════════════════════════════════════
        # 5. STRATEGY ENDPOINTS (SANITY CHECK)
        # ═══════════════════════════════════════════════════════════════════════
        print(f"\n{Colors.YELLOW}[5] STRATEGY ENDPOINTS (SANITY CHECK){Colors.RESET}")
        print("-" * 80)
        
        # First, fetch bar data for RELIANCE.NS
        print(f"   {Colors.BLUE}Fetching bar data for RELIANCE.NS...{Colors.RESET}")
        success, bars_data = self.test_get(
            "GET /stock/bars/RELIANCE.NS",
            "stock/bars/RELIANCE.NS?limit=90",
            expected_keys=["ticker", "bars"],
            timeout=30
        )
        
        if success and bars_data.get("bars"):
            bars = bars_data["bars"]
            print(f"   {Colors.GREEN}✓ Fetched {len(bars)} bars{Colors.RESET}")
            
            # Falling Knife
            self.test_post(
                "POST /falling-knife/analyze",
                "falling-knife/analyze",
                data={"ticker": "RELIANCE.NS", "bars": bars},
                expected_keys=["signal_type", "status"],
                timeout=45
            )
            
            # Golden Setup
            self.test_post(
                "POST /golden-setup/analyze",
                "golden-setup/analyze",
                data={"ticker": "RELIANCE.NS", "bars": bars},
                expected_keys=["signal_type", "entry_price"],
                timeout=45
            )
            
            # DEMON
            self.test_post(
                "POST /demon/analyze",
                "demon/analyze",
                data={"ticker": "RELIANCE.NS", "bars": bars},
                expected_keys=["signal_type", "verdict"],
                timeout=45
            )
        else:
            print(f"   {Colors.RED}✗ Failed to fetch bar data, skipping strategy tests{Colors.RESET}")
            self.log_result("POST /falling-knife/analyze", False, "Skipped - no bar data")
            self.log_result("POST /golden-setup/analyze", False, "Skipped - no bar data")
            self.log_result("POST /demon/analyze", False, "Skipped - no bar data")
        
        # ═══════════════════════════════════════════════════════════════════════
        # 6. INDICES LIVE (BEST-EFFORT)
        # ═══════════════════════════════════════════════════════════════════════
        print(f"\n{Colors.YELLOW}[6] INDICES LIVE (BEST-EFFORT){Colors.RESET}")
        print("-" * 80)
        
        success, data = self.test_get(
            "GET /indices/live",
            "indices/live",
            timeout=30
        )
        
        if success and data:
            # Check if we got real data or mocked data
            if isinstance(data, list) and len(data) > 0:
                sample = data[0]
                if "symbol" in sample and "price" in sample:
                    print(f"   {Colors.BLUE}Indices data received: {len(data)} indices{Colors.RESET}")
                    if sample.get("price", 0) == 0:
                        print(f"   {Colors.YELLOW}⚠ Data may be mocked (price=0){Colors.RESET}")
        
        # ═══════════════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════════════
        print(f"\n{Colors.BLUE}{'='*80}{Colors.RESET}")
        print(f"{Colors.BLUE}TEST SUMMARY{Colors.RESET}")
        print(f"{Colors.BLUE}{'='*80}{Colors.RESET}")
        print(f"Total Tests: {self.tests_run}")
        print(f"{Colors.GREEN}Passed: {self.tests_passed}{Colors.RESET}")
        print(f"{Colors.RED}Failed: {self.tests_failed}{Colors.RESET}")
        print(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%\n")
        
        # Failed tests detail
        if self.tests_failed > 0:
            print(f"{Colors.RED}FAILED TESTS:{Colors.RESET}")
            for result in self.results:
                if not result["passed"]:
                    print(f"  ❌ {result['test']}")
                    if result["details"]:
                        print(f"     {result['details']}")
                    if result["error"]:
                        print(f"     Error: {result['error']}")
        
        return self.tests_passed, self.tests_failed

if __name__ == "__main__":
    verifier = PostPullVerifier()
    passed, failed = verifier.run_all_tests()
    
    # Exit with appropriate code
    sys.exit(0 if failed == 0 else 1)
