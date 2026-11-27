// Types mirror the JSON of backend/tokenpilot/audit.py. Money is always an exact decimal string.

export type Money = string;
export type Tenant = "local" | "demo";

export interface Finding {
  rule: "right_sizing" | "batch" | "caching" | "anomaly" | "idle_key";
  kind: "saving" | "investigate" | "hygiene";
  title: string;
  explanation: string;
  confidence: "low" | "medium" | "high";
  monthly_savings_usd: Money | null;
  subject: Record<string, string>;
  evidence: Record<string, string | number | null>;
  assumptions: string[];
}

export interface SpendRow {
  provider: string;
  label: string;
  computed_usd: Money | null;
  input_tokens: string | number;
  output_tokens: string | number;
  unpriced_rows: number;
}

export interface Reconciliation {
  provider: string;
  days_compared: number;
  days_without_bill: number;
  days_unpriced: number;
  computed_usd: Money | null;
  billed_usd: Money | null;
  diff_pct: string | null;
}

export interface Audit {
  tenant: string;
  period: { start: string; end: string; days: number };
  spend: {
    total_usd: Money;
    monthly_run_rate_usd: Money;
    monthly_after_likely_usd: Money;
    by_provider: { provider: string; usd: Money }[];
    by_model: SpendRow[];
    by_key: SpendRow[];
    by_scope: SpendRow[];
  };
  savings: {
    likely_monthly_usd: Money;
    potential_monthly_usd: Money;
    findings: number;
    hidden_small: number;
  };
  findings: Finding[];
  data_quality: {
    reconciliation: Reconciliation[];
    unmapped_models: { provider: string; model_id: string }[];
    notes: string[];
  };
}

export interface ImportResult {
  file: string;
  ok: boolean;
  error?: string;
  already_imported?: boolean;
  provider?: string;
  source_type?: string;
  usage_rows?: number;
  cost_rows?: number;
  unpriced_rows?: number;
  rejected_count?: number;
  rejected?: { line: number; reason: string }[];
  unmapped_models?: string[];
}

export class ApiError extends Error {
  readonly type: string;
  readonly status: number;

  constructor(status: number, type: string, message: string) {
    super(message);
    this.status = status;
    this.type = type;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = body?.error ?? {};
    throw new ApiError(response.status, error.type ?? "error", error.message ?? response.statusText);
  }
  return body as T;
}

/** The audit, or null when the tenant has no data yet. */
export async function getAudit(tenant: Tenant, showSmall: boolean): Promise<Audit | null> {
  const params = new URLSearchParams({ tenant, min_savings: showSmall ? "0" : "1" });
  try {
    return await call<Audit>(`/api/spend/audit?${params}`);
  } catch (error) {
    if (error instanceof ApiError && error.type === "no_data") return null;
    throw error;
  }
}

export async function uploadFiles(tenant: Tenant, files: File[]): Promise<ImportResult[]> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const body = await call<{ results: ImportResult[] }>(`/api/spend/imports?tenant=${tenant}`, {
    method: "POST",
    body: form,
  });
  return body.results;
}

export function deleteData(tenant: Tenant): Promise<void> {
  return call(`/api/spend/data?tenant=${tenant}`, { method: "DELETE" });
}

export interface SyncProvider {
  provider: string;
  configured: boolean;
  synced_through: string | null;
}

export interface SyncResult {
  provider: string;
  start: string | null;
  end: string | null;
  ok: boolean;
  error: string | null;
  unchanged: boolean;
  usage_rows: number;
  cost_rows: number;
  unpriced_rows: number;
  rejected_count: number;
  unmapped_models: string[];
}

export async function getSyncStatus(tenant: Tenant): Promise<SyncProvider[]> {
  const body = await call<{ providers: SyncProvider[] }>(`/api/spend/sync?tenant=${tenant}`);
  return body.providers;
}

export async function runSync(tenant: Tenant): Promise<SyncResult[]> {
  const body = await call<{ results: SyncResult[] }>(`/api/spend/sync?tenant=${tenant}`, {
    method: "POST",
  });
  return body.results;
}
