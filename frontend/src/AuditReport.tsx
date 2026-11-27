import { type ReactNode, useState } from "react";

import type { Audit, Finding, Reconciliation, SpendRow } from "./api.ts";
import { compareDecimal, count, percent, ratioPercent, sharePercent, usd } from "./format.ts";

interface Props {
  audit: Audit;
  isDemo: boolean;
  showSmall: boolean;
  onShowSmall: (value: boolean) => void;
}

export function AuditReport({ audit, isDemo, showSmall, onShowSmall }: Props) {
  return (
    <>
      <Summary audit={audit} isDemo={isDemo} />
      <Findings audit={audit} showSmall={showSmall} onShowSmall={onShowSmall} />
      <SpendBreakdown audit={audit} />
      <DataQuality audit={audit} />
    </>
  );
}

// ---------------------------------------------------------------- summary

export const PROVIDER_NAMES: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic" };

function Summary({ audit, isDemo }: { audit: Audit; isDemo: boolean }) {
  const { period, spend, savings } = audit;
  const likelyCount = audit.findings.filter(
    (f) => f.kind === "saving" && f.confidence !== "low",
  ).length;
  const savedShare = sharePercent(savings.likely_monthly_usd, spend.monthly_run_rate_usd);
  return (
    <section className="pt-14 pb-4">
      <h1 className="text-[36px] leading-[1.08] font-extrabold tracking-[-0.02em] md:text-[46px]">
        {isDemo ? "The demo company's AI bill, audited" : "Your AI bill, audited"}
      </h1>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1.5 text-text-2">
        <p>
          <span className="num">
            {period.start} – {period.end}
          </span>{" "}
          · {period.days} days ·{" "}
          {spend.by_provider
            .map((p) => `${PROVIDER_NAMES[p.provider] ?? p.provider} ${usd(p.usd)}`)
            .join(" · ")}
        </p>
        <BillMatch reconciliation={audit.data_quality.reconciliation} />
      </div>

      <div className="mt-10 overflow-hidden rounded-2xl border border-hairline bg-tint">
        <div className="grid md:grid-cols-3">
          <Stat label="Monthly AI spend" value={usd(spend.monthly_run_rate_usd)} note="at the pace of this period" />
          <Stat
            label="Likely savings"
            value={usd(savings.likely_monthly_usd)}
            aside={savedShare ? `${savedShare}%` : undefined}
            tone="text-amber"
            note={
              likelyCount
                ? `per month, from ${likelyCount} medium- or high-confidence finding${likelyCount > 1 ? "s" : ""}`
                : "no medium- or high-confidence findings yet"
            }
          />
          <Stat
            label="After likely fixes"
            value={usd(spend.monthly_after_likely_usd)}
            tone="text-teal"
            note="per month"
          />
        </div>
        {savedShare !== null && savedShare > 0 && (
          <div className="px-6 pb-6">
            <div
              role="img"
              aria-label={`${savedShare}% of monthly spend is likely savings`}
              className="flex h-2 overflow-hidden rounded bg-white/5"
            >
              <div className="bg-teal/55" style={{ width: `${100 - Math.min(savedShare, 100)}%` }} />
              <div className="bg-amber" style={{ width: `${Math.min(savedShare, 100)}%` }} />
            </div>
          </div>
        )}
      </div>
      {usd(savings.potential_monthly_usd) !== usd(savings.likely_monthly_usd) && (
        <p className="mt-4 max-w-[80ch] text-sm text-text-3">
          Up to <span className="num text-text-2">{usd(savings.potential_monthly_usd)}</span> per
          month if every candidate below passes testing. Findings on the same spend are counted once.
        </p>
      )}
    </section>
  );
}

function Stat({
  label,
  value,
  aside,
  note,
  tone = "text-text-1",
}: {
  label: string;
  value: string;
  aside?: string;
  note: string;
  tone?: string;
}) {
  return (
    <div className="border-hairline p-6 not-last:border-b md:not-last:border-r md:not-last:border-b-0">
      <div className="text-[13px] font-medium text-text-3">{label}</div>
      <div className={`mt-2 flex items-baseline gap-2.5 ${tone}`}>
        <span className="num text-[28px] font-extrabold">{value}</span>
        {aside && <span className="num text-[15px]">{aside}</span>}
      </div>
      <div className="mt-1 text-sm text-text-3">{note}</div>
    </div>
  );
}

