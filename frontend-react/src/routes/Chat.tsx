import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { Composer } from "../components/Composer";
import { SuggestionChips } from "../components/SuggestionChips";
import { SourcesPanel } from "../components/SourcesPanel";
import { Markdown } from "../components/Markdown";
import { streamChat } from "../lib/api/sse";
import { ensureSession } from "../lib/api/sessions";
import { apiFetch } from "../lib/api/client";
import { useSmoothText } from "../lib/useSmoothText";
import type { Source } from "../lib/api/schemas";

type Msg = { role: "user" | "assistant"; content: string };

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [streaming, setStreaming] = useState(false);
  const sessionId = useRef<string | null>(null);
  const pendingFinal = useRef<string | null>(null);
  const qc = useQueryClient();
  const smooth = useSmoothText();

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

  // Commit the streamed answer once the smooth reveal has caught up to the final text.
  useEffect(() => {
    if (pendingFinal.current == null) return;
    if (smooth.shown === pendingFinal.current) {
      const final = pendingFinal.current;
      pendingFinal.current = null;
      setMessages((m) => [...m, { role: "assistant", content: final }]);
      smooth.reset();
      setStreaming(false);
    }
  }, [smooth.shown, smooth]);

  async function handleSend(text: string) {
    if (streaming) return;
    const isNew = sessionId.current === null;
    setStreaming(true);
    setSources([]);
    smooth.reset();
    setMessages((m) => [...m, { role: "user", content: text }]);

    try {
      const sid = await ensureSession(sessionId.current);
      sessionId.current = sid;
      if (isNew) qc.invalidateQueries({ queryKey: ["sessions"] });

      await streamChat(
        { message: text, session_id: sid },
        {
          onToken: (delta) => smooth.push(delta),
          onSources: (s) => setSources(s),
          onDone: (env) => {
            pendingFinal.current = env.answer;
            smooth.finalize(env.answer);
          },
          onError: (_code, message) => {
            const msg = `⚠️ ${message}`;
            pendingFinal.current = msg;
            smooth.finalize(msg);
          },
        },
      );

      // First message in a brand-new session: title it from the question.
      if (isNew) {
        const title = text.length > 60 ? `${text.slice(0, 57)}…` : text;
        apiFetch(`/chat/sessions/${sid}`, {
          method: "PATCH",
          auth: true,
          body: JSON.stringify({ title }),
        })
          .then(() => qc.invalidateQueries({ queryKey: ["sessions"] }))
          .catch(() => {});
      }
    } catch {
      pendingFinal.current = "⚠️ Something went wrong. Please try again.";
      smooth.finalize(pendingFinal.current);
    }
  }

  const empty = messages.length === 0 && !streaming;

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
              <Bubble key={i} role={m.role} content={m.content} />
            ))}
            {streaming && <Bubble role="assistant" content={smooth.shown} streaming />}
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

function Bubble({
  role,
  content,
  streaming,
}: {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}) {
  if (role === "user") {
    return (
      <div className="self-end">
        <div className="rounded-card border border-hairline bg-surface px-4 py-2 text-[15px]">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="self-start">
      <div className="mb-1 h-6 w-6 rounded-full bg-ink" aria-hidden />
      <div className="max-w-[640px] text-[15px] leading-[1.55] text-text-ink">
        {content ? <Markdown>{content}</Markdown> : streaming ? <span className="text-text-tertiary">…</span> : null}
      </div>
    </div>
  );
}
