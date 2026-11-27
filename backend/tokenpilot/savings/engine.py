"""Runs all rules and sums the savings without double counting."""

from dataclasses import dataclass
from decimal import Decimal

from tokenpilot.savings.model import AuditConfig, Finding, Ledger
from tokenpilot.savings.rules import RULES

KIND_ORDER = {"saving": 0, "investigate": 1, "hygiene": 2}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class AuditResult:
    findings: list[Finding]  # shown: savings first, most certain first, then largest
    hidden_small: int  # savings findings below the configured minimum
    # Findings on the same spend are counted once (the largest), so the totals never double count.
    likely_monthly_usd: Decimal  # medium and high confidence only: the headline number
    potential_monthly_usd: Decimal  # all candidates, if every test succeeds


def run_audit(ledger: Ledger, cfg: AuditConfig) -> AuditResult:
    found = [finding for rule in RULES for finding in rule(ledger, cfg)]
    shown = [
        f
        for f in found
        if f.monthly_savings_usd is None or f.monthly_savings_usd >= cfg.min_monthly_savings
    ]

    shown.sort(
        key=lambda f: (
            KIND_ORDER[f.kind],
            CONFIDENCE_ORDER[f.confidence],
            -(f.monthly_savings_usd or 0),
        )
    )
    likely = [f for f in shown if f.confidence != "low"]
    return AuditResult(
        findings=shown,
        hidden_small=len(found) - len(shown),
        likely_monthly_usd=_without_overlap(likely),
        potential_monthly_usd=_without_overlap(shown),
    )


def _without_overlap(findings: list[Finding]) -> Decimal:
    """Right-sizing, batch and caching on the same key + model act on the same dollars:
    applying all three does not save the sum. Count only the largest per group."""
    best: dict[tuple, Decimal] = {}
    for f in findings:
        if f.monthly_savings_usd is not None and f.group is not None:
            best[f.group] = max(best.get(f.group, Decimal(0)), f.monthly_savings_usd)
    return sum(best.values(), Decimal(0))
