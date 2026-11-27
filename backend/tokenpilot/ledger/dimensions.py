"""Get-or-create for dimension rows. All dimension SQL lives here."""

from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tokenpilot.ledger.models import DimApiKey, DimDate, DimModel, DimScope
from tokenpilot.sources.records import KeyRef, ScopeRef


def date_key_of(day: date) -> int:
    return day.year * 10_000 + day.month * 100 + day.day


class Dimensions:
    """Resolves dimension keys for one import, caching what it has already seen."""

    def __init__(self, session: Session, tenant_id: str):
        self.session = session
        self.tenant_id = tenant_id
        self._dates: set[int] = set()
        self._models: dict[str, dict[str, int]] = {}  # provider -> name -> model_key
        self._unmapped_keys: set[int] = set()
        self.unmapped_models: set[tuple[str, str]] = set()

    def date_key(self, day: date) -> int:
        key = date_key_of(day)
        if key not in self._dates:
            iso = day.isocalendar()
            self.session.execute(
                insert(DimDate)
                .values(
                    date_key=key,
                    date=day,
                    year=day.year,
                    month=day.month,
                    iso_week=iso.week,
                    weekday=iso.weekday,
                    is_weekend=iso.weekday >= 6,
                )
                .on_conflict_do_nothing()
            )
            self._dates.add(key)
        return key

    def model_key(self, provider: str, raw_name: str) -> int:
        names = self._models.get(provider)
        if names is None:
            names = self._models[provider] = self._load_model_names(provider)
        if raw_name not in names:
            # Unknown name: keep the data, but make it visible instead of guessing a mapping.
            self.session.execute(
                insert(DimModel)
                .values(provider=provider, model_id=raw_name, is_mapped=False)
                .on_conflict_do_nothing(constraint="uq_dim_model_provider")
            )
            names[raw_name] = self.session.scalar(
                select(DimModel.model_key).where(
                    DimModel.provider == provider, DimModel.model_id == raw_name
                )
            )
            self._unmapped_keys.add(names[raw_name])
        key = names[raw_name]
        if key in self._unmapped_keys:
            self.unmapped_models.add((provider, raw_name))
        return key

    def api_key_key(self, provider: str, ref: KeyRef, seen_on: date) -> int:
        stmt = insert(DimApiKey).values(
            tenant_id=self.tenant_id,
            provider=provider,
            natural_id=ref.natural_id,
            key_id=ref.key_id,
            name=ref.name,
            status=ref.status,
            first_seen=seen_on,
            last_seen=seen_on,
        )
        # Enrich, never erase: a later export may know the id or status an earlier one lacked.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_dim_api_key_tenant_id",
            set_={
                "key_id": func.coalesce(stmt.excluded.key_id, DimApiKey.key_id),
                "name": func.coalesce(stmt.excluded.name, DimApiKey.name),
                "status": func.coalesce(stmt.excluded.status, DimApiKey.status),
                "first_seen": func.least(stmt.excluded.first_seen, DimApiKey.first_seen),
                "last_seen": func.greatest(stmt.excluded.last_seen, DimApiKey.last_seen),
            },
        ).returning(DimApiKey.api_key_key)
        return self.session.scalar(stmt)

    def enrich_key(self, provider: str, ref: KeyRef) -> None:
        """Update a key we already have (e.g. archived since); never creates one."""
        self.session.execute(
            update(DimApiKey)
            .where(
                DimApiKey.tenant_id == self.tenant_id,
                DimApiKey.provider == provider,
                DimApiKey.natural_id == ref.natural_id,
            )
            .values(
                key_id=func.coalesce(ref.key_id, DimApiKey.key_id),
                name=func.coalesce(ref.name, DimApiKey.name),
                status=func.coalesce(ref.status, DimApiKey.status),
            )
        )

    def scope_key(self, provider: str, ref: ScopeRef) -> int:
        stmt = insert(DimScope).values(
            tenant_id=self.tenant_id,
            provider=provider,
            kind=ref.kind,
            natural_id=ref.natural_id,
            name=ref.name,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_dim_scope_tenant_id",
            set_={"name": func.coalesce(stmt.excluded.name, DimScope.name)},
        ).returning(DimScope.scope_key)
        return self.session.scalar(stmt)

    def _load_model_names(self, provider: str) -> dict[str, int]:
        names: dict[str, int] = {}
        for model in self.session.scalars(select(DimModel).where(DimModel.provider == provider)):
            for name in (model.model_id, model.display_name, *model.aliases):
                if name:
                    names[name] = model.model_key
            if not model.is_mapped:
                self._unmapped_keys.add(model.model_key)
        return names
