"""FastAPI router for RL Agent endpoints — powered by DreamerV3."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from . import dreamer_trainer as rl_trainer

rl_router = APIRouter(prefix="/api/rl-agent", tags=["RL Agent"])


class TrainRequest(BaseModel):
    algorithm:  str = "DreamerV3"    # always DreamerV3 (legacy PPO/SAC field kept for API compat)
    mode:       str = "historical"   # historical | live | hybrid
    ticker:     str = "RELIANCE.NS"
    timesteps:  int = 50_000


class PredictRequest(BaseModel):
    ticker: str = "RELIANCE.NS"


@rl_router.post("/train")
async def start_training(req: TrainRequest):
    ts = max(5_000, min(req.timesteps, 500_000))
    return rl_trainer.start_training(req.algorithm, req.mode, req.ticker, ts)


@rl_router.post("/stop")
async def stop_training():
    return rl_trainer.stop_training()


@rl_router.post("/reset")
async def reset_agent():
    return rl_trainer.reset_agent()


@rl_router.get("/status")
async def get_status():
    return rl_trainer.get_state()


@rl_router.post("/predict")
async def predict(req: PredictRequest):
    return rl_trainer.get_prediction(req.ticker)


@rl_router.post("/rebalance")
async def rebalance_weights(req: PredictRequest):
    return rl_trainer.rebalance(req.ticker)
