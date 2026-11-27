"""Command line entry point: python -m tokenpilot.cli <command> (see `make help`)."""

import argparse
import sys
import textwrap
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tokenpilot.audit import build_audit
from tokenpilot.config import load_settings
from tokenpilot.db import make_engine, make_session_factory
from tokenpilot.demo import DEMO_TENANT, build_demo
from tokenpilot.ledger import reports
from tokenpilot.ledger.catalog import DEFAULT_CATALOG, seed_catalog
from tokenpilot.ledger.importer import delete_tenant_data, import_csv, import_parsed
from tokenpilot.ledger.prices import reprice
from tokenpilot.savings.model import AuditConfig
from tokenpilot.sources.http import SyncError
from tokenpilot.sources.records import UnsupportedFileError
from tokenpilot.sync import PROVIDERS, sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tokenpilot")
    parser.add_argument("--tenant", default="local", help="tenant id (default: local)")
    commands = parser.add_subparsers(dest="command", required=True)

    seed = commands.add_parser("seed-prices", help="load the model catalog and price list")
    seed.add_argument("--file", type=Path, default=DEFAULT_CATALOG)

    imp = commands.add_parser("import", help="import OpenAI / Anthropic CSV exports")
    imp.add_argument("files", nargs="+", type=Path)

    commands.add_parser("reprice", help="recompute cost of stored usage")
    commands.add_parser("report", help="spend by model / key / scope, and reconciliation")

    audit = commands.add_parser("audit", help="savings findings for a period (default: all data)")
    audit.add_argument("--from", dest="start", type=date.fromisoformat)
    audit.add_argument("--to", dest="end", type=date.fromisoformat)
    audit.add_argument(
        "--min-savings",
        type=Decimal,
        default=AuditConfig.min_monthly_savings,
        help="hide savings below this many USD per month (default: 1)",
    )

    demo = commands.add_parser(
        "demo", help=f"replace tenant {DEMO_TENANT!r} with 30 days of synthetic usage"
    )
    demo.add_argument("--end", type=date.fromisoformat, help="last day (default: yesterday)")

    sync = commands.add_parser("sync", help="pull usage and cost from the providers' admin APIs")
    sync.add_argument("--provider", choices=sorted(PROVIDERS), action="append")
    sync.add_argument(
        "--days", type=int, help="re-sync this many days up to yesterday (default: only new days)"
    )

    args = parser.parse_args(argv)
    settings = load_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    with sessions() as session:
        if args.command == "seed-prices":
            r = seed_catalog(session, args.file)
            print(
                f"{r.models} models, {r.prices} price versions loaded; "
                f"{r.merged_unmapped} placeholder models merged; {r.repriced_rows} rows re-priced"
            )
        elif args.command == "import":
            return _import(session, args.tenant, args.files)
        elif args.command == "reprice":
            print(f"{reprice(session, args.tenant)} rows re-priced")
            session.commit()
        elif args.command == "report":
            _report(session, args.tenant)
        elif args.command == "audit":
            cfg = AuditConfig(min_monthly_savings=args.min_savings)
            return _audit(session, args.tenant, args.start, args.end, cfg)
        elif args.command == "demo":
            # Always the demo tenant (replaced on every run), never the caller's real data.
            end = args.end or datetime.now(UTC).date() - timedelta(days=1)
            delete_tenant_data(session, DEMO_TENANT)
            for parsed, source_id in build_demo(end):
                r = import_parsed(session, DEMO_TENANT, parsed, source_id)
                print(f"demo {parsed.provider}: {r.usage_rows} usage rows (tenant {DEMO_TENANT!r})")
        elif args.command == "sync":
            return _sync(session, args.tenant, settings, args.provider, args.days)
    return 0


def _sync(session, tenant: str, settings, providers, days) -> int:
    if tenant == DEMO_TENANT:
        print(f"Tenant {DEMO_TENANT!r} holds synthetic data only; sync into your own tenant.")
        return 1
    try:
        results = sync(session, tenant, settings, providers, days)
    except SyncError as exc:
        print(f"✗ {exc}")
        return 1
    for r in results:
        if not r.ok:
            print(f"✗ {r.provider}: {r.error}")
            continue
        window = f"{r.start} to {r.end}"
        if r.unchanged:
            print(f"= {r.provider} {window}: no changes since the last sync")
            continue
        print(
            f"✓ {r.provider} {window}: {r.usage_rows} usage + {r.cost_rows} cost rows, "
            f"{r.unpriced_rows} unpriced, {len(r.rejected)} rejected"
        )
        for rej in r.rejected[:10]:
            print(f"    {rej.reason}")
        if r.unmapped_models:
            print(f"    ! unknown models (not in catalog): {', '.join(r.unmapped_models)}")
    return 0 if all(r.ok for r in results) else 1


