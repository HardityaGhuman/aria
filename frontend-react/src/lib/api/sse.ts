import { getAccessToken } from "./client";
import { API_BASE } from "../config";
import type { Source, ChatStatus } from "./schemas";

export type DoneEnvelope = {
  answer: string;
  sources: Source[];
  latency_ms: number;
  session_id: string;
  status: ChatStatus;
};

export type StreamHandlers = {
  onToken: (delta: string) => void;
  onSources: (sources: Source[]) => void;
  onDone: (env: DoneEnvelope) => void;
  onError: (code: string, message: string) => void;
};

// Pure SSE frame parser. Returns completed events + the unconsumed tail.
export function parseSSE(
  chunk: string,
  buffer: string,
): { events: { event: string; data: any }[]; buffer: string } {
  // sse-starlette emits CRLF line endings; normalize so frame splitting works.
  const text = (buffer + chunk).replace(/\r\n/g, "\n");
  const parts = text.split("\n\n");
  const tail = parts.pop() ?? ""; // last piece may be incomplete
  const events: { event: string; data: any }[] = [];

  for (const frame of parts) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) continue;
    try {
      events.push({ event, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      /* skip malformed frame */
    }
  }
  return { events, buffer: tail };
}

export async function streamChat(
  body: { message: string; session_id: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    handlers.onError("stream_failed", `Stream failed (${res.status})`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseSSE(decoder.decode(value, { stream: true }), buffer);
    buffer = parsed.buffer;
    for (const ev of parsed.events) {
      if (ev.event === "token") handlers.onToken(ev.data.delta);
      else if (ev.event === "sources") handlers.onSources(ev.data.sources ?? []);
      else if (ev.event === "done") handlers.onDone(ev.data as DoneEnvelope);
      else if (ev.event === "error")
        handlers.onError(ev.data?.error?.code ?? "error", ev.data?.error?.message ?? "Error");
    }
  }
}
