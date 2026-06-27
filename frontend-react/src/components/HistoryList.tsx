import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api/client";
import type { SessionInfo } from "../lib/api/schemas";

export function HistoryList({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

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

  function startEdit(s: SessionInfo) {
    setEditing(s.session_id);
    setDraft(s.title || "");
  }
  function commitEdit(id: string) {
    const title = draft.trim();
    if (title) rename.mutate({ id, title });
    setEditing(null);
  }

  if (!data?.length) {
    return <div className="px-3 text-[12px] text-text-tertiary">No history yet.</div>;
  }

  return (
    <div className="flex flex-col gap-0.5">
      {data.map((s) =>
        editing === s.session_id ? (
          <input
            key={s.session_id}
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => commitEdit(s.session_id)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitEdit(s.session_id);
              if (e.key === "Escape") setEditing(null);
            }}
            className="mx-1 rounded-md border border-ink bg-surface px-2 py-1 text-[13px] outline-none"
          />
        ) : (
          <div
            key={s.session_id}
            className="group flex items-center gap-1 rounded-lg px-3 py-1.5 text-[13px] text-text-secondary hover:bg-canvas"
          >
            <button className="flex-1 truncate text-left" onClick={() => onOpen(s.session_id)}>
              {s.title || "Untitled chat"}
            </button>
            <button
              className="text-text-tertiary opacity-0 hover:text-text-ink group-hover:opacity-100"
              onClick={() => startEdit(s)}
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
        ),
      )}
    </div>
  );
}
