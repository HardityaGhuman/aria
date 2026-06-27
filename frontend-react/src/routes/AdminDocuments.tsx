import { useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { Button } from "../components/Button";
import { StatusBadge } from "../components/StatusBadge";
import { listDocuments, uploadDocument, deleteDocument, reindex } from "../lib/api/admin";

const DEPARTMENTS = [
  "hr",
  "finance",
  "it",
  "time-and-leave",
  "benefits",
  "legal-compliance",
  "people-career",
];
const STATUSES = ["all", "queued", "processing", "indexed", "failed"];

export default function AdminDocuments() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dept, setDept] = useState("hr");
  const fileRef = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
    // Poll while anything is still processing so status badges advance.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((d) => d.status === "queued" || d.status === "processing")
        ? 2000
        : false,
  });

  const upload = useMutation({
    mutationFn: ({ file }: { file: File }) => uploadDocument(file, dept),
    onSuccess: (r) => {
      setNotice(`Queued ${r.document_id}`);
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e: any) => setNotice(e?.message ?? "Upload failed"),
  });
  const del = useMutation({
    mutationFn: (id: string) => deleteDocument(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
  const rebuild = useMutation({
    mutationFn: reindex,
    onSuccess: (r) => {
      setNotice(`Reindexed: +${r.indexed}, -${r.deleted}, skipped ${r.skipped}`);
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const rows = useMemo(() => {
    let r = data ?? [];
    if (statusFilter !== "all") r = r.filter((d) => d.status === statusFilter);
    if (search.trim()) r = r.filter((d) => d.document_id.toLowerCase().includes(search.toLowerCase()));
    return r;
  }, [data, statusFilter, search]);

  return (
    <AppShell>
      <div className="py-2">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="mb-1 text-[24px] font-semibold tracking-tight">HR Documents</h1>
            <p className="text-[14px] text-text-secondary">
              Manage and index the internal policy knowledge base.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
              {rebuild.isPending ? "Rebuilding…" : "↻ Rebuild index"}
            </Button>
            <Button onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
              {upload.isPending ? "Uploading…" : "↑ Upload Document"}
            </Button>
            <input
              ref={fileRef}
              type="file"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate({ file: f });
                e.target.value = "";
              }}
            />
          </div>
        </div>

        <div className="mb-3 flex items-center gap-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search documents…"
            className="flex-1 rounded-lg border border-hairline bg-surface px-3 py-2 text-[14px] outline-none focus:border-ink"
          />
          <select
            value={dept}
            onChange={(e) => setDept(e.target.value)}
            className="rounded-lg border border-hairline bg-surface px-3 py-2 text-[14px] outline-none focus:border-ink"
            title="Upload department"
          >
            {DEPARTMENTS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-lg border border-hairline bg-surface px-3 py-2 text-[14px] outline-none focus:border-ink"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "All statuses" : s}
              </option>
            ))}
          </select>
        </div>

        {notice && (
          <div className="mb-3 rounded-lg bg-canvas px-3 py-2 text-[13px] text-text-secondary">
            {notice}
          </div>
        )}

        <div className="overflow-hidden rounded-card border border-hairline bg-surface">
          <table className="w-full text-left text-[14px]">
            <thead className="border-b border-hairline text-[12px] uppercase tracking-wide text-text-tertiary">
              <tr>
                <th className="px-4 py-3 font-medium">Document</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Department</th>
                <th className="px-4 py-3 font-medium">Updated</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.document_id} className="border-b border-hairline last:border-b-0">
                  <td className="px-4 py-3 font-mono text-[13px]">{d.document_id}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={d.status} />
                  </td>
                  <td className="px-4 py-3 text-text-secondary">{d.department}</td>
                  <td className="px-4 py-3 text-text-tertiary">
                    {d.updated_at ? new Date(d.updated_at).toLocaleDateString() : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      className="text-text-tertiary hover:text-badge-hr"
                      aria-label="delete"
                      onClick={() => {
                        if (confirm(`Delete ${d.document_id}?`)) del.mutate(d.document_id);
                      }}
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-text-tertiary">
                    No documents.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </AppShell>
  );
}