// Within this, computed costs count as matching the bill.
const MATCH_THRESHOLD_PCT = "1";

function BillMatch({ reconciliation }: { reconciliation: Reconciliation[] }) {
  const gaps = reconciliation
    .filter((r) => r.days_compared > 0 && r.diff_pct !== null)
    .map((r) => (r.diff_pct as string).replace(/^-/, ""));
  if (gaps.length === 0) return null;
  const worst = gaps.reduce((a, b) => (compareDecimal(b, a) > 0 ? b : a));
  if (compareDecimal(worst, MATCH_THRESHOLD_PCT) > 0) {
    return (
      <p>
        Computed costs differ from your bill by up to <span className="num">{percent(worst)}</span>
      </p>
    );
  }
  return (
    <p className="inline-flex items-center gap-1.5">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
        strokeLinecap="round" strokeLinejoin="round" className="text-teal" aria-hidden="true">
        <path d="M20 6 9 17l-5-5" />
      </svg>
      Computed costs match your bill <span className="num">({percent(worst)})</span>
    </p>
  );
}

// ---------------------------------------------------------------- findings

const CONFIDENCE_LABELS: Record<Finding["confidence"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence: test first",
};

const KIND_LABELS: Record<Finding["kind"], string> = {
  saving: "Saving",
  investigate: "Investigate",
  hygiene: "Security hygiene",
};

function Findings({ audit, showSmall, onShowSmall }: Omit<Props, "isDemo">) {
  const hidden = audit.savings.hidden_small;
  // The bars next to monthly savings are relative to the largest one. A spike's excess is a
  // one-day amount, so it gets no bar.
  const largest = audit.findings
    .map((f) => f.monthly_savings_usd)
    .reduce<string | null>((max, a) => (a !== null && (max === null || compareDecimal(a, max) > 0) ? a : max), null);
  return (
    <section className="mt-16">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <h2 className="text-[28px] leading-[1.15] font-extrabold md:text-[36px]">What to look at</h2>
        <label className="flex items-center gap-2 text-sm text-text-2">
          <input
            type="checkbox"
            checked={showSmall}
            onChange={(e) => onShowSmall(e.target.checked)}
            className="accent-amber"
          />
          Show savings under $1 per month{!showSmall && hidden ? ` (${hidden} hidden)` : ""}
        </label>
      </div>

      {audit.findings.length === 0 ? (
        <p className="mt-6 rounded-2xl border border-hairline bg-tint p-8 text-text-2">
          Nothing worth your time in this period
          {hidden ? `; ${hidden} smaller savings are hidden` : ""}. Spend is low, or already well
          optimized.
        </p>
      ) : (
        <ol className="mt-6 divide-y divide-hairline rounded-2xl border border-hairline bg-tint">
          {audit.findings.map((finding, i) => (
            <FindingRow key={i} index={i + 1} finding={finding} largest={largest} />
          ))}
        </ol>
      )}
    </section>
  );
}

