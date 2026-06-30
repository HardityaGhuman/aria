const CHIPS = [
  "What is our remote work policy?",
  "What is my current leave balance?",
  "Company holidays this month?",
];

export function SuggestionChips({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      {CHIPS.map((c) => (
        <button
          key={c}
          onClick={() => onPick(c)}
          className="rounded-card border border-hairline bg-surface p-4 text-left text-[14px] text-text-secondary hover:border-ink"
        >
          {c}
        </button>
      ))}
    </div>
  );
}
