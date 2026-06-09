"""
DreamerV3-inspired RL Trainer — replaces PPO/SAC for TradingEnv.

Architecture:
  RSSM            : Recurrent State Space Model (prior + posterior)
  RewardPredictor : predicts reward from latent state
  Actor           : latent → Gaussian action (tanh-squashed, [-1, 1])
  Critic          : latent → scalar value estimate
  ReplayBuffer    : ring-buffer storing (obs, action, reward, next_obs, done)

Kronos Integration:
  When Kronos model is already loaded (lazy-loaded via kronos_router),
  its OHLC price forecasts are used as reward-shaping bonuses during training,
  aligning DreamerV3 exploration with Kronos market-direction priors.
"""

import glob
import logging
import os
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions import Normal

# ─── Advanced modules (lazy-imported to avoid circular deps) ──────────────────
try:
    from .per_buffer    import PrioritizedReplayBuffer
    from .risk_reward   import RiskAdjustedRewardEngine, dynamic_kelly, compute_cvar
    _PER_AVAILABLE = True
except ImportError:
    _PER_AVAILABLE = False
    logger.warning("PER / RiskReward modules not available — using uniform replay")

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ─── Architecture constants ───────────────────────────────────────────────────
STATE_DIM      = 38    # TradingEnv observation dims
ACTION_DIM     = 16    # TradingEnv action dims
LATENT_DIM     = 128   # DreamerV3 latent representation
HIDDEN_DIM     = 512   # MLP hidden size
OBS_ENC_DIM    = 128   # Obs encoder bottleneck
KRONOS_FEAT_DIM = 7   # Kronos raw feature vector: [direction, conf, move%, Δclose, Δhigh, Δlow, vol]
KRONOS_COND_DIM = 32  # Kronos conditioning vector injected into RSSM prior

STRATEGY_NAMES = [
    "Godzilla TTE", "SMC", "MiroFish", "Explosive Volume",
    "Falling Knife", "AI Indicator", "DEMON Confluence",
    "Golden Setup", "Reverse Swings", "AMDS-Hybrid",
    "PAC+S&O", "Narrative Swing",
]
_DEFAULT_WEIGHTS = [round(1 / 12, 4)] * 12

# ─── Shared state (thread-safe) ───────────────────────────────────────────────
_lock = threading.Lock()
_state: Dict = {
    "status":           "idle",
    "algorithm":        "DreamerV3",
    "mode":             "historical",
    "ticker":           "RELIANCE.NS",
    "episode":          0,
    "total_episodes":   0,
    "timesteps_done":   0,
    "timesteps_total":  50000,
    "current_reward":   0.0,
    "best_reward":      -1e9,
    "avg_reward_10":    0.0,
    "episode_rewards":  [],
    "last_weights":     _DEFAULT_WEIGHTS,
    "last_trade_signal": 0.0,
    "total_return":     0.0,
    "current_drawdown": 0.0,
    "started_at":       None,
    "last_updated":     None,
    "error":            None,
    "model_saved":      False,
    # DreamerV3-specific
    "wm_loss":          0.0,
    "actor_loss":       0.0,
    "critic_loss":      0.0,
    "kronos_active":    False,
    "kronos_bonus":     0.0,
    # PER buffer stats
    "per_enabled":      False,
    "per_size":         0,
    "per_beta":         0.4,
    "per_max_priority": 1.0,
    # Risk-adjusted reward
    "risk_reward_mode": False,
    "rr_sharpe":        0.0,
    "rr_cvar_penalty":  0.0,
    "rr_kelly_align":   0.0,
    "rr_drawdown":      0.0,
    # Continuous training
    "continuous_mode":  False,
    "continuous_cycles": 0,
}

_stop_evt    = threading.Event()
_train_thread: threading.Thread = None

# ─── PER Buffer global (shared across training runs) ──────────────────────────
_per_buffer = None        # PrioritizedReplayBuffer or None
_per_lock   = threading.Lock()

# ─── Continuous training ──────────────────────────────────────────────────────
_continuous_evt    = threading.Event()
_continuous_thread: threading.Thread = None

# ─── Risk-Reward engine (per ticker) ──────────────────────────────────────────
_rr_engine = None         # RiskAdjustedRewardEngine or None
_rr_state:  Dict = {}     # latest reward breakdown


def _upd(**kw):
    with _lock:
        _state.update(kw)
        _state["last_updated"] = datetime.now(timezone.utc).isoformat()


def get_state() -> Dict:
    with _lock:
        return dict(_state)


# ─── DreamerV3 Components ─────────────────────────────────────────────────────

class ObsEncoder(nn.Module):
    """
    Encodes raw observations into a fixed-dim embedding before the RSSM.
    Proper DreamerV3 always separates raw obs encoding from latent dynamics.
    """

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(STATE_DIM, HIDDEN_DIM),  nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, OBS_ENC_DIM),
        )

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.net(obs)


class RSSM(nn.Module):
    """Recurrent State Space Model — core of DreamerV3 world model."""

    def __init__(self):
        super().__init__()
        # Prior: p(z_t | z_{t-1}, a_{t-1}, k_t)  — Kronos-conditioned
        self.prior_net = nn.Sequential(
            nn.Linear(LATENT_DIM + ACTION_DIM + KRONOS_COND_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM),                                  nn.SiLU(),
            nn.Linear(HIDDEN_DIM, LATENT_DIM * 2),
        )
        # Posterior: q(z_t | z_{t-1}, enc(o_t))   — uses encoded obs
        self.posterior_net = nn.Sequential(
            nn.Linear(LATENT_DIM + OBS_ENC_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM),                 nn.SiLU(),
            nn.Linear(HIDDEN_DIM, LATENT_DIM * 2),
        )

    @staticmethod
    def _to_dist(params: torch.Tensor) -> Normal:
        mean, log_std = torch.chunk(params, 2, dim=-1)
        std = torch.exp(log_std.clamp(-4, 4)) + 0.01
        return Normal(mean, std)

    def compute_prior(
        self, prev_latent: torch.Tensor, action: torch.Tensor,
        kronos_cond: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, Normal]:
        """p(z_t | z_{t-1}, a_{t-1}, k_t) — Kronos-conditioned world model prior."""
        if kronos_cond is None:
            kronos_cond = torch.zeros(prev_latent.shape[0], KRONOS_COND_DIM,
                                      device=prev_latent.device)
        h    = torch.cat([prev_latent, action, kronos_cond], dim=-1)
        dist = self._to_dist(self.prior_net(h))
        return dist.rsample(), dist

    def compute_posterior(
        self, prev_latent: torch.Tensor, obs_enc: torch.Tensor
    ) -> Tuple[torch.Tensor, Normal]:
        """Takes encoded obs (OBS_ENC_DIM), not raw obs."""
        h    = torch.cat([prev_latent, obs_enc], dim=-1)
        dist = self._to_dist(self.posterior_net(h))
        return dist.rsample(), dist


