import { AppShell } from "../components/AppShell";

// Placeholder for Plan 2 screens (Preferences, HR Documents). Keeps the nav
// links live so they don't dead-end on an unmatched route.
export function ComingSoon({ title, blurb }: { title: string; blurb: string }) {
  return (
    <AppShell>
      <div className="flex h-full flex-col items-center justify-center text-center">
        <h1 className="mb-2 text-[24px] font-semibold tracking-tight">{title}</h1>
        <p className="max-w-[420px] text-[14px] text-text-secondary">{blurb}</p>
        <div className="mt-4 rounded-full border border-hairline bg-surface px-3 py-1 font-mono text-[12px] uppercase tracking-wide text-text-tertiary">
          Coming soon
        </div>
      </div>
    </AppShell>
  );
}
