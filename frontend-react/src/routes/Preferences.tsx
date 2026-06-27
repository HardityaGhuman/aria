import { useEffect, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { AppShell } from "../components/AppShell";
import { Segmented } from "../components/Segmented";
import { Toggle } from "../components/Toggle";
import { Button } from "../components/Button";
import { getPreferences, updatePreferences } from "../lib/api/preferences";
import { lengthToTone, toneToLength, type Tone } from "../lib/prefMapping";

const LANGUAGES = ["English (US)", "English (UK)", "Spanish", "French", "German", "Hindi"];

export default function Preferences() {
  const { data } = useQuery({ queryKey: ["preferences"], queryFn: getPreferences });
  const [tone, setTone] = useState<Tone>("Balanced");
  const [language, setLanguage] = useState("English (US)");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!data) return;
    setTone(lengthToTone(data.response_length));
    if (data.language) setLanguage(data.language);
  }, [data]);

  const save = useMutation({
    mutationFn: () =>
      updatePreferences({ response_length: toneToLength(tone), language, tone: data?.tone ?? null }),
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    },
  });

  const card = "rounded-card border border-hairline bg-surface p-5 shadow-card";
  const row = "flex items-center justify-between border-t border-hairline py-4 first:border-t-0";

  return (
    <AppShell>
      <div className="py-2">
        <h1 className="mb-1 text-[24px] font-semibold tracking-tight">Preferences</h1>
        <p className="mb-6 text-[14px] text-text-secondary">Customize how Aria assists you.</p>

        <div className={`${card} mb-5`}>
          <div className="mb-3 text-[15px] font-semibold">Output Style</div>
          <div className="mb-4">
            <div className="mb-2 text-[13px] text-text-secondary">Answer Tone</div>
            <Segmented
              options={["Concise", "Balanced", "Detailed"]}
              value={tone}
              onChange={(v) => setTone(v as Tone)}
            />
          </div>
          <div>
            <div className="mb-2 text-[13px] text-text-secondary">Primary Language</div>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="w-full max-w-[280px] rounded-lg border border-hairline bg-surface px-3 py-2 text-[15px] outline-none focus:border-ink"
            >
              {LANGUAGES.map((l) => (
                <option key={l}>{l}</option>
              ))}
            </select>
          </div>
        </div>

        <div className={`${card} mb-5`}>
          <div className="mb-1 text-[15px] font-semibold">Features</div>
          <div className="text-[12px] text-text-tertiary">
            Not yet available — coming in a later release.
          </div>
          <div className={row}>
            <div>
              <div className="text-[14px] text-text-ink">Auto-cite Sources</div>
              <div className="text-[12px] text-text-secondary">
                Always include inline citations for factual claims.
              </div>
            </div>
            <Toggle checked disabled />
          </div>
          <div className={row}>
            <div>
              <div className="text-[14px] text-text-ink">Code Highlighting</div>
              <div className="text-[12px] text-text-secondary">
                Enable syntax highlighting for code snippets.
              </div>
            </div>
            <Toggle checked disabled />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save changes"}
          </Button>
          {saved && <span className="text-[13px] text-text-secondary">Saved ✓</span>}
        </div>
      </div>
    </AppShell>
  );
}
