"""HTTP API for the audit: /api/spend/...

Local tool: the tenant comes from a query parameter. A hosted, multi-user version would take
it from the authenticated user instead.
"""

import re
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.orm import Session

from tokenpilot.audit import build_audit
from tokenpilot.config import Settings
from tokenpilot.demo import DEMO_TENANT
from tokenpilot.ledger.importer import delete_tenant_data, import_csv
from tokenpilot.savings.model import AuditConfig
from tokenpilot.sources.http import SyncError
from tokenpilot.sources.records import UnsupportedFileError
from tokenpilot.sync import PROVIDERS, configured, sync, synced_through

bp = Blueprint("spend", __name__, url_prefix="/api/spend")

TENANT_PATTERN = re.compile(r"[a-z0-9_-]{1,40}")
MAX_FILES = 20


class ApiError(Exception):
    def __init__(self, status: int, type_: str, message: str, details: object = None):
        super().__init__(message)
        self.status, self.type, self.message, self.details = status, type_, message, details


@bp.errorhandler(ApiError)
def _api_error(exc: ApiError):
    body = {"error": {"type": exc.type, "message": exc.message, "details": exc.details}}
    return jsonify(body), exc.status


@bp.post("/imports")
def upload():
    """Multipart upload, field "files" (one or more CSV exports). Files are not stored."""
    tenant = _tenant()
    files = request.files.getlist("files")
    if not files:
        raise ApiError(400, "no_files", 'Attach one or more CSV exports as "files".')
    if len(files) > MAX_FILES:
        raise ApiError(400, "too_many_files", f"Upload at most {MAX_FILES} files at once.")

    results = []
    with _session() as session:
        for file in files:
            try:
                r = import_csv(session, tenant, file.read())
            except UnsupportedFileError as exc:
                session.rollback()
                results.append({"file": file.filename, "ok": False, "error": str(exc)})
                continue
            results.append(
                {
                    "file": file.filename,
                    "ok": True,
                    "already_imported": r.already_imported,
                    "provider": r.provider,
                    "source_type": r.source_type,
                    "usage_rows": r.usage_rows,
                    "cost_rows": r.cost_rows,
                    "unpriced_rows": r.unpriced_rows,
                    "rejected": [{"line": x.line, "reason": x.reason} for x in r.rejected[:20]],
                    "rejected_count": len(r.rejected),
                    "unmapped_models": r.unmapped_models,
                }
            )
    return jsonify({"results": results})


@bp.get("/audit")
def audit():
    """?tenant=&from=YYYY-MM-DD&to=YYYY-MM-DD&min_savings=1 — all optional."""
    tenant = _tenant()
    start, end = _date_param("from"), _date_param("to")
    if start and end and start > end:
        raise ApiError(400, "bad_period", '"from" must not be after "to".')
    cfg = AuditConfig(min_monthly_savings=_decimal_param("min_savings", Decimal("1")))
    with _session() as session:
        report = build_audit(session, tenant, start, end, cfg)
    if report is None:
        raise ApiError(404, "no_data", "No usage imported yet. Upload your exports first.")
    return jsonify(to_json(report))


@bp.delete("/data")
def delete_data():
    """Start over: remove everything imported for the tenant."""
    tenant = _tenant()
    with _session() as session:
        delete_tenant_data(session, tenant)
    return "", 204


@bp.get("/sync")
def sync_status():
    """Which providers have an admin key configured, and the newest day synced."""
    tenant = _tenant()
    available = configured(_settings())
    with _session() as session:
        providers = [
            {
                "provider": p,
                "configured": p in available,
                "synced_through": synced_through(session, tenant, p) if p in available else None,
            }
            for p in PROVIDERS
        ]
    return jsonify(to_json({"providers": providers}))


@bp.post("/sync")
def run_sync():
    """Pull new days from every configured provider (admin keys come from backend/.env)."""
    tenant = _tenant()
    if tenant == DEMO_TENANT:
        raise ApiError(400, "demo_tenant", "The demo company holds synthetic data only.")
    with _session() as session:
        try:
            results = sync(session, tenant, _settings())
        except SyncError as exc:
            raise ApiError(400, "not_configured", str(exc)) from exc
    body = []
    for r in results:
        item = asdict(r)
        item["rejected_count"] = len(r.rejected)
        item["rejected"] = [{"line": x.line, "reason": x.reason} for x in r.rejected[:20]]
        body.append(item)
    return jsonify(to_json({"results": body}))


# ---------------------------------------------------------------- helpers


def to_json(value):
    """Money stays exact: Decimal -> string. Dates -> ISO strings (Flask's default is RFC 822)."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(v) for v in value]
    return value


def _settings() -> Settings:
    return current_app.extensions["tokenpilot.settings"]


def _session() -> Session:
    return current_app.extensions["tokenpilot.sessions"]()


def _tenant() -> str:
    tenant = request.args.get("tenant", "local")
    if not TENANT_PATTERN.fullmatch(tenant):
        raise ApiError(400, "bad_tenant", "tenant: 1-40 characters of a-z, 0-9, _ or -.")
    return tenant


def _date_param(name: str) -> date | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ApiError(400, "bad_date", f"{name}: expected YYYY-MM-DD, got {raw!r}.") from exc


def _decimal_param(name: str, default: Decimal) -> Decimal:
    raw = request.args.get(name)
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ApiError(400, "bad_number", f"{name}: expected a number, got {raw!r}.") from exc
    if not value.is_finite() or value < 0:
        raise ApiError(400, "bad_number", f"{name}: expected a non-negative number.")
    return value
