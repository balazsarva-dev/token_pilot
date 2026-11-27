"""Synthetic usage of a fictional company, to show what an audit finds. Never real data.

Each scenario is built so that one savings rule has something to find. Deterministic:
the same end date and seed always give the same numbers.
"""

import hashlib
import random
from datetime import date, timedelta

from tokenpilot.sources.records import KeyRef, ParsedFile, ScopeRef, TokenCounts, UsageRecord

DEMO_TENANT = "demo"


def build_demo(end: date, days: int = 30, seed: int = 7) -> list[tuple[ParsedFile, str]]:
    """(records, source id) per provider, ready for ledger.importer.import_parsed."""
    rng = random.Random(seed)  # noqa: S311 - demo numbers, not security
    openai = ParsedFile(source_type="demo", provider="openai")
    anthropic = ParsedFile(source_type="demo", provider="anthropic")
    spike_day = days - 9

    for i in range(days):
        day = end - timedelta(days=days - 1 - i)
        weekend = day.isoweekday() >= 6

        def jitter(base: int) -> int:
            return int(base * rng.uniform(0.85, 1.15))

        # Support chatbot: many short requests on a mid-price model -> right-sizing.
        _add(
            openai,
            day,
            "gpt-5.4-mini",
            _openai_key("support-bot-prod"),
            _project("Support Bot"),
            jitter(22_000 if weekend else 40_000),
            avg_input=900,
            avg_output=120,
            cached_pct=5,
        )
        # Nightly reports: steady long prompts, nothing cached, standard tier -> batch + caching.
        _add(
            openai,
            day,
            "gpt-5.4-mini",
            _openai_key("nightly-reports"),
            _project("Analytics"),
            jitter(2_000),
            avg_input=6_000,
            avg_output=800,
            cached_pct=2,
        )
        # A prototype key that stopped being used -> idle key.
        if i < 10:
            _add(
                openai,
                day,
                "gpt-4o-mini",
                _openai_key("prototype-2025"),
                _project("Sandbox"),
                jitter(300),
                avg_input=1_500,
                avg_output=300,
            )
        # Product assistant: large contexts, almost no cache reads, one bad day
        # -> caching, right-sizing, anomaly. Console exports have no request counts.
        _add(
            anthropic,
            day,
            "claude-sonnet-5-5",
            KeyRef("assistant-prod", name="assistant-prod"),
            _workspace("Product"),
            jitter(1_800 if weekend else 3_000) * (6 if i == spike_day else 1),
            avg_input=12_000,
            avg_output=600,
            cached_pct=3,
            write_pct=1,
            requests_known=False,
        )
        # Evaluations already on the Batch API -> nothing to find.
        _add(
            anthropic,
            day,
            "claude-haiku-5-5",
            KeyRef("evals", name="evals"),
            _workspace("Research"),
            jitter(5_000),
            avg_input=2_000,
            avg_output=200,
            tier="batch",
            requests_known=False,
        )

    return [
        (parsed, _source_id(parsed.provider, end, days, seed)) for parsed in (openai, anthropic)
    ]


def _add(
    parsed: ParsedFile,
    day: date,
    model: str,
    key: KeyRef,
    scope: ScopeRef,
    requests: int,
    *,
    avg_input: int,
    avg_output: int,
    cached_pct: int = 0,
    write_pct: int = 0,
    tier: str = "standard",
    requests_known: bool = True,
) -> None:
    prompt = requests * avg_input
    cached = prompt * cached_pct // 100
    written = prompt * write_pct // 100
    parsed.usage.append(
        UsageRecord(
            provider=parsed.provider,
            day=day,
            model_name=model,
            api_key=key,
            scope=scope,
            pricing_tier=tier,
            tokens=TokenCounts(
                input_uncached=prompt - cached - written,
                input_cached=cached,
                cache_write_short=written,
                output=requests * avg_output,
            ),
            requests=requests if requests_known else None,
        )
    )


def _openai_key(name: str) -> KeyRef:
    return KeyRef(natural_id=f"key_demo_{name}", key_id=f"key_demo_{name}", name=name)


def _project(name: str) -> ScopeRef:
    return ScopeRef(
        kind="project", natural_id=f"proj_demo_{name.lower().replace(' ', '_')}", name=name
    )


def _workspace(name: str) -> ScopeRef:
    return ScopeRef(kind="workspace", natural_id=name, name=name)


def _source_id(provider: str, end: date, days: int, seed: int) -> str:
    return hashlib.sha256(f"demo:{provider}:{end}:{days}:{seed}".encode()).hexdigest()
