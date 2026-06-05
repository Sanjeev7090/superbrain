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

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ─── Architecture constants ───────────────────────────────────────────────────
STATE_DIM   = 38    # TradingEnv observation dims
ACTION_DIM  = 16    # TradingEnv action dims
LATENT_DIM  = 128   # DreamerV3 latent representation (↑ from 64 — richer market state)
HIDDEN_DIM  = 512   # MLP hidden size              (↑ from 256 — deeper feature extraction)
OBS_ENC_DIM = 128   # Obs encoder bottleneck        (new — proper DreamerV3 encoding)

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
}

_stop_evt    = threading.Event()
_train_thread: threading.Thread = None


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
        # Prior: p(z_t | z_{t-1}, a_{t-1})
        self.prior_net = nn.Sequential(
            nn.Linear(LATENT_DIM + ACTION_DIM, HIDDEN_DIM), nn.SiLU(),
            nn.Linear(HIDDEN_DIM, HIDDEN_DIM),               nn.SiLU(),
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
        self, prev_latent: torch.Tensor, action: torch.Tensor
    ) -> Tuple[torch.Tensor, Normal]:
        h    = torch.cat([prev_latent, action], dim=-1)
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


class ReplayBuffer:
    def __init__(self, capacity: int = 100_000):
        self._buf: deque = deque(maxlen=capacity)

    def push(self, obs, action, reward, next_obs, done):
        self._buf.append((
            np.asarray(obs,      dtype=np.float32),
            np.asarray(action,   dtype=np.float32),
            float(reward),
            np.asarray(next_obs, dtype=np.float32),
            float(done),
        ))

    def sample(self, n: int):
        batch           = random.sample(self._buf, min(n, len(self._buf)))
        obs, act, rew, nobs, done = zip(*batch)
        return (
            torch.FloatTensor(np.stack(obs)),
            torch.FloatTensor(np.stack(act)),
            torch.FloatTensor(rew).unsqueeze(1),
            torch.FloatTensor(np.stack(nobs)),
            torch.FloatTensor(done).unsqueeze(1),
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
        horizon:  int   = 20,      # ↑ from 15 — captures longer market cycles (≈1 trading month)
    ):
        self.encoder      = ObsEncoder()
        self.rssm         = RSSM()
        self.reward_pred  = RewardPredictor()
        self.actor        = Actor()
        self.critic       = Critic()
        self.target_critic= Critic()
        self.target_critic.load_state_dict(self.critic.state_dict())

        wm_params = (
            list(self.encoder.parameters())
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

    def act(self, obs: np.ndarray, deterministic: bool = False) -> np.ndarray:
        obs_t = torch.FloatTensor(obs).unsqueeze(0)
        with torch.no_grad():
            enc      = self.encoder(obs_t)
            # Use distribution mean for deterministic inference (rebalance/predict)
            z_dist   = self.rssm._to_dist(self.rssm.posterior_net(
                torch.cat([self._zero_lat(1), enc], dim=-1)
            ))
            z        = z_dist.mean if deterministic else z_dist.rsample()
            a_params = self.actor.net(z)
            a_mean, a_log_std = torch.chunk(a_params, 2, dim=-1)
            a        = torch.tanh(a_mean) if deterministic else self.actor(z)[0]
        return a.squeeze(0).cpu().numpy()

    # ---- world model update ----

    def update_world_model(self, batch) -> float:
        obs, act, rew, next_obs, _ = batch
        B  = obs.shape[0]
        z0 = self._zero_lat(B)

        enc_obs = self.encoder(obs)                              # (B, OBS_ENC_DIM)
        z_post, post_dist = self.rssm.compute_posterior(z0, enc_obs)
        _, prior_dist      = self.rssm.compute_prior(z0, act)

        # Reward reconstruction
        rew_loss = nn.MSELoss()(self.reward_pred(z_post), rew)

        # KL(posterior || prior) — free-bits 0.1 nats
        kl_loss = torch.distributions.kl_divergence(
            post_dist, prior_dist
        ).mean().clamp(min=0.1)

        loss = rew_loss + 0.5 * kl_loss

        self.wm_opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(
            list(self.encoder.parameters())
            + list(self.rssm.parameters())
            + list(self.reward_pred.parameters()),
            max_norm=10.0,
        )
        self.wm_opt.step()
        return float(loss.item())

    # ---- actor-critic update on imagined trajectories ----

    def update_actor_critic(
        self, batch, kronos_bonus: float = 0.0
    ) -> Tuple[float, float]:
        obs, act, rew, next_obs, _ = batch
        B  = obs.shape[0]
        z0 = self._zero_lat(B)

        with torch.no_grad():
            enc_obs   = self.encoder(obs)
            start_z, _ = self.rssm.compute_posterior(z0, enc_obs)

        # Imagined rollout
        latents, rewards, values, log_probs = [], [], [], []
        cur_z = start_z.detach()

        for _ in range(self.horizon):
            a_img, lp = self.actor(cur_z)            # gradients through actor

            with torch.no_grad():
                r_pred   = self.reward_pred(cur_z) + kronos_bonus
                v_pred   = self.target_critic(cur_z)
                next_z, _= self.rssm.compute_prior(cur_z, a_img)

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
            "rssm":          self.rssm.state_dict(),
            "reward_pred":   self.reward_pred.state_dict(),
            "actor":         self.actor.state_dict(),
            "critic":        self.critic.state_dict(),
            "target_critic": self.target_critic.state_dict(),
            # store dims so load can validate architecture
            "latent_dim":    LATENT_DIM,
            "hidden_dim":    HIDDEN_DIM,
            "horizon":       self.horizon,
        }, path)
        logger.info("DreamerV3 model saved → %s", path)

    def load(self, path: str):
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        # Validate architecture compatibility
        if ckpt.get("latent_dim", LATENT_DIM) != LATENT_DIM:
            raise ValueError(
                f"Saved model latent_dim={ckpt['latent_dim']} "
                f"!= current LATENT_DIM={LATENT_DIM}. Delete model and retrain."
            )
        self.encoder.load_state_dict(ckpt["encoder"])
        self.rssm.load_state_dict(ckpt["rssm"])
        self.reward_pred.load_state_dict(ckpt["reward_pred"])
        self.actor.load_state_dict(ckpt["actor"])
        self.critic.load_state_dict(ckpt["critic"])
        self.target_critic.load_state_dict(ckpt["target_critic"])
        logger.info("DreamerV3 model loaded ← %s", path)


