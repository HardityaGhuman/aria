import type { Source } from "../lib/api/schemas";
import { TierBadge } from "./TierBadge";

type Tier = "all" | "manager" | "hr_only";
const TIERS: Tier[] = ["all", "manager", "hr_only"];

function asTier(value: string | null | undefined): Tier {
  return TIERS.includes(value as Tier) ? (value as Tier) : "all";
}

export function SourcesPanel({ sources }: { sources: Source[] }) {
  return (
    <div>
      <div className="mb-4 text-[15px] font-semibold">Sources</div>
      {sources.length === 0 ? (
        <div className="text-[13px] text-text-tertiary">
          Document references and citations will appear here during your conversation.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {sources.map((s, i) => (
            <div
              key={`${s.document_id}-${i}`}
              className="rounded-lg border border-hairline bg-surface p-3"
            >
              <div className="font-mono text-[12px] text-text-ink">{s.file}</div>
              {s.section && <div className="text-[12px] text-text-secondary">{s.section}</div>}
              <div className="mt-2">
                <TierBadge tier={asTier(s.source_type)} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
