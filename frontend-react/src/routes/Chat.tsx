import { useEffect, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { Composer } from "../components/Composer";
import { SuggestionChips } from "../components/SuggestionChips";
import { Markdown } from "../components/Markdown";
import { AssistantAvatar } from "../components/AssistantAvatar";
import { ResponseSources } from "../components/ResponseSources";
import { AnimatedBackground } from "../components/AnimatedBackground";
import { streamChat } from "../lib/api/sse";
import { ensureSession } from "../lib/api/sessions";
import { apiFetch } from "../lib/api/client";
import { useSmoothText } from "../lib/useSmoothText";
import type { Source } from "../lib/api/schemas";

type Msg = { role: "user" | "assistant"; content: string; sources?: Source[] };

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [streaming, setStreaming] = useState(false);
  const sessionId = useRef<string | null>(null);
  const pendingFinal = useRef<string | null>(null);
  const pendingSources = useRef<Source[]>([]);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const smooth = useSmoothText();

  const [params] = useSearchParams();
  const openId = params.get("s");

  // Sync the active conversation to the URL's ?s=. Opening a session loads it;
  // landing on "/" (e.g. clicking New chat) resets to a fresh, empty chat.
  useEffect(() => {
    if (openId && openId !== sessionId.current) {
      sessionId.current = openId;
      (async () => {
        const res = await apiFetch<{
          history: { role: string; content: string; sources?: Source[] | null }[];
        }>(`/chat/history/${openId}`, { auth: true });
        setMessages(
          res.history.map((h) => ({
            role: h.role as "user" | "assistant",
            content: h.content,
            sources: h.sources ?? undefined,
          })),
        );
      })();
    } else if (!openId && sessionId.current !== null) {
      sessionId.current = null;
      setMessages([]);
      smooth.reset();
    }
  }, [openId, smooth]);

  // Commit the streamed answer (with its sources) once the smooth reveal catches up.
  useEffect(() => {
    if (pendingFinal.current == null) return;
    if (smooth.shown === pendingFinal.current) {
      const final = pendingFinal.current;
      const srcs = pendingSources.current;
      pendingFinal.current = null;
      pendingSources.current = [];
      setMessages((m) => [...m, { role: "assistant", content: final, sources: srcs }]);
      smooth.reset();
      setStreaming(false);
    }
  }, [smooth.shown, smooth]);

  async function handleSend(text: string) {
    if (streaming) return;
    const isNew = sessionId.current === null;
    setStreaming(true);
    pendingSources.current = [];
    smooth.reset();
    setMessages((m) => [...m, { role: "user", content: text }]);

    try {
      const sid = await ensureSession(sessionId.current);
      sessionId.current = sid; // set before navigate so the openId effect skips a reload
      if (isNew) {
        navigate(`/?s=${sid}`, { replace: true });
        qc.invalidateQueries({ queryKey: ["sessions"] });
      }

      await streamChat(
        { message: text, session_id: sid },
        {
          onToken: (delta) => smooth.push(delta),
          onSources: (s) => {
            pendingSources.current = s;
          },
          onDone: (env) => {
            pendingFinal.current = env.answer;
            pendingSources.current = env.sources ?? pendingSources.current;
            smooth.finalize(env.answer);
          },
          onError: (_code, message) => {
            pendingFinal.current = `⚠️ ${message}`;
            smooth.finalize(pendingFinal.current);
          },
        },
      );

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
    <AppShell>
      <div className="flex h-full flex-col">
        {empty ? (
          <div className="relative flex flex-1 flex-col items-center justify-center gap-6">
            <AnimatedBackground />
            <div className="text-2xl">✦</div>
            <h1 className="text-[24px] font-semibold tracking-tight">
              Good morning. How can I help you today?
            </h1>
            <div className="w-full max-w-[640px]">
              <SuggestionChips onPick={handleSend} />
            </div>
          </div>
        ) : (
          <div className="flex flex-1 flex-col gap-7 overflow-y-auto px-2 pb-4 pt-4">
            {messages.map((m, i) => (
              <Bubble key={i} role={m.role} content={m.content} sources={m.sources} />
            ))}
            {streaming && <Bubble role="assistant" content={smooth.shown} streaming />}
          </div>
        )}

        <div className="px-2 pt-4">
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
  sources,
  streaming,
}: {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  streaming?: boolean;
}) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-card border border-hairline bg-surface px-4 py-2.5 text-[15px]">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5">
        <AssistantAvatar />
      </div>
      <div className="min-w-0 flex-1">
        <div className="max-w-[680px] pt-0.5 text-[15px] leading-[1.6] text-text-ink">
          {content ? (
            <Markdown>{content}</Markdown>
          ) : streaming ? (
            <span className="text-text-tertiary">…</span>
          ) : null}
        </div>
        {sources && <ResponseSources sources={sources} />}
      </div>
    </div>
  );
}
