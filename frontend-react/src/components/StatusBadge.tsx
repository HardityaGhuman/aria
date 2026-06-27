const STYLE: Record<string, string> = {
  indexed: "bg-badge-all text-text-secondary",
  processing: "bg-badge-manager/15 text-badge-manager",
  queued: "bg-canvas text-text-tertiary",
  failed: "bg-badge-hr/15 text-badge-hr",
  unknown: "bg-canvas text-text-tertiary",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[12px] font-medium ${
        STYLE[status] ?? STYLE.unknown
      }`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
      {status[0].toUpperCase() + status.slice(1)}
    </span>
  );
}
