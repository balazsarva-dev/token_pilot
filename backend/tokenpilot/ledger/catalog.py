"""Load the reviewed model catalog + price list (pricing/prices.toml) into the dimensions."""

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tokenpilot.ledger.models import DimModel, DimModelPrice, FctBilledCost, FctUsage
from tokenpilot.ledger.prices import reprice

DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "pricing" / "prices.toml"
RATE_FIELDS = ("input_uncached", "input_cached", "cache_write_short", "cache_write_long", "output")


@dataclass
class SeedResult:
    models: int
    prices: int
    merged_unmapped: int
    repriced_rows: int


def seed_catalog(session: Session, path: Path = DEFAULT_CATALOG) -> SeedResult:
    with path.open("rb") as f:
        catalog = tomllib.load(f)["model"]

    model_keys: dict[tuple[str, str], int] = {}
    for m in catalog:
        stmt = insert(DimModel).values(
            provider=m["provider"],
            model_id=m["model_id"],
            display_name=m.get("display_name"),
            aliases=m.get("aliases", []),
            family=m.get("family"),
            size_tier=m.get("size_tier"),
            is_mapped=True,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_dim_model_provider",
            set_={
                c: stmt.excluded[c]
                for c in ("display_name", "aliases", "family", "size_tier", "is_mapped")
            },
        ).returning(DimModel.model_key)
        model_keys[(m["provider"], m["model_id"])] = session.scalar(stmt)

    merged = 0
    for m in catalog:
        key = model_keys[(m["provider"], m["model_id"])]
        names = [n for n in (m.get("display_name"), *m.get("aliases", [])) if n]
        merged += _merge_unmapped(session, m["provider"], names, key)
        alternative = m.get("cheaper_alternative")
        alternative_key = model_keys[(m["provider"], alternative)] if alternative else None
        session.execute(
            update(DimModel)
            .where(DimModel.model_key == key)
            .values(cheaper_alternative_key=alternative_key)
        )

    price_rows = 0
    for m in catalog:
        versions = sorted(m.get("price", []), key=lambda p: p["valid_from"])
        for i, p in enumerate(versions):
            valid_to = versions[i + 1]["valid_from"] if i + 1 < len(versions) else None
            values = {
                "model_key": model_keys[(m["provider"], m["model_id"])],
                **{r: Decimal(p[r]) if r in p else None for r in RATE_FIELDS},
                "batch_discount": Decimal(p["batch_discount"]) if "batch_discount" in p else None,
                "valid_from": p["valid_from"],
                "valid_to": valid_to,
                "source": p["source"],
            }
            stmt = insert(DimModelPrice).values(**values)
            session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_dim_model_price_model_key",
                    set_={
                        k: stmt.excluded[k] for k in values if k not in ("model_key", "valid_from")
                    },
                )
            )
            price_rows += 1

    session.flush()
    repriced = reprice(session)
    session.commit()
    return SeedResult(len(catalog), price_rows, merged, repriced)


def _merge_unmapped(session: Session, provider: str, names: list[str], target_key: int) -> int:
    """Facts imported before a model was in the catalog point to an auto-created, unmapped
    dim_model row. Move them to the real model, then delete the placeholder.
    On a grain collision the fact from the newer import wins."""
    unmapped = session.scalars(
        select(DimModel.model_key).where(
            DimModel.provider == provider,
            DimModel.model_id.in_(names),
            DimModel.is_mapped.is_(False),
        )
    ).all()
    for old_key in unmapped:
        _move_facts(session, FctUsage, "uq_fct_usage_tenant_id", old_key, target_key)
        _move_facts(session, FctBilledCost, "uq_fct_billed_cost_tenant_id", old_key, target_key)
        session.execute(delete(DimModel).where(DimModel.model_key == old_key))
    return len(unmapped)


def _move_facts(session: Session, table, constraint: str, old_key: int, new_key: int) -> None:
    columns = [c.name for c in table.__table__.columns if c.name != "id"]
    source = select(
        *[
            func.cast(new_key, table.model_key.type) if c == "model_key" else table.__table__.c[c]
            for c in columns
        ]
    ).where(table.model_key == old_key)
    stmt = insert(table).from_select(columns, source)
    updatable = [c for c in columns if c not in _grain_columns(table)]
    stmt = stmt.on_conflict_do_update(
        constraint=constraint,
        set_={c: stmt.excluded[c] for c in updatable},
        where=table.import_batch_id < stmt.excluded.import_batch_id,
    )
    session.execute(stmt)
    session.execute(delete(table).where(table.model_key == old_key))


def _grain_columns(table) -> set[str]:
    constraint = next(
        c for c in table.__table__.constraints if c.name and str(c.name).startswith("uq_")
    )
    return {col.name for col in constraint.columns}
