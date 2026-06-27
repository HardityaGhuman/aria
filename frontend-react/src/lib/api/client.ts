import { API_BASE } from "../config";

let accessToken: string | null = null;
let onAuthFailure: () => void = () => {};

export function setAccessToken(token: string | null) {
  accessToken = token;
}
export function getAccessToken() {
  return accessToken;
}
export function setOnAuthFailure(cb: () => void) {
  onAuthFailure = cb;
}

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

type FetchInit = RequestInit & { auth?: boolean };

function withAuth(init: FetchInit): RequestInit {
  const headers = new Headers(init.headers);
  if (init.auth && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  // Only force JSON for string bodies; FormData must keep the browser-set
  // multipart boundary, so don't touch its Content-Type.
  if (init.body && typeof init.body === "string" && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  return { ...init, headers, credentials: "include" };
}

async function refreshAccessToken(): Promise<boolean> {
  const res = await fetch(`${API_BASE}/auth/refresh`, { method: "POST", credentials: "include" });
  if (!res.ok) return false;
  const data = (await res.json()) as { access_token: string };
  accessToken = data.access_token;
  return true;
}

export async function apiFetch<T>(path: string, init: FetchInit = {}): Promise<T> {
  let res = await fetch(`${API_BASE}${path}`, withAuth(init));

  if (res.status === 401 && init.auth) {
    const refreshed = await refreshAccessToken();
    if (!refreshed) {
      onAuthFailure();
      throw new ApiError("unauthorized", "Session expired", 401);
    }
    res = await fetch(`${API_BASE}${path}`, withAuth(init)); // retry with fresh token
  }

  if (!res.ok) {
    let code = "error";
    let message = res.statusText;
    try {
      const body = await res.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(code, message, res.status);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
