import { describe, it, expect, beforeEach, vi } from "vitest";
import { apiFetch, setAccessToken, ApiError } from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  beforeEach(() => setAccessToken("expired-token"));

  it("refreshes once on 401 then retries the original request", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "unauthorized", message: "no" } }, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: "fresh", role: "employee", region: "us" }))
      .mockResolvedValueOnce(jsonResponse({ id: 1, role: "employee", region: "us" }));
    vi.stubGlobal("fetch", fetchMock);

    const me = await apiFetch<{ id: number }>("/auth/me", { auth: true });

    expect(me.id).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toContain("/auth/refresh");
    const retryHeaders = (fetchMock.mock.calls[2][1] as RequestInit).headers as Headers;
    expect(retryHeaders.get("Authorization")).toBe("Bearer fresh");
  });

  it("throws ApiError carrying the envelope code on a non-401 error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error: { code: "rate_limited", message: "slow down" } }, 429),
      ),
    );
    await expect(apiFetch("/chat", { method: "POST", auth: true })).rejects.toMatchObject({
      code: "rate_limited",
      status: 429,
    } as Partial<ApiError>);
  });

  it("does not force JSON content-type on FormData bodies", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const fd = new FormData();
    fd.append("x", "y");
    await apiFetch("/admin/documents/upload", { method: "POST", body: fd, auth: true });
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Headers;
    expect(headers.get("Content-Type")).toBeNull();
  });
});
