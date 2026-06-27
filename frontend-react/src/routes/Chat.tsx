import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { Composer } from "../components/Composer";
import { SuggestionChips } from "../components/SuggestionChips";
import { SourcesPanel } from "../components/SourcesPanel";
import { streamChat } from "../lib/api/sse";
import { ensureSession } from "../lib/api/sessions";
import { apiFetch } from "../lib/api/client";
import type { Source } from "../lib/api/schemas";

type Msg = { role: "user" | "assistant"; content: string };

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [streaming, setStreaming] = useState(false);
  const sessionId = useRef<string | null>(null);

  const [params] = useSearchParams();
  const openId = params.get("s");

  // Load an existing session when navigated to via ?s=<id>.
  useEffect(() => {
    if (!openId) return;
    sessionId.current = openId;
    (async () => {
      const res = await apiFetch<{ history: { role: string; content: string }[] }>(
        `/chat/history/${openId}`,
        { auth: true },
      );
      setMessages(
        res.history.map((h) => ({ role: h.role as "user" | "assistant", content: h.content })),
      );
      setSources([]);
    })();
  }, [openId]);

  async function handleSend(text: string) {
    if (streaming) return;
    setStreaming(true);
    setSources([]);
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);

    try {
      const sid = await ensureSession(sessionId.current);
      sessionId.current = sid;
      await streamChat(
        { message: text, session_id: sid },
        {
          onToken: (delta) =>
            setMessages((m) => {
              const next = [...m];
              next[next.length - 1] = {
                role: "assistant",
                content: next[next.length - 1].content + delta,
              };
              return next;
            }),
          onSources: (s) => setSources(s),
          onDone: (env) =>
            setMessages((m) => {
              const next = [...m];
              next[next.length - 1] = { role: "assistant", content: env.answer };
              return next;
            }),
          onError: (_code, message) =>
            setMessages((m) => {
              const next = [...m];
              next[next.length - 1] = { role: "assistant", content: `⚠️ ${message}` };
              return next;
            }),
        },
      );
    } finally {
      setStreaming(false);
    }
  }

  const empty = messages.length === 0;

  return (
    <AppShell inspector={<SourcesPanel sources={sources} />}>
      <div className="flex h-full flex-col">
        {empty ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-6">
            <div className="text-2xl">✦</div>
            <h1 className="text-[24px] font-semibold tracking-tight">
              Good morning. How can I help you today?
            </h1>
            <div className="w-full max-w-[640px]">
              <SuggestionChips onPick={handleSend} />
            </div>
          </div>
        ) : (
          <div className="flex flex-1 flex-col gap-6 overflow-y-auto py-4">
            {messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "self-end" : "self-start"}>
                {m.role === "assistant" && (
                  <div className="mb-1 h-6 w-6 rounded-full bg-ink" aria-hidden />
                )}
                <div
                  className={
                    m.role === "user"
                      ? "rounded-card border border-hairline bg-surface px-4 py-2 text-[15px]"
                      : "max-w-[640px] text-[15px] leading-[1.55] text-text-ink"
                  }
                >
                  {m.content || (streaming ? "…" : "")}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="pt-4">
          <Composer
            placeholder={empty ? "Message Aria…" : "Ask a follow up…"}
            onSend={handleSend}
            disabled={streaming}
          />
          <div className="mt-2 text-center text-[12px] text-text-tertiary">
            Aria can make mistakes. Verify important information.
          </div>
        </div>
      </div>
    </AppShell>
  );
}