class RewardPredictor(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(LATENT_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, 128),        nn.SiLU(),
            nn.Linear(128, 1),
        )

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        return self.net(z)


class Actor(nn.Module):
    """Gaussian policy: latent → (action ∈ [-1,1], log_prob)."""

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(LATENT_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, ACTION_DIM * 2),
        )

    def forward(
        self, latent: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        params          = self.net(latent)
        mean, log_std   = torch.chunk(params, 2, dim=-1)
        std             = log_std.clamp(-5, 2).exp()
        dist            = Normal(mean, std)
        raw             = dist.rsample()
        action          = torch.tanh(raw)
        # Jacobian correction for tanh squashing
        log_prob = (
            dist.log_prob(raw)
            - torch.log(1 - action.pow(2) + 1e-6)
        ).sum(-1, keepdim=True)
        return action, log_prob


class Critic(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(LATENT_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, 1),
        )

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        return self.net(z)


class KronosConditioner(nn.Module):
    """
    Encodes Kronos 7-dim market forecast features into a KRONOS_COND_DIM conditioning
    vector that is injected into the RSSM prior at every imagination step.

    This makes the world model's transition dynamics Kronos-aware:
      p(z_t | z_{t-1}, a_{t-1}, Kronos_forecast_t)

    Feature layout (KRONOS_FEAT_DIM = 7):
      [0] direction_score  : +1 BUY / 0 WAIT / -1 SELL
      [1] confidence       : 0 → 1
      [2] move_pct         : expected net % price change (signed, normalised ÷ 10)
      [3] pred_close_delta : (pred_close - last_close) / last_close
      [4] pred_high_delta  : (pred_high  - last_close) / last_close
      [5] pred_low_delta   : (pred_low   - last_close) / last_close
      [6] forecast_volatility : std(pred_closes) / mean(pred_closes)
    """

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(KRONOS_FEAT_DIM, 64),         nn.SiLU(),
            nn.Linear(64, 64),                       nn.SiLU(),
            nn.Linear(64, KRONOS_COND_DIM),          nn.Tanh(),  # bounded [-1,1]
        )

    def forward(self, feat: torch.Tensor) -> torch.Tensor:
        return self.net(feat)

    def zeros(self, batch_size: int, device=None) -> torch.Tensor:
        """Returns zero conditioning when Kronos is unavailable."""
        return torch.zeros(batch_size, KRONOS_COND_DIM,
                           device=device or "cpu")


class ReplayBuffer:
    """Stores transitions including per-step Kronos feature snapshot."""

    def __init__(self, capacity: int = 100_000):
        self._buf: deque = deque(maxlen=capacity)

    def push(self, obs, action, reward, next_obs, done,
             kronos_feat: Optional[np.ndarray] = None):
        kf = kronos_feat if kronos_feat is not None else np.zeros(KRONOS_FEAT_DIM, dtype=np.float32)
        self._buf.append((
            np.asarray(obs,      dtype=np.float32),
            np.asarray(action,   dtype=np.float32),
            float(reward),
            np.asarray(next_obs, dtype=np.float32),
            float(done),
            np.asarray(kf,       dtype=np.float32),
        ))

    def sample(self, n: int):
        batch = random.sample(self._buf, min(n, len(self._buf)))
        obs, act, rew, nobs, done, kfeat = zip(*batch)
        return (
            torch.FloatTensor(np.stack(obs)),
            torch.FloatTensor(np.stack(act)),
            torch.FloatTensor(rew).unsqueeze(1),
            torch.FloatTensor(np.stack(nobs)),
            torch.FloatTensor(done).unsqueeze(1),
            torch.FloatTensor(np.stack(kfeat)),   # (B, KRONOS_FEAT_DIM)
        )

    def __len__(self):
        return len(self._buf)


# ─── DreamerV3 Agent ──────────────────────────────────────────────────────────

