import { apiFetch } from "./client";

export async function ensureSession(current: string | null): Promise<string> {
  if (current) return current;
  const res = await apiFetch<{ session_id: string }>("/chat/sessions", {
    method: "POST",
    auth: true,
  });
  return res.session_id;
}