def _import(session, tenant: str, files: list[Path]) -> int:
    failed = False
    for path in files:
        try:
            r = import_csv(session, tenant, path.read_bytes())
        except UnsupportedFileError as exc:
            print(f"✗ {path.name}: {exc}")
            failed = True
            continue
        if r.already_imported:
            print(f"= {path.name}: already imported (batch {r.batch_id})")
            continue
        print(
            f"✓ {path.name}: {r.source_type}, {r.usage_rows} usage + {r.cost_rows} cost rows, "
            f"{r.unpriced_rows} unpriced, {r.skipped_empty} empty days skipped, "
            f"{len(r.rejected)} rejected"
        )
        for rej in r.rejected[:10]:
            print(f"    line {rej.line}: {rej.reason}")
        if r.unmapped_models:
            print(f"    ! unknown models (not in catalog): {', '.join(r.unmapped_models)}")
    return 1 if failed else 0


def _report(session, tenant: str) -> None:
    for grouping in ("model", "key", "scope"):
        rows = reports.spend_by(session, tenant, grouping)
        print(f"\nSpend by {grouping}")
        header = f"  {'provider':<10} {grouping:<34} {'computed $':>12}"
        print(f"{header} {'input tok':>11} {'output tok':>11}")
        for r in rows:
            note = f"  ({r['unpriced_rows']} unpriced)" if r["unpriced_rows"] else ""
            print(
                f"  {r['provider']:<10} {str(r['label'])[:34]:<34} {_usd(r['computed_usd']):>12} "
                f"{r['input_tokens']:>11,} {r['output_tokens']:>11,}{note}"
            )

    print(
        "\nDaily reconciliation (computed from tokens vs billed by the provider, token items only)"
    )
    header = f"  {'provider':<10} {'date':<11} {'computed $':>12} {'billed $':>12}"
    print(f"{header} {'diff $':>12} {'diff %':>8}")
    for r in reports.reconciliation(session, tenant):
        pct = "" if r["diff_pct"] is None else f"{r['diff_pct']}%"
        if r["unpriced_rows"]:
            pct += f"  ({r['unpriced_rows']} unpriced rows, computed $ is partial)"
        print(
            f"  {r['provider']:<10} {r['date']!s:<11} {_usd(r['computed_usd']):>12} "
            f"{_usd(r['billed_usd']):>12} {_usd(r['diff_usd']):>12} {pct:>8}"
        )

    unmapped = reports.unmapped_models(session)
    if unmapped:
        names = ", ".join(f"{m['provider']}/{m['model_id']}" for m in unmapped)
        print(f"\n! Models not in the catalog (usage unpriced): {names}")


def _audit(session, tenant: str, start, end, cfg: AuditConfig) -> int:
    report = build_audit(session, tenant, start, end, cfg)
    if report is None:
        print(f"No usage for tenant {tenant!r}. Import exports first (make import).")
        return 1
    period, spend, savings = report["period"], report["spend"], report["savings"]
    print(f"\nAI bill audit — {period['start']} to {period['end']} ({period['days']} days)")
    print(
        f"Spend: ${spend['total_usd']:,.2f} in the period, "
        f"run rate ${spend['monthly_run_rate_usd']:,.2f} per month"
    )
    hidden = f", {savings['hidden_small']} below ${cfg.min_monthly_savings}/month hidden"
    print(
        f"Savings: likely ${savings['likely_monthly_usd']:,.2f} per month, "
        f"up to ${savings['potential_monthly_usd']:,.2f} if every candidate passes testing "
        f"({savings['findings']} opportunities{hidden if savings['hidden_small'] else ''})"
    )

    wrap = textwrap.TextWrapper(width=96, initial_indent="     ", subsequent_indent="     ")
    for i, f in enumerate(report["findings"], start=1):
        amount = (
            f"${f['monthly_savings_usd']:,.2f}/month"
            if f["monthly_savings_usd"] is not None
            else ""
        )
        print(f"\n{i:>2}. [{f['kind']} · {f['confidence']}] {f['title']}  {amount}")
        print(f"     {' · '.join(f['subject'].values())}")
        print(wrap.fill(f["explanation"]))
        for assumption in f["assumptions"]:
            print(wrap.fill(f"Assumes: {assumption}"))

    quality = report["data_quality"]
    print("\nData quality")
    for r in quality["reconciliation"]:
        if r["days_compared"]:
            print(
                f"  {r['provider']}: computed ${r['computed_usd']:,.2f} vs billed "
                f"${r['billed_usd']:,.2f} on {r['days_compared']} days ({r['diff_pct']}%)"
                + (
                    f", {r['days_unpriced']} day(s) with unpriced models left out"
                    if r["days_unpriced"]
                    else ""
                )
            )
        else:
            print(f"  {r['provider']}: no billing export to compare with")
    for note in quality["notes"]:
        print(textwrap.fill(note, width=96, initial_indent="  - ", subsequent_indent="    "))
    return 0


def _usd(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.6f}"


if __name__ == "__main__":
    sys.exit(main())
