import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api/client";
import type { SessionInfo } from "../lib/api/schemas";

export function HistoryList({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["sessions"],
    queryFn: () =>
      apiFetch<{ sessions: SessionInfo[] }>("/chat/sessions", { auth: true }).then(
        (r) => r.sessions,
      ),
  });
  const del = useMutation({
    mutationFn: (id: string) => apiFetch(`/chat/sessions/${id}`, { method: "DELETE", auth: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });

  if (!data?.length) {
    return <div className="px-3 text-[12px] text-text-tertiary">No history yet.</div>;
  }

  return (
    <div className="flex flex-col gap-0.5">
      {data.map((s) => (
        <div
          key={s.session_id}
          className="group flex items-center justify-between rounded-lg px-3 py-1.5 text-[13px] text-text-secondary hover:bg-canvas"
        >
          <button className="flex-1 truncate text-left" onClick={() => onOpen(s.session_id)}>
            {s.title || "Untitled chat"}
          </button>
          <button
            className="text-text-tertiary opacity-0 group-hover:opacity-100"
            onClick={() => del.mutate(s.session_id)}
            aria-label="delete"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
