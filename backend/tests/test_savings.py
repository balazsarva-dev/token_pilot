"""Savings rules on small synthetic ledgers, with exact expected dollar amounts.

The period is 30 days, so a monthly estimate equals the amount saved in the period.
"""

from datetime import date
from decimal import Decimal

from tokenpilot.pricing.cost import Rates, compute_cost
from tokenpilot.savings import rules
from tokenpilot.savings.engine import run_audit
from tokenpilot.savings.model import AuditConfig, KeyInfo, Ledger, ModelInfo, UsageFact
from tokenpilot.sources.records import TokenCounts

GPT54_MINI, GPT5_MINI, SONNET = 1, 2, 3
SUPPORT_KEY, OLD_KEY = 10, 11
RATES = {
    GPT54_MINI: Rates(
        input_uncached=Decimal("0.75"),
        input_cached=Decimal("0.075"),
        output=Decimal("4.50"),
        batch_discount=Decimal("0.5"),
    ),
    GPT5_MINI: Rates(
        input_uncached=Decimal("0.25"),
        input_cached=Decimal("0.025"),
        output=Decimal("2.00"),
        batch_discount=Decimal("0.5"),
    ),
    SONNET: Rates(
        input_uncached=Decimal("2"),
        input_cached=Decimal("0.10"),
        cache_write_short=Decimal("2.50"),
        cache_write_long=Decimal("4"),
        output=Decimal("10"),
        batch_discount=Decimal("0.5"),
    ),
}
CFG = AuditConfig(min_monthly_savings=Decimal(0))


def ledger(facts, keys=None) -> Ledger:
    return Ledger(
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        facts=facts,
        models={
            GPT54_MINI: ModelInfo("GPT-5.4 mini", cheaper_alternative_key=GPT5_MINI),
            GPT5_MINI: ModelInfo("GPT-5 mini"),
            SONNET: ModelInfo("Claude Sonnet 5.5"),
        },
        keys=keys or {SUPPORT_KEY: KeyInfo("support-prod"), OLD_KEY: KeyInfo("old-prototype")},
        scopes={20: "Support"},
        rates_on=lambda model_key, day: RATES.get(model_key),
    )


def fact(
    day=1, model=GPT54_MINI, key=SUPPORT_KEY, tier="standard", requests=1000, cost=None, **tokens
) -> UsageFact:
    counts = TokenCounts(**tokens)
    provider = "anthropic" if model == SONNET else "openai"
    if cost is None:
        cost = compute_cost(counts, RATES[model], tier)
    return UsageFact(
        date(2026, 9, day), provider, model, key, 20, tier, counts, requests, Decimal(cost)
    )


# A day of short support requests on GPT-5.4 mini: 1M input + 100k output = $0.75 + $0.45.
SHORT_REQUESTS = {"input_uncached": 1_000_000, "output": 100_000, "requests": 1000}


# ---------------------------------------------------------------- R1


def test_right_sizing_prices_the_same_tokens_on_the_cheaper_model():
    [finding] = rules.right_sizing(ledger([fact(**SHORT_REQUESTS)]), CFG)

    # $1.20 now vs $0.25 + $0.20 on GPT-5 mini
    assert finding.monthly_savings_usd == Decimal("0.75")
    assert finding.evidence["alternative_cost_usd"] == Decimal("0.45")
    assert finding.confidence == "medium"  # 1000 in / 100 out per request: short
    assert "1,000 requests averaging 1,000 input and 100 output tokens" in finding.explanation


def test_right_sizing_without_request_counts_is_low_confidence():
    [finding] = rules.right_sizing(
        ledger([fact(input_uncached=1_000_000, output=100_000, requests=None)]), CFG
    )

    assert finding.confidence == "low"
    assert "no request counts" in finding.explanation


def test_right_sizing_skips_models_without_a_cheaper_alternative():
    assert rules.right_sizing(ledger([fact(model=GPT5_MINI, **SHORT_REQUESTS)]), CFG) == []


# ---------------------------------------------------------------- R2


def test_batch_assumes_a_share_of_steady_traffic_can_wait():
    facts = [fact(day=d, **SHORT_REQUESTS) for d in range(1, 6)]  # 5 days x $1.20

    [finding] = rules.batch(ledger(facts), CFG)

    assert finding.monthly_savings_usd == Decimal("0.75")  # $6.00 x 25% x 50%
    assert finding.evidence["active_days"] == 5


def test_batch_skips_traffic_that_drops_on_weekends():
    # 2026-09-05/06 is a weekend: half the weekday spend -> people are waiting for answers.
    facts = [fact(day=d, **SHORT_REQUESTS) for d in (1, 2, 3, 4)]
    facts += [fact(day=d, cost="0.60", **SHORT_REQUESTS) for d in (5, 6)]

    assert rules.batch(ledger(facts), CFG) == []


def test_batch_needs_enough_active_days():
    facts = [fact(day=d, **SHORT_REQUESTS) for d in range(1, 5)]

    assert rules.batch(ledger(facts), CFG) == []


def test_batch_skips_traffic_already_mostly_on_batch():
    facts = [fact(day=d, **SHORT_REQUESTS) for d in range(1, 6)]
    facts += [fact(day=d, tier="batch", cost="2.00", **SHORT_REQUESTS) for d in range(1, 6)]

    assert rules.batch(ledger(facts), CFG) == []


