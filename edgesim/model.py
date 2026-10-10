"""Analytical interruption model (plan §9.3) — where T_guard comes from.

At the trigger the predictor says ~T_guard seconds remain; true remaining
life is T_guard + eps, eps ~ N(mu, sigma) measured on held-out traces.
The handover needs T_ho = t_attach + t_probe. The interruption is whatever
the lead time falls short by:

    I = max(0, T_ho - T_guard - eps)

With a = T_ho - T_guard - mu (expected shortfall of a normal):

    E[I]     = a * Phi(a/sigma) + sigma * phi(a/sigma)
    P(I > 0) = Phi(a/sigma)
    expected duplication per transition ~= T_guard + mu + T_hold

Choosing a target miss probability Phi(-z) gives

    T_guard = T_ho - mu + z * sigma

— the handover latency plus one prediction-error margin, derived rather
than tuned (plan §12 guideline 2). Missed detections (the predictor never
fires) add p_miss * (t_detect + T_ho) to E[I]. Validation: model E[I] vs
simulated interruption as T_guard is swept — agreement is a result.
"""
from __future__ import annotations

import math

import numpy as np

_Z = {0.80: 0.8416, 0.90: 1.2816, 0.95: 1.6449, 0.99: 2.3263}


def _phi_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _Phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def exp_interruption(t_guard: float, t_ho: float, mu: float = 0.0,
                     sigma: float = 1.0, p_miss: float = 0.0,
                     t_detect: float = 1.0) -> float:
    """E[I] in closed form (expected shortfall of a normal)."""
    if sigma <= 1e-9:
        base = max(0.0, t_ho - t_guard - mu)
    else:
        a = t_ho - t_guard - mu
        z = a / sigma
        base = a * _Phi(z) + sigma * _phi_pdf(z)
    return base + p_miss * (t_detect + t_ho)


def p_nonzero(t_guard: float, t_ho: float, mu: float = 0.0,
              sigma: float = 1.0) -> float:
    """P(interruption > 0) = Phi(a/sigma)."""
    if sigma <= 1e-9:
        return 1.0 if t_ho - t_guard - mu > 0 else 0.0
    return _Phi((t_ho - t_guard - mu) / sigma)


def dup_seconds(t_guard: float, mu: float = 0.0, t_hold: float = 1.0) -> float:
    """Expected duplication time per transition (plan §9.3)."""
    return t_guard + mu + t_hold


def recommend_t_guard(t_ho: float, mu: float = 0.0, sigma: float = 1.0,
                      p: float = 0.90) -> float:
    """T_guard = T_ho - mu + z*sigma for target miss probability 1-p."""
    return t_ho - mu + _Z.get(p, 1.2816) * sigma


def curve(t_ho: float, mu: float, sigma: float, t_guards=None):
    """(t_guard, E[I], dup_s) arrays for the model-vs-simulation figure."""
    if t_guards is None:
        t_guards = np.arange(0.0, 10.01, 0.25)
    EI = np.array([exp_interruption(g, t_ho, mu, sigma) for g in t_guards])
    dup = np.array([dup_seconds(g, mu) for g in t_guards])
    return np.asarray(t_guards), EI, dup
