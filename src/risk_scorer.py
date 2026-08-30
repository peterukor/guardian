"""
Deterministic Risk Scorer for Guardian.

Accepts a batch of per-file raw signals and returns a risk score (0–10) for
each file, along with the contributing factor values so the score is
explainable rather than a black box.

Scoring uses percentile rank (not min-max normalisation). Percentile rank is
endpoint-preserving: the lowest value in the batch gets exactly 0.0, the
highest gets exactly 1.0. This makes scores explainable in the passport
("highest fan-in of all analyzed files, 100th percentile") and keeps outlier
"god files" from compressing every other score toward zero.

The scorer always operates on a batch. Percentile rank is only meaningful
when computed across the full set of files being scored; scoring one file in
isolation returns 0.0 for all ranked signals (no distribution to compare).

Weights are defined as base values and renormalized at module load time.
Inactive signals (staleness in Phase 1) are zeroed before renormalization so
the active weights always sum to 1.0 and the maximum score stays a true 10.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Risk level classification thresholds
# ---------------------------------------------------------------------------

RISK_THRESHOLD_LOW_MEDIUM: float = 4.0   # score < 4.0  → LOW
RISK_THRESHOLD_MEDIUM_HIGH: float = 7.0  # score >= 7.0 → HIGH


def classify_risk_level(score: float) -> str:
    """
    Return a human-readable risk label for a given risk score.

    Thresholds:
        score <  4.0  → "LOW"
        4.0 <= score < 7.0  → "MEDIUM"
        score >= 7.0  → "HIGH"

    This function is deterministic and pure — it contains no I/O, no
    randomness, and no calls to an AI model.  The Agent must never determine
    the risk label itself; it must always call this function.
    """
    if score >= RISK_THRESHOLD_MEDIUM_HIGH:
        return "HIGH"
    if score >= RISK_THRESHOLD_LOW_MEDIUM:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Configuration — weights
# ---------------------------------------------------------------------------

# Base weights as defined in AGENTS.md. To activate staleness in Phase 2,
# change its _BASE_WEIGHTS entry from 0.0 to 0.10; renormalization is
# automatic — no other lines need editing.
_BASE_WEIGHTS: dict[str, float] = {
    "bug_fix_count":           0.40,
    "fan_in":                  0.30,
    "ownership_concentration": 0.20,
    "days_since_last_touch":   0.00,  # Phase 1: inactive
}

# Active weights: zero out inactive signals, then renormalize the rest so
# they sum to 1.0. This keeps the maximum possible score a true 10 regardless
# of which signals are enabled.
_active_total = sum(_BASE_WEIGHTS.values())
_WEIGHTS: dict[str, float] = (
    {k: v / _active_total for k, v in _BASE_WEIGHTS.items()}
    if _active_total > 0
    else {k: 0.0 for k in _BASE_WEIGHTS}
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FileSignals:
    """
    Raw input signals for one file. All values must be non-negative.

    ownership_concentration is already a 0–1 fraction (e.g. 0.73 for 73%).
    It is used directly in the formula without percentile-ranking, because
    concentration is already a relative measure (fraction of commits by the
    top author) — ranking it again would distort its meaning.

    days_since_last_touch is accepted but carries weight 0 in Phase 1.
    """
    path: str
    fan_in: int
    bug_fix_count: int
    ownership_concentration: float   # 0.0–1.0
    days_since_last_touch: int = 0   # optional in Phase 1; defaults to 0


@dataclass
class FileRiskResult:
    """
    Risk score and contributing factors for one file.

    risk_score is on a 0–10 scale. It is a relative engineering-risk indicator
    — never describe it as a probability of failure (e.g. 8.7/10 ≠ 87% chance
    of failure).

    percentile_* fields are the 0–1 percentile ranks for the ranked signals,
    included so a caller can see exactly what drove the score.
    """
    path: str
    risk_score: float
    fan_in: int
    bug_fix_count: int
    ownership_concentration: float
    days_since_last_touch: int
    percentile_bug_fix_count: float
    percentile_fan_in: float
    percentile_days_since_last_touch: float


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def _percentile_rank(value: float, all_values: list[float]) -> float:
    """
    Endpoint-preserving percentile rank: count(values < x) / (n - 1).

    The lowest value in the batch gets exactly 0.0; the highest gets exactly
    1.0. This is deliberately different from the exclusive definition
    (/ n, which never reaches 1.0) — the 0=lowest, 1=highest framing is
    far more explainable in the passport than a value like 0.667 for what
    is actually the maximum in the batch.

    Returns 0.0 for n=1 (required special case — n-1 would divide by zero).
    """
    n = len(all_values)
    if n <= 1:
        return 0.0
    below = sum(1 for v in all_values if v < value)
    return below / (n - 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_files(signals: list[FileSignals]) -> list[FileRiskResult]:
    """
    Compute risk scores for a batch of files and return one result per file,
    in the same order as the input.

    Percentile ranks are computed across the full batch, so the result for
    any one file depends on all others. Passing a single file returns a score
    of 0.0 — not because the file is safe, but because rank is meaningless
    without a distribution to compare against.
    """
    if not signals:
        return []

    # Extract the distributions needed for percentile ranking.
    all_bug_fix    = [float(s.bug_fix_count) for s in signals]
    all_fan_in     = [float(s.fan_in) for s in signals]
    all_staleness  = [float(s.days_since_last_touch) for s in signals]

    results = []
    for s in signals:
        p_bug   = _percentile_rank(float(s.bug_fix_count),          all_bug_fix)
        p_fanin = _percentile_rank(float(s.fan_in),                 all_fan_in)
        p_stale = _percentile_rank(float(s.days_since_last_touch),  all_staleness)

        # ownership_concentration is used directly (it is already a 0–1
        # relative measure), not percentile-ranked.
        score = 10.0 * (
            _WEIGHTS["bug_fix_count"]           * p_bug   +
            _WEIGHTS["fan_in"]                  * p_fanin +
            _WEIGHTS["ownership_concentration"] * s.ownership_concentration +
            _WEIGHTS["days_since_last_touch"]   * p_stale
        )

        # Clamp to [0.0, 10.0] to guard against any floating-point overshoot.
        score = max(0.0, min(10.0, score))

        results.append(FileRiskResult(
            path=s.path,
            risk_score=round(score, 2),
            fan_in=s.fan_in,
            bug_fix_count=s.bug_fix_count,
            ownership_concentration=s.ownership_concentration,
            days_since_last_touch=s.days_since_last_touch,
            percentile_bug_fix_count=round(p_bug, 4),
            percentile_fan_in=round(p_fanin, 4),
            percentile_days_since_last_touch=round(p_stale, 4),
        ))

    return results
