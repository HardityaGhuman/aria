import { useState } from "react";
import type { Source } from "../lib/api/schemas";
import { TierBadge } from "./TierBadge";

type Tier = "all" | "manager" | "hr_only";
const TIERS: Tier[] = ["all", "manager", "hr_only"];
const asTier = (v: string | null | undefined): Tier =>
  TIERS.includes(v as Tier) ? (v as Tier) : "all";

// Discreet, per-response citations. Collapsed by default — a quiet line under
// the answer that expands into the chunk list for that specific reply.
export function ResponseSources({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 text-[12px] text-text-tertiary hover:text-text-secondary"
      >
        <span aria-hidden>◇</span>
        {sources.length} source{sources.length > 1 ? "s" : ""}
        <span aria-hidden className="text-[10px]">
          {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div className="mt-2 flex flex-col gap-1.5 border-l-2 border-marigold/40 pl-3">
          {sources.map((s, i) => (
            <div key={`${s.document_id}-${i}`} className="flex items-center gap-2 text-[12px]">
              <span className="font-mono text-text-ink">{s.file}</span>
              {s.section && <span className="text-text-tertiary">· {s.section}</span>}
              <TierBadge tier={asTier(s.source_type)} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
