"""Dimensional model: 2 facts + 5 dimensions + import lineage."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from tokenpilot.db import Base

MONEY = Numeric(20, 10)  # USD
RATE = Numeric(20, 10)  # USD per 1M tokens

PROVIDERS = ("openai", "anthropic")
TOKEN_TYPES = ("input_uncached", "input_cached", "cache_write_short", "cache_write_long", "output")


def _provider_check() -> CheckConstraint:
    return CheckConstraint(f"provider IN {PROVIDERS}", name="provider")


# ---------------------------------------------------------------- dimensions


class DimDate(Base):
    __tablename__ = "dim_date"

    date_key: Mapped[int] = mapped_column(Integer, primary_key=True)  # YYYYMMDD
    date: Mapped[date] = mapped_column(Date, unique=True)
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    iso_week: Mapped[int] = mapped_column(SmallInteger)
    weekday: Mapped[int] = mapped_column(SmallInteger)  # 1 = Monday … 7 = Sunday (ISO)
    is_weekend: Mapped[bool]


class DimModel(Base):
    __tablename__ = "dim_model"
    __table_args__ = (UniqueConstraint("provider", "model_id"), _provider_check())

    model_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str] = mapped_column(String(120))  # canonical API id
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Raw names seen in exports that mean this model (dated snapshots, display names).
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    family: Mapped[str | None] = mapped_column(String(60))
    size_tier: Mapped[str | None] = mapped_column(String(10))  # large / medium / small
    cheaper_alternative_key: Mapped[int | None] = mapped_column(ForeignKey("dim_model.model_key"))
    # False = created automatically from an unknown name in an export; needs review.
    is_mapped: Mapped[bool] = mapped_column(server_default="true")


class DimModelPrice(Base):
    """Slowly changing dimension (type 2): one row per model per validity period."""

    __tablename__ = "dim_model_price"
    __table_args__ = (
        UniqueConstraint("model_key", "valid_from"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
    )

    price_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_key: Mapped[int] = mapped_column(ForeignKey("dim_model.model_key"))
    # USD per 1M tokens. NULL = price not published -> usage of that type is unpriced.
    input_uncached: Mapped[Decimal | None] = mapped_column(RATE)
    input_cached: Mapped[Decimal | None] = mapped_column(RATE)
    cache_write_short: Mapped[Decimal | None] = mapped_column(RATE)
    cache_write_long: Mapped[Decimal | None] = mapped_column(RATE)
    output: Mapped[Decimal | None] = mapped_column(RATE)
    batch_discount: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))  # 0.5 = 50% off
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)  # exclusive; NULL = current
    source: Mapped[str] = mapped_column(Text)


class DimApiKey(Base):
    __tablename__ = "dim_api_key"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "natural_id"),
        _provider_check(),
    )

    api_key_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20))
    # Identity used for matching: OpenAI key id; Anthropic key name (its usage export has no id).
    # Empty string = "no key" (e.g. Console playground usage).
    natural_id: Mapped[str] = mapped_column(String(200))
    key_id: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str | None] = mapped_column(String(30))
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)


class DimScope(Base):
    """OpenAI project or Anthropic workspace."""

    __tablename__ = "dim_scope"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "natural_id"),
        _provider_check(),
    )

    scope_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))  # project / workspace
    natural_id: Mapped[str] = mapped_column(String(200))  # project id / workspace name
    name: Mapped[str | None] = mapped_column(String(200))


# ---------------------------------------------------------------- lineage


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = (UniqueConstraint("tenant_id", "file_sha256"), _provider_check())

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(40))  # e.g. openai_usage_csv
    file_sha256: Mapped[str] = mapped_column(String(64))
    rows_ok: Mapped[int]
    rows_rejected: Mapped[int]
    # Days an API sync covered (complete for them, even where there was no usage). NULL for files.
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- facts


class FctUsage(Base):
    """Token usage. Grain: tenant × day × model × API key × scope × pricing tier."""

    __tablename__ = "fct_usage"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "date_key", "model_key", "api_key_key", "scope_key", "pricing_tier"
        ),
        _provider_check(),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20))
    date_key: Mapped[int] = mapped_column(ForeignKey("dim_date.date_key"))
    model_key: Mapped[int] = mapped_column(ForeignKey("dim_model.model_key"))
    api_key_key: Mapped[int] = mapped_column(ForeignKey("dim_api_key.api_key_key"))
    scope_key: Mapped[int] = mapped_column(ForeignKey("dim_scope.scope_key"))
    # standard / batch are priced; anything else (fast, us_geo, priority, …) is kept but unpriced.
    pricing_tier: Mapped[str] = mapped_column(String(30))

    input_uncached: Mapped[int] = mapped_column(BigInteger)
    input_cached: Mapped[int] = mapped_column(BigInteger)
    cache_write_short: Mapped[int] = mapped_column(BigInteger)
    cache_write_long: Mapped[int] = mapped_column(BigInteger)
    output: Mapped[int] = mapped_column(BigInteger)
    requests: Mapped[int | None] = mapped_column(BigInteger)  # Anthropic exports have none

    computed_cost: Mapped[Decimal | None] = mapped_column(MONEY)  # NULL = unpriced
    price_key: Mapped[int | None] = mapped_column(ForeignKey("dim_model_price.price_key"))
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"))


class FctBilledCost(Base):
    """Official cost from the provider. Grain: tenant × day × scope × key × model × line item."""

    __tablename__ = "fct_billed_cost"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "date_key",
            "scope_key",
            "api_key_key",
            "model_key",
            "line_item",
            "token_type",
            postgresql_nulls_not_distinct=True,  # Postgres 15+
        ),
        _provider_check(),
        CheckConstraint(f"token_type IS NULL OR token_type IN {TOKEN_TYPES}", name="token_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(20))
    date_key: Mapped[int] = mapped_column(ForeignKey("dim_date.date_key"))
    scope_key: Mapped[int] = mapped_column(ForeignKey("dim_scope.scope_key"))
    api_key_key: Mapped[int | None] = mapped_column(ForeignKey("dim_api_key.api_key_key"))
    model_key: Mapped[int | None] = mapped_column(ForeignKey("dim_model.model_key"))
    line_item: Mapped[str] = mapped_column(String(60))  # token, web_search, file search tool calls…
    token_type: Mapped[str | None] = mapped_column(String(30))  # NULL for non-token items
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"))
