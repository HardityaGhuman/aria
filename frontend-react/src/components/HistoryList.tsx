import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api/client";
import type { SessionInfo } from "../lib/api/schemas";

export function HistoryList({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["sessions"],
    // GET /chat/sessions returns a bare SessionInfo[] array.
    queryFn: () => apiFetch<SessionInfo[]>("/chat/sessions", { auth: true }),
  });

  const del = useMutation({
    mutationFn: (id: string) => apiFetch(`/chat/sessions/${id}`, { method: "DELETE", auth: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      apiFetch(`/chat/sessions/${id}`, {
        method: "PATCH",
        auth: true,
        body: JSON.stringify({ title }),
      }),
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
          className="group flex items-center gap-1 rounded-lg px-3 py-1.5 text-[13px] text-text-secondary hover:bg-canvas"
        >
          <button className="flex-1 truncate text-left" onClick={() => onOpen(s.session_id)}>
            {s.title || "Untitled chat"}
          </button>
          <button
            className="text-text-tertiary opacity-0 hover:text-text-ink group-hover:opacity-100"
            onClick={() => {
              const next = window.prompt("Rename chat", s.title || "");
              if (next && next.trim()) rename.mutate({ id: s.session_id, title: next.trim() });
            }}
            aria-label="rename"
            title="Rename"
          >
            ✎
          </button>
          <button
            className="text-text-tertiary opacity-0 hover:text-badge-hr group-hover:opacity-100"
            onClick={() => del.mutate(s.session_id)}
            aria-label="delete"
            title="Delete"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
