import { describe, it, expect } from "vitest";
import { parseSSE } from "./sse";

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

  it("completes a frame split across two chunks", () => {
    const first = parseSSE('event: token\ndata: {"del', "");
    expect(first.events).toEqual([]);
    const second = parseSSE('ta":"Hi"}\n\n', first.buffer);
    expect(second.events).toEqual([{ event: "token", data: { delta: "Hi" } }]);
  });
});