function FindingRow({ index, finding, largest }: { index: number; finding: Finding; largest: string | null }) {
  const subject = [
    PROVIDER_NAMES[finding.subject.provider] ?? finding.subject.provider,
    finding.subject.key,
    finding.subject.model,
    finding.subject.scope,
  ].filter(Boolean);
  return (
    <li className="grid gap-4 p-6 md:grid-cols-[2rem_1fr_auto] md:gap-6">
      <span className="num hidden pt-0.5 text-sm text-text-3 md:block">{String(index).padStart(2, "0")}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap gap-2 text-xs font-medium text-text-2">
          <Chip>{KIND_LABELS[finding.kind]}</Chip>
          <Chip>{CONFIDENCE_LABELS[finding.confidence]}</Chip>
        </div>
        <h3 className="mt-3 text-base font-semibold">{finding.title}</h3>
        <p className="num mt-1 text-xs break-words text-text-3">{subject.join(" · ")}</p>
        <p className="mt-3 max-w-[72ch] text-[15px] leading-relaxed text-text-2">{finding.explanation}</p>
        <Details finding={finding} />
      </div>
      <Amount finding={finding} largest={largest} />
    </li>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return <span className="rounded-md border border-hairline px-2 py-0.5">{children}</span>;
}

// The amount a finding is about: a monthly saving, or a spike's excess over a typical day.
function findingAmount(finding: Finding): string | null {
  if (finding.monthly_savings_usd !== null) return finding.monthly_savings_usd;
  const excess = finding.evidence.excess_usd;
  return typeof excess === "string" ? excess : null;
}

function Amount({ finding, largest }: { finding: Finding; largest: string | null }) {
  const amount = findingAmount(finding);
  if (amount === null) return <div />;
  const isSaving = finding.monthly_savings_usd !== null;
  // Amber is money you will likely get back; everything else stays neutral.
  const likely = finding.kind === "saving" && finding.confidence !== "low";
  const share = isSaving ? sharePercent(amount, largest) : null;
  return (
    <div className="md:text-right">
      <div className={`num text-2xl font-extrabold ${likely ? "text-amber" : "text-text-2"}`}>
        {isSaving ? usd(amount) : `+${usd(amount)}`}
      </div>
      <div className="text-sm text-text-3">{isSaving ? "per month" : "above a typical day"}</div>
      {share !== null && (
        <div className="mt-2.5 h-1 w-28 rounded-sm bg-white/5 md:ml-auto" aria-hidden="true">
          <div className={`h-1 rounded-sm ${likely ? "bg-amber" : "bg-text-3"}`} style={{ width: `${share}%` }} />
        </div>
      )}
    </div>
  );
}

const EVIDENCE_LABELS: Record<string, string> = {
  period_cost_usd: "Cost in this period",
  alternative_cost_usd: "Same tokens on the cheaper model",
  alternative_model: "Cheaper model",
  requests: "Requests",
  avg_input_tokens: "Average input tokens per request",
  avg_output_tokens: "Average output tokens per request",
  standard_cost_usd: "Standard-price cost in this period",
  active_days: "Days with traffic",
  weekend_ratio: "Weekend spend vs weekdays",
  batch_discount: "Batch discount",
  cache_hit_ratio: "Input read from cache",
  uncached_input_tokens: "Uncached input tokens",
  date: "Date",
  cost_usd: "Cost that day",
  typical_day_usd: "Typical day",
  excess_usd: "Above a typical day",
  last_used: "Last used",
  idle_days: "Days without use",
};

function evidenceValue(key: string, value: string | number): string {
  if (key.endsWith("_usd")) return usd(String(value));
  if (key.endsWith("_ratio") || key.endsWith("_discount")) return ratioPercent(String(value));
  if (typeof value === "number" || /^\d+$/.test(value)) return count(value);
  return value;
}

function Details({ finding }: { finding: Finding }) {
  const evidence = Object.entries(finding.evidence).filter(
    (entry): entry is [string, string | number] => entry[1] !== null,
  );
  return (
    <details className="group mt-4 text-sm">
      <summary className="cursor-pointer list-none font-semibold text-text-2 hover:text-text-1">
        <span className="inline-block transition-transform group-open:rotate-90">›</span> Why we think so
      </summary>
      <div className="mt-3 grid gap-6 md:grid-cols-2">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
          {evidence.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-text-3">{EVIDENCE_LABELS[key] ?? key}</dt>
              <dd className="num text-text-1">{evidenceValue(key, value)}</dd>
            </div>
          ))}
        </dl>
        {finding.assumptions.length > 0 && (
          <div>
            <div className="text-text-3">Assumptions</div>
            <ul className="mt-1.5 list-disc space-y-1 pl-5 text-text-2">
              {finding.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

// ---------------------------------------------------------------- spend breakdown

const GROUPINGS = [
  { key: "by_model", label: "Model", column: "Model" },
  { key: "by_key", label: "API key", column: "API key" },
  { key: "by_scope", label: "Project / workspace", column: "Project / workspace" },
] as const;

function SpendBreakdown({ audit }: { audit: Audit }) {
  const [active, setActive] = useState<(typeof GROUPINGS)[number]>(GROUPINGS[0]);
  const rows: SpendRow[] = audit.spend[active.key];
  return (
    <section className="mt-20">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <h2 className="text-[26px] font-extrabold">Where the money goes</h2>
        <div className="flex gap-1 rounded-lg border border-hairline p-1" role="tablist">
          {GROUPINGS.map((g) => (
            <button
              key={g.key}
              type="button"
              role="tab"
              aria-selected={g.key === active.key}
              onClick={() => setActive(g)}
              className={`rounded-md px-3 py-1.5 text-sm font-semibold ${
                g.key === active.key ? "bg-white/10 text-text-1" : "text-text-2 hover:text-text-1"
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-6 overflow-x-auto rounded-2xl border border-hairline bg-tint">
        <table className="w-full text-sm sm:min-w-[640px]">
          <thead className="text-left text-text-3">
            <tr className="border-b border-hairline">
              <th className="px-5 py-3 font-medium">{active.column}</th>
              <th className="px-5 py-3 font-medium">Provider</th>
              <th className="w-[30%] px-5 py-3 font-medium">Share of spend</th>
              <th className="px-5 py-3 text-right font-medium">Cost in period</th>
              <th className="hidden px-5 py-3 text-right font-medium sm:table-cell">Input tokens</th>
              <th className="hidden px-5 py-3 text-right font-medium sm:table-cell">Output tokens</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {rows.map((row) => (
              <tr key={`${row.provider}-${row.label}`}>
                <td className="px-5 py-3 font-medium break-all">{row.label}</td>
                <td className="px-5 py-3 text-text-2">{PROVIDER_NAMES[row.provider] ?? row.provider}</td>
                <td className="px-5 py-3">
                  <ShareBar part={row.computed_usd} whole={audit.spend.total_usd} />
                </td>
                <td className="num px-5 py-3 text-right">
                  {usd(row.computed_usd)}
                  {row.unpriced_rows > 0 && <span className="text-text-3"> + unpriced</span>}
                </td>
                <td className="num hidden px-5 py-3 text-right text-text-2 sm:table-cell">
                  {count(row.input_tokens)}
                </td>
                <td className="num hidden px-5 py-3 text-right text-text-2 sm:table-cell">
                  {count(row.output_tokens)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ShareBar({ part, whole }: { part: string | null; whole: string }) {
  const share = sharePercent(part, whole);
  if (part === null || share === null) return <span className="text-text-3">—</span>;
  // A small but real cost should not read as 0%.
  const label = share === 0 && compareDecimal(part, "0") > 0 ? "<1%" : `${share}%`;
  return (
    <div className="flex items-center gap-2.5">
      <div className="h-1.5 flex-1 rounded-sm bg-white/5" aria-hidden="true">
        <div className="h-1.5 rounded-sm bg-text-2" style={{ width: `${Math.min(share, 100)}%` }} />
      </div>
      <span className="num w-10 text-right text-text-2">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------- data quality

function DataQuality({ audit }: { audit: Audit }) {
  const { reconciliation, unmapped_models, notes } = audit.data_quality;
  return (
    <section className="mt-20">
      <h2 className="text-[26px] font-extrabold">Can you trust these numbers?</h2>
      <p className="mt-2 max-w-[65ch] text-text-2">
        Costs are computed from your token counts and the providers' published prices, then checked
        against your bill where a cost export was uploaded.
      </p>
      <ul className="mt-6 divide-y divide-hairline rounded-2xl border border-hairline bg-tint">
        {reconciliation.map((r) => (
          <li key={r.provider} className="flex flex-col gap-1 p-5 sm:flex-row sm:justify-between">
            <span className="font-semibold">{PROVIDER_NAMES[r.provider] ?? r.provider}</span>
            {r.days_compared > 0 ? (
              <span className="text-text-2">
                computed <span className="num text-text-1">{usd(r.computed_usd)}</span> vs billed{" "}
                <span className="num text-text-1">{usd(r.billed_usd)}</span> on {r.days_compared} days
                (<span className="num">{percent(r.diff_pct)}</span>)
                {r.days_unpriced > 0 && (
                  <span className="text-text-3">
                    {" "}
                    · {r.days_unpriced} {r.days_unpriced === 1 ? "day" : "days"} with unpriced models
                    left out
                  </span>
                )}
              </span>
            ) : (
              <span className="text-text-3">No cost export uploaded to compare with</span>
            )}
          </li>
        ))}
        {unmapped_models.length > 0 && (
          <li className="p-5 text-text-2">
            Not in our price list yet (left unpriced, never counted as free):{" "}
            <span className="num">{unmapped_models.map((m) => m.model_id).join(", ")}</span>
          </li>
        )}
        {notes.map((note) => (
          <li key={note} className="p-5 text-text-2">
            {note}
          </li>
        ))}
      </ul>
    </section>
  );
}
