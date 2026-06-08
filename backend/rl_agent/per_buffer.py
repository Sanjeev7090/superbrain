"""
Prioritized Experience Replay (PER) Buffer — DreamerV3 upgrade.

Replaces uniform ReplayBuffer with SumTree-based priority sampling.
  - Priority = |TD error| + epsilon  (higher error → sampled more)
  - Importance-sampling (IS) weights correct gradient bias
  - O(log n) push/sample via SumTree
"""

import random
import numpy as np
import torch
from typing import Optional, Tuple

# ─── SumTree ────────────────────────────────────────────────────────────────

class SumTree:
    """Binary sum-tree for O(log n) priority updates and sampling."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._tree  = np.zeros(2 * capacity - 1, dtype=np.float64)
        self._data  = [None] * capacity
        self._ptr   = 0
        self._size  = 0

    def _propagate(self, idx: int, delta: float):
        parent = (idx - 1) // 2
        self._tree[parent] += delta
        if parent != 0:
            self._propagate(parent, delta)

    def _retrieve(self, idx: int, s: float) -> int:
        left  = 2 * idx + 1
        right = left + 1
        if left >= len(self._tree):
            return idx
        return self._retrieve(left, s) if s <= self._tree[left] else self._retrieve(right, s - self._tree[left])

    def add(self, priority: float, data):
        leaf = self._ptr + self.capacity - 1
        self._data[self._ptr] = data
        self.update(leaf, priority)
        self._ptr  = (self._ptr + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def update(self, leaf_idx: int, priority: float):
        delta = priority - self._tree[leaf_idx]
        self._tree[leaf_idx] = priority
        self._propagate(leaf_idx, delta)

    def get(self, s: float) -> Tuple[int, float, object]:
        leaf_idx  = self._retrieve(0, s)
        data_idx  = leaf_idx - (self.capacity - 1)
        return leaf_idx, self._tree[leaf_idx], self._data[data_idx]

    @property
    def total(self) -> float:
        return float(self._tree[0])

    def __len__(self) -> int:
        return self._size


# ─── PER Buffer ─────────────────────────────────────────────────────────────

KRONOS_FEAT_DIM = 7   # must match dreamer_trainer.py

class PrioritizedReplayBuffer:
    """
    Prioritized Experience Replay with importance-sampling correction.

    Hyperparams:
      alpha: priority exponent (0 = uniform, 1 = full priority)
      beta:  IS weight exponent (annealed 0.4 → 1.0)
      eps:   small constant to keep all priorities > 0
    """

    def __init__(
        self,
        capacity : int   = 100_000,
        alpha    : float = 0.6,
        beta     : float = 0.4,
        beta_max : float = 1.0,
        eps      : float = 1e-6,
        beta_anneal_steps: int = 100_000,
    ):
        self._tree  = SumTree(capacity)
        self.alpha  = alpha
        self.beta   = beta
        self.beta_max= beta_max
        self.eps    = eps
        self.beta_step = (beta_max - beta) / max(beta_anneal_steps, 1)
        self._max_priority = 1.0   # for new transitions

        # Stats
        self.total_pushes  = 0
        self.total_samples = 0
        self.priority_sum  = 0.0

    # ── push ──

    def push(
        self,
        obs, action, reward, next_obs, done,
        kronos_feat: Optional[np.ndarray] = None,
    ):
        kf = kronos_feat if kronos_feat is not None else np.zeros(KRONOS_FEAT_DIM, dtype=np.float32)
        transition = (
            np.asarray(obs,      dtype=np.float32),
            np.asarray(action,   dtype=np.float32),
            float(reward),
            np.asarray(next_obs, dtype=np.float32),
            float(done),
            np.asarray(kf,       dtype=np.float32),
        )
        priority = self._max_priority ** self.alpha
        self._tree.add(priority, transition)
        self.total_pushes += 1

    # ── sample ──

    def sample(self, n: int):
        n = min(n, len(self._tree))
        batch_leaves, is_weights_raw, transitions = [], [], []

        segment = self._tree.total / n
        for i in range(n):
            lo, hi  = segment * i, segment * (i + 1)
            s       = random.uniform(lo, hi)
            leaf_idx, priority, data = self._tree.get(s)
            if data is None:
                continue
            batch_leaves.append(leaf_idx)
            is_weights_raw.append(max(priority, self.eps))
            transitions.append(data)

        # IS weights
        min_prob   = min(is_weights_raw) / (self._tree.total + 1e-10)
        max_weight = (min_prob * len(self._tree)) ** (-self.beta)
        is_weights = np.array([
            ((w / (self._tree.total + 1e-10)) * len(self._tree)) ** (-self.beta) / max_weight
            for w in is_weights_raw
        ], dtype=np.float32)

        obs, act, rew, nobs, done, kfeat = zip(*transitions)

        # Anneal beta
        self.beta = min(self.beta_max, self.beta + self.beta_step * n)
        self.total_samples += n

        return (
            torch.FloatTensor(np.stack(obs)),
            torch.FloatTensor(np.stack(act)),
            torch.FloatTensor(rew).unsqueeze(1),
            torch.FloatTensor(np.stack(nobs)),
            torch.FloatTensor(done).unsqueeze(1),
            torch.FloatTensor(np.stack(kfeat)),
            torch.FloatTensor(is_weights).unsqueeze(1),  # extra: IS weights
            batch_leaves,                                 # extra: leaf indices for update
        )

    # ── priority update after TD error ──

    def update_priorities(self, leaf_indices, td_errors: np.ndarray):
        for idx, err in zip(leaf_indices, td_errors):
            priority = (abs(float(err)) + self.eps) ** self.alpha
            self._tree.update(idx, priority)
            self._max_priority = max(self._max_priority, priority)

    # ── stats ──

    def stats(self) -> dict:
        n = len(self._tree)
        return {
            "size":          n,
            "capacity":      self._tree.capacity,
            "total_pushes":  self.total_pushes,
            "total_samples": self.total_samples,
            "beta":          round(self.beta, 4),
            "max_priority":  round(self._max_priority, 6),
            "fill_pct":      round(n / self._tree.capacity * 100, 1),
        }

    def __len__(self) -> int:
        return len(self._tree)
