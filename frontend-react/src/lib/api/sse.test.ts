import { describe, it, expect, vi, beforeEach } from "vitest";
import { parseSSE, streamChat, type StreamHandlers } from "./sse";
import { setAccessToken, setOnAuthFailure } from "./client";

const enc = new TextEncoder();

// A streaming Response whose body emits the given SSE frames then closes.
function sseResponse(frames: string[], status = 200): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(f));
      controller.close();
    },
  });
  return new Response(stream, { status, headers: { "Content-Type": "text/event-stream" } });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function collectingHandlers(): StreamHandlers & { tokens: string[]; done: unknown; errors: [string, string][] } {
  const tokens: string[] = [];
  const errors: [string, string][] = [];
  let done: unknown = undefined;
  return {
    tokens,
    errors,
    get done() {
      return done;
    },
    onToken: (d) => tokens.push(d),
    onSources: () => {},
    onDone: (env) => {
      done = env;
    },
    onError: (code, msg) => errors.push([code, msg]),
  };
}

const BODY = { message: "hi", session_id: "s1" };

describe("parseSSE", () => {
  it("parses complete event frames and keeps a partial frame buffered", () => {
    const raw =
      'event: token\ndata: {"delta":"Hello"}\n\n' +
      'event: sources\ndata: {"sources":[]}\n\n' +
      'event: done\ndata: {"answer":"Hi"'; // partial, no terminator

    const { events, buffer } = parseSSE(raw, "");
    expect(events).toEqual([
      { event: "token", data: { delta: "Hello" } },
      { event: "sources", data: { sources: [] } },
    ]);
    expect(buffer).toContain("event: done"); // partial held for next chunk
  });

  it("handles CRLF line endings (sse-starlette)", () => {
    const raw = 'event: token\r\ndata: {"delta":"Hi"}\r\n\r\n';
    const { events } = parseSSE(raw, "");
    expect(events).toEqual([{ event: "token", data: { delta: "Hi" } }]);
  });

  it("completes a frame split across two chunks", () => {
    const first = parseSSE('event: token\ndata: {"del', "");
    expect(first.events).toEqual([]);
    const second = parseSSE('ta":"Hi"}\n\n', first.buffer);
    expect(second.events).toEqual([{ event: "token", data: { delta: "Hi" } }]);
  });
});

describe("streamChat", () => {
  beforeEach(() => {
    setAccessToken("expired");
    setOnAuthFailure(() => {});
    vi.restoreAllMocks();
  });

  const OK_FRAMES = [
    'event: token\ndata: {"delta":"Hel"}\n\n',
    'event: token\ndata: {"delta":"lo"}\n\n',
    'event: done\ndata: {"answer":"Hello","sources":[],"latency_ms":1,"session_id":"s1","status":"ok"}\n\n',
  ];

  it("refreshes once on 401 then re-establishes the stream", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "unauthorized" } }, 401)) // stream #1
      .mockResolvedValueOnce(jsonResponse({ access_token: "fresh" })) // /auth/refresh
      .mockResolvedValueOnce(sseResponse(OK_FRAMES)); // stream #2
    vi.stubGlobal("fetch", fetchMock);

    const h = collectingHandlers();
    await streamChat(BODY, h);

    expect(h.tokens.join("")).toBe("Hello");
    expect((h.done as { answer: string }).answer).toBe("Hello");
    expect(fetchMock.mock.calls[1][0]).toContain("/auth/refresh");
    const retryHeaders = (fetchMock.mock.calls[2][1] as RequestInit).headers as Record<string, string>;
    expect(retryHeaders.Authorization).toBe("Bearer fresh");
  });

  it("bounces to login when the refresh fails", async () => {
    const onFail = vi.fn();
    setOnAuthFailure(onFail);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "unauthorized" } }, 401)) // stream
      .mockResolvedValueOnce(jsonResponse({ error: "no" }, 401)); // refresh rejected
    vi.stubGlobal("fetch", fetchMock);

    const h = collectingHandlers();
    await streamChat(BODY, h);

    expect(onFail).toHaveBeenCalledOnce();
    expect(h.errors).toEqual([["unauthorized", "Session expired"]]);
    expect(h.done).toBeUndefined();
  });

  it("propagates a backend error event with its envelope code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(['event: error\ndata: {"error":{"code":"internal_error","message":"boom"}}\n\n']),
      ),
    );
    const h = collectingHandlers();
    await streamChat(BODY, h);
    expect(h.errors).toEqual([["internal_error", "boom"]]);
    expect(h.done).toBeUndefined();
  });

  // §4.1 client-contract: the control layer's new envelope statuses must reach
  // onDone unchanged so the UI can render them distinctly (not collapse to ok/error).
  it.each(["partial", "tool_unavailable"] as const)(
    "carries the %s status through the done envelope unchanged",
    async (status) => {
      const frame =
        `event: done\ndata: {"answer":"A","sources":[],"latency_ms":1,"session_id":"s1","status":"${status}"}\n\n`;
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([frame])));
      const h = collectingHandlers();
      await streamChat(BODY, h);
      expect((h.done as { status: string }).status).toBe(status);
    },
  );

  it("stops silently on abort without persisting a partial answer", async () => {
    const controller = new AbortController();
    // Body that emits one token, then the caller aborts before completion.
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode('event: token\ndata: {"delta":"par"}\n\n'));
        controller.abort();
        c.error(new DOMException("aborted", "AbortError"));
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(stream, { status: 200 })),
    );

    const h = collectingHandlers();
    await streamChat(BODY, h, controller.signal);

    expect(h.done).toBeUndefined(); // never finalized
    expect(h.errors).toEqual([]); // abort is not surfaced as an error
  });
});