# ─── Kronos Integration ───────────────────────────────────────────────────────

def _get_kronos_bonus(ticker: str) -> float:
    """
    Fetch Kronos price-forecast direction and return a reward-shaping bonus.
    Uses the already-loaded Kronos predictor from kronos_router (no new download).
    Returns 0.0 if Kronos is not loaded or any error occurs.
    """
    try:
        import sys
        import importlib

        # Resolve kronos_router from parent backend directory
        parent = str(Path(__file__).parent.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        kr = importlib.import_module("kronos_router")
        predictor = getattr(kr, "_PREDICTOR", None)
        if predictor is None:
            return 0.0

        import pandas as pd
        _fetch  = getattr(kr, "_fetch_history")
        _signal = getattr(kr, "_build_signal")
        _ftss   = getattr(kr, "_build_future_timestamps")

        hist = _fetch(ticker, "1d", 60)
        in_df = pd.DataFrame({
            "open":   hist["Open"].astype(float).values,
            "high":   hist["High"].astype(float).values,
            "low":    hist["Low"].astype(float).values,
            "close":  hist["Close"].astype(float).values,
            "volume": hist["Volume"].astype(float).values,
        })
        in_df["amount"] = in_df["volume"] * in_df["close"]

        x_ts      = pd.Series(pd.to_datetime(hist.index)).reset_index(drop=True)
        y_ts_idx  = _ftss(x_ts.iloc[-1], 5, "1d")
        y_ts      = pd.Series(y_ts_idx)

        pred_df = predictor.predict(
            df=in_df, x_timestamp=x_ts, y_timestamp=y_ts,
            pred_len=5, T=1.0, top_p=0.9, sample_count=1, verbose=False,
        )
        sig = _signal(hist, pred_df)

        direction  = sig.get("direction", "WAIT")
        confidence = sig.get("confidence", 50) / 100.0
        bonus      = confidence * 0.15 if direction != "WAIT" else 0.0
        return float(bonus)

    except Exception as exc:
        logger.debug("Kronos bonus skipped for %s: %s", ticker, exc)
        return 0.0


# ─── Background Training Worker ──────────────────────────────────────────────

BATCH_SIZE    = 64
WARMUP_STEPS  = 500
UPDATE_EVERY  = 4
KRONOS_REFRESH_EPISODES = 10   # re-query Kronos every N episodes


def _train_worker(mode: str, ticker: str, timesteps: int):
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

        # Load historical OHLCV data for historical/hybrid modes
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
        replay = ReplayBuffer(capacity=50_000)

        model_path = str(MODELS_DIR / f"dreamer_{ticker.replace('.', '_')}.pt")
        if os.path.exists(model_path):
            try:
                agent.load(model_path)
            except Exception as exc:
                logger.warning("Stale model removed (%s) — fresh start", exc)
                os.remove(model_path)

        kronos_bonus  = 0.0
        ep_rewards: List[float] = []
        total_steps   = 0
        episode       = 0
        info: Dict    = {}

        while total_steps < timesteps and not _stop_evt.is_set():
            obs, _ = env.reset()
            ep_reward  = 0.0
            done       = False

            # Refresh Kronos bonus periodically
            if episode % KRONOS_REFRESH_EPISODES == 0:
                kronos_bonus = _get_kronos_bonus(ticker)
                _upd(
                    kronos_active=(kronos_bonus > 0),
                    kronos_bonus=round(kronos_bonus, 4),
                )

            while not done and not _stop_evt.is_set():
                # Warm-up: random exploration before using actor
                if total_steps < WARMUP_STEPS or random.random() < 0.05:
                    action = env.action_space.sample()
                else:
                    action = agent.act(obs)

                next_obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated

                # Kronos reward shaping: bonus aligned with trade direction
                trade_sig   = info.get("trade_signal", 0.0)
                shaped_rew  = reward + kronos_bonus * float(np.sign(trade_sig + 1e-8))

                replay.push(obs, action, shaped_rew, next_obs, float(done))
                obs         = next_obs
                ep_reward  += reward
                total_steps += 1

                # Train every UPDATE_EVERY steps once buffer has data
                if len(replay) >= BATCH_SIZE and total_steps % UPDATE_EVERY == 0:
                    batch = replay.sample(BATCH_SIZE)
                    wm_l  = agent.update_world_model(batch)
                    a_l, c_l = agent.update_actor_critic(batch, kronos_bonus)
                    _upd(
                        timesteps_done=total_steps,
                        wm_loss=round(float(wm_l), 6),
                        actor_loss=round(float(a_l), 6),
                        critic_loss=round(float(c_l), 6),
                    )

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

        # ── Training complete ──
        if not _stop_evt.is_set():
            agent.save(model_path)
            _upd(status="running", model_saved=True)

            # Hybrid: keep fine-tuning with live data
            if mode == "hybrid":
                _upd(mode="live", status="training")
                env2 = TradingEnv(ticker=ticker)
                fine_tune_steps = max(5_000, timesteps // 5)
                ep_rewards2: List[float] = []
                total2 = 0
                while total2 < fine_tune_steps and not _stop_evt.is_set():
                    obs2, _ = env2.reset()
                    ep2 = 0.0
                    done2 = False
                    info2: Dict = {}
                    while not done2 and not _stop_evt.is_set():
                        if total2 < WARMUP_STEPS:
                            a2 = env2.action_space.sample()
                        else:
                            a2 = agent.act(obs2)
                        no2, r2, t2, tr2, info2 = env2.step(a2)
                        done2 = t2 or tr2
                        replay.push(obs2, a2, r2, no2, float(done2))
                        obs2 = no2
                        ep2 += r2
                        total2 += 1
                        if len(replay) >= BATCH_SIZE and total2 % UPDATE_EVERY == 0:
                            b2 = replay.sample(BATCH_SIZE)
                            wl2 = agent.update_world_model(b2)
                            al2, cl2 = agent.update_actor_critic(b2)
                            _upd(wm_loss=round(float(wl2), 6),
                                 actor_loss=round(float(al2), 6))
                    ep_rewards2.append(ep2)
                    avg10_2 = float(np.mean(ep_rewards2[-10:])) if ep_rewards2 else 0.0
                    _upd(avg_reward_10=avg10_2,
                         timesteps_done=timesteps + total2,
                         last_weights=info2.get("strategy_weights", _DEFAULT_WEIGHTS),
                         last_trade_signal=info2.get("trade_signal", 0.0))
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
    """Periodically re-trains model with fresh market data (live mode)."""
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
            for _ in range(1000):
                if _stop_evt.is_set():
                    break
                a = agent.act(obs)
                no, r, t, tr, _ = env.step(a)
                replay.push(obs, a, r, no, float(t or tr))
                obs = no if not (t or tr) else env.reset()[0]
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
        "kronos_active":    s.get("kronos_active",  False),
        "kronos_bonus":     s.get("kronos_bonus",   0.0),
        "message": (
            f"Ep {s['episode']} | "
            f"WM: {s.get('wm_loss', 0):.4f} | "
            f"Avg Rew: {s.get('avg_reward_10', 0):.4f}"
            + (" | Kronos Active" if s.get("kronos_active") else "")
        ),
    }
