import { apiFetch } from "./client";
import type { Schemas } from "./schemas";

export type DocumentInfo = Schemas["DocumentInfo"];

export const listDocuments = () => apiFetch<DocumentInfo[]>("/admin/documents", { auth: true });

export function uploadDocument(file: File, department: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("department", department);
  return apiFetch<{ document_id: string; status: string }>("/admin/documents/upload", {
    method: "POST",
    auth: true,
    body: fd,
  });
}

export const deleteDocument = (id: string) =>
  apiFetch<{ document_id: string; deleted_chunks: number }>(
    `/admin/documents/${encodeURIComponent(id)}`,
    { method: "DELETE", auth: true },
  );

export const reindex = () =>
  apiFetch<{ deleted: number; indexed: number; skipped: number }>("/admin/reindex", {
    method: "POST",
    auth: true,
  });

export const getDocStatus = (id: string) =>
  apiFetch<{ status: string; error?: string | null }>(
    `/admin/documents/${encodeURIComponent(id)}/status`,
    { auth: true },
  );