class DreamerV3Agent:
    """
    Full DreamerV3 training loop:
      1. Collect real transitions → replay buffer
      2. Train world model  (ObsEncoder + RSSM posterior + reward predictor, KL vs prior)
      3. Imagine H-step rollouts from current latents
      4. Optimize actor (maximize λ-return) and critic (match λ-return)
    """

    def __init__(
        self,
        lr:       float = 3e-4,
        gamma:    float = 0.99,
        lambda_:  float = 0.95,
        horizon:  int   = 20,
    ):
        self.encoder       = ObsEncoder()
        self.kronos_cond   = KronosConditioner()   # Kronos → RSSM conditioning
        self.rssm          = RSSM()
        self.reward_pred   = RewardPredictor()
        self.actor         = Actor()
        self.critic        = Critic()
        self.target_critic = Critic()
        self.target_critic.load_state_dict(self.critic.state_dict())

        wm_params = (
            list(self.encoder.parameters())
            + list(self.kronos_cond.parameters())   # train Kronos encoder jointly
            + list(self.rssm.parameters())
            + list(self.reward_pred.parameters())
        )
        self.wm_opt     = optim.AdamW(wm_params, lr=lr)
        self.actor_opt  = optim.AdamW(self.actor.parameters(),  lr=lr)
        self.critic_opt = optim.AdamW(self.critic.parameters(), lr=lr)

        self.gamma   = gamma
        self.lambda_ = lambda_
        self.horizon = horizon

    # ---- inference ----

    def _zero_lat(self, batch_size: int) -> torch.Tensor:
        return torch.zeros(batch_size, LATENT_DIM)

    def act(
        self, obs: np.ndarray,
        deterministic: bool = False,
        kronos_feat: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        obs_t = torch.FloatTensor(obs).unsqueeze(0)
        with torch.no_grad():
            enc = self.encoder(obs_t)
            # Posterior latent
            z_dist = self.rssm._to_dist(
                self.rssm.posterior_net(torch.cat([self._zero_lat(1), enc], dim=-1))
            )
            z = z_dist.mean if deterministic else z_dist.rsample()
            a_params = self.actor.net(z)
            a_mean, _ = torch.chunk(a_params, 2, dim=-1)
            a = torch.tanh(a_mean) if deterministic else self.actor(z)[0]
        return a.squeeze(0).cpu().numpy()

    # ---- world model update ----

    def update_world_model(self, batch) -> float:
        obs, act, rew, next_obs, _, kfeat = batch
        B  = obs.shape[0]
        z0 = self._zero_lat(B)

        enc_obs = self.encoder(obs)                              # (B, OBS_ENC_DIM)
        z_post, post_dist = self.rssm.compute_posterior(z0, enc_obs)

        # Kronos-conditioned prior
        k_cond = self.kronos_cond(kfeat)                        # (B, KRONOS_COND_DIM)
        _, prior_dist = self.rssm.compute_prior(z0, act, k_cond)

        # Reward reconstruction
        rew_loss = nn.MSELoss()(self.reward_pred(z_post), rew)

        # KL(posterior || Kronos-conditioned prior) — free-bits 0.1 nats
        kl_loss = torch.distributions.kl_divergence(
            post_dist, prior_dist
        ).mean().clamp(min=0.1)

        loss = rew_loss + 0.5 * kl_loss

        self.wm_opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(
            list(self.encoder.parameters())
            + list(self.kronos_cond.parameters())
            + list(self.rssm.parameters())
            + list(self.reward_pred.parameters()),
            max_norm=10.0,
        )
        self.wm_opt.step()
        return float(loss.item())

    # ---- actor-critic update on imagined trajectories ----

    def update_actor_critic(
        self, batch, kronos_feat: Optional[np.ndarray] = None
    ) -> Tuple[float, float]:
        """Imagines H-step trajectories using Kronos-conditioned RSSM prior."""
        obs, act, rew, next_obs, _, kfeat_batch = batch
        B  = obs.shape[0]
        z0 = self._zero_lat(B)

        # Encode obs and get Kronos conditioning for imagination
        with torch.no_grad():
            enc_obs    = self.encoder(obs)
            start_z, _ = self.rssm.compute_posterior(z0, enc_obs)
            # Use per-batch Kronos features for imagination conditioning
            k_cond     = self.kronos_cond(kfeat_batch)          # (B, KRONOS_COND_DIM)

        # Imagined rollout — every step uses Kronos-conditioned prior
        latents, rewards, values, log_probs = [], [], [], []
        cur_z = start_z.detach()

        for _ in range(self.horizon):
            a_img, lp = self.actor(cur_z)            # gradients through actor

            with torch.no_grad():
                r_pred    = self.reward_pred(cur_z)
                v_pred    = self.target_critic(cur_z)
                next_z, _ = self.rssm.compute_prior(cur_z, a_img, k_cond)

            latents.append(cur_z)
            rewards.append(r_pred)
            values.append(v_pred)
            log_probs.append(lp)

            cur_z = next_z.detach()

        # λ-return targets
        with torch.no_grad():
            final_v = self.target_critic(cur_z)
        R = final_v
        returns = []
        for r, v in zip(reversed(rewards), reversed(values)):
            R = r + self.gamma * ((1 - self.lambda_) * v + self.lambda_ * R)
            returns.insert(0, R)

        returns_t  = torch.stack(returns,   dim=1)   # (B, H, 1)
        log_prbs_t = torch.stack(log_probs, dim=1)   # (B, H, 1)

        # ── critic loss ──
        all_z = torch.stack(latents, dim=1)            # (B, H, LATENT_DIM)
        c_pred = self.critic(
            all_z.detach().view(-1, LATENT_DIM)
        ).view(B, self.horizon, 1)
        critic_loss = nn.MSELoss()(c_pred, returns_t.detach())

        self.critic_opt.zero_grad()
        critic_loss.backward()
        nn.utils.clip_grad_norm_(self.critic.parameters(), 10.0)
        self.critic_opt.step()

        # ── actor loss ──
        actor_loss = -(returns_t.detach() * log_prbs_t).mean()

        self.actor_opt.zero_grad()
        actor_loss.backward()
        nn.utils.clip_grad_norm_(self.actor.parameters(), 10.0)
        self.actor_opt.step()

        # Soft update target critic (τ = 0.005)
        for p, tp in zip(
            self.critic.parameters(),
            self.target_critic.parameters()
        ):
            tp.data.copy_(0.995 * tp.data + 0.005 * p.data)

        return float(actor_loss.item()), float(critic_loss.item())

    # ---- persistence ----

    def save(self, path: str):
        torch.save({
            "encoder":       self.encoder.state_dict(),
            "kronos_cond":   self.kronos_cond.state_dict(),
            "rssm":          self.rssm.state_dict(),
            "reward_pred":   self.reward_pred.state_dict(),
            "actor":         self.actor.state_dict(),
            "critic":        self.critic.state_dict(),
            "target_critic": self.target_critic.state_dict(),
            "latent_dim":    LATENT_DIM,
            "hidden_dim":    HIDDEN_DIM,
            "horizon":       self.horizon,
        }, path)
        logger.info("DreamerV3 model saved → %s", path)

    def load(self, path: str):
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        if ckpt.get("latent_dim", LATENT_DIM) != LATENT_DIM:
            raise ValueError(
                f"Saved model latent_dim={ckpt['latent_dim']} "
                f"!= current LATENT_DIM={LATENT_DIM}. Delete model and retrain."
            )
        self.encoder.load_state_dict(ckpt["encoder"])
        if "kronos_cond" in ckpt:
            self.kronos_cond.load_state_dict(ckpt["kronos_cond"])
        self.rssm.load_state_dict(ckpt["rssm"])
        self.reward_pred.load_state_dict(ckpt["reward_pred"])
        self.actor.load_state_dict(ckpt["actor"])
        self.critic.load_state_dict(ckpt["critic"])
        self.target_critic.load_state_dict(ckpt["target_critic"])
        logger.info("DreamerV3 model loaded ← %s", path)


# ─── Kronos Integration ───────────────────────────────────────────────────────

# Per-mode Kronos refresh intervals (in env steps)
KRONOS_REFRESH = {
    "historical": 300,   # historical data rarely changes → refresh every 300 steps
    "live":        50,   # live market → refresh every 50 steps (≈ every ~5 candles)
    "hybrid":     150,   # blend of both
}


def _get_kronos_features(ticker: str, pred_len: int = 5) -> Tuple[np.ndarray, float]:
    """
    Fetch Kronos forecast → extract 7 normalised features + reward bonus.

    Features (KRONOS_FEAT_DIM = 7):
      [0] direction_score  : +1 BUY / 0 WAIT / -1 SELL
      [1] confidence       : 0 → 1
      [2] move_pct         : expected net % change ÷ 10  (normalised)
      [3] pred_close_delta : (mean_pred_close - last_close) / last_close
      [4] pred_high_delta  : (max_pred_high  - last_close) / last_close
      [5] pred_low_delta   : (min_pred_low   - last_close) / last_close
      [6] forecast_vol     : std(pred_closes) / (mean_pred_closes + 1e-8)

    Returns (features, bonus):
      features: np.ndarray of shape (KRONOS_FEAT_DIM,) in float32
      bonus:    scalar reward-shaping value (0 if unavailable)
    """
    zero = np.zeros(KRONOS_FEAT_DIM, dtype=np.float32)
    try:
        import sys
        import importlib
        import pandas as pd

        parent = str(Path(__file__).parent.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        kr        = importlib.import_module("kronos_router")
        predictor = getattr(kr, "_PREDICTOR", None)
        if predictor is None:
            return zero, 0.0

        _fetch  = getattr(kr, "_fetch_history")
        _signal = getattr(kr, "_build_signal")
        _ftss   = getattr(kr, "_build_future_timestamps")

        hist = _fetch(ticker, "1d", 60)
        if hist is None or len(hist) < 20:
            return zero, 0.0

        last_close = float(hist["Close"].iloc[-1])

        in_df = pd.DataFrame({
            "open":   hist["Open"].astype(float).values,
            "high":   hist["High"].astype(float).values,
            "low":    hist["Low"].astype(float).values,
            "close":  hist["Close"].astype(float).values,
            "volume": hist["Volume"].astype(float).values,
        })
        in_df["amount"] = in_df["volume"] * in_df["close"]

        x_ts     = pd.Series(pd.to_datetime(hist.index)).reset_index(drop=True)
        y_ts_idx = _ftss(x_ts.iloc[-1], pred_len, "1d")
        y_ts     = pd.Series(y_ts_idx)

        pred_df = predictor.predict(
            df=in_df, x_timestamp=x_ts, y_timestamp=y_ts,
            pred_len=pred_len, T=1.0, top_p=0.9, sample_count=1, verbose=False,
        )
        sig = _signal(hist, pred_df)

        direction  = sig.get("direction", "WAIT")
        confidence = float(sig.get("confidence", 50)) / 100.0

        dir_score = 1.0 if direction == "BUY" else (-1.0 if direction == "SELL" else 0.0)

        # Extract multi-candle forecast statistics
        pred_closes = pred_df["close"].astype(float).values if "close" in pred_df else np.array([last_close])
        pred_highs  = pred_df["high"].astype(float).values  if "high"  in pred_df else np.array([last_close])
        pred_lows   = pred_df["low"].astype(float).values   if "low"   in pred_df else np.array([last_close])

        mean_pred_close = float(pred_closes.mean())
        max_pred_high   = float(pred_highs.max())
        min_pred_low    = float(pred_lows.min())
        forecast_vol    = float(pred_closes.std() / (mean_pred_close + 1e-8))

        move_pct = (mean_pred_close - last_close) / (last_close + 1e-8)

        feat = np.array([
            dir_score,                                             # [0]
            confidence,                                            # [1]
            float(np.clip(move_pct / 0.10, -3.0, 3.0)),          # [2] norm ÷10%
            float(np.clip((mean_pred_close - last_close) / (last_close + 1e-8), -0.5, 0.5)),  # [3]
            float(np.clip((max_pred_high   - last_close) / (last_close + 1e-8),  0.0, 0.5)),  # [4]
            float(np.clip((min_pred_low    - last_close) / (last_close + 1e-8), -0.5, 0.0)),  # [5]
            float(np.clip(forecast_vol, 0.0, 0.1) * 10),         # [6] scale 0-1
        ], dtype=np.float32)

        # Reward bonus: confidence × direction × magnitude
        bonus = confidence * abs(dir_score) * min(abs(move_pct) / 0.02 + 0.5, 0.20)

        logger.debug(
            "Kronos features [%s]: dir=%+.0f conf=%.2f move=%.3f%% bonus=%.4f",
            ticker, dir_score, confidence, move_pct * 100, bonus,
        )
        return feat, float(bonus)

    except Exception as exc:
        logger.debug("Kronos features skipped for %s: %s", ticker, exc)
        return zero, 0.0


# ─── Background Training Worker ──────────────────────────────────────────────

BATCH_SIZE    = 64
WARMUP_STEPS  = 500
UPDATE_EVERY  = 4


def _train_worker(mode: str, ticker: str, timesteps: int):
    """
    Unified training loop for all modes.

    Kronos adaptive schedule (steps between refreshes):
      historical → 300  (market context rarely changes)
      live       → 50   (fresh candles ≈ every 5 steps)
      hybrid     → 150  (blend)
    """
    _stop_evt.clear()
    try:
        from .trading_env import TradingEnv
        import yfinance as yf
        import pandas as pd

        _upd(
            status="training", algorithm="DreamerV3", mode=mode,
            ticker=ticker, timesteps_total=timesteps, timesteps_done=0,
            episode=0, episode_rewards=[], error=None,
            wm_loss=0.0, actor_loss=0.0, critic_loss=0.0,
            kronos_active=False, kronos_bonus=0.0,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # ── Load historical OHLCV ──
        df_hist = None
        if mode in ("historical", "hybrid"):
            try:
                raw = yf.download(
                    ticker, period="2y", interval="1d",
                    progress=False, auto_adjust=True,
                )
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.droplevel(1)
                df_hist = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(df_hist) < 60:
                    df_hist = None
            except Exception as exc:
                logger.warning("History load failed for %s: %s", ticker, exc)

        env    = TradingEnv(data=df_hist, ticker=ticker)
        agent  = DreamerV3Agent()

        # ── Use PER buffer if available ──
        global _per_buffer, _rr_engine
        if _PER_AVAILABLE:
            with _per_lock:
                if _per_buffer is None:
                    _per_buffer = PrioritizedReplayBuffer(capacity=100_000)
                replay = _per_buffer
            _rr_engine = RiskAdjustedRewardEngine()
            _upd(per_enabled=True, risk_reward_mode=True)
        else:
            replay = ReplayBuffer(capacity=50_000)
            _upd(per_enabled=False, risk_reward_mode=False)

        model_path = str(MODELS_DIR / f"dreamer_{ticker.replace('.', '_')}.pt")
        if os.path.exists(model_path):
            try:
                agent.load(model_path)
            except Exception as exc:
                logger.warning("Stale model removed (%s) — fresh start", exc)
                os.remove(model_path)

        # ── Kronos state ──
        kronos_refresh = KRONOS_REFRESH.get(mode, 150)
        kronos_feat    = np.zeros(KRONOS_FEAT_DIM, dtype=np.float32)
        kronos_bonus   = 0.0
        kronos_active  = False
        next_kronos_step = 0          # step at which to refresh Kronos

        ep_rewards: List[float] = []
        total_steps   = 0
        episode       = 0
        info: Dict    = {}

        while total_steps < timesteps and not _stop_evt.is_set():
            obs, _ = env.reset()
            ep_reward = 0.0
            done      = False

            while not done and not _stop_evt.is_set():

                # ── Adaptive Kronos refresh ──
                if total_steps >= next_kronos_step:
                    new_feat, new_bonus = _get_kronos_features(ticker)
                    active = bool(new_bonus > 0)
                    if active:
                        kronos_feat   = new_feat
                        kronos_bonus  = new_bonus
                        kronos_active = True
                    else:
                        kronos_active = bool(kronos_feat.any())  # keep last if fresh fails
                    next_kronos_step = total_steps + kronos_refresh
                    _upd(
                        kronos_active=kronos_active,
                        kronos_bonus=round(float(kronos_bonus), 4),
                    )

                # ── Action selection ──
                if total_steps < WARMUP_STEPS or random.random() < 0.05:
                    action = env.action_space.sample()
                else:
                    action = agent.act(obs, kronos_feat=kronos_feat)

                next_obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated

                # ── Risk-adjusted reward shaping ──
                trade_sig   = info.get("trade_signal", 0.0)
                position_sz = abs(float(trade_sig))    # proxy for position size
                dir_align   = float(np.sign(trade_sig + 1e-8)) * kronos_feat[0]
                kronos_shaped = kronos_bonus * max(dir_align, 0.0)

                if _rr_engine is not None:
                    kelly_f = dynamic_kelly(
                        win_rate=max(0.3, _state.get("avg_reward_10", 0) / 10 + 0.5),
                        avg_win=0.01, avg_loss=0.005,
                    ) if _PER_AVAILABLE else 0.25
                    shaped_rew, rr_breakdown = _rr_engine.compute(reward, position_sz, kelly_f)
                    shaped_rew += kronos_shaped
                    global _rr_state
                    _rr_state = rr_breakdown
                    _upd(
                        rr_sharpe=rr_breakdown["sharpe_bonus"],
                        rr_cvar_penalty=rr_breakdown["cvar_penalty"],
                        rr_kelly_align=rr_breakdown["kelly_align"],
                        rr_drawdown=rr_breakdown["drawdown"],
                    )
                else:
                    shaped_rew = reward + kronos_shaped

                replay.push(obs, action, shaped_rew, next_obs, float(done),
                            kronos_feat=kronos_feat)

                # ── Update PER priorities after world-model update ──
                if _PER_AVAILABLE and isinstance(replay, PrioritizedReplayBuffer):
                    if len(replay) >= BATCH_SIZE and total_steps % UPDATE_EVERY == 0:
                        batch = replay.sample(BATCH_SIZE)
                        # batch[6] = IS weights, batch[7] = leaf indices
                        obs_b, act_b, rew_b, nobs_b, done_b, kfeat_b, is_w, leaves = batch
                        wm_l  = agent.update_world_model((obs_b, act_b, rew_b, nobs_b, done_b, kfeat_b))
                        a_l, c_l = agent.update_actor_critic((obs_b, act_b, rew_b, nobs_b, done_b, kfeat_b))
                        # Use world-model loss as TD proxy for priority update
                        td_errors = np.full(len(leaves), abs(wm_l) + 1e-6)
                        replay.update_priorities(leaves, td_errors)
                        per_s = replay.stats()
                        _upd(
                            timesteps_done=total_steps,
                            wm_loss=round(float(wm_l), 6),
                            actor_loss=round(float(a_l), 6),
                            critic_loss=round(float(c_l), 6),
                            per_size=per_s["size"],
                            per_beta=per_s["beta"],
                            per_max_priority=per_s["max_priority"],
                        )
                else:
                    if len(replay) >= BATCH_SIZE and total_steps % UPDATE_EVERY == 0:
                        batch = replay.sample(BATCH_SIZE)
                        wm_l  = agent.update_world_model(batch)
                        a_l, c_l = agent.update_actor_critic(batch)
                        _upd(
                            timesteps_done=total_steps,
                            wm_loss=round(float(wm_l), 6),
                            actor_loss=round(float(a_l), 6),
                            critic_loss=round(float(c_l), 6),
                        )

                obs         = next_obs
                ep_reward  += reward
                total_steps += 1

            # ── End of episode ──
            episode   += 1
            ep_rewards.append(ep_reward)
            avg10  = float(np.mean(ep_rewards[-10:])) if ep_rewards else 0.0
            best   = max(_state["best_reward"], ep_reward)

            weights   = info.get("strategy_weights", _DEFAULT_WEIGHTS)
            trade_sig = info.get("trade_signal",     0.0)
            tot_ret   = info.get("total_return",     0.0)
            drawdn    = info.get("drawdown",         0.0)

            _upd(
                episode=episode,
                timesteps_done=total_steps,
                current_reward=ep_reward,
                best_reward=best,
                avg_reward_10=avg10,
                episode_rewards=ep_rewards[-200:],
                last_weights=list(weights) if not isinstance(weights, list) else weights,
                last_trade_signal=float(trade_sig),
                total_return=float(tot_ret),
                current_drawdown=float(drawdn),
            )

        # ── Phase done ──
        if not _stop_evt.is_set():
            agent.save(model_path)
            _upd(status="running", model_saved=True)

            # ── Hybrid Phase 2: Live fine-tuning with Kronos live refresh ──
            if mode == "hybrid":
                _upd(mode="live", status="training")
                kronos_refresh = KRONOS_REFRESH["live"]
                env2 = TradingEnv(ticker=ticker)
                fine_tune_steps = max(5_000, timesteps // 5)
                ep_rewards2: List[float] = []
                total2 = 0
                next_k2 = 0

                while total2 < fine_tune_steps and not _stop_evt.is_set():
                    obs2, _ = env2.reset()
                    ep2 = 0.0
                    done2 = False
                    info2: Dict = {}

                    while not done2 and not _stop_evt.is_set():
                        # Live Kronos refresh (more frequent)
                        if total2 >= next_k2:
                            nf, nb = _get_kronos_features(ticker)
                            if nb > 0:
                                kronos_feat  = nf
                                kronos_bonus = nb
                            next_k2 = total2 + kronos_refresh
                            _upd(
                                kronos_active=bool(kronos_bonus > 0),
                                kronos_bonus=round(float(kronos_bonus), 4),
                            )

                        a2 = agent.act(obs2, kronos_feat=kronos_feat) if total2 >= WARMUP_STEPS else env2.action_space.sample()
                        no2, r2, t2, tr2, info2 = env2.step(a2)
                        done2 = t2 or tr2

                        trade2    = info2.get("trade_signal", 0.0)
                        dir2      = float(np.sign(trade2 + 1e-8)) * kronos_feat[0]
                        sr2       = r2 + kronos_bonus * max(dir2, 0.0)

                        replay.push(obs2, a2, sr2, no2, float(done2), kronos_feat=kronos_feat)
                        obs2   = no2
                        ep2   += r2
                        total2 += 1

                        if len(replay) >= BATCH_SIZE and total2 % UPDATE_EVERY == 0:
                            b2 = replay.sample(BATCH_SIZE)
                            wl2 = agent.update_world_model(b2)
                            al2, cl2 = agent.update_actor_critic(b2)
                            _upd(
                                timesteps_done=timesteps + total2,
                                wm_loss=round(float(wl2), 6),
                                actor_loss=round(float(al2), 6),
                                critic_loss=round(float(cl2), 6),
                            )

                    ep_rewards2.append(ep2)
                    avg10_2 = float(np.mean(ep_rewards2[-10:])) if ep_rewards2 else 0.0
                    _upd(
                        avg_reward_10=avg10_2,
                        last_weights=info2.get("strategy_weights", _DEFAULT_WEIGHTS),
                        last_trade_signal=info2.get("trade_signal", 0.0),
                    )

                if not _stop_evt.is_set():
                    agent.save(model_path)
                    _upd(status="running")
        else:
            _upd(status="paused")

    except Exception as exc:
        logger.exception("DreamerV3 training error")
        _upd(status="idle", error=str(exc))


# ─── Live Continuous Learning ─────────────────────────────────────────────────

def _live_loop(ticker: str):
    """Continuously fine-tunes model with fresh market data + live Kronos context."""
    while not _stop_evt.is_set():
        time.sleep(300)
        if _stop_evt.is_set():
            break
        if _state["status"] != "running":
            continue
        try:
            from .trading_env import TradingEnv
            model_path = str(MODELS_DIR / f"dreamer_{ticker.replace('.', '_')}.pt")
            if not os.path.exists(model_path):
                continue

            agent  = DreamerV3Agent()
            agent.load(model_path)
            env    = TradingEnv(ticker=ticker)
            replay = ReplayBuffer(capacity=10_000)
            obs, _ = env.reset()

            # Fresh Kronos features for this live cycle
            kfeat, kbonus = _get_kronos_features(ticker)
            _upd(kronos_active=bool(kbonus > 0), kronos_bonus=round(float(kbonus), 4))

            for step in range(1000):
                if _stop_evt.is_set():
                    break
                a = agent.act(obs, kronos_feat=kfeat)
                no, r, t, tr, info = env.step(a)
                done = t or tr
                trade_sig = info.get("trade_signal", 0.0)
                dir_align = float(np.sign(trade_sig + 1e-8)) * kfeat[0]
                sr = r + kbonus * max(dir_align, 0.0)
                replay.push(obs, a, sr, no, float(done), kronos_feat=kfeat)
                obs = no if not done else env.reset()[0]

                # Refresh Kronos every 50 steps in live mode
                if step % KRONOS_REFRESH["live"] == 0:
                    nf, nb = _get_kronos_features(ticker)
                    if nb > 0:
                        kfeat, kbonus = nf, nb
                    _upd(kronos_active=bool(kbonus > 0), kronos_bonus=round(float(kbonus), 4))

                if len(replay) >= BATCH_SIZE:
                    b = replay.sample(BATCH_SIZE)
                    agent.update_world_model(b)
                    agent.update_actor_critic(b)

            agent.save(model_path)
            _upd(timesteps_done=_state["timesteps_done"] + 1000)
        except Exception as exc:
            logger.debug("Live loop iteration error: %s", exc)


# ─── Public API ───────────────────────────────────────────────────────────────

def start_training(
    algorithm: str, mode: str, ticker: str, timesteps: int = 50_000
) -> Dict:
    global _train_thread
    if _state["status"] == "training":
        return {"success": False, "error": "DreamerV3 training already in progress"}

    _stop_evt.clear()
    _train_thread = threading.Thread(
        target=_train_worker,
        args=(mode, ticker, timesteps),
        daemon=True,
        name="dreamer-trainer",
    )
    _train_thread.start()

    if mode == "live":
        threading.Thread(
            target=_live_loop,
            args=(ticker,),
            daemon=True,
            name="dreamer-live-loop",
        ).start()

    return {
        "success": True,
        "message": f"DreamerV3 training started ({mode}) for {ticker}",
    }


def stop_training() -> Dict:
    _stop_evt.set()
    _upd(status="paused")
    return {"success": True, "message": "DreamerV3 training stopped"}


def reset_agent() -> Dict:
    _stop_evt.set()
    for f in glob.glob(str(MODELS_DIR / "dreamer_*.pt")):
        try:
            os.remove(f)
        except OSError:
            pass
    with _lock:
        _state.update({
            "status":        "idle",
            "episode":       0,
            "timesteps_done": 0,
            "episode_rewards": [],
            "last_weights":  _DEFAULT_WEIGHTS,
            "total_return":  0.0,
            "best_reward":   -1e9,
            "avg_reward_10": 0.0,
            "error":         None,
            "model_saved":   False,
            "current_reward": 0.0,
            "wm_loss":       0.0,
            "actor_loss":    0.0,
            "critic_loss":   0.0,
            "kronos_active": False,
            "kronos_bonus":  0.0,
        })
    return {"success": True}


def rebalance(ticker: str) -> Dict:
    """Run DreamerV3 inference → return rebalanced strategy weights + signal."""
    s = get_state()
    if s["status"] not in ("running", "paused"):
        return {
            "success":    False,
            "error":      "Train DreamerV3 first before rebalancing",
            "weights":    _DEFAULT_WEIGHTS,
            "confidence": 0,
            "changes":    [],
        }

    model_path = str(MODELS_DIR / f"dreamer_{ticker.replace('.', '_')}.pt")
    if not os.path.exists(model_path):
        alts = glob.glob(str(MODELS_DIR / "dreamer_*.pt"))
        if alts:
            model_path = alts[0]
        else:
            return {
                "success":    False,
                "error":      "No saved DreamerV3 model found. Complete training first.",
                "weights":    _DEFAULT_WEIGHTS,
                "confidence": 0,
                "changes":    [],
            }

    try:
        from .trading_env import TradingEnv

        agent = DreamerV3Agent()
        agent.load(model_path)

        env  = TradingEnv(ticker=ticker)
        obs, _ = env.reset()
        action  = agent.act(obs, deterministic=True)

        # Strategy weights (dims 0-11) via softmax
        raw_w  = np.array(action[:12], dtype=np.float32)
        scaled = (raw_w + 1.0) / 2.0
        exp_w  = np.exp(scaled - scaled.max())
        new_w  = (exp_w / exp_w.sum()).tolist()

        trade_sig = float(action[12])

        # Confidence = 1 − normalised entropy
        w_arr = np.array(new_w)
        H     = -float(np.sum(w_arr * np.log(w_arr + 1e-9)))
        max_H = float(np.log(len(new_w)))
        confidence = max(0, min(100, int(round((1.0 - H / max_H) * 100))))

        if trade_sig > 0.3:
            signal   = "BUY"
            sig_conf = min(int((trade_sig - 0.3) / 0.7 * 100), 100)
        elif trade_sig < -0.3:
            signal   = "SELL"
            sig_conf = min(int((-trade_sig - 0.3) / 0.7 * 100), 100)
        else:
            signal   = "HOLD"
            sig_conf = int((0.3 - abs(trade_sig)) / 0.3 * 100)

        old_w   = s.get("last_weights", _DEFAULT_WEIGHTS)
        changes = [
            {
                "strategy": name,
                "old":      round(old_w[i] * 100, 1),
                "new":      round(new_w[i] * 100, 1),
                "delta":    round((new_w[i] - old_w[i]) * 100, 1),
            }
            for i, name in enumerate(STRATEGY_NAMES)
        ]

        _upd(last_weights=new_w, last_trade_signal=trade_sig)

        return {
            "success":           True,
            "ticker":            ticker,
            "weights":           new_w,
            "weights_named":     dict(
                zip(STRATEGY_NAMES, [round(w * 100, 1) for w in new_w])
            ),
            "confidence":        confidence,
            "signal":            signal,
            "signal_confidence": sig_conf,
            "changes":           changes,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        logger.exception("DreamerV3 rebalance error")
        return {
            "success":    False,
            "error":      str(exc),
            "weights":    _DEFAULT_WEIGHTS,
            "confidence": 0,
            "changes":    [],
        }


def get_prediction(ticker: str) -> Dict:
    s         = get_state()
    weights   = s.get("last_weights",      _DEFAULT_WEIGHTS)
    trade_sig = s.get("last_trade_signal", 0.0)

    if s["status"] not in ("running", "paused", "training"):
        return {
            "signal":           "HOLD",
            "confidence":       0,
            "strategy_weights": dict(
                zip(STRATEGY_NAMES, [round(w * 100, 1) for w in _DEFAULT_WEIGHTS])
            ),
            "weights_raw":      _DEFAULT_WEIGHTS,
            "wm_loss":          0.0,
            "kronos_active":    False,
            "message":          "DreamerV3 not trained yet — start training first",
        }

    if trade_sig > 0.3:
        signal     = "BUY"
        confidence = min(int((trade_sig - 0.3) / 0.7 * 100), 100)
    elif trade_sig < -0.3:
        signal     = "SELL"
        confidence = min(int((-trade_sig - 0.3) / 0.7 * 100), 100)
    else:
        signal     = "HOLD"
        confidence = int((0.3 - abs(trade_sig)) / 0.3 * 100)

    kronos_bonus  = s.get("kronos_bonus", 0.0)
    kronos_active = s.get("kronos_active", False)
    mode          = s.get("mode", "historical")

    return {
        "signal":           signal,
        "confidence":       confidence,
        "strategy_weights": dict(
            zip(STRATEGY_NAMES, [round(w * 100, 1) for w in weights])
        ),
        "weights_raw":      list(weights),
        "total_return":     s.get("total_return",   0.0),
        "episode":          s.get("episode",        0),
        "avg_reward_10":    s.get("avg_reward_10",  0.0),
        "wm_loss":          s.get("wm_loss",        0.0),
        "actor_loss":       s.get("actor_loss",     0.0),
        "critic_loss":      s.get("critic_loss",    0.0),
        "kronos_active":    kronos_active,
        "kronos_bonus":     kronos_bonus,
        "kronos_refresh_rate": KRONOS_REFRESH.get(mode, 150),
        "message": (
            f"Ep {s['episode']} | "
            f"WM: {s.get('wm_loss', 0):.4f} | "
            f"Avg Rew: {s.get('avg_reward_10', 0):.4f}"
            + (f" | Kronos ×{KRONOS_REFRESH.get(mode, 150)}-step" if kronos_active else "")
        ),
    }


# ─── PER / Risk-Reward / Continuous — new public API ─────────────────────────

def get_per_stats() -> Dict:
    """Return PER buffer statistics."""
    with _per_lock:
        buf = _per_buffer
    if buf is None:
        return {"enabled": False, "message": "PER buffer not initialised — start training first"}
    stats = buf.stats()
    stats["enabled"] = True
    return stats


def get_risk_reward_state() -> Dict:
    """Return latest risk-adjusted reward breakdown."""
    global _rr_state
    if not _rr_state:
        return {"enabled": False, "message": "Risk-reward engine not active"}
    return {"enabled": True, **_rr_state}


def _continuous_worker(ticker: str):
    """
    Continuous online-learning loop — runs indefinitely until stopped.
    Every 60 seconds: collect 200 steps of fresh data → train → save.
    """
    _upd(continuous_mode=True, continuous_cycles=0)
    cycles = 0
    while not _continuous_evt.is_set():
        try:
            from .trading_env import TradingEnv
            model_path = str(MODELS_DIR / f"dreamer_{ticker.replace('.', '_')}.pt")
            agent = DreamerV3Agent()
            if os.path.exists(model_path):
                agent.load(model_path)
            else:
                # No pre-trained model — start from scratch and save initial weights
                logger.info("No model found for %s — initialising fresh DreamerV3 for continuous training", ticker)
                MODELS_DIR.mkdir(parents=True, exist_ok=True)
                agent.save(model_path)
            env   = TradingEnv(ticker=ticker)
            global _per_buffer, _rr_engine

            with _per_lock:
                if _per_buffer is None and _PER_AVAILABLE:
                    _per_buffer = PrioritizedReplayBuffer(capacity=100_000)
                replay = _per_buffer if _per_buffer is not None else ReplayBuffer(10_000)

            if _PER_AVAILABLE and _rr_engine is None:
                _rr_engine = RiskAdjustedRewardEngine()

            kfeat, kbonus = _get_kronos_features(ticker)
            obs, _ = env.reset()

            for step in range(200):
                if _continuous_evt.is_set():
                    break
                action = agent.act(obs, kronos_feat=kfeat)
                next_obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated

                trade_sig   = info.get("trade_signal", 0.0)
                dir_align   = float(np.sign(trade_sig + 1e-8)) * kfeat[0]
                if _rr_engine is not None:
                    shaped_rew, _ = _rr_engine.compute(reward, abs(trade_sig), 0.25)
                    shaped_rew += kbonus * max(dir_align, 0.0)
                else:
                    shaped_rew = reward + kbonus * max(dir_align, 0.0)

                replay.push(obs, action, shaped_rew, next_obs, float(done), kronos_feat=kfeat)
                obs = next_obs if not done else env.reset()[0]

                if len(replay) >= BATCH_SIZE:
                    if _PER_AVAILABLE and isinstance(replay, PrioritizedReplayBuffer):
                        b = replay.sample(BATCH_SIZE)
                        obs_b, act_b, rew_b, nobs_b, done_b, kfeat_b, is_w, leaves = b
                        wm_l = agent.update_world_model((obs_b, act_b, rew_b, nobs_b, done_b, kfeat_b))
                        agent.update_actor_critic((obs_b, act_b, rew_b, nobs_b, done_b, kfeat_b))
                        replay.update_priorities(leaves, np.full(len(leaves), abs(wm_l) + 1e-6))
                    else:
                        b = replay.sample(BATCH_SIZE)
                        agent.update_world_model(b)
                        agent.update_actor_critic(b)

                # Update UI progress every 20 steps
                if step % 20 == 0:
                    _upd(continuous_cycle_step=step, continuous_cycle_total=200)

            agent.save(model_path)
            cycles += 1
            _upd(
                continuous_cycles=cycles,
                timesteps_done=_state.get("timesteps_done", 0) + 200,
            )
            logger.info("Continuous cycle %d done for %s", cycles, ticker)

        except Exception as exc:
            logger.warning("Continuous loop error (cycle %d): %s", cycles, exc, exc_info=True)

        # Wait before next cycle
        for _ in range(60):
            if _continuous_evt.is_set():
                break
            time.sleep(1)

    _upd(continuous_mode=False)
    logger.info("Continuous training stopped for %s", ticker)


def start_continuous(ticker: str) -> Dict:
    global _continuous_thread
    if _state.get("continuous_mode"):
        return {"success": False, "message": "Continuous training already running"}

    _continuous_evt.clear()
    _continuous_thread = threading.Thread(
        target=_continuous_worker,
        args=(ticker,),
        daemon=True,
        name="dreamer-continuous",
    )
    _continuous_thread.start()
    return {"success": True, "message": f"Continuous online learning started for {ticker}"}


def stop_continuous() -> Dict:
    _continuous_evt.set()
    _upd(continuous_mode=False)
    return {"success": True, "message": "Continuous training stop requested"}

