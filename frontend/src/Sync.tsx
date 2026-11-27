import { useEffect, useState } from "react";

import { type SyncProvider, type SyncResult, type Tenant, getSyncStatus, runSync } from "./api.ts";
import { PROVIDER_NAMES } from "./AuditReport.tsx";

// Shown only when admin keys are configured in backend/.env; otherwise CSV upload is the way in.
export function SyncBar({ tenant, onSynced }: { tenant: Tenant; onSynced: () => void }) {
  const [providers, setProviders] = useState<SyncProvider[]>([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<SyncResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSyncStatus(tenant)
      .then(setProviders)
      .catch(() => setProviders([]));
  }, [tenant, results]);

  const connected = providers.filter((p) => p.configured);
  if (connected.length === 0) return null;

  const sync = async () => {
    setBusy(true);
    setError(null);
    try {
      const synced = await runSync(tenant);
      setResults(synced);
      if (synced.some((r) => r.ok && !r.unchanged)) onSynced();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const lastDays = connected.map((p) => p.synced_through).filter((d): d is string => d !== null);
  const through = lastDays.length ? lastDays.sort()[0] : null;

  return (
    <div className="mt-6 rounded-xl border border-hairline bg-tint px-5 py-3 text-sm">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span className="text-text-2">
          <strong className="text-text-1">Connected:</strong>{" "}
          {connected.map((p) => PROVIDER_NAMES[p.provider] ?? p.provider).join(" · ")}
          {through ? ` · data through ${shortDate(through)}` : " · not synced yet"}
        </span>
        <button
          type="button"
          disabled={busy}
          onClick={() => void sync()}
          className="self-start rounded-lg bg-amber px-4 py-1.5 font-semibold text-ink disabled:opacity-60 sm:self-auto"
        >
          {busy ? "Syncing…" : "Sync now"}
        </button>
      </div>
      {error && <p className="mt-2 text-amber">{error}</p>}
      {results.length > 0 && (
        <ul className="mt-2 space-y-1 text-text-2">
          {results.map((r) => (
            <li key={r.provider}>{resultLine(r)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function resultLine(r: SyncResult): string {
  const name = PROVIDER_NAMES[r.provider] ?? r.provider;
  if (!r.ok) return `✗ ${name}: ${r.error}`;
  const window = r.start && r.end ? `${shortDate(r.start)} – ${shortDate(r.end)}` : "";
  if (r.unchanged) return `✓ ${name}: no changes (${window})`;
  if (r.usage_rows === 0 && r.cost_rows === 0) return `✓ ${name}: ${window} checked, no usage`;
  let line = `✓ ${name}: ${window} updated, ${r.usage_rows} usage and ${r.cost_rows} billing rows`;
  if (r.unmapped_models.length) line += `. Not in our price list yet: ${r.unmapped_models.join(", ")}`;
  if (r.rejected_count) line += `. ${r.rejected_count} rows skipped`;
  return line;
}

function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
