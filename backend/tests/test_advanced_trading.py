"""
Backend tests for Advanced Trading APIs (DreamerV3 Phase 2)
Tests: Circuit Breakers, Kill Switch, Observability, Sentiment, Portfolio, Smart Route, DreamerV3
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


@pytest.fixture
def api():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    return s


# ─── Risk / Circuit Breaker ────────────────────────────────────────────────────

class TestCircuitBreaker:
    """Circuit breaker and kill switch endpoints"""

    def test_circuit_status_200(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/risk/circuit-status')
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        print('PASS: GET /api/advanced/risk/circuit-status returns 200')

    def test_circuit_status_fields(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/risk/circuit-status')
        d = r.json()
        assert 'state' in d, f'Missing "state" in response: {d}'
        assert 'trading_allowed' in d, f'Missing "trading_allowed" in response: {d}'
        assert d['state'] in ('NORMAL', 'WARNING', 'TRIPPED'), f'Unexpected state: {d["state"]}'
        print(f'PASS: circuit status state={d["state"]}, trading_allowed={d["trading_allowed"]}')

    def test_circuit_status_normal_on_fresh_state(self, api):
        # After reset, state should be NORMAL (or at worst WARNING if previous tests left residue)
        api.post(f'{BASE_URL}/api/advanced/risk/reset-circuit')
        r = api.get(f'{BASE_URL}/api/advanced/risk/circuit-status')
        d = r.json()
        assert d['state'] == 'NORMAL', f'Expected NORMAL after reset, got: {d["state"]}'
        assert d['trading_allowed'] is True, f'Expected trading_allowed=True, got: {d["trading_allowed"]}'
        print(f'PASS: circuit reset → NORMAL, trading_allowed=True')

    def test_kill_switch_activate(self, api):
        # First reset any prior state
        api.post(f'{BASE_URL}/api/advanced/risk/reset-circuit')
        r = api.post(f'{BASE_URL}/api/advanced/risk/kill-switch', json={'action': 'activate', 'reason': 'Test activation'})
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        d = r.json()
        assert d.get('status') == 'activated', f'Expected status=activated, got: {d}'
        print(f'PASS: kill switch activated: {d}')

    def test_kill_switch_deactivate(self, api):
        r = api.post(f'{BASE_URL}/api/advanced/risk/kill-switch', json={'action': 'deactivate'})
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        d = r.json()
        assert d.get('status') == 'deactivated', f'Expected status=deactivated, got: {d}'
        print(f'PASS: kill switch deactivated: {d}')

    def test_reset_circuit(self, api):
        r = api.post(f'{BASE_URL}/api/advanced/risk/reset-circuit')
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        d = r.json()
        assert d.get('status') == 'reset', f'Expected status=reset, got: {d}'
        print(f'PASS: reset circuit: {d}')

    def test_trading_allowed_after_kill_deactivate(self, api):
        """After deactivate, circuit should allow trading again"""
        api.post(f'{BASE_URL}/api/advanced/risk/kill-switch', json={'action': 'deactivate'})
        api.post(f'{BASE_URL}/api/advanced/risk/reset-circuit')
        r = api.get(f'{BASE_URL}/api/advanced/risk/circuit-status')
        d = r.json()
        assert d['trading_allowed'] is True, f'Expected trading_allowed=True after deactivate, got: {d}'
        print(f'PASS: trading allowed after kill switch deactivate and circuit reset')


# ─── Observability / Trade Recording ──────────────────────────────────────────

class TestObservability:
    """Observability metrics, trade recording, alerts, Prometheus"""

    def test_record_trade_200(self, api):
        r = api.post(f'{BASE_URL}/api/advanced/observability/record-trade', json={
            'pnl_pct': 0.02, 'direction': 'BUY', 'ticker': 'TEST.NS'
        })
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        d = r.json()
        assert d.get('recorded') is True, f'Expected recorded=True, got: {d}'
        print(f'PASS: record-trade: {d}')

    def test_metrics_returns_200(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/observability/metrics')
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        print('PASS: GET /api/advanced/observability/metrics returns 200')

    def test_metrics_total_trades_gte_1(self, api):
        # Record a trade first to ensure at least 1 trade exists
        api.post(f'{BASE_URL}/api/advanced/observability/record-trade', json={
            'pnl_pct': 0.015, 'direction': 'BUY', 'ticker': 'RELIANCE.NS'
        })
        r = api.get(f'{BASE_URL}/api/advanced/observability/metrics')
        d = r.json()
        assert d.get('total_trades', 0) >= 1, f'Expected total_trades >= 1, got: {d.get("total_trades")}'
        assert 'win_rate' in d, f'Missing win_rate in metrics: {d}'
        assert 'gross_pnl' in d, f'Missing gross_pnl in metrics: {d}'
        print(f'PASS: metrics total_trades={d["total_trades"]}, win_rate={d["win_rate"]}, gross_pnl={d["gross_pnl"]}')

    def test_metrics_has_required_fields(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/observability/metrics')
        d = r.json()
        required = ['total_trades', 'win_rate', 'gross_pnl', 'max_drawdown', 'sharpe_rolling',
                    'profit_factor', 'kill_switch_active', 'circuit_state', 'trading_allowed']
        for field in required:
            assert field in d, f'Missing field "{field}" in metrics response: {d}'
        print(f'PASS: all required metrics fields present')

    def test_alerts_returns_200(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/observability/alerts')
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        d = r.json()
        assert 'alerts' in d, f'Missing "alerts" key in response: {d}'
        assert isinstance(d['alerts'], list), f'Expected alerts to be a list, got: {type(d["alerts"])}'
        print(f'PASS: alerts endpoint returns alerts array with {len(d["alerts"])} items')

    def test_prometheus_returns_text(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/observability/prometheus')
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        assert 'gann_trader_total_trades' in r.text, f'Missing Prometheus metric in response: {r.text[:200]}'
        print(f'PASS: Prometheus endpoint returns text format with gann_trader_total_trades')

    def test_prometheus_format(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/observability/prometheus')
        # Prometheus format: # HELP ... / # TYPE ... / metric value
        assert '# HELP' in r.text, f'Missing # HELP in Prometheus output'
        assert '# TYPE' in r.text, f'Missing # TYPE in Prometheus output'
        print(f'PASS: Prometheus format correct (# HELP, # TYPE present)')


# ─── Sentiment ────────────────────────────────────────────────────────────────

class TestSentiment:
    """Fear & Greed Index and News Sentiment"""

    def test_fear_greed_200(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/sentiment/fear-greed',
                    params={'pcr': 0.8, 'india_vix': 12, 'breadth': 0.6, 'sentiment_score': 0.2})
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        print('PASS: GET /api/advanced/sentiment/fear-greed returns 200')

    def test_fear_greed_score_and_label(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/sentiment/fear-greed',
                    params={'pcr': 0.8, 'india_vix': 12, 'breadth': 0.6, 'sentiment_score': 0.2})
        d = r.json()
        assert 'score' in d, f'Missing "score" in response: {d}'
        assert 'label' in d, f'Missing "label" in response: {d}'
        assert isinstance(d['score'], (int, float)), f'score should be numeric, got: {type(d["score"])}'
        assert 0 <= d['score'] <= 100, f'score should be [0-100], got: {d["score"]}'
        valid_labels = {'EXTREME_FEAR', 'FEAR', 'NEUTRAL', 'GREED', 'EXTREME_GREED'}
        assert d['label'] in valid_labels, f'Unexpected label: {d["label"]}'
        print(f'PASS: fear_greed score={d["score"]}, label={d["label"]}')

    def test_fear_greed_components(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/sentiment/fear-greed',
                    params={'pcr': 0.8, 'india_vix': 12, 'breadth': 0.6, 'sentiment_score': 0.2})
        d = r.json()
        assert 'components' in d, f'Missing "components" in response: {d}'
        comps = d['components']
        assert 'pcr_score' in comps, f'Missing pcr_score in components: {comps}'
        assert 'vix_score' in comps, f'Missing vix_score in components: {comps}'
        assert 'breadth_score' in comps, f'Missing breadth_score in components: {comps}'
        print(f'PASS: fear_greed components: {comps}')

    def test_news_sentiment_200(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/sentiment/news', params={'ticker': 'RELIANCE.NS'})
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        d = r.json()
        assert 'ticker' in d, f'Missing ticker in response: {d}'
        assert 'articles' in d, f'Missing articles in response: {d}'
        print(f'PASS: news sentiment for RELIANCE.NS: {d.get("article_count", 0)} articles, label={d.get("aggregate_label")}')


# ─── Portfolio ────────────────────────────────────────────────────────────────

class TestPortfolio:
    """Portfolio hedge suggestions and smart order routing"""

    def test_hedge_suggest_200(self, api):
        r = api.post(f'{BASE_URL}/api/advanced/portfolio/hedge-suggest', json={
            'ticker': 'RELIANCE.NS',
            'current_price': 2800,
            'position_size': 0.1,
            'volatility': 0.25,
            'view': 'neutral',
        })
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        print('PASS: POST /api/advanced/portfolio/hedge-suggest returns 200')

    def test_hedge_suggest_strategies(self, api):
        r = api.post(f'{BASE_URL}/api/advanced/portfolio/hedge-suggest', json={
            'ticker': 'RELIANCE.NS',
            'current_price': 2800,
            'position_size': 0.1,
            'volatility': 0.25,
            'view': 'neutral',
        })
        d = r.json()
        assert 'strategies' in d or 'error' not in d, f'Response: {d}'
        if 'strategies' in d:
            assert isinstance(d['strategies'], list), f'strategies should be list: {d}'
            assert len(d['strategies']) > 0, f'Expected at least 1 strategy, got: {d}'
            # Check for Protective Put or Collar
            names = [s.get('name', '') for s in d['strategies']]
            print(f'PASS: hedge strategies: {names}')
        else:
            print(f'INFO: hedge-suggest response: {d}')

    def test_smart_route_200(self, api):
        r = api.post(f'{BASE_URL}/api/advanced/portfolio/smart-route', json={
            'ticker': 'RELIANCE.NS',
            'direction': 'BUY',
            'quantity': 500,
            'avg_volume': 200000,
            'volatility': 0.2,
            'urgency': 0.5,
        })
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        print('PASS: POST /api/advanced/portfolio/smart-route returns 200')

    def test_smart_route_twap_strategy(self, api):
        r = api.post(f'{BASE_URL}/api/advanced/portfolio/smart-route', json={
            'ticker': 'RELIANCE.NS',
            'direction': 'BUY',
            'quantity': 500,
            'avg_volume': 200000,
            'volatility': 0.2,
            'urgency': 0.5,
        })
        d = r.json()
        assert 'strategy' in d or 'slices' in d or 'error' not in d, f'Unexpected response: {d}'
        if 'strategy' in d:
            assert d['strategy'] in ('TWAP', 'VWAP', 'MARKET'), f'Unexpected strategy: {d["strategy"]}'
            print(f'PASS: smart route strategy={d["strategy"]}, slices={len(d.get("slices", []))}')
        else:
            print(f'INFO: smart-route response: {d}')


# ─── DreamerV3 / PER ──────────────────────────────────────────────────────────

class TestDreamerV3:
    """DreamerV3 PER stats and Risk-Reward state"""

    def test_per_stats_200(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/dreamer/per-stats')
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        print('PASS: GET /api/advanced/dreamer/per-stats returns 200')

    def test_per_stats_has_enabled(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/dreamer/per-stats')
        d = r.json()
        assert 'enabled' in d, f'Missing "enabled" field in per-stats: {d}'
        print(f'PASS: per-stats has enabled field: {d.get("enabled")}')

    def test_risk_reward_200(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/dreamer/risk-reward')
        assert r.status_code == 200, f'Expected 200, got {r.status_code}: {r.text}'
        print('PASS: GET /api/advanced/dreamer/risk-reward returns 200')

    def test_risk_reward_has_enabled(self, api):
        r = api.get(f'{BASE_URL}/api/advanced/dreamer/risk-reward')
        d = r.json()
        assert 'enabled' in d, f'Missing "enabled" field in risk-reward: {d}'
        print(f'PASS: risk-reward has enabled field: {d.get("enabled")}')
