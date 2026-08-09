from dataclasses import dataclass


@dataclass(frozen=True)
class TrustResult:
    score: float
    action: str


def risk_action(score: float) -> str:
    if score >= 75:
        return "Approve"
    if score >= 55:
        return "Delay"
    if score >= 30:
        return "Verify"
    return "Block"


def update_score(current_score: float, fraud_probability: float, alpha: float = 0.25) -> TrustResult:
    if not 0 <= current_score <= 100 or not 0 <= fraud_probability <= 1 or not 0 < alpha <= 1:
        raise ValueError("score, probability, or alpha is outside its valid range")
    target = 100 * (1 - fraud_probability)
    score = round(max(0, min(100, (1 - alpha) * current_score + alpha * target)), 2)
    return TrustResult(score, risk_action(score))


def convergence_steps(
    current_score: float, fraud_probability: float, tolerance: float = 5,
    alpha: float = 0.25, maximum: int = 100,
) -> int:
    """Updates required to get within tolerance of a steady repeated prediction."""
    if not 0 < tolerance <= 100:
        raise ValueError("tolerance must be between 0 and 100")
    target = 100 * (1 - fraud_probability)
    for step in range(maximum + 1):
        if abs(current_score - target) <= tolerance:
            return step
        current_score = update_score(current_score, fraud_probability, alpha).score
    raise RuntimeError(f"DWTS did not converge within {maximum} updates")