# ---------------------------------------------------------------- R3


def test_caching_on_openai_has_no_write_premium():
    # 1M uncached input in 500 requests (2,000 tokens each), nothing cached.
    facts = [fact(input_uncached=1_000_000, requests=500)]

    [finding] = rules.caching(ledger(facts), CFG)

    # half cacheable = 500k; 5 of 6 are reads: 416,666.67 x ($0.75 - $0.075) / 1M
    assert finding.monthly_savings_usd == Decimal("0.28125")
    assert finding.confidence == "medium"
    assert "OpenAI caches automatically" in finding.explanation


def test_caching_on_anthropic_subtracts_cache_writes():
    facts = [fact(model=SONNET, input_uncached=1_000_000, requests=None)]

    [finding] = rules.caching(ledger(facts), CFG)

    # reads 416,666.67 x ($2 - $0.10) - writes 83,333.33 x ($2.50 - $2) = 750,000 / 1M
    assert finding.monthly_savings_usd == Decimal("0.75")
    assert finding.confidence == "low"  # no request counts
    assert "cache_control" in finding.explanation


def test_caching_skips_short_prompts_and_good_hit_ratios():
    short_prompts = fact(input_uncached=1_000_000, requests=2000)  # 500 tokens each
    well_cached = fact(input_uncached=700_000, input_cached=300_000, requests=500)

    assert rules.caching(ledger([short_prompts]), CFG) == []
    assert rules.caching(ledger([well_cached]), CFG) == []


# ---------------------------------------------------------------- R4


def test_anomaly_flags_a_day_far_above_the_usual():
    facts = [fact(day=d, cost="10", **SHORT_REQUESTS) for d in range(1, 13) if d != 6]
    facts.append(fact(day=6, cost="100", **SHORT_REQUESTS))

    [finding] = rules.anomalies(ledger(facts), CFG)

    assert finding.evidence["date"] == date(2026, 9, 6)
    assert finding.evidence["excess_usd"] == Decimal("90")
    assert finding.monthly_savings_usd is None
    assert "10.0× a typical day ($10.00)" in finding.explanation


def test_anomaly_ignores_steady_spend_and_short_histories():
    steady = [fact(day=d, cost="10", **SHORT_REQUESTS) for d in range(1, 13)]
    short = [fact(day=d, cost="10", **SHORT_REQUESTS) for d in range(1, 5)]
    short.append(fact(day=5, cost="100", **SHORT_REQUESTS))

    assert rules.anomalies(ledger(steady), CFG) == []
    assert rules.anomalies(ledger(short), CFG) == []


# ---------------------------------------------------------------- R5


def test_idle_key_is_reported_relative_to_the_end_of_the_data():
    facts = [fact(day=1, key=OLD_KEY, **SHORT_REQUESTS), fact(day=30, **SHORT_REQUESTS)]

    [finding] = rules.idle_keys(ledger(facts), CFG)

    assert finding.subject["key"] == "old-prototype"
    assert finding.evidence["idle_days"] == 29


def test_idle_key_skips_revoked_keys_and_keyless_usage():
    facts = [fact(day=1, key=OLD_KEY, **SHORT_REQUESTS), fact(day=30, **SHORT_REQUESTS)]
    revoked = {SUPPORT_KEY: KeyInfo("support-prod"), OLD_KEY: KeyInfo("old", status="inactive")}
    keyless = {SUPPORT_KEY: KeyInfo("support-prod"), OLD_KEY: KeyInfo("(none)", is_key=False)}

    assert rules.idle_keys(ledger(facts, revoked), CFG) == []
    assert rules.idle_keys(ledger(facts, keyless), CFG) == []


# ---------------------------------------------------------------- engine


def test_engine_counts_overlapping_savings_once_and_hides_small_ones():
    facts = [fact(day=d, **SHORT_REQUESTS) for d in range(1, 6)]

    result = run_audit(ledger(facts), AuditConfig(min_monthly_savings=Decimal("1")))

    # right-sizing $3.75 and batch $0.75 act on the same spend; batch is below $1 and hidden
    assert [f.rule for f in result.findings] == ["right_sizing"]
    assert result.likely_monthly_usd == Decimal("3.75")
    assert result.hidden_small == 1


def test_engine_takes_the_largest_saving_per_key_and_model():
    facts = [fact(day=d, **SHORT_REQUESTS) for d in range(1, 6)]

    result = run_audit(ledger(facts), CFG)

    assert [f.rule for f in result.findings] == ["right_sizing", "batch"]  # medium before low
    assert result.potential_monthly_usd == Decimal("3.75")  # not 3.75 + 0.75


def test_engine_headline_counts_only_medium_and_high_confidence():
    short = [fact(day=d, **SHORT_REQUESTS) for d in range(1, 6)]  # medium right-sizing $3.75
    unknown_size = [
        fact(day=d, key=OLD_KEY, input_uncached=1_000_000, output=100_000, requests=None)
        for d in range(1, 6)
    ]  # low-confidence right-sizing, another $3.75

    result = run_audit(ledger(short + unknown_size), CFG)

    assert result.likely_monthly_usd == Decimal("3.75")
    assert result.potential_monthly_usd == Decimal("7.50")
