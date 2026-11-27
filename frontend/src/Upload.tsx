import { useRef, useState } from "react";

import { type ImportResult, type Tenant, deleteData, uploadFiles } from "./api.ts";

interface Props {
  tenant: Tenant;
  onImported: () => void;
  onDeleted: () => void;
}

export function Upload({ tenant, onImported, onDeleted }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [results, setResults] = useState<ImportResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  const send = async (files: File[]) => {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const imported = await uploadFiles(tenant, files);
      setResults(imported);
      if (imported.some((r) => r.ok)) onImported();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
      <div>
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void send([...e.dataTransfer.files]);
          }}
          className={`flex min-h-48 cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed p-8 text-center transition-colors ${
            dragging ? "border-amber bg-amber/5" : "border-white/20 bg-tint hover:border-white/35"
          }`}
        >
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            multiple
            className="sr-only"
            onChange={(e) => void send([...(e.target.files ?? [])])}
          />
          <span className="font-semibold">{busy ? "Reading your exports…" : "Drop CSV exports here"}</span>
          <span className="mt-1 text-sm text-text-2">or click to choose files · several at once is fine</span>
          <span className="mt-4 text-xs text-text-3">
            The files are not stored, only the usage numbers in them.
          </span>
        </label>

        {error && <p className="mt-4 text-sm text-amber">{error}</p>}
        {results.length > 0 && (
          <ul className="mt-4 space-y-2 text-sm">
            {results.map((r) => (
              <li key={r.file} className="rounded-lg border border-hairline bg-tint px-4 py-3">
                <ResultLine result={r} />
              </li>
            ))}
          </ul>
        )}
        <DeleteData tenant={tenant} onDeleted={onDeleted} />
      </div>
      <ExportHelp />
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  openai_usage_csv: "OpenAI activity",
  openai_cost_csv: "OpenAI cost",
  anthropic_usage_csv: "Anthropic usage",
  anthropic_cost_csv: "Anthropic cost",
};

function ResultLine({ result: r }: { result: ImportResult }) {
  if (!r.ok) {
    return (
      <>
        <span className="font-semibold">✗ {r.file}</span>
        <p className="mt-1 text-text-2">{r.error}</p>
      </>
    );
  }
  const label = SOURCE_LABELS[r.source_type ?? ""] ?? r.source_type;
  return (
    <>
      <span className="font-semibold">✓ {r.file}</span>{" "}
      <span className="text-text-2">
        {r.already_imported
          ? "already imported, nothing changed"
          : `${label}: ${r.usage_rows ? `${r.usage_rows} usage rows` : `${r.cost_rows} cost rows`}`}
      </span>
      {!!r.rejected_count && (
        <p className="mt-1 text-text-3">
          {r.rejected_count} rows skipped, e.g. line {r.rejected?.[0]?.line}: {r.rejected?.[0]?.reason}
        </p>
      )}
      {!!r.unmapped_models?.length && (
        <p className="mt-1 text-text-3">
          Not in our price list yet (kept unpriced): {r.unmapped_models.join(", ")}
        </p>
      )}
    </>
  );
}

function DeleteData({ tenant, onDeleted }: { tenant: Tenant; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <button type="button" onClick={() => setConfirming(true)} className="mt-6 text-sm text-text-3 hover:text-text-1">
        Delete all imported data…
      </button>
    );
  }
  return (
    <div className="mt-6 flex flex-wrap items-center gap-3 text-sm">
      <span className="text-text-2">Delete every imported export and start over?</span>
      <button
        type="button"
        onClick={() => deleteData(tenant).then(() => {
          setConfirming(false);
          onDeleted();
        })}
        className="rounded-lg border border-amber px-3 py-1.5 font-semibold text-amber"
      >
        Delete
      </button>
      <button type="button" onClick={() => setConfirming(false)} className="text-text-2 hover:text-text-1">
        Cancel
      </button>
    </div>
  );
}

function ExportHelp() {
  return (
    <div className="rounded-2xl border border-hairline bg-tint p-6 text-sm leading-relaxed text-text-2">
      <h3 className="text-base font-semibold text-text-1">Which files?</h3>
      <p className="mt-3 font-semibold text-text-1">OpenAI · platform.openai.com → Usage → Export</p>
      <ul className="mt-1 list-disc space-y-1 pl-5">
        <li>
          <strong className="text-text-1">Activity</strong>: group by project, API key and model, 1-day
          interval. This is the important one.
        </li>
        <li>
          <strong className="text-text-1">Cost</strong>: group by line item and project, 1-day interval.
          Lets us check our numbers against your bill.
        </li>
      </ul>
      <p className="mt-4 font-semibold text-text-1">Anthropic · Console → Usage and Cost pages</p>
      <ul className="mt-1 list-disc space-y-1 pl-5">
        <li>
          <strong className="text-text-1">Usage</strong>: export CSV. Holds the token counts.
        </li>
        <li>
          <strong className="text-text-1">Cost</strong>: export CSV. Adds key ids and the billed amounts.
        </li>
      </ul>
      <p className="mt-4 text-text-3">The last 30 days give the most reliable monthly estimates.</p>
    </div>
  );
}
