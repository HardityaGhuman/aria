export function Segmented({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-hairline bg-canvas p-1">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={`rounded-md px-4 py-1.5 text-[14px] transition ${
            value === o ? "bg-surface font-medium text-text-ink shadow-card" : "text-text-secondary"
          }`}
        >
          {o}
        </button>
      ))}
    </div>
  );
}
