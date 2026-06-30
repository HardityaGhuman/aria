// Color-coded by lifecycle (bg tint + text + ring outline), legible in both
// themes via semi-transparent fills over the Tailwind palette. Indexed reads as
// a calm "cucumber" green; never a flat gray.
const STYLE: Record<string, string> = {
  indexed: "bg-emerald-500/15 text-emerald-700 ring-1 ring-emerald-500/40 dark:text-emerald-300",
  processing: "bg-amber-500/15 text-amber-700 ring-1 ring-amber-500/40 dark:text-amber-300",
  queued: "bg-sky-500/15 text-sky-700 ring-1 ring-sky-500/40 dark:text-sky-300",
  failed: "bg-rose-500/15 text-rose-700 ring-1 ring-rose-500/40 dark:text-rose-300",
  unknown: "bg-zinc-400/15 text-zinc-600 ring-1 ring-zinc-400/40 dark:text-zinc-300",
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
