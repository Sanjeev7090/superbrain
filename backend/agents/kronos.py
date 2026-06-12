# backend/agents/kronos.py
from typing import Dict
from datetime import datetime

class KronosScheduler:
    """Market Time & Cycle Awareness"""

    def get_cycle_signal(self, ticker: str) -> Dict:
        now = datetime.now()
        hour = now.hour

        # Intraday Cycle Logic
        if 9 <= hour <= 10:
            phase = "opening_auction"
            strength = 75
        elif 10 <= hour <= 14:
            phase = "trending"
            strength = 65
        elif 14 <= hour <= 15:
            phase = "power_hour"
            strength = 85
        else:
            phase = "closing"
            strength = 55

        return {
            "phase": phase,
            "strength": strength,
            "time_advantage": strength > 70,
            "summary": f"Kronos: {phase.upper()} phase ({strength}% strength)"
        }

    def should_trade_now(self) -> bool:
        """Circuit breaker for bad market hours"""
        hour = datetime.now().hour
        return 9 <= hour <= 15  # NSE market hours
