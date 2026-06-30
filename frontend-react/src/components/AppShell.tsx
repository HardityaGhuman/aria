import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { AnimatedBackground } from "./AnimatedBackground";

export function AppShell({ children, inspector }: { children: ReactNode; inspector?: ReactNode }) {
  return (
    <div className="relative isolate flex h-full overflow-hidden bg-canvas">
      {/* Faint dot-grid texture behind every page. */}
      <AnimatedBackground />
      <Sidebar />
      <main className="relative flex flex-1 justify-center overflow-y-auto px-6 py-6 sm:px-12 lg:px-20">
        <div className="flex w-full max-w-[780px] flex-col">{children}</div>
      </main>
      {inspector && (
        <aside className="w-[320px] shrink-0 overflow-y-auto border-l border-hairline bg-surface p-6">
          {inspector}
        </aside>
      )}
    </div>
  );
}
