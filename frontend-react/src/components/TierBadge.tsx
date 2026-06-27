type Tier = "all" | "manager" | "hr_only";

const LABEL: Record<Tier, string> = { all: "All", manager: "Manager", hr_only: "HR Only" };
const STYLE: Record<Tier, string> = {
  all: "bg-badge-all text-text-secondary",
  manager: "bg-badge-manager/15 text-badge-manager",
  hr_only: "bg-badge-hr/15 text-badge-hr",
};

export function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono text-[12px] uppercase tracking-wide ${STYLE[tier]}`}
    >
      {tier === "hr_only" && (
        <span aria-label="locked" className="text-[10px]">
          🔒
        </span>
      )}
      {LABEL[tier]}
    </span>
  );
}
